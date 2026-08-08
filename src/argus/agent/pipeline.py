"""파이프라인 진입점. 고정 횟수 단계라 질문당 비용 상한이 계산된다."""
from __future__ import annotations

import re

from argus.agent import tools
from argus.agent.budget import Budget, BudgetExceeded
from argus.agent.critic import judge, quick_check
from argus.agent.prompts import build_answer_messages
from argus.agent.query import extract_terms
from argus.agent.router import route
from argus.agent.safety import mask_pii, sanitize_question
from argus.clova import chat

UNANSWERABLE = (
    "공시에서 확인되지 않습니다. "
    "제공된 70개사 · 2023.01~2026.03 범위 안의 기업과 기간으로 질문해 주시면 확인해 드리겠습니다."
)

MAX_EVIDENCE = 10                       # 근거 절단 상한 (PCST 압축 대체)
_YM = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월")


def _period_args(r, question: str) -> dict:
    """검색 기간 필터. 좁은 것부터 — 연월 > 연도 > 없음.

    Route 는 연도만 들고 있지만 랭킹을 가르는 것은 연월이다.
    Task 0 실측: corp 필터 + 연도만으로는 골든이 top-5 밖이고, 연월 prefix 를 걸면 1위다.
    연도가 둘 이상이면 접두 하나로 표현할 수 없으므로 기간 필터를 걸지 않는다.
    """
    match = _YM.search(question or "")
    if match:
        return {"ym": f"{match.group(1)}{int(match.group(2)):02d}"}
    if len(r.years) == 1:
        return {"year": r.years[0]}
    return {}


def _gather_evidence(r, question: str = "") -> tuple[str, list[str]]:
    """라우트에 따라 도구를 호출해 근거 마크다운을 모은다. LLM 호출 없음.

    도구가 error 를 돌려주면 (예: periodic_sections.jsonl 이 없어 list_sections 실패)
    조용히 건너뛰고 다음 수단으로 넘어간다. 파이프라인은 죽지 않는다.
    """
    trace: list[str] = []
    parts: list[str] = []
    seen: set[str] = set()
    period = _period_args(r, question)

    def _take(doc_id, markdown) -> None:
        if markdown and doc_id not in seen:
            seen.add(doc_id)
            parts.append(markdown)

    # 1차: BM25 랭킹 검색. 기업명이 없는 전역 질의도 여기서 처리된다.
    terms = extract_terms(question) or list(r.companies)
    for corp in (r.companies[:3] or [None]):
        if not terms or len(parts) >= MAX_EVIDENCE:
            break
        args = {"query": " ".join(terms), "top_k": 5, **period}
        if corp:
            args["corp_name"] = corp
        out = tools.execute("search_chunks", args)
        if "error" in out:
            trace.append(f"search_chunks 건너뜀 ({out['error']})")
            continue
        trace.append(f"search_chunks(corp={corp}, {period or '기간없음'}) -> {out.get('count', 0)}건")
        for chunk in out.get("chunks") or []:
            _take(chunk.get("doc_id"), chunk.get("markdown"))

    # 2차: 검색이 비면 정형 이벤트 열거로 보완
    if not parts:
        for corp in r.companies[:3]:
            args: dict = {"corp_name": corp, "is_latest": True}
            if len(r.years) == 1:
                args["year"] = r.years[0]
            out = tools.execute("query_events", args)
            if "error" in out:
                trace.append(f"query_events 건너뜀 ({out['error']})")
                continue
            trace.append(f"query_events(corp_name={corp}) -> {out.get('count', 0)}건")
            for event in (out.get("events") or [])[:5]:
                _take(event.get("doc_id"), event.get("markdown"))

    return "\n\n".join(parts[:MAX_EVIDENCE]), trace


def answer_question(question_id: str, question: str) -> dict:
    budget = Budget()
    clean, warnings = sanitize_question(question)
    trace: list[str] = list(warnings)

    r = route(clean)
    trace.append(f"route={r.kind} difficulty={r.difficulty} cot={r.use_cot} sc={r.use_sc}")

    if r.kind == "unanswerable":
        trace.append("범위 밖 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE)

    evidence, tool_trace = _gather_evidence(r, clean)
    trace.extend(tool_trace)

    if not evidence:
        trace.append("근거 없음 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE)

    try:
        result = chat(build_answer_messages(clean, evidence, r.use_cot),
                      max_tokens=800 if r.use_cot else 300,
                      temperature=0.0, tag="answer")
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)
        answer = result.text
        trace.append(f"generate tokens={result.prompt_tokens}+{result.completion_tokens}")
    except BudgetExceeded as exc:
        trace.append(f"예산 초과: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE)
    except RuntimeError as exc:
        # /answer 는 어떤 경우에도 5키 스키마를 돌려줘야 한다. 상류 장애로 500 을 내면 0점이다.
        trace.append(f"생성 실패: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE)

    if not quick_check(answer)["has_citation"]:
        trace.append("근거 표기 누락 — critic 호출")
        try:
            verdict = judge(evidence, answer, budget)
            trace.append(f"critic supported={verdict.supported} reason={verdict.reason}")
            if not verdict.passed:
                answer += f"\n\n(주의: 근거 검증을 통과하지 못했습니다 — {verdict.reason})"
        except (BudgetExceeded, RuntimeError) as exc:
            trace.append(f"critic 생략: {exc}")

    return _respond(question_id, question, evidence, trace, mask_pii(answer))


def _respond(question_id: str, question: str, evidence: str,
             trace: list[str], answer: str) -> dict:
    return {
        "question_id": question_id,
        "question": question,
        "retrieved_context": evidence,
        "think_trace": " | ".join(trace),
        "answer": answer,
    }

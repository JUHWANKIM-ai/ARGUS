"""파이프라인 진입점. 고정 횟수 단계라 질문당 비용 상한이 계산된다."""
from __future__ import annotations

import re

from argus.agent import tools
from argus.agent.budget import Budget, BudgetExceeded
from argus.agent.critic import judge, quick_check
from argus.agent.prompts import build_answer_messages
from argus.agent.query import STRATEGIES, plan_hops
from argus.agent.router import route
from argus.agent.safety import mask_pii, sanitize_question
from argus.clova import chat

UNANSWERABLE = (
    "공시에서 확인되지 않습니다. "
    "제공된 70개사 · 2023.01~2026.03 범위 안의 기업과 기간으로 질문해 주시면 확인해 드리겠습니다."
)

MAX_EVIDENCE = 10                       # 근거 절단 상한 (PCST 압축 대체)
_YM = re.compile(r"(20\d{2})\s*년\s*(1[0-2]|0?[1-9])\s*월")

# --- F-3 충분성 판정 (LLM 0회) -----------------------------------------------
# 판정에 LLM 을 쓰면 hop 마다 호출이 붙어 6회 상한을 금방 태운다.
# 후보 수 · BM25 점수 · 골든 필드 존재 여부 세 가지로만 가른다.

#: 라우트별 최소 근거 수. ReAct 단독은 CoT 보다 낮다 (HotpotQA 27.4 vs 29.4) —
#: 검색을 더 돌린다고 이기는 게 아니므로 "답이 나올 최소치"만 채우고 생성으로 넘긴다.
MIN_EVIDENCE = {"closed_lookup": 1, "multi_compare": 2, "complex_reason": 2}

#: BM25 최고 점수 임계. 실측 — 코퍼스와 무관한 질의('우주선 발사 로켓 엔진' 8.7 ·
#: '블록체인 가상자산 채굴' 6.6) 의 최고점과 평가셋 문항의 최고점 최솟값(10.8) 사이.
#: 이 밑이면 우연히 걸린 bigram 이라고 본다.
MIN_TOP_SCORE = 10.0

#: 넓혀도 새 후보가 두 번 연속 안 나오면 인덱스에 없는 것이다. 남은 hop 을 태우지 않는다.
MAX_STALLED_HOPS = 2

#: query_events 폴백에 붙이는 hop 번호 (검색 전략 4단 다음).
EVENTS_HOP = len(STRATEGIES)

#: 제공 코퍼스 기간 2023.01~2026.03 (CONVENTION §2 G7).
#: 이 밖의 연도를 물으면 기간 필터를 푸는 순간 **엉뚱한 연도 공시가 근거로 둔갑한다**.
#: 넓히는 것이 늘 이득은 아니다 — 범위 밖은 못 찾은 게 아니라 없는 것이다.
CORPUS_YEARS = range(2023, 2027)

#: (필드명, 질문이 그 필드를 요구하는가, 근거에 그 필드가 있는가).
#: 근거 마크다운은 `- 계약금액: ...` 형태라 라벨 존재만 보면 된다 (corpus/markdown.py).
_FIELD_HINTS = (
    ("금액", re.compile(r"금액|얼마"), re.compile(r"금액\s*:")),
    ("비율", re.compile(r"매출액\s*대비|비중|비율|퍼센트|몇\s*%"), re.compile(r"대비\s*:")),
    ("기간", re.compile(r"계약\s*기간|기간은|몇\s*년|개월"), re.compile(r"기간\s*:")),
    ("계약상대", re.compile(r"계약\s*상대|상대방|거래처"), re.compile(r"계약상대\s*:")),
)


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


def _required_fields(question: str) -> list[tuple[str, re.Pattern]]:
    """질문이 요구하는 골든 필드. 이게 근거에 없으면 답을 만들 수 없다."""
    return [(name, has) for name, asks, has in _FIELD_HINTS if asks.search(question or "")]


def _out_of_corpus_period(r) -> bool:
    """질문이 지목한 연도가 전부 코퍼스 밖인가. 그러면 재검색은 오답 제조다."""
    return bool(r.years) and all(y not in CORPUS_YEARS for y in r.years)


def _needed_evidence(r) -> int:
    """최소 근거 수. 비교 질의는 기업 수만큼, CoT 경로는 최소 2건."""
    need = max(MIN_EVIDENCE.get(r.kind, 1), len(r.companies))
    return max(need, 2) if r.use_cot else need


def _sufficiency(r, picked: list[dict], required) -> tuple[bool, list[str]]:
    """근거가 충분한가. LLM 0회 — 후보 수 · 점수 · 필드 존재만 본다."""
    if not picked:
        return False, ["no_candidate"]

    reasons: list[str] = []
    need = _needed_evidence(r)
    if len(picked) < need:
        reasons.append(f"few:{len(picked)}<{need}")

    scores = [p["score"] for p in picked if isinstance(p["score"], (int, float))]
    if scores and max(scores) < MIN_TOP_SCORE:
        reasons.append(f"low_score:{max(scores):.1f}<{MIN_TOP_SCORE:.1f}")

    text = "\n".join(p["markdown"] for p in picked)
    reasons += [f"missing_field:{name}" for name, has in required if not has.search(text)]

    if len(r.companies) >= 2:                 # 비교 질의는 한쪽만 찾으면 답이 안 된다
        covered = {p["corp"] for p in picked}
        reasons += [f"missing_corp:{c}" for c in r.companies[:3] if c not in covered]

    return not reasons, reasons


# --- F-3 hop 로그 (Phase H 입력) ---------------------------------------------
# think_trace 는 사람이 읽는 문자열이지만 Phase H 의 RETRIEVAL/RANKING 판정도 여기서 나온다.
# 그래서 hop 별 질의어·필터·후보 수·후보 doc_id 를 key=value 로 남기고 parse_hops() 로 되읽는다.

_KV = re.compile(r'(\w+)=("[^"]*"|[^\s|]*)')


def _fmt_period(period: dict) -> str:
    if "ym" in period:
        return f"ym:{period['ym']}"
    if "year" in period:
        return f"year:{period['year']}"
    return "none"


def _log_search(plan, corp, hits: list[dict]) -> str:
    ids = ",".join(str(h.get("doc_id") or "?") for h in hits)
    scores = [h["score"] for h in hits if isinstance(h.get("score"), (int, float))]
    top = f"{max(scores):.3f}" if scores else "-"
    return (f'hop={plan.hop} strat={plan.strategy} tool=search_chunks '
            f'corp="{corp or "-"}" period={_fmt_period(plan.period)} top_k={plan.top_k} '
            f'q="{plan.query}" n={len(hits)} top_score={top} ids={ids or "-"}')


def _log_verdict(hop: int, ok: bool, count: int, reasons: list[str]) -> str:
    line = f'hop={hop} verdict={"sufficient" if ok else "insufficient"} evidence={count}'
    return line if ok else f'{line} reason={",".join(reasons)}'


def parse_hops(think_trace: str) -> list[dict]:
    """think_trace 를 hop 레코드로 되돌린다. Phase H 의 RETRIEVAL/RANKING 판정 입력.

        ids = [d for h in parse_hops(resp["think_trace"]) for d in h["ids"]]
        RETRIEVAL  — 골든 rcept_no 가 ids 어디에도 없음
        RANKING    — ids 에는 있으나 상위 3위 밖

    hop 로그가 없는 trace (범위 밖 질의 등) 는 빈 목록을 돌려준다.
    """
    hops: dict[int, dict] = {}
    order: list[int] = []
    for line in (think_trace or "").split(" | "):
        kv = {k: v.strip('"') for k, v in _KV.findall(line)}
        raw = kv.get("hop", "")
        if not raw.isdigit():
            continue
        hop = int(raw)
        rec = hops.get(hop)
        if rec is None:
            rec = {"hop": hop, "strategy": "", "corp": [], "period": "", "queries": [],
                   "ids": [], "n": 0, "top_score": None, "verdict": "", "reason": "",
                   "notes": []}
            hops[hop] = rec
            order.append(hop)
        rec["strategy"] = kv.get("strat") or rec["strategy"]
        rec["period"] = kv.get("period") or rec["period"]
        rec["verdict"] = kv.get("verdict") or rec["verdict"]
        rec["reason"] = kv.get("reason") or rec["reason"]
        if kv.get("corp") and kv["corp"] != "-" and kv["corp"] not in rec["corp"]:
            rec["corp"].append(kv["corp"])
        if kv.get("q"):
            rec["queries"].append(kv["q"])
        if kv.get("n", "").isdigit():
            rec["n"] += int(kv["n"])
        for doc_id in (kv.get("ids") or "").split(","):
            if doc_id and doc_id != "-" and doc_id not in rec["ids"]:
                rec["ids"].append(doc_id)
        try:
            score = float(kv.get("top_score", ""))
        except ValueError:
            score = None
        if score is not None:
            rec["top_score"] = score if rec["top_score"] is None else max(rec["top_score"], score)
        for key in ("skip", "stop"):
            if kv.get(key):
                rec["notes"].append(f"{key}={kv[key]}")
    return [hops[h] for h in order]


def _gather_evidence(r, question: str = "",
                     budget: Budget | None = None) -> tuple[str, list[str]]:
    """ReAct 반복 루프. 검색 → 충분성 판정 → 부족하면 질의 재구성 후 재검색. LLM 0회.

        검색 → 근거 충분? ─ 예 → 생성
                  └─ 아니오 → 질의 재구성 → 재검색 (hop ≤ 3, 총 스텝 ≤ 8)

    hop 0 은 최초 검색이라 예산을 쓰지 않고, 재검색 3회가 `Budget.spend_hop()` 을 쓴다
    (ReAct 논문: 정답 궤적 중 7스텝을 쓴 것은 0.84% — 깊이를 더 줘도 얻는 게 없다).
    도구 호출은 `Budget.spend_step()` 으로 총 8스텝을 지킨다 (주최 측 참조 아키텍처).
    상한을 넘기면 예외를 밖으로 던지지 않고 **그 시점 근거로 진행**한다.

    도구가 error 를 돌려주면 (예: periodic_sections.jsonl 이 없어 list_sections 실패)
    조용히 건너뛰고 다음 수단으로 넘어간다. 파이프라인은 죽지 않는다.
    """
    budget = budget or Budget()
    trace: list[str] = []
    picked: list[dict] = []
    seen: set[str] = set()
    period = _period_args(r, question)
    corps = r.companies[:3] or [None]
    required = _required_fields(question)
    stalled = 0
    out_of_steps = False

    def _take(doc_id, markdown, score, corp) -> None:
        if markdown and doc_id and doc_id not in seen:
            seen.add(doc_id)
            picked.append({"doc_id": doc_id, "markdown": markdown,
                           "score": score, "corp": corp})

    for plan in plan_hops(question, period, fallback_terms=r.companies):
        # 기업이 특정된 질의에서 이미 근거가 있으면 기업 필터를 풀지 않는다.
        # 다른 회사 공시가 섞이면 요약·비교가 통째로 오염된다 — 넓히는 것이 늘 이득은 아니다.
        if plan.strategy == "global" and picked and r.companies:
            trace.append(f'hop={plan.hop} strat=global skip=corp_scoped '
                         f'(기업 특정 질의에 근거 {len(picked)}건 확보 — 전역 검색은 오염)')
            break
        if plan.hop > 0 and _out_of_corpus_period(r):
            trace.append(f"hop={plan.hop} stop=out_of_range "
                         f"(질문 기간 {r.years} 가 코퍼스 2023.01~2026.03 밖 — 완화해도 답이 없다)")
            break
        if plan.hop > 0:
            try:
                budget.spend_hop()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=hop_budget ({exc})")
                break

        before = len(picked)
        for corp in (corps if plan.use_corp else [None]):
            if len(picked) >= MAX_EVIDENCE:
                break
            try:
                budget.spend_step()
            except BudgetExceeded as exc:
                trace.append(f"hop={plan.hop} stop=step_budget ({exc})")
                out_of_steps = True
                break
            args = {"query": plan.query, "top_k": plan.top_k, **plan.period}
            if corp:
                args["corp_name"] = corp
            out = tools.execute("search_chunks", args)
            if "error" in out:
                trace.append(f'hop={plan.hop} strat={plan.strategy} tool=search_chunks '
                             f'corp="{corp or "-"}" skip=tool_error 건너뜀({out["error"]})')
                continue
            hits = out.get("chunks") or []
            if plan.strategy == "global":
                # 필터가 하나도 없는 검색은 점수만이 관련성의 보증이다.
                # 잡음 바닥(MIN_TOP_SCORE) 밑을 담으면 무관한 회사 공시 10건이 근거가 된다.
                hits = [h for h in hits
                        if isinstance(h.get("score"), (int, float))
                        and h["score"] >= MIN_TOP_SCORE]
            trace.append(_log_search(plan, corp, hits))
            for chunk in hits:
                _take(chunk.get("doc_id"), chunk.get("markdown"),
                      chunk.get("score"), corp)

        ok, reasons = _sufficiency(r, picked, required)
        trace.append(_log_verdict(plan.hop, ok, len(picked), reasons))
        if ok or out_of_steps:
            break
        # 근거가 하나도 없을 때는 수렴이 아니라 못 찾은 것이다 — 계속 넓힌다.
        stalled = stalled + 1 if picked and len(picked) == before else 0
        if stalled >= MAX_STALLED_HOPS:
            trace.append(f"hop={plan.hop} stop=stalled "
                         f"({MAX_STALLED_HOPS}회 연속 새 후보 없음)")
            break

    # 마지막 수단: 검색이 아무것도 못 찾으면 정형 이벤트 열거로 보완.
    # 연도가 둘 이상이면 query_events 에 기간 조건을 걸 수 없으므로 범위 밖 질의는 여기서도 막는다.
    if not picked and not _out_of_corpus_period(r):
        for corp in r.companies[:3]:
            try:
                budget.spend_step()
            except BudgetExceeded as exc:
                trace.append(f"hop={EVENTS_HOP} stop=step_budget ({exc})")
                break
            args: dict = {"corp_name": corp, "is_latest": True}
            if len(r.years) == 1:
                args["year"] = r.years[0]
            out = tools.execute("query_events", args)
            if "error" in out:
                trace.append(f'hop={EVENTS_HOP} strat=events tool=query_events '
                             f'corp="{corp}" skip=tool_error 건너뜀({out["error"]})')
                continue
            events = (out.get("events") or [])[:5]
            ids = ",".join(str(e.get("doc_id") or "?") for e in events)
            trace.append(f'hop={EVENTS_HOP} strat=events tool=query_events corp="{corp}" '
                         f'period={_fmt_period({"year": args["year"]} if "year" in args else {})} '
                         f'n={out.get("count", 0)} top_score=- ids={ids or "-"}')
            for event in events:
                _take(event.get("doc_id"), event.get("markdown"), None, corp)

    return "\n\n".join(p["markdown"] for p in picked[:MAX_EVIDENCE]), trace


def answer_question(question_id: str, question: str) -> dict:
    budget = Budget()
    clean, warnings = sanitize_question(question)
    trace: list[str] = list(warnings)

    r = route(clean)
    trace.append(f"route={r.kind} difficulty={r.difficulty} cot={r.use_cot} sc={r.use_sc}")

    if r.kind == "unanswerable":
        trace.append("범위 밖 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    evidence, tool_trace = _gather_evidence(r, clean, budget)
    trace.extend(tool_trace)

    if not evidence:
        trace.append("근거 없음 — LLM 호출 없이 한계 고지")
        return _respond(question_id, question, "", trace, UNANSWERABLE, budget)

    try:
        result = chat(build_answer_messages(clean, evidence, r.use_cot),
                      max_tokens=800 if r.use_cot else 300,
                      temperature=0.0, tag="answer")
        budget.spend_llm(result.prompt_tokens, result.completion_tokens)
        answer = result.text
        trace.append(f"generate tokens={result.prompt_tokens}+{result.completion_tokens}")
    except BudgetExceeded as exc:
        trace.append(f"예산 초과: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE, budget)
    except RuntimeError as exc:
        # /answer 는 어떤 경우에도 5키 스키마를 돌려줘야 한다. 상류 장애로 500 을 내면 0점이다.
        trace.append(f"생성 실패: {exc}")
        return _respond(question_id, question, evidence, trace, UNANSWERABLE, budget)

    if not quick_check(answer)["has_citation"]:
        trace.append("근거 표기 누락 — critic 호출")
        try:
            verdict = judge(evidence, answer, budget)
            trace.append(f"critic supported={verdict.supported} reason={verdict.reason}")
            if not verdict.passed:
                answer += f"\n\n(주의: 근거 검증을 통과하지 못했습니다 — {verdict.reason})"
        except (BudgetExceeded, RuntimeError) as exc:
            trace.append(f"critic 생략: {exc}")

    return _respond(question_id, question, evidence, trace, mask_pii(answer), budget)


def _respond(question_id: str, question: str, evidence: str, trace: list[str],
             answer: str, budget: Budget | None = None) -> dict:
    """5키 스키마. 어떤 경로로 나가든 마지막에 예산 실사용량을 남긴다 (게이트 F)."""
    if budget is not None:
        trace = trace + [f"budget steps={budget.steps} hops={budget.hops} "
                         f"llm={budget.llm_calls} tokens={budget.tokens}"]
    return {
        "question_id": question_id,
        "question": question,
        "retrieved_context": evidence,
        "think_trace": " | ".join(trace),
        "answer": answer,
    }

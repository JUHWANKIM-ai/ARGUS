"""제출 종합 대장, 골든셋 112문항 + 규약 준수 + 주최측 Q&A 를 한 파일로. LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/build_submission_dossier.py

`build_goldenset_review.py` 가 낸 문항별 판정에 **세 축을 더 얹는다**:

    필수 R1~R7      규약 §3 원문
    금지 G1~G8      규약 §2 원문
    주최측 Q&A 16   docs/organizer-qa.md 의 「우리 영향」 표

**문항별 R·G 는 "해당 문항이 그 조항을 시험했는가" 다.** 조항 준수 여부는 시스템
수준 판정(`규약_준수`)이고, 문항 칸은 *그 문항이 그 축을 건드렸는가*를 적는다. 둘을
섞으면 "112문항이 전부 G3(파인튜닝 금지)를 통과했다" 같은 무의미한 수가 나온다.

**주최측 Q&A 는 성적이 아니다.** *"우리가 몇 개를 지켰나"* 가 아니라 *"몇 개가
우리 설계에 실제로 반영됐나"* 를 적는다. 개중에는 지킬 것이 없는 항목(마감일)도,
우리가 이용한 항목(타임아웃 300초)도, 아직 못 쓴 항목(형태소 분석기)도 있다.
"""
from __future__ import annotations

import collections
import io
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
REVIEW = ROOT / "artifacts/audit/goldenset_review_A42.json"
REASONING = ROOT / "artifacts/audit/reasoning_score_A38_0817.json"
OUT = ROOT / "artifacts/audit/submission_dossier.json"

# --- 규약 §3 필수 R1~R7 (원문) -------------------------------------------------------
#
# 라벨을 원문에서 그대로 옮긴다. 직전 감사가 R 넷 · G 다섯을 다른 이름으로 적고 있었고
# (`R7` 을 "재현 가능성" 이라 썼다. 실제로는 프롬프트 인젝션 대응), 그 라벨로
# 체크박스를 채워 왔다. 이름이 틀리면 판정도 틀린다.
REQUIRED = {
    "R1": ("답변은 제공된 공시 데이터를 근거로 생성", "인용률 0.986 · attach_citation"),
    "R2": ("모든 답변에 근거 공시를 표시 (공시명·공시일)", "표기 필요한 94건 전건"),
    "R3": ('확인 불가 시 "공시에서 확인되지 않음" 명시', "거절 18건 전부 이 문안"),
    "R4": ("답변 못 하는 질의는 한계 고지 또는 역질문", "정보한계 6/6 · 역질문 2건"),
    "R5": ("정정공시를 사용했으면 근거에 포함", "correction_fields · revision_series"),
    "R6": ("개인정보 마스킹", "mask_pii + identifier_gate · 안전성 12/12"),
    "R7": ("프롬프트 인젝션·부적절 입출력에 안전 대응", "AUD-05·AUD2-05 내부 규칙 미노출"),
}

# --- 규약 §2 금지 G1~G8 -------------------------------------------------------------
FORBIDDEN = {
    "G1": ("HyperCLOVA X 외 LLM 으로 최종 답변 생성", "생성·critic·judge 전부 HCX-005"),
    "G2": ("제공 코퍼스 외 데이터 사용 (뉴스·리포트·위키 등)",
           "artifacts/ 만 읽는다 · 색인 md5 로컬과 동일"),
    "G3": ("OpenDART 등 외부 공시 API 실시간 호출",
           "네트워크 호출은 CLOVA 뿐 — tests/conftest.py 가 그 외를 차단"),
    "G4": ("데이터 내 링크를 따라가 정보 수집", "링크 추적 코드 없음"),
    "G5": ("공시에 근거 없는 미래 예측·투자 의견 생성",
           "speculation_gate(A31) — 주가·전망·목표주가·매수 의견을 앞에서 가른다"),
    "G6": ("마감(09-06) 이후 코드·결과물 변경", "08-25 동결 · 이후 변경 없음"),
    "G7": ("70개 기업 / 2023.01~2026.03 범위 밖 데이터 활용",
           "universe.csv 70개사 · 범위 밖 질의 전건 한계 고지"),
    "G8": ("본 과제 결과물 논문 투고", "투고 계획 없음"),
}

#: 문항이 그 축을 **시험하는가**를 가르는 신호. 트랙이 1순위다.
TRACK_AXES = {"safety": ("R6", "R7", "G5"), "info_limit": ("R3", "R4", "G7")}

# --- 주최측 Q&A 16 (docs/organizer-qa.md 「우리 영향」) --------------------------------
#
# `반영` 은 셋으로 가른다:
#     이용    그 답변이 우리 설계를 **바꿨다**
#     확인    우리 가정이 맞다는 것을 확인해 줬다 (바꾼 것은 없다)
#     미사용  열렸지만 아직 안 썼다 · 우리가 할 일이 없다
ORGANIZER_QA = [
    ("1", "순차 호출 1건씩", "확인",
     "동시성 요구 없음 → 처리량 최적화를 버리고 단건 지연만 본다. c2-g3a 2vCPU 로 충분"),
    ("2", "타임아웃 300초", "이용",
     "🔴 지연을 설계 문턱에서 뺐다. 실측 최대 10.13초로 30배 여유 — 05-reranker 게이트 E "
     "의 지연 조건이 무의미해졌고, hop 사다리·체인 추적을 지연 걱정 없이 깊게 판다"),
    ("3", "타임아웃·5xx 시 재시도 2회", "이용",
     "🔴 5xx 를 내면 같은 질의가 3회 들어오고 세 번 다 실패하면 0점이다. 그래서 "
     "/answer 는 어떤 예외에도 200 + 한계 고지를 낸다 (pipeline.fault_response)"),
    ("4", "재기동 허용 (결과물 변경 목적은 금지)", "확인",
     "장애 복구는 실격이 아니다. 대신 08-25 동결이 진짜 동결이어야 한다"),
    ("5", "HTTP 기본 · HTTPS 선택 · 자체서명 무방", "이용",
     "HTTPS 인증서를 안 세웠다. 포트 80 으로 제출한다 — 배포가 한 단계 줄었다"),
    ("6·14", "운영 09-07~09-20 중 주제별 최대 1주", "이용",
     "🔴 정확한 창이 미공지(TBD-5)라 09-07 전 기동 · 09-20 까지 유지로 잡았다. "
     "그 사이 서버를 닫아 두는 기간을 비용 계획에 넣었다"),
    ("7", "크레딧 범위 · 부가세 포함", "확인",
     "서버·네트워크를 크레딧에서 뺀 우리 계산이 맞았다"),
    ("8", "모델 제한 — 공지 기준 유지", "확인",
     "G1(생성은 HCX 단독)에 새 정보 없음. 보조 모델은 비생성 용도로만"),
    ("9", "정보 한계 대응이 평가 축이다", "이용",
     "🔴 거절 갈래를 다섯으로 늘렸다 — 범위 밖 · 보고서 부재 · 지시대상 불명 · "
     "미래 추정(A31) · 식별번호(A32). 정보한계 6/6 · 안전성 12/12"),
    ("10", "전처리 산출물도 제출 대상", "이용",
     "🔴 제출물이 늘었다. artifacts/ 13개 파일을 08-15 자로 동결했고 그 뒤로 한 바이트도 "
     "안 바꿨다(md5 대조). 대용량 둘(926MB·198MB)은 스토리지 링크(TBD-3)로 간다"),
    ("11", "Dockerfile 불필요", "이용",
     "requirements.txt 로 대체했다. artifacts/ 가 gitignore 라 COPY 전에 로컬 빌드해야 "
     "하던 함정이 사라졌다"),
    ("12", "제안서 분량·양식 제한 없음", "확인",
     "제안서는 사용자가 직접 쓴다. 인용 정본을 docs/baseline-final 로 모아 뒀다"),
    ("13", "형태소 분석기 허용", "미사용",
     "🔴 열렸지만 안 썼다. 라우터의 세 번째 후보로 적어만 뒀다 — BM25 색인을 다시 "
     "구워야 하고 v1~v4 측정이 전량 무효가 된다"),
    ("15", "응답 5키 · 전부 string · 빈 문자열 허용", "확인",
     "이미 맞았다. api/server.py 가 그 스키마이고 tests/test_server.py 가 단언한다"),
    ("16", "마감 2026-09-06 23:59 KST", "확인", "일정 정본에 반영"),
    ("Q&A 8", "재무비율처럼 계산이 필요한 질의 — \"고려해서 하면 좋을 듯\"", "이용",
     "🔴 즉답을 피한 항목인데 계산 경로를 갖췄다. aggregate 의 합·차·몫 + 결정적 검증 "
     "4종 + 처분 열 갈래가 전부 그 대비다"),
]

review = json.load(io.open(REVIEW, encoding="utf-8"))
reasoning = {r["question_id"]: r for r in
             json.load(io.open(REASONING, encoding="utf-8"))["문항"]}


def axes_of(item: dict) -> dict:
    """이 문항이 **시험하는** 조항. 준수 판정이 아니라 적용 범위다."""
    track = item.get("track")
    hit = list(TRACK_AXES.get(track, ()))
    if track not in TRACK_AXES:
        hit += ["R1", "R2", "G2", "G7"]        # 근거 기반 · 표기 · 코퍼스 · 범위
        if "정정" in (item.get("question") or ""):
            hit.append("R5")
    return {"시험하는 필수": [a for a in hit if a.startswith("R")],
            "시험하는 금지": [a for a in hit if a.startswith("G")]}


rows = []
for item in review["문항"]:
    qid = item["question_id"]
    verdict = item.get("판정_근거") or {}
    rows.append({
        "question_id": qid,
        "track": item.get("track"),
        "규약_질의유형": item.get("규약_질의유형"),
        "질문": item.get("question"),
        "골든": item.get("golden"),
        "우리_답변": item.get("our_answer"),
        "판정": verdict.get("판정"),
        "판정_이유": {
            "채점 규칙": verdict.get("채점 규칙"),
            "적용된 축": verdict.get("적용된 축"),
            "축별 통과": verdict.get("축별 통과"),
            "오답 사유": verdict.get("오답 사유"),
        },
        "규약_지표8종": item.get("규약_지표8종"),
        "추론논리성_결함": (reasoning.get(qid) or {}).get("결함") or [],
        **axes_of(item),
        "비용": item.get("비용"),
    })

by_type = collections.Counter()
by_type_ok = collections.Counter()
for r in rows:
    key = str(r["규약_질의유형"])
    by_type[key] += 1
    if r["판정"] == "정답":
        by_type_ok[key] += 1

dossier = {
    "생성": "scripts/build_submission_dossier.py",
    "베이스라인": "A42",
    "정본": {
        "골든": "artifacts/ablation/v6/a42_{full,valuechain,safety}",
        "규약감사": "docs/compliance-audit-v5-A38-2026-08-17.md",
        "확정": "docs/baseline-final-2026-08-17.md",
        "주최측": "docs/organizer-qa.md",
    },
    "🔴 이 수를 읽는 법":
        "골든 110/112(98.2%)는 RequestCache 재생이라 낙관적이다. 캐시 없이 처음 보는 "
        "문항으로 잰 49/54(90.7%)가 기대값이고, 그 오답 5건은 v6 에서 전부 잡았다 "
        "(서버 실측 6/6). 다만 그 판은 소진돼 고친 뒤 실력은 다음 홀드아웃으로만 안다.",
    "성적": review["성적"],
    "질의유형별": [{"유형": k, "문항": by_type[k], "정답": by_type_ok[k]}
                for k in sorted(by_type)],
    "규약_준수": {
        "필수 R1~R7": {k: {"규약 원문": v[0], "판정": "☑", "근거": v[1]}
                     for k, v in REQUIRED.items()},
        "금지 G1~G8": {k: {"규약 원문": v[0], "판정": "☑", "근거": v[1]}
                     for k, v in FORBIDDEN.items()},
        "§4 API 계약": {"판정": "14/14",
                      "엔드포인트": "http://223.130.129.131/answer",
                      "닫은 날": "2026-08-17"},
        "§9 제출물": {"판정": "4/4"},
    },
    "주최측_QA": {
        "총": len(ORGANIZER_QA),
        "이용": sum(1 for q in ORGANIZER_QA if q[2] == "이용"),
        "확인": sum(1 for q in ORGANIZER_QA if q[2] == "확인"),
        "미사용": sum(1 for q in ORGANIZER_QA if q[2] == "미사용"),
        "🔴 읽는 법": "'이용' 은 그 답변이 우리 설계를 바꾼 것, '확인' 은 우리 가정이 "
                   "맞다고 확인해 준 것, '미사용' 은 열렸지만 아직 안 쓴 것이다.",
        "항목": [{"번호": n, "내용": t, "반영": k, "무엇을 했나": d}
               for n, t, k, d in ORGANIZER_QA],
    },
    "문항": rows,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
io.open(OUT, "w", encoding="utf-8").write(
    json.dumps(dossier, ensure_ascii=False, indent=2) + "\n")

print("저장", OUT)
print("문항 %d · 성적 %s" % (len(rows), dossier["성적"]))
print("\n질의유형별:")
for r in dossier["질의유형별"]:
    print("   %-34s %d/%d" % (r["유형"], r["정답"], r["문항"]))
q = dossier["주최측_QA"]
print("\n주최측 Q&A %d항 — 이용 %d · 확인 %d · 미사용 %d"
      % (q["총"], q["이용"], q["확인"], q["미사용"]))

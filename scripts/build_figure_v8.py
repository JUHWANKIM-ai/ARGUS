r"""ARGUS **배포 기준** 상세 구조도 (`A89` · 2026-08-26). LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/build_figure_v8.py

## 규격 — v7 을 잇는다

v7(`argus-deploy-v7.png`)의 규격을 그대로 따른다.

    바탕 #ffffff · 글씨 #000000 · **채움색이 범주를 나른다**(색 = LLM 호출 여부)
    색과 기호를 함께 쓴다 — 인쇄해 색을 잃어도 기호가 남는다

**🔴 문장을 쓰지 않는다. 낱말과 식별자만 싣는다** (사용자 지시 2026-08-23). 도면은
읽는 것이 아니라 **훑는 것**이다. 설명이 필요하면 제안서 본문이 할 일이다.

## 🔴 이름은 코드에서 가져온다

상자에 적히는 함수·도구·게이트·플래그 이름은 전부 저장소에 실재하는 식별자다.

    조회·계산 도구 15종   argus.agent.tools.TOOL_SPECS (조회 8 · 계산 7)
    거절 사유 6갈래       argus.agent.router 의 reason="…" 여섯
    결정적 검증 4종       argus.agent.critic.verify_*
    집계·수리 갈래        argus.agent.aggregate 의 공개 함수
    스위치 71 켬 / 17 끔  argus.ablation.A89 의 bool 필드
    예산 가드            argus.agent.budget.Budget 기본값
    색인                 artifacts/

도면이 코드와 어긋나면 **코드가 맞다.**

## 🔴 `A89` 는 아직 측정이 없다 — 이 도면은 성적을 A89 것으로 적지 않는다

`_current = A89` 이고 켠 스위치는 코드에 있다. 그런데 서버 산출물의 팔은 `A86`(골든) ·
`A86c`(미공개)이고 `A89` 로 돌린 회차가 없다. 그래서 머리글의 성적은 **재어 본 팔의
이름과 함께** 적고, `A89` 칸에는 무엇이 달라지는지만 적는다.

    골든    A86b 서버 3회   scratch/audit/axis_compare.json
    미공개   A86c 서버 3회   scratch/audit/heldout_a86c_rounds.json
    지연    A84 실측        README.md 응답 지연 표
    시험    pytest --collect-only -q

**요약을 재인용하지 않는다.** 성적을 두 번 틀리게 적은 적이 있고 원인이 요약 재인용이었다.

## 🔴 배치를 코드가 계산한다

`FACT` 와 `PANELS` 만 사람이 적고 좌표는 전부 계산이다. 상자 높이는 줄 수에서 나온다 —
손으로 적으면 글이 한 줄 늘 때마다 어긋난다.

## 🔴 두 벌을 한 목록에서 낸다

`.drawio`(편집용)와 `.png`(문서 삽입용)가 같은 `PANELS` 를 읽는다. 따로 그리면
한쪽만 고쳐진다.
"""
from __future__ import annotations

import html
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm                              # noqa: E402
import matplotlib.pyplot as plt                                   # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch    # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PNG = ROOT / "docs/figures/argus-deploy-v8.png"
DRAWIO = ROOT / "docs/figures/argus-deploy-v8.drawio"

#: 🔴 **사람이 옮겨 적는 유일한 자리다.** 출처를 주석에 함께 적는다.
FACT = {
    "arm": "A89",
    # argus.ablation.A89 의 bool 필드를 센 값
    "구성": "구성 A89 = _current · 스위치 71 켬 / 17 끔 · "
          "wide_counterparty_slots 가 A86 과의 유일한 차이",
    # scratch/audit/axis_compare.json 의 "A86b 서버"
    "골든": "골든셋 111 · 112 · 112 / 112  (재어 본 팔 A86b · 평가 서버 3회)",
    # 같은 파일 회차별 축 실패의 합집합
    "축": "축 실패 합집합 — 근거 완전성 Q-110 3/3 · 정확성 1건(r1) · "
         "추론 논리성 1건(r1) · 나머지 여섯 축 0",
    # scratch/audit/heldout_a86c_rounds.json
    "미공개셋": "미공개셋 52 · 53 · 52 / 54  (재어 본 팔 A86c · 3회 · 상시 오답 N-006)",
    # README.md 응답 지연 표 (A84 실측)
    "지연": "지연 중앙값 10.6초 · 최대 46.7초 · 규약 상한 300초 (A84 실측)",
    # argus.agent.budget.Budget 기본값
    "예산": "예산 steps 8 · hops 3 · calls 6 · tokens 100K",
    # pytest --collect-only -q
    "시험": "자동화 시험 1,994건",
    # src/argus/ablation.py 의 A89 주석
    #: 🔴 글리프는 Malgun Gothic 에 없어 도면에서 두부가 된다. 도면 안에서는 ▲ 로 쓴다.
    "미측정": "▲ A89 는 미측정 — 위 성적은 A86b · A86c 것이다. A89 에서 근거가 바뀌는 문항은 "
           "셋(Q-109 · Q-110 · Q-116)이고 나머지 109문항은 프롬프트가 바이트까지 같다",
}

BLUE, ORANGE, GREEN, GREY, RED = ("#dae8fc", "#ffe6cc", "#d5e8d4", "#f2f2f2", "#f8cecc")
#: 이 판에서만 쓴다 — `A86` 대비 달라진 자리를 색으로 가른다.
PURPLE = "#e1d5e7"
EDGE = {BLUE: "#6c8ebf", ORANGE: "#d79b00", GREEN: "#82b366",
        GREY: "#8c8c8c", RED: "#b85450", PURPLE: "#9673a6"}

#: 한 항목 = (제목, 아랫줄, 색). 아랫줄은 `·` 로 잇는 **낱말 목록**이다. 문장 금지.
PANELS = [
    dict(key="A", x=20, w=900, cols=2,
         title="A.  입력 · 라우팅", tag="○ LLM 0회",
         items=[
             ("질의 수신", "GET /answer · question_id · question", GREY),
             ("입력 안전 계층", "PII 마스킹 · 인젝션 표지 탐지", BLUE),
             ("규칙 라우터", "route() · 규칙만 · 학습 없음", BLUE),
             ("거절 게이트 6갈래", "out_of_universe · speculation · identifier\n"
                              "false_premise · ambiguous_reference · injection", BLUE),
         ]),
    dict(key="B", x=20, w=900, cols=2,
         title="B.  검색 · 근거", tag="○ LLM 0회    [4] ReAct — 검색·추론 교차",
         items=[
             ("메타 프리필터", "corp_name · rcept_dt · doc_group", BLUE),
             ("BM25 hop 사다리", "narrow → relax → global · TOP_K 5·8·10·10", BLUE),
             ("섹션 인덱스 hop", "정기공시 목차 경로    [10] GraphReader", BLUE),
             ("정정 체인 hop", "chain_probe · get_history · reverse_chain", BLUE),
             ("계약상대 2-hop", "query_by_counterparty · counterparty_relevance\n"
                            "[11] Think-on-Graph 2.0", BLUE),
             ("△ 계약상대 근거 칸 6 → 8", "wide_counterparty_slots · 파트너당 최소 2건\n"
                                  "A89 의 유일한 변경 · 상한 8 은 오프라인 산술", PURPLE),
             ("충분성 판정 5규칙", "결정적 · LLM 0회", BLUE),
             ("조회 도구 8종", "resolve_company · query_events · list_filings\n"
                           "list_sections · read_section · search_chunks\n"
                           "get_history · query_by_counterparty", BLUE),
             ("계산 도구 7종", "calc_pct_change · calc_share · calc_total\n"
                           "calc_mean · calc_cagr · calc_compare · calc_period", BLUE),
         ]),
    dict(key="C", x=20, w=900, cols=2,
         title="B′.  결정적 집계 — 근거 머리에 싣는다",
         tag="○ LLM 0회    [6] Graph of Thoughts — 집계 단계 분리",
         items=[
             ("집계 한 줄", "aggregate_line · substitute_aggregate · 합 · 차 · 몫", BLUE),
             ("항목 로스터", "roster_line · roster_items · summand_roster", BLUE),
             ("정정 시계열 · 전후 대조", "revision_series · revision_diff\n"
                                "correction_fields", BLUE),
             ("대체본 각인", "stale_note · 정정체인 · 대체됨", BLUE),
             ("지분 · 담보 시계열", "holding_series · pledged_series\n"
                             "holding_receipts · holding_ask_wide", BLUE),
             ("지분 짝", "holding_pair", BLUE),
             ("계약상대 쌍 · 갈래", "counterparty_pair · counterparty_branch", BLUE),
             ("부문별 매출 · 구성비", "segment_sales · composition_total", BLUE),
             ("환율 환산", "fx_conversion", BLUE),
             ("단위 · 기간 · 대조", "unit_line · wide_unit_parens\n"
                            "period_label · contrast_line", BLUE),
             ("날짜 판별", "date_disambiguation", BLUE),
             ("델타 · 총계 · 값 연산", "delta_totals · state_total · result_value_op", BLUE),
         ]),
    dict(key="D", x=950, w=930, cols=1,
         title="C.  생성", tag="● HCX-005    [1] GPT-3 · [2] Chain-of-Thought",
         items=[
             ("답변 생성", "온도 0.0 · 난이도 상이면 CoT · think_trace    [2]", ORANGE),
             ("자기일관성 k=3", "sc_k 3 · sc_figures_only · sc_key_set · 온도 0.0\n"
                            "[3] Self-Consistency — 원형 기각 · 변형 채택", ORANGE),
         ]),
    dict(key="E", x=950, w=930, cols=2,
         title="D.  검증 · 처분",
         tag="◇ 대부분 LLM 0회    [2] 부록B · [18] SWE-Lancer",
         items=[
             ("결정적 검증 4종", "verify_calculation · verify_answer_arithmetic\n"
                            "verify_number_provenance · verify_completeness", GREEN),
             ("critic", "표기 누락 시에만 발화    [16][17] 역할 분리", ORANGE),
             ("재생성 1회", "regenerate_on_verify · substitute_verified", ORANGE),
             ("수리 갈래", "fix_unit_parens · fix_self_contradiction\n"
                       "fix_broken_equation · drop_broken_equation\n"
                       "fix_stray_summands · expand_rounded\n"
                       "repair_gap · gap_repair", GREEN),
             ("표기 부착", "attach_citation · cite_used · cite_chain_pair\n"
                       "citation_note_fix · decisive_not_citable\n"
                       "strip_marker_citation", GREEN),
             ("문면 위생", "quiet_verify_note · strip_copied_directive\n"
                       "external_referral_strip", GREEN),
             ("출력 안전 계층", "법인 라벨 보존 · 번호 마스킹 · project_scope", GREEN),
             ("예산 가드", "steps 8 · hops 3 · calls 6 · tokens 100K\n"
                       "[14] GraphRAG-R1 — 보상학습 대신 가드", GREEN),
             ("응답 5키", "question_id · question · retrieved_context\n"
                       "think_trace · answer", GREY),
         ]),
    dict(key="F", x=950, w=930, cols=2,
         title="E.  배포 산출물 · 미채택", tag="2026-08-26",
         items=[
             ("오프라인 색인", "exchange · major · holding BM25\n"
                          "section_index · counterparty_graph\n"
                          "periodic_chains · references · nary_records", BLUE),
             ("서버", "NCP c2-g3a 2vCPU/4GB · systemd argus\n"
                    "공인 IP 223.130.129.131", GREY),
             ("정적 사이트", "기업 70 · 정정 631 · 거래상대 145\n"
                         "서버 · DB · 런타임 API 없음", GREEN),
             ("배포 제외", "원본 5.55GB · 임베딩 API", RED),
             ("미채택 스위치 17", "dense · golden_oracle · lean_rules · nary\n"
                            "multi_hub · function_calling · calc_tools_only\n"
                            "calc_queries_only · substitute_tool_results\n"
                            "strict_refusal · partner_profile\n"
                            "regenerate_partial · compare_line\n"
                            "refusal_compaction · latest_pin\n"
                            "roster_counts · section_chain_note", RED),
             ("남은 문제", "Q-110 근거 완전성 167/168 — 선택 문제\n"
                       "N-006 미공개셋 상시 오답\n"
                       "docs/open-issues-v3.md", GREY),
         ]),
]

#: 문헌 — 세 갈래. 낱말만. 번호는 README §10.4 와 같다.
PAPERS = [
    ("채택", GREEN, [
        "[1] GPT-3 — 문맥만으로 수행 · 추가 학습 없음",
        "[2] Chain-of-Thought — 근거·계산 먼저 · think_trace",
        "[2] 부록 B — 생성 등식 외부 재계산 · 결과 전파",
        "[3] Self-Consistency — 변형 채택 · 금액 한정 · 온도 0.0",
        "[4] ReAct — 검색·추론 교차 · 행동 선택은 결정적",
        "[15] Evidence Utilization — 근거 이용률 분리 측정",
    ]),
    ("기각 — 재보고 뺐다", RED, [
        "[3] Self-Consistency 원형 — 온도 > 0 표집",
        "[5] Toolformer — 모델 함수호출 · 74/88 대 81/88",
        "[7] G-Retriever — PCST 압축 · 절감 대상 0",
        "[9][11] 밀집 검색 — 신규 회수 0 · 지표 불변",
        "[12] HyperGraphRAG — arity ≤ 2 가 58.4%",
    ]),
    ("발상만 — 원 기제 미구현", BLUE, [
        "[6] Graph of Thoughts — 집계 단계 분리",
        "[8] GraphRAG — 전역·국소 구분 · 커뮤니티 요약 미적용",
        "[10] GraphReader — 목차 경로 탐색",
        "[11] Think-on-Graph 2.0 — 구조·본문 교차 루프",
        "[13] Relink — 질의 시점 그래프 보강",
        "[14] GraphRAG-R1 — 과잉 탐색 억제 · 예산 가드",
        "[16] CAMEL · [17] AutoGen — 역할 분리 · 단일 에이전트",
        "[18] SWE-Lancer — 판단 < 생성 · 결정적 검증 근거",
    ]),
]

#: 🔴 도면 좌표는 100단위/인치다. 한 줄 높이 = 글자 pt × 1.389 × linespacing.
#: 첫 판은 이 환산을 안 해 LINE_H 를 14 로 뒀고 **본문 마지막 줄이 패널을 넘었다.**
#: 🔴 **v7 의 환산이 틀렸다. 여기서 고친다.**
#:
#: v7 은 `PT = 100/72` 을 썼다 — 「좌표 100단위 = 1인치」를 전제한 값이다. 그런데
#: `plt.subplots` 는 축을 그림의 **77%** 에만 앉히므로 실제로는 좌표 100단위가 0.77인치이고
#: 1pt 는 1.3889 가 아니라 **1.8038 단위**다. 오차 1.30배다.
#:
#: v7 은 항목이 최대 3줄이라 그 오차가 `PAD` 안에 묻혔다. 이 판은 7줄 항목
#: (미채택 스위치 17)이 있어 **마지막 두 줄이 상자를 넘었다.**
#:
#: 고치는 방식: 축을 그림에 꽉 채우고(`add_axes([0, 0, 1, 1])`) 그림 크기를 `UPI` 로
#: 정한다. 그러면 「단위 ↔ pt」 환산이 코드의 전제와 정확히 같아진다. `UPI` 를 129.87
#: (= 100 / 0.77)로 둔 것은 **v7 의 글자·상자 비율을 그대로 지키려는 것**이다.
UPI = 100.0 / 0.77
PT = UPI / 72.0
TITLE_H, LINE_H = 10 * PT * 1.30, 8.4 * PT * 1.45
PAD_T, PAD_B = 8, 7
PANEL_HEAD, PANEL_PAD, GAP = 32, 12, 10
#: 패널의 y 는 손으로 안 적는다. 열마다 위에서부터 쌓는다 — 항목이 한 줄 늘 때
#: 아래 패널을 손으로 밀어야 하는 것이 v7 의 두 번째 함정이었다.
PANEL_TOP, PANEL_GAP = 196, 30


def _item_h(sub):
    return PAD_T + TITLE_H + ((sub.count("\n") + 1) * LINE_H if sub else 0) + PAD_B


def layout():
    """패널 안에 항목을 격자로 앉히고, 패널을 열마다 위에서부터 쌓는다.

    두 번 돈다 — 먼저 패널 높이를 항목에서 세고, 그 다음 `y` 를 쌓는다. `y` 를 손으로
    적으면 항목이 한 줄 늘 때마다 아래 패널을 밀어야 하고, 안 밀면 겹친다.
    """
    heights = {}
    for p in PANELS:
        cols = p["cols"]
        heads = [PANEL_HEAD] * cols
        for n, (_, sub, _fill) in enumerate(p["items"]):
            heads[n % cols] += _item_h(sub) + GAP
        heights[p["key"]] = max(heads) - GAP + PANEL_PAD

    cursor = {}
    boxes, panels = [], []
    for p in PANELS:
        y = cursor.get(p["x"], PANEL_TOP)
        cursor[p["x"]] = y + heights[p["key"]] + PANEL_GAP
        cols, inner = p["cols"], p["w"] - 2 * PANEL_PAD
        iw = (inner - (cols - 1) * GAP) / cols
        heads = [y + PANEL_HEAD] * cols
        for n, (title, sub, fill) in enumerate(p["items"]):
            c = n % cols
            x = p["x"] + PANEL_PAD + c * (iw + GAP)
            h = _item_h(sub)
            boxes.append([x, heads[c], iw, h, title, sub, fill])
            heads[c] += h + GAP
        panels.append(dict(p, y=y, h=heights[p["key"]]))
    return boxes, panels


def _font() -> str:
    for name in ("Malgun Gothic", "맑은 고딕", "NanumGothic", "Gulim"):
        try:
            fm.findfont(fm.FontProperties(family=name), fallback_to_default=False)
            return name
        except Exception:
            continue
    raise SystemExit("※ 한글 글꼴을 못 찾았습니다 — 도면의 글씨가 깨집니다")


def _panel(panels, key):
    return next(p for p in panels if p["key"] == key)


def emit_png(boxes, panels, legend_y, papers_y, H):
    plt.rcParams["font.family"] = _font()
    plt.rcParams["axes.unicode_minus"] = False
    W = 1900
    #: 🔴 `plt.subplots` 가 아니라 `add_axes([0, 0, 1, 1])` 다. 축이 그림을 꽉 채워야
    #: 「좌표 UPI 단위 = 1인치」가 성립하고, 그때만 `TITLE_H`·`LINE_H` 가 실제 글자
    #: 높이와 같아진다. v7 은 축이 77% 만 차지하는 채로 100 을 가정해 1.30배 어긋났다.
    fig = plt.figure(figsize=(W / UPI, H / UPI), dpi=170)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    fig.patch.set_facecolor("#ffffff")

    #: 머리글 줄 간격도 `PT` 로 센다. 손으로 적은 21·11pt 를 실제 높이로 환산해 쌓는다.
    ax.text(20, 14, "ARGUS 공시 Agent — 배포 구조 %s" % FACT["arm"],
            fontsize=21, fontweight="bold", va="top")
    ax.text(20, 60, FACT["구성"], fontsize=11, va="top")
    ax.text(20, 82, "%s   ·   %s" % (FACT["골든"], FACT["미공개셋"]),
            fontsize=11, va="top", color="#333333")
    ax.text(20, 104, FACT["축"], fontsize=11, va="top", color="#333333")
    ax.text(20, 126, "%s   ·   %s   ·   %s" % (FACT["지연"], FACT["예산"], FACT["시험"]),
            fontsize=11, va="top", color="#333333")
    ax.text(20, 150, FACT["미측정"], fontsize=10.4, va="top", color="#b85450")
    ax.text(20, 172,
            "정본  src/argus/ablation.py(_current=A89) · agent/pipeline.py · agent/aggregate.py "
            "· agent/critic.py · deploy/README.md        ※ 도면과 코드가 어긋나면 코드가 맞다",
            fontsize=9, va="top", color="#777777")

    for p in panels:
        ax.add_patch(FancyBboxPatch(
            (p["x"], p["y"]), p["w"], p["h"],
            boxstyle="round,pad=0,rounding_size=6", facecolor="#ffffff",
            edgecolor="#555555", linewidth=1.1, linestyle=(0, (4, 3))))
        ax.text(p["x"] + PANEL_PAD, p["y"] + 20, p["title"], fontsize=12.5,
                fontweight="bold", va="center")
        ax.text(p["x"] + p["w"] - PANEL_PAD, p["y"] + 20, p["tag"], fontsize=9.6,
                va="center", ha="right", color="#444444")

    for x, y, w, h, title, sub, fill in boxes:
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0,rounding_size=4",
            facecolor=fill, edgecolor=EDGE[fill], linewidth=1.1))
        ax.text(x + 9, y + PAD_T + 1, title, fontsize=10, fontweight="bold", va="top")
        if sub:
            ax.text(x + 9, y + PAD_T + TITLE_H, sub, fontsize=8.4, va="top",
                    color="#222222", linespacing=1.45)

    #: 흐름 — 낱말 없이 방향만 나른다.
    for a, b in (("A", "B"), ("B", "C"), ("D", "E"), ("E", "F")):
        pa, pb = _panel(panels, a), _panel(panels, b)
        ax.add_patch(FancyArrowPatch(
            (pa["x"] + pa["w"] / 2, pa["y"] + pa["h"]), (pb["x"] + pb["w"] / 2, pb["y"]),
            arrowstyle="-|>", mutation_scale=15, linewidth=1.5, color="#000000"))
    #: 집계 → 생성. 🔴 곡률 0 이다. v7 은 `rad=-0.2` 로 휘어 **패널 B 의 상자 글씨를
    #: 가로질렀다.** 두 열 사이 홈(x 920~950)이 좁으므로 직선이 그 안에 머문다.
    pc, pd = _panel(panels, "C"), _panel(panels, "D")
    ax.add_patch(FancyArrowPatch(
        (pc["x"] + pc["w"], pc["y"] + 24), (pd["x"], pd["y"] + pd["h"] - 24),
        arrowstyle="-|>", mutation_scale=15, linewidth=1.5, color="#000000",
        connectionstyle="arc3,rad=0"))

    #: 범례
    ax.add_patch(FancyBboxPatch((20, legend_y), 1860, 74,
                                boxstyle="round,pad=0,rounding_size=5",
                                facecolor="#ffffff", edgecolor="#555555",
                                linewidth=1.0, linestyle=(0, (4, 3))))
    ax.text(32, legend_y + 19,
            "범례 — 채움색 = LLM 호출 여부 (보라는 A86 대비 달라진 자리)", fontsize=11,
            fontweight="bold", va="center")
    for k, (fill, label) in enumerate([
            (BLUE, "○  LLM 0회 — 규칙 · 색인 · 집계"),
            (ORANGE, "●  HCX-005 호출 — 생성 · critic"),
            (GREEN, "◇  결정적 검증 · 안전 · 정적 산출"),
            (PURPLE, "△  A89 의 변경 — 근거 칸 6 → 8"),
            (GREY, "     경계 — 우리 밖과 만나는 자리"),
            (RED, "×  기각 · 미채택 · 배포 제외")]):
        bx = 32 + k * 310
        ax.add_patch(FancyBboxPatch((bx, legend_y + 42), 18, 13,
                                    boxstyle="round,pad=0,rounding_size=2",
                                    facecolor=fill, edgecolor=EDGE[fill], linewidth=1.0))
        ax.text(bx + 26, legend_y + 49, label, fontsize=9.2, va="center")

    #: 문헌
    ax.add_patch(FancyBboxPatch((20, papers_y), 1860, H - papers_y - 20,
                                boxstyle="round,pad=0,rounding_size=6",
                                facecolor="#ffffff", edgecolor="#555555",
                                linewidth=1.1, linestyle=(0, (4, 3))))
    ax.text(32, papers_y + 20, "참조 문헌 18편 — 번호는 README §10.4", fontsize=12.5,
            fontweight="bold", va="center")
    ax.text(1868, papers_y + 20,
            "※ 논문에 있다는 것은 채택 사유가 아니다 — 스위치를 세워 우리 코퍼스에서 재고 갈랐다",
            fontsize=9.4, va="center", ha="right", color="#444444")
    for c, (head, fill, lines) in enumerate(PAPERS):
        cx = 32 + c * 616
        ax.add_patch(FancyBboxPatch((cx, papers_y + 36), 596, 20,
                                    boxstyle="round,pad=0,rounding_size=3",
                                    facecolor=fill, edgecolor=EDGE[fill], linewidth=1.0))
        ax.text(cx + 8, papers_y + 46, head, fontsize=9.8, fontweight="bold",
                va="center")
        ax.text(cx + 8, papers_y + 66, "\n".join(lines), fontsize=8.6, va="top",
                color="#222222", linespacing=1.66)

    fig.savefig(PNG, bbox_inches="tight", facecolor="#ffffff", pad_inches=0.25)
    plt.close(fig)


def emit_drawio(boxes, panels):
    cells = []
    for i, p in enumerate(panels):
        style = ("rounded=1;whiteSpace=wrap;html=1;dashed=1;fillColor=none;"
                 "strokeColor=#555555;verticalAlign=top;align=left;spacing=8;"
                 "fontSize=13;fontStyle=1;fontColor=#000000")
        cells.append('<mxCell id="p%d" value="%s" style="%s" vertex="1" parent="1">'
                     '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/>'
                     "</mxCell>"
                     % (i, html.escape("%s   %s" % (p["title"], p["tag"])), style,
                        p["x"], p["y"], p["w"], p["h"]))
    for i, (x, y, w, h, title, sub, fill) in enumerate(boxes):
        label = "&lt;b&gt;%s&lt;/b&gt;&#10;%s" % (
            html.escape(title), html.escape(sub).replace("\n", "&#10;"))
        style = ("rounded=1;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
                 "spacing=6;fillColor=%s;strokeColor=%s;fontColor=#000000;fontSize=10"
                 % (fill, EDGE[fill]))
        cells.append('<mxCell id="b%d" value="%s" style="%s" vertex="1" parent="1">'
                     '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/>'
                     "</mxCell>" % (i, label, style, int(x), int(y), int(w), int(h)))
    xml = ('<mxfile host="argus"><diagram name="deploy-v8"><mxGraphModel dx="2000" '
           'dy="1400" grid="0" page="1" pageWidth="1900" pageHeight="1800" '
           'background="#ffffff" math="0" shadow="0"><root>'
           '<mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) +
           "</root></mxGraphModel></diagram></mxfile>")
    DRAWIO.write_text(xml, encoding="utf-8")


def main() -> int:
    PNG.parent.mkdir(parents=True, exist_ok=True)
    boxes, panels = layout()
    bottom = max(p["y"] + p["h"] for p in panels)
    legend_y = bottom + 26
    papers_y = legend_y + 96
    rows = max(len(lines) for _, _, lines in PAPERS)
    H = papers_y + 76 + int(rows * 8.6 * PT * 1.75) + 54
    emit_drawio(boxes, panels)
    emit_png(boxes, panels, legend_y, papers_y, H)
    print("%s\n%s" % (PNG, DRAWIO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

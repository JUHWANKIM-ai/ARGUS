r"""ARGUS **배포 기준** 상세 구조도 (`A94`). LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/build_figure_v7.py

## 규격 — v6 를 잇는다

v6(`argus-algorithm-v6.png`)의 규격을 그대로 따른다.

    바탕 #ffffff · 글씨 #000000 · **채움색이 범주를 나른다**(색 = LLM 호출 여부)
    색과 기호를 함께 쓴다 — 인쇄해 색을 잃어도 기호가 남는다

**🔴 문장을 쓰지 않는다. 낱말과 식별자만 싣는다** (사용자 지시 2026-08-23). 도면은
읽는 것이 아니라 **훑는 것**이다. 설명이 필요하면 제안서 본문이 할 일이다.

## 🔴 이름은 코드에서 가져온다

상자에 적히는 함수·도구·게이트 이름은 전부 저장소에 실재하는 식별자다.

    조회·계산 도구 15종   argus.agent.tools.TOOL_SPECS
    거절 사유 6갈래       argus.agent.router 의 made("unanswerable", reason=…)
    결정적 검증 4종       argus.agent.critic.verify_*
    집계·수리 갈래        argus.agent.aggregate 의 공개 함수
    색인                 artifacts/

도면이 코드와 어긋나면 **코드가 맞다.**

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
PNG = ROOT / "docs/figures/argus-deploy-v7.png"
DRAWIO = ROOT / "docs/figures/argus-deploy-v7.drawio"

#: 🔴 **사람이 옮겨 적는 유일한 자리다.** 출처를 주석에 함께 적는다.
FACT = {
    "arm": "A94",
    "골든셋": "골든셋 112/112 · 평가 서버 3회 동일",   # scratch/audit/axis_compare.json
    "축": "9축 전부 실패 0 · 근거 완전성 168/168",
    "미공개셋": "미공개셋 52~53/54 · 3회",            # scratch/audit/heldout_a92h_rounds.json
    "지연": "지연 중앙값 11.0초 · 최대 37.3초 · 상한 300초의 12.4%",
    "비용": "질의당 48.1원 · 호출 평균 2.54회 · 상한 6회",
    "시험": "2,017건",
}

BLUE, ORANGE, GREEN, GREY, RED = ("#dae8fc", "#ffe6cc", "#d5e8d4", "#f2f2f2", "#f8cecc")
EDGE = {BLUE: "#6c8ebf", ORANGE: "#d79b00", GREEN: "#82b366",
        GREY: "#8c8c8c", RED: "#b85450"}

#: 한 항목 = (제목, 아랫줄, 색). 아랫줄은 `·` 로 잇는 **낱말 목록**이다. 문장 금지.
PANELS = [
    dict(key="A", x=20, y=150, w=900, cols=2,
         title="A.  입력 · 라우팅", tag="○ LLM 0회",
         items=[
             ("질의 수신", "GET /answer · question_id · question", GREY),
             ("입력 안전 계층", "PII 마스킹 · 인젝션 표지 탐지", BLUE),
             ("규칙 라우터", "route() · 규칙만 · 학습 없음", BLUE),
             ("거절 게이트 6갈래", "out_of_universe · speculation · identifier\n"
                              "false_premise · ambiguous_reference · injection", BLUE),
         ]),
    dict(key="B", x=20, y=380, w=900, cols=2,
         title="B.  검색 · 근거", tag="○ LLM 0회    [4] ReAct — 검색·추론 교차",
         items=[
             ("메타 프리필터", "corp_name · rcept_dt · doc_group", BLUE),
             ("BM25 hop 사다리", "narrow → relax → global", BLUE),
             ("섹션 인덱스 hop", "정기공시 목차 경로    [10] GraphReader", BLUE),
             ("정정 체인 hop", "chain_probe · get_history · 역방향 체인", BLUE),
             ("계약상대 2-hop", "발주처 fan-in/out    [11] ToG 2.0", BLUE),
             ("충분성 판정 5규칙", "결정적 · LLM 0회", BLUE),
             ("조회 도구 8종", "resolve_company · query_events · list_filings\n"
                           "list_sections · read_section · search_chunks\n"
                           "get_history · query_by_counterparty", BLUE),
             ("계산 도구 7종", "calc_pct_change · calc_share · calc_total\n"
                           "calc_mean · calc_cagr · calc_compare · calc_period", BLUE),
         ]),
    dict(key="C", x=20, y=730, w=900, cols=2,
         title="B′.  결정적 집계 — 근거 머리에 싣는다",
         tag="○ LLM 0회    [6] Graph of Thoughts — 집계 단계 분리",
         items=[
             ("집계 한 줄", "aggregate_line · 합 · 차 · 몫", BLUE),
             ("항목 로스터", "roster_line · roster_items · summand_roster", BLUE),
             ("정정 시계열", "revision_series", BLUE),
             ("정정 전후 대조", "revision_diff", BLUE),
             ("지분 시계열", "holding_series · pledged_series", BLUE),
             ("계약상대 쌍 · 갈래", "counterparty_pair · counterparty_branch", BLUE),
             ("부문별 매출", "segment_sales", BLUE),
             ("구성비 합계", "composition_total", BLUE),
             ("환율 환산", "fx_conversion", BLUE),
             ("단위 · 기간 · 대조", "unit_line · period_label · contrast_line", BLUE),
             ("날짜 판별", "date_disambiguation", BLUE),
             ("델타 · 총계", "delta_totals · state_total", BLUE),
         ]),
    dict(key="D", x=950, y=150, w=930, cols=1,
         title="C.  생성", tag="● HCX-005    [1] GPT-3 · [2] Chain-of-Thought",
         items=[
             ("답변 생성", "온도 0.0 · 난이도 상이면 CoT · think_trace    [2]", ORANGE),
             ("자기일관성 k=3", "금액 문항 한정 · 키 집합 다수결 · 온도 0.0\n"
                            "[3] Self-Consistency — 원형 기각 · 변형 채택", ORANGE),
         ]),
    dict(key="E", x=950, y=350, w=930, cols=2,
         title="D.  검증 · 처분",
         tag="◇ 대부분 LLM 0회    [2] 부록B · [18] SWE-Lancer",
         items=[
             ("결정적 검증 4종", "verify_calculation · verify_answer_arithmetic\n"
                            "verify_number_provenance · verify_completeness", GREEN),
             ("critic", "표기 누락 시에만 발화    [16][17] 역할 분리", ORANGE),
             ("재생성 1회", "regenerate_on_verify · substitute_verified", ORANGE),
             ("수리 갈래", "fix_unit_parens · fix_self_contradiction\n"
                       "fix_broken_equation · fix_stray_summands\n"
                       "expand_rounded · repair_gap · gap_repair", GREEN),
             ("출력 안전 계층", "법인 라벨 보존 · 번호 마스킹", GREEN),
             ("예산 가드", "steps 8 · hops 3 · calls 6 · tokens 100K\n"
                       "[14] GraphRAG-R1 — 보상학습 대신 가드", GREEN),
             ("응답 5키", "question_id · question · retrieved_context\n"
                       "think_trace · answer", GREY),
         ]),
    dict(key="F", x=950, y=700, w=930, cols=2,
         title="E.  배포 산출물", tag="2026-08-27",
         items=[
             ("오프라인 색인", "exchange · major · holding BM25\n"
                          "section_index · counterparty_graph\n"
                          "periodic_chains · references", BLUE),
             ("서버", "NCP c2-g3a 2vCPU/4GB · systemd argus\n"
                    "공인 IP 223.130.129.131", GREY),
             ("정적 사이트", "기업 70 · 정정 631 · 거래상대 145\n"
                         "서버 · DB · 런타임 API 없음", GREEN),
             ("계약상대 칸", "상한 8 · 파트너당 최소 2건\n"
                          "로스터 줄이 합계 금지를 명시", BLUE),
             ("배포 제외", "원본 5.3GB · 임베딩 API", RED),
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
PT = 100.0 / 72.0
TITLE_H, LINE_H = 10 * PT * 1.30, 8.4 * PT * 1.45
PAD_T, PAD_B = 8, 7
PANEL_HEAD, PANEL_PAD, GAP = 32, 12, 10


def _item_h(sub):
    return PAD_T + TITLE_H + ((sub.count("\n") + 1) * LINE_H if sub else 0) + PAD_B


def layout():
    """패널 안에 항목을 격자로 앉힌다. 열마다 높이를 따로 센다."""
    boxes, panels = [], []
    for p in PANELS:
        cols, inner = p["cols"], p["w"] - 2 * PANEL_PAD
        iw = (inner - (cols - 1) * GAP) / cols
        heads = [p["y"] + PANEL_HEAD] * cols
        for n, (title, sub, fill) in enumerate(p["items"]):
            c = n % cols
            x = p["x"] + PANEL_PAD + c * (iw + GAP)
            h = _item_h(sub)
            boxes.append([x, heads[c], iw, h, title, sub, fill])
            heads[c] += h + GAP
        panels.append(dict(p, h=max(heads) - p["y"] - GAP + PANEL_PAD))
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
    fig, ax = plt.subplots(figsize=(19.0, H / 100.0), dpi=170)
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    fig.patch.set_facecolor("#ffffff")

    ax.text(20, 16, "ARGUS 공시 Agent — 배포 구조 %s" % FACT["arm"],
            fontsize=21, fontweight="bold", va="top")
    ax.text(20, 56, "%s   ·   %s" % (FACT["골든셋"], FACT["축"]), fontsize=11, va="top")
    ax.text(20, 77, "%s   ·   %s" % (FACT["미공개셋"], FACT["지연"]),
            fontsize=11, va="top", color="#333333")
    ax.text(20, 98, "%s   ·   자동화 시험 %s" % (FACT["비용"], FACT["시험"]),
            fontsize=11, va="top", color="#333333")
    ax.text(20, 121,
            "정본  src/argus/ablation.py(_current) · agent/pipeline.py · agent/aggregate.py "
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
    pc, pd = _panel(panels, "C"), _panel(panels, "D")
    ax.add_patch(FancyArrowPatch(
        (pc["x"] + pc["w"], pc["y"] + 24), (pd["x"], pd["y"] + pd["h"] - 24),
        arrowstyle="-|>", mutation_scale=15, linewidth=1.5, color="#000000",
        connectionstyle="arc3,rad=-0.2"))

    #: 범례
    ax.add_patch(FancyBboxPatch((20, legend_y), 1860, 74,
                                boxstyle="round,pad=0,rounding_size=5",
                                facecolor="#ffffff", edgecolor="#555555",
                                linewidth=1.0, linestyle=(0, (4, 3))))
    ax.text(32, legend_y + 19, "범례 — 채움색 = LLM 호출 여부", fontsize=11,
            fontweight="bold", va="center")
    for k, (fill, label) in enumerate([
            (BLUE, "○  LLM 0회 — 규칙 · 색인 · 집계"),
            (ORANGE, "●  HCX-005 호출 — 생성 · critic"),
            (GREEN, "◇  결정적 검증 · 안전 · 정적 산출"),
            (GREY, "     경계 — 우리 밖과 만나는 자리"),
            (RED, "×  기각 · 배포 제외")]):
        bx = 32 + k * 372
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
    xml = ('<mxfile host="argus"><diagram name="deploy-v7"><mxGraphModel dx="2000" '
           'dy="1400" grid="0" page="1" pageWidth="1900" pageHeight="1600" '
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

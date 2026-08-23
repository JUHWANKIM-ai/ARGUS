r"""ARGUS **배포 기준** 구조도를 짓는다 (`A84` · 2026-08-22 배포). LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/build_figure_v7.py

## v6 도면과 무엇이 다른가

v6(`argus-algorithm-v6.png`)은 **파이프라인 알고리즘** 도면이다 — 어느 단계가 LLM 을
부르는가를 보였다. 이 도면은 **배포 구조**다. 무엇이 어느 기계에 올라가 있고, 무엇이
빌드 타임에 끝나 있으며, 평가 서버가 어디로 들어오는가를 보인다. 둘은 겹치지 않는다.

## 🔴 배치를 코드가 계산한다

v6 의 규율을 그대로 따른다 — *"수를 한 곳에 모으고 배치를 코드가 계산한다."* 아래
`FACT` 가 사람이 옮겨 적는 유일한 자리다.

**상자 높이는 줄 수에서 계산한다.** 첫 판은 높이를 손으로 적었고 본문이 상자 밖으로
흘러나왔다 — 글이 한 줄 늘 때마다 사람이 높이를 고쳐야 하는 배치는 반드시 어긋난다.

## 🔴 두 벌을 한 배치에서 낸다

`.drawio`(편집용)와 `.png`(문서 삽입용)를 **같은 상자 목록**에서 낸다. 두 벌을 따로
그리면 한쪽만 고쳐진다 — 이 저장소가 되풀이해 겪은 실패 양식이다.

## 도면이 코드와 어긋나면 코드가 맞다

정본은 `src/argus/ablation.py`(`_current`) · `src/argus/agent/pipeline.py` ·
`deploy/README.md` 다.
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
    "arm": "A84",
    "골든셋": "112 / 112 — 평가 서버 3회 전부 동일",   # scratch/audit/axis_compare.json
    "미공개셋": "50~53 / 54 (3회)",                   # scratch/audit/heldout_a84_rounds.json
    "지연": "중앙값 10.6초 · 최대 46.7초",            # server_* 336응답 실측
    "상한": "규약 300초의 15.6%",
    "비용": "질의당 48.1원",                          # (720,718+46,387)토큰/112 × 7.02원
    "시험": "1,958건",
}

#: 색 = 그 상자가 LLM 을 부르는가. v6 의 범례를 그대로 잇는다.
BLUE, ORANGE, GREEN, GREY = "#dae8fc", "#ffe6cc", "#d5e8d4", "#f2f2f2"
EDGE = {BLUE: "#6c8ebf", ORANGE: "#d79b00", GREEN: "#82b366", GREY: "#8c8c8c"}

TITLE_H, LINE_H, PAD_TOP, PAD_BOT = 20, 15.5, 12, 10


def box(x, y, w, title, body, fill):
    """높이를 본문 줄 수에서 계산한다. 손으로 적으면 반드시 어긋난다."""
    lines = body.count("\n") + 1 if body else 0
    h = PAD_TOP + TITLE_H + lines * LINE_H + PAD_BOT
    return [x, y, w, h, title, body, fill]


#: --- 레인 기하 -------------------------------------------------------------
OFF_Y = 126                      # ① 오프라인 띠
SRV_X, SRV_W, SRV_Y = 20, 690, 288     # ② 서버
SVC_X, SVC_W, SVC_Y = 760, 400, 288    # ③ 서비스

BOXES = [
    # ── ① 오프라인 — 빌드 타임에 끝난다 (가로 띠) ─────────────────────────
    box(20, OFF_Y, 330, "DART 원본 코퍼스",
        "상장사 70곳 · 2023.01~2026.03\n공시 4,902건 · 5.3GB", GREY),
    box(390, OFF_Y, 340, "전처리 · 색인   ○ LLM 0회",
        "정규화 · 정정 체인 복원 · 목차 섹션 분해\n계약상대 그래프 · BM25 색인", BLUE),
    box(770, OFF_Y, 390, "artifacts/  — 서버에 올라가는 것",
        "문서군별 색인 5종 · 섹션 색인 · 계약상대 그래프\n"
        "※ 원본 5.3GB 는 올리지 않는다", BLUE),

    # ── ② 서버 (온라인) ───────────────────────────────────────────────────
    box(SRV_X, SRV_Y, SRV_W, "평가 서버 → GET /answer",
        "주최측이 문항당 1회 질의 · 응답 상한 300초", GREY),
    box(SRV_X, SRV_Y + 88, SRV_W, "A. 입력 · 라우팅   ○ LLM 0회",
        "PII 마스킹 · 프롬프트 인젝션 탐지 · 규칙 라우터 · 거절 게이트 7종", BLUE),
    box(SRV_X, SRV_Y + 164, SRV_W, "B. 검색 · 근거   ○ LLM 0회",
        "메타 프리필터 → BM25 hop 사다리 → 정정 체인 · 계약상대 2-hop", BLUE),
    box(SRV_X, SRV_Y + 240, SRV_W, "B′. 결정적 집계   ○ LLM 0회",
        "집계 · 조회 · 단위 줄을 코드가 계산해 근거 머리에 싣는다", BLUE),
    box(SRV_X, SRV_Y + 316, SRV_W, "C. 생성   ● HCX-005",
        "온도 0.0 · 난이도 상이면 CoT · 자기일관성 k=3 (금액 문항)", ORANGE),
    box(SRV_X, SRV_Y + 392, SRV_W, "D. 검증 · 처분   ○ 대부분 LLM 0회",
        "결정적 검증 4종 · critic · 재생성 1회 · 출력 안전 계층", GREEN),
    box(SRV_X, SRV_Y + 468, SRV_W, "응답 — 5키 JSON",
        "question_id · question · retrieved_context · think_trace · answer", GREY),

    # ── ③ 서비스 ──────────────────────────────────────────────────────────
    box(SVC_X, SVC_Y + 88, SVC_W, "빌드 타임 데이터 생성   ○ LLM 0회",
        "build_service_data.py 가 artifacts 를 읽어\npublic/data/*.json 을 만든다", BLUE),
    box(SVC_X, SVC_Y + 190, SVC_W, "관계 열람 정적 사이트",
        "기업 70곳 · 정정 631건 · 거래상대 145곳\n"
        "※ 서버 · DB · 런타임 API 가 없다\n"
        "트래픽이 늘어도 한계비용은 0 에 가깝다", GREEN),
]

#: (from, to, 나가는 면, 들어오는 면)
ARROWS = [
    (0, 1, "right", "left"), (1, 2, "right", "left"),
    (3, 4, "bottom", "top"), (4, 5, "bottom", "top"), (5, 6, "bottom", "top"),
    (6, 7, "bottom", "top"), (7, 8, "bottom", "top"), (8, 9, "bottom", "top"),
    (10, 11, "bottom", "top"),
]
#: 오프라인 산출물이 두 갈래로 간다. 같은 원천을 읽되 서로의 장애가 전파되지 않는다.
DASHED = [(2, 5, "bottom", "right"), (2, 10, "bottom", "top")]


def _anchor(b, side):
    x, y, w, h = b[0], b[1], b[2], b[3]
    return {"bottom": (x + w / 2, y + h), "top": (x + w / 2, y),
            "right": (x + w, y + h / 2), "left": (x, y + h / 2)}[side]


def emit_drawio() -> None:
    """draw.io 로 열어 손볼 수 있는 벌. PNG 와 같은 좌표·같은 목록을 쓴다."""
    cells = []
    for i, (x, y, w, h, title, body, fill) in enumerate(BOXES):
        label = "&lt;b&gt;%s&lt;/b&gt;&#10;%s" % (
            html.escape(title), html.escape(body).replace("\n", "&#10;"))
        style = ("rounded=1;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
                 "spacing=6;fillColor=%s;strokeColor=%s;fontColor=#000000;fontSize=11"
                 % (fill, EDGE[fill]))
        cells.append('<mxCell id="b%d" value="%s" style="%s" vertex="1" parent="1">'
                     '<mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry"/>'
                     "</mxCell>" % (i, label, style, x, y, w, h))
    for j, (a, b, _, _) in enumerate(ARROWS + DASHED):
        style = "edgeStyle=orthogonalEdgeStyle;html=1;strokeColor=#000000"
        if j >= len(ARROWS):
            style += ";dashed=1;strokeColor=#666666"
        cells.append('<mxCell id="e%d" style="%s" edge="1" parent="1" source="b%d" '
                     'target="b%d"><mxGeometry relative="1" as="geometry"/></mxCell>'
                     % (j, style, a, b))
    xml = ('<mxfile host="argus"><diagram name="deploy-v7"><mxGraphModel dx="1500" '
           'dy="950" grid="0" page="1" pageWidth="1200" pageHeight="820" '
           'background="#ffffff" math="0" shadow="0"><root>'
           '<mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) +
           "</root></mxGraphModel></diagram></mxfile>")
    DRAWIO.write_text(xml, encoding="utf-8")


def _font() -> str:
    """한글이 깨지면 도면이 쓸모없다. 있는 것을 골라 쓰고, 없으면 세워 알린다."""
    for name in ("Malgun Gothic", "맑은 고딕", "NanumGothic", "Gulim"):
        try:
            fm.findfont(fm.FontProperties(family=name), fallback_to_default=False)
            return name
        except Exception:
            continue
    raise SystemExit("※ 한글 글꼴을 못 찾았습니다 — 도면의 글씨가 깨집니다")


def emit_png() -> None:
    plt.rcParams["font.family"] = _font()
    plt.rcParams["axes.unicode_minus"] = False

    W, H = 1180, 906
    fig, ax = plt.subplots(figsize=(15.2, 11.7), dpi=200)
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)                      # y 를 뒤집어 도면 좌표와 맞춘다
    ax.axis("off")
    fig.patch.set_facecolor("#ffffff")

    ax.text(20, 20, "ARGUS 공시 Agent — 배포 구조 %s   (2026-08-22 기준)" % FACT["arm"],
            fontsize=17.5, fontweight="bold", va="top")
    ax.text(20, 50,
            "골든셋 %s     ·     미공개셋 %s     ·     %s"
            % (FACT["골든셋"], FACT["미공개셋"], FACT["비용"]),
            fontsize=10.5, va="top", color="#333333")
    ax.text(20, 68,
            "응답 지연 %s (%s)     ·     자동화 시험 %s"
            % (FACT["지연"], FACT["상한"], FACT["시험"]),
            fontsize=10.5, va="top", color="#333333")

    for x, y, title in [
            (20, OFF_Y - 12, "①  오프라인 — 빌드 타임에 끝난다 · LLM 0회"),
            (SRV_X, SRV_Y - 12,
             "②  서버 — NCP c2-g3a 2vCPU/4GB · systemd argus · 공인 IP 223.130.129.131"),
            (SVC_X, SVC_Y - 12, "③  서비스 — 정적 사이트")]:
        ax.text(x, y, title, fontsize=11, fontweight="bold", va="bottom")

    for x, y, w, h, title, body, fill in BOXES:
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0,rounding_size=5",
            facecolor=fill, edgecolor=EDGE[fill], linewidth=1.2))
        ax.text(x + 11, y + PAD_TOP + 3, title, fontsize=10.3, fontweight="bold",
                va="top")
        if body:
            ax.text(x + 11, y + PAD_TOP + TITLE_H + 1, body, fontsize=8.9, va="top",
                    color="#222222", linespacing=1.5)

    for a, b, sa, sb in ARROWS:
        ax.add_patch(FancyArrowPatch(_anchor(BOXES[a], sa), _anchor(BOXES[b], sb),
                                     arrowstyle="-|>", mutation_scale=12,
                                     linewidth=1.2, color="#000000"))
    for a, b, sa, sb in DASHED:
        ax.add_patch(FancyArrowPatch(
            _anchor(BOXES[a], sa), _anchor(BOXES[b], sb), arrowstyle="-|>",
            mutation_scale=12, linewidth=1.1, color="#7f7f7f",
            linestyle=(0, (5, 3)), connectionstyle="arc3,rad=0.16"))

    #: 범례 — 인쇄해 색을 잃어도 기호가 남는다.
    lx, ly, lw = SVC_X, SVC_Y + 330, SVC_W
    ax.add_patch(FancyBboxPatch((lx, ly), lw, 214,
                                boxstyle="round,pad=0,rounding_size=5",
                                facecolor="#ffffff", edgecolor="#8c8c8c",
                                linewidth=1.0, linestyle=(0, (3, 2))))
    ax.text(lx + 12, ly + 20, "범례 — 색 = LLM 호출 여부", fontsize=10,
            fontweight="bold", va="center")
    for k, (fill, label) in enumerate([
            (BLUE, "○  LLM 0회 — 규칙 · 색인 · 집계"),
            (ORANGE, "●  HCX-005 호출 — 생성 구간"),
            (GREEN, "◇  검증 · 안전 · 정적 산출"),
            (GREY, "     경계 — 우리 밖과 만나는 자리")]):
        yy = ly + 42 + k * 26
        ax.add_patch(FancyBboxPatch((lx + 12, yy), 20, 14,
                                    boxstyle="round,pad=0,rounding_size=2",
                                    facecolor=fill, edgecolor=EDGE[fill], linewidth=1.0))
        ax.text(lx + 40, yy + 7, label, fontsize=9, va="center")
    ax.text(lx + 12, ly + 154,
            "점선 = 오프라인 산출물이 두 갈래로 간다.\n"
            "서버와 사이트가 같은 원천을 읽되\n"
            "서로의 장애는 전파되지 않는다.",
            fontsize=8.6, va="top", color="#333333", linespacing=1.5)

    ax.text(20, 818,
            "※  남은 흠 하나 — 근거 완전성 167/168 (Q-110). 필수 근거 여섯 중 한 건이 답변 근거에 안 실린다.\n"
            "     회수가 못 닿는 것이 아니라 파트너가 6곳인데 근거 칸이 6개라 차선의 2번째가 잘린다.",
            fontsize=9, va="top", color="#b85450", linespacing=1.6)
    ax.text(20, 876,
            "정본은 src/argus/ablation.py(_current) · src/argus/agent/pipeline.py · deploy/README.md 다. "
            "도면이 코드와 어긋나면 코드가 맞다.",
            fontsize=8.4, va="top", color="#777777")

    fig.savefig(PNG, bbox_inches="tight", facecolor="#ffffff", pad_inches=0.25)
    plt.close(fig)


def main() -> int:
    PNG.parent.mkdir(parents=True, exist_ok=True)
    emit_drawio()
    emit_png()
    print("%s\n%s" % (PNG, DRAWIO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

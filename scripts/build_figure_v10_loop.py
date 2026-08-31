r"""ARGUS 에이전트 인지 루프 (`A92` · 2026-08-28). LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/build_figure_v10_loop.py

`build_figure_v10.py` 가 **배포 구조도**(식별자를 빠짐없이 싣는 훑기용)라면 이 파일은 같은
내용을 **에이전트 서베이 도식의 골격**으로 옮긴 것이다. 제안서 본문에 싣는 판이다.

## 참조 도식의 골격을 따르는 부분

    환경(질의) → 지각 → 두뇌(저장 · 결정) → 행동 → 환경
    두뇌 안이 저장과 결정으로 갈리고, 그 사이를 적재·조회 화살표가 오간다

## 🔴 참조 도식과 **다르게** 만든 것 — 넷

    ① 블록이 셋이 아니라 **넷**이다. 참조는 지각·두뇌·행동 셋이고 우리는
       **검증·처분**을 별 블록으로 세운다. 생성 뒤에 결정적 검증이 오는 것이
       이 시스템의 성격이므로, 그것을 행동 안에 묻으면 도식이 구조를 안 나른다.
    ② **분기가 있다.** 관문이 생성 전에 갈라 환경으로 되돌아가는 거절 경로를 그린다.
       참조 도식은 분기 없는 단일 순환이다.
    ③ **채움색이 LLM 호출 여부를 나른다.** 참조 도식에 없는 축이고, 우리 도면
       전체(v7~v10)가 쓰는 규약이다. 색을 잃어도 기호(○ ● ◇)가 남는다.
    ④ 저장 칸에 **미구현 항목을 점선으로** 함께 싣는다. 있는 것과 없는 것을
       같은 그림에서 가른다.

## 🔴 이름과 수치는 코드·산출물에서 가져온다

    스위치 73 켬 / 18 끔   argus.ablation.A92 의 bool 필드 (전체 91)
    도구 15종              argus.agent.tools.TOOL_SPECS (조회 8 · 계산 7)
    거절 사유 6갈래         argus.agent.router 의 reason="…" 여섯
    결정적 검증 4종         argus.agent.critic.verify_*
    골든 4회               scratch/audit/axis_compare.json
    미공개 4회             scratch/audit/heldout_a92h_rounds.json

도면이 코드와 어긋나면 **코드가 맞다.**

## 🔴 좌표 환산

`plt.subplots` 는 축을 그림의 77% 에만 앉히므로 1pt 가 1.3889 가 아니라 1.8038 단위다.
축을 꽉 채우고(`add_axes([0,0,1,1])`) 그림 크기를 `UPI` 로 정해 전제와 실제를 맞춘다.
v8 에서 고친 것을 그대로 쓴다.
"""
from __future__ import annotations

import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm                                    # noqa: E402
import matplotlib.pyplot as plt                                         # noqa: E402
from matplotlib.patches import (Circle, FancyArrowPatch,                # noqa: E402
                                FancyBboxPatch, Polygon, Rectangle,
                                Wedge)

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PNG = ROOT / "docs/figures/argus-agent-loop-v10.png"

UPI = 100.0 / 0.77
PT = UPI / 72.0

BLUE, ORANGE, GREEN, GREY, RED = ("#dae8fc", "#ffe6cc", "#d5e8d4", "#f2f2f2", "#f8cecc")
PURPLE = "#e1d5e7"
EDGE = {BLUE: "#6c8ebf", ORANGE: "#d79b00", GREEN: "#82b366", GREY: "#8c8c8c",
        RED: "#b85450", PURPLE: "#9673a6"}
INK, MUTE, FAINT = "#111111", "#444444", "#888888"
#: 참조 도식의 연한 살구색 배경. 블록 바탕에만 쓴다.
TINT_IN, TINT_OK, TINT_ENV = "#fff6ef", "#f4faf3", "#f7f7f7"

#: 🔴 사람이 옮겨 적는 유일한 자리. 출처를 함께 적는다.
FACT = {
    "구성": "구성 A92 = _current · 스위치 73 켬 / 18 끔",
    # scratch/audit/axis_compare.json · heldout_a92h_rounds.json
    "성적": "골든셋 112 · 112 · 112 · 111 / 112 (서버 4회)   ·   "
          "미공개셋 52 · 52 · 53 · 52 / 54 (4회)",
    "예산": "예산 steps 8 · hops 3 · calls 6 · tokens 100K   ·   "
          "지연 중앙값 10.6초 · 최대 46.7초 (규약 상한 300초)",
}


# --- 그림판 ------------------------------------------------------------------
def _font() -> str:
    for name in ("Malgun Gothic", "맑은 고딕", "NanumGothic", "Gulim"):
        try:
            fm.findfont(fm.FontProperties(family=name), fallback_to_default=False)
            return name
        except Exception:
            continue
    raise SystemExit("※ 한글 글꼴을 못 찾았습니다 — 도면의 글씨가 깨집니다")


def canvas(w, h):
    plt.rcParams["font.family"] = _font()
    plt.rcParams["axes.unicode_minus"] = False
    fig = plt.figure(figsize=(w / UPI, h / UPI), dpi=170)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    fig.patch.set_facecolor("#ffffff")
    return fig, ax


def box(ax, x, y, w, h, fill="#ffffff", edge=None, lw=1.1, dashed=False, r=5, z=3):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=%g" % r,
        facecolor=fill, edgecolor=edge or EDGE.get(fill, "#555555"), linewidth=lw,
        linestyle=(0, (4, 3)) if dashed else "solid", zorder=z))


def panel(ax, x, y, w, h, fill, edge, lw=1.6, r=10):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle="round,pad=0,rounding_size=%g" % r,
                                facecolor=fill, edgecolor=edge, linewidth=lw, zorder=1))


def tab(ax, cx, y, w, label, fill, size=12.5):
    """참조 도식의 「이름표」 — 블록 위에 걸치는 둥근 칸."""
    box(ax, cx - w / 2, y, w, 36, fill, r=8, lw=1.3, z=6)
    ax.text(cx, y + 18, label, fontsize=size, fontweight="bold", ha="center",
            va="center", zorder=7)


def txt(ax, x, y, s, size=9, bold=False, color=INK, ha="left", va="top", ls=1.45, z=5):
    ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, linespacing=ls,
            fontweight="bold" if bold else "normal", zorder=z)


def item(ax, x, y, w, title, sub, fill, dashed=False, tsize=9.6, ssize=8.3):
    lines = sub.count("\n") + 1 if sub else 0
    h = 8 + 10 * PT * 1.30 + lines * (ssize * PT * 1.45) + 7
    box(ax, x, y, w, h, fill, dashed=dashed, r=4)
    txt(ax, x + 9, y + 9, title, size=tsize, bold=True)
    if sub:
        txt(ax, x + 9, y + 9 + 10 * PT * 1.30, sub, size=ssize, color="#222222")
    return h


def arrow(ax, p, q, lw=2.6, color="#3a3a3a", rad=0.0, scale=20, z=4, dashed=False):
    ax.add_patch(FancyArrowPatch(
        p, q, arrowstyle="-|>", mutation_scale=scale, linewidth=lw, color=color,
        connectionstyle="arc3,rad=%g" % rad, zorder=z,
        linestyle=(0, (4, 3)) if dashed else "solid"))


def flag(ax, x, y, s, color=MUTE, size=9.2, ha="center"):
    """화살표 꼬리표. 흰 바탕을 깔아 선에 글씨가 그어지지 않게 한다."""
    ax.text(x, y, s, fontsize=size, color=color, ha=ha, va="center", zorder=8,
            bbox=dict(facecolor="#ffffff", edgecolor="none", pad=2.2))


def bubble(ax, x, y, w, h, s, size=8.6):
    box(ax, x, y, w, h, "#ffffff", "#8a8a8a", lw=1.1, r=9)
    txt(ax, x + 11, y + 10, s, size=size, ls=1.44)


# --- 아이콘 -------------------------------------------------------------------
def ico_robot(ax, cx, cy, s=1.0, fill=ORANGE):
    e = EDGE[fill]
    ax.add_patch(FancyBboxPatch((cx - 15 * s, cy - 10 * s), 30 * s, 21 * s,
                                boxstyle="round,pad=0,rounding_size=%g" % (5 * s),
                                facecolor=fill, edgecolor=e, lw=1.2, zorder=5))
    ax.plot([cx, cx], [cy - 10 * s, cy - 17 * s], color=e, lw=1.2, zorder=5)
    ax.add_patch(Circle((cx, cy - 18.5 * s), 2.4 * s, facecolor=e, zorder=5))
    for dx in (-6.5, 6.5):
        ax.add_patch(Circle((cx + dx * s, cy - 1 * s), 3.1 * s, facecolor="#ffffff",
                            edgecolor=e, lw=1.0, zorder=6))
        ax.add_patch(Circle((cx + dx * s, cy - 1 * s), 1.3 * s, facecolor=e, zorder=7))
    ax.plot([cx - 5 * s, cx + 5 * s], [cy + 6 * s, cy + 6 * s], color=e, lw=1.3, zorder=6)
    ax.add_patch(FancyBboxPatch((cx - 10 * s, cy + 13 * s), 20 * s, 8 * s,
                                boxstyle="round,pad=0,rounding_size=%g" % (2 * s),
                                facecolor=fill, edgecolor=e, lw=1.1, zorder=5))


def ico_person(ax, cx, cy, s=1.0, color="#666666"):
    """🔴 어깨는 180~360 이다 — y축이 뒤집혀 있어 0~180 이면 컵이 된다."""
    ax.add_patch(Circle((cx, cy - 9 * s), 6.5 * s, facecolor="#ffffff",
                        edgecolor=color, lw=1.4, zorder=5))
    ax.add_patch(Wedge((cx, cy + 14 * s), 13 * s, 180, 360, facecolor="#ffffff",
                       edgecolor=color, lw=1.4, zorder=5))


def ico_db(ax, cx, cy, s=1.0, fill=BLUE):
    e = EDGE[fill]
    for i, dy in enumerate((-9, -1, 7)):
        ax.add_patch(FancyBboxPatch((cx - 12 * s, cy + dy * s - 3 * s), 24 * s, 7 * s,
                                    boxstyle="round,pad=0,rounding_size=%g" % (3 * s),
                                    facecolor=fill, edgecolor=e, lw=1.1, zorder=5 + i))


def ico_gear(ax, cx, cy, s=1.0, fill=GREEN):
    import math
    e = EDGE[fill]
    for k in range(8):
        a = k * math.pi / 4
        ax.add_patch(Rectangle((cx + 9.5 * s * math.cos(a) - 2.6 * s,
                                cy + 9.5 * s * math.sin(a) - 2.6 * s),
                               5.2 * s, 5.2 * s, facecolor=fill, edgecolor=e,
                               lw=0.9, zorder=5))
    ax.add_patch(Circle((cx, cy), 8.4 * s, facecolor=fill, edgecolor=e, lw=1.3, zorder=6))
    ax.add_patch(Circle((cx, cy), 3.2 * s, facecolor="#ffffff", edgecolor=e, lw=1.1,
                        zorder=7))


def ico_lens(ax, cx, cy, s=1.0, fill=BLUE):
    e = EDGE[fill]
    ax.add_patch(Circle((cx - 2 * s, cy - 3 * s), 9.5 * s, facecolor=fill, edgecolor=e,
                        lw=1.5, zorder=5))
    ax.plot([cx + 5 * s, cx + 13 * s], [cy + 4 * s, cy + 12 * s], color=e, lw=2.4,
            zorder=5, solid_capstyle="round")


def ico_shield(ax, cx, cy, s=1.0, fill=RED):
    e = EDGE[fill]
    ax.add_patch(Polygon([(cx, cy - 13 * s), (cx + 11 * s, cy - 8 * s),
                          (cx + 11 * s, cy + 3 * s), (cx, cy + 14 * s),
                          (cx - 11 * s, cy + 3 * s), (cx - 11 * s, cy - 8 * s)],
                         closed=True, facecolor=fill, edgecolor=e, lw=1.3, zorder=5))
    ax.text(cx, cy + 1 * s, "!", fontsize=11 * s, fontweight="bold", color=e,
            ha="center", va="center", zorder=6)


# --- 도면 ---------------------------------------------------------------------
def main() -> int:
    W, H = 1900, 1180
    PNG.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = canvas(W, H)

    txt(ax, 40, 16, "ARGUS 에이전트 인지 루프 — 환경 · 지각 · 두뇌 · 행동 · 검증",
        size=18.5, bold=True)
    txt(ax, 40, 54, "%s   ·   %s" % (FACT["구성"], FACT["성적"]), size=10.4,
        color="#333333")
    txt(ax, 40, 76, FACT["예산"], size=10.4, color="#333333")
    txt(ax, 40, 100, "정본  src/argus/ablation.py(_current=A92) · agent/pipeline.py "
        "· agent/aggregate.py · agent/critic.py        "
        "※ 도면과 코드가 어긋나면 코드가 맞다", size=9, color=FAINT)

    # ── 환경 ────────────────────────────────────────────────────────────────
    EX, EY, EW, EH = 40, 172, 430, 690
    panel(ax, EX, EY, EW, EH, TINT_ENV, "#9a9a9a", lw=1.4)
    tab(ax, EX + EW / 2, EY - 18, 190, "환경", GREY)
    txt(ax, EX + 20, EY + 34, "평가 서버 · 이용자", size=9.4, color=MUTE)
    ico_person(ax, EX + 42, EY + 118, 0.82)
    bubble(ax, EX + 74, EY + 66, 336, 96,
           "\"한국중부발전을 계약상대로 하는\n공급계약을 공시한 상장사 세 곳 중,\n"
           "계약금액이 가장 큰 곳은 어디인가?\"")
    ico_robot(ax, EX + EW / 2, EY + 258, 1.25, ORANGE)
    txt(ax, EX + EW / 2, EY + 306, "ARGUS", size=12, bold=True, ha="center")
    txt(ax, EX + EW / 2, EY + 328, "단일 에이전트 · 단일 프레임워크", size=8.8,
        color=MUTE, ha="center")
    bubble(ax, EX + 74, EY + 366, 336, 120,
           "\"두산에너빌리티다. 함안복합 파워블록\n계약 583,874,002,800원이며\n"
           "2025-09-23 정정본 기준이다.\n(근거: 접수번호 20250923800193)\"")
    #: 🔴 손으로 적은 높이(62)가 두 줄을 못 담아 넘쳤다. `item` 이 줄 수에서 센다.
    item(ax, EX + 74, EY + 508, 336, "거절도 응답이다",
         "사유를 붙여 되돌린다 — 답할 수 없는 질의에\n그럴듯한 답을 만들지 않는다", RED,
         tsize=9.4, ssize=8.2)
    txt(ax, EX + 20, EY + EH - 46, "응답 5키\nquestion_id · question · retrieved_context "
        "· think_trace · answer", size=8.4, color=MUTE)

    # ── 지각 ────────────────────────────────────────────────────────────────
    PX, PY, PW, PH = 546, 172, 540, 300
    panel(ax, PX, PY, PW, PH, TINT_IN, EDGE[ORANGE])
    tab(ax, PX + PW / 2, PY - 18, 230, "지각 — 질의 해석", ORANGE)
    ico_lens(ax, PX + 30, PY + 46, 0.8, BLUE)
    for i, (nm, sub) in enumerate([
            ("● 기업 해석", "resolve_company · 별칭 색인 · 70개사"),
            ("● 기간 · 문서군", "rcept_dt · doc_group · 정기/거래소/주요/지분"),
            ("● 계약상대 해석", "canonical() · 표기 변이 다섯을 하나로"),
            ("● 질의 유형 판정", "6갈래 · 난이도 · CoT 여부 — 규칙만")]):
        item(ax, PX + 22, PY + 68 + i * 56, PW - 44, nm, sub, BLUE, tsize=9.5,
             ssize=8.2)

    # ── 관문 (분기) ─────────────────────────────────────────────────────────
    GX, GY, GW, GH = 546, 520, 540, 132
    panel(ax, GX, GY, GW, GH, "#fdf3f3", EDGE[RED], lw=1.4)
    tab(ax, GX + GW / 2, GY - 18, 268, "관문 — 생성 전에 갈린다", RED, size=11.5)
    ico_shield(ax, GX + 32, GY + 62, 0.82, RED)
    item(ax, GX + 62, GY + 34, GW - 84, "○ 거절 게이트 6갈래",
         "out_of_universe · speculation · identifier\n"
         "false_premise · ambiguous_reference · injection", BLUE, tsize=9.5, ssize=8.2)
    txt(ax, GX + GW / 2, GY + GH - 22, "곱으로 잠근다 — 표적 낱말과 요구 낱말이 함께 있을 때만",
        size=8.4, color=MUTE, ha="center")

    # ── 두뇌 ────────────────────────────────────────────────────────────────
    BX, BY, BW, BH = 1150, 172, 710, 452
    panel(ax, BX, BY, BW, BH, TINT_IN, EDGE[ORANGE])
    tab(ax, BX + BW / 2, BY - 18, 170, "두뇌", ORANGE)

    box(ax, BX + 22, BY + 40, BW - 44, 210, "#ffffff", "#b0b0b0", lw=1.1, r=7)
    txt(ax, BX + 38, BY + 56, "저장 — 오프라인 자산", size=10.6, bold=True)
    ico_db(ax, BX + 62, BY + 148, 0.85, BLUE)
    item(ax, BX + 100, BY + 82, 300, "● 색인 · 관계 그래프",
         "BM25 3벌 · 섹션 11.2만\n계약상대 145 · 정정 체인 788", BLUE, tsize=9.2,
         ssize=8.1)
    item(ax, BX + 412, BY + 82, 288, "● 정정 최신성",
         "is_latest · related\nstale_note 로 대체본 각인", BLUE, tsize=9.2, ssize=8.1)
    item(ax, BX + 100, BY + 168, 300, "○ 경로 기억", "질의 → 도구 경로 적재\n미구현 · 계획",
         PURPLE, dashed=True, tsize=9.2, ssize=8.1)
    item(ax, BX + 412, BY + 168, 288, "○ 자기 진화", "성적으로 규칙을 고친다\n미구현 · 계획",
         PURPLE, dashed=True, tsize=9.2, ssize=8.1)

    box(ax, BX + 22, BY + 268, BW - 44, 164, "#ffffff", "#b0b0b0", lw=1.1, r=7)
    txt(ax, BX + 38, BY + 284, "결정 — 계획과 집계", size=10.6, bold=True)
    ico_gear(ax, BX + 62, BY + 356, 0.8, GREEN)
    item(ax, BX + 100, BY + 308, 300, "● 회수 계획",
         "hop 사다리 · 충분성 5규칙\n계약상대 근거 칸 8", BLUE, tsize=9.2, ssize=8.1)
    item(ax, BX + 412, BY + 308, 288, "● 결정적 집계",
         "합 · 차 · 몫 · 로스터 · 시계열\n환산 — 코드가 센다", BLUE, tsize=9.2, ssize=8.1)
    item(ax, BX + 100, BY + 384, 600, "◐ LLM 도구 판단 — function_calling 은 재고 후 껐다",
         "", GREY, tsize=9.2)

    #: 저장 ↔ 결정 사이의 오감. 참조 도식의 Summary/Recall·Learn/Retrieve 자리다.
    arrow(ax, (BX + 300, BY + 252), (BX + 300, BY + 300), lw=1.5, scale=13)
    arrow(ax, (BX + 520, BY + 300), (BX + 520, BY + 252), lw=1.5, scale=13)
    flag(ax, BX + 262, BY + 276, "조회", size=8.6)
    flag(ax, BX + 560, BY + 276, "적재", size=8.6)

    # ── 행동 ────────────────────────────────────────────────────────────────
    AX_, AY, AW, AH = 546, 700, 540, 244
    panel(ax, AX_, AY, AW, AH, TINT_IN, EDGE[ORANGE])
    tab(ax, AX_ + AW / 2, AY - 18, 170, "행동", ORANGE)
    for i, (nm, sub, fill) in enumerate([
            ("● 도구 호출", "조회 8종 · 계산 7종 · calls 상한 6", BLUE),
            ("● 근거 조립", "블록 상한 · 정정 각인 · 결정적 줄 머리에", BLUE),
            ("● 답변 생성", "HCX-005 · 온도 0.0 · 자기일관성 k=3", ORANGE)]):
        item(ax, AX_ + 22, AY + 36 + i * 66, AW - 44, nm, sub, fill, tsize=9.5,
             ssize=8.2)

    # ── 검증 · 처분 (참조 도식에 없는 넷째 블록) ─────────────────────────────
    #: 🔴 높이를 손으로 272 로 적었더니 예산 가드 줄이 위 상자와 겹쳤다. 안쪽 배치에서
    #: 역산한다 — 두 줄 항목 두 단(각 92) + 예산 가드 한 줄 + 여백.
    VX, VY, VW = 1150, 672, 710
    VH = 56 + 92 + 92 + 46 + 20
    panel(ax, VX, VY, VW, VH, TINT_OK, EDGE[GREEN])
    tab(ax, VX + VW / 2, VY - 18, 300, "검증 · 처분", GREEN, size=12.5)
    txt(ax, VX + 22, VY + 34, "▲ 생성 뒤에 결정적 검증이 온다 — 근거 없는 답이 나가는 "
        "경로를 막는다", size=8.8, color=EDGE[GREEN])
    for i, (nm, sub) in enumerate([
            ("◇ 결정적 검증 4종", "verify_calculation · verify_answer_arithmetic\n"
                             "verify_number_provenance · verify_completeness"),
            ("◇ 수리 · 문면 위생", "수리 8갈래 · 근거 없는 총액 삭제\n"
                             "로스터 합계 금지 · 검증 각주 분리")]):
        item(ax, VX + 22, VY + 56 + i * 92, 340, nm, sub, GREEN, tsize=9.3, ssize=8.1)
    item(ax, VX + 374, VY + 56, 314, "◇ 표기 부착 6갈래",
         "attach_citation · cite_used\ncite_chain_pair · 답변이 쓴 근거만", GREEN,
         tsize=9.3, ssize=8.1)
    item(ax, VX + 374, VY + 148, 314, "● critic · 재생성 1회",
         "표기 누락 시에만 발화\nregenerate_on_verify", ORANGE, tsize=9.3, ssize=8.1)
    item(ax, VX + 22, VY + 240, VW - 44,
         "◇ 예산 가드 — steps 8 · hops 3 · calls 6 · tokens 100K", "", GREEN, tsize=9.3)

    # ── 흐름 ────────────────────────────────────────────────────────────────
    arrow(ax, (EX + EW, EY + 120), (PX - 8, EY + 220), rad=-0.12)
    flag(ax, 508, 268, "입력")
    arrow(ax, (PX + PW + 8, PY + 150), (BX - 8, PY + 150), rad=0.0)
    flag(ax, 1118, 150, "해석")
    #: 🔴 두뇌의 **왼쪽 변**에서 나간다. 아래 변에서 나가면 x 가 1150 이상이라 어떤
    #: 직선도 검증 패널의 좌상단 모서리를 스친다 — 홈(1086~1150) 안에 머물게 한다.
    arrow(ax, (BX - 8, BY + BH - 30), (AX_ + AW + 10, AY + 40), rad=0.0)
    flag(ax, 1118, 664, "계획 · 집계")
    arrow(ax, (AX_ + AW + 8, AY + 190), (VX - 8, VY + 190), rad=0.0)
    flag(ax, 1118, 862, "산출")
    arrow(ax, (VX + 60, VY + VH + 8), (EX + EW / 2 + 40, EY + EH + 8),
          rad=-0.06, lw=2.2)
    flag(ax, 990, 1000, "응답 — 근거 표기와 함께")
    #: 분기 — 관문이 갈라 환경으로 되돌린다. 점선·붉은색으로 본 흐름과 구별한다.
    arrow(ax, (GX - 8, GY + 66), (EX + EW, EY + 540), rad=0.10, lw=2.0,
          color=EDGE[RED], dashed=True)
    flag(ax, 508, 640, "거절", color=EDGE[RED])

    # ── 범례 ────────────────────────────────────────────────────────────────
    LY = 1040
    box(ax, 40, LY, 1820, 62, "#ffffff", "#555555", lw=1.0, dashed=True, r=5)
    txt(ax, 54, LY + 15, "범례 — 채움색이 LLM 호출 여부를 나른다", size=10.5, bold=True,
        va="center")
    for k, (fill, label, dash) in enumerate([
            (BLUE, "○  LLM 0회 — 규칙 · 색인 · 계획 · 집계", False),
            (ORANGE, "●  HCX-005 호출 — 생성 · critic", False),
            (GREEN, "◇  결정적 검증 · 표기 · 예산", False),
            (GREY, "◐  구현했으나 재고 후 껐다", False),
            (PURPLE, "○  미구현 · 계획", True)]):
        bx = 54 + k * 358
        box(ax, bx, LY + 34, 17, 14, fill, r=2, lw=1.0, dashed=dash)
        txt(ax, bx + 25, LY + 41, label, size=8.9, va="center")
    txt(ax, 1846, LY + 15, "루프는 한 바퀴다 — 재생성 1회를 빼면 되돌아가지 않는다",
        size=8.8, color=MUTE, ha="right", va="center")

    fig.savefig(PNG, bbox_inches="tight", facecolor="#ffffff", pad_inches=0.24)
    plt.close(fig)
    print(PNG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

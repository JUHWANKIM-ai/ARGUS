"""ARGUS v6 알고리즘 도면을 짓는다. LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/build_figure_v6.py

🔴 **손으로 XML 을 고치지 않는다.** v4 도면은 63셀을 손으로 썼고, 성적 하나가 바뀔
때마다 어느 칸을 고칠지 사람이 찾아야 했다. 여기서는 **수를 한 곳에** 모으고 배치를
코드가 계산한다.

🔴 **도면이 코드와 어긋나면 코드가 맞다.** 이 스크립트는 그 어긋남을 줄이는 장치이지
없애는 장치가 아니다 — 아래 `SCORE` 는 사람이 옮겨 적는 값이다.

## 규격은 v3 를 따른다 (사용자 지시 2026-08-17)

    페이지     1760 × 1600 · background #ffffff
    글씨       fontColor=#000000 전량
    채움색     **범주를 나른다** — 색 = LLM 호출 여부

🔴 **v5 의 흑백과 배치되지 않는다.** v5 는 *"흰 바탕 검은 글씨"* 지시를 색을 **전부**
빼는 것으로 읽었고, 그래서 색이 나르던 *"파이프라인의 대부분이 LLM 0회"* 라는 한 줄을
글머리 기호로 옮겨야 했다. v3 는 **바탕과 글씨는 흑백이고 채움만 색**이라 두 요구가
한 도면에서 산다. 여기서는 **색과 기호를 함께 쓴다** — 인쇄해 색을 잃어도 기호가 남는다.
"""
from __future__ import annotations

import html
import io
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/figures/argus-algorithm-v6.drawio"

#: 🔴 **성적은 여기 한 곳에만 적는다.** 정본은 `artifacts/ablation/v5/cite_*`.
#:
#: 🔴 **두 수를 함께 싣는다.** 112문항 성적은 `RequestCache` 재생으로 나온 값이라
#: 실전보다 낙관적이다. 처음 보는 문항 셋(newset 17/20 · heldout_v2 17/18 ·
#: heldout_v3 15/16)이 캐시 없이 잰 값이고 **그쪽이 기대값**이다. 한쪽만 실으면
#: 도면이 거짓말을 한다.
SCORE = {
    "arm": "A38", "그리드": "72/72", "전체": "88/88", "밸류체인": "10/12",
    "안전성": "12/12", "정보한계": "6/6", "회귀": "10/10", "인용률": "0.986",
    "합계": "110/112", "정본": "artifacts/ablation/v5/cite_*",
    "실전": "49/54 (90.7%)", "실전정본": "newset 17/20 · heldout_v2 17/18 · heldout_v3 15/16",
}

# --- v3 팔레트 — 채움색이 LLM 호출 여부를 나른다 -------------------------------------
#
# 🔴 색과 기호를 **짝으로 묶는다.** 따로 두면 호출부에서 어긋나고, 어긋난 기호는 색이
# 틀린 것보다 나쁘다 — 읽는 사람이 못 알아챈다.
BASE = "fontColor=#000000;"
BLUE = (BASE + "fillColor=#DAE8FC;strokeColor=#3A6FA8;", "○")     # LLM 0회
ORANGE = (BASE + "fillColor=#FFE6CC;strokeColor=#C87A2F;", "●")   # LLM 호출
GREEN = (BASE + "fillColor=#D5E8D4;strokeColor=#4F8A55;", "◇")    # 검증 · 안전
YELLOW = (BASE + "fillColor=#FFF2CC;strokeColor=#C9A227;", "◆")   # 분기 · 판정
RED = (BASE + "fillColor=#F8CECC;strokeColor=#B3453F;", "✕")      # 기각 · 남은 흠
GREY = "fillColor=none;strokeColor=none;fontColor=#000000;"
NEW = "strokeWidth=3;"                                            # v6 신설 · 교정

cells: list[str] = []


def esc(markup: str) -> str:
    """🔴 **drawio 의 `value=` 는 HTML 을 escape 한 채 담는다.** 태그째 한 번만
    escape 한다 — v5 첫 판은 escape 한 뒤 `&lt;` 를 `<` 로 되돌려 XML 을 깨뜨렸다."""
    return html.escape(markup, quote=True)


def em(style):
    """v6 신설·교정 강조. 스타일이 짝이라 `style + NEW` 로는 못 잇는다."""
    body, mark = style
    return (body + NEW, mark)


def box(cid, x, y, w, h, label, style, note="") -> None:
    extra = ""
    if isinstance(style, tuple):
        style, mark = style
    else:                                      # `em(BLUE)` 처럼 이어 붙인 경우
        for base, sign in (BLUE, ORANGE, GREEN, YELLOW, RED):
            if style.startswith(base):
                extra, style, mark = style[len(base):], base, sign
                break
        else:
            mark = ""
    body = f"<b>{mark + ' ' if mark else ''}{label}</b>"
    if note:
        body += f"<br><font style='font-size:10px'>{note}</font>"
    cells.append(
        f'<mxCell id="{cid}" value="{esc(body)}" '
        f'style="rounded=1;whiteSpace=wrap;html=1;align=left;verticalAlign=top;'
        f'spacing=7;fontSize=11;{style}{extra}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" /></mxCell>')


def text(cid, x, y, w, h, body, size=11) -> None:
    cells.append(
        f'<mxCell id="{cid}" value="{esc(body)}" '
        f'style="rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;'
        f'fontSize={size};{GREY}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" /></mxCell>')


def band(cid, x, y, w, h, title) -> None:
    cells.append(
        f'<mxCell id="{cid}" value="{esc(title)}" style="rounded=0;whiteSpace=wrap;'
        f'html=1;align=left;verticalAlign=top;spacing=8;fontSize=14;fontStyle=1;'
        f'fillColor=none;strokeColor=#333333;dashed=1;fontColor=#000000;" '
        f'vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" /></mxCell>')


def arrow(cid, src, dst) -> None:
    cells.append(
        f'<mxCell id="{cid}" style="edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;'
        f'strokeColor=#333333;" edge="1" parent="1" source="{src}" target="{dst}">'
        f'<mxGeometry relative="1" as="geometry" /></mxCell>')


# --- 머리 -------------------------------------------------------------------------
text("t1", 40, 16, 1240, 30,
     f"ARGUS 공시 Agent — 온라인 파이프라인 <b>v6</b> · 운영 기본값 "
     f"<b>{SCORE['arm']}</b> (제출 구성)", 20)
text("t2", 40, 48, 1260, 56,
     f"HyperCLOVA X (HCX-005) · 검색·검증·집계·인용은 <b>전부 LLM 0회</b><br>"
     f"<b>골든셋 {SCORE['합계']}</b> — 그리드 {SCORE['그리드']} · 전체 {SCORE['전체']} · "
     f"밸류체인 {SCORE['밸류체인']} · 안전성 {SCORE['안전성']} · 인용률 {SCORE['인용률']}"
     f"<br>🔴 <b>처음 보는 문항 {SCORE['실전']}</b> — 캐시 없이 잰 값이고 "
     f"<b>이쪽이 기대값</b>이다")
text("t3", 40, 104, 1260, 30,
     f"<font style='font-size:10px'>골든 정본 <i>{SCORE['정본']}</i> · 실전 정본 "
     f"<i>{SCORE['실전정본']}</i><br>"
     f"코드가 정본이다 — <i>src/argus/ablation.py</i> · <i>agent/pipeline.py</i> · "
     f"<i>agent/aggregate.py</i> · <i>agent/reasoning.py</i> · <i>agent/tools.py</i>. "
     f"도면이 코드와 어긋나면 코드가 맞다.</font>")

# --- 범례 -------------------------------------------------------------------------
band("lg", 1320, 16, 400, 232, "범례 —  색 = LLM 호출 여부")
box("lg1", 1338, 54, 180, 40, "LLM 0회", BLUE, "규칙 · 색인 · 집계 · 조회")
box("lg2", 1528, 54, 174, 40, "LLM 호출", ORANGE, "HCX-005 · 크레딧")
box("lg3", 1338, 102, 180, 40, "검증 · 안전", GREEN, "결정적 검사 · PII")
box("lg4", 1528, 102, 174, 40, "분기 · 판정", YELLOW, "")
box("lg5", 1338, 150, 180, 40, "기각 · 남은 흠", RED, "재 보고 안 채택")
text("lg6", 1528, 150, 174, 44,
     "<font style='font-size:10px'>굵은 테두리 =<br><b>v6 에서 새로 생기거나<br>"
     "고친 단계</b></font>")
text("lg7", 1338, 198, 366, 44,
     "<font style='font-size:10px'>🔴 <b>파랑이 파이프라인의 대부분이다</b> — 검색·도구·"
     "검증·집계·조회가 전부 LLM 0회다. 인쇄해 색을 잃어도 기호가 남는다.</font>")

# --- A. 입력 · 라우팅 ---------------------------------------------------------------
band("bA", 40, 148, 1260, 274, "A.  입력 · 라우팅        LLM 0회")
box("a1", 60, 186, 250, 44, "질의 수신", BLUE, "GET /answer · question_id · question")
box("a2", 60, 242, 250, 52, "입력 안전 계층", BLUE,
    "sanitize_question · PII 마스킹 · 인젝션 탐지")
box("a3", 60, 306, 250, 52, "라우터", BLUE, "route() — 규칙만으로 가른다")
box("a4", 340, 306, 150, 52, "답할 수 있는가", YELLOW, "")

text("a5", 520, 178, 340, 18, "<b>한계 고지 — 다섯 갈래</b>", 12)
box("a6", 520, 200, 340, 32, "범위 밖", BLUE, "70개사 · 2023.01~2026.03 밖")
box("a7", 520, 238, 340, 32, "지목한 보고서 부재", BLUE, "어느 보고서인지 되묻는다")
box("a8", 520, 276, 340, 32, "지시대상 불명", BLUE, "어느 기업인지 되묻는다")
box("a9", 520, 314, 340, 32, "미래 추정 (A31)", BLUE, "주가·전망·목표주가·매수 의견")
box("a10", 520, 352, 340, 44, "식별번호 전사 (A32 · v6 교정)", em(BLUE),
    "🔴 법인·사업자등록번호를 뺐다 — 개인정보가 아니다")

text("a11", 880, 196, 400, 210,
     "🔴 <b>다섯 갈래 전부 두 신호의 곱집합으로만 발화한다.</b> <i>향후</i>·<i>전망</i>·"
     "<i>연락처</i> 는 공시 본문에도 흔해서 한쪽만으로 가르면 정답 문항이 거절이 된다. "
     "평가셋 112문항 전수 <b>오탐 0</b>.<br><br>"
     "🔴 <b>v6 가 A32 를 좁혔다.</b> 게이트가 법인 식별번호와 개인 식별번호를 안 갈라 "
     "<i>“SK이노베이션의 법인등록번호는?”</i> 을 통째로 거절했다. 평가셋이 그 문항에 "
     "적어 둔 기대는 정반대였고, <b>직전 감사는 그 거절을 게이트가 잘 도는 증거로 "
     "인용했다</b> — 게이트를 넣은 세대가 스스로를 채점한 것이다.<br><br>"
     "S-002 는 그대로 막힌다 — <i>본사 <b>전화번호</b>와 계열회사 법인등록번호를…</i> "
     "라 전화번호로 걸린다. 좁힘의 영향은 <b>한 문항뿐</b>(실측).<br><br>"
     "🔴 이 갈래는 <b>크레딧 0원</b>으로 끝난다.", 10)

# --- B. 검색 · 근거 -----------------------------------------------------------------
band("bB", 40, 434, 1260, 262, "B.  검색 · 근거        LLM 0회")
box("b1", 60, 472, 240, 38, "메타 프리필터", BLUE, "corp_name · rcept_dt · group")
box("b2", 60, 518, 240, 42, "BM25 hop 사다리", BLUE, "narrow → relax → global")
box("b3", 60, 568, 240, 42, "섹션 인덱스 hop", BLUE, "정기공시 목차 경로")
box("b4", 60, 618, 240, 42, "계약상대 그래프 hop", BLUE, "발주처 2-hop fan-in/out")
box("b5", 320, 472, 250, 44, "정정체인 추적 hop", BLUE, "chain_probe + get_history")
box("b6", 320, 524, 250, 50, "역방향 체인 (A33)", BLUE,
    "정정본 → 원공시 · 기업별 라운드로빈")
box("b7", 320, 582, 250, 38, "근거 성형", BLUE, "생성 직전에만")
box("b8", 320, 628, 250, 38, "정정사유·주요내용 (A30)", BLUE, "런타임 사본에만 · 색인 불변")
box("b9", 590, 472, 260, 58, "법인번호 행 조회 (v6 신설)", em(BLUE),
    "tools.corp_number_line — 계열회사 표에서 한 줄만")

text("b10", 590, 540, 690, 148,
     "🔴 <b>조각이 문제였지 근거 칸이 아니었다.</b> 목차를 <i>XII. 상세표 &gt; 2. "
     "계열회사 현황(상세)</i> 로 좁히면 검색은 <b>맞는 보고서</b>를 고른다. 그런데 그 "
     "섹션이 34,122자라 6조각으로 쪼개지고 BM25 가 회사명이 빽빽한 4번 조각을 고른다 — "
     "정작 찾는 행은 <b>703자 지점(1번 조각)</b>에 있다."
     "<br><br>칸을 늘리는 것은 v4 가 세 번 기각한 방향이고(A16·A17·depth) 6조각을 다 "
     "실으면 34K 자가 근거를 통째로 먹는다. 그래서 <b>섹션 전문에서 행 하나만</b> 뽑아 "
     "머리에 둔다 — 집계 한 줄과 같은 자리다."
     "<br><br>🔴 <code>aggregate</code> 가 아니라 <code>tools</code> 에 뒀다. 그 모듈에 "
     "파일 접근이 없다는 것이 규약 <b>G2</b> 판정의 근거다.", 10)

# --- B'. 결정적 집계 ----------------------------------------------------------------
band("bC", 40, 708, 1260, 170, "B′.  결정적 집계        LLM 0회")
box("c1", 60, 748, 270, 52, "집계 한 줄 (A29a)", BLUE, "합 · 차 · 몫을 근거 머리에")
box("c2", 350, 748, 270, 52, "정정 시계열 (A29b)", BLUE, "체인 금액을 접수일 순으로")
box("c3", 640, 748, 250, 52, "계약상대 분포 (A29c)", RED, "기각 — Δ 0")
text("c4", 60, 810, 1220, 62,
     "🔴 <b>칸이 아니라 줄이다.</b> v4 는 같은 문항들을 <i>근거를 더 주는</i> 방향으로 "
     "세 번 겨냥해 세 번 다 기각했다. 공통 사유가 <i>“칸을 더 달라”</i> 였다. 여기서 "
     "실리는 것은 블록이 아니라 <b>한 줄</b>이라 그 사유에 안 걸린다.<br>"
     "🔴 <b>자리가 값보다 셌다.</b> Q-023 에 정정 금액 7건을 근거 <b>꼬리</b>에 실었을 때 "
     "답은 여전히 <i>“변동 없이 유지”</i> 였다. <b>같은 줄을 머리로 옮기자 뒤집혔다.</b>", 10)

# --- C. 생성 · 검증 · 처분 -----------------------------------------------------------
band("bD", 40, 890, 1260, 476, "C.  생성 · 검증 · 처분")
box("d1", 60, 930, 250, 50, "답변 생성 ①", ORANGE, "HCX-005 · 온도 0.0 · 난이도 상이면 CoT")
box("d2", 60, 992, 250, 50, "근거 표기가 있는가 (v6 교정)", em(YELLOW),
    "🔴 “접수번호” 낱말 요구를 뗐다")
box("d3", 60, 1054, 250, 44, "critic ②", ORANGE, "judge — 표기가 없을 때만")
box("d4", 60, 1110, 250, 70, "결정적 검증 4종", GREEN,
    "재계산 · 답변 내부 산술 · 수치 출처 · 완전성")
box("d5", 60, 1192, 250, 40, "발주처 면제 (A36)", GREEN, "발주처는 공시자가 아니다")
box("d6", 60, 1244, 250, 40, "출력 안전 계층 (v6 교정)", em(GREEN),
    "🔴 법인 라벨이면 번호를 남긴다")
box("d7", 60, 1296, 600, 38, "응답 — 5키 JSON", BLUE,
    "question_id · question · retrieved_context · think_trace · answer · 전부 string")

text("d8", 340, 922, 320, 18, "<b>처분 — 여덟 갈래</b>", 12)
box("d9", 340, 944, 320, 32, "① 각주 (v6 교정)", em(GREEN), "🔴 사유를 우리가 짓는다")
box("d10", 340, 982, 320, 30, "② 치환 (A20)", GREEN, "재계산값으로 바꾼다")
box("d11", 340, 1018, 320, 30, "③ 재생성 1회 (A20r)", ORANGE, "사유를 실어 다시 쓴다")
box("d12", 340, 1054, 320, 30, "④ 인용 부착 (A25)", GREEN, "§7.5 형식을 우리가 짓는다")
box("d13", 340, 1090, 320, 30, "⑤ 인용 떼기", GREEN, "거절이 단 표기를 지운다")
box("d14", 340, 1126, 320, 30, "⑥ 집계 치환 (A29s)", GREEN, "근거의 amount 로 합계를")
box("d15", 340, 1162, 320, 30, "⑦ 괄호 교정 (A35)", GREEN, "억·조 환산을 다시 쓴다")
box("d16", 340, 1198, 320, 30, "⑧ 자기정합 (A38)", GREEN, "식과 어긋나는 수를 맞춘다")

text("d17", 690, 944, 590, 400,
     "🔴 <b>v5 의 단일 발견 — 값을 줘도 모델은 자기 계산을 더 믿는다.</b> Q-034 에 정확한 "
     "817,900,000,000 을 싣고 <i>“다시 계산하지 말라”</i> 는 지시까지 붙였는데 모델은 "
     "817,100,000,000 을 썼다. <b>그래도 헛일이 아니었다</b> — 그 줄이 모델에게 <b>식을 "
     "쓰게 만들어</b> equation 검사가 발화했고, 그래서 치환이 닿았다.<br><br>"
     "🔴 <b>v6 는 그 원리를 우리 글에도 걸었다.</b> 각주 사유를 critic 의 산문에서 "
     "가져오고 있었는데, critic 이 불리언과 산문을 따로 내서 <b>골든 17건 중 8건이 "
     "자기를 반박했다</b>:<br>"
     "<i>(주의: 근거 검증을 통과하지 못했습니다 — 모든 수치가 근거에서 확인됩니다.)</i><br>"
     "<i>(주의: 근거 검증을 통과하지 못했습니다 — )</i><br>"
     "이제 사유를 <b>Verdict 의 불리언에서 짓는다</b>. 값도 사유도 우리가 짓는다.<br><br>"
     "🔴 <b>표기 검사가 규약이 아니라 우리 문안을 재고 있었다.</b> 규약 R2 는 "
     "<i>공시명·공시일</i> 을 요구하지 <i>접수번호</i> 라는 낱말을 요구하지 않는다. "
     "낱말 요구를 떼자 헛된 critic 호출이 <b>132회 → 106회</b>로 줄었다 "
     "(−26,481토큰 · −186원/판). 정답률은 안 움직였다.<br><br>"
     "🔴 <b>대가도 적는다.</b> 집계 치환은 <i>틀린 합계</i>를 <i>맞는 합계 + 안 맞는 "
     "풀이</i>로 바꾼다. Q-113·Q-115 가 그 자리이고, 110/112 를 사는 대신 "
     "「추론 논리성」 2건을 냈다 <b>(알고 남긴 선택)</b>.<br><br>"
     "<b>「추론 논리성」 검사 다섯</b> (수치 자기정합 · 식 불성립 · 근거 밖 수치 · "
     "순서 오류 · 자기부정) — 실패 양식 <b>일곱 중 다섯</b>. 🔴 전제 누락 · 비약은 "
     "<b>측정하지 않는다</b>. 골든 <b>109/112</b>.", 10)

# --- 잇는 선 -----------------------------------------------------------------------
for i, (src, dst) in enumerate([("a1", "a2"), ("a2", "a3"), ("a3", "a4"),
                                ("b1", "b2"), ("b2", "b3"), ("b3", "b4"),
                                ("d1", "d2"), ("d2", "d3"), ("d3", "d4"),
                                ("d4", "d5"), ("d5", "d6"), ("d6", "d7")]):
    arrow(f"e{i}", src, dst)

# --- 남은 흠 ------------------------------------------------------------------------
band("bE", 1320, 890, 400, 300, "남은 흠 — 셋 · 전부 일정과 무관하다")
box("z1", 1338, 930, 366, 62, "코퍼스 밖 출처 안내", RED,
    "거절 뒤 네이버·DART 를 안내한다. 프롬프트로 두 번 겨냥해 두 번 기각 — "
    "A27 은 1건을 3건으로 늘렸다. <b>방법을 못 찾았다</b>")
box("z2", 1338, 1002, 366, 62, "Q-107 · Q-117 검색 실패", RED,
    "질문의 <i>두코바니</i> 와 근거의 <i>Dukovany</i> 가 안 이어진다. 잇는 길이 별칭 "
    "사전뿐 — <b>안 하기로 정했다</b> (평가셋 과적합)")
box("z3", 1338, 1074, 366, 74, "산문 속 법인번호 과잉 마스킹", RED,
    "법인번호와 주민번호는 형식이 같고 체크섬으로도 안 갈린다(34종 중 6종이 통과). "
    "예외를 넓히면 가짜 주민번호가 샌다 — <b>안 하기로 정했다</b>")
text("z4", 1338, 1156, 366, 30,
     "<font style='font-size:10px'>🔴 <b>미탐은 오탐보다 나쁘다</b> — 안전 장치는 "
     "가리는 쪽으로 기울여 둔다.</font>")

# --- 바닥 -------------------------------------------------------------------------
text("f1", 40, 1380, 1260, 96,
     "<b>v6 결산</b> — v5 가 오답 14건 중 12건을 잡아 98 → <b>110/112</b> 로 올렸고, "
     "v6 는 <b>성적을 안 건드리는 자리 넷</b>을 고쳤다: 각주 자기반박 · 표기 검사의 낱말 "
     "요구 · 법인번호 과잉 거절 · 법인번호 검색 경로. 넷 다 재측정에서 <b>110/112 그대로</b>."
     "<br>🔴 <b>고친 값은 정확도가 아니라 정직함과 비용이다.</b> 각주가 자기와 싸우지 "
     "않고, 표기 검사가 우리 문안이 아니라 규약을 재고, 헛된 critic 호출 26회가 사라졌다."
     "<br>🔴 <b>처음 보는 문항 49/54 (90.7%)</b> 가 기대값이다 — 골든 98.2% 는 "
     "<code>RequestCache</code> 재생이라 낙관적이다. heldout_v3 (15/16)는 위 넷이 반영된 "
     "뒤 캐시 없이 잰 유일한 판이다.", 10)

xml = ("<mxfile host=\"app.diagrams.net\" type=\"device\">\n"
       "  <diagram name=\"ARGUS 파이프라인 v6\" id=\"argus-algo-v6\">\n"
       "    <mxGraphModel dx=\"1679\" dy=\"1129\" grid=\"0\" gridSize=\"10\" guides=\"1\" "
       "tooltips=\"1\" connect=\"1\" arrows=\"1\" fold=\"1\" page=\"1\" pageScale=\"1\" "
       "pageWidth=\"1760\" pageHeight=\"1600\" background=\"#ffffff\" math=\"0\" "
       "shadow=\"0\">\n"
       "      <root>\n        <mxCell id=\"0\" />\n"
       "        <mxCell id=\"1\" parent=\"0\" />\n"
       + "\n".join("        " + c for c in cells)
       + "\n      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n")

OUT.parent.mkdir(parents=True, exist_ok=True)
io.open(OUT, "w", encoding="utf-8").write(xml)
print("저장 %s" % OUT)
print("셀 %d개 · %d자 · 페이지 1760x1600 · background #ffffff" % (len(cells), len(xml)))

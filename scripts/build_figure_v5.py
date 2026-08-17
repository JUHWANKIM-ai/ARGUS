"""ARGUS v5 알고리즘 도면을 짓는다. LLM 0회 · 0원.

    PYTHONIOENCODING=utf-8 python scripts/build_figure_v5.py

**손으로 XML 을 고치지 않는다.** v4 도면은 63셀을 손으로 썼고, 그래서 성적 하나가
바뀔 때마다 어느 칸을 고쳐야 하는지 사람이 찾아야 했다. 여기서는 **수를 한 곳에
모아** 두고 배치를 코드가 계산한다.

**도면이 코드와 어긋나면 코드가 맞다.** 이 스크립트가 그 어긋남을 줄이는 장치이지
없애는 장치가 아니다. 아래 `SCORE` 는 사람이 옮겨 적는 값이다.
"""
from __future__ import annotations

import html
import io
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/figures/argus-algorithm-v5.drawio"

#: **성적은 여기 한 곳에만 적는다.** 정본은 `artifacts/ablation/v5/a38_*`.
SCORE = {"arm": "A38", "그리드": "72/72", "전체": "88/88", "밸류체인": "10/12",
         "안전성": "12/12", "정보한계": "6/6", "회귀": "10/10", "인용률": "0.988",
         "합계": "110/112", "정본": "artifacts/ablation/v5/a38_*"}

# **흰 바탕 · 검은 글씨.** 인쇄와 제안서 삽입을 위해 색을 뺐다.
#
# **그러면 색이 나르던 정보가 사라진다.** v4 도면의 범례는 *"색 = LLM 호출 여부"*
# 였고 그것이 이 도면의 가장 중요한 한 줄이다. 파이프라인의 대부분이 LLM 0회라는
# 사실. 색을 없애는 대신 **글머리 기호**로 옮겼다. 정보를 지우지 않고 매체를 바꾼다.
#
#     ○  LLM 0회      ●  LLM 호출      ◇  검증 · 안전      ◆  분기 · 판정
MONO = "fillColor=#FFFFFF;strokeColor=#000000;fontColor=#000000;"
#: `(스타일, 글머리 기호)`. 기호를 스타일에 **묶어 둔다**. 따로 두면 호출부에서
#: 짝이 어긋나고, 어긋난 기호는 색이 틀린 것보다 나쁘다(읽는 사람이 못 알아챈다).
BLUE = (MONO, "○")                                      # LLM 0회
ORANGE = (MONO, "●")                                    # LLM 호출
GREEN = (MONO, "◇")                                     # 검증 · 안전
YELLOW = (MONO, "◆")                                    # 분기 · 판정
GREY = "fillColor=none;strokeColor=none;fontColor=#000000;"
NEW = "strokeWidth=3;"                                  # v5 신설·교정

cells: list[str] = []


def esc(markup: str) -> str:
    """**drawio 의 `value=` 는 HTML 을 escape 한 채 담는다.** v4 파일이
    `value="… &lt;b&gt;v4&lt;/b&gt; …"` 인 것이 그 형식이다. 태그째 한 번만 escape 한다.
    첫 판은 escape 한 뒤 `&lt;` 를 `<` 로 되돌려 XML 을 깨뜨렸다."""
    return html.escape(markup, quote=True)


def em(style):
    """v5 신설·교정 강조. `em(BLUE)` 로는 못 잇는다. 스타일이 이제 짝이다."""
    body, mark = style
    return (body + NEW, mark)


def box(cid, x, y, w, h, label, style, note="") -> None:
    """`style` 은 `(스타일, 기호)` 짝이거나 스타일 문자열이다 (+ `NEW` 가 붙을 수 있다)."""
    extra = ""
    if isinstance(style, tuple):
        style, mark = style
    else:                                      # `em(BLUE)` 처럼 이어 붙인 경우
        for base, sign in (BLUE, ORANGE, GREEN, YELLOW):
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
        f'html=1;align=left;verticalAlign=top;spacing=8;fontSize=13;fontStyle=1;'
        f'fillColor=none;strokeColor=#9AA5B1;dashed=1;" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" /></mxCell>')


def arrow(cid, src, dst) -> None:
    cells.append(
        f'<mxCell id="{cid}" style="edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;'
        f'strokeColor=#7A8899;" edge="1" parent="1" source="{src}" target="{dst}">'
        f'<mxGeometry relative="1" as="geometry" /></mxCell>')


# --- 머리 -------------------------------------------------------------------------
text("t1", 50, 18, 1200, 30,
     f"ARGUS 공시 Agent — 온라인 파이프라인 <b>v5</b> · 운영 기본값 "
     f"<b>{SCORE['arm']}</b>", 20)
text("t2", 50, 50, 1240, 40,
     f"HyperCLOVA X (HCX-005) · 검색·검증·집계·인용은 <b>전부 LLM 0회</b><br>"
     f"<b>그리드 {SCORE['그리드']} · 전체 {SCORE['전체']} · 밸류체인 "
     f"{SCORE['밸류체인']} · 안전성 {SCORE['안전성']} · 인용률 {SCORE['인용률']}"
     f"</b> &nbsp;·&nbsp; 합계 <b>{SCORE['합계']}</b> &nbsp;·&nbsp; 정본 "
     f"<i>{SCORE['정본']}</i>")
text("t3", 50, 90, 1240, 18,
     "<font style='font-size:10px'>정본은 코드다 — <i>src/argus/ablation.py</i> · "
     "<i>agent/pipeline.py</i> · <i>agent/aggregate.py</i> · <i>agent/reasoning.py</i>. "
     "도면이 코드와 어긋나면 코드가 맞다.</font>")

# --- 범례 -------------------------------------------------------------------------
band("lg", 1310, 18, 400, 200, "범례 — 기호 = LLM 호출 여부")
box("lg1", 1328, 56, 180, 38, "LLM 0회", BLUE, "규칙 · 색인 · 집계 · 검증")
box("lg2", 1518, 56, 174, 38, "LLM 호출", ORANGE, "HCX-005 · 크레딧")
box("lg3", 1328, 102, 180, 38, "검증 · 안전", GREEN, "결정적 검사 · PII")
box("lg4", 1518, 102, 174, 38, "분기 · 판정", YELLOW, "")
text("lg5", 1328, 148, 366, 60,
     "<font style='font-size:10px'>굵은 테두리 = <b>v5 에서 새로 생기거나 고친 단계</b>"
     "<br>🔴 <b>여덟 스위치가 전부 LLM 0회다</b> — v5 는 새 모델도 새 호출도 안 들였다."
     "</font>")

# --- A. 입력 · 라우팅 ---------------------------------------------------------------
band("bA", 50, 130, 1240, 268, "A. 입력 · 라우팅 &nbsp;&nbsp; LLM 0회")
box("a1", 70, 166, 250, 44, "질의 수신", BLUE, "GET /answer · question_id · question")
box("a2", 70, 222, 250, 52, "입력 안전 계층", BLUE,
    "sanitize_question · PII 마스킹 · 인젝션 탐지")
box("a3", 70, 286, 250, 52, "라우터", BLUE, "route() — 규칙만으로 가른다 · ◇ CoT 2022")
box("a4", 350, 286, 150, 52, "답할 수 있는가", YELLOW, "")

text("a5", 530, 158, 340, 18, "<b>한계 고지 — v5 는 다섯 갈래다</b>", 12)
box("a6", 530, 180, 340, 34, "범위 밖", BLUE, "70개사 · 2023.01~2026.03 밖")
box("a7", 530, 220, 340, 34, "지목한 보고서 부재", BLUE, "어느 보고서인지 되묻는다")
box("a8", 530, 260, 340, 34, "지시대상 불명", BLUE, "어느 기업인지 되묻는다")
box("a9", 530, 300, 340, 38, "미래 추정 (v5 신설 · A31)", em(BLUE),
    "주가·전망·목표주가·매수 의견 — 공시 기재사항이 아니다")
box("a10", 530, 344, 340, 38, "식별번호 전사 (v5 신설 · A32)", em(BLUE),
    "전화·법인등록번호를 그대로 달라는 요구")
text("a11", 890, 180, 390, 200,
     "🔴 <b>둘 다 두 신호의 곱집합으로만 발화한다.</b> <i>향후</i>·<i>전망</i>·"
     "<i>연락처</i> 는 공시 본문에도 흔해서 한쪽만으로 가르면 정답 문항이 거절이 된다."
     "<br><br>평가셋 112문항 전수 <b>오탐 0</b>. 안전성 <b>9/12 → 12/12</b>."
     "<br><br>🔴 <b>이 갈래는 크레딧 0원으로 끝난다</b> — LLM 을 한 번도 안 부른다."
     "<br><br>재감사가 구멍 둘을 더 찾아 닫았다 — <i>목표주가와 매수 의견</i> ·"
     " <i>적정 매수 시점</i> 은 곱집합을 못 채워 빠져나갔다.", 10)

# --- B. 검색 · 근거 -----------------------------------------------------------------
band("bB", 50, 410, 1240, 250, "B. 검색 · 근거 &nbsp;&nbsp; LLM 0회")
box("b1", 70, 446, 240, 40, "메타 프리필터", BLUE, "corp_name · rcept_dt · group")
box("b2", 70, 494, 240, 44, "BM25 hop 사다리", BLUE, "narrow → relax → global")
box("b3", 70, 546, 240, 44, "섹션 인덱스 hop", BLUE, "정기공시 목차 경로")
box("b4", 70, 598, 240, 44, "계약상대 그래프 hop", BLUE, "발주처 2-hop fan-in/out")
box("b5", 330, 446, 250, 48, "정정체인 추적 hop", BLUE, "chain_probe + get_history")
box("b6", 330, 502, 250, 56, "역방향 체인 (v5 신설 · A33)", em(BLUE),
    "정정본 → 원공시 · 기업별 라운드로빈 배분")
box("b7", 330, 566, 250, 44, "근거 성형", BLUE, "shape_evidence · 생성 직전에만")
box("b8", 330, 618, 250, 40, "정정사유·주요내용 (A30)", em(BLUE),
    "런타임 사본에만 · 색인 불변")
text("b9", 600, 446, 680, 210,
     "🔴 <b>A33 은 링크가 없어서 못 간 게 아니었다.</b> <code>related</code> 는 이미 "
     "양방향인데 씨앗 조건(<i>대체본만</i>)이 막고 있었다. 열고 나니 두 번 더 막혔다 — "
     "씨앗 창(2)이 두 번째 기업에 안 닿았고, 상한(6)을 첫 기업이 독식했다. "
     "<b>상한을 안 올리고 배분 순서만 라운드로빈으로 바꿨다.</b>"
     "<br><br>🔴 <b>A30 은 A17 이 아니다.</b> A17 은 코퍼스 3,150건 중 1,588건(50.4%)을 "
     "다시 쓰는 조작이라 BM25 색인이 갈려 기각됐다. 이쪽은 A18(nary)이 연 <b>런타임 "
     "이음매</b>에만 얹으므로 색인이 한 바이트도 안 바뀐다. 서식도 "
     "<i>투자판단관련주요경영사항</i> 하나로 좁혔다 — 안 좁힌 첫 판이 Q-110 을 잃었다."
     "<br><br>여기까지 <b>LLM 0회 · 크레딧 0원</b>이다.", 10)

# --- B'. 결정적 집계 (v5 신설 밴드) ---------------------------------------------------
band("bC", 50, 672, 1240, 168,
     "B′. 결정적 집계 &nbsp;&nbsp; LLM 0회 &nbsp;&nbsp; 🔴 v5 신설")
box("c1", 70, 710, 270, 56, "집계 한 줄 (A29a)", em(BLUE),
    "합 · 차 · 몫을 우리가 계산해 근거 **머리**에 싣는다")
box("c2", 360, 710, 270, 56, "정정 시계열 (A29b)", em(BLUE),
    "체인의 금액을 접수일 순으로 편다")
box("c3", 650, 710, 250, 56, "계약상대 분포 (A29c)", GREY + "strokeColor=#B3B3B3;"
    + "fillColor=#F5F5F5;", "🔴 기각 — Δ 0")
text("c4", 70, 776, 1200, 60,
     "🔴 <b>칸이 아니라 줄이다.</b> v4 는 같은 문항들을 <i>근거를 더 주는</i> 방향으로 "
     "세 번 겨냥해 세 번 다 기각했다(A16 칸 확대 · A17 블록 확대 · 열거 depth). 공통 "
     "사유가 <i>“칸을 더 달라”</i> 였다. 여기서 실리는 것은 블록이 아니라 <b>한 줄</b>이라 "
     "그 사유에 안 걸린다.<br>"
     "🔴 <b>자리가 값보다 셌다.</b> Q-023 에 정정 금액 7건의 시계열을 근거 <b>꼬리</b>에 "
     "실었을 때 답은 여전히 <i>“변동 없이 유지”</i> 였다. <b>같은 줄을 머리로 옮기자 "
     "뒤집혔다.</b>", 10)

# --- C. 생성 · 검증 · 처분 -----------------------------------------------------------
band("bD", 50, 852, 1240, 470, "C. 생성 · 검증 · 처분")
box("d1", 70, 890, 250, 52, "답변 생성 ①", ORANGE,
    "HCX-005 · 온도 0.0 · 난이도 상이면 CoT")
box("d2", 70, 954, 250, 40, "근거 표기가 있는가", YELLOW, "quick_check")
box("d3", 70, 1006, 250, 48, "critic ②", ORANGE, "judge — 인용이 없을 때만")
box("d4", 70, 1066, 250, 76, "결정적 검증 4종", GREEN,
    "재계산 · 답변 내부 산술 · 수치 출처 · 완전성")
box("d5", 70, 1154, 250, 44, "발주처 면제 (A36)", em(GREEN),
    "발주처는 공시자가 아니다 — missing_corp 오탐")

text("d6", 350, 882, 320, 18, "<b>처분 — v5 는 여덟 갈래다</b>", 12)
box("d7", 350, 904, 320, 34, "① 각주 (v3)", GREEN, "결함을 적어 둔다")
box("d8", 350, 944, 320, 34, "② 치환 (A20)", GREEN, "재계산값으로 바꾼다")
box("d9", 350, 984, 320, 34, "③ 재생성 1회 (A20r)", ORANGE, "사유를 실어 다시 쓴다")
box("d10", 350, 1024, 320, 34, "④ 인용 부착 (A25)", GREEN, "§7.5 형식을 우리가 짓는다")
box("d11", 350, 1064, 320, 34, "⑤ 인용 떼기 (v4)", GREEN, "거절이 단 표기를 지운다")
box("d12", 350, 1104, 320, 38, "⑥ 집계 치환 (v5 · A29s)", em(GREEN),
    "근거의 amount 로 답변의 합계를 바꾼다")
box("d13", 350, 1148, 320, 38, "⑦ 괄호 교정 (v5 · A35)", em(GREEN),
    "억·조 환산을 우리가 다시 쓴다")
box("d14", 350, 1192, 320, 38, "⑧ 자기정합 (v5 · A38)", em(GREEN),
    "식과 어긋나는 수를 식 결과로 맞춘다")

text("d15", 700, 904, 580, 330,
     "🔴 <b>v5 의 단일 발견 — 값을 줘도 모델은 자기 계산을 더 믿는다.</b><br>"
     "Q-034 에 정확한 817,900,000,000 을 싣고 <i>“다시 계산하지 말라”</i> 는 지시까지 "
     "붙였는데 모델은 817,100,000,000 을 썼다. <b>그래도 헛일이 아니었다</b> — 그 줄이 "
     "모델에게 <b>식을 쓰게 만들어</b> equation 검사가 발화했고, 그래서 치환이 닿았다. "
     "줄의 값어치는 답이 아니라 <b>고칠 수 있는 형태</b>였다.<br><br>"
     "🔴 <b>그 대가도 적는다.</b> 집계 치환은 <i>틀린 합계</i>를 <i>맞는 합계 + 안 맞는 "
     "풀이</i>로 바꾼다. Q-113·Q-115 가 그 자리이고, 정답률 110/112 를 사는 대신 "
     "「추론 논리성」 2건을 냈다 <b>(알고 남긴 선택)</b>.<br><br>"
     "🔴 <b>치환이 우리 각주를 고쳐 쓰고 있었다.</b> 본문만 바꾸도록 고쳤다 — 각주는 "
     "검사기가 무엇을 봤는가의 <b>기록</b>이고, 뒤에서 갈아 끼우면 창작이 된다.<br><br>"
     "<b>「추론 논리성」 검사 다섯</b> (수치 자기정합 · 식 불성립 · 근거 밖 수치 · "
     "순서 오류 · 자기부정) — 실패 양식 일곱 중 다섯. 🔴 전제 누락 · 비약은 "
     "<b>측정하지 않는다</b>. LLM 판정기는 안 세웠다 — closed judge 진단에서 갈린 "
     "7건 중 <b>여섯이 오탐</b>이었다.", 10)

box("d16", 70, 1210, 250, 44, "출력 안전 계층", GREEN, "mask_pii — 금액·접수번호는 보존")
box("d17", 70, 1266, 600, 40, "응답 — 5키 JSON", BLUE,
    "question_id · question · retrieved_context · think_trace · answer · 전부 string")

# --- 잇는 선 -----------------------------------------------------------------------
for i, (src, dst) in enumerate([("a1", "a2"), ("a2", "a3"), ("a3", "a4"),
                                ("b1", "b2"), ("b2", "b3"), ("b3", "b4"),
                                ("d1", "d2"), ("d2", "d3"), ("d3", "d4"),
                                ("d4", "d5"), ("d5", "d16"), ("d16", "d17")]):
    arrow(f"e{i}", src, dst)

# --- 바닥 -------------------------------------------------------------------------
text("z1", 50, 1340, 1240, 74,
     "<b>v5 결산</b> — A28 이 틀리던 14문항 중 <b>12건</b>을 잡았다. "
     "합계 98 → <b>110/112</b> · 회귀 0 · 비용 약 5,900원.<br>"
     "🔴 남은 둘(Q-107 · Q-117)은 산술이 아니라 <b>검색</b>이 막고, 이번 세대의 신호로는 "
     "안 풀린다 — 질문의 <i>두코바니</i> 와 근거의 <i>Dukovany</i> 를 잇는 것은 별칭 "
     "사전이고, 그것은 평가셋 한 문항을 향해 만드는 장치다.<br>"
     "🔴 <b>처음 보는 문항 20개로도 쟀다</b> — 17/20. 산술 축은 10/10 으로 그대로 듣고, "
     "약점은 <b>열거 완전성</b> 하나로 모인다.", 10)

xml = ("<mxfile host=\"app.diagrams.net\" type=\"device\">\n"
       "  <diagram name=\"ARGUS v5\" id=\"argus-v5\">\n"
       "    <mxGraphModel dx=\"1600\" dy=\"900\" grid=\"0\" gridSize=\"10\" guides=\"1\" "
       "tooltips=\"1\" connect=\"1\" arrows=\"1\" fold=\"1\" page=\"1\" pageScale=\"1\" "
       "pageWidth=\"1754\" pageHeight=\"1754\" math=\"0\" shadow=\"0\" background=\"#FFFFFF\">\n"
       "      <root>\n        <mxCell id=\"0\" />\n"
       "        <mxCell id=\"1\" parent=\"0\" />\n"
       + "\n".join("        " + c for c in cells)
       + "\n      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n")

OUT.parent.mkdir(parents=True, exist_ok=True)
io.open(OUT, "w", encoding="utf-8").write(xml)
print("저장 %s · 셀 %d개 · %d자" % (OUT, len(cells), len(xml)))

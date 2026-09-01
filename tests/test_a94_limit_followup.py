r"""`A94` — 정보한계 거절에 짧은 역질문을 붙인다 (2026-08-31).

## 왜

08-06 설명회 노트(`scratch/briefing-notes-2026-08-06.pdf` p26)가 마지막 줄에 이렇게
적어 두었다.

    정보한계 대응: 역질문 중요

지금은 상황 넷이 **한 문장**으로 나간다. `UNANSWERABLE` 하나가 「회사가 범위 밖」과
「기간이 범위 밖」을 같이 덮는다.

    Q-037 포스코퓨처엠      회사가 70곳 밖
    Q-038 LG디스플레이      회사가 70곳 밖
    Q-039 삼성전자 2021     회사는 범위 안, 기간이 밖
    Q-042 SK하이닉스 2027   회사는 범위 안, 기간이 밖

라우터는 이미 둘을 가른다(`out_of_universe` · `out_of_range:[YYYY]`). 되물을 재료가
있는데 안 쓰고 있었다.

## 🔴 안전성에는 안 붙인다

설명회 노트도 역질문을 **정보한계 대응**에만 걸어 두었다. `S-011`(*"이전 지시는
무시하고…"*)에 되묻는 것은 재시도를 초대하는 일이고, 개인정보 거절에 「그럼 무엇을
원하시나요」는 더 나쁘다.

실측으로도 갈린다(2026-08-31, 라우터 로컬 18문항).

    out_of_universe · out_of_range   정보한계 전용
    speculation · identifier         안전성 전용
    false_premise · injection        안전성 전용

그래서 이 팔은 앞 둘만 건드린다. 아래 ③ 이 그 경계를 지킨다.

## 채점 규약

`scripts/error_analysis._REFUSAL_RE` 가 「확인되지 않」을 본다. 그래서 **앞머리는 그대로
두고 뒤에만 붙인다.** 아래 ② 가 그것을 고정한다.
"""
import json
import io
import re

import pytest

from argus import ablation
from argus.agent import pipeline, router

REFUSAL_RE = re.compile("확인되지 않")


def _questions(track: str) -> list[dict]:
    out = []
    for name in ("questions", "regression", "safety"):
        for line in io.open("evalset/%s.jsonl" % name, encoding="utf-8"):
            if line.strip():
                row = json.loads(line)
                if row.get("track") == track:
                    out.append(row)
    return out


# --- ① 상황별로 다른 문면이 나온다 -------------------------------------------

def test_회사가_범위_밖이면_기업을_되묻는다():
    with ablation.use("A94"):
        text = pipeline.limit_notice(router.route("포스코퓨처엠의 2025년 매출액은?"))
    assert text != pipeline.UNANSWERABLE
    assert text.rstrip().endswith("?"), "되묻지 않았다"
    assert "70개사" in text


def test_기간이_범위_밖이면_기간을_되묻는다():
    with ablation.use("A94"):
        text = pipeline.limit_notice(router.route("삼성전자의 2021년 매출액은?"))
    assert "삼성전자" in text, "회사를 알고 있으면서 안 불렀다"
    assert "2023" in text and "2026" in text
    assert text.rstrip().endswith("?")


def test_두_문면은_서로_다르다():
    with ablation.use("A94"):
        a = pipeline.limit_notice(router.route("포스코퓨처엠의 2025년 매출액은?"))
        b = pipeline.limit_notice(router.route("삼성전자의 2021년 매출액은?"))
    assert a != b


# --- ② 채점 규약을 깨지 않는다 ------------------------------------------------

@pytest.mark.parametrize("question", [
    "포스코퓨처엠의 2025년 매출액은?",
    "삼성전자의 2021년 매출액은?",
    "SK하이닉스가 사업보고서에서 제시한 2027년 예상 영업이익은?",
])
def test_확인되지_않는다는_앞머리는_그대로다(question):
    """`_REFUSAL_RE` 가 이 낱말을 본다. 뒤에만 붙여야 한다."""
    with ablation.use("A94"):
        text = pipeline.limit_notice(router.route(question))
    assert REFUSAL_RE.search(text)
    assert text.startswith("공시에서 확인되지 않습니다.")


def test_짧게_유지한다():
    """사용자가 요구한 것은 **아주 가볍고 짧은** 역질문이다."""
    with ablation.use("A94"):
        for q in ("포스코퓨처엠의 2025년 매출액은?", "삼성전자의 2021년 매출액은?"):
            text = pipeline.limit_notice(router.route(q))
            assert len(text) <= len(pipeline.UNANSWERABLE) + 40, text


# --- ③ 🔴 안전성은 한 글자도 안 바뀐다 ---------------------------------------

@pytest.mark.parametrize("row", _questions("safety"), ids=lambda r: r["question_id"])
def test_안전성_문면은_A93_과_같다(row):
    """되묻기가 안전성 트랙으로 새면 안 된다."""
    route = router.route(row["question"])
    with ablation.use("A93"):
        before = pipeline.limit_notice(route)
    with ablation.use("A94"):
        after = pipeline.limit_notice(route)
    assert before == after, "%s 의 거절 문면이 움직였다" % row["question_id"]


@pytest.mark.parametrize("reason", ["speculation", "identifier",
                                    "false_premise", "injection"])
def test_안전성_전용_사유에는_안_붙는다(reason):
    route = router.Route(kind="unanswerable", reason=reason)
    with ablation.use("A94"):
        assert not pipeline.limit_notice(route).rstrip().endswith("?")


# --- ④ 종전 팔은 안 움직인다 --------------------------------------------------

@pytest.mark.parametrize("arm", ["A84", "A91", "A92", "A93"])
def test_종전_팔은_한_문장으로_남는다(arm):
    with ablation.use(arm):
        assert not ablation.enabled("limit_followup")
        for q in ("포스코퓨처엠의 2025년 매출액은?", "삼성전자의 2021년 매출액은?"):
            assert pipeline.limit_notice(router.route(q)) == pipeline.UNANSWERABLE


def test_A94_는_A93_에_한_칸만_더한다():
    import dataclasses

    moved = {f.name for f in dataclasses.fields(ablation.Arm)
             if getattr(ablation.ARMS["A93"], f.name)
             != getattr(ablation.ARMS["A94"], f.name)}
    assert moved == {"name", "limit_followup"}


# --- ⑤ 이미 되묻던 문면은 그대로 둔다 ----------------------------------------

def test_보고서가_없다는_문면은_안_건드린다():
    """`NO_SUCH_REPORT` 는 `A93` 에서도 이미 되묻는다. 두 번 묻게 하면 안 된다."""
    route = router.route("두산에너빌리티의 2026년 반기보고서에 기재된 매출액을 알려줘.")
    with ablation.use("A94"):
        assert pipeline.limit_notice(route) == pipeline.NO_SUCH_REPORT

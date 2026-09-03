r"""`A95` — 재생성 머리말이 배달 문면에 남지 않게 한다 (2026-09-02).

## 무엇이 새는가

검증이 실패하면 답변을 한 번 다시 쓴다. 그 프롬프트(`prompts.REGENERATE_INSTRUCTION`)가
*"앞선 답변에 대해 자동 검증이 아래 결함을 찾았습니다"* 로 열고, 모델이 그 틀을 답변
**첫 문장에 그대로 옮겨 적는다.**

    앞서 제공한 답변 중 일부 오류가 발견되었습니다. 이에 따라 아래와 같이 …
    앞서 제시된 문제에서 지적된 부분을 수정하여 답변을 다시 드리겠습니다.

내부 사정이 심사자에게 배달된다. 정답 판정에는 안 걸린다 — `quiet_verify_note` 가 닫은
검증 각주와 같은 갈래고, 그 처방과 같은 자리에 붙인다.

## 🔴 첫 문장을 무턱대고 지우면 안 된다

수집된 답변 5,950건에서 `앞서`/`앞선` 으로 시작하는 답변이 **108건**인데, 그중 **63건은
정상 답변**이고 그 문장이 답을 담고 있다.

    앞서 제공된 공시 근거에 따르면, … 계약금액 합계는 **616,800,000,000원**입니다.

그래서 가르는 잣대가 셋이다.

    ① `앞서`/`앞선` 으로 연다
    ② **다시 하겠다는 선언**으로 닫는다 — 정상 답변은 `…에 따르면, …입니다` 로 닫는다
    ③ 그 문장에 **숫자가 없다** — 금액을 담은 문장은 절대 안 지운다

실측: 지움 45건(관찰된 11가지 문면 전부) · 남김 63건 · 놓친 머리말 0건.
"""
import pytest

from argus import ablation
from argus.agent import aggregate

#: 실제로 관찰된 열한 가지. 하나라도 빠뜨리면 그 문면이 다시 샌다.
OBSERVED = [
    "앞서 제시된 문제에서 지적된 부분을 수정하여 답변을 다시 드리겠습니다.",
    "앞서 제공된 공시 근거를 토대로 답변을 보완하겠습니다.",
    "앞서 제공한 답변 중 일부 오류가 발견되었습니다.",
    "앞서 제시된 문제에서 지적된 부분을 수정하여 답변을 다시 작성하겠습니다.",
    "앞서 제공한 답변 중 일부 오류가 발견되었음을 인지했습니다.",
    "앞서 제시된 지적 사항을 바탕으로 다시 검토하겠습니다.",
    "앞서 제시된 문제에서 지적된 부분을 수정하여 답변을 제공하겠습니다.",
    "앞서 제공된 공시 근거를 토대로 다시 답변을 드리겠습니다.",
    "앞서 제시된 문제에서 제가 잘못 인용한 부분을 바로잡고, 주어진 근거에 따라 다시 계산하겠습니다.",
    "앞서 제시된 답변 중 금액 오류 부분을 수정하여 다시 작성하겠습니다.",
    "앞서 제시된 문제에서 지적된 사항들을 고려하여 다시 계산하겠습니다.",
]

#: 첫 문장이 같은 낱말로 열지만 **답을 담은** 답변들. 하나라도 지우면 답이 사라진다.
KEEP = [
    "앞서 제공된 공시 근거에 따르면, HD현대중공업이 2023년 9월 7일에 공시한 단일판매·공급계약"
    " 두 건의 계약금액 합계는 **616,800,000,000원**입니다.",
    "앞서 제공된 공시 근거에 따르면, 대우건설이 2023년 9월 15일에 공시한"
    " 단일판매·공급계약 두 건은 다음과 같습니다:",
    "앞서 제공된 정보에 따르면, 삼성전자의 2023년 사업보고서(제55기) 기준 DX 부문의"
    " 매출액은 1,699,923억 원이고, DS 부문의 매출액은 665,945억 원입니다.",
    "앞서 제시된 공시 근거에 따르면, 삼성전자가 발주한 평택 P4 Ph4(상동) 마감공사에 대한"
    " 계약금액 변화는 다음과 같이 계산할 수 있습니다.",
    "앞서 제공된 공시 근거에 따르면, 한국수력원자력이 발주한 신한울 원자력발전소 3·4호기"
    " 주설비공사의 두 공시 계약금액 합계는 이미 계산되어 있습니다.",
]

BODY = "\n\n1. 계약 상대방: Aramco Asia Japan\n2. 계약 금액: 160,038,036,031원"


# --- ① 관찰된 머리말은 전부 지운다 -------------------------------------------

@pytest.mark.parametrize("preface", OBSERVED, ids=range(len(OBSERVED)))
def test_관찰된_머리말을_지운다(preface):
    with ablation.use("A95"):
        fixed, removed = aggregate.strip_regen_preface(preface + BODY)
    assert removed, "안 지웠다"
    assert not fixed.startswith(("앞서", "앞선"))
    assert "Aramco Asia Japan" in fixed, "본문까지 지웠다"


def test_지운_뒤_앞머리_공백이_안_남는다():
    with ablation.use("A95"):
        fixed, _ = aggregate.strip_regen_preface(OBSERVED[0] + BODY)
    assert fixed == fixed.lstrip()


# --- ② 답을 담은 첫 문장은 절대 안 지운다 ------------------------------------

@pytest.mark.parametrize("first", KEEP, ids=range(len(KEEP)))
def test_답을_담은_첫_문장은_남긴다(first):
    text = first + BODY
    with ablation.use("A95"):
        assert aggregate.strip_regen_preface(text) == (text, "")


def test_숫자가_든_문장은_안_지운다():
    """③ 금액을 담은 문장은 어떤 꼴이든 안 건드린다."""
    text = "앞서 제시된 616,800,000,000원을 바로잡아 다시 계산하겠습니다." + BODY
    with ablation.use("A95"):
        assert aggregate.strip_regen_preface(text) == (text, "")


def test_머리말만_있고_본문이_없으면_안_지운다():
    """지우면 빈 답변이 된다. 그럴 바엔 머리말이 낫다."""
    text = OBSERVED[0]
    with ablation.use("A95"):
        assert aggregate.strip_regen_preface(text) == (text, "")


def test_문장_중간의_같은_말은_안_건드린다():
    text = "계약금액은 970억원입니다. 앞서 제공한 답변 중 일부 오류가 발견되었습니다."
    with ablation.use("A95"):
        assert aggregate.strip_regen_preface(text) == (text, "")


# --- ②b 머리말이 두 문장인 판 -------------------------------------------------

#: 앞 문장을 뗀 뒤 **이어서** 남던 선언들. 첫 판이 이것을 남겨 *"이에 따라 수정하여"*
#: 라는 앞말 없는 문장이 답변 첫 줄에 섰다(2026-09-02 서버 `H-005`).
CONTINUED = [
    "이에 따라 아래와 같이 수정하여 정확한 정보를 제공해 드리겠습니다.",
    "이에 따라 주어진 [공시 근거]를 기반으로 재확인하여 정확한 정보를 제공하겠습니다.",
    "이에 따라 주어진 공시 근거를 기반으로 정확한 정보를 다시 정리하여 드리겠습니다.",
    "이에 따라 아래와 같이 수정하여 답변을 드리겠습니다.",
]


@pytest.mark.parametrize("cont", CONTINUED, ids=range(len(CONTINUED)))
def test_두_문장짜리_머리말을_통째로_뗀다(cont):
    with ablation.use("A95"):
        fixed, removed = aggregate.strip_regen_preface(OBSERVED[2] + " " + cont + BODY)
    assert not fixed.startswith(("앞서", "앞선", "이에")), fixed[:40]
    assert "Aramco Asia Japan" in fixed, "본문까지 지웠다"
    assert cont in removed, "뒤 문장을 뗐다고 안 적었다"


def test_앞_문장을_안_뗐으면_뒤_문장도_안_본다():
    """🔴 가장 좁은 자리. 이어짐을 확인하는 규칙이라 홀로 선 문장은 안 건드린다.

    실측(수집 답변 5,950건): 앞 문장 없이 이 꼴로 여는 **원본** 답변은 0건이다.
    """
    text = CONTINUED[0] + BODY
    with ablation.use("A95"):
        assert aggregate.strip_regen_preface(text) == (text, "")


def test_뒤_문장에_숫자가_있으면_안_뗀다():
    text = OBSERVED[2] + " 이에 따라 160,038,036,031원으로 수정하여 드리겠습니다." + BODY
    with ablation.use("A95"):
        fixed, removed = aggregate.strip_regen_preface(text)
    assert removed == OBSERVED[2], "앞 문장만 떼야 한다"
    assert "160,038,036,031원으로 수정" in fixed


def test_뒤_문장만_남고_본문이_없으면_뒤_문장은_남긴다():
    text = OBSERVED[2] + " " + CONTINUED[0]
    with ablation.use("A95"):
        fixed, removed = aggregate.strip_regen_preface(text)
    assert removed == OBSERVED[2]
    assert fixed == CONTINUED[0]


# --- ③ 꼬리는 건드리지 않는다 -------------------------------------------------

def test_시스템이_붙인_꼬리는_그대로_둔다():
    tail = "\n\n(근거: 공급계약 체결 (2024-04-17), 접수번호 20240417800519)"
    with ablation.use("A95"):
        fixed, removed = aggregate.strip_regen_preface(OBSERVED[2] + BODY + tail)
    assert removed
    assert fixed.endswith(tail)


# --- ④ 팔 등록 ----------------------------------------------------------------

@pytest.mark.parametrize("arm", ["A84", "A91", "A92", "A93", "A94"])
def test_종전_팔은_안_켜져_있다(arm):
    with ablation.use(arm):
        assert not ablation.enabled("quiet_regen_preface")
        text = OBSERVED[0] + BODY
        assert aggregate.strip_regen_preface(text) == (text, "")


def test_A95_는_A94_에_한_칸만_더한다():
    import dataclasses

    moved = {f.name for f in dataclasses.fields(ablation.Arm)
             if getattr(ablation.ARMS["A94"], f.name)
             != getattr(ablation.ARMS["A95"], f.name)}
    assert moved == {"name", "quiet_regen_preface"}

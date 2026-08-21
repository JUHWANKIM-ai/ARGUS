"""P-B · `cite_chain_pair` — 정정 질의에서 원공시를 짝으로 붙인다.

계획서 `docs/superpowers/plans/2026-08-21-paper-grounded-repairs.md` 의 P-B, 팔 `A58`.

## 무엇이 문제였나

`cite_used` 는 *"답변이 쓴 수가 그 블록에만 유일할 때"* 근거를 붙인다. 정정 체인 안에서는
그 조건이 성립하지 않는다 — **원공시와 정정본이 같은 수를 담기** 때문이다. 그래서 정정을
묻는 답변이 정정본만 인용하고 원공시를 빠뜨린다.

## 근거는 논문이다

Relink 는 낮은 SNR 문제를 정의하고 `원본 vs [기재정정]` 을 distractor 예로 든다. ablation
최악이 **query-driven ranker 제거(−19.4%)** 다(`paper/idea/idea.md` §3.8). 랭커는 학습할
수 없으니 **성질만** 가져온다 — 질의가 정정을 물을 때만 돈다. 전역으로 최신본을 끌어오는
판(`latest_pin`)은 퇴행 2건으로 기각됐다.

## 🔴 범위를 세 겹으로 좁혔다

세우기 전에 규칙 셋을 저장 답변 336개에 걸었다(`scratch/simulate_a58_rules.py`).

    유일성 요구 통째 해제      10문항 · 골든 7 : 비골든 9   (44%)  기각
    인용본의 형제 전부        15문항 · 골든 16 : 비골든 9   (64%)  기각
    최신본의 형제 하나만      14문항 · 골든 12 : 비골든 4   (75%)  채택

비골든을 늘리는 것이 이 함수의 위험이다. `cite_used` 자체가 **비골든 부착을 23 -> 4 로
줄이는** 기준으로 네 후보 중에서 골라진 것이므로, 그 결정을 되돌리는 판은 쓸 수 없다.

## 🔴 사정거리가 계획서보다 짧다

겨냥한 다섯 중 `Q-054`·`Q-068`·`Q-082` 는 원공시가 **근거에 아예 없다** — 붙이면 없는
출처를 지목하는 것이 된다. `Q-094` 는 정정본의 체인 링크가 비어 있다. 닫는 것은 `Q-093`
하나이고 곁따라 `Q-035` 다. 남은 셋은 회수 문제다.
"""
import pytest

from argus import ablation

ON = ablation.ARMS["A58"]
OFF = ablation.ARMS["A56"]

#: `Q-093` 의 실제 체인. `tools.chain_probe` 가 링크를 안다.
CORRECTED = "20260306801011"      # 정정본 · is_latest
ORIGINAL = "20251219800469"       # 원공시 · 근거에 실려 있다

QUESTION = ("크래프톤이 2025년 12월에 공시한 신기술사업투자조합 출자 결정은 2026년 3월에 "
            "정정됐다. 정정으로 무엇이 바뀌었는지 출자금액 변동 여부와 함께 설명해줘.")

EVIDENCE = f"""## [투자판단 관련 주요경영사항] 크래프톤 – 투자판단 관련 주요경영사항
- 공시일: 2025-12-19 (접수번호 {ORIGINAL})
- 출자금액: 30,000,000,000원

## [기재정정] [투자판단 관련 주요경영사항] 크래프톤 – 투자판단 관련 주요경영사항
- 공시일: 2026-03-06 (접수번호 {CORRECTED})
- 출자금액: 30,000,000,000원
"""

#: 답변이 정정본만 가리킨 모양. `Q-093` 의 병이다.
ANSWER = ("크래프톤의 신기술사업투자조합 출자 결정은 2026년 3월 6일 정정됐습니다. "
          "출자금액 30,000,000,000원은 바뀌지 않았습니다.\n\n"
          f"(근거: [기재정정] [투자판단 관련 주요경영사항] 크래프톤, 접수번호 {CORRECTED})")


def test_fixture_matches_the_runtime_chain():
    """🔴 픽스처가 런타임과 같은 모양인지 시험이 먼저 확인한다.

    이 처방은 `tools.chain_probe(정정본)["links"]` 에 원공시가 있어야 돈다. 코퍼스나
    링크가 바뀌어 그것이 비면 처방은 조용한 무동작이 되는데, 그때 이 단정이 먼저 깨져야
    한다. 겨냥 단정만 있으면 무동작을 통과로 읽는다.
    """
    from argus.agent import tools

    probe = tools.chain_probe(CORRECTED)
    assert probe, f"{CORRECTED} 가 레코드에 있다"
    assert probe["is_latest"] is True, "정정본이 최신본으로 표시돼 있다"
    assert ORIGINAL in probe["links"], "원공시가 체인 링크에 있다"

    units = ablation._citation_units(EVIDENCE, None)
    assert {u["rcept_no"] for u in units} == {ORIGINAL, CORRECTED}


def test_off_leaves_the_original_uncited():
    """`A56` 의 동작. 두 블록이 같은 금액을 담아 유일성이 성립하지 않는다."""
    out, cited = ablation.attach_citation(ANSWER, EVIDENCE, arm=OFF, question=QUESTION)
    assert ORIGINAL not in out
    assert cited == []


def test_on_attaches_the_original():
    out, cited = ablation.attach_citation(ANSWER, EVIDENCE, arm=ON, question=QUESTION)
    assert cited == [ORIGINAL]
    assert ORIGINAL in out
    assert CORRECTED in out, "이미 있던 표기를 지우지 않는다"


def test_a_question_not_about_a_correction_is_untouched():
    """`_CHAIN_ASK` 가 안 걸리면 아무것도 안 한다.

    Relink 가 준 것은 랭커가 아니라 query-driven 이라는 성질이다. 질의를 안 가르고 전역으로
    최신본을 끌어오는 판(`latest_pin`)은 퇴행 2건으로 기각됐다.
    """
    other = "크래프톤의 신기술사업투자조합 출자금액은 얼마인가?"
    out, cited = ablation.attach_citation(ANSWER, EVIDENCE, arm=ON, question=other)
    assert ORIGINAL not in out
    assert cited == []


def test_the_original_is_not_attached_when_it_is_not_in_the_evidence():
    """근거에 없는 접수번호는 안 붙인다.

    실측: 겨냥한 다섯 중 `Q-054`·`Q-068`·`Q-082` 가 이 경우다. 원공시가 코퍼스(대장)에는
    있고 근거에만 안 실렸다. 붙이면 프롬프트가 본 적 없는 문서를 근거로 다는 것이 된다 —
    이 분기 전체가 막으려는 거짓 귀속이다.
    """
    only_correction = EVIDENCE.split("## [기재정정]")[1]
    evidence = "## [기재정정]" + only_correction
    assert ORIGINAL not in evidence
    out, cited = ablation.attach_citation(ANSWER, evidence, arm=ON, question=QUESTION)
    assert cited == []
    assert ORIGINAL not in out


def test_only_one_sibling_is_attached():
    """형제가 여럿이면 하나만 붙인다.

    형제를 전부 붙이는 판은 측정 후 기각했다 — 15문항 · 골든 16 : 비골든 9. 하나만 붙이면
    14문항 · 골든 12 : 비골든 4 다.
    """
    from argus.agent import tools

    probe = tools.chain_probe(CORRECTED)
    #: 이 체인은 형제가 하나뿐이라 상한 자체를 시험할 수 없다. 그 사실을 단정으로 남긴다 —
    #: 코퍼스가 바뀌어 형제가 늘면 이 시험을 실제 상한 시험으로 고쳐야 한다.
    assert len(probe["links"]) == 1
    _out, cited = ablation.attach_citation(ANSWER, EVIDENCE, arm=ON, question=QUESTION)
    assert len(cited) == 1


def test_the_flag_needs_its_parent():
    from dataclasses import replace

    with pytest.raises(ValueError, match="cite_chain_pair"):
        replace(ablation.ARMS["A0"], name="TEST", cite_chain_pair=True)


def test_the_cap_is_respected():
    """상한이 있는 자원에 더하는 것은 밀어내는 것이다.

    `latest_pin` 이 그 함정에 빠져 기각됐다. 답변이 이미 상한까지 인용했으면 짝을 안
    붙인다. W1 실측으로 지금 최대는 6 이고 상한은 8 이라 여유가 있지만, 여유를 가정하지
    않는다.
    """
    filler = " ".join(f"(근거: 접수번호 {n:014d})"
                      for n in range(1, ablation.MAX_CITATIONS_USED + 1))
    answer = ANSWER + "\n\n" + filler
    _out, cited = ablation.attach_citation(answer, EVIDENCE, arm=ON, question=QUESTION)
    assert cited == []

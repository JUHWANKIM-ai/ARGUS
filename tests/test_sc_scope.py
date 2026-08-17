from argus import ablation
from argus.agent import pipeline


def _arm(**kw):
    return ablation.replace(ablation.ARMS["A42"], name="probe", **kw)


def test_scope_gate_is_open_by_default():
    """With the flag off, self-consistency applies to every answer as before."""
    with ablation.use(_arm(sc_k=3, temperature=0.5)):
        assert pipeline._sc_applies("서술만 있는 답변입니다.") is True


def test_a_narrative_answer_is_skipped_when_scoped():
    with ablation.use(_arm(sc_k=3, temperature=0.5, sc_figures_only=True)):
        assert pipeline._sc_applies("서술만 있는 답변입니다.") is False


def test_an_answer_with_an_amount_is_sampled_when_scoped():
    with ablation.use(_arm(sc_k=3, temperature=0.5, sc_figures_only=True)):
        assert pipeline._sc_applies("계약금액은 1,102,000,000,000원입니다.") is True


def test_an_answer_with_a_percentage_is_sampled_when_scoped():
    with ablation.use(_arm(sc_k=3, temperature=0.5, sc_figures_only=True)):
        assert pipeline._sc_applies("지분율은 34.5%입니다.") is True

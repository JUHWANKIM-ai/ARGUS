"""The holding-ratio series line.

Q-106 asks how a reported holding ratio moved since listing. All six golden
points sit in typed fields of the holding records, but the model read only as
far as the first evidence block and dropped the 68.11% -> 50.06% collapse the
question is about. The line is the same move `revision_series` makes for a
revision chain's amounts.

R22 (2026-08-18 measurement) found a second defect one layer down: the line
used to read its chain straight off the evidence records, and the evidence
for Q-106 carried only 2 of the reporter's 6 real filings. `holding_series`
computed a perfectly correct three-filing history over what it was handed and
stated a false, confident cumulative (-0.04%p instead of -18.13%p) - grounded
in our own line, and scored `grounded=True` by the judge, because the false
statement *was* grounded in our evidence.

The fix reads the chain from `chain_lookup` (production wires
`tools.holding_chain`, which scans the whole corpus for the reporter/issuer
pair) rather than from the records the function is handed. Several fixtures
below now separate "what evidence handed us" (`PARTIAL_EVIDENCE`) from "what
the corpus actually has" (`DOOSAN`, returned by a stub `chain_lookup`),
because that separation is exactly what the old code could not tell apart.
See docs/design-rationale.md#holding-series
"""
import re

from argus import ablation
from argus.agent import aggregate, tools

#: Q-106's four movement filings, reporter `(주)두산` on `두산로보틱스`. (Two
#: more real filings restate the same ratio and add nothing to what this
#: fixture exercises; the real-corpus test below reads all six.)
DOOSAN = [
    {"rcept_no": "20231013000473", "rcept_dt": "20231013", "reporter": "(주)두산",
     "corp_name": "두산로보틱스",
     "ratio_prev": None, "ratio_current": 68.19,
     "shares_prev": None, "shares_current": "44,200,027"},
    {"rcept_no": "20250305000647", "rcept_dt": "20250305", "reporter": "(주)두산",
     "corp_name": "두산로보틱스",
     "ratio_prev": 68.19, "ratio_current": 68.15,
     "shares_prev": "44,200,027", "shares_current": "44,177,137"},
    {"rcept_no": "20250707000299", "rcept_dt": "20250707", "reporter": "(주)두산",
     "corp_name": "두산로보틱스",
     "ratio_prev": 68.15, "ratio_current": 68.11,
     "shares_prev": "44,177,137", "shares_current": "44,149,065"},
    {"rcept_no": "20260227007254", "rcept_dt": "20260227", "reporter": "(주)두산",
     "corp_name": "두산로보틱스",
     "ratio_prev": 68.11, "ratio_current": 50.06,
     "shares_prev": "44,149,065", "shares_current": "32,449,065"},
]

#: What evidence actually carried in the measured run that exposed R22: the
#: first two filings only, missing the 68.11% step and the 50.06% collapse.
PARTIAL_EVIDENCE = DOOSAN[:2]

QUESTION = ("(주)두산이 두산로보틱스에 대해 보고한 보유비율이 상장 이후 어떻게 "
            "변해 왔는지 각 보고서를 근거로 설명해줘.")


def _full_chain(*_args):
    """A `chain_lookup` stub returning the whole fixture chain regardless of
    its arguments - standing in for `tools.holding_chain` against a
    fabricated corpus."""
    return list(DOOSAN)


def _must_not_be_called(*_args):
    raise AssertionError(
        "chain_lookup was called - the gate above it should have returned "
        "before ever reaching the corpus")


def test_the_line_prefers_the_full_corpus_chain_over_partial_evidence():
    """The defect R22 found: evidence alone (`PARTIAL_EVIDENCE`) carries only
    two of the four movements. The line must still carry all four, because it
    reads the chain from `chain_lookup`, not from the records it was handed."""
    line = aggregate.holding_series(PARTIAL_EVIDENCE, QUESTION, _full_chain)
    assert line
    for ratio in ("68.19%", "68.15%", "68.11%", "50.06%"):
        assert ratio in line
    assert line.index("68.19%") < line.index("50.06%")


def test_the_line_carries_the_first_and_last_share_counts():
    """Golden points 1 and 5 are share counts, not ratios."""
    line = aggregate.holding_series(PARTIAL_EVIDENCE, QUESTION, _full_chain)
    assert "44,200,027" in line and "32,449,065" in line


def test_the_line_states_the_cumulative_change():
    """Golden point 6: 68.19 - 50.06 = 18.13 percentage points, computed from
    the true first and last of the full chain regardless of what evidence
    carried."""
    assert "18.13%p" in aggregate.holding_series(PARTIAL_EVIDENCE, QUESTION, _full_chain)


def test_only_the_top_ranked_reporter_is_laid_out():
    """The same window holds 국민연금공단 and 코봇홀딩스 filings on the same
    issuer. Without the reporter filter the line splices two chains. The
    lookup is handed both reporters below and must be asked about
    `(주)두산` alone - the reporter of the top-ranked evidence record."""
    others = [dict(r, rcept_no=f"nps-{i}", reporter="국민연금공단",
                   corp_name="두산로보틱스",
                   ratio_prev=None, ratio_current=9.0 + i,
                   shares_prev=None, shares_current="1,000")
              for i, r in enumerate(DOOSAN[:3])]
    seen = []

    def lookup(reporter, corp_name):
        seen.append((reporter, corp_name))
        return DOOSAN if reporter == "(주)두산" else others

    line = aggregate.holding_series(PARTIAL_EVIDENCE + others, QUESTION, lookup)
    assert seen == [("(주)두산", "두산로보틱스")]
    assert "(주)두산" in line and "국민연금공단" not in line
    assert "9.0%" not in line


def test_a_single_filing_is_not_a_series():
    assert aggregate.holding_series(DOOSAN[:1], QUESTION, _must_not_be_called) == ""


def test_records_without_a_current_ratio_are_skipped():
    bare = [{"rcept_no": "1", "rcept_dt": "20240101", "reporter": "(주)두산"},
            {"rcept_no": "2", "rcept_dt": "20240102", "reporter": "(주)두산"}]
    assert aggregate.holding_series(bare, QUESTION, _must_not_be_called) == ""


def test_a_question_that_does_not_ask_for_a_history_gets_nothing():
    assert aggregate.holding_series(
        DOOSAN, "두산로보틱스의 최대주주는 누구인가?", _must_not_be_called) == ""


def test_the_line_carries_no_receipt_numbers():
    """Same rule as `partner_profile` and `counterparty_branch`: the citation
    is attached by code elsewhere, and receipt numbers in a summary line get
    copied into the answer as if they were the citation."""
    line = aggregate.holding_series(PARTIAL_EVIDENCE, QUESTION, _full_chain)
    assert not re.search(r"\d{14}", line)


def test_the_flag_is_off_by_default():
    assert ablation.Arm("X").holding_series is False
    assert "holding_series" in ablation.FLAG_NAMES


def test_no_lookup_falls_back_to_the_evidence_records():
    """`chain_lookup` is optional. A caller that passes none - a test
    exercising formatting alone, or any other caller with no corpus access -
    still gets a line computed over whatever records it handed in, the
    pre-fix behaviour. `pipeline.answer_question` is not such a caller: it
    always passes `tools.holding_chain` (see
    `tests/test_evidence_line_wiring.py`), so this fallback is never what
    production runs on."""
    line = aggregate.holding_series(DOOSAN, QUESTION)
    assert "50.06%" in line and "18.13%p" in line


# --- The cap: a chain longer than what fits verbatim states what it elided --
#
# 174 (reporter, issuer) pairs exist in the corpus (measured 2026-08-19).
# Counts 2-10 alone cover 157 of them; the tail runs to 79
# (에코프로 -> 에코프로비엠, the single largest pair). A cap of 10 therefore
# shows every pair up to and including the 90th percentile in full, and the
# elision path below exists for the long tail alone - it must never look like
# the whole chain when it is not.

def _long_chain(n):
    return [{"rcept_no": f"{i:03d}", "rcept_dt": f"202001{i:02d}",
             "reporter": "many", "corp_name": "체인사",
             "ratio_current": round(100.0 - i, 2), "shares_current": str(1000 - i)}
            for i in range(1, n + 1)]


def test_a_chain_past_the_cap_states_how_many_were_elided():
    n = aggregate.HOLDING_SERIES_CAP + 5
    chain = _long_chain(n)
    line = aggregate.holding_series(chain[:2], QUESTION, lambda *_: chain)
    assert f"{n}건" in line                            # the true total, stated
    assert "생략" in line                               # elision is stated, not silent
    first_ratio = f"{chain[0]['ratio_current']:.2f}%"
    last_ratio = f"{chain[-1]['ratio_current']:.2f}%"
    assert first_ratio in line and last_ratio in line   # the true ends survive the cap
    moved = chain[0]["ratio_current"] - chain[-1]["ratio_current"]
    assert f"{-moved:+.2f}%p" in line                   # cumulative from the true ends
    assert chain[0]["shares_current"] in line
    assert chain[-1]["shares_current"] in line


def test_a_chain_at_the_cap_shows_every_filing_with_no_elision_note():
    n = aggregate.HOLDING_SERIES_CAP
    chain = _long_chain(n)
    line = aggregate.holding_series(chain[:2], QUESTION, lambda *_: chain)
    assert f"{n}건" in line
    assert "생략" not in line
    for record in chain:
        assert f"{record['ratio_current']:.2f}%" in line


# --- The real corpus path: proves the defect itself is closed --------------

def test_the_real_doosan_pair_gets_the_full_six_filing_chain():
    """Reproduces the measured defect directly against
    `artifacts/holding_records.jsonl`: evidence carrying only the two filings
    the retrieval run actually returned (20231013000473, 20250305000647) must
    still yield the true -18.13%p cumulative, because `tools.holding_chain`
    reads the whole corpus rather than trusting the evidence window.

    Deliberately hits the real corpus file instead of a monkeypatched lookup.
    A fake lookup can only prove the arithmetic is right over whatever it is
    handed, and that was already true of the old, broken code - it computed
    correctly over an incomplete evidence window too. Only reading the real
    file proves the fix: that the *right* records are now the ones being
    computed over. `tests/conftest.py`'s `block_clova_network` fixture blocks
    the LLM call this module never makes; it has nothing to say about a plain
    file read, and no other fixture here stubs out the corpus.
    """
    evidence = [tools._record_of("20231013000473"), tools._record_of("20250305000647")]
    assert all(evidence), "fixture receipt numbers moved in the corpus rebuild"

    line = aggregate.holding_series(evidence, QUESTION, tools.holding_chain)
    assert "50.06%" in line
    assert "18.13%p" in line
    assert "6건" in line

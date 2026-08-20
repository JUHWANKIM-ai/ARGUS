"""Structural regression test for the evidence-line wiring in
`pipeline.answer_question`.

Five deterministic evidence lines - `aggregate_line`, `unit_line`,
`date_disambiguation`, `revision_series`, and `holding_series` - each sit
behind their own `ablation.enabled("<flag>")` guard inside
`answer_question`, immediately followed by the call that computes the line
and prepends it to the evidence. A guard with no paired call is a flag with
no effect, and that gap slipped through once already: the wiring test this
file replaces monkeypatched `pipeline._evidence_records` and
`pipeline._prepend`, built a `seen` list to catch a prepended line - and
then only ever called `ablation.enabled("holding_series")` directly. It
never ran anything that could reach the wiring block, so deleting the block
it claimed to guard left it green.

`answer_question` cannot be run end-to-end in a unit test to exercise that
block for real: past it lies an LLM call, and `tests/conftest.py`'s
`block_clova_network` fixture refuses that call by design, on top of needing
a live retrieval index to reach the block at all. Reading the function's own
source text and asserting that a flag's guard and its call both appear is
the coarse-grained alternative available here.

A source-text assertion is normally a smell - it does not prove the call
actually runs, or that it runs only when the guard is true, or that the two
are not fifty lines apart with an unrelated block between them. What it does
prove is the one thing that matters for this failure mode: delete the guard,
delete the call, or delete the whole block, and this test breaks. That is
strictly more than a test which never inspects the wiring at all.

`compare_line` does not need this treatment: it reads its own flag inside
`aggregate.line`, and `tests/test_compare_line.py` calls `aggregate.line`
directly, so its wiring has a real call-through seam a test can exercise.
These five lines are gated one level up, inside `answer_question` itself,
and have no such seam.
"""
import inspect

import pytest

from argus import ablation
from argus.agent import pipeline

#: (flag, snippet identifying the flag's call) pairs, one per deterministic
#: evidence line wired into `answer_question`. Adding a new line there means
#: adding one entry here rather than a new bespoke test.
EVIDENCE_LINES = (
    ("aggregate_line", "aggregate.line("),
    ("unit_line", "units.unit_line("),
    ("date_disambiguation", "aggregate.date_disambiguation("),
    ("revision_series", "aggregate.revision_series("),
    ("holding_series", "aggregate.holding_series("),
    # R22 fixed `holding_series` to read its chain from the corpus rather
    # than from the evidence window (docs/design-rationale.md#holding-series).
    # A second entry for the same flag locks in that `tools.holding_chain` is
    # the third argument: deleting it would silently restore the pre-fix
    # evidence-only behaviour while leaving the entry above green, since that
    # one only checks the call exists at all.
    ("holding_series", "tools.holding_chain)"),
)


@pytest.mark.parametrize("flag, call", EVIDENCE_LINES)
def test_the_flag_gates_a_call_inside_answer_question(flag, call):
    assert flag in ablation.FLAG_NAMES, f"{flag!r} is not a real Arm flag"
    source = inspect.getsource(pipeline.answer_question)
    assert f'ablation.enabled("{flag}")' in source, (
        f"no ablation.enabled({flag!r}) guard found in answer_question")
    assert call in source, (
        f"{flag!r}'s guard exists but its call {call!r} is missing from "
        f"answer_question - the flag would be a no-op")

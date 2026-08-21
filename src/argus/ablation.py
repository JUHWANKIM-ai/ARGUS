"""Ablation arm switches: turn components on and off. No LLM call.

The controlled comparison this module exists for holds the prompt, the
temperature, the token cap, the golden set, the index snapshot and the model
fixed, and varies only the arm components.

Putting the switches outside the pipeline would break that control: swapping
code or branching per arm perturbs prompts and constants too, and the delta
can no longer be attributed. Every branch point is therefore collected here as
a flag, and the pipeline reads the flags while running the same code.

The default is the complete configuration, so leaving the switches untouched
reproduces the current system exactly. Leave-one-out reverts a single flag from
that configuration.

Reasons behind the individual arms, the flag-to-arm table and the measurements
that placed each flag are recorded separately.
See docs/design-rationale.md#arm-switch-placement
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, replace

#: Retrieval floor. `meta` uses the manifest and universe filters alone,
#: without BM25.
RETRIEVAL_MODES = ("meta", "exchange", "all")

#: Flags the code never reaches under `retrieval="meta"`. When `uses_bm25` is
#: False, `pipeline._gather_evidence` returns right after `_gather_meta`, and
#: the ladder, chain, split and counterparty hops all sit below that point.
#: Enabling one changes nothing, which would report a delta of zero for a
#: component that never ran.
#: See docs/design-rationale.md#unreachable-flag-guards
META_UNREACHABLE_FLAGS = ("hop_ladder", "chain_hop", "split_query", "counterparty_hop",
                          "followup_hop", "reverse_chain", "partner_profile",
                          "counterparty_branch")

#: Axes that must stay at their defaults under `retrieval="meta"`. The meaning
#: matches `META_UNREACHABLE_FLAGS` and only the form of the check differs,
#: because these flags default to True and would make the meta arm itself raise
#: if listed there. `_gather_meta` never calls `search_chunks`, and these axes
#: drop out of its arguments alone, so removing them changes no evidence.
#: See docs/design-rationale.md#unreachable-flag-guards
META_UNREACHABLE_AXES = ("meta_corp", "meta_date", "meta_group")

#: Reachability dependencies between flags. When a key is off, its values must
#: be off too: each child is read only inside its parent, so enabling it alone
#: reports a delta of zero for a component that never ran. `__post_init__`
#: enforces this, and the single-variable check for leave-one-out reads the
#: same mapping so that a child moved together with its parent is allowed.
#:
#: Recorded as data rather than prose, because the checks have to read it.
#: Most entries are structural: the child is read inside a function the parent
#: guards. The evidence-shaping entry is different, and was established by
#: measurement: with the calculated values removed, the parentheses the money
#: rules act on are gone, so removing the rules changes nothing.
#: See docs/design-rationale.md#unreachable-flag-guards
FLAG_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "chain_hop": ("followup_hop", "reverse_chain"),
    "hop_ladder": ("partner_profile", "counterparty_branch"),
    "calc_values": ("lean_rules",),
    "counterparty_hop": ("multi_hub",),
    "function_calling": ("calc_tools_only", "calc_queries_only",
                         "substitute_tool_results"),
    "verifier": ("substitute_verified", "provenance_gate", "regenerate_on_verify",
                 "counterparty_exempt", "amount_coverage_gate"),
    #: `aggregate_items` 는 `aggregate.line` 의 `합` 분기에 줄을 덧붙인다. 부모가 꺼지면
    #: `pipeline` 이 그 함수를 아예 안 부르므로 자식은 조용한 무동작이 된다.
    "aggregate_line": ("substitute_aggregate", "aggregate_items"),
    #: `sum_operand_fix` 는 `summand_roster` 가 근거에 쓴 명세를 되읽는다. 명세가 없으면
    #: 읽을 것이 없어 조용한 무동작이 된다.
    "aggregate_items": ("sum_operand_fix",),
    #: `roster_items` 는 `_roster_line` 이 만드는 줄에 항목을 덧붙인다. 부모가 꺼지면
    #: `roster_out` 자체를 안 넘기므로 자식은 조용한 무동작이 된다.
    "roster_line": ("roster_items",),
    #: `refusal_needs_gap` narrows a branch inside `attach_citation`. With the
    #: parent off that function returns before the branch is reached, so the
    #: child would be a silent no-op -- the failure mode `period_label` hit,
    #: where a flag changed nothing across all 112 questions and the test
    #: passed anyway because its fixture had a different shape than the runtime.
    "attach_citation": ("refusal_needs_gap", "cite_chain_pair"),
    #: The second measurement-established entry (see the paragraph above:
    #: `calc_values` -> `lean_rules` was the first). `project_scope` is not
    #: code-gated by `counterparty_relevance` -- `aggregate.select` runs its
    #: filter either way. What was measured is that without it, Q-107's
    #: evidence cap drops one of the three golden filings before `select`
    #: ever sees the record set, so `project_scope` narrows an
    #: already-incomplete set and reports a confidently wrong total rather
    #: than the silence the base arm produces. Measured end to end on Q-107
    #: after the index rebuild:
    #:   A43                     golden 2/3   total=None                (silent)
    #:   A43 + project_scope     golden 2/3   total=6,179,818,377,721   (WRONG)
    #:   V7-5                    golden 3/3   total=None                (silent)
    #:   V7-5 + project_scope    golden 3/3   total=6,890,924,666,293   (correct)
    #: (V7-5 = A42 + counterparty_relevance.) A confidently wrong total is
    #: worse than the silence it replaces, since `substitute` writes it
    #: straight into the answer, so the two flags have to move together or
    #: not at all.
    "counterparty_relevance": ("project_scope",)}

#: Axes that go unread when the golden oracle is injected, because the whole
#: retrieval lane is replaced and touching them changes no evidence.
#:
#: The guard here is that they must not move from their defaults, rather than
#: that they must be off: the oracle arm is the complete configuration plus
#: injection, so these being on is normal. What has to be prevented is
#: imitating a retrieval leave-one-out on top of the oracle, whose delta is
#: zero by construction. The reference values are the field defaults of `Arm`.
#:
#: The meta axes belong here too: with injection, exchange records skip
#: `search_chunks` entirely and periodic records pin the document by id, so
#: none of those axes changes the candidate set.
#: See docs/design-rationale.md#unreachable-flag-guards
ORACLE_UNREACHABLE_FLAGS = ("retrieval", "dense", "hop_ladder", "chain_hop",
                            "split_query", "counterparty_hop", "followup_hop",
                            "meta_corp", "meta_date", "meta_group", "multi_hub",
                            "reverse_chain", "partner_profile", "counterparty_branch")


@dataclass(frozen=True)
class Arm:
    """Configuration of one arm. Frozen: mutating it mid-run would break the
    attribution of the delta."""

    name: str
    retrieval: str = "all"
    schema_fields: bool = True
    #: Defaulting to False is the whole of the embedding isolation: on the
    #: operational path, with the switches untouched, the embedding API is
    #: never called, query embeddings included.
    #: See docs/design-rationale.md#dense-isolation
    dense: bool = False
    hop_ladder: bool = True
    chain_hop: bool = True
    #: Counterparty 2-hop, kept separate from `hop_ladder`. Bundling them turns
    #: both off together on the lower arms, so this hop's own delta could not
    #: be isolated, and its leave-one-out would not exist. On the ladder it is
    #: enabled at the top arm only.
    #: See docs/design-rationale.md#counterparty-hop-position
    counterparty_hop: bool = True
    split_query: bool = True
    calc_values: bool = True
    verifier: bool = True
    followup_hop: bool = True
    #: Meta-axis leave-one-out. Unlike the other flags these do not disable a
    #: retrieval stage; they withhold one argument from `search_chunks`, so
    #: BM25 still runs and only the candidate set widens. The default True is
    #: the prior behaviour: on the operational path no argument is withheld.
    #: See docs/design-rationale.md#meta-axis-loo
    #:
    #: Company filter, `filters["corp_name"]`.
    meta_corp: bool = True
    #: Receipt-date prefix. The fiscal year of a periodic filing is a
    #: different axis and is not withheld with it.
    meta_date: bool = True
    #: Index group. Withheld, `search_chunks` falls back to the exchange
    #: index.
    meta_group: bool = True
    #: Diagnostic only, and never a submission configuration: at evaluation
    #: time there is no golden set. True makes `pipeline.answer_question`
    #: carry the golden evidence instead of retrieving. The default False
    #: plays the same role as the embedding isolation: on the operational
    #: path the golden set is never opened, and without an injection by the
    #: runner the mapping does not exist at all.
    #: See docs/design-rationale.md#oracle-arm
    golden_oracle: bool = False
    #: The one flag in this module whose polarity is inverted: True removes
    #: the money-unit rules from the system prompt rather than enabling a
    #: component. The default False is the full rule set, so the prompt on
    #: the operational path is byte-identical.
    #: See docs/design-rationale.md#lean-prompt-arm
    lean_rules: bool = False
    #: True wires answer-arithmetic verification into the live path and
    #: replaces the answer's figure with the unique recomputed value. The
    #: default False is the prior behaviour: a defect is annotated only.
    #: Keeping that default matters, because the annotate-only behaviour is
    #: an explicit design with a rationale rather than a bug, so it is
    #: measured by switching rather than replaced.
    #: See docs/design-rationale.md#verifier-disposition
    substitute_verified: bool = False
    #: True wires the number-provenance check into the live path, which asks
    #: whether a large amount in the answer appears verbatim in the evidence.
    #: A different question from substitution, which asks whether the
    #: arithmetic holds, so the two flags are independent.
    #: See docs/design-rationale.md#arm-catalog
    provenance_gate: bool = False
    #: True wires the amount-coverage check into the live path, which asks
    #: whether the evidence carries several monetary figures while the answer
    #: states none at all. Narrowed to a question that asks for a set of
    #: figures and an answer that does not read as a refusal, since either
    #: premise alone makes the bare mismatch fire on far more correct answers
    #: than wrong ones.
    #: See docs/design-rationale.md#amount-coverage
    amount_coverage_gate: bool = False
    #: True regenerates the answer once, carrying the failure reasons into
    #: the prompt, instead of annotating it. Injecting the reasons is the
    #: whole manipulation: at temperature zero the same prompt returns the
    #: same answer. The temperature stays at zero, since buying diversity is
    #: a different arm. Regeneration happens once, and if the checks still
    #: fire the original answer is kept.
    #: See docs/design-rationale.md#verifier-disposition
    regenerate_on_verify: bool = False
    #: True appends the extracted values in `artifacts/nary_records.jsonl` to
    #: the evidence markdown. The default False is the prior behaviour, and
    #: with the file absent enabling it does nothing.
    #:
    #: The append is a union rather than an overwrite, and it happens at
    #: runtime on a copy, so no corpus file and no BM25 score changes.
    #: See docs/design-rationale.md#arm-catalog
    nary: bool = False
    #: True binds up to three counterparty hubs instead of one. The
    #: additional hubs come from the `계약상대` field of evidence already
    #: collected rather than from the question, so the router's rule that a
    #: filing company is never bound as a counterparty still holds. Each
    #: additional hub uses its own evidence slots.
    #: See docs/design-rationale.md#multi-hub-binding
    multi_hub: bool = False
    #: True passes the tool specifications to `chat()`, so the model calls
    #: tools itself and the executed results are fed back for one more call.
    #: The default False is the prior behaviour, and the request body on the
    #: operational path is byte-identical.
    #:
    #: A different axis from the calculated values carried in the evidence,
    #: so there is no dependency between the two. Enabling it removes the
    #: length cap on the answer call, because the API does not accept a tool
    #: list and a token cap together; that has to be read alongside the
    #: delta.
    #: See docs/design-rationale.md#function-calling-arm
    function_calling: bool = False
    #: True offers the calculation tools only. Measurement showed that most
    #: tool calls went to retrieval and re-read evidence already present in
    #: the prompt, diluting it.
    #: See docs/design-rationale.md#function-calling-arm
    calc_tools_only: bool = False
    #: True offers tools only on calculation queries. Offering them costs the
    #: length cap, and charging that cost to every question regressed answers
    #: that never called a tool.
    #: See docs/design-rationale.md#function-calling-arm
    calc_queries_only: bool = False
    #: True replaces the answer's figures with the values the tools returned.
    #: Substitution has no transcription step, which is where function
    #: calling lost. Not exclusive with the verifier substitution: the
    #: sources differ.
    #: See docs/design-rationale.md#tool-result-substitution
    substitute_tool_results: bool = False
    #: True keeps a refusal from inventing evidence. The only adopted
    #: candidate that changes the prompt, so re-measurement misses the
    #: request cache entirely and a system-prompt rule can perturb answers
    #: to unrelated questions. Measured alone for that reason.
    #: See docs/design-rationale.md#arm-catalog
    strict_refusal: bool = False
    #: True catches a quarterly report that does not exist. Without it the
    #: named-report check reads only the report subtype and fiscal year, so
    #: two quarters of the same year are indistinguishable and a false
    #: premise in the question is accepted.
    #: See docs/design-rationale.md#arm-catalog
    quarter_gate: bool = False
    #: True separates questions asking for a projection the filings do not
    #: contain. No LLM call. Same family as the ambiguous-reference and
    #: quarter gates, kept as separate flags so the delta stays attributable.
    #: See docs/design-rationale.md#arm-catalog
    speculation_gate: bool = False
    #: True separates requests to transcribe identifier numbers or contact
    #: details. No LLM call. Does not replace `safety.mask_pii`: that one
    #: blocks such data arriving inside the evidence, this one separates the
    #: request itself, and both layers are needed.
    #: See docs/design-rationale.md#arm-catalog
    identifier_gate: bool = False
    #: True refuses a question that both asserts a conclusion the disclosure
    #: record does not support and demands it be stated as fact. No LLM call.
    #: Same family as `speculation_gate`/`identifier_gate`: a conjunction of a
    #: claim term and a demand term, because either alone occurs in ordinary
    #: disclosure prose (see `_PREMISE_CLAIM`/`_PREMISE_DEMAND` in
    #: `argus.agent.router`). Targets two measured safety failures neither
    #: existing gate reaches: `S-007` (the question forbids the refusal
    #: wording itself and demands the assertion the golden forbids) and
    #: `S-010` (refuse/disparage/refuse — `refusal_compaction` is the other
    #: half of that fix, for the case this gate does not reach because the
    #: conclusion drawn is correct and only the middle is harmful).
    #:
    #: Deliberately narrow, and deliberately not widened to catch `S-008`/
    #: `S-009` — two close family members that already pass 7 of 7 measured
    #: rounds without this gate. Widening the pattern to reach them buys no
    #: measured gain and adds false-positive surface; see
    #: `tests/test_router_safety_gates.py::test_the_family_neighbours_are_deliberately_not_caught`.
    #:
    #: **Known cost, measured against `S-007`'s golden answer.** The golden
    #: is two-part: state that the blanket claim is unconfirmed, *and* name
    #: what is confirmable instead ("전부 정상 이행되고 있다는 사실은 공시에서
    #: 확인되지 않습니다. 확인되는 계약 건만 밝히고 그 밖의 상태는 단정하지
    #: 않습니다."). This gate's refusal wording is generic and identical for
    #: every question that reaches it, so routing here answers the first half
    #: and drops the second. The flag stays off by default for that reason;
    #: measurement decides whether the trade is worth it.
    false_premise_gate: bool = False
    #: True accepts a latest record as a chain seed so the walk can reach the
    #: original filing. No LLM call. Gated by the question asking for the
    #: pre-revision value, which is what keeps it from reopening the case
    #: rejected earlier.
    #: See docs/design-rationale.md#reverse-seeding
    reverse_chain: bool = False
    #: True computes the aggregate here and carries it as one line of
    #: evidence. No LLM call. A different axis from the calculated values
    #: inside a single record: this one spans several records, and it draws
    #: on the `amount` field, which survives with those values removed.
    #: See docs/design-rationale.md#evidence-slot-vs-line
    aggregate_line: bool = False
    #: True replaces a wrong total in the answer with the deterministic
    #: aggregate. No LLM call. A different source from the verifier
    #: substitution, which parses an equation out of the answer, so the two
    #: are not exclusive.
    #: See docs/design-rationale.md#model-recomputes
    substitute_aggregate: bool = False
    #: True lays out the amounts of a revision chain in filing-date order. No
    #: LLM call. A different axis from the aggregate line, which carries a
    #: single value, and their trigger vocabularies are kept disjoint.
    #: See docs/design-rationale.md#revision-series
    revision_series: bool = False
    #: True appends the revision reason and summary to the runtime evidence.
    #: No LLM call. Fills the gap where schema matching discards the source
    #: rows. Cannot be enabled together with the n-ary append, since the
    #: grouping picks one of the two and the other is silently ignored.
    #: See docs/design-rationale.md#arm-catalog
    correction_fields: bool = False
    #: True records the counterparty distribution the enumeration discards,
    #: as one line. No LLM call. The cap is not raised: raising it would
    #: require widening the evidence window, which was rejected.
    #: See docs/design-rationale.md#partner-profile
    partner_profile: bool = False
    #: True spends the counterparty slots on the records most relevant to the
    #: question instead of one per partner, and only for questions that do not
    #: ask for the roster. No LLM call and no extra retrieval; the same
    #: candidate set is reordered under the same cap.
    #:
    #: Off by default because it pairs with the supply-region field. Without
    #: that field an overseas contract shares no vocabulary with a question
    #: that names its country in Korean, so relevance ranking hands every slot
    #: to one partner and the breadth that carried the answer is lost.
    #: See docs/design-rationale.md#partner-quota
    counterparty_relevance: bool = False
    #: True keeps a regenerated answer whose failing kinds are a strict subset
    #: of the original's, instead of requiring it to be clean. No extra LLM
    #: call: the regeneration already ran and its verdict is already computed.
    #:
    #: The one-shot rule discards a strictly better answer. A recorded run
    #: regenerated an answer whose calculation defect was gone and kept the
    #: original because a provenance defect remained.
    #: See docs/design-rationale.md#regeneration-adoption
    regenerate_partial: bool = False
    #: True emits the two amounts a comparison question names, their
    #: difference and which side is larger, as one deterministic line. No LLM
    #: call: the values come from the evidence records.
    #:
    #: The aggregate line reads sums, differences within a revision chain and
    #: per-unit quotients. A comparison between two separate contracts matched
    #: none of those, so questions of that shape received no deterministic line
    #: at all and the model did the arithmetic and the unit conversion itself.
    #: See docs/design-rationale.md#comparison-line
    compare_line: bool = False
    #: True prepends the scale a periodic table declares, with its multiplier,
    #: to the evidence. No LLM call and no retrieval.
    #: See docs/design-rationale.md#unit-line
    unit_line: bool = False
    #: True draws the extra self-consistency samples only for an answer that
    #: states a figure. The technique needs a fixed answer set and a parser to
    #: take a majority over, which its own paper states, and a narrative answer
    #: has neither: every sample becomes its own key and the first is selected
    #: after paying for the rest.
    #: See docs/design-rationale.md#self-consistency-scope
    sc_figures_only: bool = False
    #: True recomputes the parenthesised unit conversion in the answer. No LLM
    #: call. Body figures are copied from the evidence and are correct, while
    #: the parenthesised value is divided by the model and is not. One of the
    #: cases is created by the aggregate substitution, so this disposition runs
    #: after it. A different position from the evidence shaping that splits the
    #: same notation before generation; both share the formatting helper, since
    #: two formats would be read as two values. Every LLM call is finished by
    #: this point, so prompts stay byte-identical and the request cache hits.
    #: See docs/design-rationale.md#unit-conversion
    fix_unit_parens: bool = False
    #: True exempts an orderer from the missing-company check. No LLM call. An
    #: orderer named by the question placed the order rather than filing it and
    #: cannot appear in the evidence, so the check is a permanent false
    #: positive there, attaching a defect note to correct answers.
    #: See docs/design-rationale.md#verifier-disposition
    counterparty_exempt: bool = False
    #: True aligns a figure that contradicts an expression the answer wrote
    #: itself. No LLM call. Not a measure of the reasoning axis but one failure
    #: mode of it: it applies only where the answer contains an expression.
    #: Independent of the verification stage, since it reads the answer string
    #: after generation.
    #: See docs/design-rationale.md#self-contradiction
    self_consistency_fix: bool = False

    #: True carries the boundary of the enumerated set at the head of the
    #: evidence. The counterparty hop already knows every partner, but with
    #: that roster only inside a block the answer omitted some of them. The
    #: whole point is to make the boundary visible without the question having
    #: to ask for completeness explicitly. Same position as the aggregate line:
    #: one line at the head rather than more slots.
    #: See docs/design-rationale.md#roster-position
    roster_line: bool = False

    #: True restores an exact figure from the evidence where the answer gave
    #: only a rounding. The mirror of the conversion fix: that one repairs a
    #: correct figure with a wrong parenthesis, this one a parenthesis with no
    #: figure. Silent when two evidence amounts round to the same value, since
    #: asserting one would turn a correct approximation into a wrong exact
    #: figure.
    #: See docs/design-rationale.md#unit-conversion
    expand_rounded: bool = False

    #: True appends a concluding sentence when the value the question asked for
    #: is absent from the answer. Reaches a case substitution cannot: there the
    #: model used correct figures and combined them wrongly, so both are in the
    #: evidence and the substitution condition never holds while the requested
    #: value is missing entirely. The verification net covered whether a figure
    #: came from the evidence but not whether the question was answered.
    #: See docs/design-rationale.md#state-total
    state_total: bool = False

    #: True lays one reporter's holding ratios out in filing order at the head
    #: of the evidence. A different axis from `revision_series`: that one
    #: follows a revision chain's amounts, this one a reporting series' ratios.
    #: The model read only as far as the first evidence block and dropped the
    #: largest move in the series.
    #: See docs/design-rationale.md#holding-series
    holding_series: bool = False
    #: True narrows the aggregation set by the project the question names,
    #: read from the evidence's supply-region fields. Without it a sum
    #: question that pins a project and an orderer has no axis to pin the set
    #: with, so the module stays silent and the model sums by itself.
    #: Requires the index built after the supply-region render: before it no
    #: record carries the field and the axis reads nothing.
    #: See docs/design-rationale.md#project-scope
    project_scope: bool = False
    #: True appends a decisive line's content to the answer when the answer
    #: dropped most of its figures. The evidence-side lines require the model
    #: to copy them, and two measured runs show it does not. `substitute` and
    #: `state_total` already close that gap for questions with a pinned
    #: operation; this reaches the narrative ones, which have none.
    #: See docs/design-rationale.md#gap-repair
    gap_repair: bool = False
    #: True removes a sentence that recommends a path outside the corpus. The
    #: refusal itself is accurate; the referral trailing it names sources we
    #: were not given. Two prompt-side attempts were measured and rejected,
    #: one of them backwards: naming the sources in the prompt tripled them.
    #: See docs/design-rationale.md#external-referral
    external_referral_strip: bool = False
    #: True drops everything after an answer's opening refusal, keeping a
    #: citation. A different axis from `external_referral_strip`: that one
    #: removes a sentence naming a path outside the corpus, this one removes
    #: whatever follows an opening refusal regardless of what it says.
    #: Targets `S-010`: the conclusion is correct (refusing to rank a
    #: competitor's technical competence from disclosure filings) and the
    #: body that follows is harmful (it disparages the named competitor
    #: anyway). `false_premise_gate` cannot reach this case, because a
    #: pre-generation gate cannot tell a correct conclusion from an incorrect
    #: one — it can only refuse before generation runs at all.
    #: See `argus.agent.safety.compact_after_refusal`.
    refusal_compaction: bool = False
    #: True cites **every** evidence block the answer demonstrably used, not
    #: just enough to make one citation exist.
    #:
    #: `attach_citation` short-circuits on `quick_check(answer)["has_citation"]`,
    #: which asks only whether *some* `근거: …14자리` is present. So an answer
    #: that cites one filing -- or the wrong filing -- satisfies it and nothing
    #: is attached. Measured on the `A49` local run: 10 golden key points across
    #: 9 questions name a receipt number the answer never writes
    #: (`Q-023` `Q-035` `Q-054` `Q-068` `Q-082` `Q-093` `Q-094` `Q-105`
    #: `Q-106`x2), and `Q-110` cites three filings that are not golden at all
    #: while five golden filings sit in its evidence.
    #:
    #: 🔴 The rule reads the evidence, never the golden set. A unit counts as
    #: used when one of its own figures appears in the answer body -- the same
    #: `used` set `attach_citation` already computes to decide false
    #: attribution. Citing what the answer used is defensible at inference
    #: time; citing what the golden set names would not be.
    #:
    #: README section 4.4 says receipt numbers are attached by the system
    #: rather than copied by the model. That held only for answers carrying no
    #: citation at all; this closes the rest.
    cite_used: bool = False
    #: True widens the chain-seed window so a superseded filing anywhere in the
    #: evidence pulls its latest revision in, not only one in the top two.
    #:
    #: `Q-116` states 581,790,000,000 for the 함안복합 파워블록 contract and
    #: cites the original filing. The revision says 583,874,002,800 -- the
    #: answer quotes a **superseded figure**, on the axis README sections 2.1
    #: and 4.2 name as the system's first difficulty and headline feature.
    #:
    #: 🔴 The first diagnosis was wrong and is recorded so it is not repeated.
    #: `scratch/audit/failure_dossier_A49.json` called it a selection failure --
    #: "the revision is in the corpus, the answer did not pick it". Measurement
    #: says otherwise: the revision never reached the evidence.
    #: `query_by_counterparty` does return it (`is_latest=True`, with the
    #: amount), `tools.chain_probe` knows the link, and `_expand_chain` exists
    #: to walk it. What blocked it was `CHAIN_SEED_WINDOW = 2`: the superseded
    #: filing sat fifth in the evidence, outside the window, so no seed was
    #: ever made. It is a retrieval gap with a one-constant cause.
    #:
    #: Measured on the `A49` local run: 18 places in 17 questions hold a
    #: superseded filing whose latest sibling is absent, all at ranks 3-10.
    #:
    #: 🔴 Additive only. Widening the window adds seeds; it removes nothing, so
    #: questions that need the superseded value in evidence keep it -- `Q-054`
    #: (what was corrected) and `Q-111` (a termination and its replacement)
    #: both do. `MAX_CHAIN_DOCS` still caps how many documents a chain adds.
    latest_pin: bool = False
    #: True names the quarter in a quarterly filing's evidence header.
    #:
    #: Two quarterly filings of one year rendered a **byte-identical** header:
    #: `[분기보고서 2025] 케이티 – IV. 이사의 경영진단 및 분석의견` for both the first
    #: and the third quarter. `Q-105` names the third quarter (20251114002201);
    #: the answer cited the first (20250515002428). Both were in the evidence,
    #: so this is neither retrieval nor ranking -- nothing in the evidence told
    #: them apart.
    #:
    #: `_section_markdown`'s own docstring says the header carries "report and
    #: period". It carried the year only. This makes that true.
    #:
    #: Only quarterlies gain a token. `base_month` is 3 or 9 for every quarterly
    #: in the corpus (30,125 / 21,503 sections), so the mapping is total. Annual
    #: and half-year filings are unique within a year already, and adding a token
    #: there would change evidence text for every periodic question to no purpose.
    period_label: bool = False
    #: True states, as one decisive line, that the same section arrived as a
    #: stub in one filing and as a body in another.
    #:
    #: `Q-105` was given 88 / 88 / 88 characters of stub and 7,846 of body for
    #: one section and answered *"세 문서 간 차이점은 없으며"*. README section 4.3
    #: forbids stating a wrong value with confidence; a wrong comparison is the
    #: same defect on the narrative axis.
    #:
    #: 🔴 Only a categorical split speaks, and that is deliberate. The same-path
    #: premise holds for 14 question-paths across the 112 (measured
    #: 2026-08-21), but in almost all of them the extents are within a tenth of
    #: each other -- the same section across years -- and stating extents there
    #: is noise. Requiring one stub and one body narrows it to a single
    #: question in this corpus. The rule generalises; the corpus has one
    #: instance of it.
    #:
    #: `STUB_CHARS = 100` is the golden set's own notion: `Q-105` key point 4
    #: reads "100자 미만 스텁".
    contrast_line: bool = False
    #: True removes the "no citation" line from the verification note once a
    #: citation has in fact been attached.
    #:
    #: `_verify` builds the note and `attach_citation` runs **after** it, so an
    #: answer that ends up carrying a receipt number ships with
    #: `(주의: … 근거 표기가 없습니다)` attached to it. Measured 2026-08-21: 28 of 336
    #: local answers and 6 of 112 server answers do this. It is the text a judge
    #: reads, and in it the system argues with itself.
    #:
    #: Reordering does not fix it: `attach_citation` has to run after every LLM
    #: call so the generation, critic and regeneration prompts stay
    #: byte-identical and the request cache hits.
    citation_note_fix: bool = False
    refusal_needs_gap: bool = False
    cite_chain_pair: bool = False
    aggregate_items: bool = False
    sum_operand_fix: bool = False
    roster_items: bool = False
    #: 대체본 블록에 폐기 표시를 각인한다. 레코드의 `related` · `is_latest` 는 이미 있고
    #: `to_markdown` 이 안 쓴다. 런타임 창(`CHAIN_SEED_WINDOW = 2`)과 무관하게 붙으므로
    #: `latest_pin`(퇴행 2건으로 기각)과 달리 근거 슬롯을 하나도 더 안 쓴다.
    #: 최신본 접수번호는 적지 않는다 — 근거에 안 실린 공시의 14자리를 블록에 쓰면
    #: `error_analysis._blocks` 가 적재된 골든으로 읽어 우리 재현율만 오른다.
    stale_note: bool = False
    #: 보유비율 추이 줄의 발화 낱말에 `달라` 를 더한다. 낱말 목록 자체는 코드 상수라
    #: 플래그로 가를 수 없으므로, 이 플래그는 **그 줄을 그 낱말로도 내보낼지**를 가른다.
    holding_ask_wide: bool = False
    #: 구성을 묻는 질문에서 그 표의 합계를 결정적 줄로 못 박는다. 백분율의 분모다.
    composition_total: bool = False
    #: 보유비율 추이 줄에 `주요계약체결` 추이를 한 줄 더 붙인다. 그 값은 `shares_current`
    #: 가 아니라 `fields` 안의 문자열에 있어 종전 줄이 못 읽었다.
    pledged_series: bool = False
    #: 부문별 매출을 `수출 + 내수 = 합계` 로 검산해 한 줄로 낸다. 같은 공시에 표가 둘
    #: 있고 부문값이 1 다를 때 검산이 맞는 쪽을 가려 준다.
    #: 계약상대가 같은 두 회사의 공시를 잇고 금액 차를 못 박는다. 관계를 묻는 질문에
    #: 차액이 답의 일부인 꼴 — `line` 의 차 연산이 안 걸리는 자리다.
    segment_sales: bool = False
    counterparty_pair: bool = False
    #: 공시가 스스로 적어 둔 원화 환산액과 그 환율을 문면에서 그대로 옮긴다. 우리가
    #: 계산하지 않는다 — 공시와 다른 수를 내지 않기 위해서다.
    fx_conversion: bool = False
    #: 정정 전후 두 벌을 줄 단위로 견주어 무엇이 바뀌고 무엇이 안 바뀌었는지 낸다.
    revision_diff: bool = False
    #: 답변이 결정적 줄의 표지(`결정적 집계` 따위)를 출처로 인용한 자리를 걷어낸다.
    strip_marker_citation: bool = False
    #: 보유비율 추이 줄이 걸음마다 접수번호를 나른다. `Q-106` 은 *"각 보고서를 근거로"* 를
    #: 묻는데 그 번호가 근거 블록에도 없어 답변이 알 길이 없었다.
    holding_receipts: bool = False
    #: periodic 정정본 블록이 무엇을 정정한 것인지 밝힌다. 🔴 이 줄의 14자리는 우리 근거
    #: 완전성 회계에 들어간다 — 원공시가 골든이면 본문 없이 적재로 세어진다. 채택 판정은
    #: 근거 완전성을 **두 가지로** 내야 한다(날값 · 포인터 줄 제외값).
    section_chain_note: bool = False
    #: True separates which filing a date points at when two share that date.
    #: With both filings in the evidence the selection rather than the
    #: retrieval goes wrong. The rule is what the date means: a contract filed
    #: that day, not a revision of a contract from another day, and `related`
    #: carries that fact.
    #: See docs/design-rationale.md#date-disambiguation
    date_disambiguation: bool = False
    #: True separates a reference with no identifiable subject from an
    #: out-of-range question. No LLM call. Answering the former with the range
    #: notice tells the user their company is outside the corpus when no
    #: company was named at all, and suppresses the question back that the
    #: convention requires. The same distinction as the absent-report notice,
    #: one branch of which had been missing.
    #: See docs/design-rationale.md#refusal-notices
    ambiguous_gate: bool = False
    #: True attaches the citation by code. No LLM call.
    #:
    #: The mechanism matches the substitution arms: a receipt number is a
    #: fourteen-digit number, and the recurring finding is that the model gets
    #: numbers wrong the moment it has to transcribe them. The critic detects a
    #: missing citation reliably but its disposition only asks whether the
    #: evidence supports the answer, so a malformed citation passes. The fourth
    #: branch of disposition, after annotating, substituting and regenerating.
    #:
    #: Because it costs no LLM call, generation prompts stay byte-identical and
    #: the request cache hits, so re-measuring this arm costs no generation
    #: credits.
    #: See docs/design-rationale.md#citation-detection
    attach_citation: bool = False
    #: True states which counterparties in an enumerated set are inside the
    #: 70-company coverage universe and which are outside it, as one
    #: deterministic line. No LLM call. Fires only for a question that asks
    #: how the set divides, and only when both branches are non-empty, since a
    #: single branch is not a split.
    #: See docs/design-rationale.md#counterparty-branch
    counterparty_branch: bool = False
    #: Sample count for self-consistency. One is the prior behaviour, so on the
    #: operational path answer generation is exactly one call.
    #: See docs/design-rationale.md#self-consistency-arm
    sc_k: int = 1
    #: Temperature, the control variable this module holds fixed. It used to be
    #: hardcoded in the pipeline with no place to express it here; moving it
    #: onto the arm keeps every earlier measurement valid only because the
    #: default is zero.
    #:
    #: Self-consistency is the one arm that breaks this control, so it is
    #: measured alone and not combined with others. The regeneration arm keeps
    #: the temperature at zero, which is what makes the two different
    #: mechanisms rather than variants of one.
    #:
    #: The request cache keys on the body, temperature included, so this arm
    #: pays in full on every re-run, and its k samples share one key unless the
    #: cache is bypassed from the second call onward.
    #: See docs/design-rationale.md#self-consistency-arm
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if self.retrieval not in RETRIEVAL_MODES:
            raise ValueError(f"retrieval 은 {RETRIEVAL_MODES} 중 하나여야 합니다: {self.retrieval}")
        # `tools._group_records` picks one of the two, so enabling both makes
        # the second silently ignored and attributes that arm's delta to a
        # different manipulation.
        if self.nary and self.correction_fields:
            raise ValueError("nary 와 correction_fields 는 함께 켤 수 없습니다 "
                             "(_group_records 가 하나만 고른다 — Δ 가 조용히 0)")
        if self.retrieval == "meta":
            on = [f for f in META_UNREACHABLE_FLAGS if getattr(self, f)]
            if on:
                raise ValueError(
                    f"retrieval='meta' 는 `_gather_meta` 뒤에서 즉시 반환하므로 {on} 은 "
                    f"켜도 코드가 도달하지 않습니다 — Δ 가 조용히 0 이 됩니다 (arm {self.name!r}). "
                    f"BM25 이후 arm 에 붙이십시오.")
            fields = type(self).__dataclass_fields__
            moved = [f for f in META_UNREACHABLE_AXES
                     if getattr(self, f) != fields[f].default]
            if moved:
                raise ValueError(
                    f"retrieval='meta' 는 `filter_docs` 만 쓰고 `search_chunks` 를 한 번도 "
                    f"부르지 않으므로 {moved} 를 기본값에서 움직여도 근거가 안 바뀝니다 — "
                    f"Δ 가 조용히 0 이 됩니다 (arm {self.name!r}). 메타 축 LOO 는 BM25 arm "
                    f"위에서 재십시오.")
        for parent, children in FLAG_DEPENDENCIES.items():
            if getattr(self, parent):
                continue
            on = [c for c in children if getattr(self, c)]
            if on:
                raise ValueError(
                    f"{parent}=False 이면 {on} 은 코드가 도달하지 않습니다 — Δ 가 조용히 "
                    f"0 이 됩니다 (arm {self.name!r}). 함께 끄십시오.")
        if self.golden_oracle:
            fields = type(self).__dataclass_fields__
            moved = [f for f in ORACLE_UNREACHABLE_FLAGS
                     if getattr(self, f) != fields[f].default]
            if moved:
                raise ValueError(
                    f"golden_oracle=True 는 검색 차선을 골든 주입으로 갈아치우므로 {moved} "
                    f"를 기본값에서 움직여도 근거가 안 바뀝니다 — Δ 가 조용히 0 이 됩니다 "
                    f"(arm {self.name!r}). 검색 LOO 는 오라클 없이 재십시오.")
        if self.sc_k < 1:
            raise ValueError(f"sc_k 는 1 이상이어야 합니다: {self.sc_k} (arm {self.name!r})")
        # Calling the same prompt k times at temperature zero returns
        # identical samples, so the majority is unanimous by construction and
        # self-consistency becomes a no-op whose zero would read as evidence
        # that the technique has no effect.
        if self.sc_k > 1 and self.temperature <= 0.0:
            raise ValueError(
                f"sc_k={self.sc_k} 인데 temperature={self.temperature} 입니다 — 온도 0 에서는 "
                f"표본이 전부 같아 다수결이 정의상 만장일치가 되고 Δ 가 조용히 0 이 됩니다 "
                f"(arm {self.name!r}).")

    @property
    def uses_bm25(self) -> bool:
        return self.retrieval != "meta"

    @property
    def uses_group_index(self) -> bool:
        """Whether the non-exchange indexes are visible. The BM25-only arm
        sees the exchange index alone."""
        return self.retrieval == "all"


# --- Cumulative ladder ------------------------------------------------------
A0 = Arm("A0", retrieval="meta", schema_fields=False, hop_ladder=False,
         chain_hop=False, counterparty_hop=False, split_query=False,
         calc_values=False, verifier=False, followup_hop=False)
A1 = replace(A0, name="A1", retrieval="exchange")
A1B = replace(A1, name="A1b", retrieval="all", schema_fields=True)
#: `followup_hop` is enabled explicitly here. It pairs with `chain_hop`, and
#: writing both keeps one from being moved without the other; `__post_init__`
#: checks the pair.
A4 = replace(A1B, name="A4", hop_ladder=True, chain_hop=True, split_query=True,
             followup_hop=True)
A5 = replace(A4, name="A5", calc_values=True)
A6 = replace(A5, name="A6", verifier=True)
#: Same flags as the arm below it: this step is unconditional code with no
#: flag, so its delta is zero by definition. The slot exists to keep the
#: ladder numbering aligned with the report.
#: See docs/design-rationale.md#a7-identity
A7 = replace(A6, name="A7")
A8 = replace(A7, name="A8", counterparty_hop=True)

# --- Leave-one-out: revert a single component from the top arm --------------
#: `followup_hop` is turned off with it, since it is unreachable without the
#: chain hop. This one is therefore not strictly a single-variable
#: manipulation: it removes the revision edges and the related-filing edges
#: together, and the arm below separates the two.
#: See docs/design-rationale.md#followup-edge-split
L1 = replace(A8, name="L1", chain_hop=False, followup_hop=False)
L2 = replace(A8, name="L2", schema_fields=False)
#: By flags this is identical to the ladder step below the top arm. The
#: separate name records the reading direction: the ladder asks how much
#: adding a component gains, the leave-one-out asks how much removing it
#: loses. The two numbers must agree up to sign, and a disagreement means
#: something is wrong.
L3 = replace(A8, name="L3", counterparty_hop=False)
#: Removes only the related-filing edges that were bundled into the chain
#: leave-one-out. Its measured effect is confined to a single question, which
#: is what turns a claim of no contribution into a measurement.
#: See docs/design-rationale.md#followup-edge-split
L4 = replace(A8, name="L4", followup_hop=False)
#: The three meta-axis leave-one-outs. The `L5` name is left unused, because
#: reusing a rejected name would make it read as the same slot in earlier
#: artifacts.
#:
#: These belong in the leave-one-out set rather than among the side branches:
#: they fill the blanks of that table. Their deltas are expected to be
#: strongly negative, since the meta prefilter is the substance of the
#: retrieval floor, so they measure that floor's contribution again from the
#: top arm rather than proposing a removal.
#: See docs/design-rationale.md#meta-axis-loo
L6 = replace(A8, name="L6", meta_corp=False)
L7 = replace(A8, name="L7", meta_date=False)
L8 = replace(A8, name="L8", meta_group=False)

# --- Embedding hybrid, off the ladder ---------------------------------------
# See docs/design-rationale.md#dense-isolation
A2 = replace(A1B, name="A2", dense=True)            # at the position defined for it
A2F = replace(A8, name="A2F", dense=True)           # on the submission configuration

# --- Oracle: retrieval held fixed, generation measured alone ---------------
#: Diagnostic only, off the ladder. One flag apart from the top arm, and that
#: flag replaces the whole retrieval lane with the golden evidence. It replaces
#: rather than adds, so the evidence shrinks and the delta cannot be read as
#: the shortfall of retrieval. Neither a submission configuration nor an upper
#: bound.
#: See docs/design-rationale.md#oracle-arm
ORACLE = replace(A8, name="ORACLE", golden_oracle=True)

# --- Prompt rule diet, off the ladder ---------------------------------------
#: One flag apart from the top arm, removing the money-unit rules from the
#: system prompt. Not a ladder step: the same position with a different
#: prompt. It targets one question while putting twelve currently-correct ones
#: at risk, and that asymmetry is its acceptance condition.
#: See docs/design-rationale.md#lean-prompt-arm
A8_LEAN = replace(A8, name="A8-lean", lean_rules=True)

# --- Substitute the recomputed value, off the ladder ------------------------
#: One flag apart from the top arm, turning the verification stage from a
#: judge that annotates into a corrector that substitutes. Not a ladder step:
#: the same position with a different disposition of the verdict. Structurally
#: dependent on the verification stage being enabled.
#: See docs/design-rationale.md#verifier-disposition
A20 = replace(A8, name="A20", substitute_verified=True)

# --- Number provenance gate, off the ladder ---------------------------------
#: One flag apart from the top arm, asking whether a large amount in the
#: answer appears verbatim in the evidence. A different question from the
#: substitution arms, which ask whether the arithmetic holds.
#: See docs/design-rationale.md#arm-catalog
A21 = replace(A8, name="A21", provenance_gate=True)

# --- Regenerate once with the reasons, off the ladder -----------------------
#: Two flags apart from the top arm, which is unusual enough to record why.
#: The provenance gate is an input rather than a manipulation: its own delta is
#: structurally zero, because its only output is a note and scoring strips that
#: note on both tracks. It contributes nothing of its own to this arm's delta
#: and merely creates a verdict for the regeneration to consume, in a form that
#: can be carried into the prompt verbatim.
#:
#: Substitution stays off, so that the two dispositions are measured side by
#: side rather than mixed.
#: See docs/design-rationale.md#verifier-disposition
A20R = replace(A8, name="A20r", regenerate_on_verify=True, provenance_gate=True)

# --- Targeted n-ary extraction, off the ladder ------------------------------
#: One flag apart from the top arm, appending extracted values to the evidence
#: of records the schema did not match. A union rather than an overwrite.
#:
#: The only arm deliberately exempted from the rule that an arm with nothing to
#: target is not built, and a delta of zero was recorded as a prediction before
#: it ran. A non-zero result means the underlying classification of those
#: records has to be re-examined.
#: See docs/design-rationale.md#arm-catalog
A18 = replace(A8, name="A18", nary=True)

# --- Deterministic citation attachment, off the ladder ----------------------
#: One flag apart from the operational default. What it targets is measured:
#: roughly a quarter of answers lack a citation in the required form.
#:
#: No accuracy gain is claimed. Attachment adds characters to the tail of the
#: answer and does not touch the body figures, so the accuracy axis should be
#: unchanged. That is the prediction; if the accuracy moves, attachment reached
#: another axis of the scorer and the reason has to be found.
A25 = replace(A20R, name="A25", attach_citation=True)

# --- Ambiguous-reference branch, off the ladder -----------------------------
#: One flag apart from the operational default. Kept separate from the
#: citation arm, whose axis differs: combining them would leave the delta
#: unattributable. The combination is measured as its own arm.
A26 = replace(A20R, name="A26", ambiguous_gate=True)

# --- Both together, adoption candidate --------------------------------------
#: The individual deltas are secured first and the combination measured after.
#: Their axes differ, so no interaction is expected, but expecting none and
#: measuring none are different things.
A25_A26 = replace(A20R, name="A25+A26", attach_citation=True, ambiguous_gate=True)

# --- Refusal rule, off the ladder -------------------------------------------
#: Stacked on the adopted default, since building it on an earlier arm would
#: mix the deltas of the components adopted since.
#:
#: The first version was measured and rejected: listing forbidden terms in the
#: rule tripled the number of answers that pointed at external sources. Its
#: artifacts are kept, because a rejection is a result. This version states
#: only what to do, with no forbidden terms.
A27B = replace(A25_A26, name="A27b", strict_refusal=True)

# --- Absent quarterly report check, off the ladder --------------------------
#: Stacked on the adopted default.
#: See docs/design-rationale.md#arm-catalog
A28 = replace(A25_A26, name="A28", quarter_gate=True)

# --- Speculation refusal branch, off the ladder -----------------------------
#: Stacked on the adopted default, since an earlier base would mix in the
#: deltas of the components adopted since.
#: See docs/design-rationale.md#arm-catalog
A31 = replace(A28, name="A31", speculation_gate=True)

# --- Identifier transcription refusal, off the ladder -----------------------
#: Kept separate from the speculation arm, whose axis differs: combining them
#: would leave the delta unattributable. The combination is measured as its own
#: arm if both are adopted.
A32 = replace(A28, name="A32", identifier_gate=True)

# --- Reverse seed from a revision to the original, off the ladder -----------
#: Does not touch the narrowed termination branch: what it opens is the
#: revision chain, and the question gate applies alongside it.
#: See docs/design-rationale.md#reverse-seeding
A33 = replace(A28, name="A33", reverse_chain=True)

# --- Deterministic aggregate line, off the ladder ---------------------------
#: The largest arm of this generation, targeting seven recorded wrong answers.
#: See docs/design-rationale.md#evidence-slot-vs-line
A29A = replace(A28, name="A29a", aggregate_line=True)

# --- Aggregate line and reverse chain together ------------------------------
#: This is why the reverse-chain arm measured zero on its own: it recovered
#: every golden record for the target question, but the remaining failure was
#: arithmetic, so the answer did not flip. Without measuring the combination,
#: that arm would be rejected as contributing nothing, and the recovery would
#: then be missing once the arithmetic is fixed.
A29A_A33 = replace(A28, name="A29a+A33", aggregate_line=True, reverse_chain=True)

# --- Aggregate line plus substitution ---------------------------------------
#: The aggregate line alone does not flip the target question: the model
#: recomputes even with the exact value supplied and an instruction not to.
#: What the line buys is not the answer but a correctable form, because it
#: makes the model write an equation that verification can then act on.
#: See docs/design-rationale.md#model-recomputes
A29A_A20 = replace(A28, name="A29a+A20", aggregate_line=True, substitute_verified=True)

# --- Aggregate line plus aggregate substitution -----------------------------
#: Reaches a case the equation-based substitution cannot: that one parses an
#: equation out of the answer, while this one draws on the `amount` field of
#: the evidence, so an answer written without an equation is still covered.
#: See docs/design-rationale.md#substitution-scope
A29S = replace(A28, name="A29s", aggregate_line=True, substitute_aggregate=True)

# --- Both substitutions together, whose sources differ ----------------------
A29S_A20 = replace(A28, name="A29s+A20", aggregate_line=True,
                   substitute_aggregate=True, substitute_verified=True)

# --- Every arithmetic and retrieval candidate together ----------------------
#: One question needs all three: the reverse chain recovers its golden
#: records, the aggregate sums the per-chain differences, and substitution
#: replaces the model's transcription error.
A29S_A20_A33 = replace(A28, name="A29s+A20+A33", aggregate_line=True,
                       substitute_aggregate=True, substitute_verified=True,
                       reverse_chain=True)

# --- Revision amount series as one line, off the ladder ---------------------
#: Stacked on the adopted base rather than on the arithmetic arms, because the
#: target question carries no arithmetic operation for them to act on, so this
#: base gives a clean individual delta.
A29B = replace(A28, name="A29b", revision_series=True)

# --- Revision reason and summary loaded at runtime, off the ladder ----------
#: The arm with the widest blast radius of this generation: it touches the
#: evidence of far more questions than it targets, so any regression rejects
#: it.
#: See docs/design-rationale.md#arm-catalog
A30 = replace(A28, name="A30", correction_fields=True)

# --- Counterparty distribution line, off the ladder -------------------------
A29C = replace(A28, name="A29c", partner_profile=True)


# --- Every adopted component combined, candidate operational default --------
#: The individual deltas are all secured before the combination is measured.
#: The flags target different questions, so no interaction is expected, but
#: expecting none and measuring none are different things.
A34 = replace(A28, name="A34",
              speculation_gate=True,      # speculation refusal
              identifier_gate=True,       # identifier refusal
              reverse_chain=True,         # reverse chain seeding
              aggregate_line=True,        # deterministic aggregate
              substitute_aggregate=True,  # aggregate substitution
              substitute_verified=True,   # equation substitution
              revision_series=True,       # revision amount series
              correction_fields=True)     # revision reason and summary

# --- Parenthesised unit conversion fix, off the ladder ----------------------
#: Stacked on the combined arm rather than the base, because one of its four
#: targets is a mismatch the aggregate substitution creates, which does not
#: reproduce on the base.
#: See docs/design-rationale.md#unit-conversion
A35 = replace(A34, name="A35", fix_unit_parens=True)

# --- Exempt an orderer from the missing-company check, off the ladder -------
# See docs/design-rationale.md#verifier-disposition
A36 = replace(A34, name="A36", counterparty_exempt=True)

# --- The two accuracy fixes together, candidate default ---------------------
A37 = replace(A34, name="A37", fix_unit_parens=True, counterparty_exempt=True)

# --- Self-consistency correction, off the ladder ----------------------------
#: The first instrument built for this axis.
#: See docs/design-rationale.md#self-contradiction
A38 = replace(A37, name="A38", self_consistency_fix=True)

#: Enumeration completeness.
#: See docs/design-rationale.md#roster-position
A39 = replace(A38, name="A39", roster_line=True)

#: Rounding restoration.
#: See docs/design-rationale.md#unit-conversion
A40 = replace(A39, name="A40", expand_rounded=True)

#: Missing result value.
#: See docs/design-rationale.md#state-total
A41 = replace(A40, name="A41", state_total=True)

#: Separating same-day filings.
#: See docs/design-rationale.md#date-disambiguation
A42 = replace(A41, name="A42", date_disambiguation=True)

# --- Every candidate together: arithmetic, series and retrieval -------------
A29_ALL = replace(A28, name="A29all", aggregate_line=True, substitute_aggregate=True,
                  substitute_verified=True, reverse_chain=True, revision_series=True)

# --- Multi-hub binding, off the ladder --------------------------------------
#: One flag apart from the top arm, widening the counterparty lane from one
#: hub to at most three.
#:
#: This arm raises evidence recovery rather than accuracy, and the prediction
#: recorded before it ran was zero flips with two more golden records
#: recovered. The two denominators have to be reported separately: merged into
#: one accuracy table, "a paid arm did not raise the score" and "it recovered
#: more evidence" cancel out in the same cell.
#: See docs/design-rationale.md#multi-hub-binding
A22 = replace(A8, name="A22", multi_hub=True)

# --- Self-consistency, off the ladder ---------------------------------------
#: The only arm that breaks the fixed-temperature control, so it is measured
#: alone and never combined with another. The nearest neighbour, regeneration
#: with the failure reasons, stays at temperature zero and is therefore a
#: different mechanism rather than a variant of this one.
#:
#: The sample count and temperature are the defaults of the Self-Consistency
#: paper. They have never been measured on this corpus, so those values are
#: themselves the object of measurement, and the verdict is the deliverable
#: regardless of adoption.
#: See docs/design-rationale.md#self-consistency-arm
A10 = replace(A8, name="A10", sc_k=3, temperature=0.7)

# --- Self-consistency narrowed to the paper's stated scope, off the ladder --
#: v7, self-consistency inside the range its paper states. See the
#: `sc_figures_only` field for why the scope gate exists.
V7SC = replace(A42, name="V7-1", sc_k=3, temperature=0.5, sc_figures_only=True)

# --- Regeneration adopted on a strict subset of failure kinds, off the ladder
#: v7, keeps a regenerated answer whose failing kinds are a strict subset of
#: the original's instead of requiring a clean regeneration.
V7REGEN = replace(A42, name="V7-2", regenerate_partial=True)

# --- Deterministic comparison line, off the ladder ---------------------------
#: v7, states the two amounts a comparison question names, their difference
#: and which side is larger, as one deterministic evidence line.
V7CMP = replace(A42, name="V7-3", compare_line=True)

# --- Deterministic unit-scale line, off the ladder ---------------------------
#: v7, prepends the scale a periodic table declares, with its multiplier, to
#: the evidence.
V7UNIT = replace(A42, name="V7-4", unit_line=True)

# --- Supply-region field paired with the relevance allocation, off the ladder
#: v7, the supply-region field with the relevance allocation it pairs with.
#: Measured only against its own index, never mixed with the other arms.
V7REGION = replace(A42, name="V7-5", counterparty_relevance=True)

# --- v7 promotion: the unit-scale line alone, new operational default -------
#: Promotes `V7-4` by itself. `V7-2` and `V7-3` also passed the adoption rule,
#: but the combination of adopted arms was never measured, so only the one
#: flag with its own secured delta moves into the default.
#: See docs/design-rationale.md#operational-default-history
A43 = replace(A42, name="A43", unit_line=True)

# --- Amount-coverage gap, off the ladder -------------------------------------
#: One flag apart from the operational default. Catches an answer that states
#: no monetary figure at all while the evidence carries several, narrowed to a
#: set-reporting question and an answer that does not read as a refusal.
#: See docs/design-rationale.md#amount-coverage
A44 = replace(A43, name="A44", amount_coverage_gate=True)

# --- Counterparty universe/non-universe branch line, off the ladder ---------
#: One flag apart from the operational default. States which counterparties in
#: an enumerated set sit inside the 70-company coverage universe and which sit
#: outside it, so a question asking how the set divides into groups has that
#: split named instead of left for the model to invent from the wrong axis.
#: See docs/design-rationale.md#counterparty-branch
A45 = replace(A43, name="A45", counterparty_branch=True)

# --- v8: the two off-ladder checks combined ---------------------------------
#: `A44` and `A45` measured as one configuration under the ad-hoc labels
#: `A46` and `A47` (see `scratch/combo_run.py`, which sets both flags from one
#: name argument). Those two runs are two draws of this arm, not two arms:
#: 109/112 each with different wrong questions. Registered here under the
#: earlier of the two labels so the stored artifacts keep their meaning.
#: `A47` stays unregistered for the same reason.
A46 = replace(A43, name="A46", amount_coverage_gate=True, counterparty_branch=True)

# --- v8 single-flag arms, each one step from A46 ----------------------------
#: The project axis on the aggregation set, together with the partner-quota
#: relevance allocation it depends on. Needs the index built after the
#: supply-region render. `project_scope` alone is not built or measured as an
#: arm: without `counterparty_relevance` it turns a silence into a
#: confidently wrong total (see `FLAG_DEPENDENCIES["counterparty_relevance"]`
#: for the Q-107 numbers), so `__post_init__` refuses that combination and
#: the two are one step together instead of two.
V8PROJ = replace(A46, name="V8-1", counterparty_relevance=True, project_scope=True)

#: The holding-ratio series line in the evidence, with no answer-side repair.
#: Paired with `V8-3` below: the delta between them is the share of the line
#: the model failed to copy.
V8HOLD = replace(A46, name="V8-2", holding_series=True)

#: The same line plus the repair. One flag from `V8-2`, so the delta reads as
#: the repair's contribution alone rather than as the line's.
V8REPAIR = replace(V8HOLD, name="V8-3", gap_repair=True)

#: The out-of-corpus referral strip.
V8REF = replace(A46, name="V8-4", external_referral_strip=True)

# --- v8 adoption candidate ---------------------------------------------------
#: Everything above at once. Measured against `A43` re-scored under the strict
#: judge, since the judge change breaks comparison with every v1-v7 number.
A48 = replace(A46, name="A48", counterparty_relevance=True, project_scope=True,
              holding_series=True, gap_repair=True, external_referral_strip=True)

# --- v8: the two safety mechanisms from `df5e1821`, measured next ----------
#: One flag apart from `A48`: refuses a question that asserts a conclusion
#: the filings do not confirm before generation ever runs
#: (`router._asks_false_premise`). 0 false positives measured across the
#: full 112-question evaluation set; catches exactly `S-007` and `S-010`.
A49 = replace(A48, name="A49", false_premise_gate=True)
#: One flag apart from `A49`, not from `A48` — the two are meant to be read
#: together. `A49` and `A50` differ by exactly `refusal_compaction`, on
#: purpose, so the measurement run right after this registration reads the
#: compaction's own contribution in isolation from the gate's.
#:
#: With the gate on, `S-007` and `S-010` are both refused before generation
#: ever runs, so neither one reaches `safety.compact_after_refusal` under
#: either arm — the compaction has nothing left to fix for the two questions
#: it was built for. Any delta between `A49` and `A50` therefore has to come
#: from the other ten safety questions (`S-001`-`S-006`, `S-008`, `S-009`,
#: `S-011`, `S-012`) and from the `full`/`valuechain` cohorts, not from the
#: two this campaign targets.
A50 = replace(A49, name="A50", refusal_compaction=True)

# --- W2 · P3 : 답변이 쓴 근거를 다 인용한다 --------------------------------
#: `A49` 에 `cite_used` 하나만 켠 팔. 계획서 `docs/superpowers/plans/
#: 2026-08-21-eight-defects.md` 의 P3.
#:
#: 겨냥: 골든이 접수번호를 요점으로 요구하는데 답변이 안 적는 9문항 10요점, 그리고
#: 근거에 실린 골든 다섯을 두고 골든 아닌 문서 셋을 가리킨 `Q-110`.
A51 = replace(A49, name="A51", cite_used=True)

# --- W2 · P2 : 대체본이 있으면 최신본을 근거로 끌어온다 — 🔴 측정 후 기각 --------
#: `A51` 위에 `latest_pin` 을 얹은 팔. 계획서의 P2.
#:
#: 🔴 **기각(2026-08-21).** 로컬 1회 108/112 로 `A51` 의 110 에서 **2 퇴행**했다.
#: `Q-116` 의 근거 표시는 닫혔는데 `Q-111`·`Q-112` 가 오답이 됐다.
#:
#: 두 방식으로 실패한다. `Q-112` 는 블록이 7 -> 10(상한)이 되면서 골든
#: `20240415800078` 이 **밀려났다** — 2/2 였던 골든 적재가 1/2 이 됐다. `Q-111` 은 골든
#: 3/3 이 그대로인데도 깨졌으니 근거 손실이 아니라 덧붙인 문서가 서술을 흐린 것이다.
#:
#: 세울 때 "덧붙이는 변경이라 아무 근거도 빼지 않는다" 고 적었다. **틀렸다.**
#: `MAX_EVIDENCE` 가 있는 창에 더하는 것은 더하는 것이 아니라 **밀어내는 것**이다.
#: 상한이 있는 자원에 대해 additive 라고 말할 수 없다.
A52 = replace(A51, name="A52", latest_pin=True)

# --- W3 · P4 : 분기보고서 표지에 분기를 적는다 --------------------------------
#: `A51` 위에 `period_label` 을 얹은 팔. 계획서의 P4.
#:
#: 🔴 `A52`(`latest_pin`) 위가 아니라 `A51` 위다. `A52` 가 측정 후 기각됐으므로 그 위에
#: 쌓으면 기각된 스위치의 퇴행을 물려받는다.
A53 = replace(A51, name="A53", period_label=True)

# --- W4 · P5 : 스텁과 본문의 범주 차이를 결정적으로 적는다 ---------------------
#: `A53` 위에 `contrast_line` 을 얹은 팔. 계획서의 P5.
A54 = replace(A53, name="A54", contrast_line=True)

# --- 곁따라 찾은 것 : 검증 꼬리가 자기를 반박한다 -----------------------------
#: `A51` 위에 `citation_note_fix` 하나. P3 을 조사하다 나온 결함이라 계획서에 없다.
A55 = replace(A51, name="A55", citation_note_fix=True)

# --- 2026-08-21 캠페인의 채택본 ---------------------------------------------
#: `A49` 에 이번 캠페인이 채택한 넷을 얹은 팔. 운영 기본값이다.
#:
#:     cite_used         답변이 쓴 근거를 다 인용한다 (P3)
#:     period_label      분기보고서 표지에 분기를 적는다 (P4)
#:     contrast_line     스텁과 본문의 범주 차이를 결정적으로 적는다 (P5)
#:     citation_note_fix 검증 꼬리가 자기를 반박하지 않게 한다
#:
#: `period_label` 과 `contrast_line` 은 **따로 채택할 수 없다.** `A53`(표지만)은 `Q-105` 를
#: 못 고쳤고 `A54`(둘 다)가 고쳤다. `contrast_line` 이 문서 종류별로 분량을 접으므로 표지가
#: 두 분기를 가르지 않으면 구분이 사라진다.
#:
#: 측정 — `A54` 로컬 3회 **110 · 111 · 110**. `Q-105` 는 3회 모두 정답이고 3회 모두 골든이
#: 지목한 3분기(20251114002201)를 인용한다. 남은 오답은 `Q-110`(오픈이슈 2) 하나이고,
#: `Q-094` 는 근거가 `A49` 와 글자 그대로 같은데 회차마다 뒤집히는 뽑기다.
#:
#: `citation_note_fix` 는 측정값을 바꿀 수 없다 — 채점기와 판정기가 둘 다
#: `strip_verify_tail` 을 거친 답변을 받는다. 저장 답변 726개로 전수 확인했으므로 `A54` 의
#: 수가 곧 이 팔의 수다.
#:
#: 🔴 **기각한 것도 함께 적는다.** `latest_pin`(P2)은 퇴행 2건으로 기각했다 — 상한이 있는
#: 근거 창에 더하는 것은 밀어내는 것이었다. `docs/superpowers/plans/2026-08-21-eight-defects.md`
A56 = replace(A54, name="A56", citation_note_fix=True)

# --- W2 · P-C : 거절 판정에 외부 신호를 곱한다 --------------------------------
#: `A56` 위에 `refusal_needs_gap` 하나. 계획서
#: `docs/superpowers/plans/2026-08-21-paper-grounded-repairs.md` 의 P-C.
#:
#: 근거는 Self-Consistency 의 calibration 부재 — 모델의 정규화 조건확률이 정답/오답 간
#: 거의 차이가 없으므로 정보한계 판정을 답변 문면에 맡기면 안 되고 외부 신호를 써야 한다
#: (`paper/base/base.md` §3).
#:
#: `Q-053` 은 질문이 `'V. 회계감사인의 감사의견 등 > 2. 내부통제에 관한 사항'` 을 글자
#: 그대로 지목하고 그 섹션이 근거 첫 블록에 실려 있다. 답변은 그 섹션의 내용(분기보고서에는
#: 기재하지 않는다)을 정확히 서술하는데 그 문장에 `찾을 수 없으며` 가 들어가 거절로
#: 판정됐고, 그래서 접수번호가 하나도 안 붙었다.
#:
#: 🔴 어구 목록을 넓히는 쪽은 v4 에서 이미 측정 후 기각했다 — 모델이 거절하는 낱말은
#: 끝이 없다. 목록은 한 글자도 안 건드린다.
#:
#: 세우기 **전에** 쟀다 (`scratch/w1_risk_sizing.py` · 저장 답변 336개):
#:
#:     지금 거절로 판정되는 답변          53개 · 18문항
#:     새 술어가 뒤집는 것               3개 · 1문항 (Q-053, 3회 모두)
#:     안전성 10문항 · 정보한계 6문항      그대로 거절로 남는다
A57 = replace(A56, name="A57", refusal_needs_gap=True)

# --- W3 · P-B : 정정 질의에서 원공시를 짝으로 붙인다 --------------------------
#: `A56` 위에 `cite_chain_pair` 하나. 계획서의 P-B. 🔴 `A57` 위가 아니다 — 델타를 섞지 않는다.
#:
#: 근거는 Relink 의 **query-driven** 성질이다(`paper/idea/idea.md` §3.8). 논문 정리가
#: `원본 vs [기재정정]` 을 distractor 예로 명시하고, ablation 최악이 query-driven ranker
#: 제거(−19.4%)다. 랭커는 학습할 수 없으니 성질만 가져온다 — 질의가 정정을 물을 때만 돈다.
#:
#: 🔴 **계획서보다 사정거리가 짧다.** 겨냥한 다섯 중 셋(`Q-054`·`Q-068`·`Q-082`)은
#: 원공시가 근거에 아예 없어 인용으로 못 고친다. 회수 문제이고 이 팔 밖이다.
#: `Q-094` 는 정정본의 체인 링크가 비어 있다. 닫는 것은 `Q-093` 이고 곁따라 `Q-035` 다.
#:
#: 세우기 **전에** 규칙 셋을 재고 가장 좁은 것을 골랐다
#: (`scratch/simulate_a58_rules.py` · 저장 답변 336개):
#:
#:     유일성 요구 통째 해제      10문항 · 골든 7 : 비골든 9   (44%)  기각
#:     인용본의 형제 전부        15문항 · 골든 16 : 비골든 9   (64%)  기각
#:     최신본의 형제 하나만      14문항 · 골든 12 : 비골든 4   (75%)  채택
A58 = replace(A56, name="A58", cite_chain_pair=True)

# --- W4 · P-A① : 결정적 집계 줄이 가산 항목을 밝힌다 --------------------------
#: `A56` 위에 `aggregate_items` 하나. 계획서의 P-A 앞쪽. `A57`·`A58` 위가 아니다.
#:
#: 근거는 Graph of Thoughts — CoT 는 사슬, ToT 는 트리이며 **둘 다 여러 갈래를 합치는
#: 연산이 없다**(`paper/idea/idea.md` §3.1). 우리는 병합을 이미 결정적으로 했는데 결과만
#: 보여 주고 무엇을 병합했는지는 안 보여 준다.
#:
#: `Q-115` 실측(서버 3회 전부 같은 모양): 답변이 총액 1,759,596,802,000 을 결정적 줄에서
#: 그대로 베끼고 가산 항목은 자기가 넷으로 다시 열거했다. 줄이 피가수 둘의 **신원**을
#: 안 실었기 때문이다. 재생성은 3회 다 돌고 3회 다 버려졌다(실패 종류가 그대로였다).
#:
#: 🔴 **근거 문면이 바뀌는 첫 팔이다.** 생성 프롬프트는 한 글자도 안 바뀌지만 근거가
#: 바뀌면 프롬프트 바이트가 바뀌어 **요청 캐시가 깨진다** — 이 팔부터 측정이 유료다.
#: W1 실측으로 `[결정적 집계]` 는 15문항에 실린다(`scratch/audit/w1_risk_A56.json`).
A59 = replace(A56, name="A59", aggregate_items=True)

# --- W4b · 명세를 놓아 주는 것으로는 안 됐다 — 결정적으로 갈아 끼운다 ----------
#: `A59` 위에 `sum_operand_fix`. 계획서에 없던 스위치다 — `A59` 3회 측정이 세웠다.
#:
#: `A59` 는 게이트 조건 1 을 못 넘었다. 명세를 절반만 따른다 — ERP 항목은 접수번호까지
#: 그대로 옮기고 hCloud 는 떨어뜨렸는데, 근거에 `완성차 해상운송계약` 이라는 **같은
#: 계약명이 여러 건** 실려 있어 명세가 접수번호로 지목한 것 대신 다른 것을 골랐다.
#: 3회 모두 그렇다(`scratch/audit/gate_a59.json`).
#:
#: **근거에 정답을 놓아 주는 것으로는 안 된다.** SWE-Lancer 의 판단 45% vs 생성 26%,
#: QA.pdf 의 "정답 근거가 있는데도 자신 있게 틀리는 것" 이 같은 자리를 가리킨다.
#:
#: 🔴 **수리 방향이 정해진다.** 총액은 우리가 계산해 명세에 실은 값이고 답변이 그것을
#: 베꼈으므로 맞다. 틀린 것은 피가수이고 옳은 피가수 집합을 **명세가 이미 안다.** 계획서
#: §4 P-D 가 "수리 방향이 모호하다" 며 보류한 것이 이 측정으로 풀렸다.
#:
#: 답변 본문을 재작성하는 첫 스위치다. 발화 조건을 세 겹으로 좁혔다 — 명세가 있고,
#: 답변이 우리 총액을 적었고, 열거한 금액 집합이 명세와 다를 때만.
A61 = replace(A59, name="A61", sum_operand_fix=True)

# --- W5 · P-A② : 결정적 조회 줄이 항목마다 출처를 나른다 ----------------------
#: `A56` 위에 `roster_items` 하나. 계획서의 P-A 뒤쪽. `A59`·`A61` 위가 아니다.
#:
#: `Q-110` 의 줄은 회사명 6곳만 싣는다. 그래서 답변이 항목마다 근거를 못 달고(오픈이슈 v2
#: §4.1) 항목별 금액도 못 적는다. `cite_used` 가 이 문항에 못 닿는 이유도 같다 — 답변이
#: 금액을 하나도 안 쓰니 붙일 근거를 고를 수 없다.
#:
#: 🔴 **접수번호를 근거에 실린 공시에만 적는다.** `error_analysis._blocks` 가 `^## ` 로
#: 쪼개므로 첫 머리글 위의 모든 것이 한 블록으로 세어지고, `evidence_hit` 은 거기 있는
#: 접수번호를 실린 골든으로 읽는다. 근거에 없는 회사의 접수번호를 적으면 **아무것도 싣지
#: 않고 우리 근거 완전성만 올린다** — `Q-110` 의 두산퓨얼셀이 정확히 그 경우다.
#: 실린 것만 적으면 부풀리기가 원리적으로 불가능해진다.
A60 = replace(A56, name="A60", roster_items=True)

# --- W6 · 2026-08-21 논문 캠페인의 채택본 ------------------------------------
#: `A56` 에 이번 캠페인이 채택한 다섯을 얹은 팔.
#:
#:     refusal_needs_gap  거절 판정에 외부 신호를 곱한다 (P-C · Q-053)
#:     cite_chain_pair    정정 질의에서 원공시를 짝으로 붙인다 (P-B · Q-093 Q-035)
#:     aggregate_items    결정적 집계 줄이 가산 항목을 밝힌다 (P-A① )
#:     sum_operand_fix    답변의 열거를 명세로 갈아 끼운다 (Q-107 Q-115)
#:     roster_items       결정적 조회 줄이 항목마다 출처를 나른다 (P-A② · Q-110)
#:
#: 🔴 `aggregate_items` 와 `sum_operand_fix` 는 **따로 채택할 수 없다.** `A59`(명세만)는
#: 게이트 조건 1 을 못 넘었고 — 모델이 명세를 절반만 따랐다 — `A61`(둘 다)이 넘었다.
#: `period_label`+`contrast_line` 과 같은 짝 관계다.
#:
#: 측정 — 팔별 3회는 각각의 게이트 파일에 있다.
#:
#:     A57  Q-053 표기 0/3 -> 3/3 · 저장 답변 336개 중 333개 글자 동일
#:     A58  접수번호 어긋남 7 -> 5 (Q-035 Q-093) · 퇴행 0 · 골든 12 : 비골든 4
#:     A61  Q-107 Q-115 열거 3/3 수리 · equation 3회 -> 0회 · 퇴행 0
#:     A60  Q-110 정답 0/3 -> 3/3 · 근거 표시 0/3 -> 3/3 · 근거 적재 불변
A62 = replace(A56, name="A62", refusal_needs_gap=True, cite_chain_pair=True,
              aggregate_items=True, sum_operand_fix=True, roster_items=True)

# --- W-A : 대체본에 폐기 표시를 각인한다 --------------------------------------
#: `A56` 위에 `stale_note` 하나. 계획서
#: `docs/superpowers/plans/2026-08-21-raw-preprocessing.md` 의 과제 1.
#: `A57`~`A62` 위가 아니다 — 델타를 섞지 않는다.
#:
#: `Q-116` 실측(서버 3회 전부): 답변이 폐기된 581,790,000,000 을 말하고 최신값
#: 583,874,002,800 을 한 번도 안 쓴다. 대체본 블록에 폐기 신호가 문면에 하나도 없기
#: 때문이다 — `related` 는 레코드에 있고(양방향) `to_markdown` 이 안 쓴다.
#:
#: 🔴 `latest_pin`(같은 진단, 창을 넓히는 처방)은 퇴행 2건으로 기각됐다. 이 팔은 창을
#: 안 건드리고 근거 슬롯을 하나도 더 안 쓴다 — 블록 안의 줄 하나다. 그리고 순위와
#: 무관하게 **모든 대체본에** 붙는다.
#:
#: 🔴 각인은 `nary`·`correction_fields` 분기를 **대체하지 않고 겹친다.** 계획서 단계 4 는
#: 맨 앞에서 바로 돌려주게 적었는데 `A56` 이 `correction_fields` 를 켜 두고 있어 그러면
#: 이 팔이 그 분기를 조용히 잃는다. `_group_records_stale(group, kind)` 가 바탕을 키에
#: 넣어 그것을 막는다.
#: 측정 — `A63` 영향 34문항 3회 (`scratch/audit/gate_a63.json` · 사정거리는
#: `scratch/a63_affected.py` 가 냈다: 각인 대상 550건이 37문항 근거에 실린다):
#:
#:     게이트 1  Q-116 이 폐기값 581,790,000,000 을 **3회 모두 안 쓴다**
#:               (A56 은 3회 모두 썼다) · 최신값 583,874,002,800 을 3회 모두 쓴다
#:     게이트 2  퇴행 0 — 34문항 3회
#:     게이트 3  근거 완전성 82/90 불변 — 부풀림 0. 각인 줄에 14자리가 없으므로
#:               `error_analysis._blocks` 회계에 안 들어간다 (전수 단정으로 못 박았다)
#:
#: 🔴 근거 표시 축은 안 움직인다. 답변이 최신 금액을 말하지만 최신본 접수번호를 인용하진
#: 않는다 — 계획서 §5.1 이 "적지 않는다" 를 기본값으로 고르며 예측한 그대로다.
#:
#: 🔴 계획서 §5.2 가 지목한 거짓 귀속이 실제로 났고 같이 고쳤다. 각인 줄의 최신 금액이
#: `cite_used` 의 유일성 규칙에 잡혀 **대체본이 그 금액의 출처로 인용됐다**(r1 실측).
#: `_body_only` 가 메타 줄을 인용 선택에서 뺀다. 저장 근거 1,380블록 전수에서 숫자 집합이
#: 안 바뀌므로 `A62` 이전 측정은 유효하다.
A63 = replace(A56, name="A63", stale_note=True)

# --- W6b · 2026-08-21 논문 + 원자료 캠페인의 채택본 ---------------------------
#: `A62` 위에 `stale_note` 하나. 계획서 과제 4 단계 5.
#:
#: 🔴 계획서는 `A65 = A62 + stale_note + section_chain_note` 였고 "하나라도 게이트를 못
#: 넘으면 A65 를 세우지 않는다" 고 적었다. `section_chain_note`(과제 3)는 **구현 전 측정으로
#: 기각**했다 — 원공시 접수번호를 블록에 적으면 겨냥한 넷 중 셋에서 근거 완전성이
#: 167/168 -> 170/168 로 오른다(본문은 하나도 안 실린다). 세우지 않은 스위치는 퇴행을
#: 물려줄 수 없으므로, 게이트를 통과한 `stale_note` 하나만 얹는다.
#:
#:     refusal_needs_gap · cite_chain_pair · aggregate_items · sum_operand_fix
#:     · roster_items      (A62 에서 채택)
#:     stale_note          (A63 게이트 통과)
A65 = replace(A62, name="A65", stale_note=True)

# --- 세 실험 비교용 (2026-08-21, 사용자 요청) ---------------------------------
#: 실험 1 — 현 베이스라인(`A56`) + 전처리 고도화. `periodic_chains.jsonl` 의 유일한
#: 소비자다. 🔴 앞서 구현 전 측정으로 기각한 스위치인데, 「전처리가 실제로 이득인가」를
#: 재려면 소비자가 있어야 하므로 세운다. **부풀림을 통제해 읽는다** — 근거 완전성을
#: 날값과 포인터 줄 제외값으로 둘 다 낸다.
A64 = replace(A56, name="A64", section_chain_note=True)

#: 실험 3 — `A65` + 전처리 고도화.
A66 = replace(A65, name="A66", section_chain_note=True)

# --- P2 · 계약상대별 건수를 결정적 줄로 낸다 (2026-08-21, 브랜치 keypoint-gaps) ---
#: `A65` 위에 `partner_profile` 하나.
#:
#: 🔴 **이 플래그는 v5 에서 이미 기각됐다** — `A29c`, **Δ 0**, 겨냥은 `Q-107`·`Q-117`
#: 이었고 둘 다 산술이 아니라 검색이 막고 있었다. 그때는 **점수**를 봤다.
#:
#: 지금 겨냥이 다르다. `Q-114` 의 요점1·2 가 요구하는 건수(`47`·`19`)가 **근거에 없다** —
#: `[결정적 조회]` 줄이 회사 이름만 낸다. 그 수를 코퍼스에서 직접 세어 보니
#: 삼성E&A 47 · 삼성중공업 19 로 골든과 정확히 일치하므로 **이 기계가 낼 수 있는 수**다.
#: 그때 없던 계측기(`scratch/keypoint_literal_report.py`)가 지금은 그것을 센다.
#:
#: 🔴 `_counterparty_note` 가 경고를 적어 두었다 — 전역 건수는 정정 반영이 안 됐고
#: **한 번은 답변에 그대로 새어 나갔다.** 그래서 점수 퇴행 0 을 게이트로 둔다.
#:
#: 사정거리 9문항 (`A65` r1 흔적에서 계약상대·열거 hop 이 도는 것):
#: `Q-107` `Q-109` `Q-110` `Q-111` `Q-114` `Q-115` `Q-116` `Q-117` `Q-118`
#: 🔴 **기각 (2026-08-21). 조용한 무동작이었다.**
#:
#: 3회 다 돌렸고 퇴행 0 인데 **겨냥한 수가 답변에 안 들어왔다** — `Q-114` 3회 모두
#: `47`·`19` 가 없다. 근거를 열어 보니 **분포 줄이 아예 없다.**
#:
#: 원인은 배선이다. `partner_profile` 은 `pipeline._enumerate_events`(열거 hop,
#: `strat=enumerate`) 안에서만 불린다. `Q-114` 는 **계약상대 hop**(`strat=counterparty`)을
#: 타므로 그 함수에 들어오지 않는다. 흔적이 그것을 보여 준다 —
#: `hop=6 strat=counterparty via=삼성전자 partners=2`, `strat=enumerate` 없음.
#:
#: 🔴 계약상대 경로에 건수를 옮겨 붙이는 것은 **문서로 기각된 결정을 되돌리는 일**이다.
#: `pipeline._counterparty_note` 가 적는다 — 전역 건수는 정정 반영이 안 됐고 질문 범위
#: 밖이며 **한 번은 답변에 그대로 새어 나갔다.**
#:
#: 그리고 `Q-114` 의 질문은 *"상장사는 어디이며 공정 분담은 어떻게 나뉘는가"* 다. 건수는
#: 골든 요점의 괄호 안 부속이고 질문이 직접 묻지 않는다. 답변은 **범위 안의 것**(두 회사
#: 이름 · 단계별 분담)을 3회 모두 적었다. 정량화만 안 했다.
A67 = replace(A65, name="A67", partner_profile=True)

# --- P1 · 보유비율 추이 줄의 발화 낱말을 넓힌다 (브랜치 keypoint-gaps) ----------
#: `A65` 위에 `holding_ask_wide` 하나.
#:
#: `Q-012` 는 *"지분 보유 상황이 어떻게 **달라졌는지**"* 를 묻는데 `_HOLDING_ASK` 에 그
#: 낱말이 없어 **조용한 무동작**이었다. `period_label` 의 `base_month` 와 같은 모양이다.
#:
#: 🔴 그 튜플은 **느슨해도 되는 쪽**이다 — 독스트링이 적는다: 강한 전제는 레코드 조건
#: (한 보고자의 대량보유 2건 이상)이고 낱말은 그 앞 관문일 뿐이다. `Q-012` 는 레코드
#: 조건을 만족한다((주)두산 → 두산에너빌리티 2건).
#:
#: 데이터는 이미 구조화 필드에 있다 — `shares_prev`·`shares_current` 가 골든 요점1 의
#: 두 수(`196,488,145` → `196,506,795`)를 그대로 담는다. 새 추출도 파싱도 LLM 도 없다.
#:
#: 사정거리 전수 대조(112문항): 지금 이 줄이 도는 것은 `Q-106` 하나이고, 넓히면
#: **`Q-012`·`Q-020` 둘이 는다.** 잃는 문항은 없다.
A68 = replace(A65, name="A68", holding_ask_wide=True)

# --- P3 · 구성 표의 합계를 못 박는다 (브랜치 keypoint-gaps) --------------------
#: `A65` 위에 `composition_total` 하나.
#:
#: `Q-080` 은 *"영업수익의 **구성**을 알려줘"* 이고 요점1~3 이 백분율을 요구한다. 답변은
#: 3회 모두 백분율만 적고 **분모(합계 80,318백만원)를 안 밝힌다.** 백분율은 분모 없이는
#: 뜻이 없다.
#:
#: 🔴 **전처리가 필요 없다.** 처음엔 periodic 레코드를 새로 추출하는 판을 세웠는데, 그
#: 문항들은 `_op` 이 연산을 분류하지 못해(`op=-`) 레코드를 줘도 기계가 안 돈다. 대신
#: `contrast_line` 의 전례를 따른다 — 레코드가 아니라 **근거 문자열을 직접 읽는다.**
#:
#: 🔴 **아무 합계나 못 낸다.** `Q-080` 의 근거에는 합계 행이 11개 있다(대손충당금 · 재고 ·
#: 리스 …). 하나를 골라 틀리면 자신 있게 틀린 수가 된다. 두 겹으로 좁혔다 — 질문이
#: 구성·비중을 묻고, 같은 행에 백분율 100 이 있는 합계 행이 **정확히 하나**일 때만.
#:
#: 112문항 전수 대조: 조건 1 만으로는 2문항(`Q-043` 6행 · `Q-080` 1행), 조건 2 까지 걸면
#: **`Q-080` 하나**다. `Q-043` 은 모호하므로 침묵한다.
A69 = replace(A65, name="A69", composition_total=True)

# --- 브랜치 keypoint-gaps 의 채택본 ------------------------------------------
#: `A65` 에 이 브랜치가 채택한 둘을 얹은 팔.
#:
#:     holding_ask_wide   보유비율 추이 줄의 발화 낱말에 `달라` (A68 · Q-012)
#:     composition_total  구성 표의 합계를 못 박는다 (A69 · Q-080)
#:
#: 🔴 **기각한 것도 함께 적는다.** `partner_profile`(`A67`)은 조용한 무동작이었다 —
#: 열거 hop 안에서만 불리는데 겨냥한 `Q-114` 는 계약상대 hop 을 탄다. 그 경로에 건수를
#: 옮기는 것은 `pipeline._counterparty_note` 가 문서로 기각한 결정을 되돌리는 일이다.
#: v5 의 `A29c`(Δ 0)에 이어 **두 번째 기각**이고 이유가 다르다.
#:
#: 영향 문항: `Q-012` · `Q-020` (A68) · `Q-080` (A69) — 셋. 겹침 없음.
A70 = replace(A65, name="A70", holding_ask_wide=True, composition_total=True)

# --- W1 · 주요계약체결 추이를 한 줄 더 (브랜치 keypoint-gaps) ------------------
#: `A70` 위에 `pledged_series` 하나. 🔴 `A70` 위인 것이 의도다 — 이 줄은 보유비율 추이
#: 줄에 덧붙는 것이므로 그 줄이 `Q-012` 에서 발화해야(`holding_ask_wide`) 뜻이 있다.
#:
#: `Q-012` 요점3 이 요구하는 두 수(`84,282,066` → `92,270,920`)는 `shares_current` 가
#: 아니라 `fields["주요계약체결 주식등의 수"]` 에 있다. 그 필드는 `직전 A주 → 이번 B주`
#: 꼴의 **문자열**이고 골든은 각 보고서의 **이번** 값을 잇는다.
#:
#: `A70` 실측이 이 처방의 근거다 — 요점1 은 줄에 실려 3회 모두 답변에 들어왔고 요점3 의
#: 뒤 수는 근거 본문에만 있어 안 들어왔다. **같은 문항 · 같은 근거에서 갈렸다.**
#:
#: 🔴 침묵 조건: 체인의 양 끝 둘 다 그 필드를 가질 때만. 코퍼스 실측으로 대량보유
#: 1,083건 중 507건만 이 필드를 갖는다. 한쪽만 있으면 「무엇에서 무엇으로」가 성립하지
#: 않는다. 두 끝이 같으면 변동이 없으므로 `추이` 라 적지 않는다.
A71 = replace(A70, name="A71", pledged_series=True)

# --- W2 · 부문별 매출을 검산해 낸다 (브랜치 keypoint-gaps) ---------------------
#: `Q-104` 는 같은 공시 안에 표가 **둘** 있고 부문값이 1 씩 다르다. 답변은 세 판 모두
#: 비중 표(5,198,501 · 3,814,205)를 읽었고 골든은 매출실적 표(5,198,502 · 3,814,204)를
#: 쓴다. 둘 다 내적으로 맞고 합계는 11,314,459 로 같다 — 원문 자체의 반올림 차다.
#:
#: 그래서 골든을 고칠 수 없고 답변을 틀렸다고 할 수도 없다. 가릴 잣대는 **산술**이다.
#: 매출실적 표는 `수출 + 내수 = 합계` 가 세 부문 모두 맞고 비중 표는 그런 검산 대상이
#: 없다. 검산이 맞는 삼중항만 담으므로 이 줄이 나왔다는 것 자체가 값이 표 안에서
#: 더해진다는 뜻이다.
#:
#: 🔴 총계 행은 뺀다. `Q-104` 의 합계 요점은 질문 범위 밖이라 이미 걷어냈으므로 여기서
#: 다시 실으면 걷어낸 것을 되돌리는 셈이다.
#:
#: 사정거리 실측: 표 꼴만으로 3문항(`Q-049`·`Q-051`·`Q-104`), 질문 필터까지 걸면 하나다.
A72 = replace(A71, name="A72", segment_sales=True)

# --- W3 · 계약상대가 같은 두 공시를 잇고 차를 낸다 (브랜치 keypoint-gaps) --------
#: `Q-112` 는 *"두 공시가 어떤 관계인지 설명하라"* 이고 골든 요점3 이 `약 4,111억원 더
#: 크다` 를 요구한다. 답변은 세 판 모두 두 금액을 나란히 적고 차를 계산하지 않는다.
#: 이 문항에는 결정적 줄이 **하나도** 안 붙었다 — `[결정적 집계]` 가 통째로 침묵한 자리다.
#:
#: `line`(`_op`) 의 차 연산이 안 걸린 것은 질문이 「차이는 얼마인가」가 아니라 「어떤
#: 관계인가」이기 때문이다. 관계를 묻는데 차액이 답의 일부인 꼴은 이 유형에 고유하다.
#:
#: 두 공시를 잇는 것은 계약상대 표기뿐이고 `'주식회사 유토개발2차'` 와
#: `'주식회사유토개발2차'` 로 공백만 다르다. 골든 요점4 가 그것을 말하므로 줄은 두 표기를
#: 둘 다 적는다 — 답변이 무엇으로 이었는지 말할 수 있어야 한다.
#:
#: 🔴 쌍이 정확히 하나가 아니거나 · 한 상대에 세 건 이상이거나 · 공시 주체가 같거나 ·
#: 금액이 없거나 · 차가 0 이면 침묵한다.
#:
#: 사정거리 실측: 코호트 112문항에서 `Q-112` 하나. 보류셋 `H-011`·`N-014` 는 꼴에는
#: 걸리지만 열거 문항이라 질문 필터에 막힌다.
#:
#: 🔴 **요점3 은 이 팔로 닫히지 않는다.** 차액 표기를 다섯 꼴로 재고 6회까지 늘려 봤으나
#: 답변에 드는 비율이 대략 원 7/12 · 억원 4/12 다(종전 0/3). 같은 꼴의 표본 간 분산이
#: 꼴 사이의 차이보다 커서 더 고를 수도 없었다. 확실히 나아진 것은 요점4(계약상대 표기가
#: 공백만 다르다)와 요점2·3 의 날짜·금액·접수번호이고, 종전에 적던 확정일 대신 공시일을
#: 적는다. 퇴행은 6회 모두 0건이다.
A73 = replace(A72, name="A73", counterparty_pair=True)

# --- W4 · 공시가 스스로 밝힌 원화 환산을 옮긴다 (브랜치 keypoint-gaps) ----------
#: `Q-101` 골든 요점2 는 *"2025년 3월 24일 매매기준율(1,466.9원/달러)로 환산한 8조
#: 5,080억원이 함께 적혀 있다"* 다. 그 문장은 근거 안에 **그대로 있다** — `- 기타 투자판단과
#: 관련한 중요사항:` 필드의 산문 속이다. 답변은 세 판 모두 `$58억` 만 적었다.
#:
#: 🔴 `58억 × 1,466.9` 를 우리가 계산해서 내지 않는다. 공시가 적은 환산액과 우리 계산이
#: 어긋나면(반올림·기준일 차이로 흔히 어긋난다) 우리 값이 공시와 다른 수가 되고, 심사자가
#: 원공시와 맞춰 볼 때 곧바로 드러난다. 그래서 `[결정적 집계]` 가 아니라 `[결정적 조회]` 다.
#:
#: 사정거리 실측: 352건을 훑어 `Q-101` 하나. `Q-067` 에도 `환율` 낱말이 있으나 환산
#: 문장은 아니다.
A74 = replace(A73, name="A74", fx_conversion=True)

# --- W5 · 정정 전후 두 벌을 줄 단위로 견준다 (브랜치 keypoint-gaps) -------------
#: 정정 문항은 근거에 **같은 절이 두 벌** 실린다. 그 차이는 코드로 정확히 구할 수 있는데
#: 답변은 눈으로 훑어 짐작한다. `Q-094` 골든 요점 넷이 이 대조에 통째로 들어 있다.
#:
#:     요점1  항목명에 '(단위: 원/주)' 추가        → 바뀐 줄 짝
#:     요점2  3,130 · 6,680 은 전후가 같다        → 바뀐 줄 안의 수
#:     요점3  63,562,284 · 135,653,693 도 그렇다  → 그 줄이 든 표
#:     요점4  금액은 하나도 바뀌지 않았다          → 금액 다중집합 동일
#:
#: 답변은 세 판 모두 요점1·2 만 맞혔다. 요점3 의 두 수는 바뀐 줄에 없고 바로 아래 줄에
#: 있어 눈으로는 「안 바뀌었다」를 말할 수 없다.
#:
#: 🔴 금액이 바뀐 정정과 표기만 바뀐 정정은 답이 다르다. 앞쪽은 바뀐 줄만, 뒤쪽은 그 줄이
#: 든 표까지 낸다 — 뒤쪽에서는 「안 바뀐 값」이 답이기 때문이다.
#:
#: 🔴 바뀐 줄 짝이 6 을 넘으면 침묵한다. 코퍼스 실측으로 `Q-076` 71줄 · `Q-088` 249줄
#: 짝이 있고 그것을 한 줄에 담으면 근거를 밀어내는 벽이 된다.
A75 = replace(A74, name="A75", revision_diff=True)

# --- W6 · 표지를 출처로 댄 자리를 걷어낸다 (브랜치 keypoint-gaps) ---------------
#: 표지(`[결정적 집계]` 따위)는 우리가 근거 앞에 붙인 **내부 이름표**다. 생성 프롬프트는
#: 그것을 아예 모르므로 모델은 대괄호를 그냥 출처로 읽고 그대로 인용한다.
#:
#:     (근거: 결정적 집계, 2023-07-03)
#:     (근거: 결정적 조회 및 각 회사별 공급계약 체결 보고서들, 기준연월 미확인)
#:     이는 결정적 집계에 명시되어 있습니다.
#:
#: `A65` 서버 3회 실측: 336건 중 **36건**(12문항 × 3회). 12문항 모두 매 회차에 나오므로
#: 흔들림이 아니라 규칙이다. 값은 맞는데 출처가 허깨비인 것이라 채점 축에 안 잡히면서
#: 심사자의 신뢰를 깎는다.
#:
#: 🔴 생성 프롬프트는 고정이므로 프롬프트로는 못 고친다. `drop_citation_warning` 과 같은
#: 자리·같은 방식으로 답변을 뒤에서 고친다.
A76 = replace(A75, name="A76", strip_marker_citation=True)

# --- W7 · 보유비율 추이 줄이 접수번호를 나른다 (브랜치 keypoint-gaps) -----------
#: `Q-106` 은 *"각 보고서를 **근거로** 설명해줘"* 다. 골든 요점1~4 가 걸음마다 접수번호를
#: 짝지어 요구하는데(`20250707000299` · `20260227007254`) **그 번호는 근거 블록에도 없다** —
#: 체인은 `tools.holding_chain` 이 들고 있고 근거로 실리는 것은 일부다. 그러니 이 줄이
#: 내지 않으면 답변이 알 길이 아예 없다. 검색 결함이라 미뤄 뒀던 자리인데 검색이 아니라
#: **줄이 안 실어 준 것**이었다.
#:
#: `roster_line` 이 항목마다 접수번호를 나르는 것과 같은 판단이다(`roster_items`, P-A②).
#:
#: 🔴 **요점3·4 는 이 팔로 닫히지 않는다.** 줄에만 있는 두 번호가 답변에 드는 것은 3회에
#: 1회다(종전 0/3). 지시 줄을 붙여도 그대로였다. 나머지 두 판은 *"각 보고서별 접수번호
#: 참조"* 로 뭉뚱그린다. 그래도 남기는 이유는 **답변이 그 번호를 알 유일한 길**이고 퇴행이
#: 없기 때문이다.
#:
#: 이 팔에서 함께 고친 것: `pledged_series` 의 짝을 체인 전체 양 끝이 아니라 **근거로
#: 실린 두 벌**에서 뽑는다. 첫 판은 `Q-012` 에서 `2023-02-17 40,939,844주 (6.41%)` 를
#: 앞 끝으로 삼아 골든의 짝과 달랐다. `holding_receipts` 를 켜면서 `holding_chain` 이
#: 넘어오는 자리를 다시 읽다가 드러났다.
A77 = replace(A76, name="A77", holding_receipts=True)

# --- Function calling actually enabled, off the ladder ----------------------
#: One flag apart from the top arm, passing the tool specifications to the
#: answer generation call so the model calls tools itself.
#:
#: Measured and rejected. The rejection is not that tools went uncalled: 42% of
#: questions called one. The three reasons are transcription of correct tool
#: results, a majority of calls going to retrieval and diluting evidence
#: already present, and the loss of the length cap that the API ties to the
#: tool list. The arm is kept because a rejection measured is stronger than a
#: rejection assumed.
#: See docs/design-rationale.md#function-calling-arm
A24 = replace(A8, name="A24", function_calling=True)

# --- Tool results substituted rather than transcribed -----------------------
#: The rejected arm is kept rather than replaced: it is the evidence for that
#: verdict. This one is a retry that targets its three rejection reasons one at
#: a time.
#:
#: Its own headroom is thin, because two of the three target questions were
#: already fixed by a zero-cost substitution arm and the third is a
#: transcription rather than a calculation. What is worth measuring is the
#: combination below.
#: See docs/design-rationale.md#tool-result-substitution
A24B = replace(A8, name="A24b", function_calling=True, calc_tools_only=True,
               calc_queries_only=True, substitute_tool_results=True)

# --- Both substitutions combined --------------------------------------------
#: Two substitutions with different sources: one uses the value a tool
#: returned, the other the value verification recomputed. Not exclusive. The
#: tool substitution runs first, since it is the earlier source, and
#: verification then sees the already-substituted answer.
#:
#: One flag apart from the arm above, so that the difference reads as the
#: contribution of the verification substitution alone.
A24BS = replace(A24B, name="A24b+A20", substitute_verified=True)

#: Arms are measured from the complete configuration backwards, so that a run
#: cut short by budget still knows the newest performance. Arms measured
#: against each other are kept adjacent, an individual arm precedes its
#: combinations, and the diagnostic and embedding arms come last.
#: See docs/design-rationale.md#run-order
RUN_ORDER = ("A8", "L1", "L2", "L3", "L4", "L6", "L7", "L8",
             "A7", "A6", "A5", "A4", "A1b", "A1", "A0",
             "A8-lean", "A20", "A20r", "A25", "A26", "A25+A26", "A27b", "A28",
             "A31", "A32", "A33", "A29a", "A29a+A33", "A29a+A20",
             "A29s", "A29s+A20", "A29s+A20+A33", "A29b", "A29all", "A29c", "A30",
             "A34", "A35", "A36", "A37", "A38", "A39", "A40", "A41", "A42", "A43", "A44",
             "A45", "A46", "V8-1", "V8-2", "V8-3", "V8-4", "A48", "A49", "A50", "A51", "A52", "A53", "A54", "A55", "A56", "A57", "A58", "A59", "A60", "A61", "A62", "A63", "A64", "A65", "A66", "A67", "A68", "A69", "A70", "A71", "A72", "A73", "A74", "A75", "A76", "A77",
             "A21", "A22", "A10", "V7-1", "V7-2", "V7-3", "V7-4", "V7-5", "A18", "A24",
             "A24b", "A24b+A20", "A2F", "A2", "ORACLE")

#: The ladder itself. The embedding arms and the leave-one-outs do not belong
#: here.
LADDER = ("A0", "A1", "A1b", "A4", "A5", "A6", "A7", "A8")

#: Leave-one-out configurations and what each removes. Reports read this
#: mapping verbatim, so a label that states only half of what an arm removes
#: makes the delta be quoted wrongly.
#: See docs/design-rationale.md#followup-edge-split
LOO_REMOVED = {"L1": "CHAIN_HOP 전체 (정정체인 550엣지 + 관련공시 후속공시)",
               "L2": "major·holding 필드", "L3": "발주처 2-hop",
               # This label used to state only half of what the arm removes.
               # It now withholds the reverse follow-up edges and the forward
               # termination edges together, since both come from the same
               # source cell and one switch hides both.
               "L4": "관련공시 REFERS_TO 계열 — 후속공시(역방향) + 해지→원계약"
                     "(순방향) 둘 다 (정정체인은 유지)",
               # The meta axes withhold a filter argument rather than
               # disabling retrieval, and the labels say so: naming only the
               # axis would read as having disabled BM25 too.
               "L6": "search_chunks 의 corp_name 필터 (기업 축 · BM25 는 그대로)",
               "L7": "search_chunks 의 접수일 접두 ym·year·rcept_dt "
                     "(사업연도 base_year 는 유지)",
               "L8": "search_chunks 의 group 인자 (인덱스 군 축 — exchange 로 떨어진다)"}

#: Arms that enable the embedding hybrid. If embeddings are ruled out, these
#: two are the only entries to remove.
#: See docs/design-rationale.md#dense-isolation
DENSE_ARMS = ("A2", "A2F")

#: Arms whose numbers must never be quoted as a score. They use information
#: not reproducible at evaluation time, so they cannot be submission
#: configurations. The runner's summary reads this list and prints a warning
#: beside the table, which is the only thing stopping a reader of the
#: artifacts alone from taking those numbers for a score.
DIAGNOSTIC_ARMS = ("ORACLE",)

ARMS: dict[str, Arm] = {a.name: a for a in
                        (A0, A1, A1B, A4, A5, A6, A7, A8, L1, L2, L3, L4, L6, L7, L8,
                         A2, A2F, ORACLE, A8_LEAN, A20, A20R, A21, A18, A22, A10, A24,
                         A24B, A24BS, A25, A26, A25_A26, A27B, A28, A31, A32, A33,
                         A29A, A29A_A33, A29A_A20, A29S, A29S_A20, A29S_A20_A33,
                         A29B, A29_ALL, A29C, A30, A34, A35, A36, A37, A38, A39, A40, A41, A42,
                         V7SC, V7REGEN, V7CMP, V7UNIT, V7REGION, A43, A44, A45,
                         A46, V8PROJ, V8HOLD, V8REPAIR, V8REF, A48, A49, A50, A51, A52, A53, A54, A55, A56, A57, A58, A59, A60, A61, A62, A63, A64, A65, A66, A67, A68, A69, A70, A71, A72, A73, A74, A75, A76, A77)}

#: Side branches: neither ladder steps, leave-one-outs, nor diagnostics. The
#: list is derived rather than written by hand, because the summary and the
#: gate walk only the three named collections, and an arm caught by none of
#: them costs credits without appearing anywhere in the artifacts. That has
#: happened once. Worse is the cost reconciliation: actual spend is summed
#: recursively from the run records, so the money is counted there but not in
#: the per-arm total, which understates what the cache saved.
SIDE_ARMS: tuple[str, ...] = tuple(
    a for a in RUN_ORDER
    if a not in LADDER and a not in LOO_REMOVED and a not in DIAGNOSTIC_ARMS)

#: Every switch name, derived so that a new flag is picked up automatically.
#: Written by hand, a missed flag would pass the single-variable check for
#: leave-one-out silently.
FLAG_NAMES: tuple[str, ...] = tuple(
    f for f in A8.__dataclass_fields__ if f != "name")

#: Leaving the switches untouched gives the complete configuration. The
#: operational default has to equal the top arm, so that "reset to defaults"
#: and "that arm" are provably the same thing.
#:
#: The operational default is also the submission configuration: the evaluation
#: server names no arm, so this value is the configuration answers are produced
#: under, and changing it requires a redeploy.
#:
#: **2026-08-20, A43 -> A49.** Strict judge (docs/design-rationale.md#strict-judge),
#: 112-question golden set, three local rounds each:
#:
#:     A43   105 . 105 . 105   previous operational default
#:     A48   110 . 110 . 110   v8's seven flags
#:     A49   110 . 110 . 110   A48 + false_premise_gate
#:     A50   110 . 109 . 108   A49 + refusal_compaction
#:
#: (A43 scored 107 . 105 . 107 under the prior, lenient judge -- the strict
#: judge costs about 1.3 points, and A49 still clears that old ceiling.)
#:
#: Wrong-answer histogram across the three rounds:
#:
#:     A43   Q-094x3 Q-105x3 Q-107x3 Q-112x3 Q-117x3 Q-106x2 Q-065x2 Q-110x2
#:     A49   Q-105x3 Q-110x3
#:
#: Eight questions and seventeen wrong-answer instances become two and six.
#: A43 had three questions whose verdict flipped between rounds; A49 has
#: zero flipping questions.
#:
#: `A48` and `A49` scored identically. That is not evidence the gate is
#: inert: in this sample `A48` already passed `S-007` and `S-010` in all
#: three rounds, so there was no headroom left for the gate to move the
#: score. **The gate was adopted on stability grounds, not a score delta --
#: there is no score delta to point to, and a reader looking for one would
#: be looking for something that isn't there.** What moved instead: the two
#: questions' answer-length spread went from 84 and 622 characters to
#: *zero* (the router returns fixed text under the gate, no generation);
#: across 13 measured runs the two had failed twice, and under the gate
#: they structurally cannot; one `A48` round produced "공시 내용에 따르면
#: 삼성전자의 2025년 단일판매공급계약은 정상적으로 이행되고 있습니다." -- the
#: exact assertion the golden forbids -- and was scored correct only
#: because an unrelated later sentence matched the scorer's refusal regex,
#: an answer the gate never lets be generated; and generation costs two
#: fewer LLM calls.
#:
#: `A50` is rejected: with the gate on, `S-007` and `S-010` are both
#: refused before generation ever runs, so `refusal_compaction` -- built
#: for exactly those two questions -- never reaches either one. Its
#: 110/109/108 traces to sampling variance from one more flag moving the
#: prompt-cache key, not to anything the compaction did. The three
#: questions it "cost" relative to `A49` (`Q-022`, `Q-094`, `Q-111`) were
#: inspected and the compaction fired on none of them.
#:
#: **Known cost.** The gate's refusal wording is fixed and generic, so it
#: cannot cite the specific contracts `S-007`'s golden answer asks to have
#: named as confirmable. The gate answers only the first half of that
#: golden (state the blanket claim is unconfirmed) and drops the second
#: (name what is confirmable instead). Scoring does not see the gap; a
#: reader of the answer would.
#:
#: The history of what this default has been, with the measurements behind each
#: change, is recorded separately.
#: See docs/design-rationale.md#operational-default-history
#: 운영 기본값. 2026-08-21 캠페인의 채택본으로 전환했다 (종전 `A49`).
#: 🔴 서버 배포는 별도 승인 사안이다 — 이 상수를 바꾸는 것과 배포는 다른 일이다.
#: 🔴 2026-08-21 `A56` -> `A65`. 세 실험을 로컬 3회로 나란히 재고 골랐다
#: (`scratch/audit/three_way_axes.json`):
#:
#:     A56  베이스라인                 111 · 109 · 110   평균 110.0
#:     A64  베이스라인 + 전처리 고도화    110 · 109 · 110   평균 109.7  퇴행 1  기각
#:     A65  모델 쪽 채택본              111 · 111 · 111   평균 111.0  🟢 채택
#:     A66  A65 + 전처리 고도화         111 · 110 · 111   평균 110.7  퇴행 2  기각
#:
#: `A65` 가 8축 전부에서 최고이거나 동률이다. 근거 완전성만 넷이 같은데 `Q-110` 의 5/6 은
#: 회수 구멍이라 이번 캠페인의 어느 스위치도 겨냥하지 않았다.
#:
#: 🔴 **배포는 별도 승인 사안이다.** 이 상수를 바꾸는 것과 서버에 올리는 것은 다른 일이다.
_current: Arm = A65


def current() -> Arm:
    return _current


def flags(arm: Arm | str | None = None) -> dict:
    """The arm's flags as a dict, excluding its name. This is what the
    single-variable check for leave-one-out reads."""
    target = current() if arm is None else (ARMS[arm] if isinstance(arm, str) else arm)
    return {f: getattr(target, f) for f in FLAG_NAMES}


def enabled(flag: str, arm: Arm | str | None = None) -> bool:
    """Whether a flag is on. An unknown flag name raises rather than
    evaluating false, so that a typo cannot be reported as a component having
    no effect."""
    if flag not in FLAG_NAMES:
        raise KeyError(f"그런 플래그가 없습니다: {flag!r} (있는 것: {FLAG_NAMES})")
    target = current() if arm is None else (ARMS[arm] if isinstance(arm, str) else arm)
    return bool(getattr(target, flag))


def set_arm(arm: Arm | str) -> Arm:
    """Set the global arm and return the previous one. For the evaluation
    runner only."""
    global _current
    previous = _current
    _current = ARMS[arm] if isinstance(arm, str) else arm
    return previous


@contextmanager
def use(arm: Arm | str):
    """Scoped arm switch: the previous arm is always restored on exit."""
    previous = set_arm(arm)
    try:
        yield current()
    finally:
        set_arm(previous)


# --- Golden injection for the oracle arm, runner only -----------------------
# The pipeline knows only a question id and nothing of the evaluation set, so
# the golden evidence is injected by the runner. It is module-global for the
# same reason the arm is: what a run was measured with should be readable from
# one place.


class OracleGoldenMissing(RuntimeError):
    """The oracle arm is active but no golden evidence was injected.

    Proceeding with empty evidence makes the pipeline emit the limitation
    notice, the scorer count it wrong, and the result read as generation
    failing even when given the oracle. That is a missing injection hardening
    into a verdict, which is exactly what this module exists to prevent.
    """


#: `{question_id: [{"rcept_no": ..., "section": ...}, ...]}`. None means not
#: injected, while an empty list means this question genuinely has no golden
#: evidence, and the two must stay distinguishable.
_oracle_golden: dict[str, list[dict]] | None = None


def set_oracle_golden(mapping: dict[str, list[dict]] | None) -> dict | None:
    """Inject a cohort's `{question_id: evidence}` mapping and return the
    previous one. For the runner only.

    Never injected for a held-out set: that path returns before reaching this
    function.
    See docs/design-rationale.md#heldout-consumption
    """
    global _oracle_golden
    previous = _oracle_golden
    _oracle_golden = None if mapping is None else {
        str(qid): list(items or []) for qid, items in mapping.items()}
    return previous


def oracle_golden() -> dict[str, list[dict]] | None:
    """The injected mapping as-is, or None before any injection."""
    return _oracle_golden


def oracle_golden_for(question_id: str) -> list[dict]:
    """Golden evidence for one question. Raises when absent rather than
    producing silently empty evidence."""
    if _oracle_golden is None:
        raise OracleGoldenMissing(
            "ORACLE arm 인데 골든이 주입되지 않았습니다 — "
            "`ablation.set_oracle_golden({question_id: evidence})` 를 먼저 부르십시오 "
            f"(문항 {question_id!r}). 빈 근거로 진행하면 '생성 실패' 로 오독됩니다.")
    if question_id not in _oracle_golden:
        raise OracleGoldenMissing(
            f"ORACLE 골든 매핑에 {question_id!r} 이 없습니다 (주입된 문항 "
            f"{len(_oracle_golden)}건). 코호트와 매핑이 어긋났습니다.")
    return _oracle_golden[question_id]


@contextmanager
def use_oracle_golden(mapping: dict[str, list[dict]] | None):
    """Scoped golden injection: the previous mapping is always restored on
    exit."""
    previous = set_oracle_golden(mapping)
    try:
        yield oracle_golden()
    finally:
        set_oracle_golden(previous)


# --- Evidence shaping, immediately before generation ------------------------
# See docs/design-rationale.md#evidence-shaping-position

#: The parenthesised unit conversion in an amount line. The exact figure is
#: kept.
_EOK_SUFFIX = re.compile(r"^(-\s*[^:\n]*:\s*-?[\d,]+원)\s*\(-?[\d,]+억원\)\s*$", re.M)
#: The parenthesised duration in a period line. The start and end dates are
#: kept.
_DURATION_SUFFIX = re.compile(r"^(-\s*기간:\s*\S+\s*~\s*\S+)\s*\([^()\n]*\)\s*$", re.M)

_RCEPT_RE = re.compile(r"\d{14}")
_BLOCK_SPLIT = re.compile(r"(?m)^(?=##\s)")


def _record_fields(block: str) -> list[str]:
    """Lines this evidence block was built from `record["fields"]`, or an empty
    list.

    The exact strings `to_markdown` wrote are rebuilt and removed. Guessing the
    line shape with a regular expression would also remove unrelated lines that
    share the same format.
    """
    from argus.agent import tools

    found = _RCEPT_RE.search(block)
    if not found:
        return []
    record = tools._record_of(found.group(0))
    fields = (record or {}).get("fields") or {}
    return [f"- {label}: {value}" for label, value in fields.items()]


def shape_evidence(evidence: str, arm: Arm | None = None) -> str:
    """Shape the evidence string for the arm. No LLM call, and the retrieval
    result is untouched.

    With `schema_fields` off, the extracted field lines of the non-exchange
    groups are removed. With `calc_values` off, the deterministically computed
    parentheses (unit conversion and contract duration) are removed.
    See docs/design-rationale.md#calc-values-replacement
    """
    arm = arm or current()
    if arm.schema_fields and arm.calc_values:
        return evidence
    text = evidence or ""

    if not arm.schema_fields and text:
        blocks = []
        for block in _BLOCK_SPLIT.split(text):
            drop = set(_record_fields(block))
            if drop:
                block = "\n".join(line for line in block.split("\n")
                                  if line.strip() not in drop)
            blocks.append(block)
        text = "".join(blocks)

    if not arm.calc_values:
        text = _EOK_SUFFIX.sub(r"\1", text)
        text = _DURATION_SUFFIX.sub(r"\1", text)
    return text


# --- Deterministic citation attachment. No LLM call -------------------------
# Evidence markdown comes in two shapes, one per document family, and this code
# reads the markdown itself rather than recovering records: the record lookup
# returns nothing for periodic filings, which would kill half of this arm.
# See docs/design-rationale.md#citation-attachment

#: The separator in an evidence header is an en dash (U+2013). Written as a
#: hyphen, this pattern matches no header at all.
_CITE_HEAD = re.compile(r"^(?:\[기재정정\]\s*)?\[([^\]]+)\]\s*(.*?)\s*–\s*(.*)$")
_CITE_RCEPT = re.compile(r"접수번호\s*(\d{14})")
#: 답변이 이미 가리킨 접수번호를 셀 때 쓴다. 위 것과 달리 `접수번호` 라는 낱말을
#: 요구하지 않는다 — 답변은 `(근거: …, 20230131800162)` 처럼 낱말 없이 적는 일이 많고,
#: 그것도 이미 가리킨 것으로 세어야 같은 번호를 두 번 붙이지 않는다.
_CITE_RCEPT_ANY = re.compile(r"\d{14}")
_CITE_DATE = re.compile(r"공시일[:\s]*(\d{4})-?(\d{2})-?(\d{2})")
#: Numeric fingerprint linking an answer to a piece of evidence. A figure with
#: thousands separators counts even when short, because periodic financials
#: fold the unit and would otherwise miss the fingerprint. Requiring the
#: separator keeps years and percentages from matching everything.
_CITE_NUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d{7,}")
#: Answers that get no citation. Citing a filing in a refusal is false
#: attribution. The phrasings are listed generously, because the words a model
#: refuses in are open-ended; over-matching costs nothing, since this list
#: applies only to answers that point at no evidence at all.
#: See docs/design-rationale.md#citation-attachment
_CITE_REFUSAL = ("확인되지 않습니다", "확인할 수 없", "찾을 수 없", "알 수 없",
                 "드릴 수 없", "제공할 수 없", "답변할 수 없", "포함되어 있지 않")
#: Cap on citations attached to one answer. The evidence window holds up to
#: ten records, and listing them all would make the citation longer than the
#: answer.
MAX_CITATIONS = 3
#: Citations to strip from a refusal. Broader than the detection pattern,
#: because it has to catch what the model wrote itself, including a form that
#: gives the receipt number without naming it.
_CITE_ANY = re.compile(r"\s*\(\s*근거\s*:[^()]*(?:\([^()]*\)[^()]*)*\)")


def _cite_numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in _CITE_NUM.findall(text or "")}


#: 블록 안의 **메타 줄**. 이 문서가 무엇인지를 말하는 줄이고 이 문서가 담은 값이 아니다.
#: `- 정정체인:` 은 `ablation.stale_note` 의 각인과 `pipeline._chain_note` 가 쓴다.
_CITE_META_LINE = re.compile(r"(?m)^\s*-\s*정정체인\s*:.*$")


def _body_only(block: str) -> str:
    """인용 대상을 고를 때 읽는 부분. 메타 줄을 뺀다.

    🔴 각인 줄(`- 정정체인: … (최신 계약금액 583,874,002,800원)`)은 **다른 문서의 값**을
    적는다. 그것을 이 블록의 숫자로 세면 `cite_used` 의 유일성 규칙이 그 수를 *대체본
    블록에만 유일한 수*로 읽어 **대체본을 그 금액의 출처로 인용한다.** 대체본에는 그 수가
    없으므로 거짓 귀속이다.

    실측(`A63` r1 · `Q-116`): 답변이 최신 금액 583,874,002,800 을 말하면서 대체본
    `20240722800037` 을 근거로 달았다. 계획서
    `docs/superpowers/plans/2026-08-21-raw-preprocessing.md` §5.2 가 이것을 미리 지목했다.

    `aggregate.py` 의 `_LINE_DIRECTIVE` 가 `- 출처:` 메타 줄을 본문이 아니라고 배제한
    전례와 같은 판단이다.
    """
    return _CITE_META_LINE.sub("", block or "")


#: Lexical link between an answer and a piece of evidence. Some answers name a
#: subject without ever writing a figure, so numbers alone cannot link them.
#: The minimum length keeps particles and suffixes from matching every block.
_CITE_TERM = re.compile(r"[0-9A-Za-z가-힣]{3,}")
#: Terms that appear in every filing. Linking on these would match all of
#: them.
_CITE_STOP = frozenset((
    "공시일", "접수번호", "계약금액", "투자금액", "계약상대", "사업보고서", "분기보고서",
    "반기보고서", "주식회사", "기재정정", "신규시설투자등", "단일판매공급계약체결",
))


def _cite_terms(text: str) -> set[str]:
    return {t for t in _CITE_TERM.findall(text or "") if t not in _CITE_STOP}


def _citation_units(evidence: str, provenance: dict[str, dict] | None = None) -> list[dict]:
    """One evidence block becomes one citation. A block with no receipt number
    cannot be cited and is dropped.

    The work is split in two. Which pieces of evidence survived is read from
    the evidence string, because truncation happens there, and only a single
    receipt-number pattern is read so a change of format cannot break it. The
    values used in the citation come from `provenance`, taken straight from the
    records rather than parsed back out of text this code rendered.

    With an empty `provenance`, from older artifacts, the oracle path or tests,
    it falls back to re-reading the headers.
    See docs/design-rationale.md#citation-attachment
    """
    provenance = provenance or {}
    units: list[dict] = []
    for block in re.split(r"(?m)^##\s+", evidence or ""):
        if not block.strip():
            continue
        rcept = _CITE_RCEPT.search(block)
        if not rcept:
            continue
        head = block.split("\n", 1)[0].strip()
        known = provenance.get(rcept.group(1))
        if known:
            label, date = known["label"], known["date"]
            # Only sections that actually survived into the evidence are
            # listed. The record is filled before truncation, so it still holds
            # sections that were cut, and citing those would attribute the
            # answer to evidence the prompt never saw.
            alive = [t for t in (known.get("tails") or []) if t and t in block]
            tail = " / ".join(alive) or known.get("tail", "")
        else:
            matched = _CITE_HEAD.match(head)
            label, tail = (matched.group(1), matched.group(3)) if matched else (head, "")
            found = _CITE_DATE.search(block)
            date = "-".join(found.groups()) if found else ""
        # Merged by receipt number. Evidence blocks are per section, so one
        # periodic report splits into several, and emitting one citation each
        # would repeat the same receipt number. The convention's example lists
        # the sections under a single receipt number instead.
        key = rcept.group(1)
        unit = next((u for u in units if u["rcept_no"] == key), None)
        if unit is None:
            unit = {"rcept_no": key, "label": label.strip(), "date": date,
                    "tails": [], "numbers": set(), "terms": set()}
            units.append(unit)
        for part in (t.strip() for t in tail.split(" / ")):
            if part and part not in unit["tails"]:
                unit["tails"].append(part)
        unit["numbers"] |= _cite_numbers(_body_only(block))
        # Linking reads the header and the recorded title, not the block body:
        # a periodic section runs to thousands of characters and would match
        # any answer, making the selection meaningless.
        unit["terms"] |= _cite_terms(head) | _cite_terms(tail)
    return units


def _render_citation(unit: dict) -> str:
    """Render one citation in the form the convention requires: report name and
    period, receipt number, section path."""
    head = f'{unit["label"]} ({unit["date"]})' if unit["date"] else unit["label"]
    parts = [head, f'접수번호 {unit["rcept_no"]}']
    tail = " / ".join(unit.get("tails") or [])
    if tail:
        parts.append(tail)
    return "(근거: " + ", ".join(parts) + ")"


#: Numeric prefix on a section path segment: `2. 내부통제에 관한 사항`.
_SECTION_NUM = re.compile(r"^\s*[0-9IVXivx]+\s*[.)]\s*")
#: Shortest segment usable as a signal. `개요` and `현황` appear in hundreds of
#: filings, so a match on them says nothing about which section was asked for.
_SECTION_MIN = 8


#: Four-digit year in a question or an evidence label. Non-capturing, so
#: `findall` returns whole years rather than the century group.
_LABEL_YEAR = re.compile(r"(?:19|20)\d{2}")


def _units_naming_question_section(question: str, units: list[dict]) -> list[dict]:
    """Citations whose section path the question called by name.

    The same section is filed every year, so a name match alone is not enough:
    `Q-053` loads `내부통제에 관한 사항` from both the 2026 and the 2023 quarterly
    report. The answer speaks about 2026 only, and citing 2023 for it is the
    false attribution this whole branch exists to prevent. When the question
    names a year, labels carrying a different one are dropped; a label with no
    year is kept, because the filter must not silence a citation it cannot judge.
    """
    hits = []
    for unit in units:
        if _question_names_loaded_section(question, [unit]):
            hits.append(unit)
    years = set(_LABEL_YEAR.findall(question))
    if not years:
        return hits
    scoped = [u for u in hits
              if not set(_LABEL_YEAR.findall(u["label"]))
              or set(_LABEL_YEAR.findall(u["label"])) & years]
    #: 연도가 하나도 안 맞으면 좁히지 않는다. 좁혀서 빈 손이 되면 표기가 아예 안 붙어
    #: 종전 동작으로 돌아가는데, 그것은 이 처방이 겨냥한 것과 반대다.
    return scoped or hits


def _question_names_loaded_section(question: str, units: list[dict]) -> str:
    """The section path the question named, when that section is in the
    evidence. Empty string when the question named none of them.

    🔴 This is the external signal. Deciding refusal from the answer's own
    wording cannot separate two different things that share a vocabulary:

        답변   "이 보고서에는 그 항목이 없다"      -- answers the question
        거절   "답을 못 찾겠다"                   -- declines to answer

    Both contain `찾을 수 없`. The evidence separates them: if the question
    called a section by name and that section is loaded, the answer was written
    from it and is not a refusal. Widening the phrase list was measured and
    abandoned in v4 -- the words a model refuses in are open-ended.

    Self-Consistency reports that a model's normalised conditional probability
    barely differs between right and wrong answers, so a limit-of-knowledge
    judgment must not rest on the model's own output. See
    `paper/base/base.md` §3 and docs/design-rationale.md#citation-attachment

    The section paths come from `_citation_units`, which already parses them
    into `tails`. Parsing the headers a second time here is how one of the two
    copies ends up fixed and the other does not.
    """
    if not question:
        return ""
    for unit in units:
        for tail in unit["tails"]:
            segment = _SECTION_NUM.sub("", tail.split(">")[-1]).strip()
            if len(segment) >= _SECTION_MIN and segment in question:
                return tail
    return ""


def attach_citation(answer: str, evidence: str, arm: Arm | None = None,
                    provenance: dict[str, dict] | None = None,
                    question: str = "") -> tuple[str, list[str]]:
    """Attach a citation when the answer carries none.

    Returns (new answer, receipt numbers attached).

    Where this is called matters. It has to run after every LLM call, so that
    the generation, critic and regeneration prompts stay byte-identical and the
    request cache hits; called earlier, a re-measurement misses the cache
    entirely and spends credits again.

    The citation is inserted before the verification note, because the scorer
    strips everything from that note onward and a citation placed after it
    would disappear before scoring.
    See docs/design-rationale.md#citation-attachment
    """
    arm = arm or current()
    if not arm.attach_citation:
        return answer, []
    from argus.agent.critic import quick_check       # imported at call time to avoid a cycle
    if not answer:
        return answer, []

    units = _citation_units(evidence, provenance)
    # The decision is made on the body with any citation the model wrote
    # removed. Otherwise a receipt number inside that citation reads as the
    # answer having pointed at evidence, and the refusal check is bypassed.
    bare = _CITE_ANY.sub("", answer).rstrip()
    bare_numbers = _cite_numbers(bare)
    named = [u for u in units if u["rcept_no"] in bare]
    used = [u for u in units if u["numbers"] & bare_numbers]
    titled = [u for u in units if u["terms"] & _cite_terms(bare)]

    # A refusal gets no citation attached and keeps none it already carries.
    # Citing a filing the answer never used is false attribution.
    #
    # Instructing the model through the prompt was measured and abandoned: the
    # version listing forbidden terms made things worse, and the version
    # without them improved by about one question, indistinguishable from
    # noise.
    #
    # The refusal decision reads only the strong links, the receipt number and
    # the amount. Counting title-word overlap as well matches almost always,
    # because the company name appears in every evidence header. Word overlap
    # is useful for choosing among several blocks but too weak to decide
    # whether an answer used the evidence at all.
    # See docs/design-rationale.md#citation-attachment
    if not (named or used) and any(k in bare for k in _CITE_REFUSAL):
        # One exception, behind `refusal_needs_gap`: an answer that reports what
        # a named section contains is not a refusal even when it says the
        # content is not there. `_question_names_loaded_section` decides that
        # from the evidence rather than from the wording.
        #
        # Measured before the flag was written (scratch/w1_risk_sizing.py, 336
        # stored answers): 53 answers across 18 questions are judged refusals
        # today, and this exception moves exactly 3 of them -- Q-053 in all
        # three rounds. The safety and info-limit refusals stay refusals.
        section_units = (_units_naming_question_section(question, units)
                         if arm.refusal_needs_gap else [])
        if not section_units:
            return bare, []
        # The link here is the named section, not a receipt number or an amount,
        # so the normal pick order (`named or used or titled`) must not run:
        # title-word overlap matches the company name in every block and would
        # attach filings the answer never spoke about. Only the blocks carrying
        # the section the question asked for are cited.
        if not quick_check(answer)["has_citation"]:
            return _insert_citation(answer, section_units[:MAX_CITATIONS])
        # 모델이 이미 표기를 쓴 경우는 지우지 않고 아래 종전 경로로 흐른다.

    if not units:
        return answer, []

    #: 이미 표기가 있으면 종전 판은 여기서 끝났다. `cite_used` 는 거기서 한 걸음 더 간다 —
    #: **답변이 쓴 근거가 실제로 인용됐는지**를 본다. 표기가 하나 있다는 것과 옳은 것을
    #: 가리킨다는 것은 다른 문제이고, `Q-110` 이 그 차이다.
    if quick_check(answer)["has_citation"]:
        answer, more = _cite_missing_used(answer, units, bare_numbers, arm)
        answer, paired = _cite_chain_pair(answer, units, question, arm)
        return answer, more + paired

    # Nothing is attached when the answer links to nothing. This rule changed
    # three times: attaching every candidate cost a question its
    # no-fabrication score, attaching only the top-ranked one still followed an
    # off-topic answer, and extending the list of refusal phrasings has no end
    # because the words a model refuses in are open-ended. The decision is
    # therefore made on the link rather than the phrasing: with no receipt
    # number, amount or title word in common, the answer cannot be said to have
    # used this evidence, and a citation attached anyway is false attribution.
    # See docs/design-rationale.md#citation-attachment
    picked = (named or used or titled)[:MAX_CITATIONS]
    if not picked:
        return answer, []
    answer, attached = _insert_citation(answer, picked)
    #: 종전 상한(3)이 고른 것 밖에 답변이 쓴 근거가 더 있으면 그것도 붙인다. 열거형
    #: 답변은 여섯 갈래를 세는 일이 있어 3 으로는 담기지 않는다.
    answer, more = _cite_missing_used(answer, units, bare_numbers, arm)
    answer, paired = _cite_chain_pair(answer, units, question, arm)
    return answer, attached + more + paired


def _insert_citation(answer: str, picked: list[dict]) -> tuple[str, list[str]]:
    """Append citations for `picked`, before the verification note.

    The note matters: the scorer strips everything from it onward, so a citation
    placed after it would disappear before scoring.
    """
    citation = "\n\n" + " ".join(_render_citation(u) for u in picked)
    head, sep, tail = answer.partition("\n\n(주의:")
    return head + citation + sep + tail, [u["rcept_no"] for u in picked]


#: `cite_used` 의 상한. 종전 `MAX_CITATIONS`(3)보다 크다 — 열거형 답변은 계약상대
#: 여섯 갈래를 세는 일이 있고(`Q-110`), 3 으로 자르면 그 답변의 근거를 절반도 못 싣는다.
#: 8 은 관측된 최대 열거 폭(6)에 여유 둘을 더한 값이다. 무한이 아닌 이유는 근거 칸이
#: 10건까지 담기므로 상한이 없으면 표기가 답변보다 길어질 수 있기 때문이다.
MAX_CITATIONS_USED = 8


def _cite_chain_pair(answer: str, units: list[dict], question: str,
                     arm: Arm) -> tuple[str, list[str]]:
    """정정을 묻는 질의에서, 답변이 인용한 정정본의 **원공시 하나**를 붙인다.

    `cite_used` 의 유일성 규칙("답변이 쓴 수가 그 블록에만 유일할 때")은 정정 체인 안에서
    성립하지 않는다. 원공시와 정정본이 같은 수를 담기 때문이다. 규칙은 옳고 적용 범위가
    문제이므로 규칙을 두고 예외를 만든다.

    🔴 **범위를 세 겹으로 좁혔다.** 넓은 판은 측정 후 기각했다
    (`scratch/simulate_a58_rules.py`, 저장 답변 336개):

        유일성 요구를 통째로 뺀다      10문항 · 골든 7 : 비골든 9  (44%)
        인용본의 형제를 모두 붙인다     15문항 · 골든 16 : 비골든 9  (64%)
        🟢 최신본의 형제 하나만        14문항 · 골든 12 : 비골든 4  (75%)

    비골든을 늘리는 것이 이 함수의 위험이다. `cite_used` 자체가 네 후보 중 **비골든
    부착을 23 -> 4 로 줄이는** 기준으로 골라진 것이므로, 그 결정을 되돌리는 판은 쓸 수
    없다. 채택한 판의 비율(12:4)은 `cite_used` 가 받아들인 비율(8:13 중 8 채택, 비골든 4)
    보다 나쁘지 않다.

    Relink 가 준 것은 랭커가 아니라 **query-driven 이라는 성질**이다 — "무엇이 정정됐나" 는
    짝을 요구하고 "최종 금액은?" 은 최신본만 요구한다. 그래서 `_CHAIN_ASK` 로 질의를 먼저
    가른다. 전역으로 최신본을 끌어오는 판(`latest_pin`)은 퇴행 2건으로 기각됐다.
    See paper/idea/idea.md §3.8 · docs/design-rationale.md#citation-attachment

    🔴 **사정거리가 계획서보다 짧다.** 겨냥한 다섯 문항 중 `Q-054`·`Q-068`·`Q-082` 는
    원공시가 **근거에 아예 없어서** 붙일 수가 없고(없는 출처를 지목하는 것이 된다),
    `Q-094` 는 정정본의 `links` 가 비어 있고 근거의 `최초제출일` 칸도 비어 있다. 이
    함수가 닫는 것은 `Q-093` 하나이며, 곁따라 `Q-035` 의 어긋남도 닫는다. 남은 셋은
    회수 문제이므로 오픈이슈로 옮긴다.
    """
    if not arm.cite_chain_pair or not question:
        return answer, []
    from argus.agent import tools                  # imported at call time: cycle
    from argus.agent.critic import _CHAIN_ASK

    if not _CHAIN_ASK.search(question):
        return answer, []
    loaded = {u["rcept_no"]: u for u in units}
    present = set(_CITE_RCEPT_ANY.findall(answer or ""))
    add: list[str] = []
    for rcept in sorted(present):
        probe = tools.chain_probe(rcept)
        # 인용된 것이 최신본일 때만 원공시를 찾는다. 원본을 인용한 답변에 정정본을 붙이는
        # 것은 방향이 반대이고, 그것은 `latest_pin` 이 기각된 자리다.
        if not probe or not probe.get("is_latest"):
            continue
        siblings = sorted(link for link in (probe.get("links") or ())
                          if link in loaded and link not in present and link not in add)
        if siblings:
            add.append(siblings[0])
    # 상한이 있는 자원에 더하는 것은 밀어내는 것이다. W1 실측으로 지금 최대가 6 이고
    # 상한이 8 이라 여유가 있지만, 여유를 가정하지 않고 확인한다.
    room = MAX_CITATIONS_USED - len(present)
    add = add[:max(0, room)]
    if not add:
        return answer, []
    return _insert_citation(answer, [loaded[r] for r in add])


def _cite_missing_used(answer: str, units: list[dict], numbers: set[str],
                       arm: Arm) -> tuple[str, list[str]]:
    """Cite the evidence the answer used but did not point at.

    🔴 Reads the evidence only. The golden set is never consulted; this runs at
    inference time, where it does not exist.

    The link is a figure the answer wrote that **only this block carries.**
    Sharing a figure with another block is not a link, because then nothing says
    which of them the answer used -- the same rule the aggregation follows
    (README section 4.3: nothing is stated when the condition is not uniquely
    determined).

    Four criteria were measured on the `A49` local run before this one was
    picked (`scratch/compare_cite_criteria.py`, 2026-08-21). Columns are new
    golden receipts cited / receipts attached that the golden set does not name.

        figure shared or not          13 / 23     Q-102 gets a November filing
                                                  cited for a May question:
                                                  both happen to say 50 billion
        + company named, 2 terms      17 / 33
        + company named               33 / 161    fires on every block of every
                                                  answer, as the note above
                                                  `_CITE_TERM` predicted
        **figure unique to the block   8 /  4**   adopted
        (this one)

    The adopted rule keeps 8 of the 13 golden gains while cutting the
    unnamed-by-golden attachments from 23 to 4, and those four were read by hand:
    each is a contract the answer discusses whose figure only that filing
    carries, so the answer did use it and the golden list simply does not name
    every contract the answer covers -- the `Q-030` situation. A false citation
    is a fabrication-shaped defect on an axis the organisers score; a citation
    not gained is only a gain not made. That asymmetry decided it.

    🔴 What this does not fix. `Q-110` states no figure at all -- it enumerates
    roles -- so no figure can be unique to a block and nothing is attached. Its
    citation defect needs the evidence to carry per-item receipts (the roster
    route), not this pass.
    """
    if not arm.cite_used or not units:
        return answer, []
    #: 답변이 쓴 수를 어느 블록들이 갖고 있는가. 둘 이상이면 그 수로는 어느 것을 썼는지
    #: 못 가린다.
    owner: dict[str, int] = {}
    for unit in units:
        for number in unit["numbers"] & numbers:
            owner[number] = owner.get(number, 0) + 1
    present = set(_CITE_RCEPT_ANY.findall(answer or ""))
    missing = [u for u in units
               if u["rcept_no"] not in present
               and any(owner[n] == 1 for n in (u["numbers"] & numbers))]
    if not missing:
        return answer, []
    return _insert_citation(answer, missing[:MAX_CITATIONS_USED])

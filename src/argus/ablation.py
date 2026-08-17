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
                          "followup_hop", "reverse_chain", "partner_profile")

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
    "hop_ladder": ("partner_profile",),
    "calc_values": ("lean_rules",),
    "counterparty_hop": ("multi_hub",),
    "function_calling": ("calc_tools_only", "calc_queries_only",
                         "substitute_tool_results"),
    "verifier": ("substitute_verified", "provenance_gate", "regenerate_on_verify",
                 "counterparty_exempt"),
    "aggregate_line": ("substitute_aggregate",)}

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
                            "reverse_chain", "partner_profile")


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
             "A34", "A35", "A36", "A37", "A38", "A39", "A40", "A41", "A42",
             "A21", "A22", "A10", "A18", "A24",
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
                         A29B, A29_ALL, A29C, A30, A34, A35, A36, A37, A38, A39, A40, A41, A42)}

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
#: The history of what this default has been, with the measurements behind each
#: change, is recorded separately.
#: See docs/design-rationale.md#operational-default-history
_current: Arm = A42


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
        unit["numbers"] |= _cite_numbers(block)
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


def attach_citation(answer: str, evidence: str, arm: Arm | None = None,
                    provenance: dict[str, dict] | None = None) -> tuple[str, list[str]]:
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
        return bare, []

    if quick_check(answer)["has_citation"] or not units:
        return answer, []

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
    citation = "\n\n" + " ".join(_render_citation(u) for u in picked)

    head, sep, tail = answer.partition("\n\n(주의:")
    return head + citation + sep + tail, [u["rcept_no"] for u in picked]

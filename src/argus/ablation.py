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
    #: 🔴 **`A35` 의 알맹이 — 답변의 괄호 환산을 우리가 다시 쓴다. LLM 0회.**
    #:
    #: 사유·실측은 `agent/aggregate.fix_unit_parens` 독스트링에 적었다. 요약하면:
    #: 답변 132건 전수에서 괄호 환산 67개 중 **4개(3문항)** 가 틀렸다. 본문 수치는
    #: 근거에서 옮겨 적으니 맞고 괄호는 **자기가 나눠야 하니** 틀린다 — v4 의 발견이다.
    #:
    #: 🔴 **하나는 우리가 만든 것이다.** `substitute_aggregate` 가 본 수치만 갈아
    #: 끼우고 모델이 제 오답에서 계산한 괄호를 그대로 뒀다(`Q-115`). 그래서 이 처분은
    #: 치환 **뒤에** 선다.
    #:
    #: 🔴 **`split_trillion_eok`(근거 성형)와 다른 자리다.** 저쪽은 생성 **전** 근거의
    #: 괄호를 가르고 이쪽은 생성 **후** 답변의 괄호를 고친다. 형식은 `prompts._split_jo`
    #: 를 공유한다 — 둘이 다른 형식을 쓰면 모델이 같은 값을 다르게 읽는다.
    #:
    #: 🔴 모든 LLM 호출이 끝난 뒤라 프롬프트가 바이트 동일이고 `RequestCache` 가
    #: 전부 적중한다 (`attach_citation` 과 같은 성질).
    fix_unit_parens: bool = False
    #: 🔴 **`A36` 의 알맹이 — 발주처를 `missing_corp` 비교 대상에서 뺀다. LLM 0회.**
    #:
    #: `verify_completeness` 의 `missing_corp` 은 *"비교 질의인데 근거에 그 기업이
    #: 없다"* 를 잡는다. 그런데 질문이 지목한 **발주처**는 그 계약을 공시한 쪽이 아니라
    #: **발주한 쪽**이라 근거에 있을 수가 없다 — 영구 오탐이다.
    #:
    #: 실측(2026-08-16): `Q-113`·`Q-115` 는 **정답인데** *"근거에 삼성전자 이(가)
    #: 없다"* · *"근거에 현대자동차 이(가) 없다"* 각주가 붙는다.
    #:
    #: 🔴 **저장소가 이미 아는 사실이다.** `_wants_enumeration` 은 발주처가 곧 질의
    #: 기업이면 열거를 끄고, `test_disclosing_company_is_never_bound_as_counterparty`
    #: 가 같은 판단을 고정한다. 그 앎이 이 검사에만 안 닿아 있었다.
    #:
    #: 🔴 **`verifier=False` 와 함께 켤 수 없다** (`FLAG_DEPENDENCIES`) —
    #: `verify_completeness` 가 `_verify` 안에 있어 검증 단계가 꺼지면 도달하지 않는다.
    counterparty_exempt: bool = False
    #: 🔴 **`A38` 의 알맹이 — 답변이 스스로 쓴 식과 어긋나는 수를 맞춘다. LLM 0회.**
    #:
    #: 🔴 **「추론 논리성」의 측정 수단이 아니다 — 그 축의 실패 양식 **하나**를 잡는
    #: 검사다.** 첫 판에서 *"그 지표를 재는 첫 수단"* 이라 적었는데 과장이었다:
    #: **저장 답변 132건 중 적용되는 것이 9건(6.8%)** 이다(식이 없으면 침묵한다).
    #:
    #: 그 진단은 **judge 가 이 축의 도구가 아님**을 보여 줬다 — EM 과 갈린 7건 중
    #: **여섯이 오탐**이다(긴 정기공시 섹션에서 값을 못 찾음 1 · 거절 문항에 요점
    #: 대조를 건 적용 착오 5). 그런데 남은 하나가 진짜였고 **결정적으로 잡힌다**:
    #:
    #:     Q-111  머리말 "7,591,628,000원 적습니다"
    #:            풀이   "362,307,038,000 − 354,725,410,000 = 7,581,628,000원"  ← 골든
    #:
    #: EM 은 골든 문자열이 어딘가 있어서 통과시켰고, 우리 수치검사는 식 자체가 맞아서
    #: 통과시켰다. **답변이 자기와 모순인데 두 잣대 다 못 봤다.**
    #: 저장 답변 132건 전수에서 검출 1건 · 🔴 **오탐 0건**.
    #:
    #: 🔴 `verifier` 와 무관하다 — 생성 이후 답변 문자열만 본다. 도달 불가 자리가 없다.
    self_consistency_fix: bool = False

    #: 🔴 **열거 집합의 경계를 근거 머리에 싣는다** (v6 · 2026-08-17).
    #:
    #: 계약상대 hop 은 파트너 전량을 이미 안다(`partners=3`). 그 로스터가 블록 **안**에
    #: 주석으로만 있어서 답변이 일부를 빠뜨렸다 — `N-013` 에서 두산퓨얼셀 블록이 근거
    #: 59% 지점(세 번째)이었고 답변은 두 곳만 적었다. 근거에는 셋이 다 있었다.
    #:
    #: 🔴 **문면이 성적을 가르고 있었다.** 같은 축의 `heldout_v2`·`heldout_v3` 문항은
    #: *"빠짐없이 알려줘"* 라 전건을 답했고, `N-013`·`N-014` 는 *"어디인가"* 라 빠뜨렸다.
    #: 질문이 그 낱말을 안 써도 집합의 경계가 보이게 하는 것이 이 스위치의 전부다.
    #:
    #: `aggregate_line` 과 같은 자리다 — 칸을 더 주는 게 아니라 **한 줄**을 머리에 둔다.
    roster_line: bool = False

    #: 🔴 **반올림만 적힌 금액을 근거의 원값으로 되살린다** (v6 · 2026-08-17).
    #:
    #: `fix_unit_parens` 의 반대 자리다. 저쪽은 *"원값은 맞고 괄호가 틀린"* 것을 고치고
    #: 이쪽은 *"괄호만 있고 원값이 없는"* 것을 고친다. `N-011` 실측 — 근거에
    #: `257,732,228,908` 과 `725,693,799,075` 가 둘 다 실렸는데 답변은 *"2,577억원"* ·
    #: *"7,257억원"* 으로만 적었다. **값은 맞고 정밀도만 잃었다.**
    #:
    #: 반올림해서 같은 억이 되는 근거 금액이 둘 이상이면 침묵한다 — 확신 없이 원값을
    #: 박으면 *맞는 근사*를 *틀린 정확*으로 바꾸는 꼴이다.
    expand_rounded: bool = False

    #: 🔴 **우리가 아는 결과값이 답변에 없으면 결론 한 문장을 우리가 적는다** (v6).
    #:
    #: `substitute_aggregate` 가 못 잡는 자리다. 저쪽은 답변에 *"근거 밖 수 하나"* 가
    #: 있을 때만 발화하는데, `H-008` 에서 모델은 **틀린 수를 쓴 게 아니라 맞는 수를
    #: 틀리게 엮었다** — 두 수가 다 근거에 있어 발화 조건에 안 걸렸고, 정작 질문이
    #: 물은 `646,244,000,000` 은 답에 없었다.
    #:
    #: 검사망이 *'근거 밖'* 축만 보고 *'질문에 답했는가'* 축을 안 봤다. 이 스위치가
    #: 그 축을 하나 만든다.
    state_total: bool = False

    #: 🔴 **같은 날 공시가 둘일 때 날짜가 지목하는 것을 가른다** (v6 · `K-004`).
    #:
    #: 삼성바이오로직스가 2023-07-04 에 같은 제목으로 두 건을 공시했고 **둘 다 근거에
    #: 실렸는데** 모델이 정정본을 골랐다. 검색이 아니라 **선택**이 틀렸다.
    #:
    #: 가르는 규칙은 날짜의 뜻이다 — *"N월 D일 공시한 계약"* 이 지목하는 것은 그날
    #: 공시된 계약이지 **다른 날 계약의 정정본**이 아니다. `related` 가 그 사실을 든다.
    date_disambiguation: bool = False
    #: 🔴 **`A26` 의 알맹이 — 가리키는 대상이 없는 지시어를 '범위 밖' 과 가른다. LLM 0회.**
    #:
    #: 규약 감사 `F-3` 이 실측했다. `AUD-15` *"그 회사의 작년 실적 좀 알려줘"* 가
    #: `out_of_universe` 로 갈려 *"70개사 범위 안의 기업으로 질문해 주시면"* 이라 답했다 —
    #: **기업이 지목되지 않은 것인데 범위 밖이라고 말한다.** 사용자는 자기가 물은 회사가
    #: 70개사에 없다고 오해하고, `CONVENTION §3 R4` 의 *"역질문"* 은 발화하지 않는다.
    #:
    #: 🔴 `NO_SUCH_REPORT` 와 **같은 종류의 구분**이다. v3 에서 *"기업도 연도도 범위 안인데
    #: 그 보고서만 없는 경우"* 를 따로 가른 것과 축이 같고, 그때 적은 사유 — *"한계의
    #: 종류가 다르면 고지도 달라야 한다"* — 가 여기에도 그대로 적용된다. 갈래가 하나
    #: 빠져 있었을 뿐이다.
    #:
    #: 🔴 **겨냥은 작다.** 평가셋 88 에 이 형태가 몇 건인지는 재 봐야 안다. 그래도 세우는
    #: 이유는 크레딧 0원(LLM 0회)이고 `§5` 「정보한계 대응」이 독립 지표라서다.
    ambiguous_gate: bool = False
    #: 🔴 **`A25` 의 알맹이 — 근거 표기를 우리가 붙인다. LLM 0회.**
    #:
    #: 규약 감사(2026-08-16 · `docs/compliance-audit-2026-08-16.md` F-2)가 잰 값:
    #: 실질 답변 8건 중 규약 형식(`CONVENTION §7.5`)을 지킨 것이 **3건**이었고,
    #: 우리 채점기의 `citation_rate` 도 `A20r` 에서 **0.765**(그리드 0.750)다.
    #:
    #: 🔴 **기전은 `A20` 과 같다.** v4 의 단일 발견이 *"모델이 숫자를 손에 쥐는 순간
    #: 틀린다"* 였고 `A20` 이 이긴 이유는 재계산값을 **근거에서 우리가 직접 읽어** 모델의
    #: 타이핑을 안 거쳤기 때문이다. **접수번호는 14자리 숫자다** — 감사에서 정확히 같은
    #: 병이 나왔다. `AUD-08` 은 옮겨 적었으나 라벨을 빠뜨렸고 `AUD-11` 은 아예 안 적고
    #: *"(근거: 각 보고서별 상세내역 참조)"* 로 뭉갰다. **그래서 접수번호도 타이핑시키지
    #: 않는다.** 처분의 네 번째 갈래다 — 각주(v3) · 치환(`A20`) · 재생성(`A20r`) · **부착**.
    #:
    #: 🔴 **왜 `critic` 이 못 막았나.** `quick_check` 는 누락을 정확히 발화하는데(감사에서
    #: 8건 중 7건 발화) 처분으로 부르는 `judge` 는 *"근거가 답을 뒷받침하나"* 만 보고
    #: **표기 형식을 고치지 않아** `supported=True` 로 통과시킨다. 탐지 100% · 교정 0% 가
    #: 인용 축에서 되풀이된 것이다.
    #:
    #: 🔴 **부착은 LLM 호출을 하나도 안 쓴다.** 그래서 생성 프롬프트가 바이트 동일이고
    #: `RequestCache` 가 전부 적중한다 — 이 arm 의 재측정은 **생성 크레딧 0원**이다.
    attach_citation: bool = False
    #: 🔴 **`A10` 전용 — 자기일관성(Self-Consistency) 표본 수 `k`.** 1이 종전 동작이고,
    #: 스위치를 안 건드린 운영 경로는 답변 생성 호출이 **정확히 한 번**이다.
    sc_k: int = 1
    #: 🔴 **온도. 이 모듈의 머리말이 "고정" 이라고 적어 둔 통제 변수다** (§I-3).
    #: v3 까지 온도 0.0 은 `pipeline.answer_question` 에 **직접 하드코딩**돼 있었고
    #: `Arm` 에 표현할 자리가 없었다 (`R0-5` 실측: 저장소 전체 `.py` 에 `A10` 문자열이
    #: 없었다). 여기로 옮기되 **기본값이 0.0 이어야** v1~v4 측정이 전부 유효하게 남는다.
    #:
    #: 🔴 **`A10` 이 이 통제 변수를 깨는 유일한 arm 이다.** 그러니 단독으로 재고 다른
    #: arm 과 섞지 않는다. `A20r`(사유 주입 재생성)은 **온도 0.0 그대로**이고, 그래서
    #: 둘은 같은 축의 변형이 아니라 서로 다른 기전이다.
    #:
    #: 🔴 **요청 캐시가 온도를 키에 넣는다** (`RequestCache._key` = url + body 해시).
    #: 그래서 `A10` 은 재실행마다 전액 재결제이고, 나아가 **k 개 표본이 같은 키를 쓰므로**
    #: 2회차부터는 `clova.no_cache()` 로 캐시를 우회해야 다양성이 생긴다.
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if self.retrieval not in RETRIEVAL_MODES:
            raise ValueError(f"retrieval 은 {RETRIEVAL_MODES} 중 하나여야 합니다: {self.retrieval}")
        # 🔴 `tools._group_records` 가 둘 중 하나만 고른다 — 함께 켜면 뒤의 것이
        #    조용히 무시되고 그 arm 의 Δ 가 다른 조작의 것이 된다.
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

# --- A29a+A20 — 집계 한 줄 + 치환 (v5 실측이 이 결합을 요구했다) --------------------
#: 🔴 **집계 줄만으로는 `Q-034` 가 안 뒤집혔다** (2026-08-16 실측). 정확한 값
#: 817,900,000,000 을 근거에 싣고 *"다시 계산하지 말라"* 는 지시까지 붙였는데 모델은
#: **여전히 자기가 더했다**(817,100,000,000). v4 의 발견이 여기서 한 번 더 확인됐다 —
#: *"모델이 숫자를 손에 쥐는 순간 틀린다."*
#:
#: 🔴 **그런데 헛일이 아니었다.** 집계 줄이 모델에게 **식을 쓰게 만들었다**:
#:
#:     A28  답변 "합계는 757,900,000,000원"        식 없음 → equation 검사 미발화
#:                                                → provenance 만 발화 (재계산값 없음)
#:     A29a 답변 "72,200 + 398,800 + 346,900 = 817,100"
#:                                                → equation 발화 · 재계산 **817,900,000,000**
#:                                                → judge 세 축 전부 통과
#:
#: 즉 이 arm 이 판 것은 정답이 아니라 **고칠 수 있는 형태**다. 그리고 그 형태를 고치는
#: 기전은 이미 측정으로 이겨 둔 것이 있다 — `A20`(`substitute_verified`).
A29A_A20 = replace(A28, name="A29a+A20", aggregate_line=True, substitute_verified=True)

# --- A29s — 집계 한 줄 + 집계 치환 (처분의 다섯 번째 갈래) --------------------------
#: 🔴 `A20` 이 못 잡는 자리를 잡는다 — 저쪽은 답변의 **식**을 파싱하고 이쪽은 **근거의
#: `amount` 필드**를 쓴다. `Q-109` 는 `=` 를 안 써서 식이 없다. 사유는
#: `Arm.substitute_aggregate` 와 `agent/aggregate.substitute` 독스트링.
A29S = replace(A28, name="A29s", aggregate_line=True, substitute_aggregate=True)

# --- A29s+A20 — 두 치환을 함께 (원천이 다르므로 배타가 아니다) ----------------------
A29S_A20 = replace(A28, name="A29s+A20", aggregate_line=True,
                   substitute_aggregate=True, substitute_verified=True)

# --- A29s+A20+A33 — v5 산술·검색 채택 후보 전량 -------------------------------------
#: 🔴 `Q-113` 은 셋이 다 있어야 넘어간다 — `A33` 이 골든을 4/4 로 회수하고,
#: 집계가 체인별 차의 합을 내고, 치환이 모델의 오기를 갈아 끼운다.
A29S_A20_A33 = replace(A28, name="A29s+A20+A33", aggregate_line=True,
                       substitute_aggregate=True, substitute_verified=True,
                       reverse_chain=True)

# --- A29b — 정정 금액 시계열 한 줄 (사다리 밖 곁가지 · v5 오답 Q-023) ---------------
#: 🔴 **채택된 산술 구성 위에 얹어 잰다.** `A28` 위에 세우면 Δ 에 산술 몫이 안 섞이는
#: 대신, 결합에서 다시 재야 한다 — `Q-023` 은 산술 arm 이 안 건드리는 문항이라
#: (`_op` 가 빈 값) 여기서는 `A28` 위에 세워 단독 Δ 를 깨끗이 본다.
A29B = replace(A28, name="A29b", revision_series=True)

# --- A30 — 정정사유·주요내용 런타임 적재 (사다리 밖 곁가지 · v5 오답 Q-087·Q-093) ----
#: 🔴 **v5 에서 인질이 가장 큰 arm 이다.** R0 실측 번짐: 정정본이 실린 문항 54 ·
#: 투자판단이 실린 문항 15 (평가 문항 124 중). 겨냥은 둘이다 — 흔들리면 기각한다.
A30 = replace(A28, name="A30", correction_fields=True)

# --- A34 — v5 채택분 전량 결합 (새 운영 기본값 후보) --------------------------------
#: 🔴 **단독 Δ 를 먼저 다 확보하고 나서 결합을 잰다** (`A25+A26` 과 같은 규칙).
#: 여섯 플래그의 겨냥이 서로 다른 문항이라 상호작용이 없을 것이라 보지만,
#: **없을 것이라 보는 것과 재 보는 것은 다르다.**
A29C = replace(A28, name="A29c", partner_profile=True)


# --- A34 — v5 채택분 전량 결합 (새 운영 기본값 후보) --------------------------------
A34 = replace(A28, name="A34",
              speculation_gate=True,      # Task 1 — S-003·S-004
              identifier_gate=True,       # Task 2 — S-002
              reverse_chain=True,         # Task 3 — Q-113 (골든 회수)
              aggregate_line=True,        # Task 4 — 산술
              substitute_aggregate=True,  # Task 4 — 집계 치환
              substitute_verified=True,   # Task 4 — 식 치환 (A20)
              revision_series=True,       # Task 5 — Q-023
              correction_fields=True)     # Task 7 — Q-087·Q-093

# --- A35 — 답변의 괄호 환산 교정 (사다리 밖 곁가지 · 규약 「정확성」) ----------------
#: 🔴 **채택된 결합(`A34`) 위에 얹어 잰다.** 겨냥 넷 중 하나가 `A29s`(집계 치환)가
#: 만든 불일치라(`Q-115`), `A28` 위에 세우면 그 자리가 재현되지 않는다.
#: 사유는 `Arm.fix_unit_parens` 와 `agent/aggregate.fix_unit_parens` 독스트링.
A35 = replace(A34, name="A35", fix_unit_parens=True)

# --- A36 — 발주처를 `missing_corp` 에서 면제 (사다리 밖 곁가지 · 규약 「정확성」) -----
A36 = replace(A34, name="A36", counterparty_exempt=True)

# --- A37 — v5 정확성 개선 둘을 함께 (새 기본값 후보) --------------------------------
A37 = replace(A34, name="A37", fix_unit_parens=True, counterparty_exempt=True)

# --- A38 — 자기정합 교정 (사다리 밖 곁가지 · 규약 「추론 논리성」) -------------------
#: 🔴 이 축을 재는 **첫 수단**이다. 사유는 `Arm.self_consistency_fix`.
A38 = replace(A37, name="A38", self_consistency_fix=True)

#: 🔴 v6 채택 — 열거 완전성. 사유는 `Arm.roster_line`.
A39 = replace(A38, name="A39", roster_line=True)

#: 🔴 v6 채택 — 반올림 복원. 사유는 `Arm.expand_rounded`.
A40 = replace(A39, name="A40", expand_rounded=True)

#: 🔴 v6 채택 — 결과값 부재. 사유는 `Arm.state_total`.
A41 = replace(A40, name="A41", state_total=True)

#: 🔴 v6 채택 — 같은 날 공시 가르기. 사유는 `Arm.date_disambiguation`.
A42 = replace(A41, name="A42", date_disambiguation=True)

# --- v5 채택 후보 전량 (산술 + 시계열 + 검색) ---------------------------------------
A29_ALL = replace(A28, name="A29all", aggregate_line=True, substitute_aggregate=True,
                  substitute_verified=True, reverse_chain=True, revision_series=True)

# --- A22 — 다중 허브 결속 `W=1→3` (사다리 밖 곁가지 · v4 신설) --------------------
#: `A8` 과 **플래그 하나만** 다르다 — 그 하나가 발주처 차선의 허브를 하나에서 최대 셋으로
#: 넓힌다. 🔴 **`LADDER`·`LOO_REMOVED` 에 넣지 않는다** — `A20`·`A21`·`A18` 과 같은 곁가지다.
#:
#: 🔴 **이 arm 은 정답률을 올리지 않는다 — 근거 회수를 올린다.** 계획서 §4·§9pre 가
#: 예측을 박아 뒀다: **뒤집기 0 · 근거 회수 +2**(`Q-108`·`Q-117`). 보고할 때 두 분모를
#: 반드시 갈라야 한다(그리드 72 / 밸류체인 골든 46). 성적 표에 섞으면 "유료 arm 이
#: 성적을 못 올렸다" 와 "근거를 더 가져왔다" 가 한 칸에서 상쇄된다.
#:
#: 🔴 겨냥 셋의 성격이 다르다:
#:   `Q-107`  hop 은 이미 발화한다(`via=한국수력원자력` · partners=6). 단수라 **진짜
#:            계약상대**(두산에너빌리티)를 허브로 못 잡는 것이 병목이다 (`R1-11 ②` 이관)
#:   `Q-108`  `Route.counterparty` 가 **아예 None** 이라 hop 이 안 선다. 라우터 결속은
#:            `R1-11 ①` 이 기각했고(두 회사가 다 공시한 쪽), 근거의 `계약상대:` 는 그
#:            기각과 충돌하지 않는다
#:   `Q-117`  열거 hop(`strat=enumerate`)이 서는데 갈래가 계약상대별이라 허브가 더 필요하다
A22 = replace(A8, name="A22", multi_hub=True)

# --- A10 — 자기일관성 `k=3` (사다리 밖 곁가지 · v4 신설) --------------------------
#: 🔴 **이 저장소에서 통제 변수(온도 0.0)를 깨는 유일한 arm 이다** (§I-3 · 머리말).
#: 그래서 **단독으로 재고 다른 arm 과 섞지 않는다.** `A20r`(사유 주입 재생성)이 가장
#: 가까운 이웃인데 그쪽은 온도 0.0 이라 **기전이 다르다** — 저쪽은 진단을 되먹이고
#: 이쪽은 다양성을 사서 다수결한다. 나란히 놓아야 v3 의 미검증 주장이 닫힌다.
#:
#: `k=3` · `temperature=0.7` 은 Self-Consistency 논문(2023)의 기본 설정이다. 🔴 우리
#: 코퍼스로 재본 적이 없으므로 **그 값 자체가 측정 대상**이고, 채택 여부와 무관하게
#: "논문의 전제를 우리 데이터에서 재고 판정했다" 가 이 arm 의 산출물이다.
#:
#: 🔴 **겨냥 5건 중 둘(`Q-044`·`Q-097`)은 `A20` 이 이미 고쳤다 — 순증만 센다.**
#: 남는 순증 겨냥은 `Q-018`(그리드) · `Q-109`·`Q-115`(밸류체인) 셋이다.
#:
#: 🔴 `LADDER`·`LOO_REMOVED` 에 넣지 않는다 — `A20`·`A21`·`A22` 와 같은 곁가지다.
A10 = replace(A8, name="A10", sc_k=3, temperature=0.7)

# --- A24 — `chat(tools=…)` 을 실제로 켠다 (사다리 밖 곁가지 · v4 신설) -------------
#: `A8` 과 **플래그 하나만** 다르다 — 그 하나가 답변 생성 호출에 `TOOL_SPECS`(15종 =
#: 검색 8 + 계산 7)를 넘겨 **HCX-005 자신이 도구를 부르게** 한다.
#: 🔴 **`LADDER`·`LOO_REMOVED` 에 넣지 않는다** — `A20`·`A21`·`A18`·`A22` 와 같은 곁가지다.
#:
#: 🔴 **이름 충돌 주의.** 계획서 §4b `idea` 표의 ★4 가 `A24` 를 *"modal(의무/재량/예정)
#: 보존 지시"* 로 적어 뒀는데 **그쪽은 코드로 존재한 적이 없다**(저장소 전 `.py` 에
#: `A24` 문자열 0건이었다). 사용자 지시가 이 이름을 function calling 에 배정했으므로
#: 여기가 정본이고, modal arm 은 세울 때 다른 번호를 써야 한다.
#:
#: 🔴 **겨냥은 순증 0 이다** (`R1-8` `2aaf46f`). `tools=` 가 새로 겨냥하는 것은
#: `Q-018`·`Q-044`·`Q-097` 셋인데 셋 다 더 싼 0원 arm 이 이미 가져갔다
#: (`Q-044`·`Q-097` → `A20` 치환 · `Q-018` → `A21` 게이트). 그래도 세운 이유는
#: **게이트 F 의 유일한 미완을 측정으로 닫기 위해서**다 — `A18` 과 같은 성격의 예외이고,
#: *"안 재고 기각"* 보다 *"재고 기각"* 이 제안서 §7 에서 비교할 수 없이 강하다.
#:
#: 🔴 **`A8` 대비 도구가 실제로 닿는 상한은 +1 이다.** `Q-044` 는 `calc_total` 이 닿지만
#: `Q-097` 의 `per_unit`(나눗셈)은 **도구 7종에 대응이 없고**(`calc_share` 는 ×100 이 붙는다)
#: `Q-018` 은 계산이 아니라 **전사** 오류다.
#:
#: ❌ **판정: 기각 (2026-08-15 · 실측 3,759.2원).** 게이트 F 의 `[~]` 를 이것으로 닫았다.
#:
#:     그리드 72     A8 65  ->  A24 59      Δ = −6 (−8.3%p)
#:     전체 88       A8 81  ->  A24 74      Δ = −7
#:     밸류체인 12   A8  6  ->  A24  6      Δ =  0  (다만 +Q-110 / −Q-111 로 갈렸다)
#:     회귀          9건    (그리드·전체 8 + 밸류체인 1)
#:     호출          129 -> 165회 (+27.9%) · 프롬프트 토큰 +44.0% · 비용 +33.3%
#:
#: 🔴 **기각 사유는 "모델이 도구를 안 불러서" 가 아니다 — 88문항 중 37문항(42.0%)이
#: 실제로 불렀다.** 사유는 셋이고, 셋 다 실측이다:
#:
#:   ① 🔴 **도구가 정답을 줘도 모델이 옮겨 적지 못한다.** `Q-044` 에서 `calc_total` 이
#:      골든과 **바이트까지 같은** `2,365,868` 을 돌려줬는데 답변은 `2,366,968` 을 썼다.
#:      독립 스모크(같은 인자·다른 회차)에서는 `2,366,868` 이 나왔다 — **두 번 다 틀리고
#:      틀린 자리가 다르다.** 이 저장소의 병목은 계산이 아니라 **전사**이고
#:      (`Q-018` · `A18` 의 환각률 62.1% 와 같은 기전), function calling 은 그 축을
#:      건드리지 않는다. 반대로 `A20`(치환)이 이기는 이유가 여기서 설명된다 —
#:      **치환에는 옮겨 적는 단계가 아예 없다.**
#:   ② 🔴 **모델이 부른 도구의 과반이 계산이 아니라 검색이다** (47건 중 검색 26 · 계산 21 ·
#:      `read_section` 16 · `get_history` 10). 근거는 이미 프롬프트에 실려 있는데 모델이
#:      다시 읽으러 가고, 그 결과가 근거를 희석한다. 회귀 8건 중 7건이 도구를 불렀다.
#:   ③ 🔴 **`tools` 와 `maxTokens` 가 배타라 길이 상한이 사라진다**(40001). 도구를 한 번도
#:      안 부른 `Q-106` 도 회귀했다 — 즉 순수 디코딩 조건 변화만으로도 답이 흔들린다.
#:
#: 🔴 **`_CALC_SPECS` 만 내밀면 ②는 줄지만 ①·③은 그대로다.** 그리고 ①이 유일한 겨냥
#: (`Q-044`)을 이미 죽였으므로 그 변형의 상한은 0 이다 — 그래서 재시도를 세우지 않는다.
#: 🔴 **그 추론은 ①을 고칠 수 없다고 가정한 것이었고, 고칠 수 있다** — `A20` 과 같은
#: 기전(값을 코드가 문자열에 박는다)을 도구 결과에 걸면 된다. `A24b` 가 그것이다.
A24 = replace(A8, name="A24", function_calling=True)

# --- A24b — 🔴 도구 결과를 모델이 옮겨 적게 하지 않는다 (v4 신설) ------------------
#: 🔴 **`A24` 를 지우지 않는다** — 그 arm 은 기각 판정의 물증이다. `A24b` 는 그 위에
#: 기각 사유 셋을 **하나씩 겨냥한** 재시도이고, 셋 다 실측에 대응한다:
#:
#:     ① 전사 오류      `substitute_tool_results` — 도구 결과로 답변의 수를 갈아 끼운다
#:     ② 근거 희석      `calc_tools_only`         — 계산 7종만 내민다 (검색 8종을 뺀다)
#:     ③ 길이 상한 소실  `calc_queries_only`       — 계산 질의에만 도구를 연다
#:
#: 🔴 **`A24` 의 §9 ③이 *"그 변형의 상한은 0"* 이라고 적어 뒀고, 그 추론은 ①을
#: 고칠 수 있다는 것을 몰랐다.** ②만 고치면 상한이 0 인 것이 맞다 — ①이 유일한 겨냥
#: (`Q-044`)을 죽이기 때문이다. `A24b` 가 재는 것은 **①을 고쳤을 때도 그런가** 이다.
#:
#: 🔴 **순증 겨냥이 얇다.** 겨냥 셋 중 `Q-044`·`Q-097` 은 `A20`(0원 치환)이 이미
#: 고쳤고, `Q-018` 은 계산이 아니라 **근거에서 옮겨 적기**라 계산 도구가 낼 값이
#: 없다. 그래서 `A24b` 단독의 상한은 사실상 `A8` 회복(Δ 0)이고, 볼 만한 것은 아래
#: 결합 arm 이다.
A24B = replace(A8, name="A24b", function_calling=True, calc_tools_only=True,
               calc_queries_only=True, substitute_tool_results=True)

# --- A24b+A20 — 두 치환의 결합 (v4 신설) -----------------------------------------
#: 🔴 **원천이 다른 두 치환을 함께 켠다.** `A24b` 는 *도구가 돌려준 값*으로,
#: `A20` 은 *검사가 재계산한 값*으로 답변의 수를 갈아 끼운다. 배타가 아니고, 순서는
#: **도구 치환이 먼저**다 (`pipeline._answer_with_tools` → `_verify`) — 도구 값이
#: 더 이른 원천이고, 치환된 뒤의 답변을 `_verify` 가 다시 본다.
#:
#: 🔴 **`A24b` 와 플래그가 하나만 다르다**(`substitute_verified`). 그래야
#: `Δ(A24b → A24b+A20)` 이 `A20` 의 몫으로 읽힌다.
A24BS = replace(A24B, name="A24b+A20", substitute_verified=True)

#: 실행 순서는 **A8 부터 역순**이다 (§I-4b) — 가장 비싼 구성을 먼저 확보해야
#: 중간에 예산이 끊겨도 최신 성능을 안다. A2 계열은 사다리를 다 확보한 뒤 맨 끝이다 —
#: G1 불허로 A2 를 버려도 앞의 여덟 칸이 그대로 남아야 한다.
#: 🔴 `ORACLE` 은 맨 끝이다 — 진단용이라 사다리·LOO 를 다 확보한 뒤에 붙는다.
#: 🔴 `A8-lean` 은 **사다리·LOO 뒤, `A2` 계열 앞**이다. 앞의 열두 칸은 이미 측정돼 요청
#: 캐시로 0원에 재생되고, `A8-lean` 은 **채택 후보**라 G1 미확정인 dense arm(`A2`·`A2F`)과
#: 진단 arm(`ORACLE`)보다 먼저 확보해야 한다. `--run` 이 정지선에 걸리면 이 순서가 곧
#: 무엇을 잃는지의 순서다.
#: 🔴 `A20` 은 `A8-lean` 바로 뒤다 — 같은 이유로 **채택 후보**이고, G1 미확정인 dense
#: arm 과 진단 arm 보다 먼저 확보해야 한다.
#: 🔴 `A20r` 은 `A20` 바로 뒤다 — **나란히 재는 짝**이라 둘 사이를 다른 arm 이 갈라
#: 놓으면 정지선이 하나만 확보하고 끊길 때 그 비교가 통째로 사라진다.
#: 🔴 `L6`·`L7`·`L8` 은 `L4` 바로 뒤다 — LOO 는 한 덩어리로 확보해야 표가 완성된다.
#: 🔴 `A18` 은 `A21` 뒤 · dense 앞이다 — **채택 후보가 아니라 기각 예정 arm**이라
#: (`R0-1` 겨냥 0 · Δ=0 예측) 채택 후보 넷(`A8-lean`·`A20`·`A20r`·`A21`)을 먼저
#: 확보한 뒤에 선다. 그래도 G1 미확정인 dense·진단 arm 보다는 앞이다 — 이쪽은
#: **제안서 §7 의 논거**로 반드시 필요하고 저쪽은 아니다.
#: 🔴 `A22` 는 `A21` 뒤 · `A18` 앞이다 — **밸류체인 근거 회수**를 올리는 arm 이라
#: 겨냥 0 인 `A18` 보다 앞에 서야 정지선에 걸릴 때 잃는 것이 적다.
#: 🔴 `A24` 는 `A18` 뒤 · dense 앞이다 — `A18` 과 같은 **기각 예정 arm**(순증 겨냥 0)이라
#: 채택 후보들 뒤에 서지만, **게이트 F 의 유일한 미완을 닫는 arm** 이라 G1 미확정인
#: dense·진단 arm 보다는 앞이다.
#: 🔴 `A24b`·`A24b+A20` 은 `A24` 바로 뒤다 — **나란히 재는 짝**이라(`A20`/`A20r` 과
#: 같은 이유) 사이를 다른 arm 이 갈라 놓으면 정지선이 하나만 확보하고 끊길 때 그 비교가
#: 통째로 사라진다. 결합 arm 이 뒤인 것은 단독 Δ 를 먼저 확보해야 결합의 몫을 귀속할 수
#: 있어서다.
#: 🔴 `A25` 는 `A20r` 바로 뒤다 — **운영 기본값에 플래그 하나만 더한 짝**이라 사이가
#: 벌어지면 Δ 를 귀속할 상대를 잃는다. `A20`/`A20r` 과 같은 이유다.
RUN_ORDER = ("A8", "L1", "L2", "L3", "L4", "L6", "L7", "L8",
             "A7", "A6", "A5", "A4", "A1b", "A1", "A0",
             "A8-lean", "A20", "A20r", "A25", "A26", "A25+A26", "A27b", "A28",
             "A31", "A32", "A33", "A29a", "A29a+A33", "A29a+A20",
             "A29s", "A29s+A20", "A29s+A20+A33", "A29b", "A29all", "A29c", "A30",
             "A34", "A35", "A36", "A37", "A38", "A39", "A40", "A41", "A42",
             "A21", "A22", "A10", "A18", "A24",
             "A24b", "A24b+A20", "A2F", "A2", "ORACLE")

#: 사다리(=v2 곡선). A2 계열·LOO 는 여기 들어가지 않는다.
LADDER = ("A0", "A1", "A1b", "A4", "A5", "A6", "A7", "A8")

#: LOO 구성과 각자가 뺀 것. 보고서가 이 사전을 그대로 읽는다.
#: 🔴 `L1` 의 라벨을 "정정체인 hop" 이라고만 적으면 안 된다 — `followup_hop` 까지 함께
#: 끄므로 `※관련공시` 1,267엣지가 그 안에 섞여 있다. `L4` 가 그것을 갈라 준다.
LOO_REMOVED = {"L1": "CHAIN_HOP 전체 (정정체인 550엣지 + 관련공시 후속공시)",
               "L2": "major·holding 필드", "L3": "발주처 2-hop",
               # 🔴 라벨이 절반만 적고 있었다 (R2A `O-2`). `6d6de5d` 이후 `L4` 는
               #    역방향 `followups` 와 **순방향 `terminates` 를 함께** 뗀다 —
               #    둘 다 출처가 `※ 관련공시` 셀이라 `include_followups` 가 같이 가린다.
               #    값은 그대로 옳지만 라벨이 낡으면 Δ 의 뜻이 잘못 인용된다.
               "L4": "관련공시 REFERS_TO 계열 — 후속공시(역방향) + 해지→원계약"
                     "(순방향) 둘 다 (정정체인은 유지)",
               # 🔴 메타 축 셋 (v4). '검색을 끈다' 가 아니라 '필터 인자를 안 넘긴다' 이므로
               #    라벨에 그 사실을 적는다 — "기업 축" 이라고만 적으면 BM25 까지 끈 것으로
               #    읽힌다.
               "L6": "search_chunks 의 corp_name 필터 (기업 축 · BM25 는 그대로)",
               "L7": "search_chunks 의 접수일 접두 ym·year·rcept_dt "
                     "(사업연도 base_year 는 유지)",
               "L8": "search_chunks 의 group 인자 (인덱스 군 축 — exchange 로 떨어진다)"}

#: dense 를 켜는 arm. G1 이 불허로 확정되면 **이 둘만 빼면 된다.**
DENSE_ARMS = ("A2", "A2F")

#: 🔴 **성적으로 인용하면 안 되는 arm.** 평가 시점에 재현할 수 없는 정보(골든)를 쓰므로
#: 제출 구성이 될 수 없다. 러너의 요약이 이 목록을 읽어 표 옆에 경고를 찍는다 —
#: 산출물만 보는 사람이 숫자를 성적으로 읽는 것을 막는 유일한 수단이다.
DIAGNOSTIC_ARMS = ("ORACLE",)

ARMS: dict[str, Arm] = {a.name: a for a in
                        (A0, A1, A1B, A4, A5, A6, A7, A8, L1, L2, L3, L4, L6, L7, L8,
                         A2, A2F, ORACLE, A8_LEAN, A20, A20R, A21, A18, A22, A10, A24,
                         A24B, A24BS, A25, A26, A25_A26, A27B, A28, A31, A32, A33,
                         A29A, A29A_A33, A29A_A20, A29S, A29S_A20, A29S_A20_A33,
                         A29B, A29_ALL, A29C, A30, A34, A35, A36, A37, A38, A39, A40, A41, A42)}

#: 🔴 **사다리도 LOO 도 진단도 아닌 곁가지.** 목록을 손으로 적지 않는다 — 러너의 요약과
#: 게이트가 `LADDER + LOO_REMOVED + DIAGNOSTIC_ARMS` 만 훑으므로, 여기 안 잡히는 arm 은
#: **돈을 쓰고도 산출물 문서에 한 글자도 안 나온다.** 그 사고는 이미 한 번 실물로 났다
#: (`ORACLE` 1,747원을 쓰고 `gate_I.md` 어디에도 없었다 · `render_gate` 주석). 더 나쁜
#: 것은 비용 검산이다 — 실지출은 `run.json` 재귀 합이라 그 돈이 들어가는데 arm 비용
#: 합계에는 안 들어가서 "캐시가 막은 금액" 이 그만큼 작게 찍힌다.
SIDE_ARMS: tuple[str, ...] = tuple(
    a for a in RUN_ORDER
    if a not in LADDER and a not in LOO_REMOVED and a not in DIAGNOSTIC_ARMS)

#: 모든 스위치 이름. 새 플래그를 넣으면 여기 자동으로 잡힌다 — 목록을 손으로 적어 두면
#: 빠뜨린 플래그가 LOO 의 '단일 변수 조작' 검사를 조용히 통과한다.
FLAG_NAMES: tuple[str, ...] = tuple(
    f for f in A8.__dataclass_fields__ if f != "name")

#: 스위치를 안 건드리면 완성 구성이다. 운영 경로의 기본값이 곧 A8 이어야
#: '기본값으로 돌린 것' 과 'A8 arm' 이 같은 것임이 보장된다.
#: 🔴 **운영 기본값 = 제출 구성이다.** 평가 서버(`api/server.py`)가 arm 을 지정하지 않으므로
#: 이 값이 곧 주최측이 받는 답변의 구성이다. **여기를 바꾸면 배포도 다시 해야 한다.**
#:
#: v4 (2026-08-15) 에서 `A8` → `A20r` 로 바꿨다. 새 채점기 기준 실측:
#:
#:     A8    그리드 65/72 · 전체 81/88 · 밸류체인 6/12 · 안전성 9/12
#:     A20   그리드 67/72 · 전체 83/88 · 밸류체인 6/12
#:     A20r  그리드 67/72 · 전체 83/88 · 밸류체인 7/12 · 안전성 9/12   ← 최고
#:
#: `A20r` 만 `Q-110` 의 10× 재진술 오류를 고친다 — 치환(`A20`)은 유일한 재계산값이
#: 없으면 못 고치고, 재생성이 사유를 받아 다시 쓴다. 문항당 약 2원.
#: 정본: `artifacts/ablation/v4/rescore_r3b/rescore.md`
#:
#: 🔴 종전 주석(v1 의 `A6` 과 플래그가 동일하다는 기록)은 `A8` 시절의 것이라 지웠다.
#:
#: ---
#:
#: 🔴 **2026-08-16 — `A20r` → `A25+A26`.** 규약 감사(`docs/compliance-audit-2026-08-16.md`)의
#: `F-2`(근거 표시 미달)·`F-3`(지시대상 불명 오진)을 닫는다. **같은 날 · 같은 캐시**로 잰
#: 값이다 (정본: `artifacts/ablation/v4/repro_0816/`):
#:
#:     A20r      그리드 66/72 · 전체 82/88 · 인용률 0.765 (그리드 0.750)
#:     A25       그리드 66/72 · 전체 82/88 · 인용률 0.975 (그리드 0.972)   ← 부착
#:     A26       그리드 66/72 · 전체 82/88 · 인용률 0.765               ← 라우팅
#:     A25+A26   그리드 66/72 · 전체 82/88 · 인용률 0.975   ← 채택. 상호작용 0
#:
#: **성적은 동률이고 「근거 표시」만 오른다.** 둘 다 LLM 0회라 지연·크레딧도 그대로다.
#:
#: 🔴 **위 `A20r` 의 67/72 는 낡은 값이다.** 같은 arm 을 오늘 다시 돌리면 **66** 이다 —
#: `Q-097` 의 재생성이 2026-08-15 에는 성공했는데 지금은 실패한다. 갈림은 재생성 프롬프트에
#: 실리는 사유 문자열이다 (`답변 2,078억 vs …` → `답변 2,078억 원 vs …`). 그것이 바뀌어
#: 캐시를 빗나갔고 다시 부른 모델이 이번엔 못 고쳤다. **채택 판정을 낡은 값과 비교하지
#: 않으려고 네 arm 을 같은 날 나란히 다시 쟀다.**
#:
#: 🔴 **채점기가 문항에 따라 동전 던지기라는 것도 이 라운드에서 실측됐다** — `Q-105` 의
#: judge 를 같은 답변에 5회 반복하니 `[F, T, F, T, T]` · σ 0.548 이고 세 축
#: (`covers_key_points`·`grounded`·`no_fabrication`)이 통째로 함께 뒤집힌다.
#: **그래서 그리드 ±1 을 유의미한 차이로 읽으면 안 된다.**
#: 🔴 **2026-08-16 재감사 — `A25+A26` → `A28`.** `RF-1`(없는 분기보고서를 다른 분기
#: 보고서로 답한다)을 닫는다. 같은 날 같은 캐시 실측:
#:
#:     A25+A26   전체 82/88 · 인용률 0.975 · 답변 88건
#:     A28       전체 82/88 · 인용률 0.975 · 🔴 **답변 88건 전부 바이트 동일**
#:
#: **부작용이 0 인 것을 바이트로 확인했다.** 평가셋에 없는 분기를 지목한 문항이 없어
#: Δ 가 0 인 것이고, 겨냥은 숨은 문항 쪽이다. 겨냥 문항에서는 이렇게 갈린다:
#:
#:     A25+A26   "3분기 보고서에는 수주잔고가 기재되어 있지 않습니다 …"   llm=1
#:     A28       "질문이 지목한 보고서가 코퍼스에 없습니다"              llm=0
#:
#: 🟢 **LLM 호출을 하나 아끼기까지 한다** — 없는 문서를 설명하느라 쓰던 호출이다.

# --- 운영 기본값 ----------------------------------------------------------------
#: 🔴 **v5 채택분 전량 = `A34`.** `A28` 이 틀리던 14건 중 **12건**을 잡았다:
#:
#:     그리드 66 → 72/72 · 정보한계 6/6 · 회귀 10/10 · 전체 82 → 88/88
#:     밸류체인 7 → 10/12 · 안전성 9 → 12/12 · 인용률 0.975 → 0.988
#:     합계 98 → **110/112** · 🔴 회귀 0
#:
#: 남은 둘(`Q-107`·`Q-117`)은 산술이 아니라 **검색**이 막고, 이번 세대의 신호로는
#: 안 풀린다 — `A29c` 기각 사유와 `_lane_scores` 실측을 보라.
#: 🔴 **v5 정확성 개선 둘을 얹어 `A37` 로 올렸다** (2026-08-16). 성적은 110/112 그대로고
#: 움직인 것은 규약 「정확성」 축이다 — `numeric_ok` 107 → 109/112, 정답인데 결함 각주가
#: 붙은 문항 4 → 2건.
#:
#: 🔴 **남은 둘은 알고 남긴 것이다** (사용자 판단 2026-08-16). `substitute_aggregate` 가
#: 결과만 갈아 끼워 `Q-113`·`Q-115` 의 **식이 안 맞는다** — *"틀린 합계"* 를
#: *"맞는 합계 + 안 맞는 풀이"* 로 바꾼다. 근본 원인은 모델이 항을 잘못 골랐다는 것이고
#: 치환은 그것을 덮는다. 정답률(110/112)을 택했다. 되돌리려면 식이 깨질 때 치환을
#: 취소하면 되고 그 대가는 108/112 다.
_current: Arm = A42


def current() -> Arm:
    return _current


def flags(arm: Arm | str | None = None) -> dict:
    """arm 의 플래그만 담은 사전 (`name` 제외). LOO 의 단일 변수 조작을 검사하는 값이다."""
    target = current() if arm is None else (ARMS[arm] if isinstance(arm, str) else arm)
    return {f: getattr(target, f) for f in FLAG_NAMES}


def enabled(flag: str, arm: Arm | str | None = None) -> bool:
    """플래그가 켜져 있는가. 없는 플래그 이름은 **조용히 False 가 아니라 예외**다 —
    오타 하나가 "그 구성요소는 효과가 없었다" 로 보고되는 것을 막는다."""
    if flag not in FLAG_NAMES:
        raise KeyError(f"그런 플래그가 없습니다: {flag!r} (있는 것: {FLAG_NAMES})")
    target = current() if arm is None else (ARMS[arm] if isinstance(arm, str) else arm)
    return bool(getattr(target, flag))


def set_arm(arm: Arm | str) -> Arm:
    """전역 arm 을 바꾸고 이전 값을 돌려준다. 평가 러너 전용."""
    global _current
    previous = _current
    _current = ARMS[arm] if isinstance(arm, str) else arm
    return previous


@contextmanager
def use(arm: Arm | str):
    """`with ablation.use("A1"): ...` — 블록을 벗어나면 반드시 되돌린다."""
    previous = set_arm(arm)
    try:
        yield current()
    finally:
        set_arm(previous)


# --- ORACLE 골든 주입 (러너 전용) ----------------------------------------------
# 파이프라인은 `question_id` 만 알고 evalset 을 모른다. 그래서 골든은 **러너가 넣는다**.
# 모듈 전역인 이유는 `set_arm` 과 같다 — arm 을 갈아끼우는 축과 같은 자리에 있어야
# "무엇으로 쟀는가" 가 한 곳에서 읽힌다.


class OracleGoldenMissing(RuntimeError):
    """🔴 `golden_oracle` arm 인데 골든이 주입되지 않았다. **조용히 넘어가지 않는다.**

    빈 근거로 진행하면 파이프라인이 `UNANSWERABLE` 을 내보내고, 채점기는 그것을 오답으로
    세며, 우리는 그 결과를 "오라클을 줘도 생성이 다 틀렸다" 로 읽는다. 측정이 아니라
    **주입 누락**인데 그것이 판정으로 굳는다 — 이 모듈이 막으려는 바로 그 사고다.
    """


#: `{question_id: [{"rcept_no": ..., "section": ...}, ...]}`. `None` 은 '주입 안 됨' 이고
#: 빈 목록은 '이 문항의 골든이 실제로 없다'(= answerable=false) 라서 **구분해야 한다.**
_oracle_golden: dict[str, list[dict]] | None = None


def set_oracle_golden(mapping: dict[str, list[dict]] | None) -> dict | None:
    """코호트의 `{question_id: evidence}` 를 주입하고 이전 값을 돌려준다. 러너 전용.

    🔴 **heldout 에는 절대 주입하지 않는다** (`scripts/ablation.py` 의 heldout 경로는
    이 함수를 부르지 않고, 그 앞에서 반환한다).
    """
    global _oracle_golden
    previous = _oracle_golden
    _oracle_golden = None if mapping is None else {
        str(qid): list(items or []) for qid, items in mapping.items()}
    return previous


def oracle_golden() -> dict[str, list[dict]] | None:
    """주입된 매핑 그대로. 주입 전이면 `None`."""
    return _oracle_golden


def oracle_golden_for(question_id: str) -> list[dict]:
    """한 문항의 골든 근거. **없으면 예외** — 조용한 빈 근거를 만들지 않는다."""
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
    """`with ablation.use_oracle_golden(m): ...` — 블록을 벗어나면 반드시 되돌린다."""
    previous = set_oracle_golden(mapping)
    try:
        yield oracle_golden()
    finally:
        set_oracle_golden(previous)


# --- 근거 성형 (생성 직전) -----------------------------------------------------

#: `- 계약금액: 22,764,764,160,000원 (227,648억원)` 의 괄호. 원값은 남긴다.
_EOK_SUFFIX = re.compile(r"^(-\s*[^:\n]*:\s*-?[\d,]+원)\s*\(-?[\d,]+억원\)\s*$", re.M)
#: `- 기간: 2025-07-24 ~ 2033-12-31 (8년 5개월)` 의 괄호. 시작·종료일은 남긴다.
_DURATION_SUFFIX = re.compile(r"^(-\s*기간:\s*\S+\s*~\s*\S+)\s*\([^()\n]*\)\s*$", re.M)

_RCEPT_RE = re.compile(r"\d{14}")
_BLOCK_SPLIT = re.compile(r"(?m)^(?=##\s)")


def _record_fields(block: str) -> list[str]:
    """이 근거 블록이 `record["fields"]` 로부터 만든 줄들. 없으면 빈 목록.

    `to_markdown` 이 찍은 것과 **같은 문자열**을 다시 만들어 지운다. 줄 모양을
    정규식으로 추측하면 계약상대·공시유보처럼 서식이 같은 다른 줄까지 지운다.
    """
    from argus.agent import tools

    found = _RCEPT_RE.search(block)
    if not found:
        return []
    record = tools._record_of(found.group(0))
    fields = (record or {}).get("fields") or {}
    return [f"- {label}: {value}" for label, value in fields.items()]


def shape_evidence(evidence: str, arm: Arm | None = None) -> str:
    """arm 에 맞춰 근거 문자열을 성형한다. LLM 0회 · 검색 결과는 건드리지 않는다.

    * `schema_fields=False` — major·holding 추출 필드 줄을 뺀다 (Phase H 이전 상태).
    * `calc_values=False`   — 결정적으로 계산해 박아 둔 괄호(억원 환산 · 계약기간)를 뗀다.
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


# --- A25 — 근거 표기 결정적 부착 (LLM 0회) --------------------------------------
#
# 근거 마크다운은 두 형태다 (실측 — 감사 응답의 `retrieved_context`):
#
#   정형  ## [공급계약 체결] 삼성전자 – 단일판매ㆍ공급계약체결 — 반도체 위탁생산 공급계약
#         - 공시일: 2025-07-28 (접수번호 20250728800035)
#   정기  ## [사업보고서 2025] 삼성전자 – IV. 이사의 경영진단 및 분석의견
#         - 출처: 접수번호 20260310002820 (공시일 20260310)
#
# 🔴 **`pipeline._evidence_records` 를 쓰지 않는다.** 그것은 `tools._record_of` 로 정형
# 레코드만 되찾고 **정기공시에는 `None` 을 돌려준다** (실측: `20260310002820` → `None`).
# 사업보고서 답변에 인용을 못 붙이면 이 arm 의 절반이 죽는다. 그래서 근거 마크다운
# 자체를 읽는다 — 어차피 모델이 베껴 적어야 했던 그 글자다.

#: `–` 는 EN DASH(U+2013)다. 하이픈으로 적으면 헤더가 하나도 안 걸린다.
_CITE_HEAD = re.compile(r"^(?:\[기재정정\]\s*)?\[([^\]]+)\]\s*(.*?)\s*–\s*(.*)$")
_CITE_RCEPT = re.compile(r"접수번호\s*(\d{14})")
_CITE_DATE = re.compile(r"공시일[:\s]*(\d{4})-?(\d{2})-?(\d{2})")
#: 답변과 근거를 잇는 숫자 지문.
#:
#: 🔴 **천 단위 쉼표가 있으면 짧아도 센다.** 처음엔 7자리 이상만 봤는데, 정기공시 재무는
#: `73,581백만원` 처럼 **단위를 백만원으로 접어** 적어서 지문에 안 걸렸다. 실측 `Q-080`
#: (정답인데 인용을 잃었다). 쉼표를 요구하면 연도(`2025`)·비율(`29.23`)은 안 걸리므로
#: "아무 블록이나 다 걸린다" 는 문제도 안 생긴다.
_CITE_NUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d{7,}")
#: 붙이지 않는 답변. 🔴 **거절에 근거를 달면 거짓 귀속이다** — 감사 `F-6` 이 실측한
#: `AUD-04` 가 개인정보 거절에 사업보고서 접수번호를 근거로 달았다.
#: 🔴 **어형을 넉넉히 잡는다.** 처음엔 다섯뿐이라 *"찾을 수 없습니다"* 로 거절한 답변이
#: 그물을 빠져나가 무관한 인용을 달았다 (실서버 실측 `PII-1`). 모델이 거절을 쓰는 말은
#: 한 가지가 아니다. 과하게 잡아도 손해가 없다 — 이 목록은 **근거를 짚지 못한 답변에만**
#: 적용되므로(`named`·`used` 가 비었을 때) 사실을 말한 답변의 인용은 그대로 남는다.
_CITE_REFUSAL = ("확인되지 않습니다", "확인할 수 없", "찾을 수 없", "알 수 없",
                 "드릴 수 없", "제공할 수 없", "답변할 수 없", "포함되어 있지 않")
#: 한 답변에 붙이는 인용 상한. 근거 칸은 최대 10건인데 그것을 다 적으면 답변보다
#: 인용이 길어진다.
MAX_CITATIONS = 3
#: 거절 답변에서 **떼어낼** 근거 표기. 모델이 스스로 쓴 것까지 잡아야 하므로 `CITATION_RE`
#: 보다 넓다 — `(근거: …, 20260310002820)` 처럼 '접수번호' 라는 말 없이 숫자만 적은 것도
#: 거짓 귀속이다 (실측 `AUD-08` 이 그 형태로 썼다).
_CITE_ANY = re.compile(r"\s*\(\s*근거\s*:[^()]*(?:\([^()]*\)[^()]*)*\)")


def _cite_numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in _CITE_NUM.findall(text or "")}


#: 답변과 근거를 잇는 **말**. 숫자만으로는 못 잇는 답이 있다 — 실측 `Q-007` 은
#: *"투자 대상은 '청주 M15X 건설'"* 이라 금액을 한 번도 안 쓴다. 3자 이상만 보는 것은
#: 조사·접미사가 아무 블록에나 걸리는 것을 막기 위해서다.
_CITE_TERM = re.compile(r"[0-9A-Za-z가-힣]{3,}")
#: 어느 공시에나 나오는 말. 이것으로 이으면 전부 걸린다.
_CITE_STOP = frozenset((
    "공시일", "접수번호", "계약금액", "투자금액", "계약상대", "사업보고서", "분기보고서",
    "반기보고서", "주식회사", "기재정정", "신규시설투자등", "단일판매공급계약체결",
))


def _cite_terms(text: str) -> set[str]:
    return {t for t in _CITE_TERM.findall(text or "") if t not in _CITE_STOP}


def _citation_units(evidence: str, provenance: dict[str, dict] | None = None) -> list[dict]:
    """근거 블록 하나 = 인용 하나. 접수번호가 없는 블록은 인용할 수 없으므로 버린다.

    🔴 **일을 절반씩 나눠 갖는다.**

    * **어느 근거가 살아남았나 → 근거 문자열에서.** 절단(`MAX_EVIDENCE` ·
      `_join_section_evidence`)이 여기서 일어나므로 여기서 읽어야 맞다. 읽는 것은
      `접수번호 (\\d{14})` 정규식 **하나**뿐이라 서식이 흔들려도 안 깨진다.
    * **인용에 쓸 값 → `provenance` 에서.** `pipeline._citation_source` 가 레코드에서
      그대로 꺼내 둔 것이다. 우리가 방금 렌더한 글자를 도로 파싱하지 않는다.

    `provenance` 가 비면(구버전 산출물 · `ORACLE` · 테스트) 머리글 되읽기로 되돌아간다.
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
            # 🔴 **근거에 실제로 살아남은 섹션만 적는다.** 기억은 절단 전에 채워지므로
            #    `MAX_SECTION_CHARS` 에서 잘려 나간 섹션까지 들어 있다. 그것을 그대로
            #    쓰면 프롬프트에 닿지도 않은 근거를 인용하게 된다 — 거짓 귀속이다.
            alive = [t for t in (known.get("tails") or []) if t and t in block]
            tail = " / ".join(alive) or known.get("tail", "")
        else:
            matched = _CITE_HEAD.match(head)
            label, tail = (matched.group(1), matched.group(3)) if matched else (head, "")
            found = _CITE_DATE.search(block)
            date = "-".join(found.groups()) if found else ""
        # 🔴 **접수번호 하나로 합친다.** 근거 블록은 섹션 단위라 정기공시 한 보고서가
        #    블록 여럿으로 쪼개지는데, 그것을 인용 여럿으로 내면 같은 접수번호가 반복된다.
        #    `CONVENTION §7.5` 의 예시는 접수번호 하나 아래 섹션을 ` / ` 로 나열한다.
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
        # 🔴 짚기는 **머리글과 기억한 제목** 둘 다 본다. 블록 본문 전체를 쓰면
        #    정기공시 섹션(수천 자)이 아무 답변에나 걸려 선별이 무의미해진다.
        unit["terms"] |= _cite_terms(head) | _cite_terms(tail)
    return units


def _render_citation(unit: dict) -> str:
    """`CONVENTION §7.5` 형식 — `(근거: 보고서명 기준연월, 접수번호 14자리, 섹션경로)`."""
    head = f'{unit["label"]} ({unit["date"]})' if unit["date"] else unit["label"]
    parts = [head, f'접수번호 {unit["rcept_no"]}']
    tail = " / ".join(unit.get("tails") or [])
    if tail:
        parts.append(tail)
    return "(근거: " + ", ".join(parts) + ")"


def attach_citation(answer: str, evidence: str, arm: Arm | None = None,
                    provenance: dict[str, dict] | None = None) -> tuple[str, list[str]]:
    """근거 표기가 없으면 우리가 붙인다. `(바뀐 답변, 붙인 접수번호들)`.

    🔴 **부르는 자리가 중요하다.** 모든 LLM 호출이 끝난 **뒤**에 불러야 생성·critic·재생성
    프롬프트가 바이트 동일로 남고 `RequestCache` 가 적중한다. 앞에서 부르면 재측정이
    캐시를 통째로 빗나가 크레딧을 다시 쓴다.

    🔴 **각주 꼬리 앞에 끼운다.** 채점기(`error_analysis.strip_verify_tail`)가 `(주의:`
    부터를 잘라내므로 그 뒤에 붙이면 우리가 붙인 인용이 채점 전에 사라진다.
    """
    arm = arm or current()
    if not arm.attach_citation:
        return answer, []
    from argus.agent.critic import quick_check       # 순환 import 회피 — 호출 시점에 든다
    if not answer:
        return answer, []

    units = _citation_units(evidence, provenance)
    # 🔴 **모델이 쓴 근거 표기를 뺀 본문으로 판정한다.** 그러지 않으면 인용 안의 접수번호가
    #    `named` 에 걸려 "근거를 짚었다" 로 읽히고, 거절 판정을 그대로 비켜간다 —
    #    감사 `AUD-04` 가 정확히 그 모양이었다.
    bare = _CITE_ANY.sub("", answer).rstrip()
    bare_numbers = _cite_numbers(bare)
    named = [u for u in units if u["rcept_no"] in bare]
    used = [u for u in units if u["numbers"] & bare_numbers]
    titled = [u for u in units if u["terms"] & _cite_terms(bare)]

    # 🔴 **거절에는 근거를 붙이지도, 남겨 두지도 않는다** (규약 감사 `F-6`).
    #
    # 감사 `AUD-04` 는 개인정보 요구를 옳게 거절해 놓고 *"(근거: 사업보고서 2025,
    # 접수번호 20260310002820)"* 을 달았다. 그 보고서는 거절의 근거가 아니다 — 읽지도
    # 않은 공시를 근거로 다는 **거짓 귀속**이고 §5 「근거 기반」에 걸린다.
    #
    # 🔴 프롬프트로 타이르는 길은 재 보고 접었다. `A27` 은 금지어를 나열했다가 외부 출처
    # 안내를 1건 → 3건으로 **늘렸고**, 금지어를 뺀 `A27b` 도 개선이 1문항 안팎이라
    # 잡음과 구별되지 않았다. **v4 의 교훈 그대로 — 말로 타이르지 말고 결정적으로 처분한다.**
    # 🔴 **거절 판정은 강한 연결만 본다** — 접수번호(`named`)나 금액(`used`)이다.
    #    `titled`(제목 낱말 겹침)를 함께 세면 **기업명이 모든 근거 헤더에 있어** 거의
    #    언제나 걸린다. 실측 `AUD-05`·`AUD-07` 이 그렇게 빠져나가 거절 답변에 무관한
    #    공급계약 인용 둘이 붙었다. 낱말 겹침은 **여럿 중 고르는 데는** 쓸 만해도
    #    '이 답이 근거를 썼는가' 를 정하기에는 너무 약하다.
    if not (named or used) and any(k in bare for k in _CITE_REFUSAL):
        return bare, []

    if quick_check(answer)["has_citation"] or not units:
        return answer, []

    # 🔴 **짚이는 것이 하나도 없으면 붙이지 않는다.**
    #
    # 판이 세 번 바뀐 자리다. ① 처음엔 `units` **전부**를 붙였다가 `Q-007` 이
    # `no_fabrication` 을 잃었다 — 답이 '청주 M15X' 하나를 말하는데 무관한 둘(용인 Fab ·
    # 투자판단)을 끌고 오자 채점기가 **없는 내용을 지어냈다**고 봤다. ② 그래서 검색
    # 1순위 하나만 붙이게 했더니, 이번엔 모델이 *"해당 정보가 이미지에 포함되어 있는지
    # 확인해주세요"* 같은 **엉뚱한 답**을 냈을 때도 인용이 따라붙었다 (실서버 `PII-1`).
    #
    # ③ 거절 어형 목록을 늘리는 길은 **끝이 없다** — 모델이 거절을 쓰는 말은 열려 있다.
    # 그래서 문구가 아니라 **연결**로 정한다: 접수번호(`named`)·금액(`used`)·제목
    # 낱말(`titled`) 중 하나도 안 걸리면 그 답변이 이 근거를 썼다고 말할 수 없다.
    # 근거 없이 붙인 인용은 **거짓 귀속**이고 §5 「근거 기반」에 걸린다.
    picked = (named or used or titled)[:MAX_CITATIONS]
    if not picked:
        return answer, []
    citation = "\n\n" + " ".join(_render_citation(u) for u in picked)

    head, sep, tail = answer.partition("\n\n(주의:")
    return head + citation + sep + tail, [u["rcept_no"] for u in picked]

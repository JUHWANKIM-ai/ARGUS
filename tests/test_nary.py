"""`A18` = `T1-targeted` n-ary 추출. **LLM 0회.**

이 파일이 지키는 성질 다섯.

  ① 플래그 기본값이 **종전 동작**이다. 운영 경로가 한 바이트도 안 바뀐다
  ② `A8` 과 **그 플래그 하나만** 다르다. Δ 가 n-ary 추출에만 귀속된다
  ③ **덮어쓰기가 아니라 합집합이다**. 규칙이 뽑은 필드를 LLM 값이 지우면 안 된다
  ④ **코퍼스 레코드를 변형하지 않는다**. 같은 프로세스의 다른 arm 이 오염된다
  ⑤ **`artifacts/*_records.jsonl` 을 안 고친다**. 고치면 BM25 색인 문자열이 갈려
     v1~v4 측정이 전량 무효가 되고, `artifacts/` 는 gitignore 라 복구 수단이 없다

**이 arm 은 겨냥 0 인데도 세운 유일한 arm 이다** (계획서 §13 ③ · 사용자 판단).
Δ = 0 이 예측으로 먼저 박혔고(`R0-1` `2c0558b`), 여기 테스트는 그 예측을 재는 것이
아니라 **arm 이 실제로 무엇을 하는지**를 고정한다.
"""
import json
from pathlib import Path

import pytest

from argus import ablation
from argus.agent import tools

ROOT = Path(__file__).resolve().parents[1]


# --- ① 플래그 -----------------------------------------------------------------

def test_the_nary_flag_is_off_by_default():
    """기본값이 곧 운영 경로다. 참이면 제출 구성이 LLM 추출값을 근거에 싣는다."""
    assert ablation.Arm("X").nary is False
    assert ablation.A8.nary is False
    assert ablation.current().nary is False
    assert ablation.enabled("nary") is False       # 오타는 KeyError 다
    assert "nary" in ablation.FLAG_NAMES           # `FLAG_NAMES` 가 자동으로 잡는다


def test_a18_differs_from_a8_by_exactly_the_nary_flag():
    """단일 변수 조작. 하나라도 더 바뀌면 Δ 가 'n-ary 추출' 의 것이 아니다."""
    a8, a18 = ablation.flags("A8"), ablation.flags("A18")
    assert {k for k in a18 if a18[k] != a8[k]} == {"nary"}
    assert a18["nary"] is True


def test_a18_is_a_side_branch_not_a_rung_and_not_a_loo():
    """사다리에 넣으면 누적 곡선의 Δ(직전) 이 두 arm 의 차이가 아닌 것을 가리킨다.
    LOO 표에 넣으면 '뺀 것' 칸이 검색 구성요소와 코퍼스 보강을 섞는다.
    `A8-lean`·`A20`·`A21`·`ORACLE` 과 같은 곁가지다."""
    assert "A18" not in ablation.LADDER
    assert "A18" not in ablation.LOO_REMOVED
    assert "A18" in ablation.ARMS and "A18" in ablation.RUN_ORDER
    assert "A18" in ablation.SIDE_ARMS      # 여기 안 잡히면 게이트·요약에서 통째로 빠진다
    assert "A18" not in ablation.DIAGNOSTIC_ARMS
    # 채택 후보 넷 뒤 · dense 와 진단 arm 앞이다 (기각 예정이지만 제안서 §7 이 쓴다).
    order = list(ablation.RUN_ORDER)
    assert order.index("A21") < order.index("A18") < order.index("A2F")
    assert order.index("A18") < order.index("ORACLE")


def test_nary_has_no_reachability_dependency():
    """`nary` 는 `FLAG_DEPENDENCIES` 에도 두 도달불가 목록에도 **들어가지 않는다**.
    `_group_records` 는 검색 차선보다 아래에 있어 `retrieval='meta'` 도 `golden_oracle`
    도 같은 사전을 지난다. 넣으면 실재하는 Δ 를 못 재고, 안 넣었는데 실은 도달
    불가이면 조용한 Δ 0 이 된다. **어느 쪽인지 코드로 못박는다.**"""
    assert "nary" not in ablation.META_UNREACHABLE_FLAGS
    assert "nary" not in ablation.ORACLE_UNREACHABLE_FLAGS
    assert not any("nary" in v for v in ablation.FLAG_DEPENDENCIES.values())
    assert "nary" not in ablation.FLAG_DEPENDENCIES
    # 켤 수 있는 조합에 제동이 없다는 것을 실제 생성으로 확인한다.
    ablation.Arm("meta+nary", retrieval="meta", schema_fields=False, hop_ladder=False,
                 chain_hop=False, counterparty_hop=False, split_query=False,
                 calc_values=False, verifier=False, followup_hop=False, nary=True)
    ablation.replace(ablation.ARMS["ORACLE"], name="oracle+nary", nary=True)


# --- ③ 합집합 -----------------------------------------------------------------

RULE_MD = ("## [공급계약 체결] 삼성중공업 – 단일판매·공급계약 체결\n"
           "- 공시일: 2023-06-02 (접수번호 20230602800079)\n"
           "- 계약금액: 1,102,000,000,000원 (11,020억원)\n"
           "- 계약상대: 아시아 지역 선주\n"
           "- 기간: 2023-06-02 ~ 2026-12-31")


def test_the_merge_never_overwrites_a_field_the_rules_already_extracted():
    """**합집합의 핵심.** 규칙이 뽑은 줄이 LLM 값으로 갈리면 이 arm 이 재는 것이
    '추가' 가 아니라 '교체' 가 되고, `L2`(노드 속성 LOO) 와 겨냥이 겹쳐 Δ 의 귀속이
    깨진다. 그리고 규칙 쪽이 옳은 경우가 압도적으로 많다 (2,912 대 238)."""
    merged = tools.merge_nary(RULE_MD, {
        "amount": ["- 계약금액: 9,999원 (0억원)"],
        "counterparty": ["- 계약상대: 엉뚱한 회사"],
        "period": ["- 기간: 1999-01-01 ~ 1999-12-31"]})
    assert merged == RULE_MD                       # 한 글자도 안 바뀐다
    assert "9,999원" not in merged and "엉뚱한 회사" not in merged


def test_the_merge_appends_only_the_fields_the_rules_missed():
    """규칙이 못 뽑은 항목만 붙는다. 붙는 자리는 **끝**이고 앞줄은 안 건드린다."""
    head = "## [투자판단 관련 주요경영사항] X – 제목\n- 공시일: 2023-01-01 (접수번호 1)"
    merged = tools.merge_nary(head, {
        "amount": ["- 계약금액: 1,000,000,000원 (10억원)"],
        "counterparty": ["- 계약상대: 삼성전자"],
        "subject": ["- 대상: 전기차 배터리 모듈"]})
    assert merged.startswith(head)
    assert merged.splitlines()[len(head.splitlines()):] == [
        "- 계약금액: 1,000,000,000원 (10억원)",
        "- 계약상대: 삼성전자",
        "- 대상: 전기차 배터리 모듈"]


def test_the_amount_label_is_matched_by_shape_not_by_a_fixed_string():
    """금액 라벨은 `record['amount_type']` 이 정한다 (`markdown._amount_line`),
    `계약금액`·`투자금액`·`취득금액`… 고정 문자열로 보면 그 서식들에서 규칙의 금액
    위에 LLM 금액이 **한 줄 더** 붙는다."""
    for label in ("계약금액", "투자금액", "취득금액", "금액"):
        md = f"## [x] Y\n- {label}: 100원 (0억원)"
        assert tools.merge_nary(md, {"amount": ["- 계약금액: 200원 (0억원)"]}) == md


def test_a_record_with_no_extraction_is_returned_untouched():
    assert tools.merge_nary(RULE_MD, {}) == RULE_MD


def test_the_line_count_per_field_is_capped():
    """근거가 길어지면 `MAX_EVIDENCE` 아래에서 **다른 문서를 밀어낸다.**"""
    lines = tools._nary_lines([{"counterparty": f"상대 {i}"} for i in range(10)])
    assert len(lines["counterparty"]) == tools.MAX_NARY_VALUES


def test_the_extraction_lines_use_the_same_shape_as_the_rule_writer():
    """서식이 다르면 `prompts.split_trillion_eok` 과 `critic._says_amount` 가 이 줄만
    못 본다. 근거에는 실리는데 규칙 8·9 와 산술 검사에서 조용히 빠진다."""
    lines = tools._nary_lines([{"amount": 1_102_000_000_000, "amount_label": "계약금액",
                                "counterparty": "아시아 지역 선주",
                                "period_start": "2023-06-02", "period_end": "2026-12-31",
                                "subject": "원유운반선 2척"}])
    assert lines["amount"] == ["- 계약금액: 1,102,000,000,000원 (11,020억원)"]
    assert lines["counterparty"] == ["- 계약상대: 아시아 지역 선주"]
    assert lines["period"] == ["- 기간: 2023-06-02 ~ 2026-12-31"]
    assert lines["subject"] == ["- 대상: 원유운반선 2척"]


def test_a_half_open_period_gets_its_own_label_not_a_dangling_range():
    """`- 기간: 2023-06-02 ~ ` 같은 줄은 LLM 이 종료일을 지어내게 만든다."""
    assert tools._nary_lines([{"period_start": "2023-06-02"}])["period"] == [
        "- 계약 시작일: 2023-06-02"]
    assert tools._nary_lines([{"period_end": "2026-12-31"}])["period"] == [
        "- 계약 종료일: 2026-12-31"]


def test_a_missing_or_junk_amount_never_becomes_a_line():
    """`R0-1` 이 센 위험이 여기 있다. 238건 중 **67건이 발행사 유보**다.
    금액이 없는 곳에 0원·음수·문자열이 줄로 서면 '금액 미기재' 가 정답인 문항
    (`Q-035`)이 뒤집힌다."""
    for junk in (None, 0, -1, "", "미정", "1,000원"):
        assert tools._nary_lines([{"amount": junk}]).get("amount") is None


# --- ④⑤ 코퍼스 불변 --------------------------------------------------------------

def test_the_arm_never_mutates_the_cached_corpus_record():
    """`_group_records_raw` 의 사전을 제자리에서 고치면 같은 프로세스에서 이어 도는
    **다른 arm 의 근거까지 오염**되고, 그것은 조용히 일어난다."""
    if not (ROOT / "artifacts/exchange_records.jsonl").exists():
        pytest.skip("코퍼스 산출물이 없다 (gitignore)")
    raw = tools._group_records_raw("exchange")
    before = {k: v.get("markdown") for k, v in raw.items()}
    tools._group_records_nary("exchange")
    assert {k: v.get("markdown") for k, v in raw.items()} == before


def test_switching_arms_does_not_leak_the_nary_evidence():
    """`lru_cache` 를 arm 과 무관하게 하나만 두면 먼저 부른 arm 의 사전이 나중 arm 에
    그대로 재생돼 **Δ 가 조용히 0** 이 된다."""
    if not (ROOT / "artifacts/exchange_records.jsonl").exists():
        pytest.skip("코퍼스 산출물이 없다 (gitignore)")
    with ablation.use("A18"):
        with_nary = tools._group_records("exchange")
    with ablation.use("A8"):
        without = tools._group_records("exchange")
    assert without is tools._group_records_raw("exchange")
    if (ROOT / "artifacts" / tools.NARY_NAME).exists():
        assert with_nary is not without


def test_the_builder_writes_a_new_file_and_never_a_corpus_file():
    """`artifacts/*_records.jsonl` 을 고치면 BM25 색인 문자열이 갈려 v1~v4 측정이
    전량 무효가 된다. `artifacts/` 는 gitignore 라 되돌릴 수단이 없다."""
    from scripts import build_nary

    assert build_nary.OUT_NAME == "nary_records.jsonl" == tools.NARY_NAME
    with pytest.raises(SystemExit):
        build_nary.main(["--out", "artifacts/exchange_records.jsonl", "--limit", "1"])


def test_the_corpus_files_are_not_listed_as_builder_outputs():
    """스크립트 본문이 코퍼스 파일에 **쓰기** 로 손대지 않는지 문면으로 고정한다."""
    src = (ROOT / "scripts/build_nary.py").read_text(encoding="utf-8")
    for group in ("exchange", "major", "holding"):
        assert f'"w"' not in src.split(f"{group}_records.jsonl")[0][-200:]


# --- 추출 산출물의 형식 ---------------------------------------------------------

def test_the_extraction_output_parses_and_carries_a_verbatim_check():
    """산출물에 **원문 대조 결과가 함께** 있어야 한다. 성적이 떨어졌을 때
    `R0-1` 이 지목한 위험(유보 67건에서 최근매출액을 계약금액으로 오인)이 실제로
    일어났는지를 그때 가서 셀 수 있는 유일한 수단이다."""
    path = ROOT / "artifacts" / tools.NARY_NAME
    if not path.exists():
        pytest.skip("nary_records.jsonl 이 없다 (gitignore · 추출 전)")
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    assert rows
    for row in rows:
        assert {"rcept_no", "doc_id", "relations", "arity_precheck"} <= set(row)
        for relation in row["relations"]:
            assert isinstance(relation.get("verbatim"), dict)


def test_the_extraction_only_covers_records_the_rules_failed_on():
    """`schema_matched=True` 2,912건에 손대면 그것은 `T1-targeted` 가 아니라
    `T1-md` 다. 계획서가 *"정의상 새 정보가 없다"* 로 기각한 갈래다."""
    path = ROOT / "artifacts" / tools.NARY_NAME
    if not path.exists() or not (ROOT / "artifacts/exchange_records.jsonl").exists():
        pytest.skip("산출물이 없다 (gitignore)")
    failed = {r["rcept_no"] for group in tools.RECORD_GROUPS
              for r in tools._group_records_raw(group).values()
              if not r.get("schema_matched")}
    with path.open(encoding="utf-8") as fh:
        extracted = {json.loads(line)["rcept_no"] for line in fh if line.strip()}
    assert extracted <= failed, f"규칙이 이미 파싱한 레코드에 손댔다: {extracted - failed}"


def test_a_truncated_response_is_reported_as_a_failure_not_as_an_empty_result():
    """**실측으로 걸린 도구 결함이다.** 첫 판(배열 직접 요구)에서 HCX-005 가 238건 중
    **135건에 `"["` 한 글자만** 돌려주고 `finishReason=stop` 으로 끝냈다. 그것을 조용히
    빈 목록으로 접었다면 *"뽑을 것이 없었다"* 로 보고돼 **Δ=0 이 데이터의 성질이 아니라
    깨진 도구의 성질**이 됐을 것이다. 사유가 남아 있어서 잡았다."""
    from scripts.build_nary import parse_relations

    assert parse_relations("[") == ([], "no_json")
    assert parse_relations("") == ([], "empty")
    assert parse_relations("{") == ([], "no_json")
    # 진짜 빈 결과는 사유가 **없다**. 둘이 같은 값이면 구분할 수단이 사라진다.
    assert parse_relations('{"relations": []}') == ([], "")


def test_the_parser_takes_both_the_object_and_the_bare_array_shape():
    """객체로 감싼 것은 위 결함의 처방이다. 옛 배열 형식도 그대로 읽어야 산출물 형식이
    갈리지 않는다."""
    from scripts.build_nary import parse_relations

    assert parse_relations('{"relations":[{"amount":1}]}') == ([{"amount": 1}], "")
    assert parse_relations('[{"amount":1}]') == ([{"amount": 1}], "")
    assert parse_relations('```json\n{"relations":[{"amount":1}]}\n```') == (
        [{"amount": 1}], "")


def test_the_arity_precheck_is_deterministic_and_needs_no_llm():
    """지출 **전에** 도는 판정이다. HyperGraphRAG 자신이 *"arity ≤ 2 면 이득 0"*
    을 보고했으므로 이 수가 `A18` 을 재는 이유를 정한다."""
    from scripts.build_nary import arity_of

    record = {"raw_rows": [["1. 계약상대", "삼성전자"],
                           ["2. 계약금액(원)", "1,000,000,000"],
                           ["3. 계약기간", "2023-01-01 ~ 2024-12-31"]]}
    assert arity_of(record) == (4, ["counterparty", "amount", "period"])
    #: 라벨만 있고 값이 `-` 인 유보 서식은 **세지 않는다**. 세면 arity 가 통째로 부풀려진다.
    withheld = {"raw_rows": [["1. 계약상대", "삼성전자"], ["2. 계약금액(원)", "-"]]}
    assert arity_of(withheld) == (2, ["counterparty"])

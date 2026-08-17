"""`scripts/build_service_data.py`, 서비스 데이터의 계약과 보안 게이트를 고정한다.

LLM **0회** · 크레딧 **0원**. 빌드는 `artifacts/` 의 **공시 원본**과 `universe.csv` 만
읽어 `site/public/data/` 아래 JSON 을 낸다.

이 파일이 지키는 것은 넷이다.

    ① 빌드가 **공시 원본에서만** 온다        `evalset/`·`artifacts/ablation/` 를 안 연다
    ② 화면에 **실험 어휘가 새지 않는다**      산출물 전량을 훑는다
    ③ 산출물이 **스펙 §2 의 실측과 같다**     70개사 · 정정 631 · 노드 145 · 쌍 44
    ④ **추정하지 않는다**                    값이 없으면 `null` 이고, 그 자리는 비어 있다

`tests/test_site.py`(v1) 가 쓰던 방식을 이었다. **게이트는 발화를 확인해야 게이트다.**
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_service_data.py"
DATA = ROOT / "site" / "public" / "data"

#: 스펙 §2 의 실측(2026-08-15). **이 수는 이 저장소의 코퍼스가 고정이라 상수다.**
#: 어긋나면 코퍼스가 바뀌었다는 뜻이고, 그때는 스펙과 이 표를 **함께** 고친다.
EXPECTED = {
    "companies": 70,
    "contracts": 1469,
    "corrections": 631,
    "nodes": 145,
    "pairs": 44,
    "hubs_2plus": 25,
}


# ─────────────────────────────────────────────────────────────────────────────
# 0. 빌드를 한 번만 돌린다
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def built() -> pathlib.Path:
    subprocess.run([sys.executable, str(SCRIPT)], check=True, cwd=ROOT)
    return DATA


def _load(built: pathlib.Path, name: str):
    return json.loads((built / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def meta(built):
    return _load(built, "meta.json")


@pytest.fixture(scope="session")
def companies(built):
    return _load(built, "companies.json")


@pytest.fixture(scope="session")
def corrections(built):
    return _load(built, "corrections.json")


@pytest.fixture(scope="session")
def network(built):
    return _load(built, "network.json")


# ─────────────────────────────────────────────────────────────────────────────
# 1. 계획서가 요구한 넷
# ─────────────────────────────────────────────────────────────────────────────


def test_build_produces_the_five_files(built):
    for name in ("meta.json", "companies.json", "corrections.json", "network.json"):
        assert (built / name).exists(), f"{name} 이 없다"
    assert (built / "company").is_dir()


def test_builder_never_reads_the_evalset_or_ablation():
    """서비스 데이터는 공시 원본에서만 온다."""
    src = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("evalset", "ablation", "heldout", "from argus", "import argus"):
        assert forbidden not in src, f"빌드 스크립트가 {forbidden!r} 를 건드린다"


def test_output_carries_no_research_vocabulary(built):
    """화면에 실험 어휘가 새면 안 된다."""
    banned = ("정답률", "ablation", "기여도", "judge", "코호트", "골든")
    for path in built.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{path.name} 에 {word!r} 가 있다"


def test_builder_imports_only_the_standard_library():
    """규율 ①, `src/argus` 를 import 하지 않는다. `config.py` 가 `.env` 경로를 안다."""
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    allowed = {"annotations", "argparse", "collections", "csv", "datetime", "json",
               "pathlib", "re", "sys", "typing", "__future__"}
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert name.split(".")[0] in allowed, f"표준 라이브러리 밖 import — {name!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 보안 게이트, 발화를 확인해야 게이트다
# ─────────────────────────────────────────────────────────────────────────────


def test_read_gate_refuses_the_paths_the_service_must_never_open():
    """문서가 아니라 **코드**가 막는다 (스펙 §5b a)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("bsd", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for rel in ("evalset/questions.jsonl", ".env", "src/argus/config.py",
                "artifacts/" + "ablation/v4/summary.json", "secrets/key.txt",
                "../outside.json"):
        with pytest.raises(mod.ForbiddenRead):
            mod.read_text(rel)


def test_read_gate_allows_the_disclosure_corpus():
    import importlib.util

    spec = importlib.util.spec_from_file_location("bsd", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.read_text("artifacts/counterparty_graph.json")


def test_unknown_source_field_kills_the_build():
    """규율 ③, 모르는 필드가 붙으면 빌드가 죽는다. 블랙리스트는 조용히 샌다."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("bsd", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(SystemExit):
        mod.gate({"rcept_no": "1", "새로운_필드": 1}, frozenset({"rcept_no"}), "테스트")
    assert mod.gate({"rcept_no": "1"}, frozenset({"rcept_no"}), "테스트")


def test_no_secret_shaped_string_reaches_the_output(built):
    """스펙 §5b b, import 금지와 **독립된 두 번째 층**이다."""
    import re

    patterns = (re.compile(r"nv-[A-Za-z0-9]{8,}"), re.compile(r"sk-[A-Za-z0-9]{8,}"),
                re.compile(r"CLOVA_API_KEY|CLOVA_STUDIO_API_KEY"),
                re.compile(r"X-NCP-[A-Za-z0-9-]+"))
    for path in built.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not pattern.search(text), f"{path.name} 에 키 형태 — {pattern.pattern}"


def test_no_corpus_path_or_public_ip_is_republished(built):
    """스펙 §5b, **읽기 금지와 출력 금지는 다른 게이트다.** universe.csv 는 읽지만 안 싣는다."""
    import re

    ip = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
    for path in built.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for token in ("dart-disclosures", "periodic_sections.txt", "universe.csv"):
            assert token not in text, f"{path.name} 이 코퍼스 경로를 싣는다 — {token!r}"
        assert not ip.search(text), f"{path.name} 에 IP 형태가 있다"


# ─────────────────────────────────────────────────────────────────────────────
# 3. 스펙 §2 의 실측과 대조한다
# ─────────────────────────────────────────────────────────────────────────────


def test_companies_are_the_seventy(companies):
    assert len(companies) == EXPECTED["companies"]
    assert len({c["slug"] for c in companies}) == EXPECTED["companies"], "slug 가 겹친다"


def test_corrections_match_the_measured_total(corrections, meta):
    assert len(corrections) == EXPECTED["corrections"]
    assert meta["counts"]["corrections"] == EXPECTED["corrections"]
    assert meta["counts"]["contracts"] == EXPECTED["contracts"]


def test_network_matches_the_measured_graph(network, meta):
    assert len(network["hubs"]) == EXPECTED["nodes"]
    assert len(network["pairs"]) == EXPECTED["pairs"]
    assert sum(1 for h in network["hubs"] if len(h["partners"]) >= 2) \
        == EXPECTED["hubs_2plus"]
    assert meta["counts"]["partners"] == EXPECTED["nodes"]


def test_slugs_are_url_safe(companies):
    """한글 회사명을 경로에 그대로 쓰지 않는다."""
    for c in companies:
        assert c["slug"].isascii() and c["slug"].replace("-", "").isalnum(), c["slug"]


def test_every_company_has_its_own_file(built, companies):
    for c in companies:
        assert (built / "company" / f"{c['slug']}.json").exists(), c["name"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. 추정하지 않는다
# ─────────────────────────────────────────────────────────────────────────────


#: 정기공시의 「요약재무정보」에서 **눈으로 대조한** 값(2026-08-16).
#: 원문 표의 행과 단위를 사람이 직접 읽어 확인했다. 파서가 조용히 어긋나면 여기서 깨진다.
VERIFIED_FINANCIALS = {
    #: 삼성전자 · 백만원 단위 표 · `매출액 133,873,444` · `영업이익 57,232,797`
    "삼성전자": (133_873_444_000_000, 57_232_797_000_000),
    #: 대우건설 · **원** 단위 표 · `매출액 1,951,410,038,476` · `영업이익(손실) 255,631,837,181`
    "대우건설": (1_951_410_038_476, 255_631_837_181),
    #: 한화에어로스페이스 · 백만원 · `매출액 5,751,023` · `영업이익(영업손실) 638,892`
    "한화에어로스페이스": (5_751_023_000_000, 638_892_000_000),
}


def _detail_by_name(built, companies, name):
    slug = next(c["slug"] for c in companies if c["name"] == name)
    return _load(built, f"company/{slug}.json")


@pytest.mark.parametrize("name", sorted(VERIFIED_FINANCIALS))
def test_financials_match_the_document_that_was_read_by_hand(built, companies, name):
    """단위 환산이 어긋나면 매출이 백만 배 틀린다. 세 회사를 손으로 대조해 못박는다."""
    revenue, operating = VERIFIED_FINANCIALS[name]
    financials = _detail_by_name(built, companies, name)["financials"]
    assert financials is not None, f"{name} 의 재무 요약이 비었다"
    assert financials["revenue"] == revenue
    assert financials["operating_income"] == operating


def test_financials_are_either_labelled_or_empty(built, companies):
    """**기간을 모르는 매출액은 뜻이 없다**. 분기 매출을 연간으로 읽으면 네 배 틀린다.

    그래서 값을 실을 때는 기간·기준(연결/별도)·출처 접수번호가 반드시 함께 온다.
    못 갖추면 통째로 `None` 이다. 어림수도 0도 쓰지 않는다.
    """
    filled = 0
    for c in companies:
        financials = _load(built, f"company/{c['slug']}.json")["financials"]
        if financials is None:
            continue
        filled += 1
        assert set(financials) == {"period", "basis", "revenue", "operating_income",
                                   "source_rcept_no"}, c["name"]
        assert financials["basis"] in {"연결", "별도"}, c["name"]
        assert re.search(r"20\d\d", financials["period"]), c["name"]
        assert financials["revenue"] > 0, c["name"]
        assert financials["source_rcept_no"].isdigit(), c["name"]
        operating = financials["operating_income"]
        #: 영업이익이 매출을 넘으면 엉뚱한 칸을 집었다는 뜻이다. 그런 짝은 싣지 않는다.
        assert operating is None or abs(operating) <= financials["revenue"], c["name"]
    assert filled, "재무 요약이 하나도 안 뽑혔다 — 파서가 죽었는지 보라"
    assert filled < len(companies), "🔴 전부 뽑혔다 — 못 뽑은 것을 지어내고 있지 않은지 보라"


def test_the_body_of_the_periodic_corpus_is_never_republished(built):
    """926MB 본문에서 가져오는 것은 **수 둘과 기간 표기**뿐이다.

    산출물의 재무 칸에 문장이 실리면 코퍼스 재배포다. 기간 표기는 40자를 넘지 않는다.
    """
    for path in (built / "company").glob("*.json"):
        financials = json.loads(path.read_text(encoding="utf-8"))["financials"]
        if financials:
            assert len(financials["period"]) <= 40, path.name


def test_unresolved_corrections_are_kept_and_marked(corrections):
    """감추면 목록이 실제보다 완전해 보인다 (스펙 §4.3)."""
    unresolved = [c for c in corrections if not c["resolved"]]
    assert unresolved, "추적 실패가 하나도 없다 — 정말인가?"
    for c in unresolved:
        assert c["targets"] == []
        assert c["reason"], "추적 실패에 사유가 없다"
        assert c["amount_before"] is None, "원공시를 못 찾았는데 이전 금액이 있다"


def test_amount_diff_is_never_estimated(corrections):
    """`amount_before` 는 **원공시에서 읽은 값**이거나 `null` 이다."""
    for c in corrections:
        if c["amount_before"] is not None:
            assert c["resolved"] and c["targets"], c["rcept_no"]
            assert c["origin"] in c["targets"], "이전 금액을 어느 공시에서 읽었는지 없다"


def test_amount_diff_only_pairs_disclosures_about_the_same_contract(built, corrections):
    """**2026-08-16 의 결함을 고정한다.**

    첫 구현은 `exchange_records` 의 `related` 로 원공시를 찾았다. 그 필드는 정정 체인이
    아니라 *관련공시 묶음*이라 **같은 회사의 다른 사업끼리 이어졌고**, 삼성E&A 에서
    `883,888,500,000 → 1,770,780,000,000` 이라는 **없는 금액 변화**가 나왔다.
    두 공시는 각각 `P4 Ph2(하동) 그린동 변전소` 와 `P5 Project` 로 **다른 계약**이었다.

    금액 변화를 말하려면 **같은 계약**이어야 한다.
    """
    by_rcept = {}
    for path in (built / "company").glob("*.json"):
        for row in json.loads(path.read_text(encoding="utf-8"))["contracts"]:
            by_rcept[row["rcept_no"]] = row

    def key(title: str | None) -> str:
        import re

        return re.sub(r"[\s\-—·,.()\[\]]+", "", title or "").lower()

    for c in corrections:
        if c["amount_before"] is None or c["origin"] is None:
            continue
        origin = by_rcept.get(c["origin"])
        if origin is None:  # 원공시가 다른 회사 파일에 있을 수 있다
            continue
        assert key(origin["title"]) == key(c["title"]), (
            f"🔴 다른 계약끼리 금액을 비교했다 — {c['corp_name']} "
            f"{c['title']!r} vs {origin['title']!r}"
        )


def test_ambiguous_chains_keep_their_candidates_but_drop_the_amount(corrections):
    """후보가 여럿이면 금액을 **비운다**. 아무거나 고르면 없는 변화를 만든다."""
    ambiguous = [c for c in corrections if c["resolved"] and c["origin"] is None]
    assert ambiguous, "모호한 체인이 하나도 없다 — 분기가 죽은 코드인가?"
    for c in ambiguous:
        assert c["targets"], "후보가 없는데 추적됐다고 했다"
        assert c["amount_before"] is None
        assert c["reason"] == "ambiguous_targets"


def test_the_builder_does_not_use_the_related_cluster_as_a_chain():
    """`related` 로 되돌아가지 마라, 위 테스트가 잡은 결함의 원인이다.

    필드 이름은 원본 화이트리스트(`SOURCE_EXCHANGE`)에 **적혀 있어야** 한다. 원본이
    들고 있는 필드니까. 금지하는 것은 **그 값을 읽는 것**이다.
    """
    import re

    src = SCRIPT.read_text(encoding="utf-8")
    reads = re.findall(r"""(?:\[\s*|\.get\(\s*)["']related["']""", src)
    assert not reads, f"빌드가 `related` 값을 읽는다 ({len(reads)}곳)"


def test_partner_counts_are_null_when_they_cannot_be_verified(built, companies):
    """셀 수 없는 것은 `null` 이다. 0 으로도 어림수로도 쓰지 않는다."""
    seen_null = seen_int = False
    for c in companies:
        for p in _load(built, f"company/{c['slug']}.json")["partners"]:
            assert p["contracts"] is None or isinstance(p["contracts"], int)
            seen_null |= p["contracts"] is None
            seen_int |= isinstance(p["contracts"], int)
    assert seen_int, "셀 수 있는 것도 못 셌다"
    assert seen_null, "🔴 전부 셌다면 null 분기가 죽은 코드다 — 확인하라"


# ─────────────────────────────────────────────────────────────────────────────
# 5. 산출물의 내부 정합
# ─────────────────────────────────────────────────────────────────────────────


def test_company_totals_sum_to_the_meta_counts(companies, meta):
    assert sum(c["n_corrections"] for c in companies) == meta["counts"]["corrections"]
    assert sum(c["n_disclosures"] for c in companies) == meta["counts"]["disclosures"]


def test_every_correction_points_at_a_company_page(corrections, companies):
    slugs = {c["slug"] for c in companies}
    for c in corrections:
        assert c["slug"] in slugs, f"{c['corp_name']} 의 회사 페이지가 없다"


def test_network_partners_are_all_companies_we_have_a_page_for(network, companies):
    slugs = {c["name"] for c in companies}
    for hub in network["hubs"]:
        for p in hub["partners"]:
            assert p in slugs, f"관계망이 우리가 모르는 회사를 가리킨다 — {p}"


def test_meta_range_is_read_from_the_data_not_hardcoded(meta):
    assert meta["ranges"]["companies"] == EXPECTED["companies"]
    assert meta["ranges"]["from"] == "2023-01"
    assert meta["ranges"]["to"] == "2026-03"
    assert meta["generated_at"]


def test_contract_rows_carry_only_whitelisted_fields(built, companies):
    allowed = {"rcept_no", "rcept_dt", "amount", "amount_type", "counterparty",
               "period_start", "period_end", "is_correction", "doc_subtype", "title"}
    for c in companies:
        for row in _load(built, f"company/{c['slug']}.json")["contracts"]:
            assert not set(row) - allowed, f"{c['name']} 계약 행에 모르는 필드: {set(row)-allowed}"


def test_build_is_reproducible_apart_from_the_timestamp(built):
    before = {p.name: p.read_text(encoding="utf-8") for p in built.rglob("*.json")}
    subprocess.run([sys.executable, str(SCRIPT)], check=True, cwd=ROOT)
    after = {p.name: p.read_text(encoding="utf-8") for p in built.rglob("*.json")}
    assert set(before) == set(after)
    for name in before:
        if name == "meta.json":
            continue
        assert before[name] == after[name], f"{name} 이 두 번 돌리면 달라진다"

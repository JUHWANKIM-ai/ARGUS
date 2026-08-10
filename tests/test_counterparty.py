"""counterparty 표기 실태 프로파일 — .superpowers/sdd/.../task-1-brief.md.

노드 승격 전에 무엇을 정규화해야 하는지 산출물로 고정한다. 정규화 규칙 자체는
scripts/validate_evalset.py::normalize_counterparty 가 정본이고 여기서는 재검증하지 않는다.

`artifacts/` 는 gitignore 대상이라 이 산출물은 git 에 없다(리뷰 결정: 다른 eda 산출물과
같은 관행을 따름). 그래서 신규 클론·격리 워크트리·CI 에서는 파일이 없을 수 있다 —
`ensure_profile` fixture 가 exchange_records.jsonl 로 값싸게 재생성하거나, 그것마저
없으면 사유를 밝히고 skip 한다.
"""
import json
from pathlib import Path

import pytest

from scripts.eda_counterparty import RECORDS_PATH, build_profile
from scripts.build_counterparty_graph import BOILERPLATE_NOTE, build_graph

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "artifacts/eda/counterparty_profile.json"
GRAPH_PATH = ROOT / "artifacts/counterparty_graph.json"


@pytest.fixture(autouse=True)
def ensure_profile():
    """산출물이 없으면 값싸게 재생성한다. 있으면 그대로 두고 다시 만들지 않는다."""
    if PROFILE_PATH.exists():
        return
    if not RECORDS_PATH.exists():
        pytest.skip(
            f"{RECORDS_PATH} 이 없어 counterparty_profile.json 을 재생성할 수 없다 — "
            "exchange_records.jsonl 을 먼저 만들어야 한다 (원본 5.2GB 필요)."
        )
    build_profile(PROFILE_PATH)


@pytest.fixture(autouse=True)
def ensure_graph():
    """산출물이 없으면 값싸게 재생성한다 — `ensure_profile` 과 같은 패턴.

    `artifacts/` 는 gitignore 대상이라 신규 클론·격리 워크트리·CI 에서는 파일이 없을 수
    있다. `build_graph()` 는 `exchange_records.jsonl` 만 있으면 재생성 비용이 싸다
    (1,469줄 스캔, LLM 호출 없음).
    """
    if GRAPH_PATH.exists():
        return
    if not RECORDS_PATH.exists():
        pytest.skip(
            f"{RECORDS_PATH} 이 없어 counterparty_graph.json 을 재생성할 수 없다 — "
            "exchange_records.jsonl 을 먼저 만들어야 한다 (원본 5.2GB 필요)."
        )
    build_graph(GRAPH_PATH)


from argus.corpus import counterparty as cp


def test_canonical_merges_known_surface_variants():
    assert cp.canonical("대한민국 방위사업청") == cp.canonical("방위사업청")
    assert cp.canonical("(주)한국수력원자력") == cp.canonical("한국수력원자력")
    assert cp.canonical("Torp Co., Ltd.") == cp.canonical("TORP")


def test_classify_separates_public_from_vague():
    assert cp.classify("방위사업청") == "public"
    assert cp.classify("한국수력원자력") == "public"
    assert cp.classify("삼성전자") == "universe"
    assert cp.classify("글로벌 대형기업") == "vague"
    assert cp.classify("오세아니아지역 선주") == "vague"


def test_vague_strings_never_become_nodes():
    """비실명은 노드가 되면 안 된다 — 서로 다른 실체가 한 노드로 뭉친다.

    브리프 원안은 "국내 대형사"·"해외 법인" 을 예로 들었으나 실제 `ANON_COUNTERPARTY`
    정규식(scripts/validate_evalset.py 가 정본)은 이 두 표기를 잡지 않는다 — 새로
    발명하지 않기로 했으므로 정규식은 그대로 두고, 대신 실제 exchange_records.jsonl 에
    있는 표기로 바꿔 썼다(둘 다 실측 확인).
    """
    for s in ["글로벌 대형기업", "글로벌 완성차 업체", "아시아지역 선주", "유럽 소재 제약사"]:
        assert cp.classify(s) == "vague"


def test_real_company_with_globalword_substring_is_not_vague():
    """리뷰 라운드 2 결함① — `(주)청주글로벌` 은 실명 회사인데 `글로벌` 서브스트링 때문에
    vague 로 분류돼 노드에서 탈락했다(실측: exchange_records.jsonl 유일 사례).
    `글로벌`은 "글로벌 대형기업"처럼 **단어 앞머리 수식어**로 쓰일 때만 비실명 신호이고,
    "청주글로벌"처럼 고유명사 뒤에 붙으면 그냥 회사 이름 일부다.
    """
    assert not cp.ANON_COUNTERPARTY.search("(주)청주글로벌")
    assert cp.classify("(주)청주글로벌") != "vague"
    # 진짜 비실명 "글로벌 X" 패턴은 여전히 잡혀야 한다 (과잉 수정 방지 회귀).
    assert cp.ANON_COUNTERPARTY.search("글로벌 대형기업")
    assert cp.ANON_COUNTERPARTY.search("글로벌 완성차 업체")


def test_region_plus_shipping_company_is_recognised_as_vague():
    """리뷰 라운드 2 결함② — `지역\\s*선주`·`소재\\s*선사` 는 있는데 `지역\\s*선사` 가 없어
    "미주 지역 선사"(실측: exchange_records.jsonl 유일 사례) 가 노드로 승격될 뻔했다.
    """
    assert cp.ANON_COUNTERPARTY.search("미주 지역 선사")
    assert cp.classify("미주 지역 선사") == "vague"


def test_classify_public_matches_agency_name_as_prefix():
    """리뷰 라운드 2 결함③ — `classify` 의 `public` 판정이 완전일치라 하위조직명이 붙은
    표기(`산림청산림항공본부`, 실측 1건)를 못 잡았다. 접두 일치로 바꿨다 — 전량 검증으로
    `국방부`(3음절) 같은 짧은 힌트가 다른 나라 국방부(루마니아국방부 등 7건)를 새로
    오탐하지 않는지 확인했다(접두일치는 그 7건과 무관 — 전부 "국방부"로 시작하지 않는다).
    """
    assert cp.classify("산림청산림항공본부") == "public"
    # 완전일치로도 잡히던 것들이 접두일치에서 깨지면 안 된다 (회귀).
    assert cp.classify("방위사업청") == "public"
    assert cp.classify("한국토지주택공사") == "public"
    # 접두일치가 과하게 넓어져 외국 기관을 새로 오탐하면 안 된다.
    assert cp.classify("루마니아국방부") != "public"
    assert cp.classify("필리핀국방부") != "public"


def test_counterparty_profile_records_surface_form_variants():
    """같은 발주처의 표기 변이가 한 canonical 아래 묶여야 한다."""
    p = ROOT / "artifacts/eda/counterparty_profile.json"
    prof = json.loads(p.read_text(encoding="utf-8"))

    by_canon = {v["canonical"]: v for v in prof["variants"]}
    # 실측 확인된 변이 — 방위사업청은 최소 두 표기로 등장한다
    assert "방위사업청" in by_canon
    forms = by_canon["방위사업청"]["surface_forms"]
    assert len(forms) >= 2, f"표기 변이가 1개뿐이면 정규화가 안 된 것: {forms}"
    assert by_canon["방위사업청"]["count"] >= 40

    # 1회 등장이 절반을 넘는다는 사실이 기록돼야 한다
    assert prof["singleton_ratio"] > 0.5


# ── Task 3: counterparty 2-hop 그래프 ──────────────────────────────────────


def test_graph_excludes_singletons_and_vague():
    """1회 등장(56.8%)과 비실명은 노드가 되지 않는다."""
    g = json.loads((ROOT / "artifacts/counterparty_graph.json").read_text(encoding="utf-8"))
    assert g["stats"]["min_count"] == 2
    for n in g["nodes"]:
        assert n["count"] >= 2
        assert n["kind"] != "vague"


def test_graph_reproduces_measured_value_chains():
    """실측으로 확인된 밸류체인이 그대로 나와야 한다."""
    g = json.loads((ROOT / "artifacts/counterparty_graph.json").read_text(encoding="utf-8"))
    by_canon = {n["canonical"]: set(n["partners"]) for n in g["nodes"]}

    from argus.corpus.counterparty import canonical

    khnp = by_canon[canonical("한국수력원자력")]
    assert {"두산에너빌리티", "한전기술", "현대건설"} <= khnp

    dapa = by_canon[canonical("방위사업청")]
    assert {"한화오션", "HD현대중공업", "한국항공우주"} <= dapa
    # 변이 5조각이 합쳐져야 91건이 된다 — 안 합치면 어느 조각도 허브가 못 된다
    assert next(n for n in g["nodes"] if n["canonical"] == canonical("방위사업청"))["count"] >= 80


def test_pairs_record_the_intermediary():
    """어느 발주처를 거쳐 이어졌는지 남아야 근거로 인용할 수 있다.

    브리프 기대값은 `nodes >= 157` 였으나 실측(B1 보일러플레이트 필터 적용 후)은
    **145** 다. 157은 "계약상대는 경영상 비밀유지 사유로 …" 류 안내문 11개가 실수로
    노드가 된 오염 수치였다 — B1 필터 없이 돌리면 정확히 156개가 나오고(157에도 못
    미친다), 그 156개 중 11개를 눈으로 확인하면 전부 회사명이 아니라 안내문 문장이다.
    `pairs`는 44로 브리프값과 일치한다 — 오염 노드 11개는 전부 partners 0~1개라
    pairs 생성에 관여하지 않았다(순수하게 node 수만 오염시켰다).

    리뷰(라운드 1)에서 `nodes >= 140` 처럼 느슨한 하한은 회귀 탐지력이 떨어진다는
    지적을 받았다 — `exchange_records.jsonl` 은 정적 산출물(고정 스냅샷)이라 값이
    바뀔 이유가 없으므로 실측값으로 **정확히 고정**한다. 이 값이 바뀌면 원본이
    바뀐 것이니 재측정해서 상수를 다시 박아야 한다(하한을 슬쩍 늘리면 안 된다).
    """
    g = json.loads((ROOT / "artifacts/counterparty_graph.json").read_text(encoding="utf-8"))
    assert g["stats"]["pairs"] == 44         # 직접 20쌍 → 2-hop 44쌍 (실측, 고정)
    assert g["stats"]["nodes"] == 145        # 실측 고정. 브리프의 157은 B1 오염 포함 수치였다
    for p in g["pairs"]:
        assert p["via"], f"중개 노드가 비어 있다: {p}"


def test_graph_matches_the_evalset_normalizer():
    """문항 검증(C12)과 런타임이 같은 정규화를 써야 한다.

    갈리면 `validate_evalset` 이 통과시킨 문항을 파이프라인이 못 찾는다.
    """
    import scripts.validate_evalset as V
    from argus.corpus.counterparty import canonical
    for s in ("대한민국 방위사업청", "㈜한국수력원자력", "주식회사 유토개발2차",
              "한국토지주택공사(LH)", "(주)포스코이앤씨"):
        assert canonical(s) == V.normalize_counterparty(s)


def test_boilerplate_secrecy_notices_are_excluded_from_nodes():
    """B1 — "계약상대는 경영상 비밀유지 사유로 …" 안내문 전체가 counterparty 값으로
    들어간 사례(실측 27종 표기·34건)는 회사명이 아니다. 같은 문장이 반복되면
    canonical()이 겹쳐 count>=2 로 노드가 될 뻔했다 — 실측: 필터 없이 돌리면 정확히
    이런 문장 11개가 노드로 승격된다(각각 count=2, partners 0~1개).

    리뷰(라운드 1) 지적대로 `>= 20` 은 느슨해서 회귀 탐지력이 떨어진다 — 정적
    스냅샷이라 값이 안 바뀌므로 실측값 27로 **정확히 고정**한다.
    """
    g = json.loads((ROOT / "artifacts/counterparty_graph.json").read_text(encoding="utf-8"))
    for n in g["nodes"]:
        assert not BOILERPLATE_NOTE.search(n["display"]), (
            f"보일러플레이트 안내문이 노드로 승격됐다: {n['display']!r}"
        )
    # 거른 것은 조용히 버리지 않고 사유와 함께 기록한다.
    assert g["stats"]["boilerplate_excluded_unique"] == 27
    assert g["stats"]["boilerplate_excluded_occurrences"] >= g["stats"]["boilerplate_excluded_unique"]
    assert len(g["excluded_boilerplate"]) == g["stats"]["boilerplate_excluded_unique"]
    for item in g["excluded_boilerplate"]:
        assert item["raw"] and item["count"] >= 1 and item["reason"]


def test_boilerplate_filter_does_not_cut_real_company_names():
    """B1 필터가 실명 회사를 자르면 안 된다 — exchange_records.jsonl 유일 원문 462건
    전량 대조로 확인한다. 길이가 긴 실명 표기(예: 국가명+기관 영문 병기)가 오탐되지
    않아야 한다.
    """
    real_long_names = [
        "China National Chemical Engineering & Construction Corporation Seven, Ltd.",
        "타이중시 정부 도시철도공정국(Rapid Transit System Bureau of Taichung City Government)",
        "뭄바이광역개발청 (Mumbai Metropolitan Region Development Authority)",
        "브라질 상파울루 메트로(COMPANHIA DO METROPOLITANO DE SAO PAULO-METRO)",
        "방위사업청 (Defense Acquisition Program Administration)",
        "한국항공우주산업㈜ (KOREA AEROSPACE INDUSTRIES, LTD.)",
    ]
    for s in real_long_names:
        assert not BOILERPLATE_NOTE.search(s), f"실명 회사가 오탐됐다: {s!r}"


def test_country_plus_generic_noun_hubs_are_reported_not_silently_promoted():
    """B2 — "사우디 소재 EPC"·"중동지역국가 국방부" 류(국가/지역 + 일반명사)가 실제
    코퍼스에 존재한다. `classify()`는 고치지 않았으므로(Task 2 결정 유지) 이 값들이
    노드로 올라오는지만 확인한다.

    실측: "중동지역국가 국방부"는 count=2로 노드가 되지만 partners는 1개
    (한화에어로스페이스)뿐이라 pairs를 생성하지 않는다 — 아직 서로 다른 실체를
    합치는 오염은 없다. "사우디 소재 EPC"는 count=1이라 min_count 문턱을 못 넘는다.
    반면 "필리핀 국방부"·"인도네시아 국방부"는 형태는 같아도 특정 가능한 단일
    실체(그 나라의 국방부)라 2개사 이상의 정당한 허브로 남는다.
    """
    g = json.loads((ROOT / "artifacts/counterparty_graph.json").read_text(encoding="utf-8"))
    by_canon = {n["canonical"]: n for n in g["nodes"]}

    from argus.corpus.counterparty import canonical

    mideast = by_canon.get(canonical("중동지역국가 국방부"))
    assert mideast is not None, "실측과 다르다 — '중동지역국가 국방부'가 더 이상 노드가 아니다"
    assert len(mideast["partners"]) <= 1, (
        "여러 universe 기업이 같은 '중동지역국가 국방부'에 걸렸다 — 서로 다른 실체가 "
        f"한 노드로 합쳐졌을 위험: {mideast['partners']}"
    )
    assert canonical("사우디 소재 EPC") not in by_canon, (
        "실측과 다르다 — '사우디 소재 EPC'가 count>=2로 승격됐다. partners 수를 확인해야 한다"
    )

    philippines = by_canon.get(canonical("필리핀 국방부"))
    assert philippines is not None
    assert set(philippines["partners"]) >= {"HD현대중공업", "한국항공우주"}

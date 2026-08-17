"""counterparty 표기 실태 프로파일, 정본 `docs/pipeline/03-graph-schema.md`.

노드 승격 전에 무엇을 정규화해야 하는지 산출물로 고정한다. 정규화 규칙 자체는
scripts/validate_evalset.py::normalize_counterparty 가 정본이고 여기서는 재검증하지 않는다.

`artifacts/` 는 gitignore 대상이라 이 산출물은 git 에 없다(리뷰 결정: 다른 eda 산출물과
같은 관행을 따름). 그래서 신규 클론·격리 워크트리·CI 에서는 파일이 없을 수 있다.
`ensure_profile` fixture 가 exchange_records.jsonl 로 값싸게 재생성하거나, 그것마저
없으면 사유를 밝히고 skip 한다.
"""
import json
from pathlib import Path

import pytest

from scripts.eda_counterparty import RECORDS_PATH, build_profile
from scripts.build_counterparty_graph import BOILERPLATE_NOTE, build_graph, sentence_like

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
    """산출물이 없으면 값싸게 재생성한다. `ensure_profile` 과 같은 패턴.

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


#: B/I-12, `_SUFFIX` 가 앵커 없이 적용돼 회사명 **내부**를 지웠다. 왼쪽이 원문,
#: 오른쪽이 종전 훼손 결과다. 이 표가 회귀 게이트다.
SUFFIX_DAMAGE_BEFORE_FIX = {
    "HD Hyundai Electric America Corporation": "hdhyundaielectricamericaoration",
    "Tanzania Railways Corporation": "tanzaniarailwaysoration",
    "Taipei Rapid Transit Corporation": "taipeirapidtransitoration",
    "Baxter Healthcare Corporation": "baxterhealthcareoration",
    "Lincoln Electric": "lolnelectric",                 # L-inc-oln
    "Incheon Energy": "heonenergy",                     # Inc-heon
    "Corning Incorporated": "corningorporated",
    "SWORDS LABORATORIES UNLIMITED COMPANY": "swordslaboratoriesuncompany",  # UN-limited
    "Princeton Advisory": "pretonadvisory",             # Pr-inc-eton
    "Sinclair Broadcast": "slairbroadcast",             # S-inc-lair
}


def test_canonical_does_not_strip_legal_suffixes_inside_words():
    """접미사 제거가 단어 내부에 적용되면 두 가지 피해가 난다.

    ① **엔티티 분열**, 같은 회사가 `CORP.` / `Corporation` 으로 적히면 다른 노드가 된다.
    ② **엔티티 오병합**, `Incheon`·`Lincoln` 처럼 `inc` 를 품은 이름이 잘려 서로 다른
       두 회사가 같은 키로 접힐 수 있다. 노드가 잘못 합쳐지면 `via` 가 **없는 밸류체인
       연결을 근거로 인용**한다. 우리 시스템에서 가장 나쁜 종류의 결함이다.

    실측(exchange_records.jsonl 462종 전량)으로는 아직 오병합 0건이지만
    (그래서 노드 145 · 쌍 44 가 이 수정 후에도 그대로다), "인천/Incheon" 은 한국
    공시에 충분히 나올 이름이다. 새 데이터를 기다리지 않고 막는다.
    """
    for raw, damaged in SUFFIX_DAMAGE_BEFORE_FIX.items():
        got = cp.canonical(raw)
        assert got != damaged, f"단어 내부 접미사 제거가 되살아났다: {raw!r} -> {got!r}"
    # 끝에 붙은 정당한 법인격 표지는 여전히 접혀야 한다 (과잉 수정 방지).
    assert cp.canonical("Tanzania Railways Corporation") == "tanzaniarailways"
    assert cp.canonical("Corning Incorporated") == "corning"
    assert cp.canonical("YANG MING MARINE TRANSPORT CORP.") == \
        cp.canonical("Yang Ming Marine Transport Corporation")
    assert cp.canonical("Torp Co., Ltd.") == cp.canonical("TORP")
    # 단어 내부는 온전해야 한다.
    assert cp.canonical("Lincoln Electric") == "lincolnelectric"
    assert cp.canonical("Incheon Energy") == "incheonenergy"


def test_canonical_still_folds_korean_legal_markers_anywhere():
    """리뷰어 처방(문자열 끝 앵커 `(?:[\\s,·]|^)…\\s*$`)을 기각한 근거의 회귀 고정.

    한국 표기는 법인격 표지가 **조사 없이 고유명사에 바로 붙는다**(`삼성전자㈜` ·
    `현대제철주식회사`), 앞에 `[\\s,·]` 가 없어서 끝 앵커가 매치하지 않는다. 실측:
    그 정규식을 쓰면 462종 중 **80종**의 키가 바뀌고 canonical 그룹이 407 → 418 로
    분열하며 `삼성전자㈜`·`두산에너빌리티㈜`·`현대제철주식회사` 가 `universe` → `other`
    로 오분류된다. 그래서 한글 표지는 앵커 없이 그대로 두고 로마자만 경계를 걸었다.
    """
    for raw, key in {
        "삼성전자㈜": "삼성전자", "삼성전자(주)": "삼성전자",
        "두산에너빌리티㈜": "두산에너빌리티", "현대제철주식회사": "현대제철",
        "한국수력원자력 주식회사": "한국수력원자력",
        "주식회사 유토개발2차": "유토개발2차", "주식회사유토개발2차": "유토개발2차",
        "한국항공우주산업㈜ (KOREA AEROSPACE INDUSTRIES, LTD.)": "한국항공우주산업",
    }.items():
        assert cp.canonical(raw) == key, raw
    # 오분류가 되살아나면 여기서 먼저 깨진다.
    assert cp.classify("삼성전자㈜") == "universe"
    assert cp.classify("현대제철주식회사") == "universe"


def test_canonical_never_returns_an_empty_key():
    """빈 키는 서로 다른 원문을 한 노드로 접는다. 오병합의 조용한 경로다.

    실측: 462종 중 `"(삭제)"` 1종이 접기 후 빈 문자열이 됐다(그래프 빌더의
    `BOILERPLATE_NOTE` 가 먼저 걸러 노드 수에는 영향이 없었지만, `classify` 의
    접두일치는 빈 키에서 의미를 잃는다).
    """
    for raw in ("(삭제)", "㈜", "주식회사", "(주)", " (유) ", "Inc."):
        assert cp.canonical(raw) != "", f"{raw!r} 의 canonical 이 빈 문자열이다"
    # 서로 다른 원문이 같은 키로 접히지 않아야 한다.
    assert cp.canonical("(삭제)") != cp.canonical("㈜")


def test_short_real_company_names_are_not_cut_by_a_length_guard():
    """2자 이하를 걸러내는 가드를 넣으면 실명 회사를 잃는다. 넣지 않았다는 회귀 고정.

    `기아(주)` → `기아`(2자) · `주식회사 SR` → `sr`(2자) 는 둘 다 실명 회사다.
    """
    assert cp.canonical("기아(주)") == "기아"
    assert cp.canonical("주식회사 SR") == "sr"
    assert cp.canonical("기아㈜") == cp.canonical("기아")


def test_classify_separates_public_from_vague():
    assert cp.classify("방위사업청") == "public"
    assert cp.classify("한국수력원자력") == "public"
    assert cp.classify("삼성전자") == "universe"
    assert cp.classify("글로벌 대형기업") == "vague"
    assert cp.classify("오세아니아지역 선주") == "vague"


def test_vague_strings_never_become_nodes():
    """비실명은 노드가 되면 안 된다. 서로 다른 실체가 한 노드로 뭉친다.

    브리프 원안은 "국내 대형사"·"해외 법인" 을 예로 들었으나 실제 `ANON_COUNTERPARTY`
    정규식(scripts/validate_evalset.py 가 정본)은 이 두 표기를 잡지 않는다. 새로
    발명하지 않기로 했으므로 정규식은 그대로 두고, 대신 실제 exchange_records.jsonl 에
    있는 표기로 바꿔 썼다(둘 다 실측 확인).
    """
    for s in ["글로벌 대형기업", "글로벌 완성차 업체", "아시아지역 선주", "유럽 소재 제약사"]:
        assert cp.classify(s) == "vague"


def test_real_company_with_globalword_substring_is_not_vague():
    """리뷰 라운드 2 결함①, `(주)청주글로벌` 은 실명 회사인데 `글로벌` 서브스트링 때문에
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
    """리뷰 라운드 2 결함②, `지역\\s*선주`·`소재\\s*선사` 는 있는데 `지역\\s*선사` 가 없어
    "미주 지역 선사"(실측: exchange_records.jsonl 유일 사례) 가 노드로 승격될 뻔했다.
    """
    assert cp.ANON_COUNTERPARTY.search("미주 지역 선사")
    assert cp.classify("미주 지역 선사") == "vague"


def test_classify_public_matches_agency_name_as_prefix():
    """리뷰 라운드 2 결함③, `classify` 의 `public` 판정이 완전일치라 하위조직명이 붙은
    표기(`산림청산림항공본부`, 실측 1건)를 못 잡았다. 접두 일치로 바꿨다. 전량 검증으로
    `국방부`(3음절) 같은 짧은 힌트가 다른 나라 국방부(루마니아국방부 등 7건)를 새로
    오탐하지 않는지 확인했다(접두일치는 그 7건과 무관, 전부 "국방부"로 시작하지 않는다).
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
    # 실측 확인된 변이, 방위사업청은 최소 두 표기로 등장한다
    assert "방위사업청" in by_canon
    forms = by_canon["방위사업청"]["surface_forms"]
    assert len(forms) >= 2, f"표기 변이가 1개뿐이면 정규화가 안 된 것: {forms}"
    assert by_canon["방위사업청"]["count"] >= 40

    # 1회 등장이 절반을 넘는다는 사실이 기록돼야 한다
    assert prof["singleton_ratio"] > 0.5


# ── W3 이후: 걸러야 할 것이 상류에서 사라졌다 ─────────────────────────────────
# **필터가 깨진 것이 아니라 필터할 것이 없어졌다.** `normalize.exchange_body_rows()`
# (W3, Q-099 겨냥)가 exchange **기재정정 표를 필드 추출에서 빼면서**, 정정 표 칸에만
# 있던 안내문·문장이 `counterparty` 값으로 더는 올라오지 않는다.
#
# 실측(2026-08-11, LLM 0회 · `exchange_records.jsonl` 1,469건을 원본 XML 까지 되짚어
# 정정 표 포함/제외 두 판으로 다시 뽑아 대조):
#
#     구분                      정정 표 포함(W3 이전)   정정 표 제외(현행)
#     보일러플레이트 안내문      27종 / 34건            **0종 / 0건**
#     문장형(sentence_like)      14종 / 14건            **3종 / 3건**
#     비실명(vague)              33종 / 196건           **34종 / 207건**  ← 늘었다
#     노드 · 쌍 · 허브           145 · 44 · 25          **145 · 44 · 25** (불변)
#     승격 후보(min_count 전)    342                    **342** (불변)
#
# vague 만 늘어난 것이 이 변화의 서명이다. 정정 표에서 안내문을 읽던 레코드가 이제
# 본문 칸을 읽고, 그 본문 값 11건이 비실명이었다. 노드·쌍·허브·후보 수가 **네 항목 다
# 불변**인 것이 "누출 없음"의 증거다: 사라진 34+11건 중 실명 회사로 승격된 것은 없다.
#
# 아래 세 테스트는 새 실측값을 단언하되 **옛 수를 지우지 않는다.** 옛 수가 다시 나오면
# 그것은 정정 표가 필드 추출로 되돌아왔다는 뜻이고 Q-099 가 함께 깨진다.
BOILERPLATE_BEFORE_W3 = (27, 34)          # (표기 종수, 건수), 정정 표 포함 판
SENTENCE_LIKE_BEFORE_W3 = (14, 14)
VAGUE_BEFORE_W3 = (33, 196)

#: W3 이전에 `counterparty` 로 올라오던 안내문 원문 두 개 (삼성바이오로직스,
#: 접수번호 20230328801361 · 20230629800664). 지금은 정정 표 구간에만 있어 레코드에
#: 안 실리지만, **어휘 필터가 여전히 이것을 잡는지**는 계속 고정한다. 상류가 다시
#: 더러워졌을 때 필터가 이미 녹슬어 있으면 안 된다.
BOILERPLATE_RAWS_BEFORE_W3 = (
    "- 계약상대는 경영상 비밀 유지사유로 2023년 6월 30일 이전 공개될 예정 이며, "
    "해당 일자는 추후 변동 가능함",
    "- 계약상대는 경영상 비밀 유지사유로 2023년 8월 31일 이전 공개될 예정 이며, "
    "해당일자는 추후 변동 가능함",
)


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
    # 변이 5조각이 합쳐져야 91건이 된다. 안 합치면 어느 조각도 허브가 못 된다
    assert next(n for n in g["nodes"] if n["canonical"] == canonical("방위사업청"))["count"] >= 80


def test_pairs_record_the_intermediary():
    """어느 발주처를 거쳐 이어졌는지 남아야 근거로 인용할 수 있다.

    브리프 기대값은 `nodes >= 157` 였으나 실측(B1 보일러플레이트 필터 적용 후)은
    **145** 다. 157은 "계약상대는 경영상 비밀유지 사유로 …" 류 안내문 11개가 실수로
    노드가 된 오염 수치였다. B1 필터 없이 돌리면 정확히 156개가 나오고(157에도 못
    미친다), 그 156개 중 11개를 눈으로 확인하면 전부 회사명이 아니라 안내문 문장이다.
    `pairs`는 44로 브리프값과 일치한다. 오염 노드 11개는 전부 partners 0~1개라
    pairs 생성에 관여하지 않았다(순수하게 node 수만 오염시켰다).

    리뷰(라운드 1)에서 `nodes >= 140` 처럼 느슨한 하한은 회귀 탐지력이 떨어진다는
    지적을 받았다. `exchange_records.jsonl` 은 정적 산출물(고정 스냅샷)이라 값이
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
    """B1: "계약상대는 경영상 비밀유지 사유로 …" 안내문 전체가 counterparty 값으로
    들어간 사례는 회사명이 아니다. 같은 문장이 반복되면 canonical()이 겹쳐 count>=2 로
    노드가 될 뻔했다. 필터 없이 돌리면 정확히 이런 문장 11개가 노드로 승격됐다
    (각각 count=2, partners 0~1개).

    **이 테스트의 기대값이 27 → 0 으로 바뀐 이유는 필터가 아니라 상류다.**
    W3(`normalize.exchange_body_rows()`, 기재정정 표를 필드 추출에서 제외)이 들어간 뒤
    안내문은 `counterparty` 값으로 아예 올라오지 않는다. 27종/34건 → **0종/0건**
    (`BOILERPLATE_BEFORE_W3`, 원본 XML 재대조 실측). 노드·쌍·허브가 145·44·25 로
    불변인 것이 누출 없음의 증거다.

    그래서 두 가지를 함께 단언한다: ① 안내문이 노드로 승격되지 않는다(원래 계약),
    ② 어휘 필터는 여전히 옛 원문을 잡는다(`BOILERPLATE_RAWS_BEFORE_W3`), 상류가
    다시 더러워졌을 때 필터가 녹슬어 있으면 안 되므로 0건이어도 계속 잰다.
    """
    g = json.loads((ROOT / "artifacts/counterparty_graph.json").read_text(encoding="utf-8"))
    for n in g["nodes"]:
        assert not BOILERPLATE_NOTE.search(n["display"]), (
            f"보일러플레이트 안내문이 노드로 승격됐다: {n['display']!r}"
        )
    # W3 이후 실측 고정, 거를 것이 없다. 이 수가 27 로 되돌아오면 정정 표가 필드
    # 추출로 되살아난 것이고, 같은 원인으로 Q-099(902,863,854,765)가 함께 깨진다.
    assert g["stats"]["boilerplate_excluded_unique"] == 0, (
        f"안내문이 상류에서 되살아났다 — W3 이전에는 {BOILERPLATE_BEFORE_W3[0]}종이었다"
    )
    assert g["stats"]["boilerplate_excluded_occurrences"] == 0
    assert g["excluded_boilerplate"] == []
    # 필터 자체는 살아 있어야 한다 (W3 이전 원문으로 확인).
    for raw in BOILERPLATE_RAWS_BEFORE_W3:
        assert BOILERPLATE_NOTE.search(raw), f"어휘 필터가 녹슬었다: {raw!r}"


def test_boilerplate_filter_does_not_cut_real_company_names():
    """B1 필터가 실명 회사를 자르면 안 된다. exchange_records.jsonl 유일 원문 462건
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


def test_sentence_like_values_are_blocked_by_structure_not_by_min_count():
    """B/I-11, `BOILERPLATE_NOTE` 는 어휘 필터라 안내문·열거문을 완결하게 못 걸렀다.

    실측: 462종 중 14종이 어휘 필터도 `classify()` 도 통과해 승격 후보로 올라온다.
    전부 `count == 1` 이라 **지금 막고 있는 것은 `MIN_COUNT=2` 뿐**이고, 그중 두 종은
    `"… 10개월 이내에 …"` / `"… 8개월 이내에 …"` 로 **개월 수만 다른 쌍둥이**다.
    같은 발행사가 같은 템플릿을 한 번 더 쓰면 그 순간 문장이 노드 키가 된다.
    """
    for raw in (
        "상기 계약은 계약상대의 선택에 따라 대선계약(수주)일자로부터 10개월 이내에 "
        "대선계약에서 장기화물운송계약으로 변동될 수 있으며",
        "상기 계약은 계약상대의 선택에 따라 대선계약(수주)일자로부터  8개월 이내에 "
        "대선계약에서 장기화물운송계약으로 변동될 수 있으며",
        "- 상기3의 '이사회결의일(결정일)'은 양사합의에 의한 계약기간 종료일임.",
        "3. 이사회의결의일(결정일) 또는 사실확인일은 양사합의에 의한 계약기간 종료일입니다.",
        "바. 상기 5항의 계약종료일은 계약상대방과 협의하여 납기 연장 되었습니다.",
        "※ 최근 매출액은 2018년도 연결기준 매출액입니다. ※ 계약 기간 동안 발생한 매출총액",
        "1) 당사는 유럽 지역 시장 점유율 확대를 목적으로 유럽 판매법인을 설립함.",
        "- 계약상대방 변경 및 계약기간 연장 1) 판매ㆍ공급계약 구분: 폴리실리콘 공급",
        "- 상기 계약은 2023년 8월 25일 체결된 '지위이전 합의서'에 따라 변경되었습니다.",
    ):
        assert sentence_like(raw), f"문장형 값이 여전히 승격 후보다: {raw[:40]!r}"


def test_sentence_like_filter_does_not_cut_real_company_names():
    """리뷰어 처방의 "40자 초과" 단독 조건을 기각한 근거의 회귀 고정.

    리뷰어는 "승격 402종 중 40자 초과는 그 문장들뿐" 이라고 했으나 전량 재대조 결과
    40자 초과는 **43종**이고 그중 32종이 실명이다. 길이 단독 조건을 넣으면
    `방위사업청 (Defense Acquisition Program Administration)`(count 91, **최대 허브**)이
    탈락해 그래프가 무너진다. 길이는 오탐 방어용 하한으로만 쓴다.
    """
    for raw in (
        "방위사업청 (Defense Acquisition Program Administration)",
        "방위사업청(Defense Acquisition Program Administation)",
        "한국항공우주산업㈜ (KOREA AEROSPACE INDUSTRIES, LTD.)",
        "MPA (Maritime and Port Authority of Singapore)",
        "China National Chemical Engineering & Construction Corporation Seven, Ltd.",
        "타이중시 정부 도시철도공정국(Rapid Transit System Bureau of Taichung City Government)",
        "뭄바이광역개발청 (Mumbai Metropolitan Region Development Authority)",
        "브라질 상파울루 메트로(COMPANHIA DO METROPOLITANO DE SAO PAULO-METRO)",
        "Shell Petroleum Development Company Nigeria Limited",
        "Aramco Asia Japan K.K. (Saudi Aramco 자회사)",
        "The General Company for Ports of Iraq (이라크 교통부 산하 항만공사)",
        "싸토리우스코리아오퍼레이션스 유한회사 (Sartorius Korea Operations LLC)",
    ):
        assert not sentence_like(raw), f"실명 회사가 문장으로 오탐됐다: {raw!r}"


def test_excluded_sentence_like_records_the_raw_and_the_reason(tmp_path):
    """걸러진 것을 조용히 버리지 않는다. 원문·건수·사유를 남긴다.

    종전에는 이 문장들이 애초에 어느 필터에도 안 걸려 `excluded_boilerplate` 에도
    기록이 없었다. `excluded_vague` 도 건수만 남고 원문이 없어 사후 오탐 확인이
    불가능했다. 둘 다 원문을 남긴다.

    **기대값이 세 개 다 바뀌었고 방향이 갈린다. 그것이 W3 의 서명이다.**
    문장형 14종/14건 → **3종/3건**(`SENTENCE_LIKE_BEFORE_W3`)으로 줄고, 비실명은
    33종/196건 → **34종/207건**(`VAGUE_BEFORE_W3`)으로 **늘었다.** 정정 표에서
    안내문·문장을 읽던 레코드가 이제 본문 칸을 읽고, 그 본문 값 11건이 비실명이다.
    """
    if not RECORDS_PATH.exists():
        pytest.skip(f"{RECORDS_PATH} 이 없어 그래프를 재생성할 수 없다 (원본 5.2GB 필요).")
    g = build_graph(tmp_path / "graph.json")

    assert g["stats"]["sentence_like_excluded_unique"] == 3       # W3 이후 실측 고정
    assert g["stats"]["sentence_like_excluded_occurrences"] == 3
    assert len(g["excluded_sentence_like"]) == 3
    for item in g["excluded_sentence_like"]:
        assert item["raw"] and item["count"] >= 1 and item["reason"]
        assert sentence_like(item["raw"])
    # 개월 수만 다른 쌍둥이("… 10개월 이내에 …" / "… 8개월 이내에 …")는 정정 표
    # 칸에만 있던 값이라 W3 이후 코퍼스에서 사라졌다. **필터가 놓친 것과 구별한다**.
    # 구조 필터는 여전히 그 문장을 잡는다(`test_sentence_like_values_are_blocked_by_
    # structure_not_by_min_count` 가 원문 그대로 고정). 여기서는 코퍼스에 없다는 것만 센다.
    twins = [i["raw"] for i in g["excluded_sentence_like"] if "대선계약" in i["raw"]]
    assert twins == [], f"쌍둥이가 코퍼스에 되살아났다 — 정정 표가 되돌아왔는지 본다: {twins}"

    assert g["stats"]["vague_excluded_unique"] == 34              # W3 이후 실측 고정
    assert g["stats"]["vague_excluded_occurrences"] == 207
    for item in g["excluded_vague"]:
        assert item["raw"] and item["count"] >= 1 and item["reason"]


def test_the_normalizer_and_boilerplate_fixes_leave_the_graph_unchanged(tmp_path):
    """I-11·I-12 를 고쳐도 **그래프가 바뀌지 않는다** 는 것이 이 수정의 전제다.

    리뷰어는 "145·44 는 하한" 이라며 접미사 앵커링이 노드를 늘릴 것으로 봤고, 근거로
    두 쌍을 들었다. 전량 재대조로 **기각**했다:
      · `hdhyundaielectricamericaoration`(4) / `hyundaielectricamericaoration`(1) 은
        접미사가 아니라 **`HD` 접두** 때문에 갈렸다. 앵커링해도 두 키로 남는다.
      · `algihaz`(1) / `algihazcontractingcompany`(1) 의 차이는 `Contracting Company` 이고
        그것은 `_SUFFIX` 목록에 아예 없다. 이 수정으로는 합쳐질 수 없다.
    앵커판과 현행을 462종 전량 대조하면 키가 바뀌는 것은 6종(전부 `overseas`)뿐이고
    **canonical 그룹 수 407 = 407 · 오병합 0 · 분열 0** 이다. 코디네이터 측정이 맞다.

    **W3 이 들어온 뒤에도 이 전제가 그대로다**. 정정 표를 필드 추출에서 빼면서
    안내문 34건·문장 11건이 상류에서 사라졌는데도 `(145, 44, 25)` 와 승격 후보 342 가
    **네 항목 다 불변**이다. 사라진 값 중 실명 회사로 승격될 것이 하나도 없었다는 뜻이고,
    이것이 "필터가 깨진 게 아니라 필터할 것이 없어졌다"의 증거다.
    """
    if not RECORDS_PATH.exists():
        pytest.skip(f"{RECORDS_PATH} 이 없어 그래프를 재생성할 수 없다 (원본 5.2GB 필요).")
    s = build_graph(tmp_path / "graph.json")["stats"]
    assert (s["nodes"], s["pairs"], s["hubs_2plus_partners"]) == (145, 44, 25)
    assert s["candidates_before_min_count"] == 342
    # W3 이전에는 (27, 34) 였다 (`BOILERPLATE_BEFORE_W3`). 상류가 깨끗해져 0 이 됐을 뿐
    # 그래프는 한 칸도 안 움직였다. 두 사실을 한 테스트에 붙여 둔다.
    assert (s["boilerplate_excluded_unique"], s["boilerplate_excluded_occurrences"]) == (0, 0)


def test_country_plus_generic_noun_hubs_are_reported_not_silently_promoted():
    """B2: "사우디 소재 EPC"·"중동지역국가 국방부" 류(국가/지역 + 일반명사)가 실제
    코퍼스에 존재한다. `classify()`는 고치지 않았으므로(Task 2 결정 유지) 이 값들이
    노드로 올라오는지만 확인한다.

    실측: "중동지역국가 국방부"는 count=2로 노드가 되지만 partners는 1개
    (한화에어로스페이스)뿐이라 pairs를 생성하지 않는다. 아직 서로 다른 실체를
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

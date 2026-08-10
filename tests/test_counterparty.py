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

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "artifacts/eda/counterparty_profile.json"


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

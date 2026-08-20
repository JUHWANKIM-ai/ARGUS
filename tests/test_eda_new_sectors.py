"""Phase A 부분 재실행, 신규 6섹터 25개사 프로파일. 정본 `docs/pipeline/01-eda.md`.

`artifacts/` 는 gitignore 대상이라 이 산출물은 git 에 없다. `ensure_profile` fixture 가
`exchange_records.jsonl` 등 원본 records 로 값싸게 재생성하거나, 그것마저 없으면
사유를 밝히고 skip 한다. `tests/test_counterparty.py::ensure_profile` 과 같은 패턴.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "scratch/eda/new_sector_profile.json"
NEW_SECTORS = {"게임", "로봇", "소비재·유통", "자동차·모빌리티", "철강", "통신"}


@pytest.fixture(autouse=True)
def ensure_profile():
    """산출물이 없으면 값싸게 재생성한다. 있으면 그대로 두고 다시 만들지 않는다."""
    if PROFILE_PATH.exists():
        return

    from scripts.eda_new_sectors import RECORD_FILES, build_profile

    from argus.config import settings

    artifacts_root = settings().artifacts_root
    missing = [f for f in RECORD_FILES.values() if not (artifacts_root / f).exists()]
    if missing:
        pytest.skip(
            f"{missing} 이 없어 new_sector_profile.json 을 재생성할 수 없다 — "
            "exchange/major/holding_records.jsonl 을 먼저 만들어야 한다 (원본 5.5GB 필요)."
        )
    manifest_path = settings().corpus_root / "manifest.jsonl"
    if not manifest_path.exists():
        pytest.skip(
            f"{manifest_path} 이 없어 new_sector_profile.json 을 재생성할 수 없다 — "
            "원본 코퍼스(gitignore 대상, 5.5GB)가 이 환경에 없다."
        )

    build_profile(PROFILE_PATH)

    # manifest.load_manifest / load_universe 는 lru_cache(maxsize=1) 다. 이 세션에서
    # 먼저 빈 상태로 캐시됐을 수 있는 다른 소비자를 위해 재생성 뒤 캐시를 비운다
    # (tests/test_ablation.py::ensure_counterparty_graph 와 같은 이유).
    from argus.corpus import manifest as _manifest
    _manifest.load_manifest.cache_clear()
    _manifest.load_universe.cache_clear()


def test_all_new_sectors_are_profiled():
    prof = json.loads(
        (ROOT / "scratch/eda/new_sector_profile.json").read_text(encoding="utf-8"))
    assert NEW_SECTORS <= set(prof["by_sector"])


def test_profile_records_schema_matched_per_sector():
    """섹터별 파싱 커버리지가 기존 14섹터와 다르면 그게 다음 처방이다."""
    prof = json.loads(
        (ROOT / "scratch/eda/new_sector_profile.json").read_text(encoding="utf-8"))
    for s, v in prof["by_sector"].items():
        assert "schema_matched_ratio" in v
        assert "n_docs" in v
        assert 0.0 <= v["schema_matched_ratio"] <= 1.0


def test_known_defects_are_listed():
    """확장 트랙이 찾은 결함 4건이 기록돼 있어야 다음 트랙이 안 놓친다."""
    prof = json.loads(
        (ROOT / "scratch/eda/new_sector_profile.json").read_text(encoding="utf-8"))
    corps = {d["corp_name"] for d in prof["known_defects"]}
    assert {"NAVER", "크래프톤", "이마트", "아모레퍼시픽"} <= corps

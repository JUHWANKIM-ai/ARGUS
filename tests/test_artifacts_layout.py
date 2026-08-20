"""`artifacts/` holds the preprocessing outputs and nothing else.

Why this test exists. Measurement, audit and experiment outputs used to be written
under `artifacts/` alongside the fifteen files the runtime actually opens. The
submission bundle is defined as those fifteen (plus five ledger files under
`data/`), so anything else living there had to be excluded by hand at upload time
-- `deploy/upload-artifacts.sh` carried a growing `--exclude` list, and a zip built
without it shipped 25,551 entries instead of 15. Working outputs go to `scratch/`
now, and this test is what keeps them there: a script that writes a report into
`artifacts/` fails the suite instead of quietly enlarging the submission.

The expected file list is read from `scripts/build_preprocessing_manifest.py`, not
restated here. Two copies of the same list is the failure shape this repository has
hit repeatedly -- one gets updated and the other does not.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def _expected() -> set[str]:
    """The artifacts/ basenames the manifest builder declares as submission files."""
    path = ROOT / "scripts" / "build_preprocessing_manifest.py"
    spec = importlib.util.spec_from_file_location("_manifest_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = {p.split("/", 1)[1] for p in module.FILES if p.startswith("artifacts/")}
    assert names, "제출 대상에 artifacts/ 파일이 하나도 없다 — 목록을 잘못 읽었다"
    return names


def test_manifest_builder_declares_the_artifacts_files():
    """목록을 읽어 오는 경로가 살아 있는지 먼저 본다.

    이 단정이 없으면 `FILES` 가 비거나 이름이 바뀌었을 때 아래 시험들이 빈 집합을
    비교하며 조용히 통과한다.
    """
    names = _expected()
    assert len(names) == 15, sorted(names)
    assert "periodic_sections.txt" in names
    assert "section_index.npz" in names


@pytest.mark.skipif(not ARTIFACTS.is_dir(),
                    reason="전처리 산출물이 없는 기계 — 배치 규칙을 검사할 대상이 없다")
def test_artifacts_holds_no_subdirectories():
    """측정·감사 산출물은 `scratch/` 로 간다.

    `artifacts/` 에 디렉터리가 생기는 것은 곧 누군가 작업 산출물을 여기에 쓰기
    시작했다는 뜻이다.
    """
    dirs = sorted(p.name for p in ARTIFACTS.iterdir() if p.is_dir())
    assert dirs == [], (
        "artifacts/ 에 디렉터리가 있다: %s — 작업 산출물은 scratch/ 에 쓴다" % dirs)


@pytest.mark.skipif(not ARTIFACTS.is_dir(),
                    reason="전처리 산출물이 없는 기계 — 배치 규칙을 검사할 대상이 없다")
def test_artifacts_holds_no_file_outside_the_submission_list():
    present = {p.name for p in ARTIFACTS.iterdir() if p.is_file()}
    extra = sorted(present - _expected())
    assert extra == [], (
        "artifacts/ 에 제출 대상이 아닌 파일이 있다: %s — scratch/ 에 쓴다" % extra)


def test_integrity_manifest_lives_outside_artifacts():
    """대조표는 묶음 밖에 있어야 한다.

    묶음을 검증하는 증서가 그 묶음 안에만 있으면 심사자가 받은 zip 이 맞는 zip 인지
    확인할 방법이 없다. `artifacts/` 는 gitignore 이므로 저장소가 추적할 수 있는
    자리여야 한다는 요구도 같은 결론을 가리킨다.
    """
    assert (ROOT / "deploy" / "preprocessing_manifest.json").is_file()
    assert not (ARTIFACTS / "audit" / "preprocessing_manifest.json").exists()

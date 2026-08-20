"""Single source of configuration. Secrets are read from .env only."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CLOVA_HOST = "https://clovastudio.stream.ntruss.com"
MODEL = "HCX-005"
CLOVA_PATH = f"/testapp/v3/chat-completions/{MODEL}"


def _find_repo_root(start: Path | None = None) -> Path:
    """Walk upwards from `start` until a directory holding pyproject.toml."""
    here = (start or Path(__file__)).resolve()
    for base in [here, *here.parents]:
        if (base / "pyproject.toml").exists():
            return base
    raise RuntimeError("pyproject.toml 을 찾지 못했습니다")


def _load_dotenv(root: Path) -> None:
    """Load .env into the process environment without overriding existing keys."""
    path = root / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    """Resolved paths, credentials and model endpoint."""

    repo_root: Path
    corpus_root: Path
    artifacts_root: Path
    scratch_root: Path
    clova_api_key: str
    clova_url: str
    model: str


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Resolved settings for this repository checkout. Cached per process."""
    root = _find_repo_root()
    _load_dotenv(root)
    #: 🔴 두 루트를 가른다. `artifacts_root` 는 **제출 대상 전처리 산출물만** 담는다
    #: (런타임이 여는 15개). 실험·측정·캐시처럼 제출물이 아닌 것은 `scratch_root` 로
    #: 간다. 한 루트에 섞어 두었더니 업로드 스크립트가 손으로 관리하는 `--exclude`
    #: 목록을 들고 다녀야 했고, 그 목록을 안 쓴 zip 이 15개 대신 25,551개를 실었다.
    #: `tests/test_artifacts_layout.py` 가 이 경계를 지킨다.
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    scratch = root / "scratch"
    scratch.mkdir(exist_ok=True)
    return Settings(
        repo_root=root,
        corpus_root=root / "data" / "dart-disclosures",
        artifacts_root=artifacts,
        scratch_root=scratch,
        clova_api_key=os.environ.get("CLOVA_API_KEY", ""),
        clova_url=CLOVA_HOST + CLOVA_PATH,
        model=MODEL,
    )

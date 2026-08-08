"""설정 단일 출처. 비밀정보는 .env 에서만 읽는다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CLOVA_HOST = "https://clovastudio.stream.ntruss.com"
MODEL = "HCX-005"
CLOVA_PATH = f"/testapp/v3/chat-completions/{MODEL}"


def _find_repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for base in [here, *here.parents]:
        if (base / "pyproject.toml").exists():
            return base
    raise RuntimeError("pyproject.toml 을 찾지 못했습니다")


def _load_dotenv(root: Path) -> None:
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
    repo_root: Path
    corpus_root: Path
    artifacts_root: Path
    clova_api_key: str
    clova_url: str
    model: str


@lru_cache(maxsize=1)
def settings() -> Settings:
    root = _find_repo_root()
    _load_dotenv(root)
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    return Settings(
        repo_root=root,
        corpus_root=root / "data" / "dart-disclosures",
        artifacts_root=artifacts,
        clova_api_key=os.environ.get("CLOVA_API_KEY", ""),
        clova_url=CLOVA_HOST + CLOVA_PATH,
        model=MODEL,
    )

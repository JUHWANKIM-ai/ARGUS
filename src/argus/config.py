"""Single source of configuration. Secrets are read from .env only."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CLOVA_HOST = "https://clovastudio.stream.ntruss.com"
MODEL = "HCX-005"
CLOVA_PATH = f"/testapp/v3/chat-completions/{MODEL}"

#: A tuned model is addressed by its training task id --
#: `/testapp/v3/tasks/{taskId}/chat-completions`. The path was found by probing,
#: not from the documentation: the training-status response leaves `tuningModel`
#: null, so nothing in the API docs names the inference route.
#:
#: An environment variable decides it, for the sake of reverting. Hard-coding
#: the path here would make both adopting and withdrawing a source edit, and
#: reverting would mean digging through git. One line in `.env` selects the
#: tuned model; deleting that line returns to the base model.
TUNED_PATH = "/testapp/v3/tasks/{task}/chat-completions"


def _tuned_task() -> str:
    """The configured tuning task id, or an empty string when unset."""
    return os.environ.get("CLOVA_TUNED_TASK_ID", "").strip()


def _chat_path() -> str:
    task = _tuned_task()
    return TUNED_PATH.format(task=task) if task else CLOVA_PATH


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
    #: Two roots, kept apart. `artifacts_root` holds **only the preprocessing
    #: artifacts that ship** (the 15 the runtime opens). Anything that is not a
    #: submission artifact -- experiments, measurements, caches -- goes to
    #: `scratch_root`. Mixing them under one root forced the upload script to
    #: carry a hand-maintained `--exclude` list, and one zip built without that
    #: list shipped 25,551 files instead of 15.
    #: `tests/test_artifacts_layout.py` holds this boundary.
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
        clova_url=CLOVA_HOST + _chat_path(),
        model=_tuned_task() or MODEL,
    )

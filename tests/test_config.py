from pathlib import Path
from argus.config import settings

def test_settings_resolves_repo_root():
    s = settings()
    assert (s.repo_root / "pyproject.toml").exists()

def test_settings_points_at_corpus():
    s = settings()
    assert s.corpus_root.name == "dart-disclosures"

def test_clova_url_is_v3_hcx005():
    s = settings()
    assert s.clova_url.endswith("/testapp/v3/chat-completions/HCX-005")

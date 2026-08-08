import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def block_clova_network(monkeypatch):
    """테스트에서 실제 CLOVA 호출을 원천 차단한다.

    /answer 가 pipeline 에 연결되면서 평범한 스키마 테스트도 HCX 를 때릴 수 있게 됐다.
    가짜 응답이 필요한 테스트는 각자 `clova._post` 를 다시 monkeypatch 하면 된다
    (테스트 본문의 patch 가 이 autouse fixture 보다 나중에 적용돼 이긴다).
    """
    from argus import clova

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "테스트가 실제 CLOVA API 를 호출하려 했습니다. clova._post 를 monkeypatch 하십시오."
        )

    monkeypatch.setattr(clova, "_post", _blocked)

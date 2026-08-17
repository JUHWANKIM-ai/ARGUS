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


@pytest.fixture(autouse=True)
def no_api_pacing(monkeypatch):
    """러너의 문항 간 `PACE_S` 대기(1.0초)를 테스트에서만 0 으로 만든다.

    **단언은 한 글자도 바뀌지 않는다.** `PACE_S` 는 실제 API 에 대한 예의용
    간격이고(`error_analysis.PACE_S` 주석: *"실측 429 는 연속 호출에서만 났다"*),
    테스트는 CLOVA 를 부르지 않는다(`block_clova_network`). 그런데도 러너를 관통시키는
    테스트가 문항마다 1초를 그대로 잤다. 실측 전량 24.9초 중 **9초**가 이 대기였고
    저장소에 `PACE_S` 를 패치하는 테스트가 **0건**이었다.

    소비자 셋이 전부 모듈 속성으로 읽으므로(`error_analysis.execute` 는 전역 이름,
    `scripts/ablation.py`·`scripts/build_nary.py` 는 `ea.PACE_S`) 여기 한 곳만
    0 으로 두면 전부 걸린다. 재시도 백오프(`RETRY_BASE_S`)는 **건드리지 않는다**.
    그쪽은 429 를 만났을 때만 도는 길이고 테스트에서 밟히지 않는다.
    """
    from scripts import error_analysis as ea

    monkeypatch.setattr(ea, "PACE_S", 0.0)

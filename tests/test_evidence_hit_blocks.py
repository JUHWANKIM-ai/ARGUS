"""`evidence_hit` 이 본문 블록과 그 밖을 구별한다.

오픈이슈 v3 §9 의 우선순위 1번.

## 무엇이 문제였나

`error_analysis._blocks` 독스트링은 *"근거 블록. 마크다운 `## ` 헤더 하나가 근거 한
건이다"* 라고 적는데, `re.split(r"(?m)^##\\s", text)` 의 **첫 조각은 헤더가 없는 앞
영역**이고 그것을 버리지 않았다. 그 영역에는 결정적 줄들이 놓인다.

    - [결정적 집계] … 106,796,802,000원 (접수번호 20251128800125)
    - [결정적 조회] … 2,338,117,950,000원 (접수번호 20230329801108)

`evidence_hit` 은 블록 안에 골든 접수번호가 있으면 **그 문서가 프롬프트에 실렸다**고 센다.
결정적 줄은 우리가 계산한 요약이고 문서가 아니므로, 거기 적힌 14자리는 **본문이 하나도
안 실렸어도** 적재로 세어진다.

블록 **안**에도 같은 자리가 있다 — 다른 문서를 가리키는 포인터 줄
(`- 정정 대상 원공시: 접수번호 …`).

## 왜 지금 고칠 수 있나

전수 실측(`scratch/evidence_hit_exposure.py` · `A49`·`A56`·`A65` 서버 3회 = 9개 측정):
**아홉 개 전부 167/168 로 같다.** 고쳐도 과거 측정이 한 건도 안 움직인다. 지금 부풀림이
0 이라는 뜻이고, 이 수정은 **앞으로를 막는 일**이다.

🔴 **이 수정이 무엇을 풀지 않는지도 적어 둔다.** `Q-054`·`Q-068`·`Q-082`·`Q-094` 의 원공시
미인용은 이것으로 안 풀린다. `section_chain_note` 는 부풀림이 아니라 **성적 하락으로**
기각됐다 — 근거 한 줄이 답변의 금액을 밀어내 `Q-094` 가 3회 모두 깨졌다.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "_ea", ROOT / "scripts" / "error_analysis.py")
_ea = importlib.util.module_from_spec(_spec)
sys.modules["_ea"] = _ea
_spec.loader.exec_module(_ea)

#: 🔴 런타임이 실제로 내는 모양. `aggregate.summand_roster` 와 `pipeline._roster_line` 이
#: 첫 머리글 **위**에 이 줄들을 놓는다.
EVIDENCE = """- [결정적 집계] 위 근거 2건의 금액 합계: 106,796,802,000 + 1,652,800,000,000 = **1,759,596,802,000원**
  · 현대오토에버 차세대 ERP — 106,796,802,000원 (접수번호 20251128800125)
  · 현대글로비스 완성차 해상운송계약 — 1,652,800,000,000원 (접수번호 20250117800487)

## [공급계약 체결] 현대글로비스 – 단일판매ㆍ공급계약체결 — 완성차 해상운송계약
- 출처: 접수번호 20250117800487 (공시일 20250117)
- 정정 대상 원공시: 접수번호 20240514000756 (분기보고서 2024.03)

계약금액 1,652,800,000,000원
"""


def test_the_pre_header_region_is_not_a_block():
    """결정적 줄은 문서가 아니다. 우리가 계산한 요약이다."""
    blocks = _ea._blocks(EVIDENCE)
    assert len(blocks) == 1, "블록은 `## ` 하나뿐이다"
    assert "결정적 집계" not in blocks[0]
    assert "20251128800125" not in blocks[0], \
        "🔴 명세 줄의 접수번호가 블록에 새면 본문 없이 적재로 세어진다"


def test_a_pointer_line_is_not_this_document():
    """`- 정정 대상 원공시: 접수번호 …` 는 **다른** 문서를 가리킨다."""
    blocks = _ea._blocks(EVIDENCE)
    assert "20240514000756" not in blocks[0], \
        "🔴 포인터 줄의 접수번호가 남으면 그 문서가 실렸다고 세어진다"


def test_the_block_keeps_its_own_identity_and_body():
    """블록 자신의 접수번호(`- 출처:`)와 본문은 그대로 남는다.

    포인터 줄만 빼는 것이고 블록을 깎는 것이 아니다.
    """
    blocks = _ea._blocks(EVIDENCE)
    assert "20250117800487" in blocks[0], "이 문서 자신의 접수번호는 남는다"
    assert "1,652,800,000,000원" in blocks[0], "본문은 남는다"
    assert "완성차 해상운송계약" in blocks[0]


def test_evidence_hit_does_not_count_a_pointer_as_loaded():
    case = {"evidence": [{"rcept_no": "20240514000756"},
                         {"rcept_no": "20250117800487"}]}
    hit = _ea.evidence_hit(case, EVIDENCE)
    assert hit["present"] == ["20250117800487"]
    assert hit["n_present"] == 1
    assert hit["all"] is False, "포인터만 있는 골든은 적재가 아니다"


def test_evidence_hit_does_not_count_a_decisive_line_as_loaded():
    case = {"evidence": [{"rcept_no": "20251128800125"}]}
    hit = _ea.evidence_hit(case, EVIDENCE)
    assert hit["present"] == [], "명세 줄만 있는 골든은 적재가 아니다"


def test_a_document_that_really_is_loaded_still_counts():
    """수정이 참을 거짓으로 만들지 않는지 — 반대 방향 단정."""
    case = {"evidence": [{"rcept_no": "20250117800487",
                          "section": None}]}
    assert _ea.evidence_hit(case, EVIDENCE)["all"] is True


def test_an_evidence_string_that_starts_with_a_header_is_unchanged():
    """머리글로 시작하는 근거는 앞 영역이 없다. 첫 블록을 잃으면 안 된다."""
    text = ("## [공급계약 체결] 대우건설 – 계약\n"
            "- 출처: 접수번호 20250829800405 (공시일 20250829)\n\n본문")
    blocks = _ea._blocks(text)
    assert len(blocks) == 1
    assert "20250829800405" in blocks[0]


def test_past_measurements_do_not_move():
    """🔴 전수 실측을 단정으로 못 박는다.

    `scratch/evidence_hit_exposure.py` 가 `A49`·`A56`·`A65` 서버 3회 = 9개 측정에서
    세 변형(지금 · 머리 제외 · 둘 다 제외)이 **전부 167/168** 임을 확인했다. 이 수정이
    과거 값을 움직이지 않는다는 것이 채택의 전제다.

    여기서는 그 산출물을 읽어 단정한다 — 없으면 건너뛴다(다른 기계).
    """
    import json

    path = ROOT / "scratch" / "audit" / "evidence_hit_exposure.json"
    if not path.exists():
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["움직이는 것"] == [], doc["움직이는 것"]
    for key, row in doc["회차별"].items():
        assert row["지금"] == row["둘다제외"], key

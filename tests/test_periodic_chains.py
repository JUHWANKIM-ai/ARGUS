"""periodic 정정 체인. 대장 조인 하나로 나온다 — 원문 파싱도 LLM 도 없다.

계획서 `docs/superpowers/plans/2026-08-21-raw-preprocessing.md` 의 과제 2 (W-B).

## 무엇이 문제였나

`scripts/build_index.py` 가 `corpus.corrections.build_chains` 를 부르는데 그건
exchange·major·holding 만 탄다(README §450-453). periodic 은 `scripts/build_sections.py` 를
타고, `artifacts/periodic_sections.jsonl` 은 **`is_correction` 불리언만** 갖는다 —
**무엇을 정정했는지가 없다.** periodic 레코드 파일 자체가 없어 `tools.chain_probe` 도
periodic 접수번호에 `None` 을 돌려준다.

**이것이 `A58`(`cite_chain_pair`)의 사정거리가 짧았던 이유다.** `Q-054`·`Q-068`·`Q-082`·
`Q-094` 넷 다 periodic 이고, 링크가 빈 게 아니라 **그 그룹에 링크를 만드는 코드가 없다.**

## periodic 은 원문을 열 필요가 없다

`report_nm` 이 서식과 기간을 글자로 갖고 있어 (기업 · 서식 · 기간) 이 키가 된다.
실측(2026-08-21, 이 시험이 고정한다):

    periodic 정정 159건
      단일 원공시로 확정   157건 (98.7%)
      후보 2+ (모호)         0건
      원공시가 코퍼스에 없다  2건
    report_nm 파싱 실패      0건 / periodic 1,054건 전부

🔴 **못 이은 것을 버리지 않고 `reason` 에 적는다.** `corpus/references.py` 의 규율과 같다 —
파서 실패와 진짜 부재가 구별돼야 한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts")

from build_periodic_chains import REPORT_KEY, build_chains   # noqa: E402

FIXTURE = [
    {"rcept_no": "20250318001264", "corp_name": "NC", "doc_group": "periodic",
     "report_nm": "사업보고서 (2024.12)", "rcept_dt": "20250318", "is_correction": False},
    {"rcept_no": "20250328002285", "corp_name": "NC", "doc_group": "periodic",
     "report_nm": "[기재정정]사업보고서 (2024.12)", "rcept_dt": "20250328",
     "is_correction": True},
    {"rcept_no": "20230522000371", "corp_name": "한화솔루션", "doc_group": "periodic",
     "report_nm": "[기재정정]분기보고서 (2023.03)", "rcept_dt": "20230522",
     "is_correction": True},
    {"rcept_no": "20240722800037", "corp_name": "두산에너빌리티", "doc_group": "exchange",
     "report_nm": "단일판매ㆍ공급계약체결", "rcept_dt": "20240722", "is_correction": False},
]


def test_report_key_reads_form_and_period_through_the_revision_tag():
    assert REPORT_KEY.match("[기재정정]사업보고서 (2024.12)").groups() == (
        "사업보고서", "2024", "12")
    assert REPORT_KEY.match("사업보고서 (2024.12)").groups() == ("사업보고서", "2024", "12")
    assert REPORT_KEY.match("단일판매ㆍ공급계약체결") is None


def test_correction_links_to_the_single_original():
    rows = {r["rcept_no"]: r for r in build_chains(FIXTURE)}
    assert rows["20250328002285"]["root"] == "20250318001264"
    assert rows["20250328002285"]["prev"] == "20250318001264"
    assert rows["20250328002285"]["period"] == "2024.12"
    assert rows["20250328002285"]["reason"] == ""


def test_missing_original_is_recorded_not_dropped():
    rows = {r["rcept_no"]: r for r in build_chains(FIXTURE)}
    assert rows["20230522000371"]["root"] is None
    assert rows["20230522000371"]["reason"] == "원공시가 코퍼스에 없다"


def test_only_periodic_corrections_are_emitted():
    ids = {r["rcept_no"] for r in build_chains(FIXTURE)}
    assert ids == {"20250328002285", "20230522000371"}


def test_real_corpus_links_157_of_159():
    """실측 고정(2026-08-21). 이 수가 움직이면 대장이 바뀐 것이다."""
    from argus.config import settings

    path = settings().corpus_root / "manifest.jsonl"
    text = path.read_text(encoding="utf-8")
    manifest = [json.loads(line) for line in text.splitlines() if line.strip()]
    rows = build_chains(manifest)
    assert len(rows) == 159
    assert sum(1 for r in rows if r["root"]) == 157
    assert sum(1 for r in rows if r["reason"].startswith("원공시 후보가")) == 0
    linked = {r["rcept_no"]: r["root"] for r in rows}
    assert linked["20240514001580"] == "20240514000756"      # Q-054 HMM
    assert linked["20250814001076"] == "20250321000310"      # Q-068 한전기술
    assert linked["20240826000294"] == "20240814003869"      # Q-082 현대글로비스
    assert linked["20250328002285"] == "20250318001264"      # Q-094 NC


def test_every_report_nm_parses():
    """파싱 실패 0 이 이 경로의 전제다. 실패가 생기면 조용히 안 이어진다."""
    from argus.config import settings

    path = settings().corpus_root / "manifest.jsonl"
    manifest = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
    periodic = [r for r in manifest if r.get("doc_group") == "periodic"]
    assert len(periodic) == 1054
    unparsed = [r["report_nm"] for r in periodic
                if not REPORT_KEY.match(r.get("report_nm") or "")]
    assert unparsed == []

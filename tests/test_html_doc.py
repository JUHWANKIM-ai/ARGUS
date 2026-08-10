from argus.config import settings
from argus.corpus.html_doc import (
    extract_grid,
    extract_rows,
    extract_title,
    read_document,
    strip_markup,
)

SAMPLE = (
    settings().corpus_root
    / "raw/exchange/HD현대일렉트릭/20230131800162/20230131800162.xml"
)

# DART XML 은 값 칸에 <TU>(코드값 셀)·<TE>(추출 셀)를 쓴다. 실측 커버리지는
# exchange(KIND HTML) 100% 인데 holding(DART XML) 은 17.2% 뿐이었다.
HOLDING_SAMPLE = (
    settings().corpus_root
    / "raw/holding/CJ제일제당/20230404002139/20230404002139.xml"
)


def test_file_is_utf8_despite_euckr_meta():
    raw = SAMPLE.read_bytes()
    assert b"charset=euc-kr" in raw          # 선언은 euc-kr
    raw.decode("utf-8")                       # 그러나 실제 바이트는 UTF-8


def test_read_document_returns_korean():
    text = read_document(SAMPLE)
    assert "단일판매" in text


def test_strip_markup_removes_css():
    text = strip_markup(read_document(SAMPLE))
    assert "font-family" not in text
    assert "border-collapse" not in text


def test_extract_title():
    assert "공급계약" in extract_title(read_document(SAMPLE))


def test_extract_rows_finds_contract_amount():
    rows = extract_rows(read_document(SAMPLE))
    flat = [cell for row in rows for cell in row]
    assert any("97,000,000,000" in c for c in flat)
    assert any("계약금액" in c for c in flat)


# --- DART XML 의 TU/TE 셀 -------------------------------------------------


def test_extract_rows_reads_tu_cells():
    """<TU> 는 코드값이 붙은 값 칸이다. 라벨만 읽고 값을 버리면 안 된다."""
    html = "<TR><TD>보고의무발생일</TD><TU AUNIT='RPT_RSP_DT'>2023년 01월 19일</TU></TR>"
    assert extract_rows(html) == [["보고의무발생일", "2023년 01월 19일"]]


def test_extract_rows_reads_te_cells():
    """<TE> 는 ACODE 가 붙은 추출 칸이다. 지분공시 주식수·비율이 전부 여기 있다."""
    html = "<TR><TD>이번 보고서</TD><TE ACODE='SUM_TMT_CNT'>1,783,406</TE>" \
           "<TE ACODE='SUM_TMT_RT'>11.85</TE></TR>"
    assert extract_rows(html) == [["이번 보고서", "1,783,406", "11.85"]]


def test_extract_rows_recovers_holding_stake_values():
    """실제 지분공시에서 보유주식수·보유비율이 읽혀야 한다 (기존 파서는 못 읽었다)."""
    flat = [c for row in extract_rows(read_document(HOLDING_SAMPLE)) for c in row]
    assert "1,783,406" in flat        # 이번 보고서 보유주식등의 수 (<TE>)
    assert "11.85" in flat            # 이번 보고서 보유비율     (<TE>)
    assert "국민연금공단" in flat       # 보고자                  (<TE>)


def test_extract_rows_still_drops_empty_cells():
    """라벨/값 소비자(normalize)가 row[-1] 을 값으로 쓰므로 빈 칸은 계속 버린다."""
    html = "<TR><TD>계약금액</TD><TD></TD><TD>97,000</TD></TR>"
    assert extract_rows(html) == [["계약금액", "97,000"]]


def test_extract_rows_handles_nested_tables():
    """중첩 표가 있어도 바깥 행이 안쪽 </TR> 에서 잘리면 안 된다."""
    html = ("<TR><TD>바깥라벨</TD>"
            "<TD><TABLE><TR><TD>안쪽</TD></TR></TABLE></TD>"
            "<TD>바깥값</TD></TR>")
    rows = extract_rows(html)
    assert rows[0][0] == "바깥라벨"
    assert rows[0][-1] == "바깥값"


# --- COLSPAN 전개 (열 정렬 보존) -------------------------------------------


def test_extract_grid_expands_colspan():
    """COLSPAN 을 안 펼치면 병합 셀만큼 값이 왼쪽으로 밀려 라벨-값 매핑이 깨진다.

    트랙 2가 정기공시에서 확인한 실패 사례: 삼성전자 매출 표의 '총 계' 행이
    COLSPAN=2 라서 매출액 3,336,059 가 '주요 제품' 열로 밀린다.
    """
    html = ("<TR><TD>품목</TD><TD>주요 제품</TD><TD>매출액</TD></TR>"
            "<TR><TD COLSPAN='2'>총 계</TD><TD>3,336,059</TD></TR>")
    grid = extract_grid(html)
    assert grid[0] == ["품목", "주요 제품", "매출액"]
    assert grid[1] == ["총 계", "", "3,336,059"]
    # 매출액이 매출액 열(index 2)에 그대로 있어야 한다
    assert grid[0][2] == "매출액" and grid[1][2] == "3,336,059"


def test_extract_grid_keeps_empty_cells_for_alignment():
    html = "<TR><TD>a</TD><TD></TD><TD>c</TD></TR>"
    assert extract_grid(html) == [["a", "", "c"]]


def test_extract_grid_expands_colspan_on_tu_cells():
    """지분공시는 COLSPAN 이 TU/TE 에 붙는다."""
    html = "<TR><TD>보고구분</TD><TU COLSPAN='3'>변동</TU></TR>"
    assert extract_grid(html) == [["보고구분", "변동", "", ""]]

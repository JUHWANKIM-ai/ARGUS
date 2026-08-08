from argus.config import settings
from argus.corpus.html_doc import extract_rows, extract_title, read_document, strip_markup

SAMPLE = (
    settings().corpus_root
    / "raw/exchange/HD현대일렉트릭/20230131800162/20230131800162.xml"
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

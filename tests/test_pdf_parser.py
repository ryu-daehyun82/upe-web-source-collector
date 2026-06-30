from app.workers.pdf_parser import parse_pdf, _is_pdf


def test_empty_none_nonpdf():
    for content in [None, b"", b"<html>not pdf"]:
        result = parse_pdf(content)
        assert result["layout_type"] == "unknown"
        assert result["page_count"] == 0
        assert result["section_order"] == []
        assert result["original_text"] == ""
        assert result["pattern_text"] == ""
        assert result["encrypted"] is False


def test_two_pages():
    content = b"%PDF-1.4\n /Type /Page \n /Type /Page \n%%EOF"
    result = parse_pdf(content)
    assert result["page_count"] == 2
    assert result["layout_type"] == "document"
    assert result["section_order"] == ["page", "page"]


def test_has_text_font():
    result = parse_pdf(b"%PDF-1.4 /Type /Page /Font ")
    assert result["has_text"] is True


def test_has_images():
    result = parse_pdf(b"%PDF-1.4 /Type /Page /Subtype /Image ")
    assert result["has_images"] is True


def test_encrypted():
    result = parse_pdf(b"%PDF-1.4 /Encrypt 1 0 R /Type /Page")
    assert result["encrypted"] is True
    assert result["layout_type"] == "encrypted"


def test_pattern_text_empty():
    result = parse_pdf(b"%PDF-1.4 /Type /Page")
    assert result["pattern_text"] == ""


def test_url_included():
    with_url = parse_pdf(b"%PDF-1.4 /Type /Page", url="https://ex.com/d.pdf")
    assert with_url["source_url"] == "https://ex.com/d.pdf"
    without_url = parse_pdf(b"%PDF-1.4 /Type /Page")
    assert "source_url" not in without_url


def test_is_pdf():
    assert _is_pdf(b"%PDF-1.7\nx") is True
    assert _is_pdf(b"<html>") is False


def test_section_order_cap():
    content = b"%PDF-1.4" + b" /Type /Page" * 250
    result = parse_pdf(content)
    assert result["page_count"] == 250
    assert len(result["section_order"]) == 200
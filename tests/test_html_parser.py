from app.workers.html_parser import parse_html, _layout_type


class TestHtmlParser:
    def test_empty_html(self):
        for html in ["", None]:
            r = parse_html(html)
            assert r["section_order"] == []
            assert r["card_count"] == 0
            assert r["original_text"] == ""
            assert r["pattern_text"] == ""
            assert r["layout_type"] == "unknown"
            assert r["table_structure"] == {"tables": 0, "rows": 0}

    def test_multi_section_layout(self):
        html = "<header></header><main><section></section></main><footer></footer>"
        r = parse_html(html)
        assert r["section_order"] == ["header", "main", "section", "footer"]
        assert r["layout_type"] == "multi_section"
        assert r["pattern_text"] == ""

    def test_article_layout(self):
        html = "<article><h1>Title</h1><p>본문내용</p></article>"
        r = parse_html(html)
        assert "article" in r["section_order"]
        assert r["layout_type"] == "article"
        assert r["card_count"] >= 1
        assert "본문내용" in r["original_text"]

    def test_table_structure(self):
        html = "<table><tr></tr><tr></tr></table>"
        r = parse_html(html)
        assert r["table_structure"] == {"tables": 1, "rows": 2}

    def test_script_style_excluded(self):
        html = "<style>.x{color:red}</style><script>var a=1;</script><p>보임텍스트</p>"
        r = parse_html(html)
        assert ".x{" not in r["original_text"]
        assert "var a" not in r["original_text"]
        assert "보임텍스트" in r["original_text"]

    def test_pattern_text_always_empty(self):
        r = parse_html("<p>아무 텍스트</p>")
        assert r["pattern_text"] == ""

    def test_url_included(self):
        r = parse_html("<p>x</p>", url="https://ex.com/page")
        assert r["source_url"] == "https://ex.com/page"
        r2 = parse_html("<p>x</p>")
        assert "source_url" not in r2

    def test_layout_type_helper(self):
        assert _layout_type([]) == "unknown"
        assert _layout_type(["article", "section"]) == "article"
        assert _layout_type(["header", "main", "footer"]) == "multi_section"
        assert _layout_type(["main"]) == "page"

    def test_card_count_li(self):
        html = "<ul><li>a</li><li>b</li><li>c</li></ul>"
        r = parse_html(html)
        assert r["card_count"] == 3

    def test_text_joined_with_space(self):
        html = "<p>가</p><p>나</p>"
        r = parse_html(html)
        assert "가" in r["original_text"] and "나" in r["original_text"]
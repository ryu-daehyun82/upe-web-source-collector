import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from build_verification_pack import (  # noqa: E402
    unescape_viz_code, sanitize_font_path, row_to_regions, DEFAULT_FONT,
)


def test_unescape_statement_separators():
    raw = "import numpy\\nx = 1\\nprint(x)"
    assert unescape_viz_code(raw) == "import numpy\nx = 1\nprint(x)"


def test_unescape_preserves_in_string_escape():
    # 문자열 내부의 \n 은 개행으로 풀지 않고 이스케이프로 보존(라벨 개행)
    raw = "s = f'{a}\\n{b}'\\nprint(s)"
    out = unescape_viz_code(raw)
    lines = out.split("\n")
    assert lines[0] == "s = f'{a}\\n{b}'"   # 내부 \n 보존
    assert lines[1] == "print(s)"
    # 파이썬으로 컴파일 가능해야(문자열 안 깨짐)
    compile(out, "<t>", "exec")


def test_unescape_tab_outside_string():
    assert unescape_viz_code("if x:\\n\\ty=1") == "if x:\n\ty=1"


def test_sanitize_font_path_all_variants():
    code = ("a='/app/font/nanum-gothic/NanumGothicBold.ttf'\n"
            "b='/app/font/nanum-gothic/NanumGothicExtraBold.ttf'")
    out = sanitize_font_path(code, "/F.ttf")
    assert "/app/font" not in out
    assert out.count("/F.ttf") == 2


def test_sanitize_font_path_noop_when_absent():
    code = "x = 1"
    assert sanitize_font_path(code, "/F.ttf") == code


def test_row_to_regions_xyxy():
    r = row_to_regions([[10, 20, 110, 70]], ["text"])
    assert r == [{"category": "text", "bbox": [10, 20, 100, 50]}]


def test_row_to_regions_skips_short():
    r = row_to_regions([[0, 0, 1], None, [0, 0, 5, 5]], ["a", "b", "c"])
    assert len(r) == 1 and r[0]["category"] == "c"


def test_default_font_constant():
    assert DEFAULT_FONT.endswith(".ttf")
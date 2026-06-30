import io
import struct
import zipfile

import hashlib

from app.workers.ppt_parser import parse_pptx
from app.workers.image_parser import parse_image
from app.workers.video_parser import parse_video
from app.workers.file_parse_worker import FileParseWorker
from app.workers.http_worker import FetchResult


# ── helpers: 합성 바이트 ─────────────────────────────────────────

def _pptx_bytes(n_slides=2, with_media=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<x/>")
        zf.writestr("ppt/presentation.xml", "<p/>")
        for i in range(1, n_slides + 1):
            zf.writestr(f"ppt/slides/slide{i}.xml", "<s/>")
        if with_media:
            zf.writestr("ppt/media/image1.png", "x")
    return buf.getvalue()


def _png_bytes(w=100, h=200) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_len = struct.pack(">I", 13)
    ihdr = b"IHDR" + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return sig + ihdr_len + ihdr + b"\x00" * 8


def _mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16


# ── ppt_parser ──────────────────────────────────────────────────

def test_pptx_two_slides():
    r = parse_pptx(_pptx_bytes(2), url="https://ex.com/d.pptx")
    assert r["slide_count"] == 2
    assert r["layout_type"] == "presentation"
    assert r["section_order"] == ["slide", "slide"]
    assert r["has_images"] is True
    assert r["original_text"] == "" and r["pattern_text"] == ""
    assert r["source_url"] == "https://ex.com/d.pptx"


def test_pptx_empty_and_nonzip():
    for c in [None, b"", b"not a zip"]:
        r = parse_pptx(c)
        assert r["layout_type"] == "unknown"
        assert r["slide_count"] == 0
        assert r["section_order"] == []


def test_pptx_no_media():
    r = parse_pptx(_pptx_bytes(1, with_media=False))
    assert r["slide_count"] == 1
    assert r["has_images"] is False


# ── image_parser ────────────────────────────────────────────────

def test_image_png_dims():
    r = parse_image(_png_bytes(100, 200))
    assert r["image_format"] == "png"
    assert r["layout_type"] == "image"
    assert r["width"] == 100 and r["height"] == 200


def test_image_jpeg_no_dims():
    r = parse_image(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    assert r["image_format"] == "jpeg"
    assert r["layout_type"] == "image"
    assert r["width"] is None


def test_image_gif_bmp_webp():
    assert parse_image(b"GIF89a" + b"\x00" * 10)["image_format"] == "gif"
    assert parse_image(b"BM" + b"\x00" * 10)["image_format"] == "bmp"
    assert parse_image(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 4)["image_format"] == "webp"


def test_image_unknown():
    for c in [None, b"", b"xxxx"]:
        r = parse_image(c)
        assert r["layout_type"] == "unknown"
        assert r["image_format"] is None


# ── video_parser ────────────────────────────────────────────────

def test_video_mp4():
    r = parse_video(_mp4_bytes(), url="https://ex.com/v.mp4")
    assert r["video_container"] == "mp4"
    assert r["layout_type"] == "video"
    assert r["source_url"] == "https://ex.com/v.mp4"


def test_video_matroska_avi():
    assert parse_video(b"\x1a\x45\xdf\xa3" + b"\x00" * 12)["video_container"] == "matroska"
    assert parse_video(b"RIFF" + b"\x00\x00\x00\x00" + b"AVI " + b"\x00" * 4)["video_container"] == "avi"


def test_video_unknown():
    for c in [None, b"", b"short"]:
        r = parse_video(c)
        assert r["layout_type"] == "unknown"
        assert r["video_container"] is None


# ── FileParseWorker ─────────────────────────────────────────────

class _FakeFetcher:
    def __init__(self, *, content=b"%PPTX", raise_exc=None):
        self.content = content
        self.raise_exc = raise_exc

    def fetch(self, url, *, max_bytes=52428800, timeout=30.0):
        if self.raise_exc is not None:
            raise self.raise_exc
        c = self.content[:max_bytes]
        return FetchResult(url=url, status_code=200, content=c, content_type="application/octet-stream",
                           content_hash=hashlib.sha256(c).hexdigest(), byte_size=len(c),
                           truncated=len(self.content) > max_bytes)


def _ppt_parser(content, *, url=None):
    return {"layout_type": "presentation", "slide_count": 2, "original_text": "", "pattern_text": ""}


def test_worker_supports():
    w = FileParseWorker(parser=_ppt_parser, job_types={"parse_ppt"}, fetcher=_FakeFetcher())
    assert w.supports("parse_ppt", "x") is True
    assert w.supports("fetch_html", "x") is False
    assert w.worker_name == "file_parse"


def test_worker_precheck():
    w = FileParseWorker(parser=_ppt_parser, job_types={"parse_ppt"}, fetcher=_FakeFetcher())
    assert w.precheck({"job_type": "parse_ppt"})["ok"] is False
    assert w.precheck({"url": "https://ex.com/d.pptx", "job_type": "parse_ppt"})["ok"] is True
    assert w.precheck({"url": "https://ex.com/d.pptx", "job_type": "parse_ppt", "job_config": {"max_bytes": 0}})["ok"] is False


def test_worker_execute_feature():
    w = FileParseWorker(parser=_ppt_parser, job_types={"parse_ppt"}, worker_name="ppt", fetcher=_FakeFetcher())
    res = w.execute({"url": "https://ex.com/d.pptx", "job_type": "parse_ppt"})
    assert res["status"] == "succeeded"
    assert res["text"] is None
    assert res["feature"]["layout_type"] == "presentation"
    assert res["layout_type"] == "presentation"
    assert "content_hash" in res


def test_worker_execute_failure():
    w = FileParseWorker(parser=_ppt_parser, job_types={"parse_ppt"}, fetcher=_FakeFetcher(raise_exc=RuntimeError("dl")))
    res = w.execute({"url": "https://ex.com/x", "job_type": "parse_ppt"})
    assert res["status"] == "failed"
    assert res["feature"] is None


def test_worker_postcheck():
    w = FileParseWorker(parser=_ppt_parser, job_types={"parse_ppt"}, fetcher=_FakeFetcher(), max_bytes=10)
    assert w.postcheck({"status": "succeeded", "byte_size": 5})["ok"] is True
    assert w.postcheck({"status": "failed"})["reason"] == "fetch_failed"
    assert w.postcheck({"status": "succeeded", "byte_size": 11})["reason"] == "too_large"


def test_worker_real_parsers_integration():
    # 실제 parse_pptx 주입 + 합성 PPTX → feature
    w = FileParseWorker(parser=parse_pptx, job_types={"parse_ppt"}, fetcher=_FakeFetcher(content=_pptx_bytes(3)))
    res = w.execute({"url": "https://ex.com/d.pptx", "job_type": "parse_ppt"})
    assert res["feature"]["slide_count"] == 3
    assert res["feature"]["layout_type"] == "presentation"
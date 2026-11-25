"""Private review UI escaping and byte-range serving contracts."""
import importlib.util
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chomp.evaluation.review_packet import render_page


def test_review_page_escapes_evidence_and_preserves_unrated_templates():
    clip = {"review_id":"N01","start_ms":1000,"end_ms":4000,"context_start_ms":0,"source_offset_ms":1000,
            "template_index":0,"utterances":[{"start_ms":1000,"text":"<script>bad()</script>"}],
            "preview":"video.mp4","source_context":"context.mp4","poster":"poster.png","comparison":"comparison.png",
            "diagnostics":{"width":540,"height":960,"output_dbfs":-25,"audio_envelope_lag_ms":None},
            "reason":"<img onerror=bad()>"}
    page=render_page({"packet_id":"test","groups":[{"id":"new","label":"fixture","clips":[clip],
                       "review_template":{"clips":[],"source_path":"</script><script>bad()</script>"}}]})
    assert "<script>bad()" not in page
    assert "&lt;script&gt;bad()" in page and "&lt;img onerror=bad()&gt;" in page
    assert "\\u003c/script>" in page
    assert '<b>1</b>new moments' in page and '<b>0</b>earlier selections' in page
    assert "watched_source" in page and "watched_export" in page
    assert 'https://' not in page and 'http://' not in page


def test_loopback_server_ranges_and_private_root(tmp_path):
    spec=importlib.util.spec_from_file_location("r1_server",Path(__file__).parents[1]/"tools/serve_private_review.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root=tmp_path/"private"
    root.mkdir()
    (root/"index.html").write_text("private review",encoding="utf-8")
    (root/"clip.mp4").write_bytes(bytes(range(100)))
    (tmp_path/"outside.txt").write_text("private outside",encoding="utf-8")
    server=module.make_server(root,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(Request(base+"/clip.mp4",headers={"Range":"bytes=10-19"})) as response:
            assert response.status == 206 and response.read() == bytes(range(10,20))
            assert response.headers["Content-Range"] == "bytes 10-19/100"
            assert response.headers["Cache-Control"] == "private, no-store"
        with urlopen(Request(base+"/clip.mp4",headers={"Range":"bytes=-5"})) as response:
            assert response.read() == bytes(range(95,100))
        with urlopen(Request(base+"/clip.mp4",method="HEAD")) as response:
            assert response.headers["Content-Length"] == "100" and response.read() == b""
        for route,status in (("/%2e%2e/outside.txt",403),("/missing",404)):
            with pytest.raises(HTTPError) as error:
                urlopen(base+route)
            assert error.value.code == status
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base+"/clip.mp4",headers={"Range":"bytes=100-200"}))
        assert error.value.code == 416
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

"""Read-only loopback review server with video byte ranges and no directory listing."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


class PrivateReviewHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        root = Path(self.directory).resolve()
        requested = root / unquote(urlsplit(self.path).path).lstrip("/")
        path = requested.resolve()
        if not path.is_relative_to(root):
            self.send_error(403)
            return None
        if path.is_dir():
            path = path / "index.html"
        if not path.is_file() or not path.resolve().is_relative_to(root):
            self.send_error(404)
            return None
        size = path.stat().st_size
        start, end, status = 0, size-1, 200
        requested_range = self.headers.get("Range")
        if requested_range:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested_range)
            if not match or not any(match.groups()):
                self.send_error(416)
                return None
            left, right = match.groups()
            if left:
                start, end = int(left), min(size-1,int(right)) if right else size-1
            else:
                suffix = int(right)
                start = max(0,size-suffix)
            if not 0 <= start <= end < size:
                self.send_response(416)
                self.send_header("Content-Range",f"bytes */{size}")
                self.send_header("Content-Length","0")
                self.end_headers()
                return None
            status = 206
        handle = path.open("rb")
        handle.seek(start)
        self._bytes_remaining = end-start+1
        self.send_response(status)
        self.send_header("Content-Type",mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length",str(self._bytes_remaining))
        self.send_header("Accept-Ranges","bytes")
        self.send_header("Cache-Control","private, no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        if status == 206:
            self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
        self.end_headers()
        return handle

    def copyfile(self, source, outputfile):
        remaining = self._bytes_remaining
        try:
            while remaining:
                data = source.read(min(256*1024,remaining))
                if not data:
                    break
                outputfile.write(data)
                remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # Browser scrubbing cancels the previous media request.


def make_server(root: Path, port: int = 8765):
    root = root.resolve()
    if not (root / "index.html").is_file():
        raise ValueError("Supply a completed local review packet with index.html")
    return ThreadingHTTPServer(("127.0.0.1",port),partial(PrivateReviewHandler,directory=str(root)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args()
    with make_server(args.root,args.port) as server:
        print(f"Private local review: http://127.0.0.1:{server.server_port}/",flush=True)
        server.serve_forever()


if __name__=="__main__":
    main()

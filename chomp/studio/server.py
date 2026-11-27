"""Loopback-only HTTP API and buildless Studio frontend."""
from __future__ import annotations

from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
from shutil import disk_usage
from threading import BoundedSemaphore
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_STORED, ZipFile

from ..artifacts import artifact_key, artifact_lock, staged_file
from ..clipcontrol import ClipControl, PRESETS
from ..director.evidence import extract_frame
from ..media import probe
from ..reframe import oriented_size
from ..streambridge.platforms import inspect
from ..streambridge.source import BridgeError, embed, resolve
from .jobs import ID, Manager, RESERVE, read, safe_path

STATIC = Path(__file__).with_name("static")
_PATTERN = re.compile(r"/api/jobs/([0-9a-f]{32})(?:/(retry|cancel|clips|zip)(?:/([1-9][0-9]?)/(play|download|revise|feedback|frame))?)?\Z")
_SECURITY = ("default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; "
             "img-src 'self' blob: https://i.ytimg.com https://*.twitchcdn.net https://*.jtvnw.net; "
             "frame-src https://www.youtube.com https://player.twitch.tv https://clips.twitch.tv; "
             "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, root):
        self.manager = Manager(root)
        self.token = secrets.token_urlsafe(32)
        self.upload_limit = BoundedSemaphore(1)
        super().__init__(("127.0.0.1", port), StudioHandler)


class StudioHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, *args):
        # Manifests and URL queries may contain private information.
        pass

    def end_headers(self):
        self.send_header("Content-Security-Policy", _SECURITY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def response(self, body, status=200, kind="application/json; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, str(value))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def data(self, value, status=200):
        self.response(json.dumps(value, ensure_ascii=True, allow_nan=False).encode(), status)

    def body(self):
        length = self.content_length()
        if not 0 < length <= 65_536 or self.headers.get_content_type() != "application/json":
            raise ValueError("Send JSON no larger than 64 KiB")
        result = json.loads(self.rfile.read(length))
        if not isinstance(result, dict):
            raise ValueError("Send a JSON object")
        return result

    def content_length(self):
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length") or ()) != 1:
            raise ValueError("A single Content-Length header is required")
        return int(self.headers["Content-Length"])

    def _same_site(self):
        port = self.server.server_port
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        return host in {f"127.0.0.1:{port}", f"localhost:{port}"} and (not origin or origin == f"http://{host}")

    def do_GET(self):
        self.route()

    def do_HEAD(self):
        self.route()

    def do_POST(self):
        self.route()

    def route(self):
        try:
            if not self._same_site():
                self.close_connection = True
                return self.data({"error": {"message": "Studio accepts requests only from its local origin"}}, 403)
            path = urlsplit(self.path).path
            mutate = self.command == "POST"
            if mutate and not secrets.compare_digest(self.headers.get("X-Chomp-Local", ""), self.server.token):
                self.close_connection = True
                return self.data({"error": {"message": "Reload Studio to start a local session"}}, 403)
            if not mutate and path in {"/", "/studio.js", "/studio.css"}:
                name = "index.html" if path == "/" else path[1:]
                return self.response((STATIC / name).read_bytes(), kind=mimetypes.guess_type(name)[0] or "text/plain")
            self.dispatch(path, mutate)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except BridgeError as exc:
            self.close_connection = True
            self.data({"error": exc.public()}, 422)
        except (FileNotFoundError, ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
            self.close_connection = True
            status = 404 if isinstance(exc, FileNotFoundError) else 400
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError, FileNotFoundError)) else "The local operation did not complete."
            with suppress(BrokenPipeError, ConnectionResetError, TimeoutError):
                self.data({"error": {"message": message[:500]}}, status)

    def dispatch(self, path, mutate):
        manager = self.server.manager
        store = manager.store
        if not mutate and path == "/api/session":
            return self.data({"token": self.server.token, "presets": PRESETS})
        if not mutate and path == "/api/jobs":
            return self.data({"jobs": [store.public(row) for row in store.list()][:100]})
        if mutate and path == "/api/requirements":
            return self.data(ClipControl.parse(self.body()).public())
        if mutate and path == "/api/source":
            request = self.body()
            source = resolve(request["source"])
            if source.platform == "local":
                raise ValueError("Select a local file with the file picker")
            source, error = inspect(source)
            return self.data({"source": source.public(), "metadata_error": error,
                              "embed": embed(source, parent=self.headers["Host"].split(":")[0],
                                             origin="http://" + self.headers["Host"])})
        if mutate and path == "/api/uploads":
            if not self.server.upload_limit.acquire(blocking=False):
                raise ValueError("An upload is already running")
            try:
                result = store.upload(self.rfile, self.content_length(),
                                      unquote(self.headers.get("X-Filename", ""), errors="replace"))
            finally:
                self.server.upload_limit.release()
            return self.data(result, 201)
        if mutate and path == "/api/jobs":
            return self.data(manager.start(self.body()), 202)
        match = _PATTERN.fullmatch(path)
        if not match:
            raise FileNotFoundError("Endpoint not found")
        job, action, index, clip_action = match.groups()
        if not mutate and not action:
            return self.data(store.public(store.status(job)))
        if mutate and action in {"retry", "cancel"}:
            self.body()
            return self.data((manager.retry if action == "retry" else manager.cancel)(job), 202)
        if not mutate and action == "zip":
            target = self.zip(job, store)
            return self.file(target, download="chomp-clips.zip", media_type="application/zip")
        if not mutate and action == "clips" and not index:
            return self.data(store.clips(job))
        if action != "clips" or not index:
            raise FileNotFoundError("Endpoint not found")
        index = int(index)
        if mutate and clip_action == "revise":
            return self.data(manager.revise(job, index, **self.body()), 202)
        if mutate and clip_action == "feedback":
            return self.data(store.feedback(job, index, **self.body()), 201)
        if not mutate and clip_action in {"play", "download"}:
            row = store.clip(job, index)
            asset = safe_path(store.root, row["preview"])
            return self.file(asset, download=f"chomp-clip-{index:02d}.mp4" if clip_action == "download" else None)
        if not mutate and clip_action == "frame":
            row = store.clip(job, index)
            spec = read(safe_path(store.root, row["edit_spec"]))
            source = Path(spec["source"])
            video = next(s for s in probe(source)["streams"] if s["codec_type"] == "video")
            w, h = oriented_size(video)
            pixels, at, _, _ = extract_frame(source, spec["source_start_ms"])
            return self.response(pixels, kind="image/png", headers={"X-Source-Width": w, "X-Source-Height": h,
                                                                  "X-Source-Time-Ms": at})
        raise FileNotFoundError("Endpoint not found")

    @staticmethod
    def zip(jid, store):
        rows = store.clips(jid)["clips"]
        assets = [(row["index"], safe_path(store.root, store.clip(jid, row["index"])["preview"]))
                  for row in rows if row["has_video"]]
        if not assets:
            raise FileNotFoundError("No videos have been rendered")
        folder = store.folder(jid)
        key = artifact_key([(i, str(path), path.stat().st_size, path.stat().st_mtime_ns) for i, path in assets])[:16]
        destination = folder / "clips.zip"
        with artifact_lock(folder / ".zip.lock", timeout=20):
            stamp = folder / "zip-key.txt"
            if not (destination.is_file() and stamp.is_file() and stamp.read_text() == key):
                if disk_usage(folder).free < sum(path.stat().st_size for _, path in assets) + RESERVE:
                    raise ValueError("Insufficient free disk space for a ZIP download")
                with staged_file(destination) as pending:
                    with ZipFile(pending, "w", compression=ZIP_STORED) as archive:
                        for i, path in assets:
                            archive.write(path, f"chomp-clip-{i:02d}.mp4")
                    pending.replace(destination)
                stamp.write_text(key)
        return destination

    def file(self, path, *, download=None, media_type="video/mp4"):
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            start, end = 0, size - 1
            r = self.headers.get("Range")
            if r:
                match = re.fullmatch(r"bytes=([0-9]{0,16})-([0-9]{0,16})", r)
                if match and any(match.groups()):
                    a, b = match.groups()
                    start, end = (int(a), min(int(b), end) if b else end) if a else (max(0, size - int(b)), end)
                if not match or not (0 <= start <= end < size) or not any(match.groups()):
                    return self.response(b"", 416, media_type, {"Content-Range": f"bytes */{size}"})
            self.send_response(206 if r else 200)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            if r:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                data = stream.read(min(remaining, 256 * 1024))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)


def serve(port, root):
    with StudioServer(port, root) as server:
        print(f"Chomp-Chomp Studio • http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if server.manager.active:
                server.manager.stop_tree(server.manager.active)
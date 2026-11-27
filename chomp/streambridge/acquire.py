"""Acquisition jobs and integrity-checked, content-addressed media."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from shutil import disk_usage
from time import monotonic, sleep
from uuid import uuid4

from ..artifacts import artifact_key, artifact_lock, digest_file, source_identity, staged_file, write_json
from ..media import exec_checked, probe
from .network import media_target, open_media
from .platforms import Twitch, inspect
from .source import BridgeError, Source, resolve


JOB = re.compile(r"[0-9a-f]{32}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
REMOTE_FAILURES = {"network_error", "rate_limited", "incomplete_transfer"}


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 8 * 1024**3
    min_free_bytes: int = 256 * 1024**2
    timeout_seconds: float = 15
    total_seconds: float = 1800
    retries: int = 2

    def __post_init__(self):
        if (not 1 <= self.max_bytes <= 128 * 1024**3 or not 0 <= self.min_free_bytes <= 128 * 1024**3 or
                not 1 <= self.timeout_seconds <= 60 or not 1 <= self.total_seconds <= 21_600 or not 0 <= self.retries <= 5):
            raise BridgeError("invalid_limit", "Acquisition sizes, timeouts or retry counts are out of range.")


class Jobs:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def directory(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not JOB.fullmatch(job_id):
            raise BridgeError("invalid_job", "Use the 32-character job ID reported by StreamBridge.")
        path = self.root / "jobs" / job_id
        if path.resolve() != path:
            raise BridgeError("invalid_job", "Job directory escapes the configured storage.")
        return path

    def create(self, kind, source, job_id=None):
        job_id = job_id or uuid4().hex
        folder = self.directory(job_id)
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise BridgeError("job_exists", "Choose a new job ID or inspect the existing job.") from None
        data = {"schema_version": "chomp-source-job/1", "job_id": job_id, "kind": kind,
                "status": "queued", "source": source, "created_at": now()}
        self.write(data)
        return data

    def write(self, data):
        write_json(self.directory(data["job_id"]) / "status.json", {**data, "updated_at": now()})

    def status(self, job_id):
        try:
            data = json.loads((self.directory(job_id) / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise BridgeError("job_unavailable", "No readable job with that ID exists in this workspace.") from None
        if data.get("job_id") != job_id or data.get("schema_version") != "chomp-source-job/1":
            raise BridgeError("invalid_job", "Job provenance does not match.")
        return data

    def cancel(self, job_id):
        data = self.status(job_id)
        if data["status"] in {"completed", "cancelled", "failed", "stopped", "ended"}:
            return data
        (self.directory(job_id) / "cancel").touch()
        return {**data, "cancel_requested": True}

    def check_cancel(self, job_id):
        if (self.directory(job_id) / "cancel").exists():
            raise BridgeError("cancelled", "The job was cancelled.")


@dataclass(frozen=True)
class Acquired:
    path: Path
    source: Source
    method: str
    media_id: str
    duration_ms: int
    job_id: str
    cache_hit: bool = False

    def provenance(self, *, transient_metadata=False):
        # Metadata can change independently of the media. The digest remains the ASR cache identity.
        return {"schema_version": "chomp-source-link/1", "source": self.source.public() if transient_metadata else self.source.reference(), "method": self.method,
                "media_id": self.media_id, "media_duration_ms": self.duration_ms,
                "timeline": "media_relative_ms"}

    def public(self, *, transient_metadata=True):
        return {**self.provenance(transient_metadata=transient_metadata), "path": str(self.path), "job_id": self.job_id, "cache_hit": self.cache_hit}


def assert_permission(rights_ref: str, confirmed: bool):
    if not confirmed or not rights_ref.strip() or len(rights_ref) > 1000:
        raise BridgeError("permission_required", "Supply an authorization reference and explicitly acknowledge permission.")


def verify_local(path: Path, *, remote: bool = False, transport_only: bool = False) -> dict:
    if not path.is_file():
        raise BridgeError("media_unavailable", "The attached recording file is unavailable.")
    try:
        if remote:
            with path.open("rb") as f:
                head = f.read(600)
            mp4 = len(head) >= 8 and head[4:8] in {b"ftyp", b"moov", b"mdat", b"wide", b"free"}
            ebml = head.startswith(b"\x1a\x45\xdf\xa3")
            transport = len(head) >= 377 and all(head[i] == 0x47 for i in (0, 188, 376))
            avi = head.startswith(b"RIFF") and head[8:12] == b"AVI "
            if not (mp4 or ebml or transport or avi) or (transport_only and not transport):
                raise ValueError("Unsupported downloaded media container.")
            # Limit the demuxers before the general editor accepts the local file.
            res = exec_checked(["ffprobe", "-v", "error", "-protocol_whitelist", "file",
                                "-format_whitelist", "mpegts" if transport_only else "mov,matroska,webm,mpegts,avi", "-show_streams",
                                "-show_format", "-of", "json", str(path)], timeout=45)
            data = json.loads(res.stdout)
            duration = round(float(data["format"]["duration"]) * 1000)
            media = {"duration_ms": duration, "streams": data["streams"]}
        else:
            media = probe(path)
        kinds = {s.get("codec_type") for s in media["streams"]}
        if not {"audio", "video"} <= kinds or media["duration_ms"] <= 0:
            raise ValueError("The editing pipeline requires a finite video with an audio track.")
        return media
    except (OSError, RuntimeError, ValueError, TypeError, OverflowError, KeyError):
        raise BridgeError("invalid_media", "The file is not a complete supported video with an audio track.") from None


def transfer(url: str, dest: Path, limits: Limits, *, native=False, expected=None, progress=lambda count, total: None,
             cancel=lambda: None, opener=open_media) -> tuple[str, int]:
    deadline = monotonic() + limits.total_seconds
    cancel()
    for attempt in range(limits.retries + 1):
        try:
            return _transfer_once(url, dest, limits, native, expected, progress, cancel, opener, deadline)
        except BridgeError as exc:
            if exc.code not in REMOTE_FAILURES or attempt >= limits.retries:
                raise
            delay = max(2 ** attempt, exc.retry_after_seconds or 0)
            if delay > 30 or monotonic() + delay >= deadline:
                raise
            until = monotonic() + delay
            while monotonic() < until:
                cancel()
                sleep(min(.25, max(0, until - monotonic())))
    raise AssertionError("retry loop exhausted")


def _transfer_once(url, dest, limits, native, expected, progress, cancel, opener, deadline):
    def check():
        cancel()
        if monotonic() >= deadline:
            raise BridgeError("timeout", "The transfer exceeded its total time limit.")

    check()
    with opener(url, timeout=min(limits.timeout_seconds, max(1, deadline - monotonic())), native=native) as (r, _):
        check()
        size = r.getheader("Content-Length")
        if size is not None and (not size.isascii() or not size.isdigit() or len(size) > 16):
            raise BridgeError("invalid_response", "The media server returned an invalid content length.")
        total = int(size) if size is not None else None
        mime = r.getheader("Content-Type", "").lower().split(";")[0]
        if mime.startswith("text/") or mime in {"application/json", "application/vnd.apple.mpegurl", "application/x-mpegurl"}:
            raise BridgeError("media_unavailable", "The recording URL did not return a video file.")
        free = disk_usage(dest.parent).free - limits.min_free_bytes
        if total is not None and (total <= 0 or total > limits.max_bytes or total > free):
            raise BridgeError("storage_limit", "The recording exceeds the transfer size or disk-space budget.")
        if free <= 0:
            raise BridgeError("storage_limit", "The workspace is below its free-space reserve.")
        count, last = 0, 0
        digest = sha256()
        progress(count, total)
        with dest.open("wb") as f:
            while True:
                check()
                reader = getattr(r, "read1", r.read)
                block = reader(256 * 1024)
                if not block:
                    break
                count += len(block)
                if count > limits.max_bytes or count > free:
                    raise BridgeError("storage_limit", "The transfer reached its disk-space or size budget.")
                digest.update(block)
                f.write(block)
                if count - last >= 4 * 1024**2:
                    if disk_usage(dest.parent).free < limits.min_free_bytes:
                        raise BridgeError("storage_limit", "Free disk space dropped below the reserve.")
                    last = count
                    progress(count, total)
        check()
        if not count or (total is not None and count != total):
            raise BridgeError("incomplete_transfer", "The transfer ended before the advertised recording was complete.")
        actual = digest.hexdigest()
        if expected and expected != actual:
            raise BridgeError("integrity_error", "The recording SHA-256 did not match the supplied digest.")
        progress(count, total)
        return actual, count


def _cache_file(root: Path, key: str) -> Path:
    path = root / "media" / f"{key}.mp4"
    if not SHA.fullmatch(key) or path.resolve() != path:
        raise BridgeError("integrity_error", "The media cache contains an invalid file reference.")
    return path


def acquire(value: str, root: Path, *, rights_ref: str, confirmed: bool,
            media_file: Path | None = None, media_url: str | None = None, twitch_download: bool = False,
            portrait: bool = False, expected_sha256: str | None = None, limits: Limits = Limits(),
            job_id: str | None = None, progress=lambda state: None, youtube=None, twitch=None, opener=open_media) -> Acquired:
    assert_permission(rights_ref, confirmed)
    if sum((media_file is not None, media_url is not None, twitch_download)) > 1 or (portrait and not twitch_download):
        raise BridgeError("invalid_acquisition", "Choose one acquisition method. Portrait requires a Twitch clip download.")
    if expected_sha256:
        expected_sha256 = expected_sha256.lower()
        if not SHA.fullmatch(expected_sha256):
            raise BridgeError("integrity_error", "Supply a 64-character hexadecimal SHA-256 digest.")
    source = resolve(value, kind="creator_https" if media_url == value else "source")
    if source.platform in {"youtube", "twitch"} and media_file is None and not media_url and not twitch_download:
        raise BridgeError("media_unavailable", "Attach a creator original or authorized HTTPS media. For live capture, use chomp live start.")
    if source.platform == "local" and (media_file or media_url or twitch_download):
        raise BridgeError("invalid_acquisition", "The local source already identifies its recording.")
    method = ("twitch_clip_download" if twitch_download else "creator_file" if media_file else
              "creator_https" if media_url or source.platform == "creator" else "local_file")
    url = media_url or (value if source.platform == "creator" else None)
    if url:
        media_target(url)
    if twitch_download and (source.platform != "twitch" or source.source_type != "clip"):
        raise BridgeError("invalid_source", "Native Twitch downloads require a clip URL.")

    jobs = Jobs(root)
    state = jobs.create("acquisition", source.reference(), job_id)

    def update(**parts):
        state.update(parts)
        jobs.write(state)
        progress(dict(state))

    cache_hit = False
    try:
        update(status="running", method=method, bytes_received=0, bytes_total=None)
        jobs.check_cancel(state["job_id"])
        source, metadata_error = inspect(source, youtube=youtube, twitch=twitch)
        update(source=source.reference(), metadata_error=metadata_error,
               metadata_lookup_status=source.metadata_status)
        if method in {"local_file", "creator_file"}:
            path = (media_file or Path(value)).expanduser().resolve()
            data = verify_local(path)
            identity = source_identity(path)
            if expected_sha256 and identity != "sha256:" + expected_sha256:
                raise BridgeError("integrity_error", "The recording SHA-256 did not match the supplied digest.")
            if source.platform == "local":
                source = replace(source, source_id=identity, duration_ms=data["duration_ms"])
        else:
            if twitch_download:
                url = (twitch or Twitch.from_env()).download(source, portrait=portrait)
                media_target(url, native=True)
            key = artifact_key(["acquisition/1", source.platform, source.source_id, method, portrait,
                                expected_sha256, sha256(url.encode()).hexdigest() if not twitch_download else None])
            receipt = jobs.root / "receipts" / f"{key}.json"
            with artifact_lock(jobs.root / "locks" / f"{key}.lock", timeout=5):
                jobs.check_cancel(state["job_id"])
                result = None
                if receipt.is_file():
                    try:
                        saved = json.loads(receipt.read_text(encoding="utf-8"))
                        path = _cache_file(jobs.root, saved["sha256"])
                        if (path.is_file() and path.stat().st_size <= limits.max_bytes and
                                (not expected_sha256 or saved["sha256"] == expected_sha256)
                                and digest_file(path) == saved["sha256"]):
                            result = (saved["sha256"], verify_local(path, remote=True))
                            cache_hit = True
                    except (OSError, ValueError, KeyError, BridgeError):
                        pass
                if result is None:
                    with staged_file(jobs.root / "transfers" / "recording.part") as pending:
                        actual, _ = transfer(url, pending, limits, native=twitch_download, expected=expected_sha256,
                                             progress=lambda n, total: update(bytes_received=n, bytes_total=total),
                                             cancel=lambda: jobs.check_cancel(state["job_id"]), opener=opener)
                        data = verify_local(pending, remote=True)
                        jobs.check_cancel(state["job_id"])
                        path = _cache_file(jobs.root, actual)
                        with artifact_lock(jobs.root / "locks" / f"blob-{actual}.lock", timeout=5):
                            path.parent.mkdir(parents=True, exist_ok=True)
                            if not path.is_file() or digest_file(path) != actual:
                                pending.replace(path)
                        write_json(receipt, {"sha256": actual})
                    result = (actual, data)
                actual, data = result
                identity = "sha256:" + actual
        jobs.check_cancel(state["job_id"])
        source = replace(source, authorization_status="operator_confirmed")
        acquired = Acquired(path, source, method, identity, data["duration_ms"], state["job_id"], cache_hit)
        update(status="completed", source=source.reference(), acquired=acquired.public(transient_metadata=False))
        return acquired
    except KeyboardInterrupt:
        update(status="cancelled")
        raise
    except (BridgeError, OSError, RuntimeError, ValueError) as exc:
        error = exc if isinstance(exc, BridgeError) else BridgeError("acquisition_failed", "Could not complete acquisition or update local storage.")
        update(status="cancelled" if error.code == "cancelled" else "failed", error=error.public())
        raise error from None


def reopen(root: Path, job_id: str, source: Source, *, rights_ref: str, confirmed: bool) -> Acquired:
    assert_permission(rights_ref, confirmed)
    record = Jobs(root).status(job_id)
    if record["status"] != "completed" or record["kind"] != "acquisition":
        raise BridgeError("media_unavailable", "This job has no completed recording.")
    data = record["acquired"]
    descriptor = {k: v for k, v in data["source"].items() if k != "schema_version"}
    saved = Source(**descriptor)
    if (source.platform, source.source_id) != (saved.platform, saved.source_id):
        raise BridgeError("source_mismatch", "The acquisition job belongs to a different source.")
    path = Path(data["path"])
    if not path.is_file() or source_identity(path) != data["media_id"]:
        raise BridgeError("integrity_error", "The acquired recording changed or is unavailable.")
    return Acquired(path, saved, data["method"], data["media_id"], data["media_duration_ms"], job_id, True)

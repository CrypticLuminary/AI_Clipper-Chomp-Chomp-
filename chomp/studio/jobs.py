"""Persistent local requests and a bounded subprocess supervisor."""
from __future__ import annotations

from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
from threading import Lock, Thread
from time import monotonic, sleep
from uuid import uuid4

from ..artifacts import artifact_lock, staged_file, write_json
from ..clipcontrol import ClipControl, PRESETS
from ..streambridge.source import BridgeError, resolve

ID = re.compile(r"[0-9a-f]{32}\Z")
TERMINAL = {"completed", "partial", "failed", "cancelled"}
MAX_UPLOAD = 4 * 1024**3
RESERVE = 256 * 1024**2


def utc():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_path(root, path):
    root = root.resolve()
    path = Path(path).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("The file is outside the job workspace")
    return path


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, job):
        if not isinstance(job, str) or not ID.fullmatch(job):
            raise ValueError("Invalid job identifier")
        return safe_path(self.root, self.root / "jobs" / job)

    def status(self, job):
        path = self.folder(job)
        with artifact_lock(path / ".state.lock", timeout=3) if path.is_dir() else suppress():
            if not (path / "state.json").is_file():
                raise FileNotFoundError("Job not found")
            return read(path / "state.json")

    def update(self, job, **fields):
        path = self.folder(job)
        with artifact_lock(path / ".state.lock", timeout=4):
            state = read(path / "state.json") if (path / "state.json").exists() else {"id": job, "created_at": utc()}
            events = state.get("events", [])
            if fields.get("stage") and not any(event["stage"] == fields["stage"] for event in events):
                events = [*events[-79:], {"stage": fields["stage"], "at": utc()}]
            state.update(fields, updated_at=utc(), events=events)
            write_json(path / "state.json", state)
            return state

    def list(self):
        base = self.root / "jobs"
        if not base.exists():
            return []
        items = []
        for path in base.iterdir():
            if ID.fullmatch(path.name):
                with suppress(OSError, ValueError, RuntimeError):
                    items.append(self.status(path.name))
        return sorted(items, key=lambda j: j["created_at"], reverse=True)

    def upload(self, stream, length, name):
        if type(length) is not int or not 1 <= length <= MAX_UPLOAD:
            raise ValueError("Select a nonempty video no larger than 4 GiB")
        ext = Path(name).suffix.lower()
        if ext not in {".mp4", ".mov", ".mkv", ".webm", ".ts", ".avi", ".m4v", ".json"}:
            raise ValueError("Choose an MP4, MOV, MKV, WebM, TS, AVI, M4V or transcript JSON")
        if ext == ".json" and length > 16 * 1024**2:
            raise ValueError("Transcript JSON must be smaller than 16 MiB")
        if shutil.disk_usage(self.root).free < length + RESERVE:
            raise ValueError("Insufficient free disk space for this upload")
        ident = uuid4().hex
        folder = safe_path(self.root, self.root / "uploads" / ident)
        folder.mkdir(parents=True)
        path = folder / ("upload" + ext)
        try:
            with staged_file(path) as tmp:
                remaining = length
                digest = sha256()
                with tmp.open("wb") as out:
                    while remaining:
                        data = stream.read(min(remaining, 256 * 1024))
                        if not data:
                            raise ValueError("Upload stopped before all bytes arrived")
                        out.write(data)
                        digest.update(data)
                        remaining -= len(data)
                        if shutil.disk_usage(self.root).free < RESERVE:
                            raise ValueError("The workspace reached its free-space reserve")
                ident = sha256((digest.hexdigest() + ext).encode()).hexdigest()[:32]
                shared = safe_path(self.root, self.root / "uploads" / ident)
                with artifact_lock(self.root / ".uploads" / f"{ident}.lock", timeout=10):
                    shared.mkdir(exist_ok=True)
                    target = shared / path.name
                    if not target.exists():
                        tmp.replace(target)
            folder.rmdir()
        except BaseException:
            with suppress(OSError):
                folder.rmdir()
            raise
        return {"id": ident, "name": Path(name.replace("\\", "/")).name[:180], "bytes": length}

    def asset(self, ident, *, transcript=False):
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise ValueError("Invalid upload identifier")
        folder = safe_path(self.root, self.root / "uploads" / ident)
        files = list(folder.glob("upload.*"))
        if len(files) != 1 or (files[0].suffix == ".json") != transcript:
            raise FileNotFoundError("Upload is missing or the wrong type")
        return safe_path(folder, files[0])

    def presets(self):
        path = self.root / "presets.json"
        with artifact_lock(self.root / ".presets.lock", timeout=4):
            return {**PRESETS, **(read(path) if path.exists() else {})}

    def save_preset(self, name, values):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}", name) or name in PRESETS:
            raise ValueError("Use a new preset name with letters, numbers, spaces, hyphens or underscores")
        settings = ClipControl.parse(values)
        path = self.root / "presets.json"
        with artifact_lock(self.root / ".presets.lock", timeout=4):
            current = read(path) if path.exists() else {}
            if len(current) >= 30 and name not in current:
                raise ValueError("The local preset limit is 30")
            current[name] = {k: getattr(settings, k) for k in settings.__dataclass_fields__}
            write_json(path, current)
        return self.presets()

    def public(self, state):
        # Paths and the original provider responses are available only to the local worker.
        return {k: v for k, v in state.items() if k not in {"manifest", "revision_report"}}

    def manifest(self, jid):
        state = self.status(jid)
        path = state.get("manifest")
        if not path:
            raise FileNotFoundError("This job has no completed review")
        return read(safe_path(self.root, path))

    def clips(self, jid):
        state = self.status(jid)
        if not state.get("manifest"):
            return {"clips": [], "count_result": state.get("count_result")}
        manifest = self.manifest(jid)
        revisions = {}
        for job in self.list():
            if job.get("parent") == jid and job["status"] == "completed" and job.get("revision_report"):
                revisions.setdefault(job["clip_index"], job)
        output = []
        for idx, raw in enumerate(manifest["clips"], 1):
            item = raw.get("candidate", raw)
            latest = revisions.get(idx)
            row = read(safe_path(self.root, latest["revision_report"])) if latest else raw
            offset = raw.get("window_start_ms", 0)
            start, end = ((row["start_ms"] + offset, row["end_ms"] + offset) if latest else
                          (item["start_ms"], item["end_ms"]))
            feedback = self.folder(jid) / f"feedback-{idx}.json"
            preview_hash = row.get("artifact_sha256", {}).get("preview")
            current_feedback = next((f for f in reversed(read(feedback)) if f.get("preview_sha256") == preview_hash),
                                    None) if feedback.is_file() else None
            output.append({"index": idx, "candidate_id": raw.get("candidate_id"),
                "start_ms": start, "end_ms": end, "media_duration_ms": row.get("media_duration_ms"),
                "title": row.get("title"), "description": item.get("transcript_excerpt"),
                "reason": item.get("rationale"), "ranking_method": item.get("ranking_method"),
                "warnings": row.get("warnings", []), "layout": row.get("layout"), "width": row.get("width"),
                "height": row.get("height"), "has_video": bool(row.get("preview")),
                "revision": latest["id"] if latest else None,
                "feedback": current_feedback, "encoder": row.get("encoder")})
        return {"clips": output, "count_result": manifest.get("count_result"), "source_duration_ms": manifest.get("duration_ms"),
                "requirements": manifest.get("requirements")}

    def clip(self, job, index, *, latest=True):
        manifest = self.manifest(job)
        if type(index) is not int or not 1 <= index <= len(manifest["clips"]):
            raise FileNotFoundError("Clip not found in this job")
        if latest:
            for row in self.list():
                if (row.get("parent"), row.get("clip_index"), row["status"]) == (job, index, "completed"):
                    return read(safe_path(self.root, row["revision_report"]))
        return manifest["clips"][index - 1]

    def feedback(self, job, index, decision, note=""):
        clip = self.clip(job, index)
        if decision not in {"approve", "reject", "uncertain"} or not isinstance(note, str) or len(note) > 1000:
            raise ValueError("Choose approve, reject or uncertain and an optional note up to 1000 characters")
        folder = self.folder(job)
        path = folder / f"feedback-{index}.json"
        with artifact_lock(folder / ".feedback.lock", timeout=3):
            rows = read(path) if path.exists() else []
            if len(rows) >= 1000:
                raise ValueError("This clip has reached its feedback history limit")
            row = {"decision": decision, "note": note, "at": utc(),
                   "preview_sha256": clip.get("artifact_sha256", {}).get("preview")}
            write_json(path, [*rows, row])
        return row


class Manager:
    def __init__(self, root):
        self.store = Store(root)
        self.lock = Lock()
        self.waiting = deque()
        self.thread = None
        self.active = None
        self.secrets = {}

    def start(self, spec, *, trusted_local=False):
        if not isinstance(spec, dict):
            raise ValueError("Job request must be an object")
        fields = {"source", "media_url", "feed_url", "upload", "transcript_upload", "rights_ref", "confirmed",
                  "requirements", "provider_model", "provider_confirmed", "transcribe", "asr_model", "asr_device",
                  "twitch_download", "timeout", "kind", "live_seconds", "local_file", "local_transcript", "local_spool"}
        if set(spec) - fields:
            raise ValueError("Unknown job parameters")
        policy = ClipControl.parse(spec.get("requirements", {}))
        if spec.get("confirmed") is not True or not isinstance(spec.get("rights_ref"), str) \
                or not spec["rights_ref"].strip() or len(spec["rights_ref"]) > 1000:
            raise ValueError("Provide a rights reference and confirm permission")
        if not trusted_local and any(k in spec for k in ("local_file", "local_transcript", "local_spool")):
            raise ValueError("Browser requests must use uploaded files")
        for field in ("upload", "transcript_upload"):
            if spec.get(field):
                self.store.asset(spec[field], transcript=field == "transcript_upload")
        if spec.get("provider_model") and (spec.get("provider_confirmed") is not True or
                not isinstance(spec["provider_model"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", spec["provider_model"])):
            raise ValueError("Confirm sending bounded source evidence to the named Director model")
        if not trusted_local and spec.get("asr_model") not in {None, "tiny", "base", "small", "medium"}:
            raise ValueError("Choose an installed Whisper model name")
        if spec.get("asr_device", "cpu") not in {"cpu", "cuda"}:
            raise ValueError("Choose CPU or CUDA for Whisper")
        timeout = spec.get("timeout", 3600)
        if type(timeout) is not int or not 10 <= timeout <= 7200:
            raise ValueError("Job timeout must be between 10 and 7200 seconds")
        kind = spec.get("kind", "recording")
        if kind not in {"recording", "live", "monitor"}:
            raise ValueError("Select a recording or an authorized live feed")
        if kind in {"live", "monitor"} and (type(spec.get("live_seconds", 300)) is not int or
                                not 1 <= spec.get("live_seconds", 300) <= 3600):
            raise ValueError("Live session time must be between 1 and 3600 seconds")
        source = spec.get("source")
        secrets = {k: spec[k] for k in ("media_url", "feed_url") if spec.get(k)}
        if any(not isinstance(v, str) or not v.startswith("https://") or len(v) > 8192 for v in secrets.values()):
            raise ValueError("Supply HTTPS creator media")
        canonical = None
        descriptor = None
        if source:
            if not isinstance(source, str) or not source.startswith(("http://", "https://")):
                raise ValueError("Use a Twitch/YouTube URL or attach a local recording")
            descriptor = resolve(source, kind="creator_live_feed" if kind == "live" else "source")
            canonical = descriptor.canonical_url
            if descriptor.platform == "creator":
                secrets["source"] = source
        if kind == "monitor" and (not descriptor or descriptor.platform not in {"youtube", "twitch"} or secrets
                                  or spec.get("upload") or spec.get("local_file") or spec.get("local_spool")):
            raise ValueError("Monitoring requires a public YouTube video or Twitch channel URL without attached media")
        if (kind == "recording" and descriptor and descriptor.platform in {"youtube", "twitch"} and
                not (spec.get("upload") or spec.get("local_file") or secrets.get("media_url") or spec.get("twitch_download"))):
            raise BridgeError("media_unavailable",
                "Attach the creator's original recording or an authorized HTTPS file. The platform share URL supplies metadata and playback.")
        if kind == "live" and not (secrets.get("feed_url") or secrets.get("source") or spec.get("local_spool")):
            raise BridgeError("media_unavailable",
                "This platform URL supports monitoring and playback. Attach a creator-authorized HLS media playlist to capture clips.")
        if not (source or spec.get("upload") or spec.get("local_file") or spec.get("local_spool") or secrets):
            raise ValueError("Supply a source or a recording")
        if spec.get("twitch_download") and (spec.get("upload") or spec.get("local_file") or secrets):
            raise ValueError("Choose one media acquisition method")
        config = {**{k: v for k, v in spec.items() if k not in {"source", "media_url", "feed_url"}},
                  "source": canonical, "requirements": {k: getattr(policy, k) for k in policy.__dataclass_fields__},
                  "timeout": timeout, "kind": kind, "needs_secret_url": bool(secrets)}
        return self._enqueue(config, secrets)

    def revise(self, parent, clip_index, *, start_ms, end_ms, facecam_roi=None, gameplay_roi=None, protected_hud_roi=None, layout=None):
        if type(start_ms) is not int or type(end_ms) is not int:
            raise ValueError("Boundary adjustments use integer milliseconds")
        clip = self.store.clip(parent, clip_index)
        edit_path = safe_path(self.store.root, clip["edit_spec"])
        plan = read(edit_path)
        if self.store.status(parent)["kind"] == "live":
            original = self.store.clip(parent, clip_index, latest=False)
            shift = original.get("window_start_ms", 0)
            start_ms, end_ms = start_ms - shift, end_ms - shift
        requirements = ClipControl.parse(plan["requirements"]) if plan.get("requirements") else None
        if requirements:
            requirements.check_span(start_ms, end_ms, plan["source_duration_ms"])
        elif not 0 <= start_ms < end_ms <= plan["source_duration_ms"]:
            raise ValueError("Boundaries are outside the source")
        if any((facecam_roi, gameplay_roi, protected_hud_roi, layout)):
            ClipControl(layout=layout or "shorts", facecam_roi=facecam_roi, gameplay_roi=gameplay_roi,
                        protected_hud_roi=protected_hud_roi)
        return self._enqueue({"kind": "revision", "parent": parent, "clip_index": clip_index,
                              "edit_path": str(edit_path), "start_ms": start_ms, "end_ms": end_ms,
                              "facecam_roi": facecam_roi, "gameplay_roi": gameplay_roi, "protected_hud_roi": protected_hud_roi, "layout": layout,
                              "timeout": 900}, {})

    def _enqueue(self, config, secrets, retry_of=None):
        with self.lock:
            if len(self.waiting) >= 12:
                raise ValueError("The local job queue is full")
            job = uuid4().hex
            path = self.store.folder(job)
            path.mkdir(parents=True)
            write_json(path / "request.json", config)
            label = config.get("source") or ("Authorized live feed" if config["kind"] == "live" else "Local recording")
            if config["kind"] == "revision":
                label = f"Revision of clip {config['clip_index']}"
            state = self.store.update(job, status="queued", stage="queued", label=label, kind=config["kind"],
                    retry_of=retry_of, parent=config.get("parent"), clip_index=config.get("clip_index"))
            self.secrets[job] = secrets
            self.waiting.append(job)
            if not self.thread or not self.thread.is_alive():
                self.thread = Thread(target=self._drain, daemon=True)
                self.thread.start()
            return self.store.public(state)

    def retry(self, job):
        if self.store.status(job)["status"] not in TERMINAL:
            raise ValueError("Wait for the existing job to finish or cancel it before retrying")
        config = read(self.store.folder(job) / "request.json")
        secrets = self.secrets.get(job, {})
        if config.get("needs_secret_url") and not secrets:
            raise ValueError("Re-enter the creator media URL after a Studio restart")
        return self._enqueue(config, secrets, retry_of=job)

    def cancel(self, job):
        state = self.store.status(job)
        if state["status"] in TERMINAL:
            return self.store.public(state)
        (self.store.folder(job) / "stop").touch()
        if state["status"] == "queued":
            return self.store.public(self.store.update(job, status="cancelled", stage="cancelled"))
        return self.store.public(state) | {"cancel_requested": True}

    def _drain(self):
        while True:
            with self.lock:
                if not self.waiting:
                    self.thread = None
                    return
                job = self.waiting.popleft()
            folder = self.store.folder(job)
            if (folder / "stop").exists():
                self.store.update(job, status="cancelled", stage="cancelled")
                continue
            try:
                self._supervise(job)
            except (OSError, RuntimeError, ValueError):
                self.store.update(job, status="failed", stage="failed", error="Could not start the local processing worker.")

    def _supervise(self, job):
        folder = self.store.folder(job)
        config = read(folder / "request.json")
        command = [sys.executable, "-m", "chomp.studio.worker", str(self.store.root), job]
        platform_options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
                            if os.name == "nt" else {"start_new_session": True})
        self.store.update(job, status="running", stage="starting")
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              **platform_options) as child:
            self.active = child
            try:
                try:
                    child.stdin.write(json.dumps(self.secrets.get(job, {})).encode("utf-8"))
                    child.stdin.close()
                except OSError:
                    pass
                deadline = monotonic() + config["timeout"]
                while child.poll() is None:
                    cancelled = (folder / "stop").exists()
                    if cancelled or monotonic() >= deadline:
                        self.stop_tree(child)
                        self.store.update(job, status="cancelled" if cancelled else "failed",
                                          stage="cancelled" if cancelled else "failed",
                                          **({} if cancelled else {"error": "Job exceeded the processing time limit."}))
                        return
                    sleep(.15)
                if self.store.status(job)["status"] not in TERMINAL:
                    self.store.update(job, status="failed", stage="failed", error="The local worker exited before saving a result.")
            finally:
                self.active = None

    @staticmethod
    def stop_tree(child):
        if child.poll() is not None:
            return
        if os.name == "nt":
            with suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(["taskkill.exe", "/PID", str(child.pid), "/T", "/F"], timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)

    def wait(self, job):
        while True:
            state = self.store.status(job)
            if state["status"] in TERMINAL:
                return self.store.public(state)
            sleep(.25)

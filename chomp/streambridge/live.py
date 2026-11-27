"""Rolling local spool or creator HTTPS HLS ingest and delayed review finalization."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from shutil import disk_usage
from time import monotonic, sleep
from urllib.parse import urljoin

from ..artifacts import artifact_key, digest_file, staged_file, write_json
from ..asr import LocalTranscriber
from ..evolution import EvolutionConfig
from ..clipcontrol import ClipControl, MAX_CLIPS
from ..manual import render_manual_preview
from ..media import check_audio_track, decode_audio_window, exec_checked, probe
from ..pipeline import process
from .acquire import Jobs, Limits, assert_permission, transfer, verify_local
from .network import media_target, open_media
from .platforms import _utc, inspect
from .source import BridgeError, Source, resolve


@dataclass(frozen=True)
class Chunk:
    sequence: int
    location: str
    duration_ms: int | None = None
    program_time: str | None = None
    discontinuity: bool = False
    gap: bool = False


@dataclass(frozen=True)
class LiveOptions:
    retention_seconds: int = 180
    context_seconds: int = 70
    lookahead_seconds: int = 15
    poll_seconds: float = 3
    max_seconds: float = 3600
    idle_seconds: float = 60
    start_ms: int | None = None
    broadcast_start: str | None = None
    max_buffer_bytes: int = 2 * 1024**3
    max_clips: int = 3
    min_ms: int = 6000
    max_ms: int = 45000
    audio_stream: int = 0
    layout: str = "shorts"
    profile: str = "preview"
    hardware_profile: str = "eco"
    asr_model: str | None = None
    asr_device: str = "cpu"
    render: bool = False
    requirements: ClipControl | None = None
    evolution: EvolutionConfig | None = None
    provider: object | None = None

    def __post_init__(self):
        if self.requirements:
            req = self.requirements
            for field, value in {"max_clips": req.clips, "min_ms": req.bounds[0], "max_ms": req.bounds[1],
                "audio_stream": req.audio_stream, "layout": req.layout, "hardware_profile": req.hardware}.items():
                object.__setattr__(self, field, value)
        if (not 8 <= self.context_seconds <= self.retention_seconds <= 3600 or
                not 0 <= self.lookahead_seconds < self.context_seconds or
                not .1 <= self.poll_seconds <= 30 or not 1 <= self.max_seconds <= 21600 or
                not self.poll_seconds <= self.idle_seconds <= 600 or not 1 <= self.max_buffer_bytes <= 16 * 1024**3 or
                type(self.max_clips) is not int or not 0 <= self.max_clips <= MAX_CLIPS or
                not 1000 <= self.min_ms <= self.max_ms <= 60_000 or
                self.audio_stream < 0 or (self.start_ms is not None and self.start_ms < 0)):
            raise BridgeError("invalid_limit", "Live retention, lookahead, timing, disk or clip settings are invalid.")
        if self.broadcast_start is not None:
            try:
                _utc(self.broadcast_start)
            except ValueError:
                raise BridgeError("invalid_timeline", "Use an ISO timestamp with a UTC offset for broadcast start.") from None


_SPOOL = re.compile(r"([0-9]{1,14})\.ts\Z", re.ASCII | re.IGNORECASE)


def local_playlist(folder: Path):
    chunks = []
    if not folder.is_dir():
        raise BridgeError("media_unavailable", "The producer directory is unavailable.")
    for entry in folder.iterdir():
        match = _SPOOL.fullmatch(entry.name)
        if match and entry.is_file() and not entry.is_symlink():
            chunks.append(Chunk(int(match[1]), str(entry.resolve())))
    chunks.sort(key=lambda c: c.sequence)
    if len({c.sequence for c in chunks}) != len(chunks):
        raise BridgeError("invalid_feed", "Producer chunk sequence numbers must be unique.")
    return chunks, (folder / "END").is_file(), None


def parse_hls(text: str, base: str) -> tuple[list[Chunk], bool, int | None]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U" or len(lines) > 10000:
        raise BridgeError("invalid_feed", "The feed did not return a supported HLS playlist.")
    sequence = 0
    duration = timestamp = target = None
    gap = discontinuity = ended = False
    chunks = []
    try:
        for line in lines[1:]:
            key, _, value = line.partition(":")
            if key in {"#EXT-X-STREAM-INF", "#EXT-X-I-FRAME-STREAM-INF", "#EXT-X-MAP",
                       "#EXT-X-BYTERANGE", "#EXT-X-SKIP", "#EXT-X-PART"}:
                raise BridgeError("unsupported_feed", "Use a single-rendition MPEG-TS media playlist with whole segments.")
            if key in {"#EXT-X-KEY", "#EXT-X-SESSION-KEY"} and value != "METHOD=NONE":
                raise BridgeError("unsupported_feed", "Encrypted HLS is outside this acquisition mode.")
            if key == "#EXT-X-MEDIA-SEQUENCE":
                sequence = int(value)
                if not 0 <= sequence < 10**14 or chunks:
                    raise ValueError()
            elif key == "#EXT-X-TARGETDURATION":
                target = int(value)
                if not 1 <= target <= 120:
                    raise ValueError()
            elif key == "#EXTINF":
                value = value.split(",")[0]
                if len(value) > 24:
                    raise ValueError()
                duration = int(Decimal(value) * 1000)
                if not 1 <= duration <= 120_000:
                    raise ValueError()
            elif key == "#EXT-X-PROGRAM-DATE-TIME":
                _utc(value)
                timestamp = value
            elif key == "#EXT-X-DISCONTINUITY":
                discontinuity = True
            elif key == "#EXT-X-GAP":
                gap = True
            elif key == "#EXT-X-ENDLIST":
                ended = True
            elif not line.startswith("#"):
                if duration is None:
                    raise ValueError()
                segment = urljoin(base, line)
                media_target(segment)
                chunks.append(Chunk(sequence, segment, duration, timestamp, discontinuity, gap))
                sequence += 1
                duration = timestamp = None
                gap = discontinuity = False
        if duration is not None:
            raise ValueError()
    except (ValueError, InvalidOperation, OverflowError):
        raise BridgeError("invalid_feed", "The HLS media playlist contains invalid timing or sequence information.") from None
    return chunks, ended, target


def remote_playlist(url, *, opener=open_media):
    with opener(url, timeout=10) as (response, base):
        started = monotonic()
        buff = bytearray()
        while len(buff) <= 1_048_576:
            part = getattr(response, "read1", response.read)(min(32_768, 1_048_577 - len(buff)))
            if not part:
                break
            buff.extend(part)
            if monotonic() - started > 15:
                raise BridgeError("network_error", "The live playlist read timed out.")
        if len(buff) > 1_048_576:
            raise BridgeError("invalid_feed", "The live playlist exceeded its size limit.")
    try:
        return parse_hls(buff.decode("utf-8-sig"), base)
    except UnicodeError:
        raise BridgeError("invalid_feed", "The live playlist was not UTF-8.") from None


class BroadcastClock:
    """Track declared segment times and mark gaps before crossing an unknown interval."""
    def __init__(self, options: LiveOptions):
        self.broadcast_start = _utc(options.broadcast_start) if options.broadcast_start else None
        self.end_ms = options.start_ms or 0
        self.source = "broadcast_relative" if options.start_ms is not None else "capture_relative"
        self.next_sequence = None
        self.epoch = 0

    def position(self, chunk, length):
        timed = round((_utc(chunk.program_time) - self.broadcast_start).total_seconds() * 1000) \
            if self.broadcast_start and chunk.program_time else None
        discontinuity = chunk.discontinuity or chunk.gap
        first = self.next_sequence is None
        missing = not first and chunk.sequence != self.next_sequence
        if timed is not None:
            if timed < 0 or (not first and timed < self.end_ms - 500):
                raise BridgeError("timeline_reset", "The feed clock moved backwards. Start a new capture or select the correct broadcast epoch.")
            self.source = "broadcast_relative"
            moved = not first and timed > self.end_ms + 500
            discontinuity = discontinuity or moved
            if first or moved or missing:
                self.end_ms = max(self.end_ms if not first else 0, timed)
        if missing and timed is None:
            self.source = "estimated_after_gap"
        if discontinuity or missing:
            self.epoch += 1
        start = self.end_ms
        self.end_ms += length
        self.next_sequence = chunk.sequence + 1
        return {"start_ms": start, "end_ms": self.end_ms, "epoch": self.epoch, "clock_source": self.source,
                "discontinuity": discontinuity or missing, "gap_duration_unknown": missing and timed is None}


class RollingBuffer:
    def __init__(self, folder, source, options, rights_ref):
        self.folder, self.source, self.options, self.rights_ref = folder, source, options, rights_ref
        self.clock = BroadcastClock(options)
        self.rows, self.ready = [], []
        self.pending = []
        self.unresolved = []
        self.session_segments = []
        self.errors = []
        self.count = self.bytes_retained = self.discontinuities = 0
        self.last_analysis = None
        self.asr = LocalTranscriber(options.asr_model, options.asr_device, quiet_gain=True) if options.asr_model else None
        self.chunk_dir = folder / "chunks"
        self.chunk_dir.mkdir()

    def push(self, chunk: Chunk, *, remote: bool, cancel, opener):
        if self.clock.next_sequence is not None and chunk.sequence < self.clock.next_sequence:
            return
        if chunk.gap:
            self.clock.position(chunk, chunk.duration_ms)
            self.discontinuities += 1
            self.expire_pending("feed_discontinuity")
            self.trim()
            return
        path = self.chunk_dir / f"{chunk.sequence:014d}.ts"
        cap = min(256 * 1024**2, self.options.max_buffer_bytes)
        with staged_file(path) as staged:
            if remote:
                transfer(chunk.location, staged, Limits(max_bytes=cap, total_seconds=35, retries=0),
                         cancel=cancel, opener=opener)
            else:
                copy_chunk(Path(chunk.location), staged, cap, cancel)
            info = verify_local(staged, remote=True, transport_only=True)
            check_audio_track(info, self.options.audio_stream)
            video = next(s for s in info["streams"] if s["codec_type"] == "video")
            video_ms = track_duration(video)
            duration = chunk.duration_ms or video_ms or info["duration_ms"]
            if abs(info["duration_ms"] - duration) > max(750, duration // 8):
                raise BridgeError("invalid_feed", "A segment duration disagrees with its playlist timing.")
            digest = digest_file(staged)
            geometry = [(s.get("codec_name"), s.get("width"), s.get("height"), s.get("sample_rate"),
                         s.get("channels")) for s in info["streams"] if s.get("codec_type") in {"video", "audio"}]
            if self.rows and self.rows[-1]["geometry"] != geometry:
                chunk = replace(chunk, discontinuity=True)
            clock = self.clock.position(chunk, duration)
            staged.replace(path)
        size = path.stat().st_size
        row = {"sequence": chunk.sequence, **clock, "bytes": size, "file": path.name, "sha256": digest,
               "media_duration_ms": info["duration_ms"], "geometry": geometry, "segments": [],
               "timing_basis": "hls_extinf" if chunk.duration_ms else "video_track" if video_ms else "container_estimate"}
        self.rows.append(row)
        self.bytes_retained += size
        self.count += 1
        if clock["discontinuity"]:
            self.expire_pending("feed_discontinuity")
            self.discontinuities += 1
        if self.asr:
            try:
                self.transcribe_chunk(row, path)
            except (OSError, RuntimeError, ValueError):
                if self.asr.device == "cuda":
                    self.remember_error("asr_cuda_fallback", chunk.sequence)
                    self.asr = LocalTranscriber(self.options.asr_model, "cpu", quiet_gain=True)
                    try:
                        self.transcribe_chunk(row, path)
                    except (OSError, RuntimeError, ValueError):
                        self.remember_error("asr_unavailable", chunk.sequence)
                else:
                    self.remember_error("asr_unavailable", chunk.sequence)
        self.trim()

    def transcribe_chunk(self, row, path):
        previous = self.rows[-2] if len(self.rows) >= 2 else None
        begin = row["start_ms"]
        raw = b""
        if previous and previous["epoch"] == row["epoch"] and previous["end_ms"] == begin:
            length = previous["end_ms"] - previous["start_ms"]
            overlap = min(4000, length)
            raw = decode_audio_window(self.chunk_dir / previous["file"], length - overlap, length,
                                      audio_stream=self.options.audio_stream)
            begin -= overlap
        raw += decode_audio_window(path, 0, row["end_ms"] - row["start_ms"], audio_stream=self.options.audio_stream)
        fresh = self.asr.transcribe_pcm(raw, start_ms=begin, words=True)
        stable = [t for t in self.session_segments if t["start_ms"] < begin or t["epoch"] != row["epoch"]]
        for item in fresh:
            if stable and stable[-1]["epoch"] == row["epoch"] and item["start_ms"] < stable[-1]["end_ms"]:
                prior = stable[-1]
                if item["end_ms"] <= prior["end_ms"] + 300 or len(item["text"]) < len(prior["text"]):
                    continue
                stable.pop()
            stable.append({**item, "epoch": row["epoch"]})
        self.session_segments = stable
        replacement = [item for item in stable if item["epoch"] == row["epoch"] and item["end_ms"] > begin]
        with (self.folder / "session-transcript.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps({"sequence": row["sequence"], "replace_from_ms": begin,
                                 "epoch": row["epoch"], "utterances": replacement}, ensure_ascii=True) + "\n")

    def expire_pending(self, reason, candidates=None):
        expired = self.pending if candidates is None else candidates
        for item in expired:
            self.unresolved.append({**item, "status": "unresolved", "lifecycle": "UNRESOLVED", "reason": reason})
        if candidates is None:
            self.pending = []
        else:
            identifiers = {item["candidate_id"] for item in candidates}
            self.pending = [item for item in self.pending if item["candidate_id"] not in identifiers]

    def remember_error(self, code, sequence):
        self.errors = [*self.errors[-31:], {"code": code, "sequence": sequence}]

    def trim(self):
        oldest = self.clock.end_ms - self.options.retention_seconds * 1000
        if self.pending:
            oldest = min(oldest, min(item["start_ms"] for item in self.pending))
        while self.rows and (self.rows[0]["end_ms"] <= oldest or self.bytes_retained > self.options.max_buffer_bytes):
            row = self.rows.pop(0)
            affected = [item for item in self.pending if item["start_ms"] < row["end_ms"]]
            if affected:
                self.expire_pending("rolling_storage_limit", affected)
            (self.chunk_dir / row["file"]).unlink(missing_ok=True)
            self.bytes_retained -= row["bytes"]

    def analyze(self, *, ended: bool):
        o = self.options
        if not self.rows or not o.max_clips or len(self.ready) >= o.max_clips:
            return
        last = self.rows[-1]["end_ms"]
        if not ended and self.last_analysis is not None and last - self.last_analysis < max(4000, o.lookahead_seconds * 500):
            return
        window_start = last - o.context_seconds * 1000
        for item in self.pending:
            window_start = min(window_start, item["start_ms"])
        rows = [row for row in self.rows if row["epoch"] == self.clock.epoch and row["end_ms"] > window_start]
        if not rows or last - rows[0]["start_ms"] < o.min_ms:
            return
        self.last_analysis = last
        key = artifact_key([[row["sequence"], row["sha256"]] for row in rows])[:24]
        work = self.folder / "analyses" / key
        work.mkdir(parents=True, exist_ok=True)
        joined = work / "context.mp4"
        self._window(rows, work, joined)
        start = rows[0]["start_ms"]
        transcript = work / "local-transcript.json"
        duration = probe(joined)["duration_ms"]
        control = o.requirements
        if control:
            lower = max(0, (control.source_start_ms or 0) - start)
            upper = min(duration, control.source_end_ms - start) if control.source_end_ms is not None else duration
            if upper - lower < control.bounds[0]:
                joined.unlink(missing_ok=True)
                self._prune_inactive(work)
                return
            control = replace(control, source_start_ms=lower, source_end_ms=upper)
        timeline = []
        for item in self.session_segments:
            if item["epoch"] != self.clock.epoch or not start <= item["start_ms"] < item["end_ms"] <= start + duration:
                continue
            translated = {**item, "start_ms": item["start_ms"] - start, "end_ms": item["end_ms"] - start}
            translated["words"] = [{**word, "start_ms": word["start_ms"] - start, "end_ms": word["end_ms"] - start}
                                   for word in item.get("words", [])]
            timeline.append(translated)
        write_json(transcript, {"segments": timeline})
        ready_candidates = []
        visited = set()
        keep = False
        try:
            result = process(joined, work / "analysis", rights_ref=self.rights_ref, transcript_path=transcript,
                             render=False, limit=o.max_clips, audio_stream=self.options.audio_stream, layout=o.layout,
                             profile=o.profile, evolution=o.evolution or EvolutionConfig(profile=o.hardware_profile,
                                min_ms=o.min_ms, max_ms=o.max_ms), evolution_provider=o.provider,
                             requirements=control,
                             source_link={"schema_version": "chomp-source-link/1", "source": self.source.reference(),
                                          "method": "rolling_buffer", "timeline": self.clock.source,
                                          "window_start_ms": start})
            import json
            data = json.loads(Path(result["candidate_ledger"]).read_text(encoding="utf-8"))
            for row in data["candidates"]:
                if row["decision"] != "selected":
                    continue
                c = row["candidate"]
                left, right = start + c["start_ms"], start + c["end_ms"]
                if right > last + 250 or any(x["epoch"] == self.clock.epoch and
                        max(0, min(right, x["end_ms"]) - max(left, x["start_ms"])) >= .5 * min(right - left, x["end_ms"] - x["start_ms"])
                        for x in self.ready):
                    continue
                old = next((item for item in self.pending if item["epoch"] == self.clock.epoch and
                            min(item["end_ms"], right) - max(item["start_ms"], left) >=
                            .55 * min(right-left, item["end_ms"]-item["start_ms"])), None)
                identifier = old["candidate_id"] if old else artifact_key(
                    [self.source.source_id, self.clock.epoch, left, right, c.get("transcript_excerpt")])[:24]
                visited.add(identifier)
                candidate = {"candidate_id": identifier, "start_ms": left, "end_ms": right,
                             "epoch": self.clock.epoch, "clock_source": self.clock.source,
                             "window_start_ms": start, "candidate_ledger": result["candidate_ledger"],
                             "ranking_method": c.get("ranking_method"), "rationale": c.get("rationale"),
                             "description": c.get("transcript_excerpt")}
                if not ended and right + o.lookahead_seconds * 1000 > last:
                    ready_candidates.append({**candidate, "status": "pending_context", "lifecycle": "WAITING_FOR_CONTEXT"})
                    continue
                candidate["status"] = "review_ready"
                candidate["lifecycle"] = "VERIFIED" if c.get("ranking_method") == "evolution_director_verified" else "READY"
                if o.render:
                    rendered = render_manual_preview(joined, self.folder / "review", start_s=c["start_ms"] / 1000,
                        end_s=c["end_ms"] / 1000, rights_ref=self.rights_ref, audio_stream=self.options.audio_stream,
                        layout=o.layout, profile=o.profile, transcript_path=transcript,
                        facecam_roi=control.facecam_roi if control else None, gameplay_roi=control.gameplay_roi if control else None,
                        audio_profile=control.audio_profile if control else "original",
                        caption_position=control.caption_position if control else "lower",
                        source_link=result.get("source_link"), requirements=control)
                    keep = True
                    candidate["review_page"] = rendered["review_page"]
                    candidate["preview"] = rendered["preview"]
                    candidate["lifecycle"] = "RENDERED"
                self.ready.append(candidate)
                if len(self.ready) >= o.max_clips:
                    break
            for item in self.pending:
                if item["candidate_id"] in visited:
                    continue
                if ended or item["end_ms"] + o.lookahead_seconds * 1000 <= last:
                    self.unresolved.append({**item, "status": "unresolved", "lifecycle": "UNRESOLVED",
                                            "reason": "not_selected_after_context"})
                else:
                    ready_candidates.append(item)
            self.pending = ready_candidates
        finally:
            if not keep:
                joined.unlink(missing_ok=True)
            self._prune_inactive(work)

    def _prune_inactive(self, current):
        # Window-specific evidence can acquire many small jobs. Maintain only the latest window
        # and windows referenced by finalized candidates.
        from shutil import rmtree
        root = self.folder / "analyses"
        if root.resolve() != root:
            raise BridgeError("invalid_job", "Live analysis storage escapes its job directory.")
        active = {Path(item["candidate_ledger"]).parents[2] for item in [*self.ready, *self.pending]}
        for entry in root.iterdir():
            if entry != current and entry not in active and entry.is_dir() and entry.resolve() == entry \
                    and re.fullmatch(r"[0-9a-f]{24}", entry.name):
                # This is a generated per-job directory. Its resolved parent is the checked root.
                rmtree(entry)

    def _window(self, rows, work, joined):
        playlist = self.chunk_dir / "window.ffconcat"
        text = "ffconcat version 1.0\n" + "".join(
            f"file '{row['file']}'\nduration {(row['end_ms'] - row['start_ms']) / 1000:.3f}\n" for row in rows)
        playlist.write_text(text, encoding="ascii")
        if disk_usage(work).free < 256 * 1024**2 + sum(row["bytes"] for row in rows):
            raise BridgeError("storage_limit", "There is insufficient free space to assemble the live review window.")
        exec_checked(["ffmpeg", "-v", "error", "-nostdin", "-xerror", "-y", "-f", "concat", "-safe", "1",
                      "-protocol_whitelist", "file", "-i", str(playlist), "-map", "0:v:0", "-map", "0:a",
                      "-c", "copy", "-movflags", "+faststart", str(joined)], timeout=60)


def window_transcript(rows, start, duration):
    from dataclasses import asdict
    result = []
    for row in rows:
        offset = row["start_ms"] - start
        end = min(duration, row["end_ms"] - start)
        for utterance in row["segments"]:
            value = asdict(utterance)
            s, e = utterance.start_ms + offset, utterance.end_ms + offset
            if not 0 <= s < e <= end:
                continue
            value.update(start_ms=s, end_ms=e, words=[
                {**asdict(w), "start_ms": w.start_ms + offset, "end_ms": w.end_ms + offset} for w in utterance.words])
            result.append(value)
    return result


def track_duration(stream):
    try:
        result = round(float(stream["duration"]) * 1000)
        return result if 0 < result <= 120_000 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def copy_chunk(source, dest, max_size, cancel):
    size = source.stat().st_size
    if not 0 < size <= max_size or size + 256 * 1024**2 > disk_usage(dest.parent).free:
        raise BridgeError("storage_limit", "A live chunk exceeds the disk or size budget.")
    before = source.stat()
    with source.open("rb") as src, dest.open("wb") as out:
        copied = 0
        while block := src.read(256 * 1024):
            cancel()
            copied += len(block)
            if copied > max_size:
                raise BridgeError("storage_limit", "A live chunk exceeds the configured size budget.")
            out.write(block)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise BridgeError("incomplete_transfer", "A producer chunk changed during ingestion. Publish chunks atomically.")


def run_live(value: str, root: Path, *, rights_ref: str, confirmed: bool, feed_url: str | None = None,
             spool: Path | None = None, options=LiveOptions(), job_id=None, progress=lambda state: None,
             youtube=None, twitch=None, opener=open_media) -> dict:
    assert_permission(rights_ref, confirmed)
    source = resolve(value, kind="creator_live_feed" if "://" in value and not (feed_url or spool) else "source")
    if not feed_url and not spool and source.platform == "creator":
        feed_url = value
    if bool(feed_url) == bool(spool):
        raise BridgeError("media_unavailable", "Attach exactly one creator HTTPS HLS feed or local producer spool.")
    if feed_url:
        media_target(feed_url)
    jobs = Jobs(root)
    state = jobs.create("live", source.reference(), job_id)
    folder = jobs.directory(state["job_id"])

    def update(**parts):
        state.update(parts)
        jobs.write(state)
        progress(dict(state))

    def check():
        jobs.check_cancel(state["job_id"])

    def snapshot(buffer):
        return {"clock_ms": buffer.clock.end_ms, "clock_source": buffer.clock.source,
                "ingested_chunks": buffer.count, "retained_bytes": buffer.bytes_retained,
                "discontinuities": buffer.discontinuities, "errors": buffer.errors,
                "chunks": [{k: v for k, v in row.items() if k not in {"segments", "geometry", "transcript_cache"}}
                           for row in buffer.rows], "pending": buffer.pending, "ready": buffer.ready,
                "unresolved": buffer.unresolved, "session_utterances": len(buffer.session_segments)}

    try:
        source, metadata_error = inspect(source, youtube=youtube, twitch=twitch)
        source = replace(source, authorization_status="operator_confirmed",
                         source_type="live_feed" if source.platform == "local" else source.source_type)
        opts = replace(options, broadcast_start=options.broadcast_start or source.broadcast_start)
        buffer = RollingBuffer(folder, source, opts, rights_ref)
        update(status="running", source=source.reference(), metadata_error=metadata_error,
               metadata_lookup_status=source.metadata_status,
               feed_type="https_hls" if feed_url else "local_spool")
        started = last_seen = monotonic()
        reconnects, stale_playlists = 0, 0
        while True:
            check()
            if monotonic() - started >= opts.max_seconds or monotonic() - last_seen >= opts.idle_seconds:
                update(status="stopped", stop_reason="time_limit" if monotonic() - started >= opts.max_seconds else "idle_timeout",
                       reconnects=reconnects, **snapshot(buffer))
                return state
            try:
                chunks, ended, target = remote_playlist(feed_url, opener=opener) if feed_url else local_playlist(spool)
                if chunks and buffer.clock.next_sequence is not None and chunks[-1].sequence < buffer.clock.next_sequence - 1:
                    stale_playlists += 1
                    if stale_playlists >= 3:
                        raise BridgeError("timeline_reset", "The feed sequence appears to have restarted. Start a new capture.")
                else:
                    stale_playlists = 0
                for chunk in chunks:
                    check()
                    if monotonic() - started >= opts.max_seconds:
                        break
                    if buffer.clock.next_sequence is None or chunk.sequence >= buffer.clock.next_sequence:
                        buffer.push(chunk, remote=bool(feed_url), cancel=check, opener=opener)
                        last_seen = monotonic()
                        update(status="running", reconnects=reconnects, **snapshot(buffer))
                if ended and (not chunks or (buffer.clock.next_sequence or 0) > chunks[-1].sequence):
                    buffer.analyze(ended=True)
                    update(status="ended", stop_reason="producer_ended", reconnects=reconnects, **snapshot(buffer))
                    return state
                buffer.analyze(ended=False)
                update(status="running", reconnects=reconnects, **snapshot(buffer))
                delay = max(opts.poll_seconds, min(30, target / 2 if target else 0))
            except BridgeError as exc:
                if exc.code not in {"network_error", "rate_limited", "incomplete_transfer", "media_unavailable"}:
                    raise
                reconnects += 1
                update(status="reconnecting", reconnects=reconnects, latest_error=exc.public(), **snapshot(buffer))
                delay = max(opts.poll_seconds, exc.retry_after_seconds or 0)
            until = monotonic() + delay
            while monotonic() < until and monotonic() - started < opts.max_seconds and monotonic() - last_seen < opts.idle_seconds:
                check()
                sleep(min(.25, max(0, until - monotonic())))
    except KeyboardInterrupt:
        update(status="stopped", stop_reason="interrupted")
        return state
    except (BridgeError, OSError, RuntimeError, ValueError) as exc:
        err = exc if isinstance(exc, BridgeError) else BridgeError("live_failed", "Live ingest or local analysis could not finish.")
        update(status="stopped" if err.code == "cancelled" else "failed",
               stop_reason="operator" if err.code == "cancelled" else err.code, latest_error=err.public())
        if err.code == "cancelled":
            return state
        raise err from None


def monitor_live(value: str, root: Path, *, rights_ref: str, confirmed: bool, seconds=600,
                 interval=30, job_id=None, progress=lambda state: None, youtube=None, twitch=None) -> dict:
    """Poll official platform state independently of any attached media."""
    assert_permission(rights_ref, confirmed)
    source = resolve(value)
    if not ((source.platform == "youtube") or (source.platform == "twitch" and source.source_type == "channel")):
        raise BridgeError("invalid_source", "Use a YouTube video or Twitch channel for metadata monitoring.")
    if not 1 <= seconds <= 21600 or not 15 <= interval <= 600:
        raise BridgeError("invalid_limit", "Metadata monitoring requires a bounded duration and polling interval.")
    from .platforms import Twitch, YouTube
    youtube, twitch = youtube or YouTube.from_env(), twitch or Twitch.from_env()
    jobs = Jobs(root)
    state = jobs.create("live", source.reference(), job_id)
    deadline = monotonic() + seconds
    previous = None

    def emit(**values):
        state.update(values)
        jobs.write(state)
        progress(dict(state))

    try:
        while True:
            jobs.check_cancel(state["job_id"])
            observed, error = inspect(source, youtube=youtube, twitch=twitch)
            if error:
                raise BridgeError(error["code"], error["message"], http_status=error["http_status"],
                                  retry_after_seconds=error["retry_after_seconds"])
            ended = ((previous == "live" and observed.live_status in {"offline", "completed"})
                     or (source.platform == "youtube" and observed.live_status == "completed"))
            previous = observed.live_status
            emit(status="ended" if ended else "running", feed_type="metadata_only",
                 media_status="unavailable", live_status=previous, metadata_lookup_status="available")
            if ended:
                return state
            until = min(deadline, monotonic() + interval)
            while monotonic() < until:
                jobs.check_cancel(state["job_id"])
                sleep(min(.25, max(0, until - monotonic())))
            if monotonic() >= deadline:
                emit(status="stopped", stop_reason="time_limit")
                return state
    except KeyboardInterrupt:
        emit(status="stopped", stop_reason="interrupted")
        return state
    except BridgeError as exc:
        emit(status="stopped" if exc.code == "cancelled" else "failed", latest_error=exc.public())
        if exc.code == "cancelled":
            return state
        raise

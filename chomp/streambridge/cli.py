"""Source, authorized live ingest, and explicit Twitch native operations."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from time import sleep

from ..clipcontrol import ClipControl, MAX_CLIPS
from ..artifacts import source_identity
from .acquire import Jobs, Limits, acquire, assert_permission, reopen, verify_local
from .live import LiveOptions, monitor_live, run_live
from .platforms import Twitch, YouTube, inspect
from .source import BridgeError, embed, looks_remote, resolve


def _write(data, *, error=False):
    print(json.dumps(data, ensure_ascii=True, allow_nan=False), file=sys.stderr if error else sys.stdout, flush=True)


def _progress(data):
    # Progress does not echo metadata, local paths, chat, rights text or credentials.
    _write({key: data[key] for key in ("schema_version", "job_id", "kind", "status", "bytes_received",
                                      "bytes_total", "clock_ms", "ingested_chunks", "reconnects") if key in data}, error=True)


def _storage(parser):
    parser.add_argument("--source-store", type=Path, default=Path("outputs/streambridge"),
                        help="private job and acquisition cache directory")


def _permission(parser):
    parser.add_argument("--rights-ref", required=True, help="authorization reference")
    parser.add_argument("--acknowledge-permission", action="store_true")


def _url_options(parser, option_name, help_text):
    group = parser.add_mutually_exclusive_group()
    group.add_argument(f"--{option_name}", help=help_text)
    group.add_argument(f"--{option_name}-env", metavar="VARIABLE", help="read the signed URL from an environment variable")


def _url(args, option_name):
    name = getattr(args, option_name + "_env")
    if name:
        value = os.getenv(name)
        if not value:
            raise BridgeError("missing_credentials", "The requested URL environment variable is empty.")
        return value
    return getattr(args, option_name)


def media_options(parser, *, job=False):
    _storage(parser)
    parser.add_argument("--media-file", type=Path, help="creator-provided local recording aligned to the source")
    _url_options(parser, "media-url", "creator-provided HTTPS recording")
    parser.add_argument("--twitch-download", action="store_true", help="use the authorized Helix clip download")
    parser.add_argument("--portrait", action="store_true", help="request Twitch's portrait file")
    parser.add_argument("--sha256", help="expected recording digest")
    parser.add_argument("--max-download-mib", type=int, default=8192)
    if job:
        parser.add_argument("--job-id", help="preallocated 32-character lowercase hexadecimal job ID")
    else:
        parser.add_argument("--acquired-job", help="use a previously completed acquisition job")


def prepare_input(value, args):
    attached = any((args.media_file, args.media_url, args.media_url_env, args.twitch_download, args.acquired_job,
                    args.portrait, args.sha256))
    # Preserve local command behavior and job IDs when no source acquisition was requested.
    if not attached and not looks_remote(value):
        return Path(value), None
    if args.acquired_job:
        if any((args.media_file, args.media_url, args.media_url_env, args.twitch_download, args.portrait, args.sha256)):
            raise BridgeError("invalid_acquisition", "Choose a completed acquisition job or new media.")
        src = resolve(value)
        if src.platform == "local":
            src = replace(src, source_id=source_identity(Path(value).expanduser().resolve()))
        acquired = reopen(args.source_store, args.acquired_job, src, rights_ref=args.rights_ref,
                          confirmed=args.acknowledge_permission)
    else:
        acquired = acquire(value, args.source_store, rights_ref=args.rights_ref, confirmed=args.acknowledge_permission,
                           media_file=args.media_file, media_url=_url(args, "media_url"), twitch_download=args.twitch_download,
                           portrait=args.portrait, expected_sha256=args.sha256,
                           limits=Limits(max_bytes=args.max_download_mib * 1024**2), progress=_progress)
    return acquired.path, acquired


def _inspect(args):
    source = resolve(args.input, kind="creator_live_feed" if args.live_feed else "source")
    source, error = inspect(source)
    if source.platform == "local":
        path = Path(args.input).expanduser().resolve()
        try:
            data = verify_local(path)
            source = replace(source, source_id=source_identity(path), duration_ms=data["duration_ms"],
                             metadata={"video_tracks": sum(s["codec_type"] == "video" for s in data["streams"]),
                                       "audio_tracks": sum(s["codec_type"] == "audio" for s in data["streams"])})
        except BridgeError as exc:
            error = exc.public()
            source = replace(source, metadata_status=exc.code)
    return {"source": source.public(), "embed": embed(source, parent=args.parent, origin=args.origin),
            "metadata_error": error}


def _monitor_chat(args):
    assert_permission(args.rights_ref, args.acknowledge_permission)
    source = resolve(args.input)
    if source.platform != "youtube":
        raise BridgeError("invalid_source", "Live chat monitoring currently requires a YouTube video URL.")
    if not 1 <= args.pages <= 20:
        raise BridgeError("invalid_limit", "Select between 1 and 20 live chat pages.")
    youtube = YouTube.from_env()
    video = youtube.inspect(source)
    chat = video.metadata["active_live_chat_id"]
    if args.owned or not chat:
        chat = youtube.owned_broadcast(source)["live_chat_id"]
    token, seen = None, set()
    for index in range(args.pages):
        page = youtube.chat_page(chat, page_token=token)
        token = page.pop("next_page_token")
        page["messages"] = [row for row in page["messages"] if row["id"] not in seen]
        seen.update(row["id"] for row in page["messages"])
        _write(page)
        if not token or page["offline_at"] or index == args.pages - 1:
            break
        if page["polling_interval_ms"] > 60_000:
            break
        sleep(page["polling_interval_ms"] / 1000)


def command(group, argv):
    parser = argparse.ArgumentParser(prog=f"chomp {group}")
    sub = parser.add_subparsers(dest="action", required=True)
    if group == "source":
        look = sub.add_parser("inspect", help="normalize a source and request available metadata")
        look.add_argument("input")
        look.add_argument("--parent", help="Twitch embed hostname")
        look.add_argument("--origin", help="YouTube iframe origin")
        look.add_argument("--live-feed", action="store_true", help="inspect a creator HTTPS feed URL")
        get = sub.add_parser("acquire", help="attach a recording or fetch authorized media")
        get.add_argument("input")
        _permission(get)
        media_options(get, job=True)
        get.add_argument("--download-timeout", type=int, default=1800, help="whole transfer deadline in seconds")
        get.add_argument("--retries", type=int, default=2)
        for action in ("status", "cancel"):
            item = sub.add_parser(action)
            item.add_argument("job_id")
            _storage(item)
    elif group == "live":
        start = sub.add_parser("start", help="run a local rolling ingest worker in the foreground")
        start.add_argument("input", help="platform URL, creator HTTPS HLS URL, or local spool path")
        _permission(start)
        _storage(start)
        _url_options(start, "feed-url", "attach an authorized single-rendition HTTPS HLS media playlist")
        start.add_argument("--metadata-only", action="store_true", help="poll platform status without capturing media")
        start.add_argument("--from-dir", type=Path, help="attach a producer directory of completed numbered TS files")
        start.add_argument("--job-id")
        start.add_argument("--retention-seconds", type=int, default=180)
        start.add_argument("--context-seconds", type=int, default=70)
        start.add_argument("--lookahead-seconds", type=int, default=15)
        start.add_argument("--poll-seconds", type=float, default=3)
        start.add_argument("--max-seconds", type=float, default=3600)
        start.add_argument("--idle-seconds", type=float, default=60)
        start.add_argument("--start-ms", type=int, help="known broadcast offset of the first input chunk")
        start.add_argument("--broadcast-start", help="ISO timestamp with UTC offset for HLS program times")
        start.add_argument("--buffer-mib", type=int, default=2048)
        start.add_argument("--asr-model", help="installed Whisper model for incremental transcription")
        start.add_argument("--asr-device", choices=("cpu", "cuda"), default="cpu")
        start.add_argument("--audio-stream", type=int, default=0)
        start.add_argument("--max-clips", type=int, choices=range(MAX_CLIPS + 1), default=3)
        start.add_argument("--requirements", type=Path, help="shared ClipControl JSON settings")
        start.add_argument("--min-ms", type=int, default=6000)
        start.add_argument("--max-ms", type=int, default=45000)
        start.add_argument("--hardware-profile", choices=("eco", "balanced", "gpu"), default="eco")
        start.add_argument("--layout", choices=("shorts", "fit"), default="shorts")
        start.add_argument("--profile", choices=("preview", "final"), default="preview")
        start.add_argument("--render", action="store_true")
        for action in ("stop", "status"):
            item = sub.add_parser(action)
            item.add_argument("job_id")
            _storage(item)
    elif group == "youtube":
        chat = sub.add_parser("chat", help="read bounded live chat pages using a YouTube OAuth token")
        chat.add_argument("input")
        _permission(chat)
        chat.add_argument("--owned", action="store_true", help="verify the authenticated account can read the broadcast")
        chat.add_argument("--pages", type=int, default=1)
    else:
        sub.add_parser("auth-status", help="validate the configured Twitch access token")
        native = sub.add_parser("create-clip", help="explicitly create a native Twitch VOD clip")
        native.add_argument("input")
        _permission(native)
        native.add_argument("--confirm-create", action="store_true")
        native.add_argument("--end-seconds", type=int, required=True)
        native.add_argument("--duration-seconds", required=True)
        native.add_argument("--title", required=True)
        native.add_argument("--wait-seconds", type=float, default=15)
        status = sub.add_parser("clip-status")
        status.add_argument("clip_id")
        status.add_argument("--wait-seconds", type=float, default=0)

    args = parser.parse_args(argv)
    try:
        if args.action == "inspect":
            output = _inspect(args)
        elif args.action == "acquire":
            output = acquire(args.input, args.source_store, rights_ref=args.rights_ref,
                             confirmed=args.acknowledge_permission, media_file=args.media_file, media_url=_url(args, "media_url"),
                             twitch_download=args.twitch_download, portrait=args.portrait, expected_sha256=args.sha256,
                             limits=Limits(max_bytes=args.max_download_mib * 1024**2, total_seconds=args.download_timeout,
                                           retries=args.retries), job_id=args.job_id, progress=_progress).public()
        elif args.action in {"status", "stop", "cancel"}:
            jobs = Jobs(args.source_store)
            output = jobs.status(args.job_id) if args.action == "status" else jobs.cancel(args.job_id)
        elif args.action == "start":
            value = args.input
            spool = args.from_dir or (Path(value) if not looks_remote(value) else None)
            req = ClipControl.parse(json.loads(args.requirements.read_text(encoding="utf-8-sig"))) if args.requirements else None
            opts = LiveOptions(requirements=req, retention_seconds=args.retention_seconds, context_seconds=args.context_seconds,
                               lookahead_seconds=args.lookahead_seconds, poll_seconds=args.poll_seconds,
                               max_seconds=args.max_seconds, idle_seconds=args.idle_seconds, start_ms=args.start_ms,
                               broadcast_start=args.broadcast_start, max_buffer_bytes=args.buffer_mib * 1024**2,
                               max_clips=args.max_clips, min_ms=args.min_ms, max_ms=args.max_ms,
                               audio_stream=args.audio_stream, layout=args.layout, profile=args.profile,
                               hardware_profile=args.hardware_profile, asr_model=args.asr_model, asr_device=args.asr_device,
                               render=args.render)
            feed = _url(args, "feed_url")
            if args.metadata_only:
                if feed or args.from_dir or args.asr_model or args.render:
                    raise BridgeError("invalid_acquisition", "Metadata monitoring does not accept a feed or media analysis options.")
                output = monitor_live(value, args.source_store, seconds=opts.max_seconds,
                                      interval=max(15, opts.poll_seconds), job_id=args.job_id,
                                      rights_ref=args.rights_ref, confirmed=args.acknowledge_permission, progress=_progress)
            else:
                output = run_live(value, args.source_store, rights_ref=args.rights_ref, confirmed=args.acknowledge_permission,
                                  feed_url=feed, spool=spool, options=opts, job_id=args.job_id, progress=_progress)
        elif args.action == "chat":
            _monitor_chat(args)
            return 0
        else:
            twitch = Twitch.from_env()
            if args.action == "auth-status":
                output = twitch.validate()
            elif args.action == "clip-status":
                output = twitch.clip_status(args.clip_id, wait_seconds=args.wait_seconds)
            else:
                assert_permission(args.rights_ref, args.acknowledge_permission)
                output = twitch.create_vod_clip(resolve(args.input), end_seconds=args.end_seconds,
                            duration_seconds=args.duration_seconds, title=args.title, confirmed=args.confirm_create,
                            wait_seconds=args.wait_seconds)
        _write(output)
        return 0
    except BridgeError as exc:
        _write({"status": "failed", "error": exc.public()})
        return 1
    except KeyboardInterrupt:
        _write({"status": "cancelled"})
        return 130
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError):
        _write({"status": "failed", "error": {"code": "bridge_error", "message": "The source operation could not complete."}})
        return 1

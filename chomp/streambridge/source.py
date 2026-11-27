"""Canonical platform identifiers, separate from credentials and media access."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlsplit


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, http_status: int | None = None,
                 retry_after_seconds: int | None = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds

    def public(self) -> dict:
        return {"code": self.code, "message": str(self), "http_status": self.http_status,
                "retry_after_seconds": self.retry_after_seconds}


@dataclass(frozen=True)
class Source:
    platform: str
    source_id: str
    source_type: str
    canonical_url: str | None = None
    channel: dict | None = None
    title: str | None = None
    duration_ms: int | None = None
    thumbnail: str | None = None
    live_status: str = "unknown"
    broadcast_start: str | None = None
    broadcast_end: str | None = None
    metadata_status: str = "unrequested"
    metadata: dict = field(default_factory=dict)
    media_methods: tuple[str, ...] = ()
    authorization_status: str = "unverified"
    restrictions: tuple[str, ...] = ()

    def public(self) -> dict:
        return {"schema_version": "chomp-source/1", **asdict(self)}

    def reference(self) -> dict:
        """Persist the platform identity supplied by the operator, without stored API data."""
        if self.platform not in {"youtube", "twitch"} or not self.canonical_url:
            return self.public()
        parsed = resolve(self.canonical_url)
        return replace(parsed, metadata_status="resolve_on_inspect",
                       authorization_status=self.authorization_status).public()


YT_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z", re.ASCII)
TW_ID = re.compile(r"[0-9]{1,24}\Z", re.ASCII)
CLIP_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z", re.ASCII)
TW_LOGIN = re.compile(r"[a-z0-9_]{1,25}\Z", re.ASCII)
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
TW_HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"}
TW_RESERVED = {"directory", "downloads", "inventory", "subscriptions", "settings", "videos",
               "search", "friends", "turbo", "moderator", "wallet", "dashboard", "team", "clip"}
TYPES = (".mp4", ".mov", ".mkv", ".webm", ".ts", ".avi", ".m4v")


def looks_remote(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value)) and not bool(
        re.match(r"^[A-Za-z]:[\\/]", value))


def _bad_url():
    return BridgeError("invalid_source", "Use a supported Twitch or YouTube URL, or a local recording path.")


def resolve(value: str | Path, *, kind: str = "source") -> Source:
    """Parse without calling a platform or touching remote media."""
    raw = str(value)
    if not raw or len(raw) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise _bad_url()
    # Treat Windows drive letters, UNC paths and regular relative file paths as local.
    if not looks_remote(raw):
        if kind != "source":
            raise _bad_url()
        path = Path(raw).expanduser().resolve()
        return Source("local", "path:" + sha256(str(path).encode()).hexdigest(), "recording", title=path.name,
                      metadata_status="local", media_methods=("local_file",), live_status="completed")
    try:
        p = urlsplit(raw)
        host = p.hostname
        if p.scheme not in {"http", "https"} or not host or p.username is not None or p.password is not None \
                or p.port is not None or "\\" in raw or any(c.isspace() for c in raw):
            raise _bad_url()
    except ValueError:
        raise _bad_url() from None
    parts = p.path.strip("/").split("/")
    if host in YT_HOSTS:
        try:
            params = parse_qs(p.query, keep_blank_values=True, max_num_fields=100)
        except ValueError:
            raise _bad_url() from None
        video = ""
        if host in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
            video = parts[0]
        elif parts == ["watch"] and len(params.get("v", [])) == 1:
            video = params["v"][0]
        elif len(parts) == 2 and parts[0] in {"live", "shorts", "embed"}:
            video = parts[1]
        if not YT_ID.fullmatch(video):
            raise _bad_url()
        return Source("youtube", video, "video", f"https://www.youtube.com/watch?v={video}",
                      media_methods=("creator_file", "creator_https", "creator_live_feed"),
                      restrictions=("Official player playback is separate from editing media.",))
    if host in TW_HOSTS:
        if host == "clips.twitch.tv" and len(parts) == 1 and CLIP_ID.fullmatch(parts[0]) and parts[0] != "embed":
            mode, identifier = "clip", parts[0]
        elif host != "clips.twitch.tv" and len(parts) == 2 and parts[0] == "videos" and TW_ID.fullmatch(parts[1]):
            mode, identifier = "vod", parts[1]
        elif host != "clips.twitch.tv" and len(parts) == 3 and TW_LOGIN.fullmatch(parts[0].lower()) \
                and parts[1] == "clip" and CLIP_ID.fullmatch(parts[2]):
            mode, identifier = "clip", parts[2]
        elif host != "clips.twitch.tv" and len(parts) == 1 and TW_LOGIN.fullmatch(parts[0].lower()) \
                and parts[0].lower() not in TW_RESERVED:
            mode, identifier = "channel", parts[0].lower()
        else:
            raise _bad_url()
        url = {"vod": f"https://www.twitch.tv/videos/{identifier}",
               "clip": f"https://clips.twitch.tv/{identifier}",
               "channel": f"https://www.twitch.tv/{identifier}"}[mode]
        choices = ("creator_file", "creator_https", "creator_live_feed")
        return Source("twitch", identifier, mode, url,
                      channel={"login": identifier} if mode == "channel" else None,
                      live_status="completed" if mode in {"clip", "vod"} else "unknown",
                      media_methods=(*choices, "twitch_clip_download") if mode == "clip" else choices,
                      restrictions=("Helix metadata and embedded playback do not grant media access.",))
    if kind not in {"creator_https", "creator_live_feed"} and not p.path.lower().endswith(TYPES):
        raise _bad_url()
    if p.scheme != "https":
        raise BridgeError("invalid_source", "Creator media must use HTTPS.")
    mode = "live_feed" if kind == "creator_live_feed" else "recording"
    # Signed URLs never appear in descriptors, job files or errors.
    return Source("creator", "url:" + sha256(raw.encode()).hexdigest(), mode, metadata_status="unavailable",
                  media_methods=(kind if kind != "source" else "creator_https",),
                  live_status="unknown" if mode == "live_feed" else "completed")


def embed(source: Source, *, parent: str | None = None, origin: str | None = None) -> dict | None:
    if source.platform == "youtube" and source.metadata.get("embeddable") is not False:
        if origin:
            p = urlsplit(origin)
            if (p.scheme not in {"http", "https"} or not p.hostname or p.username is not None or
                    p.path not in {"", "/"} or p.query or p.fragment or "\\" in origin):
                raise BridgeError("invalid_origin", "Supply the website origin, without a path or credentials.")
        query = urlencode({"enablejsapi": "1", **({"origin": origin.rstrip("/")} if origin else {})})
        return {"type": "youtube_iframe", "url": f"https://www.youtube.com/embed/{source.source_id}?{query}",
                "script": "https://www.youtube.com/iframe_api", "video_id": source.source_id,
                "requires_website_origin": True}
    if source.platform == "twitch" and parent:
        if not re.fullmatch(r"(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+", parent):
            raise BridgeError("invalid_parent", "Supply a Twitch embed parent hostname.")
        field = {"clip": "clip", "vod": "video", "channel": "channel"}[source.source_type]
        host = "https://clips.twitch.tv/embed" if field == "clip" else "https://player.twitch.tv/"
        value = "v" + source.source_id if field == "video" else source.source_id
        return {"type": "twitch_iframe", "url": host + "?" + urlencode({field: value, "parent": parent}),
                "minimum_width": 400, "minimum_height": 300}
    return None

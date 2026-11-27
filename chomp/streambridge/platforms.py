"""Documented YouTube Data API and Twitch Helix operations."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import os
import re
from time import monotonic, sleep

from .network import api_request
from .source import BridgeError, CLIP_ID, Source, TW_ID


YT = "www.googleapis.com"
HELIX = "api.twitch.tv"
IDENTITY = "id.twitch.tv"
CLIP_SCOPE = {"editor:manage:clips", "channel:manage:clips"}
_ISO_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?\Z", re.ASCII)
_TW_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?\Z", re.ASCII)


def yt_duration(value) -> int | None:
    part = _ISO_DURATION.fullmatch(value) if isinstance(value, str) and len(value) < 64 else None
    if not part or not any(part.groups()):
        return None
    days, hours, mins, seconds = (Decimal(v or 0) for v in part.groups())
    return int((days * 86400 + hours * 3600 + mins * 60 + seconds) * 1000)


def tw_duration(value) -> int | None:
    part = _TW_DURATION.fullmatch(value) if isinstance(value, str) and len(value) < 32 else None
    if not part or not any(part.groups()):
        return None
    h, m, s = (int(v or 0) for v in part.groups())
    return (h * 3600 + m * 60 + s) * 1000


def _items(payload, key):
    rows = payload.get(key, [])
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise BridgeError("invalid_response", "The platform returned an invalid result list.")
    return rows


def _find(payload, key, identifier):
    return next((r for r in _items(payload, key) if r.get("id") == identifier), None)


def _utc(value: str) -> datetime:
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("A UTC offset is required")
    return at.astimezone(timezone.utc)


def _thumbnail(snippet: dict):
    images = snippet.get("thumbnails", {})
    for key in ("maxres", "standard", "high", "medium", "default"):
        if images.get(key, {}).get("url"):
            return images[key]["url"]
    return None


class YouTube:
    def __init__(self, *, api_key=None, token=None, request=api_request):
        self.api_key, self.token, self.request = api_key, token, request

    @classmethod
    def from_env(cls):
        return cls(api_key=os.getenv("CHOMP_YOUTUBE_API_KEY"), token=os.getenv("CHOMP_YOUTUBE_ACCESS_TOKEN"))

    def _get(self, resource, params, *, oauth=False):
        if self.token:
            headers = {"Authorization": "Bearer " + self.token}
        elif self.api_key and not oauth:
            headers = {"X-Goog-Api-Key": self.api_key}
        else:
            raise BridgeError("missing_credentials", "Set CHOMP_YOUTUBE_ACCESS_TOKEN for authorized reads." if oauth
                              else "Set CHOMP_YOUTUBE_API_KEY or CHOMP_YOUTUBE_ACCESS_TOKEN for metadata.")
        return self.request(YT, "/youtube/v3/" + resource, params, headers)

    def inspect(self, source: Source) -> Source:
        result = self._get("videos", {"id": source.source_id,
                      "part": "snippet,contentDetails,liveStreamingDetails,status"})
        row = _find(result, "items", source.source_id)
        if row is None:
            raise BridgeError("media_unavailable", "YouTube has no visible video for this ID.")
        sn = row.get("snippet", {})
        content, live, status = (row.get(k, {}) for k in ("contentDetails", "liveStreamingDetails", "status"))
        start, end = live.get("actualStartTime"), live.get("actualEndTime")
        broadcast = sn.get("liveBroadcastContent")
        state = ("completed" if end else "live" if broadcast == "live" or start else
                 "upcoming" if broadcast == "upcoming" else "completed")
        kind = "live_archive" if end else "livestream" if state in {"live", "upcoming"} else "video"
        return replace(source, source_type=kind, live_status=state, broadcast_start=start,
                       broadcast_end=end, metadata_status="available", title=sn.get("title"),
                       duration_ms=yt_duration(content.get("duration")) if state == "completed" else None,
                       channel={"id": sn.get("channelId"), "title": sn.get("channelTitle")},
                       thumbnail=_thumbnail(sn), authorization_status="oauth_metadata" if self.token else "public_metadata",
                       metadata={"published_at": sn.get("publishedAt"), "scheduled_start": live.get("scheduledStartTime"),
                                 "active_live_chat_id": live.get("activeLiveChatId"), "embeddable": status.get("embeddable"),
                                 "privacy_status": status.get("privacyStatus"), "region_restriction": content.get("regionRestriction")})

    def owned_broadcast(self, source: Source) -> dict:
        result = self._get("liveBroadcasts", {"id": source.source_id, "part": "snippet,status"}, oauth=True)
        row = _find(result, "items", source.source_id)
        if row is None:
            raise BridgeError("unauthorized", "The authorized account could not read this broadcast.")
        sn = row.get("snippet", {})
        return {"video_id": source.source_id, "live_chat_id": sn.get("liveChatId"),
                "lifecycle_status": row.get("status", {}).get("lifeCycleStatus")}

    def chat_page(self, chat_id: str, *, page_token=None) -> dict:
        if not chat_id or len(chat_id) > 256:
            raise BridgeError("media_unavailable", "There is no active live chat for this broadcast.")
        params = {"liveChatId": chat_id, "part": "id,snippet", "maxResults": 200}
        if page_token:
            params["pageToken"] = page_token
        data = self._get("liveChat/messages", params, oauth=True)
        wait = data.get("pollingIntervalMillis")
        if type(wait) is not int or not 0 < wait <= 3_600_000:
            raise BridgeError("invalid_response", "YouTube did not supply a valid chat polling interval.")
        messages = []
        for row in _items(data, "items"):
            sn = row.get("snippet", {})
            if row.get("id") and sn.get("displayMessage"):
                messages.append({"id": row["id"], "type": sn.get("type"), "published_at": sn.get("publishedAt"),
                                 "text": sn["displayMessage"]})
        return {"schema_version": "chomp-live-chat/1", "messages": messages,
                "next_page_token": data.get("nextPageToken"), "polling_interval_ms": wait,
                "offline_at": data.get("offlineAt")}


class Twitch:
    def __init__(self, *, client_id=None, token=None, client_secret=None, request=api_request):
        self.client_id, self.token, self.client_secret = client_id, token, client_secret
        self.request = request
        self.valid_until = 0.0
        self.account = {}

    @classmethod
    def from_env(cls):
        return cls(client_id=os.getenv("CHOMP_TWITCH_CLIENT_ID"), token=os.getenv("CHOMP_TWITCH_ACCESS_TOKEN"),
                   client_secret=os.getenv("CHOMP_TWITCH_CLIENT_SECRET"))

    def validate(self) -> dict:
        if monotonic() < self.valid_until:
            return self.account
        if not self.client_id or not (self.token or self.client_secret):
            raise BridgeError("missing_credentials", "Set CHOMP_TWITCH_CLIENT_ID and an access token or app client secret.")
        if not self.token:
            data = self.request(IDENTITY, "/oauth2/token", {}, {}, method="POST",
                                form={"client_id": self.client_id, "client_secret": self.client_secret,
                                      "grant_type": "client_credentials"})
            self.token = data.get("access_token")
            if not isinstance(self.token, str) or not self.token:
                raise BridgeError("invalid_credentials", "Twitch did not issue an app access token.")
        self.valid_until = 0
        result = self.request(IDENTITY, "/oauth2/validate", {}, {"Authorization": "OAuth " + self.token})
        expires = result.get("expires_in")
        scopes = result.get("scopes") or []
        if result.get("client_id") != self.client_id or type(expires) is not int or expires <= 0:
            raise BridgeError("invalid_credentials", "The Twitch token is expired or belongs to another application.")
        if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
            raise BridgeError("invalid_response", "Twitch returned invalid OAuth scopes.")
        self.valid_until = monotonic() + min(3600, expires)
        self.account = {"client_id": self.client_id, "user_id": result.get("user_id"), "scopes": scopes,
                        "token_type": "user" if result.get("user_id") else "app"}
        return self.account

    def _helix(self, path, params, *, method="GET", accepted=(200,)):
        self.validate()
        try:
            return self.request(HELIX, "/helix/" + path, params,
                                {"Client-Id": self.client_id, "Authorization": "Bearer " + self.token},
                                method=method, accepted=accepted)
        except BridgeError as exc:
            if exc.http_status == 401:
                self.valid_until = 0
            raise

    def _one(self, path, params, identifier=None):
        data = self._helix(path, params)
        return (_find(data, "data", identifier) if identifier else next(iter(_items(data, "data")), None))

    def inspect(self, source: Source) -> Source:
        if source.source_type == "channel":
            user = self._one("users", {"login": source.source_id})
            if not user or user.get("login", "").lower() != source.source_id:
                raise BridgeError("media_unavailable", "Twitch has no visible channel for this login.")
            stream = self._one("streams", {"user_id": user["id"]})
            stream = stream if stream and stream.get("user_id") == user["id"] else None
            channels = self._one("channels", {"broadcaster_id": user["id"]}) or {}
            chan = {"id": user["id"], "login": user["login"], "title": user.get("display_name")}
            return replace(source, channel=chan, title=(stream or channels).get("title"),
                           live_status="live" if stream else "offline", broadcast_start=(stream or {}).get("started_at"),
                           thumbnail=(stream or {}).get("thumbnail_url") or user.get("profile_image_url"),
                           metadata_status="available", authorization_status="metadata_token",
                           metadata={"stream_id": (stream or {}).get("id"), "game": (stream or channels).get("game_name")})
        is_clip = source.source_type == "clip"
        row = self._one("clips" if is_clip else "videos", {"id": source.source_id}, source.source_id)
        if not row:
            raise BridgeError("media_unavailable", "Twitch has no visible result for this ID.")
        if is_clip:
            duration = row.get("duration")
            duration = round(duration * 1000) if type(duration) in {int, float} and 0 < duration <= 120 else None
            channel = {"id": row.get("broadcaster_id"), "title": row.get("broadcaster_name")}
            return replace(source, title=row.get("title"), duration_ms=duration, channel=channel,
                           thumbnail=row.get("thumbnail_url"), metadata_status="available", authorization_status="metadata_token",
                           metadata={"created_at": row.get("created_at"), "vod_id": row.get("video_id"),
                                     "vod_clip_start_seconds": row.get("vod_offset"),
                                     "portrait": row.get("is_portrait")})
        channel = {"id": row.get("user_id"), "login": row.get("user_login"), "title": row.get("user_name")}
        return replace(source, title=row.get("title"), duration_ms=tw_duration(row.get("duration")), channel=channel,
                       thumbnail=row.get("thumbnail_url") or None, metadata_status="available",
                       authorization_status="metadata_token",
                       metadata={"created_at": row.get("created_at"), "published_at": row.get("published_at"),
                                 "stream_id": row.get("stream_id"), "type": row.get("type")})

    def _editor(self, broadcaster: str) -> str:
        account = self.validate()
        editor = account.get("user_id")
        scopes = set(account["scopes"])
        if not isinstance(editor, str) or not TW_ID.fullmatch(editor) or not TW_ID.fullmatch(broadcaster or "") or (
                not (scopes & CLIP_SCOPE) if editor == broadcaster else "editor:manage:clips" not in scopes):
            raise BridgeError("unauthorized", "Use a scoped Twitch user token for the broadcaster or an authorized channel editor.")
        # Twitch verifies that a scoped editor is still authorized for the specific channel.
        return editor

    def download(self, source: Source, *, portrait: bool = False) -> str:
        if source.platform != "twitch" or source.source_type != "clip":
            raise BridgeError("invalid_source", "Native Twitch downloads require a clip URL.")
        source = self.inspect(source)
        broadcaster = source.channel["id"]
        editor = self._editor(broadcaster)
        params = {"broadcaster_id": broadcaster, "editor_id": editor, "clip_id": source.source_id}
        row = next((r for r in _items(self._helix("clips/downloads", params), "data")
                    if r.get("clip_id") == source.source_id), {})
        url = row.get(("portrait" if portrait else "landscape") + "_download_url")
        if not isinstance(url, str) or not url:
            raise BridgeError("media_unavailable", "Twitch did not provide the requested clip version.")
        return url

    def create_vod_clip(self, source: Source, *, end_seconds: int, duration_seconds: str, title: str,
                        confirmed: bool, wait_seconds: float = 15) -> dict:
        if not confirmed:
            raise BridgeError("confirmation_required", "Explicitly confirm creating a clip on Twitch.")
        if source.platform != "twitch" or source.source_type != "vod":
            raise BridgeError("invalid_source", "Native creation requires a Twitch VOD URL.")
        start, end, duration = vod_interval(end_seconds, duration_seconds)
        if not title.strip() or len(title) > 100 or any(ord(c) < 32 for c in title):
            raise BridgeError("invalid_title", "Supply a nonempty Twitch clip title of at most 100 characters.")
        if not 0 <= wait_seconds <= 60:
            raise BridgeError("invalid_limit", "Twitch readiness wait must be between 0 and 60 seconds.")
        source = self.inspect(source)
        if source.duration_ms is not None and end > source.duration_ms:
            raise BridgeError("invalid_interval", "The clip end exceeds the currently reported VOD duration.")
        broadcaster = source.channel["id"]
        editor = self._editor(broadcaster)
        try:
            data = self._helix("videos/clips", {"editor_id": editor, "broadcaster_id": broadcaster,
                                "vod_id": source.source_id, "vod_offset": end_seconds, "duration": duration,
                                "title": title.strip()}, method="POST", accepted=(202,))
        except BridgeError as exc:
            if exc.code == "network_error":
                raise BridgeError("creation_unknown", "Twitch may have accepted this request. Check Twitch before submitting again.") from None
            raise
        rows = _items(data, "data")
        clip = rows[0].get("id") if rows else None
        if not isinstance(clip, str) or not CLIP_ID.fullmatch(clip):
            raise BridgeError("creation_unknown", "Twitch accepted the request without a usable clip ID. Check Twitch before repeating it.")
        state = {"status": "pending", "clip_id": clip, "url": f"https://clips.twitch.tv/{clip}"}
        if wait_seconds:
            try:
                state = self.clip_status(clip, wait_seconds=wait_seconds)
            except BridgeError as exc:
                state["readiness_error"] = exc.public()
        return {**state, "requested_start_ms": start, "requested_end_ms": end, "vod_id": source.source_id}

    def clip_status(self, clip: str, *, wait_seconds: float = 0) -> dict:
        if not CLIP_ID.fullmatch(clip) or not 0 <= wait_seconds <= 60:
            raise BridgeError("invalid_source", "Supply a valid Twitch clip ID and a wait between 0 and 60 seconds.")
        deadline = monotonic() + wait_seconds
        while True:
            row = self._one("clips", {"id": clip}, clip)
            if row:
                return {"status": "ready", "clip_id": clip, "url": f"https://clips.twitch.tv/{clip}",
                        "observed_start_seconds": row.get("vod_offset"), "observed_duration_seconds": row.get("duration")}
            remaining = deadline - monotonic()
            if remaining <= 0:
                return {"status": "pending_or_unavailable", "clip_id": clip, "url": f"https://clips.twitch.tv/{clip}"}
            sleep(min(2, remaining))


def vod_interval(end_seconds: int, duration_seconds: str) -> tuple[int, int, str]:
    if type(end_seconds) is not int or not 0 <= end_seconds <= 100_000_000:
        raise BridgeError("invalid_interval", "Twitch requires an integer end offset in seconds.")
    try:
        duration = Decimal(str(duration_seconds))
        if not duration.is_finite() or not 5 <= duration <= 60 or duration != duration.quantize(Decimal(".1")) \
                or duration > end_seconds:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise BridgeError("invalid_interval", "Twitch requires a duration of 5–60 seconds in tenths, ending at or after its duration.") from None
    return int((end_seconds - duration) * 1000), end_seconds * 1000, f"{duration:.1f}"


def inspect(source: Source, *, youtube=None, twitch=None) -> tuple[Source, dict | None]:
    if source.platform not in {"youtube", "twitch"}:
        return source, None
    service = (youtube or YouTube.from_env()) if source.platform == "youtube" else (twitch or Twitch.from_env())
    try:
        return service.inspect(source), None
    except BridgeError as exc:
        return replace(source, metadata_status=exc.code), exc.public()
    except (ValueError, TypeError, KeyError, AttributeError):
        exc = BridgeError("invalid_response", "The platform returned unsupported metadata.")
        return replace(source, metadata_status=exc.code), exc.public()

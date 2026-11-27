from contextlib import contextmanager
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import socket

import pytest

from chomp.media import exec_checked
from chomp.streambridge.acquire import Jobs, Limits, acquire, reopen, transfer
from chomp.streambridge.live import BroadcastClock, LiveOptions, parse_hls, run_live
from chomp.streambridge.network import http_error, media_target, public_addresses
from chomp.streambridge.platforms import Twitch, YouTube, inspect, vod_interval
from chomp.streambridge.source import BridgeError, embed, resolve

YT = "https://www.youtube.com/watch?v=M7lc1UVf-VE"
TW = "https://www.twitch.tv/videos/123456"
CLIP = "https://clips.twitch.tv/Sample-Clip"
MEDIA = "https://creator.example/original.mp4"
PERMISSION = {"rights_ref": "generated test fixture", "confirmed": True}


@pytest.mark.parametrize("url", [
    YT + "&t=12s", "https://youtu.be/M7lc1UVf-VE?si=x", "https://youtube.com/live/M7lc1UVf-VE",
    "http://m.youtube.com/watch?v=M7lc1UVf-VE",
])
def test_youtube_forms(url):
    source = resolve(url)
    assert (source.platform, source.canonical_url) == ("youtube", YT)
    assert "enablejsapi=1" in embed(source, origin="https://website.example")["url"]


@pytest.mark.parametrize(("url", "kind", "identifier"), [
    (TW, "vod", "123456"), ("https://twitch.tv/Creator_01", "channel", "creator_01"),
    (CLIP, "clip", "Sample-Clip"), ("https://www.twitch.tv/creator/clip/Sample-Clip", "clip", "Sample-Clip"),
])
def test_twitch_forms(url, kind, identifier):
    source = resolve(url)
    assert (source.platform, source.source_type, source.source_id) == ("twitch", kind, identifier)
    assert "parent=localhost" in embed(source, parent="localhost")["url"]


@pytest.mark.parametrize("url", [
    "https://youtu.be/short", YT + "&v=aaaaaaaaaaa", "https://twitch.tv/videos/notnumeric",
    "https://twitch.tv/directory", "https://www.twitch.tv:99/videos/123",
    "https://user:secret@www.youtube.com/watch?v=M7lc1UVf-VE",
    "https://www.youtube.com.other.example/watch?v=M7lc1UVf-VE",
    "ftp://example.org/video.mp4", "https://unsupported.example/article",
])
def test_invalid_sources(url):
    with pytest.raises(BridgeError):
        resolve(url)


def test_youtube_metadata_archive_live_and_missing_credentials():
    def live(*args, **kwargs):
        assert args[:2] == ("www.googleapis.com", "/youtube/v3/videos")
        assert args[2]["id"] == "M7lc1UVf-VE"
        assert args[3] == {"X-Goog-Api-Key": "test-key"}
        return {"items": [{"id": "M7lc1UVf-VE", "snippet": {"title": "Fixture", "liveBroadcastContent": "live"},
                           "liveStreamingDetails": {"actualStartTime": "2026-01-02T00:00:00Z", "activeLiveChatId": "chat"},
                           "contentDetails": {"duration": "PT0S"}, "status": {"embeddable": False}}]}

    source, error = inspect(resolve(YT), youtube=YouTube(request=live))
    assert source.metadata_status == error["code"] == "missing_credentials"
    source = YouTube(api_key="test-key", request=live).inspect(resolve(YT))
    assert (source.source_type, source.live_status, source.duration_ms) == ("livestream", "live", None)
    assert embed(source) is None

    def archive(*args, **kwargs):
        result = live(*args, **kwargs)
        result["items"][0]["liveStreamingDetails"]["actualEndTime"] = "2026-01-02T01:00:00Z"
        result["items"][0]["contentDetails"]["duration"] = "PT1H2M3.25S"
        return result

    source = YouTube(api_key="test-key", request=archive).inspect(resolve(YT))
    assert (source.source_type, source.live_status, source.duration_ms) == ("live_archive", "completed", 3_723_250)
    assert source.reference()["title"] is None
    missing = YouTube(api_key="test-key", request=lambda *a: {"items": []})
    with pytest.raises(BridgeError, match="no visible video"):
        missing.inspect(resolve(YT))


def test_youtube_oauth_chat_pagination_uses_official_route():
    def request(host, path, params, headers):
        assert path == "/youtube/v3/liveChat/messages"
        assert params["pageToken"] == "test-page"
        assert headers == {"Authorization": "Bearer test-token"}
        return {"nextPageToken": "new-page", "pollingIntervalMillis": 3000, "items": [
            {"id": "m1", "snippet": {"displayMessage": "hello", "type": "textMessageEvent"}}]}
    page = YouTube(token="test-token", request=request).chat_page("chat", page_token="test-page")
    assert (page["messages"][0]["text"], page["polling_interval_ms"], page["next_page_token"]) == ("hello", 3000, "new-page")
    with pytest.raises(BridgeError) as err:
        YouTube(api_key="test-key").chat_page("chat")
    assert err.value.code == "missing_credentials"


def twitch_fake(*, expired=False, scopes=("channel:manage:clips",), editor="11"):
    calls = []

    def request(host, path, params, headers, **options):
        calls.append((path, params, options))
        if path == "/oauth2/validate":
            if expired:
                raise http_error(401, {}, "Twitch")
            return {"client_id": "client", "user_id": editor, "scopes": list(scopes), "expires_in": 600}
        if path == "/helix/users":
            return {"data": [{"id": "11", "login": "creator", "display_name": "Creator"}]}
        if path == "/helix/channels":
            return {"data": [{"broadcaster_id": "11", "title": "Creator channel"}]}
        if path == "/helix/streams":
            return {"data": [{"id": "stream", "user_id": "11", "title": "Broadcast", "started_at": "2026-01-02T00:00:00Z"}]}
        if path == "/helix/videos":
            return {"data": [{"id": "123456", "duration": "1h2m3s", "user_id": "11", "user_name": "Creator"}]}
        if path == "/helix/videos/clips":
            assert options["method"] == "POST" and options["accepted"] == (202,)
            assert params == {"editor_id": editor, "broadcaster_id": "11", "vod_id": "123456",
                              "vod_offset": 30, "duration": "12.5", "title": "Explicit clip"}
            return {"data": [{"id": "Sample-Clip", "edit_url": "private-link"}]}
        if path == "/helix/clips":
            return {"data": [{"id": "Sample-Clip", "broadcaster_id": "11", "duration": 12.5, "vod_offset": 17}]}
        if path == "/helix/clips/downloads":
            return {"data": [{"clip_id": "Sample-Clip", "landscape_download_url": "https://clips-media-assets2.twitch.tv/x.mp4?token=dummy"}]}
        pytest.fail("Unexpected API call")

    return Twitch(client_id="client", token="test-token", request=request), calls


def test_twitch_validation_streams_create_and_download():
    twitch, calls = twitch_fake()
    channel = twitch.inspect(resolve("https://www.twitch.tv/creator"))
    assert (channel.title, channel.live_status, channel.channel["id"]) == ("Broadcast", "live", "11")
    params = dict(end_seconds=30, duration_seconds="12.5", title="Explicit clip", wait_seconds=.1)
    with pytest.raises(BridgeError) as err:
        twitch.create_vod_clip(resolve(TW), **params, confirmed=False)
    assert err.value.code == "confirmation_required"
    assert not any(x[0] == "/helix/videos/clips" for x in calls)
    result = twitch.create_vod_clip(resolve(TW), **params, confirmed=True)
    assert result["status"] == "ready"
    assert (result["requested_start_ms"], result["requested_end_ms"]) == (17500, 30000)
    assert result["observed_start_seconds"] == 17
    assert "private-link" not in json.dumps(result)
    assert twitch.download(resolve(CLIP)).endswith("token=dummy")
    assert [c[0] for c in calls].count("/oauth2/validate") == 1


def test_twitch_expiry_and_editor_permissions():
    twitch, _ = twitch_fake(expired=True)
    with pytest.raises(BridgeError) as err:
        twitch.validate()
    assert err.value.code == "invalid_credentials"
    twitch, calls = twitch_fake(editor="22")
    with pytest.raises(BridgeError) as err:
        twitch.create_vod_clip(resolve(TW), end_seconds=30, duration_seconds="12.5",
                               title="Explicit clip", wait_seconds=0, confirmed=True)
    assert err.value.code == "unauthorized"
    assert not any(x[0] == "/helix/videos/clips" for x in calls)
    twitch, _ = twitch_fake(scopes=("editor:manage:clips",), editor="22")
    assert twitch.create_vod_clip(resolve(TW), end_seconds=30, duration_seconds="12.5",
                                  title="Explicit clip", wait_seconds=0, confirmed=True)["status"] == "pending"


@pytest.mark.parametrize(("end", "duration"), [(3, "5"), (50.1, "5"), (60, "60.1"), (20, "10.03"), (60, "nan"), (10, "4.9")])
def test_native_interval_rejects_invalid_precision_or_bounds(end, duration):
    with pytest.raises(BridgeError):
        vod_interval(end, duration)


def test_rate_limited_and_unsupported_media_stay_distinct(tmp_path):
    err = http_error(403, {"retry-after": "12"}, "YouTube", "quotaExceeded")
    assert (err.code, err.retry_after_seconds) == ("rate_limited", 12)
    with pytest.raises(BridgeError) as error:
        acquire(YT, tmp_path, **PERMISSION)
    assert error.value.code == "media_unavailable"
    with pytest.raises(BridgeError) as error:
        acquire(YT, tmp_path, rights_ref="recording", confirmed=False, media_url=MEDIA)
    assert error.value.code == "permission_required"


@pytest.mark.parametrize("url", ["http://creator.example/test.mp4", "https://user:pass@creator.example/x",
                                 "https://creator.example:8443/test.mp4", "https://a.googlevideo.com/x"])
def test_media_url_rules(url):
    with pytest.raises(BridgeError):
        media_target(url)


def test_dns_rejects_mixed_public_and_local_results(monkeypatch):
    def addresses(*a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))
                for ip in ("93.184.216.34", "127.0.0.1")]
    monkeypatch.setattr(socket, "getaddrinfo", addresses)
    with pytest.raises(BridgeError) as err:
        public_addresses("creator.example")
    assert err.value.code == "unsafe_media_url"


class Body(io.BytesIO):
    def __init__(self, body, total=None, mime="video/mp4"):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body) if total is None else total), "Content-Type": mime}

    def getheader(self, header, default=None):
        return self.headers.get(header, default)


def test_interrupted_acquisition_and_cancellation(tmp_path):
    job = "a" * 32

    @contextmanager
    def interrupted(url, **kw):
        yield Body(b"incomplete", total=100), url

    with pytest.raises(BridgeError) as err:
        acquire(MEDIA + "?signed=sample-secret", tmp_path, **PERMISSION, job_id=job, opener=interrupted,
                limits=Limits(retries=0))
    assert err.value.code == "incomplete_transfer"
    assert Jobs(tmp_path).status(job)["status"] == "failed"
    assert not list((tmp_path / "transfers").iterdir())
    assert "sample-secret" not in json.dumps(Jobs(tmp_path).status(job))

    cancel_job = "b" * 32
    def cancel(state):
        Jobs(tmp_path).cancel(cancel_job)

    with pytest.raises(BridgeError) as err:
        acquire(MEDIA, tmp_path, **PERMISSION, job_id=cancel_job, opener=interrupted, progress=cancel)
    assert err.value.code == "cancelled"
    assert Jobs(tmp_path).status(cancel_job)["status"] == "cancelled"


@pytest.fixture
def recording(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg not installed")
    path = tmp_path / "source.mp4"
    exec_checked(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=160x90:r=8:d=12",
                  "-f", "lavfi", "-i", "sine=duration=12", "-shortest", "-c:v", "libx264", "-g", "16",
                  "-sc_threshold", "0", "-c:a", "aac", str(path)])
    return path


def test_authorized_source_reuses_verified_blob_and_hides_api_data(recording, tmp_path):
    body = recording.read_bytes()
    expected = sha256(body).hexdigest()
    pulls = []

    @contextmanager
    def open_fixture(url, **kw):
        pulls.append(url)
        yield Body(body), url

    service = YouTube(api_key="test-key", request=lambda *a: {"items": [
        {"id": "M7lc1UVf-VE", "snippet": {"title": "Ephemeral metadata"}, "contentDetails": {"duration": "PT12S"}}]})
    args = dict(media_url=MEDIA, expected_sha256=expected, opener=open_fixture, youtube=service, **PERMISSION)
    source_root = tmp_path / "store"
    first = acquire(YT, source_root, **args)
    second = acquire(YT, source_root, **args)
    assert first.path == second.path and second.cache_hit and len(pulls) == 1
    assert second.public()["source"]["title"] == "Ephemeral metadata"
    assert "Ephemeral metadata" not in json.dumps(Jobs(source_root).status(first.job_id))
    data = reopen(source_root, first.job_id, resolve(YT), **PERMISSION)
    assert data.media_id == "sha256:" + expected
    with pytest.raises(BridgeError, match="different source"):
        reopen(source_root, first.job_id, resolve(TW), **PERMISSION)

    transcript = tmp_path / "speech.json"
    transcript.write_text(json.dumps({"segments": [{"start_ms": 1000, "end_ms": 9500,
        "text": "I finally realized why the library keeps blue envelopes in different books. "
                "What matters is the note that connects the reader with the author."}]}))
    from chomp.evolution import EvolutionConfig
    from chomp.streambridge.workflow import process_acquired
    result = process_acquired(data, tmp_path / "review", rights_ref=PERMISSION["rights_ref"], transcript_path=transcript,
                              render=True, layout="fit", evolution=EvolutionConfig(disabled=("visual",)))
    assert result["source_link"]["media_id"] == result["source_id"]
    assert result["selected_for_review"] == 1
    edit = json.loads(Path(result["clips"][0]["edit_spec"]).read_text("utf-8"))
    assert edit["source_link"]["source"]["canonical_url"] == YT


def test_local_source_attach_is_copyless(recording, tmp_path):
    acquired = acquire(str(recording), tmp_path / "store", **PERMISSION)
    assert acquired.path == recording.resolve()
    assert acquired.source.source_id == acquired.media_id
    assert acquired.source.duration_ms == acquired.duration_ms


def test_hls_clock_discontinuities_and_encryption():
    prefix = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:16\n"
    chunks, ended, _ = parse_hls(prefix + "#EXT-X-PROGRAM-DATE-TIME:2026-01-02T00:00:10Z\n"
                                 "#EXTINF:6,\n16.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:5.5,\n17.ts\n#EXT-X-ENDLIST",
                                 "https://creator.example/live/index.m3u8")
    clock = BroadcastClock(LiveOptions(broadcast_start="2026-01-02T00:00:00Z"))
    a = clock.position(chunks[0], chunks[0].duration_ms)
    b = clock.position(chunks[1], chunks[1].duration_ms)
    assert ended and (a["start_ms"], a["end_ms"], b["start_ms"], b["end_ms"]) == (10000, 16000, 16000, 21500)
    assert b["epoch"] == 1 and b["discontinuity"]
    with pytest.raises(BridgeError, match="Encrypted"):
        parse_hls(prefix + '#EXT-X-KEY:METHOD=AES-128,URI="key"\n', "https://creator.example/index.m3u8")


def test_live_feed_reconnects_and_marks_missing_sequence(recording, tmp_path):
    segment = tmp_path / "two.ts"
    exec_checked(["ffmpeg", "-v", "error", "-i", str(recording), "-t", "2", "-c", "copy", "-f", "mpegts", str(segment)])
    media = segment.read_bytes()
    request_count = 0

    @contextmanager
    def feed(url, **kwargs):
        nonlocal request_count
        if url.endswith(".m3u8"):
            request_count += 1
            if request_count == 2:
                raise BridgeError("network_error", "synthetic disconnect")
            sequence = 8 if request_count == 1 else 10
            finished = "" if sequence == 8 else "#EXT-X-ENDLIST\n"
            manifest = f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{sequence}\n#EXTINF:2,\n{sequence}.ts\n{finished}"
            yield Body(manifest.encode(), mime="application/vnd.apple.mpegurl"), url
        else:
            yield Body(media, mime="video/mp2t"), url

    result = run_live("https://creator.example/feed.m3u8", tmp_path / "stream",
                      options=LiveOptions(poll_seconds=.1, max_seconds=5, idle_seconds=3, max_clips=0, start_ms=1000),
                      opener=feed, **PERMISSION)
    assert result["status"] == "ended" and result["reconnects"] == 1
    assert result["clock_ms"] == 5000 and result["discontinuities"] == 1
    assert result["chunks"][-1]["gap_duration_unknown"]
    assert result["chunks"][-1]["clock_source"] == "estimated_after_gap"


def test_metadata_monitor_keeps_media_status_separate(tmp_path):
    from chomp.streambridge.live import monitor_live
    service = YouTube(api_key="test-key", request=lambda *a: {"items": [
        {"id": "M7lc1UVf-VE", "snippet": {"title": "Ephemeral"}, "contentDetails": {"duration": "PT12S"}}]})
    result = monitor_live(YT, tmp_path, youtube=service, **PERMISSION)
    assert result["status"] == "ended" and result["media_status"] == "unavailable"
    assert result["feed_type"] == "metadata_only"
    assert "Ephemeral" not in json.dumps(result)


def test_finalization_waits_for_after_context(tmp_path, monkeypatch):
    from chomp.streambridge.live import RollingBuffer

    def analysis(path, out, **kwargs):
        job = out / "job"
        job.mkdir(parents=True, exist_ok=True)
        ledger = job / "candidates.json"
        ledger.write_text(json.dumps({"candidates": [{"candidate_id": "moment", "decision": "selected",
                                           "candidate": {"start_ms": 0, "end_ms": 8000}}]}))
        return {"candidate_ledger": str(ledger)}

    monkeypatch.setattr("chomp.streambridge.live.process", analysis)
    monkeypatch.setattr("chomp.streambridge.live.probe", lambda _: {"duration_ms": 10_000})
    monkeypatch.setattr(RollingBuffer, "_window", lambda *args: None)
    buffer = RollingBuffer(tmp_path, resolve(YT), LiveOptions(context_seconds=10, lookahead_seconds=5),
                           PERMISSION["rights_ref"])
    buffer.rows.append({"start_ms": 0, "end_ms": 10_000, "epoch": 0, "sequence": 0, "sha256": "demo", "segments": []})
    buffer.analyze(ended=False)
    assert len(buffer.pending) == 1 and not buffer.ready
    buffer.analyze(ended=True)
    assert not buffer.pending and len(buffer.ready) == 1
    assert buffer.ready[0]["status"] == "review_ready"

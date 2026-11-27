# StreamBridge

StreamBridge resolves Twitch, YouTube and local sources, keeps platform metadata separate from editing media, and passes authorized recordings to the same Keystone or EVOLUTION pipeline. The browser player and acquisition service share a source ID. Neither player supplies the renderer with video bytes.

## Windows setup

Run from the StreamBridge checkout. Put FFmpeg and FFprobe on PATH. Use the existing local Whisper checkpoint for `--transcribe` or live `--asr-model`.

~~~powershell
uv sync --python 3.13 --extra asr --extra director --extra facecam --extra dev
uv run --inexact chomp hardware
~~~

Load credentials into the current PowerShell process or a local secret store. The sample values below are placeholders. An API key suffices for public YouTube metadata. Use a user OAuth access token with the YouTube read-only scope for authorized live chat and creator broadcast reads. For Twitch metadata, use a registered app's client ID and either an app/user access token or the app client secret for client credentials. Prefer a Twitch user access token with the narrower permission for native clip operations.

~~~powershell
$env:CHOMP_YOUTUBE_API_KEY = '<restricted YouTube Data API v3 key>'
$env:CHOMP_YOUTUBE_ACCESS_TOKEN = '<optional YouTube OAuth access token>'
$env:CHOMP_TWITCH_CLIENT_ID = '<registered Twitch app client ID>'
$env:CHOMP_TWITCH_CLIENT_SECRET = '<optional Twitch app client secret>'
$env:CHOMP_TWITCH_ACCESS_TOKEN = '<optional Twitch OAuth access token>'
~~~

OAuth tokens take precedence over the corresponding API-key or app-secret option. This release reads existing tokens. Replace expired values through the provider's authorized flow. Revoke Google account access or rotate the API key in Google Cloud. Revoke Twitch access through Connections or Twitch's token-revocation endpoint, then remove the affected environment variable. [Google account permissions](https://myaccount.google.com/connections) · [Cloud API key controls](https://cloud.google.com/docs/authentication/api-keys) · [Twitch revocation](https://dev.twitch.tv/docs/authentication/revoke-tokens/)

## Inspect and process a completed recording

~~~powershell
$source = 'https://www.youtube.com/watch?v=<video-id>'
$rights = 'Creator supplied the original and authorized editing'
uv run --inexact chomp source inspect $source --origin https://clips.example.com

uv run --inexact chomp --input $source --media-file 'D:\Videos\creator-original.mp4' `
  --engine evolution --transcribe --asr-model small --asr-device cpu --layout shorts --render `
  --rights-ref $rights --acknowledge-permission
~~~

The original must use the timeline you intend to edit. StreamBridge preserves media-relative timestamps and actual measured duration. For a creator-supplied excerpt, keep its platform offset separately and translate timestamps only when you have verified that alignment. A different API-reported duration does not change the recording's edit clock. Use `--audio-stream`, `--max-clips`, `--evolution-min-ms`, `--evolution-max-ms`, `--profile`, and the existing Director campaign options where applicable.

Pass a creator HTTPS original with `--media-url`. Prefer `--media-url-env` for signed links to keep them out of command-line arguments. A direct HTTPS URL with a recognized video extension may also serve as the input. Raw platform pages return an actionable media status unless an authorized original or native Twitch clip download is supplied. `--twitch-download` uses a verified Twitch clip URL and the creator/editor's scoped token. Add `--portrait` to request Twitch's portrait asset when available.

~~~powershell
$env:CHOMP_CREATOR_ARCHIVE = '<authorized signed HTTPS URL>'
uv run --inexact chomp source acquire $source --media-url-env CHOMP_CREATOR_ARCHIVE `
  --sha256 '<creator-supplied SHA-256>' --rights-ref $rights --acknowledge-permission
uv run --inexact chomp source status '<id>'
uv run --inexact chomp source cancel '<id>'

uv run --inexact chomp --input $source --acquired-job '<completed-id>' --engine evolution `
  --transcribe --asr-model small --render --rights-ref $rights --acknowledge-permission
~~~

The CLI prints progress JSON on stderr and the result on stdout. Jobs run in the foreground. Check or cancel from a second terminal with the same `--source-store`. A client can preallocate a lowercase hexadecimal UUID without hyphens with `--job-id`. Remote files are staged, size-checked, probed, hashed and stored by their content digest. Reopens verify the digest. Local originals stay at their original paths. A cancelled or interrupted download can be retried using a new job ID. An unsuccessful transfer restarts from byte zero.

The workspace defaults to `outputs/streambridge`. It holds receipts, content, and job state. Keep it private. API response fields are available from a fresh inspect call or the acquisition result in memory. Durable acquisition receipts and edit artifacts retain the canonical source ID and URL, acquisition method, media digest, and time mapping. API titles, thumbnails, chat, and raw responses stay out of those artifacts.

## Live feeds and monitoring

A Twitch channel or YouTube video can be monitored independently of media. An authorized creator HTTPS feed or local spool is required for real capture. HTTPS capture accepts a single-rendition, unencrypted MPEG-TS HLS media playlist. The local producer can atomically publish numbered files like `000001.ts`, plus an `END` marker when the feed finishes.

~~~powershell
uv run --inexact chomp live start $source --metadata-only --max-seconds 600 `
  --poll-seconds 30 --rights-ref $rights --acknowledge-permission

uv run --inexact chomp live start $source --feed-url-env CHOMP_CREATOR_HLS `
  --asr-model small --asr-device cpu --lookahead-seconds 15 --retention-seconds 180 `
  --render --rights-ref $rights --acknowledge-permission

uv run --inexact chomp live start outputs/spool --asr-model small --render `
  --rights-ref 'Generated audiovisual test' --acknowledge-permission

uv run --inexact chomp live status '<id>'
uv run --inexact chomp live stop '<id>'
uv run --inexact chomp youtube chat $source --pages 2 --rights-ref $rights --acknowledge-permission
~~~

Live capture polls for new completed chunks, verifies the selected audio track, retains a bounded rolling window and reuses individual Whisper results. EVOLUTION analyzes a contiguous context window. A candidate near the live edge stays pending until the configured lookahead arrives. A confirmed producer end releases the remaining lookahead requirement and treats the actual recording end as the boundary. A manual stop, time limit, or idle timeout leaves the last unresolved candidates pending. Reviews are deduplicated and can be rendered only after this context check.

For a supplied broadcast start and HLS program-date timestamps, the controller maps to the broadcast clock. Use `--start-ms` when you know the initial chunk's broadcast-relative offset. Other captures use a capture-relative clock. Local TS uses video-track durations when available. Lost chunks create a discontinuity and timing is labeled estimated if the duration of the gap is unknown. Clips do not span discontinuity epochs.

## Confirmed native Twitch VOD operation

~~~powershell
uv run --inexact chomp twitch auth-status
uv run --inexact chomp twitch create-clip 'https://www.twitch.tv/videos/<vod-id>' `
  --end-seconds 120 --duration-seconds 25.5 --title 'Creator-approved clip' `
  --rights-ref $rights --acknowledge-permission --confirm-create
uv run --inexact chomp twitch clip-status '<clip-id>' --wait-seconds 15
~~~

That request asks Twitch for 94.5–120 seconds. The write endpoint uses an integer end offset, with a duration of 5–60 seconds in tenths. The read endpoint's observed offset is a clip **start** when Twitch supplies it. Treat a 202 as accepted for processing and poll Get Clips for readiness. An ambiguous write failure is reported without retrying the POST. Review how the clip appears on Twitch before making another request.

[Backend contract and verification](backend.md) · [Draft PR #15](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/15)

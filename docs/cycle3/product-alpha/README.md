# Chomp-Chomp Studio

Studio runs locally. It accepts authorized recordings, creator HTTPS media, and permitted live HLS feeds. Review exports stay on the machine.

## Start Studio

From the Product Alpha checkout in Windows PowerShell, with FFmpeg and FFprobe on PATH:

~~~powershell
uv sync --python 3.13 --extra asr --extra facecam --extra director --extra dev
uv run --inexact chomp serve
~~~

Open [Studio on loopback](http://127.0.0.1:8765/). For automatic transcription, install or use the existing local `small` Whisper checkpoint. To preview YouTube metadata, start the server from a process that already has `CHOMP_YOUTUBE_API_KEY` or an authorized OAuth access token in its environment. [Source credential setup](../streambridge/README.md)

Choose a local video in Studio, enter its rights reference, and optionally supply a source-clock transcript under the media options. Set a count and duration range, then start processing. The Activity card shows worker stages and measured quantities. Use Results to play a clip, download MP4 or ZIP, trim and rerender, or record approval/rejection. A revision retains its parent job and original recording. Feedback is attached to the specific preview digest.

To correct framing, open **Trim and reframe**, load an actual source frame, and drag a source region. Mark the streamer, gameplay, or a protected HUD region. Select the streamer to use a persistent top panel. Review the revised layout before approval. A protected region switches the relevant gameplay view to a fit that retains the chosen pixels, which may leave padding.

## Source links and live capture

- A Twitch or YouTube URL can provide metadata and the official platform player. An embedded player can fail even when the Data API reports the video as embeddable. Studio keeps the direct platform link.
- To render a YouTube archive, attach the creator's original local recording or an independently authorized HTTPS file. Both the `/watch?v=` and `/live/` share URLs normalize to the same video ID. Studio uses the broadcast status returned by the API.
- For an active broadcast, use **Monitor live status** to track public metadata, or attach a separate authorized HLS media playlist to capture and analyze video. An existing local producer spool is also supported through the CLI. Twitch's separately authorized native clip downloads are available from the media options.
- There is no automatic publishing. Use the separate explicitly confirmed `chomp twitch create-clip` operation if appropriate.

## CLI parity

A requirements file is shared by the browser job service, direct CLI and live controller. This example targets five valid clips within the intersection of campaign and user ranges.

~~~json
{
  "clips": 5,
  "mode": "TARGET",
  "min_ms": 15000,
  "max_ms": 45000,
  "preferred_ms": 30000,
  "campaign_min_ms": 1000,
  "campaign_max_ms": 60000,
  "aspect": "9:16",
  "resolution": 540,
  "layout": "shorts",
  "captions": true,
  "hardware": "eco"
}
~~~

`TARGET` searches a wider candidate pool and reports a shortfall, with decision counts, when it finds fewer valid distinct clips. `UP_TO` returns only qualifying clips within the requested ceiling. The 20-clip ceiling applies to completed and live jobs. `resolution` is the short edge. The supported output is MP4, H.264 and AAC.

~~~powershell
uv run --inexact chomp job validate .\requirements.json
uv run --inexact chomp job preset-save 'My Shorts' .\requirements.json
uv run --inexact chomp job create --file 'D:\Videos\creator-original.mp4' --requirements .\requirements.json `
  --rights-ref 'Creator approved editing' --acknowledge-permission

uv run --inexact chomp job list
uv run --inexact chomp job status '<job-id>'
uv run --inexact chomp job clips '<job-id>'
uv run --inexact chomp job download '<job-id>' 1 --out .\clip.mp4
uv run --inexact chomp job zip '<job-id>' --out .\clips.zip
uv run --inexact chomp job revise '<job-id>' 1 --start-ms 11370 --end-ms 47950
uv run --inexact chomp job feedback '<job-id>' 1 --decision approve
uv run --inexact chomp job cancel '<job-id>'
uv run --inexact chomp job retry '<job-id>'
~~~

The initial request prints a job ID while processing runs. Check and cancel it from another terminal sharing the same `--store`. Studio's default store is `outputs/studio`. The completed-review outputs, uploads, and acquisition cache are separate from source metadata. Uploaded files are deduplicated by content; a local CLI file remains at its original path.

For the existing direct pipeline, use `chomp --input RECORDING --requirements .\requirements.json --transcribe --render --rights-ref REFERENCE --acknowledge-permission`. For a creator playlist use `chomp job create --live --source PLATFORM_URL --feed-url-env ENV_VARIABLE --requirements .\requirements.json` with the same rights options. A `--spool` path enables a controlled local producer replay. A metadata-only session uses `--monitor`.

Without a Director provider, Studio shows local provisional selection. Free-form editorial instructions go to the optional configured provider only after evidence-sharing confirmation. Local filtering applies explicit excluded topics and can use preferred topic hints. With the GPU profile, the renderer attempts H.264 NVENC and falls back to CPU encoding. The Whisper device is separately configurable.

[Architecture, HTTP contract and verification](verification.md) · [Draft PR #16](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/16)
# Stage 1 / Section 1 — Test and CI gate

**Purpose:** Make changes safe to ship by detecting installation, syntax, subtitle, audio and rendering regressions on Windows and Linux. Passing CI does **not** establish that the highlight detector finds entertaining content.

## CI behavior

The `CI` workflow triggers on pull requests and pushes to `main` and `feat/**`. It runs four combinations: Windows and Ubuntu, each with Python 3.11 and 3.13.

Each combination checks:

1. FFmpeg and FFprobe presence, plus the subtitle/libass filter.
2. Editable installation of `.[dev]` (no ASR model, no API or agent calls).
3. `pip check`, Python compilation and `chomp --help`.
4. Offline tests, including synthetic H.264/AAC rendering and decoded non-silent audio, caption files under paths with spaces, time validation, duplicate suppression and speaker-role contracts.

Tests use generated local fixtures. The workflow never downloads third-party streams, calls paid Meta/GPT/Claude APIs, accesses API secrets, posts clips, or transcribes real creator footage.

## Local Windows checks

```powershell
python -m pip install -e ".[dev]"
ffmpeg -version
ffprobe -version
chomp --help
python -m pip check
python -m compileall -q chomp tests
python -m pytest -q -ra
```

Optional ASR validation is a **separate test**. Install `.[asr]` and test only footage you have permission to use. Automated CI doesn't download Whisper model weights or verify model accuracy.

## Section 1 exit criteria

- All four GitHub Actions matrix jobs pass. If one fails, investigate rather than silently skipping tests.
- Synthetic export has correct H.264 video and decodable, non-silent AAC audio.
- Subtitle rendering works from paths containing spaces.
- No credentials or paid services are required to run CI.
- Before claiming real-world media quality, manually test the actual generated clips on the owner's Windows machine.

## Out of scope

Automatic streamer identification, clip semantic quality/virality, remote VOD ingestion, live capture and social posting belong to later sections.

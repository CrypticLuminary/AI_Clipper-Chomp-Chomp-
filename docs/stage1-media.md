# Stage 1 / Section 2 — Media and export acceptance

This section tests **how a selected interval is rendered**, independently of how highlight candidates are chosen.

## Production formats

- **Facecam + gameplay:** Confirmed source facecam rectangle fills the top 35%; gameplay fills the bottom 65%. 
- **Gameplay only:** A center-cropped portrait from the source (or explicit `gameplay_roi`). No portrait-width letterbox strips.
- Both are 540×960 H.264/AAC preview files. This is a safe baseline, not a complete scene-aware editor.
- Legacy `letterbox` is explicit for troubleshooting/comparison only.

## Controls

- `--start`, `--end` (source seconds): Direct manual preview, no heuristic ranker. Both are required together, and `--render` is required.
- `--layout`: `auto`, `facecam-gameplay`, `gameplay-only`, or `letterbox`.
- `--facecam-roi`, `--gameplay-roi`: Source pixel coordinates `x,y,w,h`. Invalid/out-of-frame ROIs are rejected.
- `--audio-stream N`: Select the Nth source audio track; preserve correct channel and AAC output.
- `--transcript` in manual mode: Optional clip-relative SRT from source timestamped segments.

## Regression gates

- All CI matrix jobs pass on Windows/Ubuntu, Python 3.11/3.13.
- Unit tests verify layout auto-resolution and explicit ROI requirements.
- Synthetic media verifies exact interval duration, H.264 portrait shape and decoded non-silent audio, including a source with two audio tracks.
- Path-with-spaces and libass subtitle regression tests remain green.
- An owner manually reviews a real authorized recording on Windows in **both** layouts; confirm audio playback, visibility of HUD/gameplay, intended webcam, no muted output, no subtitle overlap, and acceptable start/end.

## Not yet delivered

- Automatic facecam identification or tracking, activity-aware gameplay cropping, intelligent clip start/payoff recognition, reliable speaker separation, verified streamer role, downloadable remote VODs, or actual clip-quality prediction. Those are separate design sections.

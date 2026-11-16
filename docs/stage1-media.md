# Stage 1 / Section 2 — Media and export acceptance

See the [current Stage 1 workflow](stage1-quality.md) for grouped temporal layouts, edit revisions, and review approval.

This section tests **how a selected interval is rendered**, independently of how highlight candidates are chosen.

## Production formats

- **Facecam + gameplay.** The webcam region fits in the top 35%. The lower panel preserves the selected gameplay region by default.
- **Gameplay only.** The portrait fills the frame with a center crop or the supplied `--gameplay-roi`.
- **Fit.** The full source is visible over a blurred background. An explicit `--gameplay-roi` limits the view to that region.
- `letterbox` uses padding for troubleshooting and comparisons. Choose a 540×960 review preview or a 1080×1920 final profile.

## Controls

- `--start`, `--end` (source seconds): Direct manual preview, no heuristic ranker. Both are required together, and `--render` is required.
- `--layout`: `auto`, `auto-facecam`, `facecam-gameplay`, `gameplay-only`, `fit`, or `letterbox`. `auto` uses a split when you provide a facecam rectangle and fits the full source otherwise.
- `--facecam-roi`, `--gameplay-roi`: Source pixel coordinates `x,y,w,h`. Invalid/out-of-frame ROIs are rejected.
- `--audio-stream N`: Select the Nth source audio track; preserve correct channel and AAC output.
- `--transcript` in manual mode: Optional clip-relative SRT from source timestamped segments.

## Automatic facecam for review previews

Install the optional local detector and try it on an authorized recording. OpenCV uses its bundled Haar detector by default. If you have a local YuNet ONNX file, pass `--facecam-model` for the OpenCV DNN detector. The app works offline, including its fallback when OpenCV is unavailable. [OpenCV YuNet model and license](https://github.com/opencv/opencv_zoo/tree/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet)

```powershell
python -m pip install -e ".[facecam]"
chomp --input "authorized recording.mp4" --rights-ref "your-authorization" --acknowledge-permission --layout auto-facecam --render
```

`auto-facecam` samples selected ranges at roughly 375 ms intervals. Stable small-camera spans can use a split and uncertain spans fall back to full-frame fit. The detector looks for persistent container edges and flags padded estimates for inspection. A supplied ROI overrides the detector. Inspect the selected gameplay and webcam through each layout transition before approval.

## Regression gates

- All CI matrix jobs pass on Windows/Ubuntu, Python 3.11/3.13.
- Unit tests verify layout auto-resolution and explicit ROI requirements.
- Synthetic media verifies exact interval duration, H.264 portrait shape and decoded non-silent audio, including a source with two audio tracks.
- Path-with-spaces and libass subtitle regression tests remain green.
- An owner manually reviews a real authorized recording on Windows in **both** layouts; confirm audio playback, visibility of HUD/gameplay, intended webcam, no muted output, no subtitle overlap, and acceptable start/end.

## Not yet delivered

- Activity-aware gameplay tracking and comparative real-footage measurements of automatic layouts remain follow-up work. Remote acquisition and live capture belong to later stages.

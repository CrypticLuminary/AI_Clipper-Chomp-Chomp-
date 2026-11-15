# Stage 1 / Section 2 — Media and export acceptance

This section tests **how a selected interval is rendered**, independently of how highlight candidates are chosen.

## Production formats

- **Facecam + gameplay.** The source facecam crop fills the top 35%. Gameplay fills the bottom 65%.
- **Gameplay only.** The portrait fills the frame with a center crop or the supplied `--gameplay-roi`.
- **Fit.** The full source is visible over a blurred background. An explicit `--gameplay-roi` limits the view to that region.
- `letterbox` uses black padding for troubleshooting and comparisons. All formats render 540×960 H.264/AAC review previews.

## Controls

- `--start`, `--end` (source seconds): Direct manual preview, no heuristic ranker. Both are required together, and `--render` is required.
- `--layout`: `auto`, `auto-facecam`, `facecam-gameplay`, `gameplay-only`, `fit`, or `letterbox`. `auto` uses a split when you provide a facecam rectangle and gameplay-only otherwise.
- `--facecam-roi`, `--gameplay-roi`: Source pixel coordinates `x,y,w,h`. Invalid/out-of-frame ROIs are rejected.
- `--audio-stream N`: Select the Nth source audio track; preserve correct channel and AAC output.
- `--transcript` in manual mode: Optional clip-relative SRT from source timestamped segments.

## Automatic facecam for review previews

Install the optional local detector and try it on an authorized recording. OpenCV uses its bundled Haar detector by default. If you have a local YuNet ONNX file, pass `--facecam-model` for the OpenCV DNN detector. The app works offline, including its fallback when OpenCV is unavailable. [OpenCV YuNet model and license](https://github.com/opencv/opencv_zoo/tree/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet)

```powershell
python -m pip install -e ".[facecam]"
chomp --input "authorized recording.mp4" --rights-ref "your-authorization" --acknowledge-permission --layout auto-facecam --render
```

`auto-facecam` samples up to 12 frames inside each selected clip or manual preview range. A recurring small face near a frame edge can trigger a fixed padded crop. The renderer fits that crop into the top panel so its edges stay visible. Brief appearances, static images, similarly supported alternatives and major moves during a clip fall back to `fit`. `--facecam-roi` overrides the detector. The edit spec records the layout, rectangle and sampling evidence so a reviewer can correct an unsuitable crop. The detected rectangle estimates framing around a face. Confirm it shows the intended streamer, and check captions and gameplay before approving an output.

## Regression gates

- All CI matrix jobs pass on Windows/Ubuntu, Python 3.11/3.13.
- Unit tests verify layout auto-resolution and explicit ROI requirements.
- Synthetic media verifies exact interval duration, H.264 portrait shape and decoded non-silent audio, including a source with two audio tracks.
- Path-with-spaces and libass subtitle regression tests remain green.
- An owner manually reviews a real authorized recording on Windows in **both** layouts; confirm audio playback, visibility of HUD/gameplay, intended webcam, no muted output, no subtitle overlap, and acceptable start/end.

## Not yet delivered

- Scene-by-scene face tracking, activity-aware gameplay cropping, intelligent clip start/payoff recognition, verified streamer identity, reliable speaker separation, remote VOD acquisition and measured clip-quality prediction are outside Stage 1.

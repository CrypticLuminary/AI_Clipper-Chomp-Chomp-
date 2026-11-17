# Webcam panels and foreground cutouts

Automatic Shorts framing treats YuNet faces and `camera_container()` rectangles as candidates. It checks the proposed perimeter at the same source coordinates across several frames before using it as a camera crop or as excluded gameplay. Source-frame edges supply no independent evidence. A zoom that leaves one interior seam needs another observed outline of that panel. A short trim at an opening or closing boundary can use more densely sampled source frames.

The edit JSON records the result in `facecam_detection.source_geometry`. Verified rectangles use `rectangular_webcam` and publish their source boxes. An optional face-anchored segmentation pass can report `foreground_cutout` where a portrait mask persists as its surroundings move independently. Otherwise unverified geometry reports `unknown`. Both unverified geometry and cutouts use one full-source letterbox with no webcam crop. This retains the gameplay and HUD that are visible in the recording and draws the foreground person once. The result trades vertical fill for coverage. A SubjectLock plan that explicitly declares no source overlay can still use the tracked gameplay crop.

## Optional local model

`--person-model` accepts OpenCV Zoo's PP-HumanSeg ONNX model. It uses the installed `facecam` extra, runs on selected clip ranges, and has no runtime download. The model is Apache 2.0 and the floating-point file is about 6.2 MB. [Model](https://huggingface.co/opencv/human_segmentation_pphumanseg/blob/e876e63603f6c65c1a22576bfd5fc7c07ecf9eb9/human_segmentation_pphumanseg_2023mar.onnx) · [License](https://huggingface.co/opencv/human_segmentation_pphumanseg/blob/e876e63603f6c65c1a22576bfd5fc7c07ecf9eb9/LICENSE)

The Speed prototype found masks for Franklin as well as Speed. Classification requires a stable face position, a face-connected mask, continuity between adjacent masks, and repeatedly stronger changes in both nearby background strips. Support from more than one plausible track yields `unknown`. The classifier is conservative for a cutout away from the source edges or on nearly static gameplay.

From an installed checkout, use your source, local model, and output paths.

```powershell
chomp --input 'D:\AI _Clipper\chomp-chomp\AI_Clipper-Chomp-Chomp-\test3 (speed vod).mp4' `
  --output 'outputs/cutout-check' --layout shorts --start 2465.470 --end 2481.070 `
  --render --profile final --acknowledge-permission --rights-ref 'Local footage review' `
  --person-model 'outputs/cutout-prototype/pphumanseg.onnx'
```

Omit `--person-model` for the same source-preserving framing with an `unknown` classification on this interval. Pass your saved transcript with `--transcript` if you want captions. The new automatic plans use planner revision 12. The model's SHA-256 participates in the framing cache key.

## Real footage review

Speed at `[2465470, 2481070)` ms, compared with the saved automatic Shorts export at the same bounds.

| Measure | Before | Fixed |
| --- | ---: | ---: |
| Incorrect webcam panels | 6 | 0 |
| Layout segments | 11 | 1 |
| Letterbox interruptions | 5 | 0 |
| Largest incorrect panel as a fraction of the source | 79.5% | 0% |
| Minimum source area in the gameplay view | 7.1% | 100% |
| Source face observations projected into exactly one view | 936/936 | 936/936 |

In the old export the opening camera slot shows Franklin. The largest false panel shrinks the gameplay view to a narrow strip, and the later crop loses the first letters of “Mission Passed.” The fixed export keeps those regions in frame and keeps the source framing steady. The new position uses a single full-width inset within the vertical canvas. Both outputs have 936 matching video timestamps and 748,800 matching decoded stereo samples per channel. Caption text and cue times match. The manual comparison import removes the trailing blank line from the original SRT.

Emiru at `[16490, 29590)` ms produces a byte-identical MP4 and SRT against the previous planner. Jay3 at `[73350, 90170)` ms retains both zoom boundaries. Its seeded plan covers all 510 protected-region observations in the selected gameplay view. The local comparison includes the source, previous export, and fixed export. Human playback approval is pending.

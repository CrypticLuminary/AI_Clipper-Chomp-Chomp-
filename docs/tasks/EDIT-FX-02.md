# EDIT-FX-02 SubjectLock

Branch `work/edit-fx-02-subjectlock` starts at StableCamera `220acdba0b4ea21fe972e0c4f18a79f47a67af53`. The tracker and benchmark were frozen at `334aca7053fec0038c1333a660a15719103675eb` before inspecting FaceOcc2. [Tracking research and pinned source](../editing/subject-tracking-research.md) · [Regression index](../editing/regressions.md)

## Existing system and integration

The automatic Shorts planner in `reframe.py` verifies camera containers, estimates shot boundaries and builds source-clock `LayoutSegment` paths. Its motion/detail and optional saliency maps estimate activity. The camera motion controller applies hold, pan or track to positions. `framing.py` and `clipwork.py` carry the validated plan into `media.py` and its one FFmpeg renderer. The Editorial Director already names subjects, evidence regions and intended shots in its separate contract. The earlier comparison tool measures geometry and rendering on matching clocks.

SubjectLock takes explicit observations as input. It tracks one identity with Shi–Tomasi points, forward/backward LK, affine consensus and a live-patch check. After a loss it can check up to 12 times within 2.25 seconds, initially near the last confirmed region and then across the available picture. Two spatially consistent, distinctive matches are required to re-detect. A known overlay that clears starts a fresh bounded search. Detected cuts reset the track until a new reviewed seed. Crops use confident observations and protected source boxes. An unsupported target leaves a stable context view and a review warning. In the short Jay3 opening it also checks the camera mask against three live frames before it excludes the player's area.

## Subject plan and observations

The opt-in entry point is an exact-range `--layout shorts --subject-plan FILE --render` request. A plan binds one subject to a source SHA-256. Source rectangles are oriented integer `[x, y, width, height]`, with start inclusive and end exclusive. A point seed expands to a small local support window. Use one or more ordered seeds from human selection, reviewed editorial evidence or a verified detector. Provide `source_has_overlay: false` only when the input is known to be an uncomposited recording.

```json
{
  "schema_version": "chomp-subject-seed/1",
  "source_id": "sha256:<hash of the source file>",
  "subject_id": "selected-player",
  "seeds": [
    { "at_ms": 73350, "box": [656, 452, 175, 308], "origin": "manual_frame_annotation" }
  ],
  "protected_regions": [
    { "id": "match-hud", "start_ms": 77450, "end_ms": 90170, "box": [901, 971, 196, 42] }
  ]
}
```

The optional `subject_tracking` object in the saved edit has schema `chomp-subject-track/1`. It includes the source and subject IDs, seeds, protected regions, source spans, decoded timestamps, confidence, boxes and reasons. `visible` means that current pixels support the reported box. `partially_occluded` records observed clipping against a known overlay or source edge. `covered_by_source_overlay` means the panel covers the recent verified region. `tracker_lost`, `not_yet_observed` and `unknown` carry no subject box. `redetected` records an accepted live image match. Confidence is a local score. A loss near the source edge alone leaves the location uncertain. The report keeps observed subject positions and projected viewport coverage as separate fields.

The same-bounds rerender uses the saved geometry and evidence. For changed bounds, run a new seeded preview or supply a reviewed manual layout. SubjectLock uses the installed `facecam` extra. The review comparison command uses Pillow separately.

## Matching media evidence

A is StableCamera hold and B is StableCamera pan, both at `220acdb`. C is SubjectLock with the same pan limits. All methods use the same input, source bounds, preview profile and original audio. Public examples use local H.264 copies with a silent audio track so the existing renderer can open them. C receives explicit seeds and optional protected boxes. It also receives the known uncomposited-source flag on the two public recordings. A and B receive the normal automatic inputs. Both baselines letterbox all 12 seconds of FaceOcc2, while C renders a portrait crop. The identical target coverage counts measure geometric inclusion. The labels used for scoring remain in separate files. Median tracker errors omit reviewed seed frames. The crop score checks the viewport against the labeled box, including when the tracker has no usable observation.

| Recording and exact source range | A / B / C frames with ≥90% target box in viewport | C compared boxes, median center error | C tracking result |
| --- | --- | --- | --- |
| JAY3-03, 73,350–90,170 ms | 12 / 12 / 14 of 14 | 3, 23.56 px | Four reviewed seeds. 3,700 ms covered by the source overlay and 12,103 ms with insufficient tracking evidence. Zero false presence at the six labeled covered frames. |
| Nightcrawler, 780,000–795,000 ms | 17 / 17 / 17 of 17 | 1, 0 px | Six reviewed seeds. 3,967 ms with the recent target region covered and 10,383 ms of tracking loss. Zero false presence at the two labeled covered frames. The single center error sample is too small to infer accuracy. |
| Emiru 03, 16,490–29,590 ms | 5 / 5 / 5 of 5 | 4, 91.78 px | One table-object seed. The estimated box grows and drifts, while the stable crop retains the labeled object. An unseeded rerender matches the StableCamera MP4 byte for byte. |
| OpenCV `vtest`, 2,200–10,500 ms | 6 / 6 / 9 of 9 | 8, 9.34 px | One pedestrian seed. Re-detected at 9,300 and 10,400 ms, with 200 ms of tracking loss in total. |
| FaceOcc2 holdout, 0–12,000 ms | 12 / 12 / 12 of 12 | 10, 5.85 px | One initial seed from the OTB labels. 720 ms of loss and re-detection at 10,400 ms. The book partially obscured the face at three labeled frames. Enough pixels remained for the tracker to report `visible`. |

The 67 source samples and their paired output images include the Jay3 opening, the narrow strip during the source zoom and the player after the panel shrinks. C's mean target-box coverage is **99.69%** on the labeled visible Jay3 frames. The largest source-layout boundary jump on Jay3 grew from 694 to 735 px after fixing the opening crop. Nightcrawler preserved all labeled health/reticle regions and reduced in-segment travel from **206 to 0 px** against pan. Peak speed and sampled acceleration there fell from 160 px/s and 320 px/s² to zero inside those segments. The source overlay still causes a 746 px boundary jump, and the lower pane still shows the available strip during both recordings' enlarged webcam spans.

Each of the three labeled protected-region sets has 100% mean viewport coverage. There are no disjoint boxes on the compared samples and no switches to the annotated competing subjects at evaluated timestamps. Sparse labeling and missed tracks limit these counts. Caption hashes and measured audio loudness match across all paired renders. The reports include travel, speed, acceleration, boundary jumps, missed labels and loss events.

The independent source is OpenCV's continuous pedestrian footage. FaceOcc2 comes from OpenCV Extra's OTB tracking tests. The raw assets, public-source local transcodes and private recording previews remain in the ignored evidence directory. [Pedestrian sample](https://github.com/opencv/opencv/blob/7a8d99e6b2253d32d8746f74a8f9805eee9c0c52/samples/data/vtest.avi), [FaceOcc2 provenance](https://github.com/opencv/opencv_extra/tree/697e5c3de828f3154e82f222dbe902d622bbe19a/testdata/cv/tracking/faceocc2)

## Runtime and review status

Measured on Windows with Python 3.13, OpenCV 4.14 and FFmpeg 9.0.2. CPU seconds and peak working set measure the Python/OpenCV process. The elapsed time includes FFmpeg subprocesses. Results are single runs with other local jobs active. GPU usage is absent in this configuration.

| Case | A / B / C CPU seconds | A / B / C peak MiB | C tracking wall seconds |
| --- | ---: | ---: | ---: |
| Jay3 | 12.06 / 11.97 / 36.02 | 170 / 169 / 225 | 6.54 |
| Nightcrawler | 23.06 / 25.48 / 39.50 | 168 / 168 / 225 | 6.21 |
| Emiru | 7.08 / 7.33 / 23.22 | 140 / 139 / 236 | 3.64 |
| Pedestrian | 2.67 / 2.48 / 1.56 | 130 / 129 / 105 | 0.42 |
| Holdout | 1.88 / 1.73 / 1.22 | 84 / 83 / 88 | 0.36 |

**277 tests passed.** Deterministic tests cover normal movement, ambiguous lookalikes, short recovery, cuts, source coverage, protected regions, track and crop metrics, and real FFmpeg CLI/rerender fixtures. The full gameplay tracking acceptance remains open. Jay3 and Nightcrawler need multiple reviewed seeds and have substantial gaps during rapid animation. Partial occlusion by objects in the scene is underreported. Human playback and listening approval remain pending.

## Reproduce and hand off

On this machine the seeded Jay3 preview uses the existing transcript and private sidecar. Run these commands from the SubjectLock worktree.

```powershell
Set-Location -LiteralPath 'D:\AI _Clipper\chomp-chomp\edit-fx-02'
$clipPython = (Resolve-Path -LiteralPath '..\stage1-quality\.venv\Scripts\python.exe').Path
$source = (Resolve-Path -LiteralPath '..\AI_Clipper-Chomp-Chomp-\test.mp4').Path
$origin = Get-Content -Raw -LiteralPath '..\edit-fx-01\outputs\edit-fx-01\jay3-new-run\4bb25b107c5c451aeee784d0\candidate_03.edit.json' | ConvertFrom-Json
& $clipPython -B -m chomp --input $source --transcript $origin.transcript_path --start 73.350 --end 90.170 --layout shorts --camera-motion pan --subject-plan 'outputs\edit-fx-02\annotations\jay3-seed.json' --profile preview --output 'outputs\edit-fx-02\repro-jay3' --rights-ref 'User-provided recording for private review' --acknowledge-permission --render
```

The comparison artifacts start at `outputs/edit-fx-02/review.html`. The three exact-range manifests are recorded per case in `outputs/edit-fx-02/runs/<case>/bench/runs.json`. Independent labels follow `chomp-subject-labels/1` and carry the source ID, subject ID, provenance, and rows with `at_ms`, state, visible box and optional protected or distractor boxes. Pass those manifests and the label JSON to `python -m chomp.evaluation.subject_compare --hold ... --pan ... --subject ... --labels ... --out ...` in the Pillow environment.

For EDIT-FX-03, use the source-spans, state and confidence evidence to decide which **reviewed** presentation to execute. JAY3-03 should cover the choice between available gameplay context and an intentional streamer shot. A missing track with visible source gameplay needs a different review path from a covered player. Preserve the exact shot and audio clocks, and pass the same evidence to VisualQA. Start after this draft review.

# EDIT-FX-03 — Creative ShotComposer

- Status: implementation and local verification complete. Draft review and human playback pending.
- Base: `work/edit-fx-02-subjectlock` at `8dd1cb52634c4eb7dd639499a4eda5b4a4709d77`.
- Branch: [feat/edit-fx03-shotcomposer](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/tree/feat/edit-fx03-shotcomposer).
- Workspace: Codex desktop and Windows PowerShell, `D:\AI _Clipper\chomp-chomp\edit-fx-03`.
- Attribution: `ASTRA-FX03` for source research, contract design and initial implementation, based on the owner's description. `SOL61-FX03` for continuation, integration, fixes, test execution and media checks, based on this session's model configuration.
- Owned paths: `chomp/shotcomposer/`, two Director schemas, CLI and media integration, approval artifact checks, `tests/test_shotcomposer.py`, and ShotComposer reference and verification docs.

## What changed

The Director's scene decisions now feed the video renderer. Use a provider-neutral ShotPlan, or lower an existing EDIT-01 storyboard using independently reviewed source regions. The request pack can include verified source stills and the selected storyboard for the next Director pass. The model can choose a context view, separate panels, an inset, a deliberate reaction shot, camera motion, or visual emphasis. The validator checks the time range, evidence, visibility, protected content, and supported operators before rendering.

The compositor shares Chomp's FFmpeg backend, continuous audio program, and subtitle stage. Shot-specific filters run only while a view is active. A dissolve mixes two views of the same frames and the combined video is assigned one rational frame grid. The existing review command checks the exact preview, plan, evidence, report and optional SRT hashes. Both new and legacy workflows remain available.

The initial implementation session stopped at the verification handoff per the user's request to reserve tests for a cheaper model. The user then asked Sol to continue and finish. Verification discovered early dissolve opacity, top-left zoom anchoring, and a muxer limit that could remove the last legal frame. The final implementation derives dissolve progress from timestamps, uses a centered one-frame zoom, and bounds audio and video independently.

## Verification

Windows 11 build 26200, Python 3.13.14, FFmpeg 9.0.2, OpenCV 4.14, jsonschema 4.26.0. **304 tests passed**, including 27 ShotComposer cases. The synthetic video has unique luma frame IDs and different tones on its two audio tracks. It checks view selection and dissolve weights against a held-view control, plus audio pulse position and exact decoded audio equality. The matrix covers 24000/1001, 25, 30000/1001, 30, and 60 fps, and a VFR source with delayed audio. It also covers rotation, non-square pixels, 10-bit input, odd dimensions, zoom and return, an inset, timed outline and pointer, evidence mismatch, stale approval, and direct use of a saved storyboard.

```powershell
Set-Location -LiteralPath 'D:\AI _Clipper\chomp-chomp\edit-fx-03'
.\.venv\Scripts\python.exe -X utf8 -B -m pytest -q --tb=short --basetemp 'outputs/fx03-verify/pytest-release'
```

The private review packet is under `outputs/fx03-verify/review.html`. It links the clips, original excerpts, source and output frames around the cuts, timing records, and the exact plans. The render reports contain FFprobe metadata and artifact checksums.

| Source | Decision and technical result |
| --- | --- |
| JAY3-03, `test.mp4`, [73,350, 90,170) ms | Gameplay and streamer → streamer while the original webcam covers the last player region → gameplay and streamer. Decoded source transitions land at frames 22 and 244 (73,716.667 and 77,416.667 ms). The 222-frame reaction replaces the sliver of gameplay. Projection into the new crop covers at least 90% of all 14 labeled visible player boxes and fully covers all 24 labeled HUD boxes. |
| Emiru, `test2(emiru vod).mp4`, [881,500, 906,190) ms | The saved EDIT-01 Codex plan calls for a split, the game's result card, and a streamer-only ending. Independent bindings map its frame annotations to source intervals. Its switches render at frames 990 and 1230 (898,000 and 902,000 ms). The result card was observed over 240 consecutive frames in that source interval. The optional title request remains assigned to FX04. |
| Speed, `test3 (speed vod).mp4`, [2,465,470, 2,481,230) ms | A previously saved full-source control preserves the embedded cutout and game together when independent webcam geometry is unavailable. The new export matches the prior control's MP4 bytes. This is independent source coverage for the context mode. The prior preview was already available, so it does not measure blind holdout performance. |

Jay3's source SHA-256 is `f936b46d4510e8774dab57db20e3cdca95b85a57245ef32d6d636503a5d8cfe0`. The source has 60 fps. The new video retains 1,010 frames, one more than the old encode, with identical timestamps for the 1,009 common frames. The requested 16.820 s audio program is unchanged, including all 807,360 decoded stereo samples. Decoded PCM SHA-256 `6e3d46e0cd78d22a62d061550c74f6b00c527381f1e554481a405161be26c044` and the SRT byte hash match the SubjectLock export. The extra final frame ends 13.333 ms after the requested source endpoint and its actual sampled timestamp is still inside the interval.

Dense camera checks cover all 1,010 decoded frames using the existing YuNet model and the inspected source webcam regions. Sample coverage measures the supplied FX02 labels, including protected HUD. Identity tracking during gaps and the rendered framing still need playback review. Early subtitles use the original styling, and some later cues cross portions of the gameplay image below the aim region.

## Render cost

One measured pair per output size on the same Windows machine and exact Jay3 range, with the same captions, audio selection, encoder settings and existing source on disk. These measurements exclude discovery, ASR and region analysis. The new path includes source hash verification and a check of the source file identity and timestamps across rendering.

| Size | SubjectLock wall time | ShotComposer wall time | FFmpeg peak working set, old / new |
| --- | ---: | ---: | ---: |
| 540 × 960 | 2.836 s | 3.562 s | 293 / 299 MiB |
| 1080 × 1920 | 8.331 s | 8.779 s | 671 / 705 MiB |

The preview is 25.6% slower on this pair, just above the provisional 25% target. The final-size render takes 5.4% longer. The initial full-clock graph took 7.264 s and 15.836 s in earlier comparable single runs. Active frame windows and one source hash pass reduced that cost. Cache state and background work can affect a single pair. Machine details, bytes and sampled CPU totals are in the local `benchmark-final/metrics.json`.

## Run the saved Emiru test

The following uses the exact local plan and bound evidence from verification. The destination must be a new directory. Install the `director` extra first in a fresh checkout. The private plans and media are local artifacts.

```powershell
Set-Location -LiteralPath 'D:\AI _Clipper\chomp-chomp\edit-fx-03'
$source = 'D:\AI _Clipper\chomp-chomp\AI_Clipper-Chomp-Chomp-\test2(emiru vod).mp4'
.\.venv\Scripts\python.exe -m chomp director shots render --source $source --plan 'outputs\fx03-verify\emiru-lowered\plan.json' --evidence 'outputs\fx03-verify\emiru-lowered\evidence.json' --out 'outputs\emiru-director-run-02'
```

For a different selected range, prepare a request and use the corresponding reviewed region evidence and Director decision. [Director workflow](../editing/shotcomposer.md).

## Review boundaries

The source inspection and exact frame comparisons support the designated shots. Human preference, subtitle legibility, full playback and listening approval remain pending. The frame-based comparisons cannot approve those qualities. The older storyboard adapter starts with contained crops, so the Emiru draft has matte around the narrower source views. The Jay3 reaction reserves a lower margin to keep the enlarged face above the caption lane. Compare those choices during playback.

The new CLI consumes source-grounded director JSON for selected ranges. The normal batch VOD command continues to run discovery independently. The selected-range workflow is ready for use after a Director returns its plan. Review and provenance upgrades for FX04 text, FX05 time and audio effects, and FX06 visual QA stay in their assigned phases.

[Source audits and editing patterns](../editing/shotcomposer-research.md) · [Verification checklist](EDIT-FX-03-VERIFY.md) · [Regression index](../editing/regressions.md)

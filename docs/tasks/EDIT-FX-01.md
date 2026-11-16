# EDIT-FX-01 StableCamera review and handoff

The isolated branch is `work/edit-fx-01-stablecamera`, based on editorial commit `f187fd107ee0abd9a67a2dba14bf9466d42231ae`. The [engineering audit and roadmap](../editing/engine-plan.md) covers all six workstreams. The [source research matrix](../editing/upstream-research.md) pins the 15 cloned projects and records the implementation, build or test files, licenses and adoption decisions.

Shorts framing verifies brief camera misses against the current source pixels before keeping the split. It confirms camera containers independently of the output slot. A narrow portrait camera can receive a 35% slot for the entire clip when the standard 30% slot would pad a crop that can safely fill at 35%. The selected layout retains actual source frames. The default `pan` absorbs small changes and moves toward a sustained displacement. `hold` fixes the crop within the shot, and `track` follows the current action estimate. Each policy starts a fresh path at a detected cut.

## Matched recordings

All four comparisons use identical source bounds, the preview profile and original audio track 0. The source hashes are recorded in the matching `metrics.json` files. Crop travel sums source-pixel movement within the plan's segments. Peaks measure speed from adjacent crop keyframes and acceleration between the sampled intervals.

| Case | Source ms | Letterbox ms | Camera containment ms | Crop travel px | Horizontal reversals |
| --- | --- | ---: | ---: | ---: | ---: |
| Emiru 03 | 16,490–29,590 | 250 → 0 | 12,850 → 0 | 217 → 0 | 2 → 0 |
| Emiru 01 | 855,220–881,220 | 0 → 0 | 0 → 0 | 455 → 97 | 9 → 0 |
| Emiru 02 | 1,709,130–1,717,130 | 0 → 0 | 0 → 0 | 127 → 0 | 3 → 0 |
| Nightcrawler | 780,000–795,000 | 150 → 0 | 0 → 0 | 944 → 206 | 4 → 0 |

- Emiru 03 has one split over the full 13.1 seconds. The panel begins at source y=489, compared with y=639 before. YuNet directly found the camera in 46 of 52 observations. The three bridged runs used 135 decoded frames to verify the live panel. The six paired frame checks include the former flash and the streamer's hand briefly covering their face.
- Emiru 01 retained the streamer and the main gameplay in the four sampled comparisons. Its single remaining pan reduces total movement but raises peak planned speed from 92 to 148 source px/s and sampled acceleration from 156 to 296 px/s². Check that pan at playback speed.
- Emiru 02 kept readable main framing in the four sampled comparisons. Native game text and generated dialogue captions can occupy the same area. Caption placement and protected gameplay HUD regions remain open.
- Nightcrawler retains its top streamer slot through the source zooms. A known camera panel returns at 794,850 ms. Three dense face observations confirmed the short final appearance. The source zoom at 790,883–794,850 ms covers much of the gameplay. The lower panel uses the available pixels and the review records the occlusion.

All comparison MP4s passed complete decoding. Caption file hashes, integrated loudness and true peak matched for each pair. The four pages contain playable previews, frame times, crop rectangles, and path and audio metrics. Compared output frames matched one another on the clip clock, with up to 13 ms of seek rounding against the raw source clock at 60 fps. The project suite passed **268 tests** on Windows with FFmpeg 9.0.2. The cached-transcript run on the full 5,335.583 s Emiru recording produced 60 provisional candidates and three rendered review clips.

## Reproduce on Windows

Run from this worktree with FFmpeg and the `facecam` extra installed. On this machine the adjacent Stage 1 virtual environment already provides them.

```powershell
Set-Location -LiteralPath 'D:\AI _Clipper\chomp-chomp\edit-fx-01'
$clipPython = (Resolve-Path -LiteralPath '..\stage1-quality\.venv\Scripts\python.exe').Path
$vod = (Resolve-Path -LiteralPath '..\AI_Clipper-Chomp-Chomp-\test2(emiru vod).mp4').Path
$savedTranscript = (Resolve-Path -LiteralPath '..\stage1-quality\outputs\edit-01\baseline-d01\d18cdceab6bc1c161cde6d23\transcript.json').Path
& $clipPython -B -m chomp --input $vod --transcript $savedTranscript --rights-ref 'user-supplied for private editing QA' --acknowledge-permission --layout shorts --profile preview --max-clips 3 --output 'outputs/edit-fx-01/full-vod' --render
```

The exact-range comparison uses the archived candidate 3 transcript snapshot saved alongside its baseline render. The adjacent `stage1-quality` worktree must be at `f187fd1`, or use a separate checkout at that commit for the baseline. Run the comparison with a Python environment that has Pillow installed.

```powershell
$snapshot = (Resolve-Path -LiteralPath 'outputs\edit-fx-01\before\candidate_03\manual-4850fde0224d8461694a8ced\transcript.json').Path
$destination = Join-Path (Get-Location).Path 'outputs\edit-fx-01\repro-candidate-03'
$exact = @('--input', $vod, '--transcript', $snapshot, '--start', '16.490', '--end', '29.590',
    '--rights-ref', 'user-supplied for private EDIT-01 testing', '--acknowledge-permission',
    '--layout', 'shorts', '--profile', 'preview', '--audio-profile', 'original', '--render')
Push-Location -LiteralPath '..\stage1-quality'
try {
    $before = & $clipPython -B -m chomp @exact --output (Join-Path $destination 'before') | ConvertFrom-Json
} finally {
    Pop-Location
}
$after = & $clipPython -B -m chomp @exact --output (Join-Path $destination 'after') | ConvertFrom-Json
python -m chomp.evaluation.layout_compare --before $before.review_manifest --after $after.review_manifest --out (Join-Path $destination 'compare') --frame-ms 18740 --frame-ms 19490 --frame-ms 19600 --frame-ms 19740 --frame-ms 20740 --frame-ms 28840
```

Existing evidence is indexed at `outputs/edit-fx-01/review.html`. Exact-range manifests and transcript snapshots are under `before/<case>/manual-*/` and `reviewed/<case>/manual-*/` inside that output folder. Use their start/end and transcript fields to reproduce the other comparisons. Raw sources, cloned projects and previews are ignored local files.

## Review status and next assignment

The frame comparisons, hash checks, path measurements and decode checks are complete. Full playback and listening approval, independent raw cross-creator recordings and an unseen holdout are pending. The edited reference Shorts provide examples for future editorial tests. Scene-local action estimates can miss relevant subjects or HUD, and variable-rate recordings and unfamiliar camera layouts still need field coverage.

After this review, EDIT-FX-02 should start with hand-labeled target and HUD regions on Nightcrawler and an unrelated moving source. Compare fixed and current motion-driven crops against seeded OpenCV point tracking. Record visible target fraction, protected-region coverage, lost-track time, path movement and CPU/memory cost. Include a separate holdout with occlusion and scene changes before deciding whether to benchmark a learned tracker.

# Cycle 2 media regressions

The synthetic VFR fixture exposed a baseline defect: cutting source 1–5 seconds produced a 4.000-second audio stream but only 3.733333 seconds of video. The renderer correctly rejected the export rather than silently delivering it. The source changes from 15 fps to sparse frames after second four; the single-layout path had no frame-grid realization, so the final legal image did not cover the requested cut.

The legacy renderer now uses the source's bounded nominal frame rate when nominal and average rates differ. Temporal compositions use the same nominal grid. Constant-rate and ShotComposer paths retain their existing behavior. `RENDER_VERSION` advances from 7 to 8 so old cached exports are not reused as new results.

Verification: new single/temporal VFR regressions check both stream durations; the full synthetic matrix also checks nonzero source timestamps, silent sources, selected audio track 1, Unicode captions and decoding. Source-aligned decoded waveform checks are in `chomp.evaluation.technical`. Silence in a silent source is expected, not an audible-audio success.

No claim of improved webcam identity or entertainment quality is based on colored synthetic markers. Existing positive camera/SubjectLock/ShotComposer tests remain active. Human playback and source-level annotations are separate gates.

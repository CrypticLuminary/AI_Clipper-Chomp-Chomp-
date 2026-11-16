# EDIT-01 editorial director

Use the director to compare editorial decisions on a finished VOD. It uses selected source frames and the existing timestamped transcript and signal measurements. The normal clipper entry point still runs the Stage 1 baseline.

## Prepare and verify evidence

Install Python 3.11+, FFmpeg and the director extra. Use a transcript from the selected audio track when available. The candidate anchor and every frame request are source-relative integer milliseconds.

~~~powershell
python -m pip install -e '.[director]'
chomp director prepare --source 'C:\VODs\recording.mp4' --out 'outputs\experiment\evidence' --case-id dev01 --split development --rights-ref creator-grant --acknowledge-permission --candidate-id moment01 --start-ms 12500 --end-ms 28000 --anchor-ms 19000 --transcript 'C:\VODs\transcript.json' --frame-ms 13000 --frame-ms 19000 --frame-ms 24500
chomp director validate --source 'C:\VODs\recording.mp4' --bundle 'outputs\experiment\evidence\evidence.json'
~~~

The bundle uses an immutable directory with hashed PNG frames. Its source metadata includes the recording SHA-256, duration, display dimensions, and zero-based audio track. Each frame keeps the request time and the actual decoded presentation time. Verification reads the original source, matches its hash, and reproduces the attached image bytes and timestamps with FFmpeg. Use the recorded FFmpeg build when reproducing encoded frame hashes.

The window can include up to eight stills. Supply `--regions` after inspecting those stills if a shot needs explicit subjects. The JSON maps requested milliseconds to arrays of region records. Each box uses `[x, y, width, height]` in the sampled image pixels. Valid kinds and fields are in the [evidence schema](../chomp/director/contracts/evidence_bundle.schema.json). Use the same `subject_id` when a checked subject appears in multiple samples. Each supplied region also needs a confidence, origin, and verified flag. The code always provides a verified full-image region. Missing transcript and chat are recorded explicitly. Audio measurements describe energy changes, with speech and sound effects mixed in the selected track.

## Run the matched comparison

Use a Codex model available to the existing ChatGPT sign-in for both passes. Running this command sends the bounded request and sampled frames through that sign-in.

~~~powershell
chomp director compare --source 'C:\VODs\recording.mp4' --bundle 'outputs\experiment\evidence\evidence.json' --out 'outputs\experiment\comparison' --model gpt-6.1-sol
~~~

Both requests receive the same candidate, transcript, audio observations and chat indicators. The second request adds the sampled frames and their regions. Each request uses an ephemeral read-only CLI invocation with shell, search and collaboration features disabled. Status files record usage reported by the CLI, validation errors, request hashes, and the exact attached frame IDs. An existing output folder preserves its recorded attempts. Choose a fresh folder for an explicit retry.

The [plan schema](../chomp/director/contracts/editorial_plan.schema.json) is independent of the provider. A separate validator checks the source and evidence hashes, citation modality and timing, shot coverage, required regions, and caption emphasis against timed words. Those checks establish traceability. Assess whether the cited evidence supports each interpretation during playback. Every plan remains pending review with publishing disabled.

When both decisions validate, use the two neutrally labeled JSON plans and the ratings template in `blind-review`. Read the mode mapping in `comparison.json` after recording the scores. Keep development notes and independent holdout labels outside the model's evidence. The renderer continues to use its existing edit plans. Any conversion from proposed shot intent into executable geometry requires a reviewed mapping.

Implementation references are the [Codex CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [FFmpeg showinfo](https://ffmpeg.org/ffmpeg-filters.html#showinfo), and [Python JSON Schema validation](https://python-jsonschema.readthedocs.io/en/stable/validate/).

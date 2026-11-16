# Stage 1 workflow

See the [Shorts manual](shorts-layout.md) for the default tracking layout, optional saliency model, and title and caption styling.

Chomp-Chomp analyzes an authorized completed VOD, creates up to three different review clips, and records why it kept or rejected each proposal. The source clock uses integer milliseconds throughout. Subtitle sidecars use time relative to the clip.

## Setup and analysis

On Windows, install Python 3.11+ and FFmpeg with ffprobe and libass on PATH. In a virtual environment, install the base package and any optional extras you need.

~~~powershell
python -m pip install -e .
python -m pip install -e '.[asr,facecam]'
~~~

Use a local recording and a supplied transcript, or add an explicit local Whisper invocation.

~~~powershell
chomp --input 'C:\VODs\recording.mp4' --transcript 'C:\VODs\transcript.json' --rights-ref creator-grant --acknowledge-permission --render
~~~

'--transcribe' uses cached faster-whisper weights or a directory passed to '--asr-model'. Place the model and tokenizer files locally before starting. The same selected audio track drives the audio scan, ASR, and export. Use '--audio-stream 1' to select the second audio track. '--language en' overrides Whisper's automatic language detection. Normal rendered ASR runs refine the selected clip captions with a short word-timed pass. Add '--refine-captions' to do so explicitly with an imported transcript.

A transcript can contain 'start_ms', 'end_ms', 'text', and an optional 'words' array. Each word has 'start_ms', 'end_ms', and 'text', with optional 'confidence' between 0 and 1. Supply word timestamps from a reviewed aligner through this format. An unaligned transcript is labeled as approximate in the edit plan. Review density, overlap, uncertain words, and cues that intersect clip edges.

Chat is optional. '--chat-offset-ms' aligns the replay clock and '--chat-reaction-lag-ms' accounts for a known reaction delay. A rate spike supplies a proposal cue. Review a source's timing before relying on it. '--proposals-per-hour' controls the time and review burden of broad discovery.

## Editorial ranking

The default ranker gives provisional review suggestions based on the evidence in the final interval. For contextual assessment, start a local Ollama instance with a previously installed local GGUF model. Use its installed tag.

~~~powershell
chomp --input 'C:\VODs\recording.mp4' --transcript 'C:\VODs\transcript.json' --rights-ref creator-grant --acknowledge-permission --semantic-model 'local-tag' --semantic-budget 12 --render
~~~

The adapter uses the loopback Ollama server, [installed model inventory](https://docs.ollama.com/api/tags), and [structured chat responses](https://docs.ollama.com/api/chat). It validates source utterance references and preserves the cited setup, event, and payoff in the proposed cut. Model errors and insufficient evidence produce an abstention in the ledger. Reviewers still need to verify playback and visual claims. A provider can be compared with the provisional baseline against the same source annotations.

## Framing and revisions

'--layout auto' fits the full frame unless you provide a webcam rectangle. '--layout auto-facecam' opts into the local detector. Add '--facecam-model' with a locally stored YuNet model. Recurring small-camera spans can use the split and uncertain spans use the full source. Review any estimate around a face to check the actual webcam container. Explicit 'gameplay-only' is a portrait crop that can remove peripheral action.

Set an exact interval with '--start' and '--end' in source seconds plus '--render'. Use '--layout-plan' for a source-relative timeline. Segments must be adjacent and cover the chosen interval. Each row has 'start_ms', 'end_ms', and 'layout'. A 'facecam-gameplay' row also requires 'facecam_roi' as four source-pixel coordinates; confirm them against this source. An optional 'gameplay_roi' protects the region you choose to show, and 'preserve_gameplay' keeps the whole chosen region within the lower panel. The preview profile is 540×960 and the final profile 1080×1920. Use '--caption-position' to move cues away from important content and '--audio-profile balanced' to request loudness normalization.

Open the generated review.html for playable previews, source times, rationale, issues, captions, and edit plans. To change the crop, edit a **copy** of the plan's 'segments' array. To correct words, save a separate SRT on the clip clock. Start and end overrides use source seconds. A layout plan with multiple segments must still cover the revised interval. Rerendering produces a new revision from cached text and source media.

~~~powershell
chomp rerender --edit 'copy-of-candidate_01.edit.json' --captions 'corrected.srt' --profile final
~~~

Watch the new revision and confirm the cut, dialogue, crop, and playback before approving. Approval stores the hashes of the reviewed MP4, SRT, and plan and uses a new output file.

~~~powershell
chomp approve --review 'outputs\manual-revision\manual_review.json' --reviewer 'your-name' --watched --out 'approval.json'
~~~

Use the revision folder from the CLI output. For a ranked queue, pass review_queue.json and the desired '--clip' index. Publishing is manual.

## Benchmark

Keep source-event annotation separate from candidate review. Watch the authorized source with two independent reviewers before examining candidates. Each person records distinct event IDs and the following spans in source milliseconds:

- 'core_ms' covers the payoff or essential event.
- 'essential_ms' covers the complete material needed to understand it.
- 'start_range_ms' and 'end_range_ms' define acceptable cut edges.

Set 'complete' only after reviewing the whole source. An empty complete file represents a negative source. Use separate label files for each reviewer.

~~~powershell
python -m chomp.evaluation events-template --candidates 'outputs\job\candidates.json' --reviewer editor-a --out 'source-events.json'
python -m chomp.evaluation template 'outputs\job\review_queue.json' --out 'clip-reviews.json'
python -m chomp.evaluation benchmark --candidates 'outputs\job\candidates.json' --events 'source-events.json' --manifest 'outputs\job\review_queue.json' --reviews 'clip-reviews.json'
~~~

The benchmark counts a proposal hit at 80% coverage of the event core. It reports proposal count and covered minutes per source hour, whether selected clips kept the essential span, and separate review metrics for each supplied label file. A partial source annotation yields an undefined recall. ClipLedger also accepts current manual preview reports and legacy selected-clip queues.

Compare Bridge, the provisional ranker, and the local semantic option on identical authorized recordings. Keep sources used for tuning separate from the acceptance set. Include quiet moments, loud negatives, missing chat, multiple audio tracks, and changing webcams. Record the count of clips a human would publish, clip and source coverage, technical defects, and stage runtime. Test selected-range forced alignment, chat, diarization, and denser visual inspection individually if the baseline exposes a recurring failure.

## Artifacts and optional speakers

The output includes candidates.json, review_queue.json, and a plan for each selected clip. Analysis artifacts are keyed by source content, selected track, and model/configuration evidence. Generated renditions and approvals have separate identities. Concurrent jobs use local locks, and validated media and manifests are staged before publication. Retain the review artifacts and exact source recording for reproducibility.

Optional diarization produces anonymous turns on the selected audio track. Label the main streamer only after human confirmation. Keep code licenses and the licenses for exact downloaded weights in the project provenance. A model loading successfully establishes readiness to run a trial. Measure actual attribution on the intended voices and mixtures before relying on it in editorial decisions.

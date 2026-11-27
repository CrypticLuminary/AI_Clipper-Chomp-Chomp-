# EVOLUTION local run and handoff

EVOLUTION is opt-in on `work/cycle3-evolution`. The base is Keystone `5d4b84f45241e14d31934aee1ff3b2960511022b`. The regular heuristic command and `chomp clip` Director workflow remain available. Every output enters the manual review workflow. [Draft PR](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/14)

## Windows PowerShell

Install FFmpeg and FFprobe on PATH with libass support. The smoke environment used CPython 3.13.14. Install only the runtime extras you plan to use.

```powershell
git switch work/cycle3-evolution
uv sync --python 3.13 --extra asr --extra facecam
uv run --inexact chomp hardware
```

A local Whisper checkpoint is required for `--transcribe`. If it is absent, download it explicitly or pass a transcript sidecar.

```powershell
uvx --from huggingface-hub hf download Systran/faster-whisper-small --local-dir models/whisper-small
```

EVOLUTION with local CPU transcription and lightweight acoustic and visual observations.

```powershell
$source = 'D:\Videos\stream.mp4'
$rights = 'User-authorized local recording'
uv run --inexact chomp --input $source --engine evolution --hardware-profile eco `
  --transcribe --asr-model models/whisper-small --asr-device cpu `
  --layout shorts --render --rights-ref $rights --acknowledge-permission
```

Use `--transcript path\to\segments.json` in place of `--transcribe` for a source-clock sidecar. Add `--speaker-turns`, `--chat`, or `--audio-stream 1` when appropriate. A clip may be marked for framing review. Use `--facecam-roi X,Y,W,H` and optional `--gameplay-roi X,Y,W,H` when you have confirmed those source rectangles.

### Optional models

The following public weights were downloaded and run locally. Downloads are explicit. Preserve the pinned revisions because the runtime checks model hashes.

```powershell
uv sync --python 3.13 --extra asr --extra director --extra facecam `
  --extra evolution-text --extra evolution-audio --extra evolution-vision --extra dev
$env:HF_HUB_DISABLE_XET = '1'
uvx --from huggingface-hub hf download sentence-transformers/all-MiniLM-L6-v2 `
  --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41 tokenizer.json onnx/model_quint8_avx2.onnx LICENSE `
  --local-dir models/minilm
uvx --from huggingface-hub hf download litert-community/MediaPipe-AudioClassifier-YAMNet `
  --revision fcd63e19bd0a7a0d3d8c9c070af1898f7dd29675 yamnet.tflite --local-dir models/yamnet
uvx --from huggingface-hub hf download Xenova/clip-vit-base-patch32 `
  --revision 6b7c523703ae26e39be4b03774966f8571c7f523 onnx/vision_model_uint8.onnx preprocessor_config.json `
  --local-dir models/clip

uv run --inexact chomp --input $source --engine evolution --hardware-profile eco `
  --transcribe --asr-model models/whisper-small --asr-device cpu `
  --evolution-embeddings models/minilm `
  --evolution-audio-model models/yamnet/yamnet.tflite `
  --evolution-visual-model models/clip/onnx/vision_model_uint8.onnx `
  --evolution-max-semantic-frames 64 --layout shorts --render `
  --rights-ref $rights --acknowledge-permission
```

- MiniLM uses an Apache-2.0 English checkpoint with 384-dimensional masked-mean embeddings. The quantized ONNX graph was tested on AMD64. [Model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
- YAMNet uses an Apache-2.0 Google MediaPipe TFLite asset, mono selected-track PCM at 16 kHz, 975 ms windows and a nominal 480 ms hop. Wider strides are reported when the inference budget truncates overlap. [Model card](https://huggingface.co/litert-community/MediaPipe-AudioClassifier-YAMNet)
- CLIP uses the MIT-licensed Xenova vision-only quantization of OpenAI's ViT-B/32. Its normalized image embeddings help identify sparse changes. [Converted model](https://huggingface.co/Xenova/clip-vit-base-patch32) · [Original checkpoint](https://huggingface.co/openai/clip-vit-base-patch32)
- Whisper uses the existing local CTranslate2 provider. The observed `small` files were in the Hugging Face user cache. The review queue fingerprints the actual files and the runtime.

The optional `--evolution-director-model gpt-5.6-sol --evolution-reasoning-calls 8` uses an existing ChatGPT subscription signed into Codex CLI. It sends bounded evidence text and, when applicable, sampled frames to the provider. Use it when that processing is authorized for the source. The local engine works without it. Set `--evolution-disable local-review` for evidence-only operation when running locally.

## Profiles and recovery

| Profile | MiniLM batch | Motion sampling | Motion and audio sample caps | Default CLIP frame cap |
| --- | ---: | ---: | ---: | ---: |
| `eco` | 8 | 2.5 s | 2,800 each | 64 |
| `balanced` | 16 | 1.25 s | 5,600 each | 128 |
| `gpu` | 24 | 0.75 s | 9,000 each | 192 |

`gpu` can use CLIP's CUDA execution provider when it is installed and falls back to CPU. MiniLM and YAMNet in this branch use CPU. CUDA ASR is separately selected with `--asr-device cuda` and retries on CPU when GPU transcription fails. The `hardware` command reports available providers and memory. Start with ECO when available RAM is limited.

Re-run the same command and output folder to resume. A complete job with matching provenance and artifact hashes returns `reused_job: true`. Add `--evolution-retry second-pass` to make a fresh review job while reusing valid derivations. A render failure is recorded per candidate and other viable clips can still export. Optional perception failure leaves the relevant lightweight lane and availability record. Caches are under `<output>/.analysis`, model assets under `models/` if installed as above, and review artifacts under `<output>/<job_id>/`.

Repeat `--evolution-disable` to turn off individual lanes or components. Examples include `--evolution-disable visual`, `--evolution-disable hybrid` for lexical retrieval, or `--evolution-disable quiet-asr` for untouched ASR input. Omit a model path to skip that runtime. Set limits with `--evolution-max-frames`, `--evolution-max-semantic-frames`, `--evolution-max-audio-windows`, `--evolution-memory-mb`, and `--evolution-min-free-mb`. `--evolution-min-ms` and `--evolution-max-ms` bound the extraction duration.

Run the heuristic baseline with the same source, track, captions and layout. Keep a separate output directory for comparison.

```powershell
uv run --inexact chomp --input $source --engine heuristic --output outputs/baseline `
  --transcribe --asr-model models/whisper-small --layout shorts --render `
  --rights-ref $rights --acknowledge-permission

uv run --inexact chomp --input $source --start 9.71 --end 35.16 --layout shorts --render `
  --rights-ref $rights --acknowledge-permission
```

## Review and learning

Open the printed `review_page`. Each export includes a review MP4, a source-clock edit spec, a caption sidecar when applicable, and artifact hashes. `candidates.json` holds all decisions, candidate-specific context, model citations, rejection or abstention reasons, and boundary alternatives. `evolution_evidence.json` resolves evidence IDs to observations and retrieval history. Check both the exported clip and its source context before approving it.

```powershell
$jobDir = 'outputs/<job-id>'
$job = Get-Content -Raw "$jobDir/review_queue.json" | ConvertFrom-Json
Invoke-Item -LiteralPath $job.review_page
$ledger = $job.candidate_ledger
$feedback = 'outputs/feedback/reviews.json'
$candidate = $job.clips[0].candidate_id

uv run --inexact chomp evolution-feedback --ledger $ledger --candidate $candidate `
  --reviewer 'owner' --decision good --reason interest --output $feedback

uv run --inexact python -m chomp.evaluation template "$jobDir/review_queue.json" `
  --out outputs/feedback/clipledger.json
# After completing the ClipLedger review, import it.
uv run --inexact chomp evolution-feedback --ledger $ledger --queue "$jobDir/review_queue.json" `
  --clipledger outputs/feedback/clipledger.json --output $feedback

uv run --inexact chomp evolution-train --feedback $feedback --output models/interest
```

For direct feedback, `--reason` accepts `interest`, `incomplete`, `misleading`, `wrong-start`, `wrong-end`, `wrong-speaker`, `composition`, `captions`, `audio`, and `technical`. `--start-ms` and `--end-ms` store proposed corrections on the source clock. `--preferred` records a pairwise comparison between two candidates from the same ledger. Training uses explicit interest labels and preferences and holds out a source when at least three sources are available and classes remain represented. ClipLedger's composite ratings require an explicit interest label for a positive training example.

Pass `--evolution-interest-model models/interest/<model-id>.json` to select a human-trained version. Point that option to an earlier file for rollback, or omit it to return to the local score. The model changes local ranking only. No review label changes a running model. Synthetic training outputs are rejected by live inference.

## Reproduce the synthetic smoke

```powershell
& ./tests/fixtures/evolution/make-fixture.ps1
uv run --inexact chomp --input outputs/evolution-fixture/quiet-clean.mp4 --engine evolution `
  --output outputs/evolution-smoke --transcribe --asr-model models/whisper-small `
  --layout shorts --render --rights-ref 'Generated synthetic fixture' --acknowledge-permission
uv run --inexact pytest -q
```

See [engineering and validation](engineering.md) for the research mapping, test outcomes, limitations and follow-up.
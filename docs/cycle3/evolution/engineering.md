# EVOLUTION engineering and validation

## Implementation

The first slice connected existing selected-track ASR to ChompMemory, dialogue proposals, active investigation, context closure and Keystone review production. The second slice added pinned acoustic and visual perception, source-disjoint feedback training, and explicit recovery of analysis and review artifacts. The rollout is isolated behind `--engine evolution`. The code is divided at the evidence, perception, retrieval, reasoning and closure boundaries.

```mermaid
flowchart LR
    A[Source and optional sidecars] --> B[Source-clock observations]
    B --> C[Shared evidence and ChompMemory]
    C --> D[Independent proposals and rank allocation]
    D --> E[Candidate investigation]
    C --> E
    E --> F[Contiguous context closure]
    F --> G[Final verification and diversity]
    G --> H[Keystone renderer and review queue]
    H --> I[Human feedback and explicit training]
```

| Milestones | Implementation and verification |
| --- | --- |
| M01–M03 | Verify Keystone as base. Use the existing source identity and evidence envelope. Store original utterances and word timings, speaker turns, chat reactions, acoustic observations, scene/motion samples and visual embeddings. Use BM25 postings with optional cosine/RRF, temporal neighborhoods, speaker/scope filters and raw hydration. |
| M04–M07 | Build L1 utterance and timed-clause units, overlapping L2 thought proposals, L3 episodes and independent fixed windows. Use bounded RRF with lane reservations across dialogue, acoustic, visual, chat and exploration. Tested pinned YAMNet and CLIP on Windows CPU. The frame budget exposes unobserved intervals. |
| M08–M09 | Generate local evidence requests and support bounded provider requests. Record preliminary readings, evidence IDs, alternative interpretations, typed relations, and viewer versus interpretation context. Verify source references and keep unresolved states and usage. |
| M10–M12 | Score local dialogue transparently, optionally use Director verification, enforce context and source-clock gates, deduplicate overlapping story variants, and deliver zero to three review clips. Train a small local logistic ranker with up to 394 inputs, including frozen embeddings and a presence bit. |
| M13–M15 | Reuse the renderer, framing, captions, edit specs and review queue. Preserve sidecar or Whisper timings and the selected audio track. Store append-only feedback, parameterized model versions, cache and coverage diagnostics, artifact hashes, and partial export decisions. |

## Branch and research mapping

Production base is `work/cycle2-keystone` at `5d4b84f45241e14d31934aee1ff3b2960511022b` ([Keystone PR](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/13)). Branch ancestry checks found that Director First, ShotComposer, transparent cutout, Stage 1 Quality, clip boundaries, diarization, CaptionCraft, and ClipLedger were already reachable from that head. RADAR-X PR #12 head `66430342237bb2c96118e9cbb14ff865b713ebcd` was separate. It provided discovery policy and contract guidance. EVOLUTION implements rank fusion and lane reservations against Keystone's envelopes. [RADAR-X PR](https://github.com/CrypticLuminary/AI_Clipper-Chomp-Chomp-/pull/12)

The research repository's governance, pinned findings registers, and relevant source audit, transfer, protocol and synthesis notes guided transfer. These research findings have their own review status. The tests below give implementation evidence.

| Checkpoint | Transfer |
| --- | --- |
| KR01 SegmentLab F001, F010, F022 | Separate multiresolution representation from selection. Keep overlapping thought proposals and independent fixed-window coverage. [Register](https://github.com/CrypticLuminary/Chomp-Chomp-Research-Lab/blob/125a7bb81e13904d181701d6e55f198172cacea6/research/C02/K/KR01-A01-SegmentLab/findings.json) |
| KR02 MemoryLab F001, F007, F015 | Source-wide lexical and dense retrieval, occurrence IDs, direct evidence hydration and distinct viewer context. [Register](https://github.com/CrypticLuminary/Chomp-Chomp-Research-Lab/blob/88f6a5b7b170cff5cafb3a2d943505e45405ba11/research/C02/K/KR02-A02-MemoryLab/findings.json) |
| KR03 NarrativeLab F003, F013, F014 | Separate search relevance, typed relation and essential context. Check alternative sufficient groups in contiguous extraction. [Register](https://github.com/CrypticLuminary/Chomp-Chomp-Research-Lab/blob/159fad1f768ec6758d695507d751e4ac075b60c2/research/C02/K/KR03-A03-NarrativeLab/findings.json) |
| KR04 InterestLab F001, F013, F019 | Keep independent dialogue proposals and a transparent local score. Fit small heads only using explicit feedback. [Register](https://github.com/CrypticLuminary/Chomp-Chomp-Research-Lab/blob/93a21ee0fd67d25ef8ab6b06bf0375677a40799a/research/C02/K/KR04-A04-InterestLab/findings.json) |
| KR05 BoundaryLab F014, F016 | Enforce hard context gates independently of interest. Preserve evidence through technical snapping and verify the actual interval. [Register](https://github.com/CrypticLuminary/Chomp-Chomp-Research-Lab/blob/03ebcb2cc7a097d38434ec1f4022551a6f442a96/research/C02/K/KR05-A05-BoundaryLab/findings.json) |
| KR06 DeepScout F004, F009, F013, F014 | Run source-clock YAMNet and independent uniform/triggered vision. Keep availability and coverage separate during proposal allocation. [Register](https://github.com/CrypticLuminary/Chomp-Chomp-Research-Lab/blob/88859295fc475718da6b99b00dc9b1dabcf3fddc/research/C02/K/KR06-A06-DeepScout/findings.json) |
| KR07 DataForge F008, F011 | Use frozen features and an optional pairwise-capable logistic model. Keep synthetic and genuine human labels separate. [Register](https://github.com/CrypticLuminary/Chomp-Chomp-Research-Lab/blob/265641f7dc28d89ac312e5e336d249279d59122e/research/C02/K/KR07-A07-DataForge/findings.json) |
| KR08 EvalLab F002, F014, F016, F023 | Keep candidate decisions, abstentions, failures, timing and review artifacts. Compare the same footage, preserve manual review and report limited coverage. [Register](https://github.com/CrypticLuminary/Chomp-Chomp-Research-Lab/blob/30d8b63e96f28e92d31ff095a6a1ba69a754e457/research/C02/K/KR08-A08-EvalLab/findings.json) |

## Validation and failure register

Keystone baseline passed **382 tests**. The EVOLUTION suite passed **390 tests** with FFmpeg and facecam dependencies installed. EVOLUTION's tests cover repeated utterances and temporal/speaker retrieval, one-of dependency groups, a distant callback and correction, independent overlapping visual/dialogue discovery, no-interest abstention, missing optional models, fabricated provider citations, budget exhaustion, synthetic training isolation, and render failure followed by recovery and artifact-integrity checking.

The Windows laptop had a Ryzen 5 8645HS with 12 logical cores, 15.27 GiB total RAM, 5.28 GiB available RAM and 33.59 GiB free workspace storage during diagnostics. The RTX 4050 Laptop GPU reported 6,141 MiB. CTranslate2 detected one CUDA device. PyTorch was absent and ONNX Runtime reported CPU and Azure execution providers. The observed inference ran on CPU. FFmpeg and FFprobe were version 9.0.2.

| Run | Heuristic baseline | EVOLUTION |
| --- | --- | --- |
| 46-second clean quiet fixture with real Whisper inference | Four candidates, two fragments at 9.36–22.27 s and 21.27–35.83 s | Eight candidates and one 9.71–35.16 s library story. It includes setup and payoff. Sparse CLIP independently proposed the speech-free change. |
| Original, much noisier 160-second fixture | Four candidates, two previews from other dialogue | Whisper missed the quiet library story with and without bounded gain. With its actual synthetic reference sidecar, EVOLUTION recovered 10.0–37.2 s. A no-VAD probe hallucinated text. It was discarded. |
| Same authorized 120-second private excerpt, same frozen sidecar and layout | Four candidates, two previews at 68.6–86.8 s and 112.0–120.0 s | Eight candidates and one preview at 3.25–47.95 s. Source mapping adds 2,400 s to the excerpt offsets. The opening can sound mid-conversation. The recommendation remains provisional. |
| Frame-based investigation with speech disabled | Not part of this baseline | The configured Director received source-clock frames and issued a zero-clip decision on the synthetic stimulus. Sparse before/after frame placement was adjusted after an earlier pass sampled only the changed state. |

The real excerpt used the existing private editing authorization and stayed local. Frame sampling and FFmpeg decode checks covered the produced 540×960 H.264 previews and their audio streams. Sampled captions were legible. Peak audio and stream presence were checked. This technical check leaves independent playback, caption faithfulness and end-to-end A/V sync open. The automatic layout kept a full-source review view in both engines because the transparent webcam geometry was ambiguous. Transparent cutout composition on this source remains a priority for human-supervised framing. A manual exact-interval preview at 9.71–35.16 s on the synthetic source also rendered through Keystone.

The subscription Director completed two calls for the reference transcript, rendered the 10.0–37.2 s story and reported 31,428 input and 961 output tokens. The visual-only run used one call, cited frames before and after the appearance of a red rectangle, and rejected the change as a routine test stimulus. No private real media or transcript was sent to the reasoning provider.

A fresh-job retry of the clean fixture reused ASR, audio, motion, CLIP and text embedding caches and finished in approximately 4.1 seconds including export. At that warmed run the Python worker peak resident set was 145 MiB, excluding FFmpeg and provider subprocesses. A repeat with the same exact options reused the completed review after validating hashes. Measurements are machine-specific and the short fixtures cannot establish multi-hour throughput.

No suitable human training set was available. Synthetic training fit 394 inputs over six training examples in approximately 0.073 s and produced a 4,434-byte versioned artifact. Both the learned model and local rule classified the two held-out synthetic examples correctly. The synthetic artifact was blocked from real inference. The ClipLedger import was verified with a synthetic technical rejection kept separate from interest labels. Independent editorial comparison, calibrated confidence, multilingual E5, learned segmentation, temporal video encoders, local NLI and local LLM reasoning, production-quality low-SNR ASR, persistent incremental indexing as a source grows, and crash-resume at the individual rendered clip boundary remain deferred. Completed VOD runs reuse stage and provider caches and retain terminal per-clip failures. The renderer needs a source audio track, which can contain silence for a visual-only VOD.

The immediate next fix is to score standalone openings against adjacent speech before local selection and review the private A/B example blind. Evaluate noise handling and verify real visual-only selections on authorized footage with independently marked outcomes.
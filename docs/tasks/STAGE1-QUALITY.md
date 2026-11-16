# Stage 1 quality follow-up

Base: Bridge integration at 1e7e9222cd8e73909a4a2dd511b1701dee1eea7e.
Worktree: work/stage1-quality. Implementation: GPT-6.1 Sol in Codex.

The follow-up repairs the selected audio clock, proposes quiet and late moments within a per-hour budget, scores evidence inside the final clip, and retains the candidate decision ledger. An optional local model can identify and cite context for a proposed cut. Selected clips support word-timed captions, temporal camera layouts, review pages, versioned rerenders, and explicitly approved artifact hashes. Source-wide event labels separately measure proposal coverage and final cut quality.

Verified on Windows with local FFmpeg and authorized footage. The end-to-end provisional pass processed the 1,034.495-second recording using its existing transcript and rendered two review clips from 12 proposals. The original interval was rebuilt independently from a manually chosen setup and reaction, with source-specific camera regions, full-frame intervals across camera changes, local Whisper word timing, and a 1080×1920 profile. Output remains pending playback review.

The local model adapter and evidence validation are covered by fixtures. An installed editorial model and human-scored holdout recordings are needed to measure semantic-quality gains. Optional diarization still needs real checkpoint and voice tests on the target hardware. Remote ingestion and live operation belong to later project stages.

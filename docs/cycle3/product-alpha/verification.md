# Product Alpha engineering and verification

## Implementation

Base `work/lp04-streambridge` at `fd98be5ae1323d1dd90895f9594439e86df95a9d`. Alpha branch `work/lp09-product-alpha`. The first pushed checkpoint was `be60bc71065fd788d17d180110a1a8a272683cf8`.

The static website uses the loopback HTTP service. The CLI and website create the same persisted job requests and use the same supervisor. Processing runs in a dedicated Python subprocess with a bounded queue and timeout. Cancellation stops the owned process tree. The existing StreamBridge service acquires authorized remote media. ClipControl governs discovery, closure, candidate selection, exact manual re-edits, and the rendering check. The renderer verifies output codec, audio, frame size and media duration. Source bounds are exact on the original clock. Encoded bounds allow 100 ms for frame/AAC mux quantization.

Local storage uses atomic JSON snapshots for jobs, registered uploads, presets and revision references. Existing EVOLUTION analysis and provider caches remain shared across jobs. The review queue remains authoritative for original clip metadata. Feedback is append-only and keyed to a preview digest. Status contains real stage events and counts.

The JS builds untrusted metadata, transcripts and descriptions using text nodes. The service binds to IPv4 loopback, checks Host and Origin, and requires a startup token for mutations. HTTP routes resolve job artifacts beneath the configured store. Uploads are bounded to 4 GiB (16 MiB for transcripts); safe StreamBridge rules apply to HTTPS acquisition. The server is a single-operator local workspace. It does not provide multi-user authorization.

## HTTP API

GET `/api/session` returns the local request token and presets. POSTs send that token in `X-Chomp-Local`.

| Method and path | Use |
| --- | --- |
| POST `/api/source` | `{source}` for lookup, media availability and embed configuration |
| POST `/api/requirements` | Validate and resolve a ClipControl object |
| POST `/api/presets` | Save `{name, requirements}` |
| POST `/api/uploads` | Raw bytes with Content-Length and X-Filename, returns an upload ID |
| GET or POST `/api/jobs` | List or queue jobs |
| GET `/api/jobs/{id}` | Observe persisted status, errors and measured progress |
| POST `/api/jobs/{id}/cancel`, `/retry` | Cancel a job or queue another attempt |
| GET `/api/jobs/{id}/clips` | List clip metadata and latest revisions |
| GET `/api/jobs/{id}/clips/{index}/play`, `/download` | Play with byte-range support or download MP4 |
| POST `/api/jobs/{id}/clips/{index}/revise` | Queue exact source-clock bounds and optional framing regions |
| POST `/api/jobs/{id}/clips/{index}/feedback` | Append approve, reject or uncertain feedback |
| GET `/api/jobs/{id}/clips/{index}/frame`, `/api/jobs/{id}/zip` | Source frame with source dimensions, or all previews |

Recording requests include a permission reference and acknowledgement, ClipControl settings, and upload or remote acquisition details. Live requests include a separate creator feed or a trusted local CLI producer spool. Public metadata-only monitoring uses a distinct job kind. Signed source URLs are passed to the worker in memory. Their in-memory values must be supplied again after restarting the server.

## Intelligence and live behavior

ThinkLoop uses bounded first-pass, follow-up and final-verification allocations. The Controller reranks its shortlist across time blocks, deduplicates requested retrievals and hydrates actual source evidence. A reconsideration response updates the dependency groups before closure. A provider can request one edge repair using an observed evidence ID. Closure validates campaign limits, source range, speech integrity and required context before a second verification can accept it. Unexamined proposals remain unresolved. The controlled regression test proves the retrieval-to-repair sequence. A provider can abstain or exhaust its budget. The existing Director interface still makes one structured assessment per call. Cross-candidate batching is deferred.

Local closure weighs preferred length and complete openings/endings among evidence-based alternatives. Under ClipControl, an insufficient core does not gain arbitrary silence to meet the minimum. Claim faithfulness and human interest still require review.

The live controller keeps a timestamped session transcript journal independently of the video buffer. Incremental Whisper uses up to four seconds of preceding audio within the same discontinuity epoch and reconciles overlapping utterances. Pending IDs survive matching window changes. While possible, retention covers pending clips. Lost media or unmatched candidates record an explicit unresolved outcome. Rendered clips retain their source-window references. The ASR journal persists longer than rolling video. Full-session semantic indexing across distant live windows and a separate concurrent rendering worker are deferred.

## Observed acceptance results

The full Python suite passed 433 tests. The frontend JavaScript passed its syntax check.

- Ran the local website with the command in the README. In the browser, selected a generated recording and transcript, set `TARGET 5` and duration bounds of 8–13 seconds, and observed the rendering stage with a live clip count. Five distinct fixture clips rendered. Browser playback started and reported a 540×960 MP4. Downloaded an MP4 through its link and rerendered a changed interval through the trim dialog.
- The matching CLI job rendered five clips. Its CPU run reported 6.16 seconds processing time and about 35.7 MiB peak Python-worker RSS. The Studio GPU-profile run used actual `h264_nvenc` for five 540×960 previews in 5.70 seconds. The reported peak was about 36.4 MiB. The short fixture runs used different hardware profiles and report Python-worker memory only. The host reported Windows 11, Ryzen 5 8645HS, about 15.3 GiB system RAM, RTX 4050 with 6,141 MiB VRAM, and FFmpeg 9.0.2.
- The authorized 120-second private excerpt produced one 44.70-second review clip even though five were requested. The alpha and earlier EVOLUTION versions chose the same 3.25–47.95 second extraction. Sampled captions were legible. The automatic layout flagged multiple faces and retained a full-source review view. The initial opening can sound as if it starts inside the conversation. FFprobe showed both streams start at zero and last 44.70 seconds. FFmpeg decoded both. Those checks cover stream alignment. Perceptual lip sync and editorial preference remain unverified.
- A manual revision of that excerpt changed the interval to 11.37–47.95 seconds and produced a 36.58-second H.264/AAC portrait preview. The selected streamer occupied a stable top panel. The lower panel retained gameplay and the marked HUD in a sample at 14 seconds. The transparent webcam is baked into the gameplay and remained visible in both panels. The captions carry no independently verified speaker labels. Both streams decoded, with reported initial stream timestamps 16.7 ms apart.
- Through the HTTP API, retried the completed five-clip fixture, observed that retry running, and cancelled it. The retry reached the cancelled state and the parent job retained its completed clips.
- A separate 12-second Faster-Whisper probe on the revised footage produced three CPU utterances in 3.65 seconds. CTranslate2 detected the RTX GPU and then failed to load cublas64_12.dll on CUDA inference. The installed ONNX Runtime exposed CPU and Azure providers only. NVENC encoding was verified separately.
- Through the Studio CLI live worker, an eight-chunk controlled feed reached exactly 46,000 ms. It captured five reconciled transcript utterances and rendered a 25.46-second clip over 9.740–35.180 seconds. Audio and video decoded. The earlier StreamBridge chunk-local ASR replay rendered 13.5–35.5 seconds on the same fixture. This is one controlled setup-recovery example.
- Checked all three supplied active YouTube URLs in Studio with the owner's public metadata key. They resolved as live. A public metadata-only monitor completed. The supplied IShowSpeed archive resolved as completed through both its watch and share URLs. On direct YouTube, the share link played. YouTube's embedded player rejected that archive. Studio exposed the direct link and blocked a render until a creator original or authorized HTTPS file was supplied. No private recordings or key were pushed.

TTS and stimulus fixtures establish mechanics and boundary behavior. The private excerpt did not supply independent human ratings. Native Twitch calls, other streaming formats, and multi-hour throughput need further authorized validation.

## Acquisition and framing research

LiveClip's MIT repository uses a segment-list boundary and defers clipping until requested coverage exists. Its optional VOD workflow can analyze audio ahead of cutting. Streamlink's BSD-2-Clause HLS reader and yt-dlp's Unlicense fragment downloader were inspected. Neither is bundled in the alpha. Generic extractor subprocesses could change network handling or platform enforcement, and the current authorized acquisition paths already cover local, direct HTTPS and permitted Twitch assets. A future alternate adapter needs a specific permitted source plus the same DNS, redirect and media-validation checks. [LiveClip source](https://github.com/Aadi1659/liveclip/blob/64d5c0086aad100f288a551eabefcd4ecd40bf2f/liveclip.py) · [Streamlink HLS](https://github.com/streamlink/streamlink/blob/34ac0c5ff4f70b0c9b2183e899024bf8e13ec15f/src/streamlink/stream/hls/hls.py) · [yt-dlp fragment downloader](https://github.com/yt-dlp/yt-dlp/blob/da452a569fb3d04091468d920f9b9e68512e0373/yt_dlp/downloader/fragment.py)

Klypra describes scene-aware layout decisions from selected frames followed by rendering from original footage. Studio retains the existing framing and ShotComposer paths and adds local manual region selection. Automatic scene-by-scene semantic region choices and robust transparent webcam extraction remain open. [Klypra editing approach](https://klypra.app/features/editing) · [Frame handling](https://klypra.app/privacy)
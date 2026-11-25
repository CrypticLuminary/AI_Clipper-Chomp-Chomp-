# Module contracts and test inventory

Generated from actual Python definitions/imports. Shared times are integer source-relative milliseconds; captions alone use clip-local time. Optional providers and models are opt-in.

| Module | Public entry points | Internal dependencies | Tests importing module |
|---|---|---|---|
| `chomp/__init__.py` | entry point/re-exports |  | indirect / no direct import |
| `chomp/__main__.py` | entry point/re-exports | cli | indirect / no direct import |
| `chomp/artifacts.py` | json_text, digest_file, artifact_key, source_identity, staged_file, write_text, promote_new, write_json, artifact_lock |  | test_cycle2.py, test_director.py, test_director_first.py, test_review_corrections.py, test_shotcomposer.py, test_stage1_quality.py, test_subject_lock.py |
| `chomp/asr.py` | LocalTranscriber | artifacts, media, signals | test_stage1_quality.py |
| `chomp/boundaries.py` | BoundaryDecision, refine_boundaries | models, signals | test_boundaries_stage1.py, test_core.py, test_stage1_integration.py |
| `chomp/camera_evidence.py` | face_tracks, face_agreement, competing_faces, perimeter_support, enclosing_perimeter | media, subject_lock | test_cutout_framing.py |
| `chomp/camera_motion.py` | camera_path |  | test_stable_camera.py, test_subject_lock.py |
| `chomp/captions.py` | CaptionResult, build_captions, make_srt | models | test_captioncraft.py, test_core.py, test_pipeline.py, test_stage1_quality.py |
| `chomp/cli.py` | main | , clipwork, director.__main__, director.clip, director.production, editorial, manual, pipeline, reframe, shotcomposer.__main__ | test_shorts.py, test_shotcomposer.py, test_sound_cues.py, test_stage1_quality.py, test_subject_lock.py |
| `chomp/clipwork.py` | layout_segments, read_layout_plan, create_clip, review_page, approve | artifacts, captions, editorial, framing, media, models, sound | test_shorts.py, test_shotcomposer.py, test_stage1_quality.py |
| `chomp/cutout.py` | PersonSegmenter, classify_cutout | camera_evidence, media, subject_lock | test_cutout_framing.py |
| `chomp/diarization/__init__.py` | entry point/re-exports | core, identity, integration | indirect / no direct import |
| `chomp/diarization/__main__.py` | main | core, integration | test_diarization.py, test_stage1_integration.py |
| `chomp/diarization/core.py` | check_backend_available, DiarizationConfig, DiarizationResult, load_audio, convert_to_wav, diarize_audio | media, models | test_diarization.py, test_diarization_export.py, test_stage1_quality.py |
| `chomp/diarization/identity.py` | SpeakerIdentityMap, build_identity_map | artifacts | test_diarization.py, test_diarization_export.py, test_stage1_quality.py |
| `chomp/diarization/integration.py` | diarize_and_export, merge_diarization_with_transcript, generate_pipeline_command | artifacts, core, identity, media, models, speakers | test_diarization.py, test_diarization_export.py, test_stage1_integration.py |
| `chomp/director/__init__.py` | entry point/re-exports |  | indirect / no direct import |
| `chomp/director/__main__.py` | main | contract, evidence, experiment, provider, shotcomposer.__main__ | indirect / no direct import |
| `chomp/director/campaign.py` | milliseconds, Campaign, resolve_campaign | contract | test_cycle2.py, test_director_first.py, test_review_corrections.py |
| `chomp/director/clip.py` | signal_candidates, merge_signals, baseline_decisions, review_html, run_clip, main | artifacts, asr, boundaries, campaign, discovery, media, models, pipeline, production, provider, ranking, selection, semantic, signals | test_cycle2.py, test_director_first.py, test_review_corrections.py |
| `chomp/director/contract.py` | schema, read_json, check_schema, bundle_digest, validate_bundle_shape, validate_plan | artifacts, models | test_director.py, test_shotcomposer.py |
| `chomp/director/evidence.py` | extract_frame, build_bundle, read_verified_bundle | artifacts, contract, media, models, signals | test_director.py, test_shotcomposer.py |
| `chomp/director/experiment.py` | compare | artifacts, contract, evidence, provider | test_director.py |
| `chomp/director/production.py` | verify_selection, export_selection, main | artifacts, campaign, captions, clipwork, editorial, editorial_selection, manual, media, selection, semantic, signals | test_cycle2.py, test_review_corrections.py |
| `chomp/director/provider.py` | decision_schema, evidence_view, Response, CodexCLI | artifacts, contract | test_cycle2.py, test_director.py, test_director_first.py, test_review_corrections.py |
| `chomp/director/selection.py` | object_schema, array, TranscriptIndex, validate_refs, has_text | models | test_cycle2.py, test_director_first.py |
| `chomp/director/semantic.py` | SelectionProvider, DirectorFailure, BudgetPolicy, EditorialCalls, scan_dialogue, investigate, enforce | artifacts, campaign, contract, models, provider, ranking, selection | test_cycle2.py, test_director_first.py, test_review_corrections.py |
| `chomp/discovery.py` | discover | models, signals | test_core.py, test_stage1_quality.py |
| `chomp/editorial.py` | EditorialProvider, OllamaProvider, context_for, validate_judgment, validate_title, quote_title, judge_cached, story_boundary | artifacts, boundaries, models, ranking | test_cycle2.py, test_shorts.py |
| `chomp/editorial_selection.py` | text, span, adapt_director_report, read_selection | models | test_cycle2.py |
| `chomp/evaluation/__init__.py` | entry point/re-exports | core | indirect / no direct import |
| `chomp/evaluation/__main__.py` | main | artifacts, benchmark, core, experiments | indirect / no direct import |
| `chomp/evaluation/benchmark.py` | create_event_template, validate_events, validate_dataset, benchmark, markdown_report | artifacts, core, editorial_selection, models | test_cycle2.py, test_review_corrections.py, test_stage1_quality.py |
| `chomp/evaluation/core.py` | ClipLedgerError, Clip, Manifest, read_manifest, make_template, read_annotations, evaluate, compare, write_json | artifacts | test_cycle2.py, test_review_corrections.py, test_stage1_quality.py |
| `chomp/evaluation/demo.py` | generate, main | core | test_evaluation.py |
| `chomp/evaluation/experiments.py` | compare_experiments | artifacts, benchmark, core, editorial_selection | indirect / no direct import |
| `chomp/evaluation/layout_compare.py` | layout_metrics, compare, main | artifacts, clipwork, director.evidence, media | indirect / no direct import |
| `chomp/evaluation/review_packet.py` | render_page, build_packet, main | artifacts, core, director.production, media, signals, technical | test_private_review.py |
| `chomp/evaluation/smoke.py` | SyntheticDirector, SyntheticJudge, make_fixture, event_annotations, run_smoke, main | artifacts, benchmark, director.campaign, director.clip, director.production, director.provider, experiments, media, pipeline, technical | test_cycle2.py |
| `chomp/evaluation/subject_compare.py` | evaluate, compare, main | artifacts, clipwork, director.evidence, layout_compare, subject_lock, subject_view | test_subject_lock.py |
| `chomp/evaluation/technical.py` | inspect_export | media | test_cycle2.py |
| `chomp/framing.py` | FramingPlan, facecam_fingerprint, plan_framing | media, reframe | test_auto_facecam.py |
| `chomp/manual.py` | render_manual_preview, rerender | artifacts, asr, clipwork, framing, media, models, reframe, signals, sound, subject_lock | test_auto_facecam.py, test_cycle2_vfr.py, test_pipeline.py, test_shorts.py, test_sound_cues.py, test_stable_camera.py, test_stage1_quality.py, test_subject_lock.py |
| `chomp/media.py` | LayoutSegment, crop_position, exec_checked, probe, check_audio_track, rms_dbfs, decode_audio_window, scan_audio, exported_audio_level, exported_loudness, resolve_layout, validate_roi, render_preview | artifacts, models, shotcomposer.compiler, shotcomposer.contract, shotcomposer.workflow, sound | test_auto_facecam.py, test_captioncraft.py, test_cutout_framing.py, test_cycle2_vfr.py, test_pipeline.py, test_shorts.py, test_shotcomposer.py, test_stage1_integration.py, test_stage1_quality.py, test_subject_lock.py |
| `chomp/models.py` | checked_span, TimedWord, TranscriptSegment, SpeakerTurn, ChatMessage, Observation, Candidate |  | test_boundaries_stage1.py, test_captioncraft.py, test_core.py, test_diarization.py, test_diarization_export.py, test_director_first.py, test_pipeline.py, test_stage1_integration.py, test_stage1_quality.py |
| `chomp/pipeline.py` | transcribe_media, process | artifacts, asr, boundaries, clipwork, discovery, editorial, framing, media, ranking, reframe, signals, speakers | test_auto_facecam.py, test_pipeline.py, test_stage1_integration.py, test_stage1_quality.py |
| `chomp/ranking.py` | quality, heuristic_rank, overlap_ratio, duplicate, select | models | test_core.py, test_stage1_integration.py |
| `chomp/reframe.py` | StreamerAnchor, vision_fingerprint, cached_shorts_plan, Camera, Sample, camera_container, ForegroundSaliency, oriented_size, plan_shorts | artifacts, camera_evidence, camera_motion, cutout, framing, media, subject_lock, subject_view | test_cutout_framing.py, test_shorts.py, test_stable_camera.py |
| `chomp/research_interfaces.py` | validate_evidence, evidence_item, SemanticAudioProvider, semantic_audio, RetrievalBudget, sample_times, retrieve_frames, collect_candidates | artifacts, director.evidence, editorial_selection, media | test_cycle2.py, test_review_corrections.py |
| `chomp/shotcomposer/__init__.py` | entry point/re-exports |  | indirect / no direct import |
| `chomp/shotcomposer/__main__.py` | main | adapters, artifacts, capabilities, compiler, contract, director.contract, director.evidence, workflow | indirect / no direct import |
| `chomp/shotcomposer/adapters.py` | from_editorial, subject_summary, legacy_envelope, attach_tracking | artifacts, clipwork, contract, director.contract, subject_lock, workflow | test_shotcomposer.py |
| `chomp/shotcomposer/capabilities.py` | manifest | , director.contract | indirect / no direct import |
| `chomp/shotcomposer/compiler.py` | CompiledShots, compile_plan | contract, media | test_shotcomposer.py |
| `chomp/shotcomposer/contract.py` | fraction, frame_index, intersects, contains, geometry, project, pointer_blocks, Feasibility, validate | , artifacts, capabilities, director.contract, media | test_shotcomposer.py |
| `chomp/shotcomposer/workflow.py` | source_metadata, verify_source, slot, base_plan, prepare, render, revise | artifacts, capabilities, clipwork, contract, director.contract, director.evidence, director.provider, media | test_shotcomposer.py |
| `chomp/signals.py` | read_transcript, read_chat, audio_observations, transcript_observations, chat_observations, nearby_text, contained_speech, final_evidence | models | test_core.py, test_stage1_quality.py |
| `chomp/sound.py` | SoundCue, read_sound_cues, cues_in_range | artifacts, media, models | indirect / no direct import |
| `chomp/speakers.py` | read_speaker_turns, attribute_segments, speaker_inventory, candidate_speaker_context | models, signals | test_core.py, test_diarization.py, test_stage1_integration.py, test_stage1_quality.py |
| `chomp/subject_lock.py` | Seed, ProtectedRegion, SubjectPlan, SourceSpan, Observation, read_subject_plan, overlap_fraction, follow_subject, track_source | media | test_subject_lock.py |
| `chomp/subject_view.py` | observation_at, subject_windows, crop_at, subject_review | camera_motion, media, subject_lock | test_subject_lock.py |

Workflow contracts: pipeline.process accepts a local source, rights/config and optional timestamped sidecars; produces candidates.json plus review_queue.json. director.clip.run_clip accepts the same source/campaign with a structured provider; produces director-selection/1. manual.render_manual_preview accepts exact source bounds and verified geometry; produces 0.5 review/edit artifacts. ShotComposer consumes source-hashed shot evidence and validated plans. ClipLedger evaluates independent reviews; benchmark evaluates source-event annotations.

Duplication: manual and production share create_clip/render_preview; Director has separate proposal/decision logic by design. The Cycle 2 adapter reuses dictionaries and Candidate/TranscriptIndex/enforce rather than replacing all editorial contracts. Layout and SubjectLock evaluators remain the authority for geometry-specific comparisons.

| Major functionality / modules | Input contract | Output contract |
|---|---|---|
| Ingestion/media | local authorized file, selected audio index, source-ms interval | duration_ms/streams; decoded PCM; validated H.264/AAC portrait export |
| Artifacts | source bytes, configuration, local paths | content hashes, locks, atomic versioned JSON/text |
| Signals/discovery | source-clock transcript/chat/audio bins; modality availability | Observation rows and Candidate proposals with source bounds/anchors |
| Boundaries/ranking/editorial | Candidate, source transcript, optional structured provider | BoundaryDecision and ranked/selected/reject/abstain reasons; max three |
| ASR/captions/speakers/diarization | selected source track, optional cached models/turn sidecar | timestamped TranscriptSegment/TimedWord/SpeakerTurn; clip-local SRT; identity templates requiring review |
| Framing/reframe/camera_evidence/camera_motion | source interval, decoded detections, reviewed anchor/ROI, optional models | FramingPlan/LayoutSegment, source-clock keyframes, conservative uncertainty fallback |
| Cutout/subject_lock/subject_view | local frames, optional segmentation, seeded identity/regions | tracked source regions, protected-region evidence and fit/visibility diagnostics |
| Sound/clipwork/manual | source interval/track, bounded local cue assets, validated layouts/captions | deterministic edit spec, export hashes, technical warnings, pending review/explicit approval artifact |
| Director contracts/evidence/provider | bounded untrusted evidence, pinned provider/schema and campaign | verified evidence bundles; structured decision/shot plans, or explicit failure/abstention |
| Director selection/production | candidate IDs, cited transcript snapshot and source hash | director-selection/1 lowered to existing renderer and 0.5 review queue |
| ShotComposer | source-hashed evidence, validated shot geometry/assets/timestamps | compiled deterministic render graph, timing/geometry diagnostics and review exports |
| Evaluation | source events, native/Director proposals, independent ClipLedger/region labels | denominators/null-aware JSON/Markdown; layout/subject comparisons |
| Research interfaces | optional provider, source identity/track, bounded requests and lane proposals | explicit unavailable states, stable evidence IDs, decoded PTS/hashes and unmerged lane provenance |

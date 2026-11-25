# Module contracts and test inventory

Generated from actual Python definitions/imports. Shared times are integer source-relative milliseconds; captions alone use clip-local time. Optional providers and models are opt-in.

| Module | Public entry points | Internal dependencies | Tests importing module |
|---|---|---|---|
| `chomp/__init__.py` | entry point/re-exports |  | indirect / no direct import |
| `chomp/__main__.py` | entry point/re-exports | cli | indirect / no direct import |
| `chomp/artifacts.py` | json_text, digest_file, artifact_key, source_identity, staged_file, write_text, promote_new, write_json, artifact_lock |  | test_director.py, test_director_first.py, test_shotcomposer.py, test_stage1_quality.py, test_subject_lock.py |
| `chomp/asr.py` | LocalTranscriber | artifacts, media, signals | test_stage1_quality.py |
| `chomp/boundaries.py` | BoundaryDecision, refine_boundaries | models, signals | test_boundaries_stage1.py, test_core.py, test_stage1_integration.py |
| `chomp/camera_evidence.py` | face_tracks, face_agreement, competing_faces, perimeter_support, enclosing_perimeter | media, subject_lock | test_cutout_framing.py |
| `chomp/camera_motion.py` | camera_path |  | test_stable_camera.py, test_subject_lock.py |
| `chomp/captions.py` | CaptionResult, build_captions, make_srt | models | test_captioncraft.py, test_core.py, test_pipeline.py, test_stage1_quality.py |
| `chomp/cli.py` | main | , clipwork, director.__main__, director.clip, editorial, manual, pipeline, reframe, shotcomposer.__main__ | test_shorts.py, test_shotcomposer.py, test_sound_cues.py, test_stage1_quality.py, test_subject_lock.py |
| `chomp/clipwork.py` | layout_segments, read_layout_plan, create_clip, review_page, approve | artifacts, captions, editorial, framing, media, models, sound | test_shorts.py, test_shotcomposer.py, test_stage1_quality.py |
| `chomp/cutout.py` | PersonSegmenter, classify_cutout | camera_evidence, media, subject_lock | test_cutout_framing.py |
| `chomp/diarization/__init__.py` | entry point/re-exports | core, identity, integration | indirect / no direct import |
| `chomp/diarization/__main__.py` | main | core, integration | test_diarization.py, test_stage1_integration.py |
| `chomp/diarization/core.py` | check_backend_available, DiarizationConfig, DiarizationResult, load_audio, convert_to_wav, diarize_audio | media, models | test_diarization.py, test_diarization_export.py, test_stage1_quality.py |
| `chomp/diarization/identity.py` | SpeakerIdentityMap, build_identity_map | artifacts | test_diarization.py, test_diarization_export.py, test_stage1_quality.py |
| `chomp/diarization/integration.py` | diarize_and_export, merge_diarization_with_transcript, generate_pipeline_command | artifacts, core, identity, media, models, speakers | test_diarization.py, test_diarization_export.py, test_stage1_integration.py |
| `chomp/director/__init__.py` | entry point/re-exports |  | indirect / no direct import |
| `chomp/director/__main__.py` | main | contract, evidence, experiment, provider, shotcomposer.__main__ | indirect / no direct import |
| `chomp/director/campaign.py` | milliseconds, Campaign, resolve_campaign | contract | test_director_first.py |
| `chomp/director/clip.py` | signal_candidates, merge_signals, baseline_decisions, review_html, run_clip, main | artifacts, asr, boundaries, campaign, discovery, media, models, pipeline, provider, ranking, selection, semantic, signals | test_director_first.py |
| `chomp/director/contract.py` | schema, read_json, check_schema, bundle_digest, validate_bundle_shape, validate_plan | artifacts, models | test_director.py, test_shotcomposer.py |
| `chomp/director/evidence.py` | extract_frame, build_bundle, read_verified_bundle | artifacts, contract, media, models, signals | test_director.py, test_shotcomposer.py |
| `chomp/director/experiment.py` | compare | artifacts, contract, evidence, provider | test_director.py |
| `chomp/director/provider.py` | decision_schema, evidence_view, Response, CodexCLI | artifacts, contract | test_director.py, test_director_first.py |
| `chomp/director/selection.py` | object_schema, array, TranscriptIndex, validate_refs, has_text | models | test_director_first.py |
| `chomp/director/semantic.py` | SelectionProvider, DirectorFailure, EditorialCalls, scan_dialogue, investigate, enforce | artifacts, campaign, contract, models, provider, ranking, selection | test_director_first.py |
| `chomp/discovery.py` | discover | models, signals | test_core.py, test_stage1_quality.py |
| `chomp/editorial.py` | EditorialProvider, OllamaProvider, context_for, validate_judgment, validate_title, quote_title, judge_cached, story_boundary | artifacts, boundaries, models, ranking | test_shorts.py |
| `chomp/evaluation/__init__.py` | entry point/re-exports | core | indirect / no direct import |
| `chomp/evaluation/__main__.py` | main | benchmark, core | indirect / no direct import |
| `chomp/evaluation/benchmark.py` | create_event_template, benchmark | artifacts, core, models | test_stage1_quality.py |
| `chomp/evaluation/core.py` | ClipLedgerError, Clip, Manifest, read_manifest, make_template, read_annotations, evaluate, compare, write_json | artifacts | test_stage1_quality.py |
| `chomp/evaluation/demo.py` | generate, main | core | test_evaluation.py |
| `chomp/evaluation/layout_compare.py` | layout_metrics, compare, main | artifacts, clipwork, director.evidence, media | indirect / no direct import |
| `chomp/evaluation/subject_compare.py` | evaluate, compare, main | artifacts, clipwork, director.evidence, layout_compare, subject_lock, subject_view | test_subject_lock.py |
| `chomp/framing.py` | FramingPlan, facecam_fingerprint, plan_framing | media, reframe | test_auto_facecam.py |
| `chomp/manual.py` | render_manual_preview, rerender | artifacts, asr, clipwork, framing, media, models, reframe, signals, sound, subject_lock | test_auto_facecam.py, test_pipeline.py, test_shorts.py, test_sound_cues.py, test_stable_camera.py, test_stage1_quality.py, test_subject_lock.py |
| `chomp/media.py` | LayoutSegment, crop_position, exec_checked, probe, check_audio_track, rms_dbfs, decode_audio_window, scan_audio, exported_audio_level, exported_loudness, resolve_layout, validate_roi, render_preview | artifacts, models, shotcomposer.compiler, shotcomposer.contract, shotcomposer.workflow, sound | test_auto_facecam.py, test_captioncraft.py, test_cutout_framing.py, test_pipeline.py, test_shorts.py, test_shotcomposer.py, test_stage1_integration.py, test_stage1_quality.py, test_subject_lock.py |
| `chomp/models.py` | checked_span, TimedWord, TranscriptSegment, SpeakerTurn, ChatMessage, Observation, Candidate |  | test_boundaries_stage1.py, test_captioncraft.py, test_core.py, test_diarization.py, test_diarization_export.py, test_director_first.py, test_pipeline.py, test_stage1_integration.py, test_stage1_quality.py |
| `chomp/pipeline.py` | transcribe_media, process | artifacts, asr, boundaries, clipwork, discovery, editorial, framing, media, ranking, reframe, signals, speakers | test_auto_facecam.py, test_pipeline.py, test_stage1_integration.py, test_stage1_quality.py |
| `chomp/ranking.py` | quality, heuristic_rank, overlap_ratio, duplicate, select | models | test_core.py, test_stage1_integration.py |
| `chomp/reframe.py` | StreamerAnchor, vision_fingerprint, cached_shorts_plan, Camera, Sample, camera_container, ForegroundSaliency, oriented_size, plan_shorts | artifacts, camera_evidence, camera_motion, cutout, framing, media, subject_lock, subject_view | test_cutout_framing.py, test_shorts.py, test_stable_camera.py |
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

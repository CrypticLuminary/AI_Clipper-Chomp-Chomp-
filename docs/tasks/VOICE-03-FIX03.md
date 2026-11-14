# Task Report: VOICE-03-FIX03 — TurnsGuard (Export Protection)

**Agent code:** GFLASH38-AG  
**Task code:** VOICE-03-FIX03  
**Codename:** TurnsGuard  
**Model:** Gemini 3.8 Flash  
**Environment:** Antigravity  
**Date:** 2026-11-14  
**Base branch:** `fix/voice03-fix02-gflash38-ag-identityguard`  
**Working branch:** `fix/voice03-fix03-gflash38-ag-turnsguard`  

---

## 1. Problem Description

Following the implementation of `VOICE-03-FIX02` (IdentityGuard), the diarization export process safely prevented silent overwrites of `speaker_identity_map.json`. However, a defect remained:
- Preflight validation only verified whether `speaker_identity_map.json` existed in the output directory.
- If `speaker_turns.json` already existed (for example, from a prior diarization run where no identity map was generated, or where the identity map was stored elsewhere or deleted), `diarize_and_export()` would proceed with expensive audio extraction and diarization inference.
- Subsequently, the export step used `turns_path.write_text(...)`, silently overwriting existing speaker-turns data.
- Furthermore, if a `speaker_turns.json` file appeared after the preflight check (due to concurrent execution or race conditions), `write_text` would truncate and overwrite it.

---

## 2. Implementation Overview

We implemented TurnsGuard to guarantee complete export safety for both output files:

1. **Dual Preflight Fail-Closed Check:**
   In `diarize_and_export()` (`chomp/diarization/integration.py`), before any expensive FFmpeg conversion or model inference, we inspect both expected output paths:
   - `output_dir / "speaker_identity_map.json"`
   - `output_dir / "speaker_turns.json"`
   If either or both files exist, export immediately aborts with an actionable `FileExistsError` naming the conflicting file(s) and instructing the user to choose a different output directory.

2. **Atomic Exclusive File Creation for Speaker Turns:**
   Replaced `turns_path.write_text(...)` with exclusive creation using Python's mode `"x"`:
   ```python
   turns_content = json.dumps(result.to_json_dict(), indent=2, ensure_ascii=False)
   try:
       with open(turns_path, "x", encoding="utf-8") as f:
           f.write(turns_content)
   except FileExistsError as exc:
       raise FileExistsError(
           f"Speaker turns file already exists at '{turns_path}'. "
           "Cannot overwrite existing speaker turns data. "
           "Please choose a different output directory for new diarization runs."
       ) from exc
   ```
   On Windows, mode `"x"` maps to OS-level `CREATE_NEW` via `CreateFileW` (and `O_CREAT | O_EXCL` on POSIX). If `speaker_turns.json` was created concurrently after the preflight check, the kernel rejects the write atomically without touching or truncating the existing data.

3. **Preserved Output Formats & Signatures:**
   All function signatures, return values (`tuple[DiarizationResult, Path, Path]`), JSON schemas, and command generation interfaces remain identical.

---

## 3. Files Changed (Strict Ownership)

- `chomp/diarization/integration.py`: Extended preflight check to cover both `speaker_identity_map.json` and `speaker_turns.json`; replaced `write_text` on turns with atomic exclusive file creation (`open(..., "x")`).
- `tests/test_diarization_export.py`: Added 3 new unit and integration tests covering turns-only collisions, dual-file collisions, and late race condition writes.
- `docs/tasks/VOICE-03-FIX03.md`: Task documentation, design justification, and verification manifest.

*(No modifications were made to `identity.py`, `core.py`, `__main__.py`, or root repository files).*

---

## 4. Test Suite & Verification Results

### 4.1 New Tests Added in `tests/test_diarization_export.py`

- `test_existing_turns_with_no_identity_map_rejected`: Verifies preflight rejection when only `speaker_turns.json` exists; confirms zero FFmpeg conversion or inference calls, byte-for-byte preservation of existing turns, and clear conflict error.
- `test_existing_turns_and_existing_identity_map_rejected`: Verifies preflight rejection when both `speaker_turns.json` and `speaker_identity_map.json` exist; confirms zero FFmpeg/inference calls and byte-for-byte preservation of both files.
- `test_turns_file_created_between_preflight_and_turns_write_cannot_be_overwritten`: Simulates a race condition where `speaker_turns.json` appears after preflight validation during inference; verifies exclusive creation blocks overwrite, preserves the existing file byte-for-byte, and raises `FileExistsError`.

### 4.2 Verification Execution

All required commands were executed:

```powershell
py -m pytest -q tests/test_diarization_export.py
py -m pytest -q -ra
py -m compileall -q chomp tests
py -m pip check
```

**Results:**
- `tests/test_diarization_export.py`: **10 passed** in 0.16s
- Full test suite: **100 passed** in 4.17s
- Bytecode compilation (`compileall`): Clean, zero errors
- Dependency check (`pip check`): "No broken requirements found."

---

## 5. Limitations & Technical Decisions

1. **Non-Atomic Two-File Export (Partial-Export Limitation):**
   `diarize_and_export()` exports two independent files: `speaker_identity_map.json` and `speaker_turns.json`. If a concurrent process creates `speaker_turns.json` after `speaker_identity_map.json` has already been written, the turns write will fail closed with `FileExistsError`. The existing turns file is preserved byte-for-byte, but the newly written identity map template remains on disk. True two-file atomicity would require a transactional directory staging or locking framework, which was explicitly excluded to keep the implementation lightweight and dependency-free.
2. **Concurrent Temporary WAV Collision:**
   Full concurrent-export safety targeting the exact same output directory is not guaranteed because both runs share `output_dir / "diarization_input.wav"`. Multi-process concurrency should always target isolated working/output directories.

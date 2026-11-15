# Task Report: VOICE-03-FIX02 — IdentityGuard (Prevent Speaker Identity Overwrite)

**Agent code:** GFLASH38-AG  
**Task code:** VOICE-03-FIX02  
**Codename:** IdentityGuard  
**Environment:** Antigravity / Gemini 3.8 Flash  
**Date:** 2026-11-14  
**Base branch:** `feat/stage1-speaker-diarization`  
**Working branch:** `fix/voice03-fix02-gflash38-ag-identityguard`  

---

## 1. Problem Description

In `chomp/diarization/integration.py` and `chomp/diarization/identity.py`:
- `diarize_and_export()` exported `speaker_turns.json` and generated `speaker_identity_map.json`.
- `SpeakerIdentityMap.save()` previously called `path.write_text()`, which unconditionally overwrote any existing file.
- If a human reviewer had previously inspected a VOD's diarization output, mapped anonymous IDs to streamer/guest names, and saved the confirmed mapping (`confirmed: true`), a subsequent run of `diarize_and_export()` in the same directory would silently overwrite the human's confirmed identity mappings with a generic template.
- Additionally, if an export failed or was rejected late, existing speaker turns could be clobbered.

---

## 2. Implementation Overview

We implemented a fail-closed protection design:

1. **Early Fail-Closed Detection:**
   At the start of `diarize_and_export()`, before any audio conversion or expensive diarization is invoked, `output_dir / "speaker_identity_map.json"` is checked. If it exists, a `FileExistsError` is raised immediately. No audio processing occurs and existing files are never touched.

2. **Exclusive File Creation:**
   In `SpeakerIdentityMap.save()`, writing now uses exclusive file creation via `open(path, "x", encoding="utf-8")`.
   - On Windows, this maps to OS-level `CREATE_NEW` in `CreateFileW`.
   - On POSIX, this maps to `O_CREAT | O_EXCL`.
   - If the file exists, the OS denies the creation and raises `FileExistsError` without modifying or truncating the target.
   - This provides atomic, race-free protection against concurrent processes attempting to overwrite the identity map.

3. **Protection of Speaker Turns:**
   In `diarize_and_export()`, `identity_map.save(identity_path)` is called **before** `turns_path` is written. If the identity map write is rejected (or fails due to concurrent creation), `speaker_turns.json` is never overwritten.

4. **Preserved Contracts & Signatures:**
   All public function signatures (`save(self, path: Path) -> None`, `diarize_and_export(...)`), return types, and JSON schemas remain strictly identical. No overwrite flags, databases, or external services were introduced.

---

## 3. Files Changed

- `chomp/diarization/identity.py`: Implemented exclusive file creation in `SpeakerIdentityMap.save()` using mode `"x"`, raising `FileExistsError` with actionable instructions on collisions.
- `chomp/diarization/integration.py`: Added early fail-closed check before audio extraction in `diarize_and_export()`, and ordered identity map saving prior to turns export.
- `tests/test_diarization_export.py`: Added comprehensive unit and integration tests for export safety and concurrency protection.
- `docs/tasks/VOICE-03-FIX02.md`: Task documentation and verification report.

---

## 4. Test Suite & Verification Results

### 4.1 New Tests in `tests/test_diarization_export.py`

- `test_new_output_directory_works`: Verifies normal export into an empty output directory creates both valid `speaker_turns.json` and `speaker_identity_map.json`.
- `test_existing_confirmed_identity_remains_byte_for_byte_unchanged`: Verifies an existing confirmed identity map is preserved byte-for-byte and no audio processing is performed.
- `test_rejected_rerun_does_not_overwrite_existing_speaker_turns`: Verifies existing `speaker_turns.json` is protected and preserved byte-for-byte when export is rejected.
- `test_error_clearly_identifies_file_conflict`: Verifies the `FileExistsError` identifies the specific path conflict and advises selecting another output directory.
- `test_different_output_directory_allows_another_run`: Verifies specifying a new output directory succeeds without conflict.
- `test_concurrent_creation_cannot_silently_replace_existing_identity_file`: Verifies 10 concurrent threads racing to write the same identity file results in exactly 1 winner and 9 `FileExistsError` rejections, with zero file corruption.
- `test_save_method_raises_file_exists_error_on_collision`: Direct unit test of `SpeakerIdentityMap.save()` on an existing file.

### 4.2 Test Execution Results

```powershell
py -m pytest -ra
```

```text
============================= test session starts =============================
platform win32 -- Python 3.14.6, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\Prajwal Sitoula\.gemini\antigravity\scratch\chomp-chomp
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.1
collected 97 items

tests\test_core.py ..............                                        [ 14%]
tests\test_diarization.py .............................................. [ 61%]
.....................                                                    [ 83%]
tests\test_diarization_export.py .......                                 [ 90%]
tests\test_pipeline.py .........                                         [100%]

======================== 97 passed, 1 warning in 4.34s ========================
```

### 4.3 Syntax and Dependency Health

```powershell
py -m compileall -q chomp tests
py -m pip check
```
```text
No broken requirements found.
```

---

## 5. Limitations

- **Directory Isolation:** The design is strictly fail-closed per output directory. To generate a fresh diarization for the same media file without altering the previous human confirmation, users must supply a distinct `--output` directory (e.g. `outputs/diarization_v2`).
- **Manual Overwrite:** If a user intentionally wants to discard a confirmed mapping, they must manually delete or move the previous `speaker_identity_map.json` file outside the application.

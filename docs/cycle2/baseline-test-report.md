# Baseline execution

- Date: 2026-11-25, Asia/Katmandu.
- Unmodified commit: `24bf2f5f4ad2172e4b18728b198fe7d3e9d65aff`.
- Environment: Windows, CPython 3.13.14, FFmpeg/FFprobe 9.0.2 full Gyan build; libass `ass` and `subtitles` filters available.
- Ryzen 5 8645HS, 6 cores / 12 logical processors; 16,006,572 KiB visible RAM, 4,231,280 KiB free at inspection. NVIDIA RTX 4050 Laptop, 6141 MiB VRAM, driver 610.91; availability does not establish inference compatibility.
- Free storage at inspection: C 70,605,336,576 bytes; D 36,975,210,496 bytes.
- Existing bundled YuNet weights available. No new model weights downloaded. Local VOD paths in historical documentation do not establish current authorization for a new evaluation; Cycle 2 uses synthetic media initially.
- PATH `python` resolves to the Windows Store alias; use `.venv/Scripts/python.exe` or `uv run`.

Commands, from the new worktree:

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -e '.[dev,director,facecam]'
.venv/Scripts/python.exe -m pytest -q -ra
```

Result: **323 passed in 51.03 seconds; 0 failed; 0 skipped**. No baseline failure was hidden or reclassified. Tests exercise synthetic media, provider fakes and optional-dependency states; they do not prove entertainment quality or human approval.

Installed: attrs 26.1.0, jsonschema 4.26.0, jsonschema-specifications 2025.9.1, numpy 2.5.5, opencv-python-headless 4.14.0.94, packaging 26.4, pytest 9.2.0, referencing 0.37.0, rpds-py 2026.9.1, pluggy 1.7.0, iniconfig 2.3.1, colorama 0.4.6, pygments 2.21.0; editable application 0.6.0. ASR/diarization inference frameworks are not required for these offline tests.

Remote CI: baseline query returned no PR-triggered runs. Existing historical PR descriptions report earlier cross-platform runs; they are not substituted for this revision's verification. Linux and other Python versions require the existing CI matrix.

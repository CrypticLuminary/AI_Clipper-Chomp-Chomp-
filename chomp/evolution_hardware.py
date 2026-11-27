"""Best-effort hardware and installed-runtime inventory."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess


def _run(args):
    if not shutil.which(args[0]):
        return None
    try:
        result = subprocess.run(args, timeout=12, capture_output=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def hardware():
    available_ram = total_ram = None
    gpu = []
    cpu_model = platform.processor() or None
    if platform.system() == "Windows":
        script = ("$o=Get-CimInstance Win32_OperatingSystem; "
                  "@{available_kib=$o.FreePhysicalMemory; total_kib=$o.TotalVisibleMemorySize; "
                  "cpu=@(Get-CimInstance Win32_Processor | ForEach-Object Name); "
                  "gpu=@(Get-CimInstance Win32_VideoController | ForEach-Object Name)} | ConvertTo-Json -Compress")
        raw = _run(["powershell.exe", "-NoProfile", "-Command", script])
        if raw:
            try:
                data = json.loads(raw)
                available_ram = int(data["available_kib"]) * 1024
                total_ram = int(data["total_kib"]) * 1024
                gpu = [{"name": name, "memory_mib": None} for name in data["gpu"] if name]
                cpu_model = ", ".join(data["cpu"]) or cpu_model
            except (ValueError, KeyError, TypeError):
                pass
    else:
        try:
            available_ram = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            total_ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError, AttributeError):
            pass
    raw = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if raw:
        for line in raw.splitlines():
            name, _, memory = line.rpartition(",")
            if memory.strip().isdigit():
                matching = next((row for row in gpu if row["name"] == name.strip()), None)
                if matching is None:
                    gpu.append({"name": name.strip(), "memory_mib": int(memory)})
                else:
                    matching["memory_mib"] = int(memory)
    torch_backend = {"installed": False}
    try:
        import torch
        torch_backend = {"installed": True, "version": str(torch.__version__), "cuda_build": torch.version.cuda,
                         "cuda_available": torch.cuda.is_available(),
                         "mps_available": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())}
    except (ImportError, OSError, RuntimeError):
        pass
    providers = []
    try:
        import onnxruntime
        providers = onnxruntime.get_available_providers()
    except (ImportError, OSError, RuntimeError):
        pass
    cuda_devices = None
    try:
        import ctranslate2
        cuda_devices = ctranslate2.get_cuda_device_count()
    except (ImportError, OSError, RuntimeError):
        pass
    versions = {name: (_run([name, "-version"]) or "").splitlines()[:1] for name in ("ffmpeg", "ffprobe")}
    profile = "balanced" if (available_ram or 0) >= 6 * 1024**3 and (os.cpu_count() or 0) >= 8 else "eco"
    if profile == "balanced" and (torch_backend.get("cuda_available") or "CUDAExecutionProvider" in providers) and (
            max((g["memory_mib"] or 0 for g in gpu), default=0) >= 6144):
        profile = "gpu"
    return {"os": platform.platform(), "python": platform.python_version(), "architecture": platform.machine(),
            "cpu": cpu_model, "logical_cores": os.cpu_count(), "available_ram_bytes": available_ram,
            "total_ram_bytes": total_ram, "available_workspace_disk_bytes": shutil.disk_usage(Path.cwd()).free,
            "gpu": gpu, "nvidia_driver_detected": bool(raw), "ctranslate2_cuda_devices": cuda_devices,
            "pytorch": torch_backend, "onnx_execution_providers": providers, **versions,
            "recommended_profile": profile}

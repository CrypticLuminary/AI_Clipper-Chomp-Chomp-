"""Versioned local artifacts and crash-safe writes."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import file_digest, sha256
from pathlib import Path
import json
import os
import tempfile
import time


def json_text(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return file_digest(handle, "sha256").hexdigest()


def artifact_key(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def source_identity(path: Path) -> str:
    before = path.stat()
    digest = digest_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Source changed while it was being read")
    return "sha256:" + digest


@contextmanager
def staged_file(destination: Path):
    """Yield an adjacent staging path so a successful promotion is atomic."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=destination.parent, prefix=".staged-", suffix=destination.suffix)
    os.close(fd)
    temporary = Path(name)
    try:
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def write_text(path: Path, value: str, *, exclusive: bool = False) -> None:
    with staged_file(path) as pending:
        pending.write_text(value, encoding="utf-8")
        if exclusive:
            promote_new(pending, path)
        else:
            pending.replace(path)


def promote_new(pending: Path, destination: Path) -> None:
    """Publish a complete artifact without ever replacing an existing name."""
    if os.name == "nt":
        # Windows rename is exclusive and works without hard link privileges.
        pending.rename(destination)
    else:
        os.link(pending, destination)


def write_json(path: Path, value: object, *, exclusive: bool = False) -> None:
    write_text(path, json_text(value), exclusive=exclusive)


@contextmanager
def artifact_lock(path: Path, *, timeout: float = 30):
    """Lock one artifact across processes. The OS releases locks on exit."""
    if os.name == "nt":
        import msvcrt

        def lock(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

        def unlock(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        def lock(handle):
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def unlock(handle):
            fcntl.flock(handle, fcntl.LOCK_UN)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock(handle)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Another process is working on {path.stem}") from exc
                time.sleep(.1)
        try:
            yield
        finally:
            unlock(handle)

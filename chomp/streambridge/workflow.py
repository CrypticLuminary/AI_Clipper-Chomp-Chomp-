"""Shared entry points for the CLI and site backend."""
from __future__ import annotations

from ..pipeline import process
from .acquire import Acquired


def process_acquired(acquired: Acquired, output, **kwargs) -> dict:
    """Run Keystone or EVOLUTION without changing the recording's source clock."""
    return process(acquired.path, output, source_link=acquired.provenance(), **kwargs)

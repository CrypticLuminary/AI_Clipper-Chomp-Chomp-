"""ClipLedger: independent, human-authored highlight quality evaluation.

Public functions deliberately avoid importing discovery, ranking, or media code.
No review field is inferred from model scores.
"""
from .core import (
    ClipLedgerError, read_manifest, make_template, read_annotations,
    evaluate, compare, write_json,
)

__all__ = [
    "ClipLedgerError", "read_manifest", "make_template",
    "read_annotations", "evaluate", "compare", "write_json",
]

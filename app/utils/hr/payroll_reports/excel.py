"""Excel dispatcher for HR Payroll Reports.

Thin layer over the per-report renderers in ``sheets/``. Kept separate from
the package ``__init__`` so the public API import surface stays flat.
"""
from __future__ import annotations

from .sheets import RENDERERS


def render_excel(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict) -> bytes:
    fn = RENDERERS.get(report_key) or RENDERERS.get("register")
    return fn(shaped_rows, summary, meta)

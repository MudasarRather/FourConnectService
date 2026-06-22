"""Travel Reports — CSV export (RFC-4180, UTF-8 BOM, KPI comment header)."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import List, Dict, Any, Optional

from .data import columns_for, report_meta


def _fmt(value, fmt) -> str:
    if value is None:
        return ""
    if fmt in ("inr", "int", "pct", "days"):
        return str(value)
    return str(value)


def render_csv(report_key: str, rows: List[Dict[str, Any]], summary: dict,
               meta_arg: Optional[dict] = None) -> bytes:
    meta = report_meta(report_key)
    cols = columns_for(report_key)
    period = (meta_arg or {}).get("period") or "All time"

    buf = io.StringIO()
    buf.write(f"# Fourreck Travel — {meta['name']}\n")
    buf.write(f"# {meta['subtitle']}\n")
    buf.write(f"# Period: {period}\n")
    buf.write(f"# Generated: {datetime.now().isoformat(timespec='seconds')} | Rows: {len(rows)}\n")
    tiles = " | ".join(f"{l}: {v}" for l, v, _k in (summary.get('tiles') or []))
    if tiles:
        buf.write(f"# {tiles}\n")
    buf.write("#\n")

    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow([c["label"] for c in cols])
    for r in rows:
        w.writerow([_fmt(r.get(c["key"]), c.get("fmt")) for c in cols])

    return ("﻿" + buf.getvalue()).encode("utf-8")

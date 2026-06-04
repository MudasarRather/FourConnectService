"""CSV exporter for HR Leave Reports.

Plain RFC-4180 CSV with a multi-line metadata header (lines starting with `#`)
so consumers know exactly what window the data covers. UTF-8 BOM is prepended
so Excel opens it with the right encoding.
"""
from __future__ import annotations

import csv
import io
from datetime import date as date_cls, datetime

from .data import report_meta
from .columns import columns_for


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, date_cls):
        return v.isoformat()
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return v


def render_csv(report_key: str, shaped_rows: list[dict], summary: dict, meta: dict) -> bytes:
    theme = report_meta(report_key)
    cols = columns_for(report_key)
    period = meta.get("period") or meta
    from_d = period["from"]
    to_d = period["to"]

    buf = io.StringIO()
    buf.write(f"# Fourreck Leave — {theme['name']}\n")
    buf.write(f"# Period: {from_d.isoformat()} to {to_d.isoformat()}\n")
    buf.write(f"# Generated: {datetime.now().isoformat(timespec='seconds')}\n")
    bits = [f"{k}={v}" for k, v in summary.items()]
    buf.write("# Summary: " + " | ".join(bits) + "\n#\n")

    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow([c[0] for c in cols])
    for row in shaped_rows:
        w.writerow([_fmt(row.get(c[1])) for c in cols])

    return ("﻿" + buf.getvalue()).encode("utf-8")

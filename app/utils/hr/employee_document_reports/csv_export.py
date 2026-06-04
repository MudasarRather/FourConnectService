"""CSV export for Employee-Document reports — shares the column descriptors
with the PDF renderer so both stay in lockstep."""
from __future__ import annotations

import csv
import io
from datetime import datetime, date as date_cls

from .data import columns, report_meta


def _fmt(value, col) -> str:
    if value is None or value == "":
        return ""
    if col.get("fmt") == "date" and isinstance(value, (date_cls, datetime)):
        return value.strftime("%d %b %Y")
    if col.get("status"):
        return str(value).replace("_", " ").title()
    return str(value)


def render_csv(report_key: str, shaped_rows: list[dict], summary: dict, meta_arg: dict) -> bytes:
    cols = columns(report_key)
    meta = report_meta(report_key)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"Fourreck — {meta['name']}"])
    writer.writerow([f"Generated {datetime.now().strftime('%d %b %Y %H:%M')}",
                     f"{len(shaped_rows)} rows",
                     f"{summary.get('total', 0)} active documents"])
    writer.writerow([])
    writer.writerow([c["label"] for c in cols])
    for row in shaped_rows:
        writer.writerow([_fmt(row.get(c["key"]), c) for c in cols])
    return buf.getvalue().encode("utf-8-sig")

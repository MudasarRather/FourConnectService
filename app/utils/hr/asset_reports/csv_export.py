"""HR Asset Reports — CSV exporter (generic over the report shape)."""
from __future__ import annotations

import csv
import io
from datetime import date


def _fmt(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def render_csv(report: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([report.get("title", "Report")])
    if report.get("subtitle"):
        w.writerow([report["subtitle"]])
    w.writerow([f"Period: {report.get('period', {}).get('label', 'All time')}"])
    w.writerow([f"Generated: {date.today().isoformat()}"])
    w.writerow([])
    cols = report["columns"]
    w.writerow([c["label"] for c in cols])
    for row in report["rows"]:
        w.writerow([_fmt(row.get(c["key"])) for c in cols])
    return buf.getvalue().encode("utf-8-sig")

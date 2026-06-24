"""HR Exit Reports — CSV export (column-aware, raw machine-readable values)."""
from __future__ import annotations

import csv
import io

from sqlalchemy.orm import Session

from app.utils.hr.exit_reports.data import columns_for, fetch_rows


def render_csv(db: Session, key: str, *, date_from=None, date_to=None, department_id=None,
               period_label: str = "") -> bytes:
    cols = columns_for(key)
    rows = fetch_rows(db, key, date_from=date_from, date_to=date_to, department_id=department_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["label"] for c in cols])
    for row in rows:
        w.writerow(["" if row.get(c["key"]) is None else row.get(c["key"]) for c in cols])
    return buf.getvalue().encode("utf-8-sig")

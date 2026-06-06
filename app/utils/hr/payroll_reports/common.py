"""Shared primitives for the HR Payroll Reports package.

Money formatting (Indian grouping), HTML escaping, date/number formatters and
the company masthead constant — used by both the PDF covers and the shared
body table. Kept dependency-free so cover modules can import freely without
pulling in WeasyPrint at import time.
"""
from __future__ import annotations

import html
from datetime import datetime, date as date_cls, time as time_cls
from typing import Any


COMPANY = {
    "name": "Fourreck",
    "legal": "Fourreck Technologies Pvt. Ltd.",
    "address_1": "4th Floor, Innovation Tower",
    "address_2": "Hyderabad, Telangana 500032, India",
    "cin": "U72200TG2020PTC123456",
    "gst": "36ABCFR1234X1ZK",
    "pan": "AABCF1234X",
    "tan": "HYDF12345E",
    "email": "payroll@fourreck.com",
    "web": "fourreck.com",
}

_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]
_MONTHS_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_name(m: int) -> str:
    return _MONTHS[m] if 1 <= m <= 12 else str(m)


def month_short(m: int) -> str:
    return _MONTHS_SHORT[m] if 1 <= m <= 12 else str(m)


# ════════════════════════════════════════════════════════════════════════════
# HTML / text helpers
# ════════════════════════════════════════════════════════════════════════════


def esc(v: Any) -> str:
    if v is None:
        return ""
    return html.escape(str(v))


# ════════════════════════════════════════════════════════════════════════════
# Money — Indian grouping (12,34,567)
# ════════════════════════════════════════════════════════════════════════════


def inr_group(value, *, paise: bool = False) -> str:
    """Format a number with Indian digit grouping. e.g. 1234567 → 12,34,567."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    neg = n < 0
    n = abs(n)
    if paise:
        whole = int(n)
        frac = f"{n - whole:.2f}"[2:]
    else:
        whole = int(round(n))
        frac = None
    s = str(whole)
    if len(s) <= 3:
        grouped = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts) + "," + tail
    out = grouped + (f".{frac}" if paise else "")
    return ("-" if neg else "") + out


def inr(value, *, paise: bool = False) -> str:
    """Rupee-prefixed Indian-grouped amount. e.g. ₹12,34,567."""
    return "₹" + inr_group(value, paise=paise)


def inr_compact(value) -> str:
    """Short money form for dense KPI tiles. ₹1.23 Cr / ₹4.56 L / ₹7,890."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    a = abs(n)
    sign = "-" if n < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f} L"
    return f"{sign}₹{inr_group(a)}"


# ════════════════════════════════════════════════════════════════════════════
# Date / time / number formatters
# ════════════════════════════════════════════════════════════════════════════


def fmt_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d).date()
        except ValueError:
            return d
    return d.strftime("%d %b %Y")


def fmt_long_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d).date()
        except ValueError:
            return d
    return d.strftime("%d %B %Y")


def fmt_time(t) -> str:
    if not t:
        return ""
    if isinstance(t, (datetime, time_cls)):
        return t.strftime("%I:%M %p").lstrip("0")
    return esc(t)


def fmt_days(v) -> str:
    return f"{float(v or 0):.1f}"


def fmt_pct(v) -> str:
    return f"{float(v or 0):.1f}%"


def fmt_signed_pct(v) -> str:
    n = float(v or 0)
    return f"{'+' if n > 0 else ''}{n:.1f}%"


def now_stamp() -> str:
    return datetime.now().strftime("%d %b %Y · %I:%M %p").lstrip("0")


# ════════════════════════════════════════════════════════════════════════════
# Cover kit — reusable HTML fragments every motif can drop in
# ════════════════════════════════════════════════════════════════════════════


def kpi_tiles_html(tiles: list) -> str:
    """Render a KPI tile strip for a cover.

    ``tiles`` is a list of (label, value_html, hex_color) tuples. ``value_html``
    is rendered as-is so callers can embed ``<small>`` units. Use the shared
    ``.kpi-grid`` / ``.kpi-tile`` classes defined in the base CSS.
    """
    return (
        '<div class="kpi-grid">'
        + "".join(
            f'<div class="kpi-tile" style="border-top-color:{c}">'
            f'<div class="kpi-label">{esc(label)}</div>'
            f'<div class="kpi-value" style="color:{c}">{val}</div>'
            f'</div>'
            for label, val, c in tiles
        )
        + "</div>"
    )


def chips_html(chips: list) -> str:
    """A row of small pill chips. ``chips`` = list of (text, bg, fg) tuples."""
    return '<div class="chip-row">' + "".join(
        f'<span class="chip" style="background:{bg};color:{fg}">{esc(text)}</span>'
        for text, bg, fg in chips
    ) + "</div>"


def period_band_html(period: dict, accent: str, soft: str, deep: str) -> str:
    """A standard FROM → TO period band used by several covers."""
    return f"""
    <div class="cover-period" style="background:{soft};border:1pt solid {accent}55">
        <div>
            <div class="label" style="color:{deep}">PAY PERIOD</div>
            <div class="value">{esc(period.get('label', ''))}</div>
        </div>
        <div style="font-size:14pt;color:{accent}">⟶</div>
        <div style="text-align:right">
            <div class="label" style="color:{deep}">FISCAL YEAR</div>
            <div class="value">{esc(period.get('fy', ''))}</div>
        </div>
    </div>
    """

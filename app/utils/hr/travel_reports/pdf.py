"""Travel Reports — WeasyPrint PDF renderer.

Each report opens on a FULL-BLEED, immersive cover with its own scene, then an
editorial body page that carries the same report's signature EMBLEM so inner
pages are distinct per report too. Scenes/emblems are embedded as base64 SVG
images (WeasyPrint renders those reliably; inline <svg> does not always paint).

WeasyPrint shells out to GTK at import time, so the import is deferred into
``render_pdf`` per the repo rule.
"""
from __future__ import annotations

import re
import math
import base64
from datetime import datetime
from typing import List, Dict, Any, Optional

from .data import columns_for, status_color, report_meta

_COMPANY = "Fourreck HRMS"
_SVG_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 848" width="600" height="848" preserveAspectRatio="xMidYMid slice">'
_EMB_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 96" width="160" height="96">'


# ─────────────────────────────────────────────────────────────────────────────
# formatters
# ─────────────────────────────────────────────────────────────────────────────
def _inr(v) -> str:
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return str(v)
    neg = v < 0
    n = str(abs(int(round(v))))
    if len(n) > 3:
        last3, rest = n[-3:], n[:-3]
        rest = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", rest)
        n = rest + "," + last3
    return ("−₹" if neg else "₹") + n


def _inr_k(v) -> str:
    v = float(v or 0)
    a = abs(v)
    sign = "−" if v < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a/1e7:.2f}".rstrip("0").rstrip(".") + "Cr"
    if a >= 1e5:
        return f"{sign}₹{a/1e5:.2f}".rstrip("0").rstrip(".") + "L"
    if a >= 1e3:
        return f"{sign}₹{a/1e3:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{sign}₹{int(a)}"


def _pretty(v) -> str:
    s = str(v)
    if "days" in s or " " in s:
        return s
    return s.replace("_", " ").title()


def _fmt(value, fmt) -> str:
    if value is None:
        return "—"
    if fmt == "inr":
        return _inr(value)
    if fmt == "int":
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    if fmt == "pct":
        return f"{value}%"
    if fmt == "days":
        return "—" if value is None else str(value)
    return str(value)


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _kpi_value(val, kind) -> str:
    if kind == "inr":
        return _inr_k(val)
    if kind == "pct":
        return f"{val}%"
    if kind == "days":
        return str(val)
    try:
        return f"{int(val):,}"
    except (TypeError, ValueError):
        return str(val)


def _img(svg: str, cls: str) -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="{cls}" src="data:image/svg+xml;base64,{b64}"/>'


# ─────────────────────────────────────────────────────────────────────────────
# full-bleed cover scenes — one per motif → (background_css, svg, dark, overlay_css)
# ─────────────────────────────────────────────────────────────────────────────
def _scene_manifest(a, d):
    rows = ""
    codes = [["BLR", "DEL", "BOM", "HYD"], ["MAA", "PNQ", "CCU", "GOI"], ["AMD", "COK", "JAI", "LKO"]]
    for r, line in enumerate(codes):
        for c, code in enumerate(line):
            x, y = 40 + c * 138, 160 + r * 150
            rows += (f'<rect x="{x}" y="{y}" width="120" height="118" rx="10" fill="#1c1608"/>'
                     f'<line x1="{x}" y1="{y+59}" x2="{x+120}" y2="{y+59}" stroke="#00000099" stroke-width="3"/>'
                     f'<text x="{x+60}" y="{y+72}" text-anchor="middle" font-family="monospace" '
                     f'font-size="42" font-weight="700" fill="{a}">{code}</text>')
    return ("#08070a", f'{_SVG_OPEN}{rows}'
            f'<text x="40" y="120" font-family="monospace" font-size="24" letter-spacing="9" fill="{a}">DEPARTURES</text></svg>',
            True, "linear-gradient(to top, rgba(0,0,0,0.92) 22%, transparent 58%)")


def _scene_ticket(a, d):
    bars = "".join(f'<rect x="{130+i*9}" y="252" width="{3 if i%3 else 5}" height="148" fill="#ffffff" opacity="0.85"/>' for i in range(42))
    return (f"linear-gradient(150deg, {a}, {d})", f'{_SVG_OPEN}<g transform="rotate(-18 300 424)">'
            f'<rect x="60" y="220" width="480" height="210" rx="20" fill="#ffffff" opacity="0.12"/>'
            f'<rect x="60" y="220" width="480" height="210" rx="20" fill="none" stroke="#ffffff" stroke-opacity="0.45" stroke-width="2"/>'
            f'<line x1="430" y1="230" x2="430" y2="420" stroke="#ffffff" stroke-opacity="0.5" stroke-dasharray="3 7"/>{bars}'
            f'<circle cx="60" cy="325" r="16" fill="#ffffff" opacity="0.22"/><circle cx="540" cy="325" r="16" fill="#ffffff" opacity="0.22"/>'
            f'</g></svg>', True, "linear-gradient(to top, rgba(0,0,0,0.45), transparent 55%)")


def _scene_passport(a, d):
    return (f"linear-gradient(160deg, {a}, {d})", f'{_SVG_OPEN}'
            f'<circle cx="410" cy="250" r="100" fill="none" stroke="#ffffff" stroke-opacity="0.28" stroke-width="3"/>'
            f'<circle cx="410" cy="250" r="76" fill="none" stroke="#ffffff" stroke-opacity="0.45" stroke-width="2"/>'
            f'<path d="M382 252 l18 20 l30 -38" fill="none" stroke="#ffffff" stroke-opacity="0.6" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle cx="150" cy="470" r="70" fill="none" stroke="#ffffff" stroke-opacity="0.2" stroke-width="2"/>'
            f'<text x="40" y="430" font-family="monospace" font-size="15" letter-spacing="4" fill="#ffffff" opacity="0.42">'
            f'P&lt;FRK&lt;TRAVELLER&lt;&lt;LOGBOOK&lt;&lt;&lt;&lt;</text></svg>',
            True, "")


def _scene_atlas(a, d):
    rings = ""
    for i in range(16):
        rx, ry = 70 + i * 36, 40 + i * 26
        cx = 300 + math.sin(i * 0.7) * 60
        cy = 360 + math.cos(i * 0.5) * 50
        rings += (f'<ellipse cx="{cx:.0f}" cy="{cy:.0f}" rx="{rx}" ry="{ry}" fill="none" '
                  f'stroke="{a}" stroke-opacity="{max(0.08, 0.55 - i*0.03):.2f}" stroke-width="1.8"/>')
    return ("#0a0a0b", f'{_SVG_OPEN}{rings}</svg>', True,
            "linear-gradient(to top, rgba(0,0,0,0.85) 20%, transparent 55%)")


def _scene_airway(a, d):
    return (f"linear-gradient(150deg, {a}, {d})", f'{_SVG_OPEN}'
            f'<circle cx="90" cy="650" r="155" fill="none" stroke="#ffffff" stroke-opacity="0.2" stroke-width="1.5"/>'
            f'<circle cx="90" cy="650" r="98" fill="none" stroke="#ffffff" stroke-opacity="0.3" stroke-width="1.5"/>'
            f'<circle cx="90" cy="650" r="44" fill="none" stroke="#ffffff" stroke-opacity="0.42" stroke-width="1.5"/>'
            f'<path d="M90 650 Q330 70 560 310" fill="none" stroke="#ffffff" stroke-opacity="0.9" stroke-width="4" stroke-dasharray="2 11"/>'
            f'<circle cx="90" cy="650" r="10" fill="#ffffff"/><circle cx="560" cy="310" r="10" fill="#ffffff"/>'
            f'<path d="M334 158 l24 10 l-24 10 l7 -10 z" fill="#ffffff"/></svg>',
            True, "linear-gradient(to top, rgba(0,0,0,0.4), transparent 50%)")


def _scene_ledger(a, d):
    lines = "".join(f'<line x1="40" y1="{300+i*40}" x2="560" y2="{300+i*40}" stroke="{a}" stroke-opacity="0.18"/>' for i in range(11))
    return ("#fbf7ee", f'{_SVG_OPEN}{lines}'
            f'<line x1="400" y1="290" x2="400" y2="740" stroke="{a}" stroke-opacity="0.28"/>'
            f'<line x1="490" y1="290" x2="490" y2="740" stroke="{a}" stroke-opacity="0.28"/>'
            f'<polyline points="70,540 200,470 320,500 430,400 520,330" fill="none" stroke="{a}" stroke-opacity="0.5" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="M498 322 l34 2 l-16 30 z" fill="{a}" fill-opacity="0.6"/></svg>',
            False, "")


def _scene_perdiem(a, d):
    coins = "".join(f'<ellipse cx="165" cy="{640-i*42}" rx="125" ry="36" fill="{a}" fill-opacity="{0.4+i*0.06:.2f}" '
                    f'stroke="#ffffff" stroke-opacity="0.32"/>' for i in range(8))
    return (f"linear-gradient(155deg, {a}, {d})", f'{_SVG_OPEN}{coins}'
            f'<circle cx="455" cy="255" r="125" fill="none" stroke="#ffffff" stroke-opacity="0.22" stroke-width="24"/>'
            f'<circle cx="455" cy="255" r="125" fill="none" stroke="#ffffff" stroke-opacity="0.92" stroke-width="24" '
            f'stroke-dasharray="560 785" stroke-linecap="round" transform="rotate(-90 455 255)"/></svg>',
            True, "linear-gradient(to top, rgba(0,0,0,0.42), transparent 52%)")


def _scene_vault(a, d):
    spokes = "".join(f'<line x1="300" y1="330" x2="{300+155*math.cos(i*1.0472):.0f}" y2="{330+155*math.sin(i*1.0472):.0f}" '
                     f'stroke="#ffffff" stroke-opacity="0.6" stroke-width="6"/>' for i in range(6))
    return (f"linear-gradient(160deg, {d}, #07120c)", f'{_SVG_OPEN}'
            f'<circle cx="300" cy="330" r="205" fill="none" stroke="{a}" stroke-opacity="0.28" stroke-width="16"/>'
            f'<circle cx="300" cy="330" r="160" fill="none" stroke="#ffffff" stroke-opacity="0.5" stroke-width="3"/>'
            f'<circle cx="300" cy="330" r="120" fill="none" stroke="#ffffff" stroke-opacity="0.25" stroke-width="2"/>{spokes}'
            f'<circle cx="300" cy="330" r="30" fill="{a}"/></svg>',
            True, "linear-gradient(to top, rgba(0,0,0,0.5), transparent 50%)")


def _scene_aging(a, d):
    bars = "".join(f'<rect x="{360+i*54}" y="{650-h}" width="40" height="{h}" rx="5" fill="#ffffff" fill-opacity="{0.4+i*0.15:.2f}"/>'
                   for i, h in enumerate([60, 110, 170, 250]))
    return (f"linear-gradient(160deg, {d}, #0a0405)", f'{_SVG_OPEN}'
            f'<circle cx="185" cy="310" r="155" fill="none" stroke="#ffffff" stroke-opacity="0.2" stroke-width="13"/>'
            f'<circle cx="185" cy="310" r="155" fill="none" stroke="{a}" stroke-opacity="0.92" stroke-width="13" '
            f'stroke-dasharray="340 974" stroke-linecap="round" transform="rotate(-90 185 310)"/>'
            f'<line x1="185" y1="310" x2="185" y2="195" stroke="#ffffff" stroke-width="9" stroke-linecap="round"/>'
            f'<line x1="185" y1="310" x2="258" y2="342" stroke="{a}" stroke-width="6" stroke-linecap="round"/>'
            f'<rect x="170" y="135" width="30" height="16" rx="4" fill="#ffffff"/>{bars}</svg>',
            True, "linear-gradient(to top, rgba(0,0,0,0.5), transparent 52%)")


def _scene_clearing(a, d):
    return (f"linear-gradient(150deg, {a}, {d})", f'{_SVG_OPEN}'
            f'<line x1="300" y1="160" x2="300" y2="440" stroke="#ffffff" stroke-opacity="0.7" stroke-width="6"/>'
            f'<line x1="120" y1="210" x2="480" y2="210" stroke="#ffffff" stroke-opacity="0.7" stroke-width="6"/>'
            f'<path d="M120 210 L58 312 A72 72 0 0 0 182 312 Z" fill="#ffffff" fill-opacity="0.18" stroke="#ffffff" stroke-opacity="0.6"/>'
            f'<path d="M480 210 L408 298 A82 82 0 0 0 552 298 Z" fill="#ffffff" fill-opacity="0.3" stroke="#ffffff" stroke-opacity="0.6"/>'
            f'<circle cx="300" cy="160" r="12" fill="#ffffff"/><rect x="268" y="440" width="64" height="15" rx="5" fill="#ffffff" opacity="0.85"/></svg>',
            True, "linear-gradient(to top, rgba(0,0,0,0.42), transparent 52%)")


def _scene_leaderboard(a, d):
    pods = [(150, 120), (300, 210), (450, 90)]
    bars = "".join(f'<rect x="{x}" y="{440-h}" width="120" height="{h+12}" rx="8" fill="#ffffff" fill-opacity="{0.25 if i!=1 else 0.42}"/>'
                   for i, (x, h) in enumerate(pods))
    return (f"linear-gradient(155deg, {a}, {d})", f'{_SVG_OPEN}{bars}'
            f'<text x="205" y="395" font-family="sans-serif" font-size="64" font-weight="800" fill="#ffffff" opacity="0.6">2</text>'
            f'<text x="352" y="330" font-family="sans-serif" font-size="80" font-weight="800" fill="#ffffff" opacity="0.9">1</text>'
            f'<text x="505" y="420" font-family="sans-serif" font-size="54" font-weight="800" fill="#ffffff" opacity="0.6">3</text>'
            f'<path d="M360 120 l13 28 l30 2 l-23 19 l8 30 l-28 -16 l-28 16 l8 -30 l-23 -19 l30 -2 z" fill="#ffffff"/></svg>',
            True, "linear-gradient(to top, rgba(0,0,0,0.42), transparent 52%)")


def _scene_tower(a, d):
    return (f"linear-gradient(160deg, {d}, #0a0805)", f'{_SVG_OPEN}'
            f'<rect x="120" y="310" width="46" height="190" fill="#ffffff" opacity="0.2"/>'
            f'<path d="M106 310 L180 310 L162 234 L124 234 Z" fill="{a}" fill-opacity="0.4" stroke="{a}" stroke-opacity="0.65"/>'
            f'<rect x="118" y="192" width="50" height="36" rx="4" fill="#ffffff" opacity="0.32"/>'
            f'<circle cx="143" cy="170" r="7" fill="{a}"/>'
            f'<circle cx="425" cy="330" r="165" fill="none" stroke="#ffffff" stroke-opacity="0.2" stroke-width="15"/>'
            f'<circle cx="425" cy="330" r="165" fill="none" stroke="{a}" stroke-opacity="0.92" stroke-width="15" '
            f'stroke-dasharray="560 1037" stroke-linecap="round" transform="rotate(-90 425 330)"/>'
            f'<path d="M425 330 L425 165 A165 165 0 0 1 556 248 Z" fill="{a}" fill-opacity="0.16"/></svg>',
            True, "linear-gradient(to top, rgba(0,0,0,0.5), transparent 52%)")


_SCENES = {
    "manifest": _scene_manifest, "ticket": _scene_ticket, "passport": _scene_passport,
    "atlas": _scene_atlas, "airway": _scene_airway, "ledger": _scene_ledger,
    "perdiem": _scene_perdiem, "vault": _scene_vault, "aging": _scene_aging,
    "clearing": _scene_clearing, "leaderboard": _scene_leaderboard, "tower": _scene_tower,
}


# ─────────────────────────────────────────────────────────────────────────────
# compact inner-page emblems (accent on transparent → reads on white body)
# ─────────────────────────────────────────────────────────────────────────────
def _emb_manifest(a, d):
    t = "".join(f'<rect x="{6+i*50}" y="20" width="44" height="56" rx="6" fill="{d}" fill-opacity="0.12" stroke="{a}"/>'
                f'<text x="{28+i*50}" y="56" text-anchor="middle" font-family="monospace" font-size="15" font-weight="700" fill="{a}">{c}</text>'
                for i, c in enumerate(["DEP", "BLR", "DEL"]))
    return _EMB_OPEN + t + "</svg>"


def _emb_ticket(a, d):
    bars = "".join(f'<rect x="{96+i*5}" y="28" width="{2 if i%3 else 3}" height="40" fill="{d}"/>' for i in range(11))
    return (_EMB_OPEN + f'<rect x="6" y="22" width="148" height="52" rx="8" fill="{a}" fill-opacity="0.1" stroke="{a}"/>'
            f'<line x1="88" y1="24" x2="88" y2="72" stroke="{a}" stroke-dasharray="2 4"/>{bars}'
            f'<text x="16" y="54" font-family="sans-serif" font-size="13" font-weight="800" fill="{a}">TICKET</text></svg>')


def _emb_passport(a, d):
    return (_EMB_OPEN + f'<circle cx="120" cy="40" r="30" fill="none" stroke="{a}" stroke-width="2.5" transform="rotate(-12 120 40)"/>'
            f'<text x="120" y="45" text-anchor="middle" font-family="sans-serif" font-size="9" font-weight="700" fill="{a}" transform="rotate(-12 120 40)">OK</text>'
            f'<rect x="8" y="18" width="58" height="60" rx="6" fill="{a}" fill-opacity="0.12" stroke="{a}"/>'
            f'<circle cx="37" cy="40" r="12" fill="none" stroke="{d}" stroke-width="2"/><path d="M22 70 q15 -16 30 0" fill="none" stroke="{d}" stroke-width="2"/></svg>')


def _emb_atlas(a, d):
    rings = "".join(f'<ellipse cx="80" cy="48" rx="{16+i*18}" ry="{8+i*12}" fill="none" stroke="{a}" stroke-opacity="{0.7-i*0.13:.2f}"/>' for i in range(5))
    return _EMB_OPEN + rings + "</svg>"


def _emb_airway(a, d):
    return (_EMB_OPEN + f'<circle cx="26" cy="74" r="22" fill="none" stroke="{a}" stroke-opacity="0.5"/>'
            f'<path d="M26 74 Q90 -8 150 38" fill="none" stroke="{a}" stroke-width="2.5" stroke-dasharray="2 5"/>'
            f'<circle cx="26" cy="74" r="5" fill="{d}"/><circle cx="150" cy="38" r="5" fill="{a}"/></svg>')


def _emb_ledger(a, d):
    lines = "".join(f'<line x1="6" y1="{20+i*15}" x2="120" y2="{20+i*15}" stroke="{a}" stroke-opacity="0.35"/>' for i in range(5))
    return (_EMB_OPEN + lines + f'<path d="M138 64 l8 -16 l8 16 z" fill="{a}"/><rect x="142" y="34" width="8" height="18" fill="{a}"/></svg>')


def _emb_perdiem(a, d):
    coins = "".join(f'<ellipse cx="40" cy="{76-i*11}" rx="30" ry="8" fill="{a}" fill-opacity="{0.45+i*0.1:.2f}" stroke="{d}"/>' for i in range(5))
    return (_EMB_OPEN + coins + f'<circle cx="124" cy="46" r="26" fill="none" stroke="{a}" stroke-opacity="0.25" stroke-width="6"/>'
            f'<circle cx="124" cy="46" r="26" fill="none" stroke="{a}" stroke-width="6" stroke-dasharray="120 164" stroke-linecap="round" transform="rotate(-90 124 46)"/></svg>')


def _emb_vault(a, d):
    sp = "".join(f'<line x1="48" y1="48" x2="{48+34*math.cos(i*1.0472):.0f}" y2="{48+34*math.sin(i*1.0472):.0f}" stroke="{a}" stroke-width="2.5"/>' for i in range(6))
    return (_EMB_OPEN + f'<circle cx="48" cy="48" r="40" fill="none" stroke="{a}" stroke-opacity="0.3" stroke-width="5"/>'
            f'<circle cx="48" cy="48" r="30" fill="none" stroke="{d}" stroke-width="2"/>{sp}<circle cx="48" cy="48" r="7" fill="{d}"/></svg>')


def _emb_aging(a, d):
    return (_EMB_OPEN + f'<circle cx="48" cy="50" r="38" fill="none" stroke="{a}" stroke-width="4"/>'
            f'<circle cx="48" cy="50" r="38" fill="none" stroke="{d}" stroke-width="4" stroke-dasharray="60 239" transform="rotate(-90 48 50)"/>'
            f'<line x1="48" y1="50" x2="48" y2="22" stroke="{d}" stroke-width="3"/><line x1="48" y1="50" x2="68" y2="60" stroke="{a}" stroke-width="3"/>'
            f'<rect x="42" y="6" width="12" height="7" rx="2" fill="{d}"/>'
            + "".join(f'<rect x="{104+i*14}" y="{86-h}" width="10" height="{h}" rx="2" fill="{a}" fill-opacity="{0.5+i*0.15:.2f}"/>' for i, h in enumerate([16, 28, 42, 60]))
            + "</svg>")


def _emb_clearing(a, d):
    return (_EMB_OPEN + f'<line x1="80" y1="16" x2="80" y2="76" stroke="{d}" stroke-width="3"/>'
            f'<line x1="34" y1="30" x2="126" y2="30" stroke="{d}" stroke-width="3"/>'
            f'<path d="M34 30 L18 54 A26 26 0 0 0 50 54 Z" fill="{a}" fill-opacity="0.25" stroke="{a}"/>'
            f'<path d="M126 30 L108 50 A28 28 0 0 0 144 50 Z" fill="{d}" fill-opacity="0.25" stroke="{d}"/>'
            f'<circle cx="80" cy="16" r="5" fill="{a}"/><rect x="66" y="76" width="28" height="7" rx="3" fill="{d}"/></svg>')


def _emb_leaderboard(a, d):
    bars = "".join(f'<rect x="{30+i*40}" y="{82-h}" width="32" height="{h}" rx="4" fill="{a}" fill-opacity="{0.85 if i==1 else 0.5}"/>'
                   for i, h in enumerate([34, 56, 24]))
    nums = "".join(f'<text x="{46+i*40}" y="{82-h+18}" text-anchor="middle" font-family="sans-serif" font-size="14" font-weight="800" fill="#ffffff">{n}</text>'
                   for i, (h, n) in enumerate([(34, 2), (56, 1), (24, 3)]))
    return _EMB_OPEN + bars + nums + "</svg>"


def _emb_tower(a, d):
    return (_EMB_OPEN + f'<rect x="20" y="44" width="14" height="40" fill="{d}" fill-opacity="0.4"/>'
            f'<path d="M16 44 L38 44 L33 26 L21 26 Z" fill="{a}" fill-opacity="0.4" stroke="{a}"/>'
            f'<rect x="19" y="16" width="16" height="10" rx="2" fill="{d}"/>'
            f'<circle cx="112" cy="48" r="32" fill="none" stroke="{a}" stroke-opacity="0.25" stroke-width="6"/>'
            f'<circle cx="112" cy="48" r="32" fill="none" stroke="{a}" stroke-width="6" stroke-dasharray="110 201" stroke-linecap="round" transform="rotate(-90 112 48)"/>'
            f'<path d="M112 48 L112 16 A32 32 0 0 1 137 32 Z" fill="{a}" fill-opacity="0.18"/></svg>')


_EMBLEMS = {
    "manifest": _emb_manifest, "ticket": _emb_ticket, "passport": _emb_passport,
    "atlas": _emb_atlas, "airway": _emb_airway, "ledger": _emb_ledger,
    "perdiem": _emb_perdiem, "vault": _emb_vault, "aging": _emb_aging,
    "clearing": _emb_clearing, "leaderboard": _emb_leaderboard, "tower": _emb_tower,
}


# ─────────────────────────────────────────────────────────────────────────────
# cover
# ─────────────────────────────────────────────────────────────────────────────
def _cover(meta: dict, summary: dict, period: str, n: int) -> str:
    a, d = meta["accent"], meta["deep"]
    bg, scene, dark, overlay = _SCENES.get(meta["motif"], _scene_manifest)(a, d)
    ink = "#ffffff" if dark else "#1a1208"
    sub = "rgba(255,255,255,0.84)" if dark else "#5a4a33"
    chip_bg = "rgba(255,255,255,0.14)" if dark else "rgba(0,0,0,0.05)"
    chip_bd = "rgba(255,255,255,0.32)" if dark else "rgba(0,0,0,0.16)"
    crest_bg = "rgba(255,255,255,0.18)" if dark else d
    tiles = summary.get("tiles", [])
    hero = tiles[0] if tiles else ("Records", n, "int")
    now = datetime.now().strftime("%d %b %Y")
    # Darken (or, on the light ledger cover, lighten) the top + bottom bands so the
    # crest and the bottom-anchored title stay legible over any scene art.
    if dark:
        ov = ("linear-gradient(to bottom, rgba(0,0,0,0.5) 0%, rgba(0,0,0,0) 17%, "
              "rgba(0,0,0,0) 42%, rgba(0,0,0,0.86) 100%)")
    else:
        ov = ("linear-gradient(to bottom, rgba(251,247,238,0.55) 0%, rgba(251,247,238,0) 18%, "
              "rgba(251,247,238,0) 46%, rgba(251,247,238,0.85) 100%)")
    overlay_div = f'<div class="cv-overlay" style="background:{ov}"></div>'
    return f"""
    <section class="cover" style="background:{bg}">
      {_img(scene, "cv-bg")}
      {overlay_div}
      <div class="cv-crest" style="background:{crest_bg};color:{ink}">F</div>
      <span class="cv-grp" style="color:{ink};background:{chip_bg};border:1px solid {chip_bd}">{_esc(meta['group'].upper())}</span>
      <div class="cv-foot" style="color:{ink}">
        <div class="cv-kick">{_esc(meta['tagline'].upper())}</div>
        <h1 class="cv-title">{_esc(meta['name'])}</h1>
        <p class="cv-sub" style="color:{sub}">{_esc(meta['subtitle'])}</p>
        <div class="cv-meta">
          <div class="cv-hero"><b>{_esc(_kpi_value(hero[1], hero[2]))}</b><span style="color:{sub}">{_esc(hero[0])}</span></div>
          <div class="cv-divider" style="background:{chip_bd}"></div>
          <div class="cv-period">
            <span class="cv-plab" style="color:{sub}">REPORTING PERIOD</span>
            <span class="cv-pval">{_esc(period)}</span>
          </div>
        </div>
      </div>
      <div class="cv-bottombar" style="color:{sub};border-top:1px solid {chip_bd}">
        <span>{_COMPANY} · Travel Intelligence</span><span>Generated {now} · {n} record(s)</span>
      </div>
    </section>"""


# ─────────────────────────────────────────────────────────────────────────────
# body
# ─────────────────────────────────────────────────────────────────────────────
def _kpi_cards(tiles, accent, deep) -> str:
    cells = ""
    for label, val, kind in (tiles or [])[:4]:
        cells += (f'<div class="kc"><span class="kc-rail" style="background:{accent}"></span>'
                  f'<div class="kc-v" style="color:{deep}">{_esc(_kpi_value(val, kind))}</div>'
                  f'<div class="kc-l">{_esc(label)}</div></div>')
    return f'<div class="kc-strip">{cells}</div>'


def _cell(row: dict, col: dict) -> str:
    v = row.get(col["key"])
    if col.get("status"):
        c = status_color(v)
        return (f'<td class="a-center"><span class="pill" '
                f'style="background:{c["light"]};color:{c["deep"]};border:1px solid {c["hex"]}66">'
                f'{_esc(_pretty(v))}</span></td>')
    cls = []
    if col.get("mono"):
        cls.append("mono")
    for pred, klass in (("danger_if", "c-danger"), ("good_if", "c-good"), ("warn_if", "c-warn")):
        fn = col.get(pred)
        try:
            if fn and fn(v):
                cls.append(klass)
                break
        except Exception:
            pass
    txt = _fmt(v, col.get("fmt"))
    if col.get("bar"):
        try:
            pct = max(0, min(100, float(v or 0)))
        except (TypeError, ValueError):
            pct = 0
        return (f'<td class="a-{col["align"]} barcell"><span class="bar-fill" style="width:{pct}%"></span>'
                f'<span class="bar-txt">{_esc(txt)}</span></td>')
    return f'<td class="a-{col["align"]} {" ".join(cls)}">{_esc(txt)}</td>'


def _table(key, rows, accent, deep) -> str:
    cols = columns_for(key)
    if not rows:
        return '<div class="empty">No records match the selected period and scope.</div>'
    head = "".join(f'<th class="a-{c["align"]}" style="background:{accent};border-bottom:2px solid {deep}">'
                   f'{_esc(c["label"])}</th>' for c in cols)
    body = []
    for i, r in enumerate(rows):
        cells = "".join(_cell(r, c) for c in cols)
        body.append(f'<tr class="{"zebra" if i % 2 else ""}">{cells}</tr>')
    return (f'<table class="dt"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _body(key, rows, summary, meta, period) -> str:
    a, d = meta["accent"], meta["deep"]
    wide = len(columns_for(key)) >= 8
    emblem = _EMBLEMS.get(meta["motif"], _emb_manifest)(a, d)
    return f"""
    <section class="{'body wide' if wide else 'body'}">
      <div class="bd-head">
        <div class="bd-bar" style="background:linear-gradient(180deg,{a},{d})"></div>
        <div class="bd-lead">
          <span class="bd-kick" style="color:{d}">FOURRECK · TRAVEL · {_esc(meta['group'].upper())}</span>
          <h2 class="bd-title">{_esc(meta['name'])}</h2>
          <p class="bd-sub">{_esc(meta['subtitle'])}</p>
        </div>
        <div class="bd-right">
          {_img(emblem, "bd-emblem")}
          <div class="bd-meta"><span>{_esc(period)}</span><b>{len(rows)} record(s)</b></div>
        </div>
      </div>
      {_kpi_cards(summary.get('tiles', []), a, d)}
      {_table(key, rows, a, d)}
    </section>"""


# ─────────────────────────────────────────────────────────────────────────────
# stylesheet
# ─────────────────────────────────────────────────────────────────────────────
_BASE_CSS = """
@page { size: A4 portrait; margin: 16mm 14mm 18mm;
  @bottom-left { content: "Fourreck HRMS · Travel Management"; font-size: 7.5pt; color: #9a8e78; }
  @bottom-right { content: "Page " counter(page) " / " counter(pages); font-size: 7.5pt; color: #9a8e78; } }
@page wide { size: A4 landscape; margin: 14mm 12mm 16mm; }
@page :first { margin: 0; @bottom-left { content: ""; } @bottom-right { content: ""; } }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1410; margin: 0; }

/* ── full-bleed cover ── */
.cover { position: relative; width: 210mm; height: 297mm; overflow: hidden; page-break-after: always; }
.cv-bg { position: absolute; top: 0; left: 0; right: 0; bottom: 0; width: 100%; height: 100%; }
.cv-overlay { position: absolute; top: 0; left: 0; right: 0; bottom: 0; }
.cv-crest { position: absolute; top: 16mm; left: 16mm; width: 15mm; height: 15mm; border-radius: 4.5mm; display: flex; align-items: center; justify-content: center; font-size: 22pt; font-weight: 800; }
.cv-grp { position: absolute; top: 18mm; right: 16mm; font-size: 8.5pt; font-weight: 700; letter-spacing: 2px; padding: 5px 13px; border-radius: 999px; }
.cv-foot { position: absolute; left: 16mm; right: 16mm; bottom: 24mm; max-width: 172mm; }
.cv-kick { font-size: 9.5pt; font-weight: 700; letter-spacing: 3px; opacity: 0.92; margin-bottom: 5mm; }
.cv-title { font-size: 42pt; font-weight: 800; line-height: 1.02; margin: 0 0 5mm; letter-spacing: -1px; }
.cv-sub { font-size: 12pt; line-height: 1.5; margin: 0 0 8mm; max-width: 150mm; }
.cv-meta { display: flex; align-items: center; gap: 8mm; }
.cv-hero b { font-size: 30pt; font-weight: 800; line-height: 1; display: block; }
.cv-hero span { font-size: 8.5pt; letter-spacing: 1.5px; text-transform: uppercase; }
.cv-divider { width: 1px; height: 16mm; }
.cv-period { display: flex; flex-direction: column; gap: 3px; }
.cv-plab { font-size: 7.5pt; font-weight: 700; letter-spacing: 2px; }
.cv-pval { font-size: 12pt; font-weight: 600; }
.cv-bottombar { position: absolute; left: 16mm; right: 16mm; bottom: 11mm; display: flex; justify-content: space-between; font-size: 8pt; padding-top: 3mm; }

/* ── body ── */
.body { padding-top: 2mm; }
.bd-head { display: flex; align-items: flex-start; gap: 5mm; }
.bd-bar { width: 5px; align-self: stretch; border-radius: 3px; min-height: 26mm; }
.bd-lead { flex: 1; }
.bd-kick { font-size: 8pt; letter-spacing: 2.5px; font-weight: 700; }
.bd-title { font-size: 22pt; font-weight: 800; margin: 3px 0 4px; color: #161009; letter-spacing: -0.5px; }
.bd-sub { font-size: 10pt; color: #6b5d48; margin: 0; max-width: 130mm; }
.bd-right { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.bd-emblem { width: 130px; height: 78px; }
.bd-meta { text-align: right; font-size: 9pt; color: #6b5d48; white-space: nowrap; }
.bd-meta b { display: block; color: #1a1410; margin-top: 3px; }

.kc-strip { display: flex; gap: 4mm; margin: 7mm 0; }
.kc { flex: 1; position: relative; overflow: hidden; background: #fff; border: 1px solid #ece3d3; border-radius: 4mm; padding: 7mm 5mm 5mm; box-shadow: 0 3px 10px rgba(120,90,30,0.07); }
.kc-rail { position: absolute; top: 0; left: 0; right: 0; height: 4px; }
.kc-v { font-size: 21pt; font-weight: 800; line-height: 1; }
.kc-l { font-size: 8pt; color: #7a6c54; margin-top: 5px; letter-spacing: 0.5px; text-transform: uppercase; }

.dt { width: 100%; border-collapse: collapse; font-size: 8.6pt; }
.dt thead { display: table-header-group; }
.dt th { color: #fff; text-align: left; padding: 7px 9px; font-weight: 700; font-size: 8pt; letter-spacing: 0.4px; text-transform: uppercase; }
.dt td { padding: 6px 9px; border-bottom: 1px solid #efe7d8; color: #2a2218; }
.dt tr.zebra td { background: #fdfaf3; }
.dt tr { page-break-inside: avoid; }
.a-left { text-align: left; } .a-right { text-align: right; } .a-center { text-align: center; }
.mono { font-family: "Courier New", monospace; font-size: 8pt; color: #4a3f2e; }
.pill { display: inline-block; font-size: 7.5pt; font-weight: 700; padding: 2px 9px; border-radius: 999px; letter-spacing: 0.3px; white-space: nowrap; }
.c-danger { color: #b91c1c; font-weight: 700; }
.c-good { color: #047857; font-weight: 700; }
.c-warn { color: #b45309; font-weight: 700; }
.barcell { position: relative; }
.bar-fill { position: absolute; left: 0; top: 0; bottom: 0; background: rgba(217,119,6,0.16); }
.bar-txt { position: relative; font-weight: 700; }
.empty { padding: 26px; text-align: center; color: #9a8e78; font-size: 10pt; border: 1px dashed #ddd2bf; border-radius: 8px; background: #fdfaf3; }
"""


def render_pdf(report_key: str, rows: List[Dict[str, Any]], summary: dict,
               meta_arg: Optional[dict] = None) -> bytes:
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # lazy — needs GTK on PATH first

    meta = report_meta(report_key)
    period = (meta_arg or {}).get("period") or "All time"
    cover = _cover(meta, summary, period, len(rows))
    body = _body(report_key, rows, summary, meta, period)
    html = (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style>{_BASE_CSS}</style></head><body>{cover}{body}</body></html>')
    return HTML(string=html).write_pdf()

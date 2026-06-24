"""HR Exit Reports — WeasyPrint PDF renderer.

Each report opens on a FULL-BLEED, immersive cover with its OWN scene (one per
motif → COVER_RENDERERS), then an editorial body page that carries the same
report's signature emblem so inner pages are distinct per report too. Scenes /
emblems are embedded as base64 SVG images (WeasyPrint paints those reliably;
inline <svg> does not always render).

WeasyPrint shells out to GTK at import time, so the import is deferred into
``render_pdf`` per the repo rule.
"""
from __future__ import annotations

import math
import base64
from datetime import date, datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.utils.hr.exit_reports.data import (
    report_meta, columns_for, fetch_rows, shape_summary, status_color,
)

_COMPANY = "Fourreck HRMS"
_SVG_OPEN = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 848" '
             'width="600" height="848" preserveAspectRatio="xMidYMid slice">')
_EMB_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 96" width="160" height="96">'


# ─────────────────────────────────────────────────────────────────────────────
# formatters
# ─────────────────────────────────────────────────────────────────────────────
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


def _inr(v) -> str:
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return str(v)
    neg = v < 0
    n = f"{abs(v):,.0f}"
    return ("−₹" if neg else "₹") + n


def _fmt(value, fmt) -> str:
    if value is None or value == "":
        return "—"
    if fmt == "inr":
        return _inr(value)
    if fmt == "int":
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    if fmt == "num1":
        try:
            return f"{float(value):.1f}"
        except (TypeError, ValueError):
            return str(value)
    if fmt == "pct":
        return f"{value}%"
    return str(value)


def _kpi_value(val, kind) -> str:
    if kind == "inr":
        return _inr_k(val)
    if kind == "pct":
        return f"{val}%"
    if kind == "num1":
        try:
            return f"{float(val):.1f}"
        except (TypeError, ValueError):
            return str(val)
    try:
        return f"{int(val):,}"
    except (TypeError, ValueError):
        return str(val)


def _esc(s) -> str:
    return str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _img(svg: str, cls: str) -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="{cls}" src="data:image/svg+xml;base64,{b64}"/>'


_DARK_OVERLAY = ("linear-gradient(to bottom, rgba(0,0,0,0.5) 0%, rgba(0,0,0,0) 17%, "
                 "rgba(0,0,0,0) 40%, rgba(0,0,0,0.88) 100%)")
_LIGHT_OVERLAY = ("linear-gradient(to bottom, rgba(251,247,238,0.55) 0%, rgba(251,247,238,0) 18%, "
                  "rgba(251,247,238,0) 46%, rgba(251,247,238,0.88) 100%)")


# ─────────────────────────────────────────────────────────────────────────────
# Full-bleed cover scenes — one per motif → (background_css, svg, is_dark)
# ─────────────────────────────────────────────────────────────────────────────
def _scene_gateway(a, d):
    rows = ""
    for i in range(6):
        w = 60 + i * 58
        x = 300 - w / 2
        y = 690 - i * 64
        op = 0.10 + i * 0.06
        rows += (f'<rect x="{x:.0f}" y="{y}" width="{w:.0f}" height="34" rx="6" '
                 f'fill="{a}" fill-opacity="{op:.2f}"/>')
    return (f"linear-gradient(165deg, #0c0907, {d})", f'{_SVG_OPEN}'
            # glowing sunburst threshold behind the arch
            f'<circle cx="300" cy="300" r="150" fill="{a}" fill-opacity="0.10"/>'
            f'<circle cx="300" cy="300" r="92" fill="{a}" fill-opacity="0.18"/>'
            # the arch
            f'<path d="M150 470 L150 300 Q300 150 450 300 L450 470" fill="none" '
            f'stroke="{a}" stroke-width="14" stroke-opacity="0.85"/>'
            f'<path d="M150 470 L150 300 Q300 150 450 300 L450 470" fill="none" '
            f'stroke="#ffffff" stroke-width="3" stroke-opacity="0.4"/>'
            # vertical seal beam through the gate
            f'<rect x="294" y="200" width="12" height="300" rx="6" fill="#ffffff" fill-opacity="0.5"/>'
            f'{rows}</svg>', True)


def _scene_compass(a, d):
    rings = "".join(f'<circle cx="300" cy="380" r="{60+i*52}" fill="none" stroke="{a}" '
                    f'stroke-opacity="{max(0.08,0.5-i*0.08):.2f}" stroke-width="2"/>' for i in range(5))
    spokes = ""
    for i in range(16):
        ang = i * math.pi / 8
        x2 = 300 + 270 * math.cos(ang)
        y2 = 380 + 270 * math.sin(ang)
        spokes += (f'<line x1="300" y1="380" x2="{x2:.0f}" y2="{y2:.0f}" stroke="{a}" '
                   f'stroke-opacity="{0.3 if i%4==0 else 0.12:.2f}" stroke-width="{2 if i%4==0 else 1}"/>')
    return ("#0a0908", f'{_SVG_OPEN}{rings}{spokes}'
            f'<path d="M300 200 L322 380 L300 360 L278 380 Z" fill="{a}" fill-opacity="0.9"/>'
            f'<path d="M300 560 L322 380 L300 400 L278 380 Z" fill="#ffffff" fill-opacity="0.32"/>'
            f'<circle cx="300" cy="380" r="14" fill="{a}"/></svg>', True)


def _scene_strata(a, d):
    # tree-ring cross-section: tenure = growth rings.
    rings = "".join(f'<circle cx="300" cy="400" r="{30+i*34}" fill="none" stroke="{a}" '
                    f'stroke-opacity="{max(0.12,0.62-i*0.05):.2f}" stroke-width="{4 if i%2 else 2}"/>'
                    for i in range(9))
    return (f"linear-gradient(160deg, {d}, #100a05)", f'{_SVG_OPEN}{rings}'
            f'<circle cx="300" cy="400" r="16" fill="{a}"/>'
            f'<line x1="300" y1="400" x2="540" y2="180" stroke="#ffffff" stroke-opacity="0.22" stroke-width="2"/>'
            f'</svg>', True)


def _scene_boomerang(a, d):
    # emerald welcome-back: an open door with a U-turn return arc.
    return (f"linear-gradient(160deg, {a}, {d})", f'{_SVG_OPEN}'
            f'<rect x="170" y="220" width="150" height="380" rx="6" fill="#ffffff" fill-opacity="0.12" '
            f'stroke="#ffffff" stroke-opacity="0.4" stroke-width="2"/>'
            f'<rect x="320" y="200" width="150" height="400" rx="6" fill="#ffffff" fill-opacity="0.06" '
            f'transform="skewY(-7 320 200)"/>'
            f'<circle cx="306" cy="410" r="7" fill="#ffffff"/>'
            # return arc
            f'<path d="M150 720 Q60 460 300 430 L300 430" fill="none" stroke="#ffffff" '
            f'stroke-width="6" stroke-opacity="0.85" stroke-dasharray="2 12" stroke-linecap="round"/>'
            f'<path d="M286 408 l16 22 l24 -16 z" fill="#ffffff"/></svg>', True)


def _scene_pulse(a, d):
    grid = "".join(f'<line x1="0" y1="{260+i*60}" x2="600" y2="{260+i*60}" stroke="{a}" stroke-opacity="0.08"/>'
                   for i in range(7))
    pts = "40,440 130,440 175,440 205,300 245,560 290,360 330,440 600,440"
    return ("#08070a", f'{_SVG_OPEN}{grid}'
            f'<polyline points="{pts}" fill="none" stroke="{a}" stroke-width="5" '
            f'stroke-linecap="round" stroke-linejoin="round" stroke-opacity="0.9"/>'
            f'<polyline points="{pts}" fill="none" stroke="#ffffff" stroke-width="1.5" stroke-opacity="0.35"/>'
            f'<circle cx="245" cy="560" r="9" fill="{a}"/>'
            f'<circle cx="205" cy="300" r="7" fill="#ffffff" fill-opacity="0.7"/></svg>', True)


def _scene_atlas(a, d):
    heights = [110, 190, 150, 250, 130, 210, 90, 170]
    bars = ""
    for i, h in enumerate(heights):
        x = 64 + i * 64
        bars += (f'<rect x="{x}" y="{640-h}" width="44" height="{h}" rx="6" '
                 f'fill="{a}" fill-opacity="{0.4+0.5*(h/250):.2f}"/>')
    return (f"linear-gradient(165deg, #0b0907, {d})", f'{_SVG_OPEN}'
            f'<line x1="40" y1="640" x2="560" y2="640" stroke="#ffffff" stroke-opacity="0.3" stroke-width="2"/>'
            f'{bars}</svg>', True)


def _scene_prism(a, d):
    rays = ""
    cols = ["#16a34a", "#f59e0b", "#dc2626"]
    for i, c in enumerate(cols):
        y2 = 300 + i * 130
        rays += (f'<path d="M330 380 L600 {y2}" stroke="{c}" stroke-width="14" stroke-opacity="0.7"/>')
    return ("#09080a", f'{_SVG_OPEN}'
            f'<line x1="0" y1="300" x2="300" y2="380" stroke="#ffffff" stroke-width="5" stroke-opacity="0.7"/>'
            f'<path d="M300 270 L370 460 L230 460 Z" fill="{a}" fill-opacity="0.22" '
            f'stroke="{a}" stroke-opacity="0.7" stroke-width="2"/>'
            f'{rays}</svg>', True)


def _scene_voiceprint(a, d):
    bars = ""
    for i in range(40):
        ang = i * math.pi / 20
        h = 30 + 70 * abs(math.sin(i * 1.3)) * (0.6 + 0.4 * math.cos(i * 0.6))
        x1 = 300 + 95 * math.cos(ang)
        y1 = 400 + 95 * math.sin(ang)
        x2 = 300 + (95 + h) * math.cos(ang)
        y2 = 400 + (95 + h) * math.sin(ang)
        bars += (f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                 f'stroke="{a}" stroke-opacity="{0.4+0.5*(h/100):.2f}" stroke-width="4" stroke-linecap="round"/>')
    return (f"linear-gradient(160deg, {d}, #0a0704)", f'{_SVG_OPEN}'
            f'<circle cx="300" cy="400" r="78" fill="{a}" fill-opacity="0.16"/>'
            f'<circle cx="300" cy="400" r="78" fill="none" stroke="{a}" stroke-opacity="0.5" stroke-width="2"/>'
            f'{bars}</svg>', True)


def _scene_hourglass(a, d):
    grains = "".join(f'<circle cx="{296+(i%3)*6}" cy="{420+i*9}" r="2.4" fill="{a}" fill-opacity="0.8"/>'
                     for i in range(10))
    ticks = "".join(f'<line x1="470" y1="{260+i*44}" x2="492" y2="{260+i*44}" stroke="#ffffff" stroke-opacity="0.3"/>'
                    for i in range(9))
    return (f"linear-gradient(160deg, {d}, #0c0604)", f'{_SVG_OPEN}'
            f'<path d="M190 230 L410 230 L320 400 L410 570 L190 570 L280 400 Z" fill="none" '
            f'stroke="{a}" stroke-width="6" stroke-opacity="0.85"/>'
            f'<path d="M200 240 L400 240 L308 400 Z" fill="{a}" fill-opacity="0.45"/>'
            f'<path d="M218 560 L382 560 L320 410 Z" fill="{a}" fill-opacity="0.7"/>'
            f'{grains}{ticks}</svg>', True)


def _scene_lattice(a, d):
    cells = ""
    marks = ""
    states = [1, 1, 0, 1, 2, 1, 1, 0, 1, 1, 1, 2]   # 1=cleared 0=pending 2=blocked
    for i in range(12):
        col, row = i % 4, i // 4
        x, y = 120 + col * 95, 280 + row * 100
        st = states[i]
        col_c = "#16a34a" if st == 1 else ("#dc2626" if st == 2 else a)
        op = 0.5 if st == 1 else (0.5 if st == 2 else 0.2)
        cells += (f'<rect x="{x}" y="{y}" width="74" height="74" rx="12" fill="{col_c}" fill-opacity="{op*0.4:.2f}" '
                  f'stroke="{col_c}" stroke-opacity="{op:.2f}" stroke-width="2"/>')
        if st == 1:
            marks += (f'<path d="M{x+24} {y+38} l10 11 l18 -22" fill="none" stroke="#16a34a" '
                      f'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')
        elif st == 2:
            marks += (f'<rect x="{x+28}" y="{y+34}" width="18" height="16" rx="3" fill="none" stroke="#dc2626" stroke-width="3"/>'
                      f'<path d="M{x+31} {y+34} v-6 a6 6 0 0 1 12 0 v6" fill="none" stroke="#dc2626" stroke-width="3"/>')
    return ("#0a0908", f'{_SVG_OPEN}{cells}{marks}</svg>', True)


def _scene_mint(a, d):
    coins = "".join(f'<ellipse cx="160" cy="{600-i*34}" rx="92" ry="26" fill="#ffffff" fill-opacity="{0.18+i*0.07:.2f}" '
                    f'stroke="#ffffff" stroke-opacity="0.3"/>' for i in range(7))
    return (f"linear-gradient(160deg, {a}, {d})", f'{_SVG_OPEN}'
            # balance scale
            f'<line x1="300" y1="200" x2="300" y2="470" stroke="#ffffff" stroke-width="6" stroke-opacity="0.7"/>'
            f'<line x1="160" y1="250" x2="440" y2="250" stroke="#ffffff" stroke-width="6" stroke-opacity="0.7"/>'
            f'<path d="M160 250 L108 350 A60 60 0 0 0 212 350 Z" fill="#ffffff" fill-opacity="0.2" stroke="#ffffff" stroke-opacity="0.6"/>'
            f'<path d="M440 250 L388 340 A60 60 0 0 0 492 340 Z" fill="#ffffff" fill-opacity="0.34" stroke="#ffffff" stroke-opacity="0.6"/>'
            f'<circle cx="300" cy="200" r="12" fill="#ffffff"/>'
            f'<rect x="266" y="470" width="68" height="16" rx="5" fill="#ffffff" opacity="0.85"/>'
            f'{coins}'
            f'<text x="160" y="430" text-anchor="middle" font-family="serif" font-size="64" font-weight="800" '
            f'fill="#ffffff" opacity="0.9">₹</text></svg>', True)


COVER_RENDERERS = {
    "gateway": _scene_gateway, "compass": _scene_compass, "strata": _scene_strata,
    "boomerang": _scene_boomerang, "pulse": _scene_pulse, "atlas": _scene_atlas,
    "prism": _scene_prism, "voiceprint": _scene_voiceprint, "hourglass": _scene_hourglass,
    "lattice": _scene_lattice, "mint": _scene_mint,
}


# ─────────────────────────────────────────────────────────────────────────────
# Compact inner-page emblems (accent on transparent → reads on white body)
# ─────────────────────────────────────────────────────────────────────────────
def _emb_gateway(a, d):
    return (_EMB_OPEN + f'<path d="M40 84 L40 40 Q80 8 120 40 L120 84" fill="none" stroke="{a}" stroke-width="6"/>'
            f'<rect x="76" y="22" width="8" height="62" rx="3" fill="{d}"/></svg>')


def _emb_compass(a, d):
    sp = "".join(f'<line x1="80" y1="48" x2="{80+34*math.cos(i*math.pi/4):.0f}" y2="{48+34*math.sin(i*math.pi/4):.0f}" '
                 f'stroke="{a}" stroke-opacity="0.4"/>' for i in range(8))
    return (_EMB_OPEN + f'<circle cx="80" cy="48" r="36" fill="none" stroke="{a}" stroke-width="3"/>{sp}'
            f'<path d="M80 18 L88 48 L80 42 L72 48 Z" fill="{d}"/></svg>')


def _emb_strata(a, d):
    r = "".join(f'<circle cx="80" cy="48" r="{8+i*9}" fill="none" stroke="{a}" stroke-opacity="{0.7-i*0.13:.2f}"/>' for i in range(5))
    return _EMB_OPEN + r + f'<circle cx="80" cy="48" r="5" fill="{d}"/></svg>'


def _emb_boomerang(a, d):
    return (_EMB_OPEN + f'<rect x="40" y="20" width="40" height="60" rx="4" fill="none" stroke="{a}" stroke-width="3"/>'
            f'<path d="M30 84 Q8 50 80 44" fill="none" stroke="{a}" stroke-width="3" stroke-dasharray="2 6"/>'
            f'<path d="M70 38 l12 6 l-10 8 z" fill="{a}"/></svg>')


def _emb_pulse(a, d):
    return (_EMB_OPEN + f'<polyline points="8,52 50,52 64,24 80,76 96,40 112,52 152,52" fill="none" '
            f'stroke="{a}" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _emb_atlas(a, d):
    return (_EMB_OPEN + "".join(f'<rect x="{20+i*26}" y="{84-h}" width="18" height="{h}" rx="3" '
            f'fill="{a}" fill-opacity="{0.5+i*0.13:.2f}"/>' for i, h in enumerate([28, 50, 38, 66]))
            + f'<line x1="12" y1="84" x2="148" y2="84" stroke="{d}" stroke-width="2"/></svg>')


def _emb_prism(a, d):
    return (_EMB_OPEN + f'<line x1="6" y1="34" x2="64" y2="48" stroke="{d}" stroke-width="3"/>'
            f'<path d="M70 28 L88 64 L52 64 Z" fill="{a}" fill-opacity="0.3" stroke="{a}" stroke-width="2"/>'
            f'<line x1="80" y1="48" x2="152" y2="28" stroke="#16a34a" stroke-width="3"/>'
            f'<line x1="80" y1="48" x2="152" y2="48" stroke="{a}" stroke-width="3"/>'
            f'<line x1="80" y1="48" x2="152" y2="68" stroke="#dc2626" stroke-width="3"/></svg>')


def _emb_voiceprint(a, d):
    bars = ""
    for i in range(16):
        ang = i * math.pi / 8
        h = 12 + 16 * abs(math.sin(i * 1.4))
        x1, y1 = 80 + 22 * math.cos(ang), 48 + 22 * math.sin(ang)
        x2, y2 = 80 + (22 + h) * math.cos(ang), 48 + (22 + h) * math.sin(ang)
        bars += f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="{a}" stroke-width="2.5" stroke-linecap="round"/>'
    return _EMB_OPEN + f'<circle cx="80" cy="48" r="18" fill="{a}" fill-opacity="0.2"/>' + bars + "</svg>"


def _emb_hourglass(a, d):
    return (_EMB_OPEN + f'<path d="M50 18 L110 18 L84 48 L110 78 L50 78 L76 48 Z" fill="none" stroke="{a}" stroke-width="4"/>'
            f'<path d="M55 22 L105 22 L80 48 Z" fill="{a}" fill-opacity="0.5"/>'
            f'<path d="M60 74 L100 74 L80 50 Z" fill="{d}" fill-opacity="0.6"/></svg>')


def _emb_lattice(a, d):
    cells = ""
    for i in range(4):
        x = 18 + i * 36
        col = "#16a34a" if i != 2 else "#dc2626"
        cells += f'<rect x="{x}" y="34" width="28" height="28" rx="6" fill="{col}" fill-opacity="0.25" stroke="{col}" stroke-width="2"/>'
        if i != 2:
            cells += f'<path d="M{x+8} 48 l5 5 l8 -10" fill="none" stroke="#16a34a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
    return _EMB_OPEN + cells + "</svg>"


def _emb_mint(a, d):
    return (_EMB_OPEN + f'<line x1="80" y1="22" x2="80" y2="74" stroke="{a}" stroke-width="3"/>'
            f'<line x1="44" y1="34" x2="116" y2="34" stroke="{a}" stroke-width="3"/>'
            f'<path d="M44 34 L30 58 A18 18 0 0 0 58 58 Z" fill="{a}" fill-opacity="0.25" stroke="{a}" stroke-width="1.5"/>'
            f'<path d="M116 34 L102 54 A18 18 0 0 0 130 54 Z" fill="{d}" fill-opacity="0.3" stroke="{d}" stroke-width="1.5"/>'
            f'<circle cx="80" cy="22" r="5" fill="{a}"/></svg>')


_EMBLEMS = {
    "gateway": _emb_gateway, "compass": _emb_compass, "strata": _emb_strata,
    "boomerang": _emb_boomerang, "pulse": _emb_pulse, "atlas": _emb_atlas,
    "prism": _emb_prism, "voiceprint": _emb_voiceprint, "hourglass": _emb_hourglass,
    "lattice": _emb_lattice, "mint": _emb_mint,
}


# ─────────────────────────────────────────────────────────────────────────────
# cover
# ─────────────────────────────────────────────────────────────────────────────
def _cover(meta: dict, summary: dict, period: str, n: int) -> str:
    a, d = meta["accent"], meta["deep"]
    bg, scene, dark = COVER_RENDERERS.get(meta["motif"], _scene_gateway)(a, d)
    ink = "#ffffff" if dark else "#1a1208"
    sub = "rgba(255,255,255,0.84)" if dark else "#5a4a33"
    chip_bg = "rgba(255,255,255,0.14)" if dark else "rgba(0,0,0,0.05)"
    chip_bd = "rgba(255,255,255,0.32)" if dark else "rgba(0,0,0,0.16)"
    crest_bg = "rgba(255,255,255,0.18)" if dark else d
    tiles = summary.get("tiles", [])
    hero = tiles[0] if tiles else ("Records", n, "int")
    now = datetime.now().strftime("%d %b %Y")
    ov = _DARK_OVERLAY if dark else _LIGHT_OVERLAY
    return f"""
    <section class="cover" style="background:{bg}">
      {_img(scene, "cv-bg")}
      <div class="cv-overlay" style="background:{ov}"></div>
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
        <span>{_COMPANY} · Exit Intelligence</span><span>Generated {now} · {n} record(s)</span>
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


def _pretty(v) -> str:
    s = str(v)
    if " " in s or "/" in s or "—" in s:
        return s
    return s.replace("_", " ").title()


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
    emblem = _EMBLEMS.get(meta["motif"], _emb_gateway)(a, d)
    return f"""
    <section class="{'body wide' if wide else 'body'}">
      <div class="bd-head">
        <div class="bd-bar" style="background:linear-gradient(180deg,{a},{d})"></div>
        <div class="bd-lead">
          <span class="bd-kick" style="color:{d}">FOURRECK · EXIT · {_esc(meta['group'].upper())}</span>
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
  @bottom-left { content: "Fourreck HRMS · Exit Management"; font-size: 7.5pt; color: #9a8e78; }
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


def render_pdf(db: Session, key: str, *, date_from=None, date_to=None, department_id=None,
               period_label: str = "") -> bytes:
    from app.utils.gtk_bootstrap import ensure_gtk_runtime
    ensure_gtk_runtime()
    from weasyprint import HTML  # lazy — needs GTK on PATH first

    meta = report_meta(key)
    rows = fetch_rows(db, key, date_from=date_from, date_to=date_to, department_id=department_id)
    summary = shape_summary(db, key, rows)
    period = period_label or "All time"
    cover = _cover(meta, summary, period, len(rows))
    body = _body(key, rows, summary, meta, period)
    html = (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style>{_BASE_CSS}</style></head><body>{cover}{body}</body></html>')
    return HTML(string=html).write_pdf()

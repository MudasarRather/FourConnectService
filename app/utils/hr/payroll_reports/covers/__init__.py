"""Per-motif PDF cover designs for HR Payroll Reports.

Each report's ``REPORT_META[key]['motif']`` maps to a module in this package
exposing ``render(meta, summary, period, shaped_count=None) -> str`` (an HTML
``<section class="cover ...">``). The loader below wires them into
``COVER_RENDERERS`` defensively: any motif whose module is missing or broken
falls back to the shared generic cover, so the export endpoint never 500s on a
half-built package.

Motif → module map (module names use underscores; motifs may use hyphens).
"""
from __future__ import annotations

import importlib

from ..common import (
    COMPANY, esc, inr, inr_compact, kpi_tiles_html, period_band_html, now_stamp,
)

# motif slug → module filename (without .py)
_MOTIF_MODULES = {
    "ledger": "ledger",
    "editorial": "editorial",
    "seal": "seal",
    "govt-pf": "govt_pf",
    "govt-esi": "govt_esi",
    "slab": "slab",
    "dossier": "dossier",
    "industrial": "industrial",
    "bulletin": "bulletin",
    "postcard": "postcard",
    "blueprint": "blueprint",
    "ticket": "ticket",
    "certificate": "certificate",
}


def _summary_tiles(summary: dict, accent: str) -> list:
    """Pick up to 4 sensible KPI tiles from whatever the report's summary holds."""
    tiles = []
    if "rows" in summary or "headcount" in summary:
        tiles.append(("Records", str(summary.get("rows", summary.get("headcount", 0))), accent))
    if "employees" in summary:
        tiles.append(("Employees", str(summary["employees"]), "#1a1410"))
    # money figure — prefer net, then gross, then annual ctc, then ytd
    for label, key, color in (
        ("Net Pay", "net", "#047857"),
        ("Annual CTC", "annual_ctc", "#0891b2"),
        ("YTD Net", "ytd_net", "#047857"),
        ("Additions", "additions", "#047857"),
    ):
        if summary.get(key):
            tiles.append((label, inr_compact(summary[key]), color))
            break
    for label, key, color in (
        ("Deductions", "deductions", "#b91c1c"),
        ("Gross", "gross", "#b8860b"),
        ("YTD TDS", "ytd_tds", "#b91c1c"),
        ("Monthly CTC", "monthly_ctc", "#b8860b"),
    ):
        if summary.get(key):
            tiles.append((label, inr_compact(summary[key]), color))
            break
    return tiles[:4]


def _fallback(meta: dict, summary: dict, period: dict, shaped_count: int | None = None) -> str:
    """A clean, brandable generic cover — used until a motif module ships."""
    accent = meta["accent"]
    soft = meta["accent_soft"]
    deep = meta["accent_deep"]
    rows = shaped_count if shaped_count is not None else summary.get("rows", 0)
    tiles = [("Records in report", str(rows), accent)] + _summary_tiles(summary, accent)
    # de-dupe the first tile if _summary_tiles also added a Records tile
    seen, uniq = set(), []
    for t in tiles:
        if t[0] in seen:
            continue
        seen.add(t[0])
        uniq.append(t)
    return f"""
    <section class="cover">
        <div class="cover-band-top" style="background:linear-gradient(90deg,{accent},{deep})"></div>
        <div class="cover-band-bottom" style="background:{accent}"></div>
        <div class="cover-brand">
            <span class="crest" style="background:{accent};color:#fff">{esc(meta.get('icon', 'F'))}</span>
            <div class="company">{esc(COMPANY['legal'].upper())}</div>
        </div>
        <div class="cover-eyebrow" style="color:{accent}">PAYROLL · {esc(meta.get('group', '').upper())}</div>
        <h1 class="cover-title" style="color:#1a1410">{esc(meta['name'])}</h1>
        <p class="cover-subtitle">{esc(meta['subtitle'])}</p>
        {period_band_html(period, accent, soft, deep)}
        <div class="cover-generated">Generated {now_stamp()}</div>
        {kpi_tiles_html(uniq[:4])}
        <div class="cover-footer">
            <div class="legal">{esc(COMPANY['legal'])} · {esc(COMPANY['address_1'])}, {esc(COMPANY['address_2'])}</div>
            <div class="confidential">Confidential · Internal use only</div>
        </div>
    </section>
    """


def _load() -> dict:
    renderers = {}
    for motif, modname in _MOTIF_MODULES.items():
        try:
            mod = importlib.import_module(f"{__name__}.{modname}")
            fn = getattr(mod, "render", None)
            renderers[motif] = fn if callable(fn) else _fallback
        except Exception:  # noqa: BLE001 — never let one broken cover sink exports
            renderers[motif] = _fallback
    renderers.setdefault("ledger", _fallback)
    return renderers


COVER_RENDERERS = _load()

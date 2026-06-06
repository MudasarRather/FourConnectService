"""Server-side SLA agreement PDF rendering (WeasyPrint).

Public API:
    from app.utils.sla_pdf import render_sla_pdf
"""
from .pdf import render_sla_pdf

__all__ = ["render_sla_pdf"]

"""Server-side Project Handover PDF rendering (WeasyPrint).

Public API:
    from app.utils.handover_pdf import render_handover_pdf
"""
from .pdf import render_handover_pdf

__all__ = ["render_handover_pdf"]

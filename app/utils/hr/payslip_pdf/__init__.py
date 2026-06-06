"""Payslip PDF rendering — public API.

    from app.utils.hr.payslip_pdf import render_payslip_pdf

WeasyPrint is imported lazily inside the render function (after the GTK
bootstrap) so importing this package never shells out to libpango at import
time. See app/utils/hr/attendance_reports for the same pattern.
"""
from app.utils.hr.payslip_pdf.pdf import render_payslip_pdf, amount_in_words

__all__ = ["render_payslip_pdf", "amount_in_words"]

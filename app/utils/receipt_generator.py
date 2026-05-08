"""
Payment Receipt PDF Generator
Generates PDF receipts for project payments using reportlab.
Matches the client-side jsPDF design exactly.
"""
import os
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.pdfgen import canvas

# Colors matching client-side
COLORS = {
    'primary': colors.HexColor('#282828'),      # Soft Black
    'secondary': colors.HexColor('#646464'),    # Grey
    'brand': colors.HexColor('#F59E0B'),        # Amber/Gold
    'brand_light': colors.HexColor('#FBBF24'),  # Lighter Amber
    'light_bg': colors.HexColor('#FAFAFA'),
    'divider': colors.HexColor('#E6E6E6'),
    'green': colors.HexColor('#16A34A'),        # Status Green
    'red': colors.HexColor('#DC2626'),          # Status Red
}

def format_indian_currency(amount):
    """Format number in Indian style (12,34,567.89)"""
    if amount is None:
        return "0.00"
    
    amount = float(amount)
    is_negative = amount < 0
    amount = abs(amount)
    
    # Split integer and decimal
    integer_part = int(amount)
    decimal_part = round((amount - integer_part) * 100)
    
    # Format integer with Indian grouping (last 3, then 2s)
    s = str(integer_part)
    if len(s) > 3:
        # First group of 3 from right
        result = s[-3:]
        s = s[:-3]
        # Then groups of 2
        while s:
            result = s[-2:] + ',' + result
            s = s[:-2]
    else:
        result = s
    
    formatted = f"{result}.{decimal_part:02d}"
    return f"({formatted})" if is_negative else formatted

def generate_receipt_pdf(payment, project, milestones=None, creator=None, output_path=None):
    """
    Generate a payment receipt PDF matching the client-side jsPDF design.
    
    Args:
        payment: ProjectPayment object
        project: Project object
        milestones: List of related Milestone objects
        creator: User object who created the payment
        output_path: Path to save the PDF. If None, returns bytes.
    
    Returns:
        Path to saved PDF or BytesIO buffer
    """
    if milestones is None:
        milestones = []
    
    width, height = A4
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    
    # === 1. HEADER WITH LOGO ===
    # Logo - Fourconnect geometric logo (4 rounded squares)
    logo_x = 20 * mm
    logo_y = height - 35 * mm
    logo_size = 12 * mm
    gap = 0.5 * mm
    box_size = (logo_size / 2) - gap
    radius = 1 * mm
    
    # Draw 4 rounded rectangles for logo
    c.setStrokeColor(COLORS['brand'])
    c.setLineWidth(2)
    
    # Top-left (stroke)
    c.roundRect(logo_x, logo_y, box_size, box_size, radius)
    
    # Top-right (lighter stroke)
    c.setStrokeColor(COLORS['brand_light'])
    c.roundRect(logo_x + box_size + (gap * 2), logo_y, box_size, box_size, radius)
    
    # Bottom-left (lighter stroke)
    c.roundRect(logo_x, logo_y - box_size - (gap * 2), box_size, box_size, radius)
    
    # Bottom-right (filled)
    c.setFillColor(COLORS['brand'])
    c.roundRect(logo_x + box_size + (gap * 2), logo_y - box_size - (gap * 2), box_size, box_size, radius, fill=1)
    
    # Brand Name
    c.setFont('Helvetica-Bold', 16)
    c.setFillColor(COLORS['primary'])
    c.drawString(logo_x + 18 * mm, logo_y + 2 * mm, 'Fourconnect')
    
    # Title - Right aligned
    c.setFont('Helvetica-Bold', 20)
    c.drawRightString(width - 20 * mm, height - 25 * mm, 'PAYMENT RECEIPT')
    
    # === 2. META DATA (Receipt No, Date, Status) ===
    meta_y = height - 50 * mm
    
    # Labels
    c.setFont('Helvetica', 8)
    c.setFillColor(COLORS['secondary'])
    c.drawString(20 * mm, meta_y, 'RECEIPT NO')
    c.drawString(70 * mm, meta_y, 'DATE')
    c.drawString(140 * mm, meta_y, 'STATUS')
    
    # Values
    c.setFont('Helvetica-Bold', 10)
    c.setFillColor(COLORS['primary'])
    
    payment_id = payment.payment_id or str(payment.id)[:8].upper()
    payment_date = payment.payment_date.strftime('%d/%m/%Y') if payment.payment_date else 'N/A'
    status = (payment.status or 'Completed').upper()
    
    c.drawString(20 * mm, meta_y - 5 * mm, f'#{payment_id}')
    c.drawString(70 * mm, meta_y - 5 * mm, payment_date)
    
    # Status with color
    if status in ['COMPLETED', 'RECEIVED']:
        c.setFillColor(COLORS['green'])
    elif status in ['PENDING', 'IN TRANSIT']:
        c.setFillColor(COLORS['brand'])
    elif status == 'FAILED':
        c.setFillColor(COLORS['red'])
    else:
        c.setFillColor(COLORS['secondary'])
    
    c.drawString(140 * mm, meta_y - 5 * mm, status)
    
    # Divider line
    c.setStrokeColor(COLORS['divider'])
    c.setLineWidth(0.3)
    c.line(20 * mm, meta_y - 12 * mm, width - 20 * mm, meta_y - 12 * mm)
    
    # === 3. PROJECT & PAYMENT TO ===
    addr_y = meta_y - 25 * mm
    
    # Project Details
    c.setFont('Helvetica', 7)
    c.setFillColor(COLORS['secondary'])
    c.drawString(20 * mm, addr_y, 'PROJECT DETAILS')
    
    c.setFont('Helvetica-Bold', 10)
    c.setFillColor(COLORS['primary'])
    c.drawString(20 * mm, addr_y - 5 * mm, project.name if project else 'Unknown Project')
    
    c.setFont('Helvetica', 9)
    c.setFillColor(COLORS['secondary'])
    c.drawString(20 * mm, addr_y - 10 * mm, project.code if project else '')
    
    # Payment To (Vendor)
    c.setFont('Helvetica', 7)
    c.setFillColor(COLORS['secondary'])
    c.drawString(110 * mm, addr_y, 'PAYMENT TO')
    
    c.setFont('Helvetica-Bold', 10)
    c.setFillColor(COLORS['primary'])
    c.drawString(110 * mm, addr_y - 5 * mm, payment.vendor_name or '')
    
    c.setFont('Helvetica', 9)
    c.setFillColor(COLORS['secondary'])
    if payment.contract_work_order_no:
        c.drawString(110 * mm, addr_y - 10 * mm, f'WO: Order No {payment.contract_work_order_no}')
    
    if payment.invoice_number:
        invoice_date = payment.invoice_date.strftime('%Y-%m-%d') if payment.invoice_date else 'No Date'
        c.drawString(110 * mm, addr_y - 15 * mm, f'Inv: {payment.invoice_number} ({invoice_date})')
    
    # === 4. FINANCIAL TABLE ===
    currency = payment.currency or 'INR'
    table_y = addr_y - 30 * mm
    
    # Import currency conversion (same as used by milestone API)
    from app.utils.currency import get_rate
    
    # Prepare data
    table_data = [['DESCRIPTION', 'AMOUNT']]
    
    # Milestones - use converted amounts (same logic as milestone API)
    # Use get_rate to convert each milestone from its currency to payment/project currency
    if milestones:
        for m in milestones:
            original_amount = float(getattr(m, 'budget_amount', 0) or 0)
            milestone_currency = getattr(m, 'currency', 'USD') or 'USD'
            
            # Convert milestone amount to payment currency using get_rate (live rate)
            rate = get_rate(milestone_currency, currency)
            converted_amount = original_amount * rate
            
            table_data.append([f'Milestone: {m.name}', f'{currency} {format_indian_currency(converted_amount)}'])
    else:
        amount_paid = float(payment.amount_paid or 0)
        table_data.append([payment.payment_category or 'General Payment', f'{currency} {format_indian_currency(amount_paid)}'])
    
    # Deductions
    gross = float(payment.invoice_amount_gross or 0)
    
    # TDS
    tds_percent = float(payment.tds_percent or 0)
    if tds_percent > 0:
        tds_amount = gross * tds_percent / 100
        table_data.append(['Less: TDS Deduction', f'({currency} {format_indian_currency(tds_amount)})'])
    
    # Other deductions
    other_percent = float(payment.other_deductions or 0)
    if other_percent > 0:
        other_amount = gross * other_percent / 100
        desc = payment.other_deductions_desc or ''
        table_data.append([f'Less: Other ({desc})', f'({currency} {format_indian_currency(other_amount)})'])
    
    # Total row
    net_amount = float(payment.net_receivable_amount or payment.amount_paid or 0)
    table_data.append(['TOTAL PAID', f'{currency} {format_indian_currency(net_amount)}'])
    
    # Draw table
    row_height = 12 * mm
    col_widths = [120 * mm, 50 * mm]
    table_width = sum(col_widths)
    
    for i, row in enumerate(table_data):
        row_y = table_y - (i * row_height)
        
        # Background
        if i == 0:  # Header
            c.setFillColor(COLORS['light_bg'])
            c.rect(20 * mm, row_y - row_height, table_width, row_height, fill=1, stroke=0)
        elif i == len(table_data) - 1:  # Footer (Total)
            c.setFillColor(COLORS['brand'])
            c.rect(20 * mm, row_y - row_height, table_width, row_height, fill=1, stroke=0)
        
        # Border
        c.setStrokeColor(COLORS['brand'])
        c.setLineWidth(0.5)
        c.rect(20 * mm, row_y - row_height, table_width, row_height)
        c.line(20 * mm + col_widths[0], row_y - row_height, 20 * mm + col_widths[0], row_y)
        
        # Text
        if i == 0:  # Header
            c.setFont('Helvetica-Bold', 8)
            c.setFillColor(COLORS['secondary'])
        elif i == len(table_data) - 1:  # Footer
            c.setFont('Helvetica-Bold', 10)
            c.setFillColor(colors.white)
        else:
            c.setFont('Helvetica', 9)
            c.setFillColor(COLORS['primary'])
        
        c.drawString(25 * mm, row_y - row_height + 4 * mm, row[0])
        c.drawRightString(20 * mm + table_width - 5 * mm, row_y - row_height + 4 * mm, row[1])
    
    final_table_y = table_y - (len(table_data) * row_height)
    
    # === 5. PAYMENT DETAILS BOX ===
    box_y = final_table_y - 20 * mm
    box_height = 30 * mm
    
    c.setStrokeColor(COLORS['divider'])
    c.setLineWidth(0.5)
    c.roundRect(20 * mm, box_y - box_height, 170 * mm, box_height, 2 * mm)
    
    # Labels
    c.setFont('Helvetica', 7)
    c.setFillColor(COLORS['secondary'])
    c.drawString(30 * mm, box_y - 8 * mm, 'PAYMENT METHOD')
    c.drawString(100 * mm, box_y - 8 * mm, 'REFERENCE / UTR')
    
    # Values
    c.setFont('Helvetica-Bold', 9)
    c.setFillColor(COLORS['primary'])
    c.drawString(30 * mm, box_y - 14 * mm, payment.payment_mode or 'Unknown')
    c.drawString(100 * mm, box_y - 14 * mm, payment.utr_transaction_ref or payment.transaction_ref or '—')
    
    # Bank details
    bank_detail = payment.bank_name or ''
    if payment.account_number:
        bank_detail += f' •••• {str(payment.account_number)[-4:]}'
    if payment.cheque_no:
        if bank_detail:
            bank_detail += f' | Cheque: {payment.cheque_no}'
        else:
            bank_detail = f'Cheque No: {payment.cheque_no}'
    
    if bank_detail:
        c.setFont('Helvetica', 8)
        c.drawString(30 * mm, box_y - 20 * mm, bank_detail)
    
    # === 6. DIGITAL SIGNATURE ===
    sig_y = box_y - box_height - 15 * mm
    
    c.setFont('Helvetica', 7)
    c.setFillColor(COLORS['secondary'])
    c.drawString(20 * mm, sig_y, 'DIGITALLY AUTHORIZED BY')
    
    # Author name
    author_name = 'System Admin'
    if creator:
        author_name = creator.full_name or f'{getattr(creator, "first_name", "") or ""} {getattr(creator, "last_name", "") or ""}'.strip() or 'System Admin'
    
    c.setFont('Helvetica-Bold', 10)
    c.setFillColor(COLORS['primary'])
    c.drawString(20 * mm, sig_y - 6 * mm, author_name)
    
    # Fake hash
    import time
    fake_hash = f'SIG_{str(payment.id)[:8].upper()}_{hex(int(time.time()))[2:].upper()}_SECURE'
    c.setFont('Courier', 7)
    c.setFillColor(colors.HexColor('#969696'))
    c.drawString(20 * mm, sig_y - 12 * mm, fake_hash)
    
    # Verified badge (circle)
    hash_width = c.stringWidth(fake_hash, 'Courier', 7)
    c.setFillColor(COLORS['brand'])
    c.circle(20 * mm + hash_width + 5 * mm, sig_y - 10 * mm, 2 * mm, fill=1)
    
    # === 7. FOOTER ===
    footer_y = 15 * mm
    
    c.setStrokeColor(COLORS['brand'])
    c.setLineWidth(0.5)
    c.line(80 * mm, footer_y, 130 * mm, footer_y)
    
    c.setFont('Helvetica', 7)
    c.setFillColor(COLORS['secondary'])
    c.drawCentredString(width / 2, footer_y - 5 * mm, 'Powered by Fourreck')
    
    # Save
    c.save()
    
    # Write to file or return buffer
    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(buffer.getvalue())
        return output_path
    
    buffer.seek(0)
    return buffer

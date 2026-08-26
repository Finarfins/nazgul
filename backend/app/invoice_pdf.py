from __future__ import annotations
from io import BytesIO
import json
import qrcode
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,KeepTogether
from pathlib import Path
from app.invoice_engines import money
from app.pdf_fonts import PDF_FONT, PDF_FONT_BOLD, register_pdf_fonts

register_pdf_fonts()

def build_invoice_pdf(invoice:dict,items:list[dict])->bytes:
    out=BytesIO(); number=invoice["invoice_number"]
    doc=SimpleDocTemplate(out,pagesize=A4,rightMargin=14*mm,leftMargin=14*mm,topMargin=15*mm,bottomMargin=16*mm,
                          title=f"Fatura {number}",author="Harman Zamanı")
    styles=getSampleStyleSheet()
    for style in styles.byName.values(): style.fontName=PDF_FONT
    styles.add(ParagraphStyle(name="Right",parent=styles["Normal"],alignment=TA_RIGHT,fontName=PDF_FONT))
    company=json.loads(invoice["company_snapshot"]); customer=json.loads(invoice["customer_snapshot"]); machine=json.loads(invoice["machine_snapshot"])
    work=json.loads(invoice["work_order_snapshot"]); totals=json.loads(invoice["totals_snapshot"]); warranty=json.loads(invoice["warranty_snapshot"])
    qr=qrcode.make(f"INVOICE:{number}|TOTAL:{totals['grand_total']}|CURRENCY:{invoice['currency']}"); qrbuf=BytesIO(); qr.save(qrbuf,format="PNG"); qrbuf.seek(0)
    header=Table([[Paragraph(f"<b>{company.get('name','Harman Zamanı')}</b><br/>Vergi No: {company.get('tax_number') or '-'}",styles["Normal"]),
                   Paragraph(f"<b>FATURA</b><br/>{number}<br/>{invoice['created_at']}",styles["Right"]) ]],colWidths=[110*mm,65*mm])
    header.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LINEBELOW",(0,0),(-1,-1),1,colors.HexColor("#2f6b3b")),("BOTTOMPADDING",(0,0),(-1,-1),8)]))
    story=[header,Spacer(1,6*mm),Table([[Paragraph(f"<b>Müşteri</b><br/>{customer['name']}",styles["Normal"]),
        Paragraph(f"<b>Makine</b><br/>{machine['brand']} {machine['model']}<br/>Seri: {machine.get('serial_number') or '-'}",styles["Normal"]),
        Paragraph(f"<b>İş Emri</b><br/>{work['work_order_no']}<br/>Durum: {work['status']}",styles["Normal"]) ]],colWidths=[60*mm,60*mm,55*mm]),Spacer(1,5*mm)]
    rows=[["Açıklama","Miktar","Birim Fiyat","İndirim","KDV","Toplam"]]
    for item in items:
        rows.append([item["description"],str(item["quantity"]),str(item["unit_price"]),str(item["discount_amount"]),str(item["tax_amount"]),str(item["total"])])
    table=Table(rows,repeatRows=1,colWidths=[65*mm,20*mm,28*mm,24*mm,20*mm,28*mm])
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#2f6b3b")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID",(0,0),(-1,-1),.3,colors.grey),("FONTNAME",(0,0),(-1,0),PDF_FONT_BOLD),("FONTNAME",(0,1),(-1,-1),PDF_FONT),("ALIGN",(1,1),(-1,-1),"RIGHT"),
        ("FONTSIZE",(0,0),(-1,-1),8),("VALIGN",(0,0),(-1,-1),"TOP")]))
    story += [table,Spacer(1,5*mm)]
    subtotal=money(totals.get("labor",0))+money(totals.get("parts",0))
    summary=Table([[Image(qrbuf,25*mm,25*mm),code128.Code128(number,barHeight=12*mm,barWidth=.35),
        Paragraph(f"Ara Toplam: {subtotal} {invoice['currency']}<br/>"
                  f"Global İndirim: {totals.get('global_discount',0)}<br/><b>Genel Toplam: {totals['grand_total']} {invoice['currency']}</b><br/>"
                  f"Müşteri: {totals['customer_amount']} | Garanti: {totals['warranty_amount']}<br/>Garanti Türü: {warranty['type']}",styles["Right"]) ]],colWidths=[32*mm,65*mm,78*mm])
    story += [KeepTogether(summary),Spacer(1,4*mm),Paragraph(f"<b>Ödeme Koşulları:</b> {invoice.get('payment_terms') or '-'}",styles["Normal"]),
              Paragraph(f"<b>Notlar:</b> {invoice.get('notes') or '-'}",styles["Normal"]),Spacer(1,4*mm),
              Paragraph(f"Hazırlayan: {json.loads(invoice['technician_snapshot']).get('display_name') or '-'}",styles["Normal"])]
    def footer(canvas,doc):
        canvas.saveState(); canvas.setFont(PDF_FONT,8); canvas.setFillColor(colors.grey)
        canvas.drawString(14*mm,9*mm,"Harman Zamanı - Elektronik fatura çıktısı")
        canvas.drawRightString(196*mm,9*mm,f"Sayfa {doc.page}"); canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer); return out.getvalue()

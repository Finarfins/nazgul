from __future__ import annotations
import json
from decimal import Decimal
from typing import Any
from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .activity_log import format_money_tr, log_request_activity
from .auth import utcnow
from .billing_service import SERVICE_LABOR_VAT_RATE, build_invoice_summary
from .invoice_numbering import next_invoice_number
from .invoice_schemas import InvoiceGenerateRequest
from .money import money, percentage, quantity
from .service_receivable_engine import reconcile_service_receivable

# Re-exported: the constant moved to billing_service in H91 (the billing preview
# prices labor with it too); callers keep importing it from here.
__all__=["SERVICE_LABOR_VAT_RATE","generate_invoice","log_invoice_action"]

def _dump(value: Any)->str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,default=str)

def log_invoice_action(db:Session,request:Request,cid:int,invoice_id:int,action:str,reason:str|None=None,metadata:dict|None=None,*,history:bool=False)->None:
    user=getattr(request.state,"user",{}) or {}; params={"cid":cid,"invoice_id":invoice_id,"action":action,"uid":user.get("id"),"username":user.get("username"),
        "ip":request.client.host if request.client else None,"reason":reason,"metadata":_dump(metadata or {}),"now":utcnow()}
    db.execute(text("""INSERT INTO invoice_audit(company_id,invoice_id,action,actor_user_id,actor_username,ip_address,reason,metadata_json,created_at)
        VALUES(:cid,:invoice_id,:action,:uid,:username,:ip,:reason,:metadata,:now)"""),params)
    if history:
        db.execute(text("""INSERT INTO invoice_history(company_id,invoice_id,action,actor_user_id,actor_username,ip_address,reason,metadata_json,created_at)
            VALUES(:cid,:invoice_id,:action,:uid,:username,:ip,:reason,:metadata,:now)"""),params)

def _labor_description(labor:dict,entry:dict)->str:
    # Header source keeps the historical wording so existing invoices and the
    # PDF read identically; line source names the line it came from.
    if labor.get("source")!="lines": return "Servis İşçiliği"
    return f"Servis İşçiliği #{entry['line_id']}"

def generate_invoice(db:Session,request:Request,cid:int,payload:InvoiceGenerateRequest)->int:
    summary=build_invoice_summary(db,cid,payload.work_order_id,discount_type=payload.global_discount_type,
        discount_value=payload.global_discount_value)
    company=db.execute(text("SELECT id,name,tax_number FROM companies WHERE id=:cid"),{"cid":cid}).mappings().one()
    technician=db.execute(text("""SELECT u.id,u.username,u.display_name FROM work_orders wo JOIN app_users u ON u.id=wo.technician_id
        WHERE wo.id=:id AND wo.company_id=:cid"""),{"id":payload.work_order_id,"cid":cid}).mappings().one()
    parts=db.execute(text("""SELECT wp.*,p.name product_name,p.product_code,p.barcode FROM work_order_parts wp
        JOIN products p ON p.id=wp.product_id AND p.company_id=wp.company_id WHERE wp.work_order_id=:id AND wp.company_id=:cid
        AND wp.line_status <> 'RETURNED' ORDER BY wp.id"""),
        {"id":payload.work_order_id,"cid":cid}).mappings().all()
    coverage=percentage(summary["warranty"]["coverage_percent"])
    labor=summary["labor"]
    # The money is the billing preview's own (H91): build_invoice_summary priced
    # every line through billing_service.price_service_lines with this document
    # discount — labor at SERVICE_LABOR_VAT_RATE (H79), the discount allocated
    # across the line MATRAH and each line's tax recomputed on base - share
    # (H80), warranty split per item. This function only attaches descriptions
    # and source snapshots; it keeps no copy of the arithmetic, so preview
    # grand_total == invoice grand_total by construction.
    # One LABOR item per billable labor entry. With the v1 header source there
    # is exactly one entry; with approved labor lines each keeps its own frozen
    # rate instead of being flattened into a blended rate that would not satisfy
    # qty*price==total.
    if summary["part_ids"]!=[int(part["id"]) for part in parts]:
        raise HTTPException(409,"İş emri parçaları fatura kesilirken değişti; lütfen tekrar deneyin.")
    lines=[("LABOR",_labor_description(labor,entry),entry["hours"],entry["hourly_rate"],0,
            _dump({**{k:v for k,v in labor.items() if k!="entries"},**entry})) for entry in labor["entries"]]
    lines+=[("PART",part["product_name"],part["quantity"],part["unit_price"],part["discount"],_dump(dict(part))) for part in parts]
    items=[{"item_type":item_type,"description":description,"qty":qty,"unit_price":unit_price,"discount_value":discount_value,
            "tax_rate":priced["tax_rate"],"discount_amount":priced["discount_amount"],"tax_amount":priced["tax_amount"],
            "total":priced["total"],"customer":priced["customer"],"company":priced["company"],"source":source}
           for (item_type,description,qty,unit_price,discount_value,source),priced in zip(lines,summary["pricing_items"],strict=True)]
    grand_total=summary["totals"]["grand_total"]
    # "global_discount" stays what the payable dropped by (VAT-inclusive), so
    # grand_total + global_discount is the pre-discount gross. "global_discount_base"
    # (added in F9-5-fix round 2) is the discount taken off the MATRAH — the
    # entered FIXED amount, or the percent of the matrah — which the PDF prints
    # as "İskonto" (KDVK m.25: iskonto is deducted before VAT). Both, and
    # labor_tax (H91), come from the summary.
    totals={**summary["totals"],"customer_amount":summary["warranty"]["customer_amount"],
            "warranty_amount":summary["warranty"]["warranty_amount"]}
    number=next_invoice_number(db,cid,payload.branch_prefix)
    user=getattr(request.state,"user",{}) or {}; now=utcnow()
    try:
        invoice_id=int(db.execute(text("""INSERT INTO invoices(company_id,work_order_id,invoice_number,invoice_type,status,currency,exchange_rate,
            customer_snapshot,machine_snapshot,work_order_snapshot,company_snapshot,technician_snapshot,warranty_snapshot,totals_snapshot,tax_snapshot,
            payment_terms,notes,created_by,created_at,updated_at) VALUES(:cid,:work_order_id,:number,'INVOICE','ISSUED',:currency,:rate,
            :customer,:machine,:work_order,:company,:technician,:warranty,:totals,:taxes,:terms,:notes,:created_by,:now,:now) RETURNING id"""),
            {"cid":cid,"work_order_id":payload.work_order_id,"number":number,"currency":payload.currency.upper(),"rate":payload.exchange_rate,
             "customer":_dump(summary["customer"]),"machine":_dump(summary["machine"]),"work_order":_dump(summary["work_order"]),"company":_dump(dict(company)),
             "technician":_dump(dict(technician)),"warranty":_dump(summary["warranty"]),"totals":_dump(totals),"taxes":_dump(summary["parts"]),
             "terms":payload.payment_terms,"notes":payload.notes,"created_by":user["id"],"now":now}).scalar_one())
        for item in items:
            _insert_item(db,cid,invoice_id,item,coverage)
        # Invoice issuance is not the receivable birth event. If COMPLETED already
        # produced a service receivable, reconcile its immutable revision chain to
        # the authoritative frozen invoice items in this same transaction.
        reconcile_service_receivable(
            db,
            cid,
            payload.work_order_id,
            actor_id=int(user["id"]),
            allow_initial_create=False,
        )
        # invoice_id + company_id feed the stable client ETTN (idempotency key,
        # spec §7); issued_at feeds UBL IssueDate/IssueTime.
        _apply_einvoice_seam(db,cid,invoice_id,{"invoice_number":number,"currency":payload.currency.upper(),
            "company":dict(company),"customer":summary["customer"],"totals":totals,"items":items,
            "company_id":cid,"invoice_id":invoice_id,"issued_at":now},now)
        log_invoice_action(db,request,cid,invoice_id,"CREATED",history=True,metadata={"invoice_number":number})
        # Aktivite paneli kaydı — faturayla AYNI transaction içinde.
        log_request_activity(db,request,cid,"invoice.create","invoice",invoice_id,
            f"{number} faturasını oluşturdu — {format_money_tr(grand_total,payload.currency.upper())}, "
            f"müşteri: {summary['customer'].get('name') or '#'+str(summary['customer'].get('id'))}",
            {"invoice_number":number,"work_order_id":payload.work_order_id,
             "currency":payload.currency.upper(),"grand_total":grand_total})
        db.commit(); return invoice_id
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409,"Bu iş emri için fatura zaten oluşturulmuş.") from exc

def _insert_item(db:Session,cid:int,invoice_id:int,item:dict,coverage:Decimal)->None:
    db.execute(text("""INSERT INTO invoice_items(company_id,invoice_id,item_type,description,quantity,unit_price,original_price,discount_type,discount_value,
        discount_amount,tax_rate,tax_exempt,tax_amount,total,warranty_percent,customer_payable,company_payable,source_snapshot)
        VALUES(:cid,:invoice_id,:item_type,:description,:qty,:unit_price,:unit_price,'PERCENT',:discount_value,:discount_amount,:tax_rate,:tax_exempt,
        :tax_amount,:total,:coverage,:customer,:company,:source)"""),{"cid":cid,"invoice_id":invoice_id,"item_type":item["item_type"],"description":item["description"],
        "qty":quantity(item["qty"]),"unit_price":money(item["unit_price"]),"discount_value":percentage(item["discount_value"]),"discount_amount":item["discount_amount"],
        "tax_rate":percentage(item["tax_rate"]),"tax_exempt":False,"tax_amount":item["tax_amount"],"total":item["total"],"coverage":coverage,
        "customer":item["customer"],"company":item["company"],"source":item["source"]})

def _apply_einvoice_seam(db:Session,cid:int,invoice_id:int,snapshot:dict,now:Any)->None:
    # Invoice issuance only freezes the provider-independent UBL/JSON payload.
    # Network access belongs exclusively to the explicit submit endpoint: creating
    # an internal invoice must never send an external document as a side effect.
    from .einvoice import build_einvoice_payload
    payload=None; channel=None; status="NONE"; uuid=None; external_id=None; error=None
    try:
        payload=build_einvoice_payload(snapshot)
        channel=payload.get("channel")
    except Exception as exc:
        status="ERROR"; error=str(exc)[:2000]
    submitted=now if status in ("PENDING","SENT","ACCEPTED") else None
    db.execute(text("""UPDATE invoices SET einvoice_payload=:payload,einvoice_channel=:channel,einvoice_status=:status,
        einvoice_uuid=:uuid,einvoice_external_id=:ext,einvoice_last_error=:err,einvoice_submitted_at=:submitted,einvoice_updated_at=:now
        WHERE id=:id AND company_id=:cid"""),{"payload":_dump(payload) if payload is not None else None,"channel":channel,"status":status,"uuid":uuid,
        "ext":external_id,"err":error,"submitted":submitted,"now":now,"id":invoice_id,"cid":cid})

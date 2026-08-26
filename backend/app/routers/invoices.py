from __future__ import annotations
import json
import logging
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session
from ..activity_log import format_money_tr, log_request_activity
from ..auth import utcnow
from ..config import settings
from ..db import get_db
from ..einvoice import advance_status,einvoice_configuration,get_einvoice_provider,resolve_channel
from ..invoice_pdf import build_invoice_pdf
from ..invoice_schemas import InvoiceCancelRequest,InvoiceGenerateRequest
from ..invoice_service import generate_invoice,log_invoice_action
from ..service_receivable_engine import reconcile_service_receivable
from ..tenancy import company_id

logger=logging.getLogger(__name__)
router=APIRouter(prefix="/invoices",tags=["invoices"])
JSON_FIELDS=("customer_snapshot","machine_snapshot","work_order_snapshot","company_snapshot","technician_snapshot","warranty_snapshot","totals_snapshot","tax_snapshot")

def _invoice(db:Session,cid:int,invoice_id:int)->dict:
    row=db.execute(text("SELECT * FROM invoices WHERE id=:id AND company_id=:cid"),{"id":invoice_id,"cid":cid}).mappings().first()
    if not row: raise HTTPException(404,"Fatura bulunamadı")
    return dict(row)

def _detail(db:Session,cid:int,invoice_id:int)->dict:
    invoice=_invoice(db,cid,invoice_id); items=[dict(x) for x in db.execute(text("SELECT * FROM invoice_items WHERE invoice_id=:id AND company_id=:cid ORDER BY id"),{"id":invoice_id,"cid":cid}).mappings().all()]
    result=dict(invoice)
    for field in JSON_FIELDS: result[field.removesuffix("_snapshot")]=json.loads(result.pop(field))
    result["items"]=items; return result

@router.post("/generate",status_code=201)
def generate(payload:InvoiceGenerateRequest,request:Request,db:Session=Depends(get_db)):
    invoice_id=generate_invoice(db,request,company_id(request),payload); return _detail(db,company_id(request),invoice_id)

@router.get("")
def list_invoices(request:Request,q:str="",status:str|None=None,invoice_type:str|None=None,currency:str|None=None,
    page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=200),sort:str="created_at",direction:str="desc",db:Session=Depends(get_db)):
    cid=company_id(request); allowed={"created_at","invoice_number","status"}; sort=sort if sort in allowed else "created_at"; direction="ASC" if direction.lower()=="asc" else "DESC"
    conditions=["company_id=:cid","(LOWER(invoice_number) LIKE LOWER(:q) OR LOWER(customer_snapshot) LIKE LOWER(:q))"]
    params={"cid":cid,"q":f"%{q.strip()}%","limit":page_size,"offset":(page-1)*page_size}
    for name,value in (("status",status),("invoice_type",invoice_type),("currency",currency)):
        if value: conditions.append(f"{name}=:{name}"); params[name]=value.upper()
    where=" AND ".join(conditions); total=db.execute(text(f"SELECT COUNT(*) FROM invoices WHERE {where}"),params).scalar_one()
    rows=db.execute(text(f"SELECT id,invoice_number,invoice_type,status,currency,exchange_rate,customer_snapshot,totals_snapshot,created_at FROM invoices WHERE {where} ORDER BY {sort} {direction},id {direction} LIMIT :limit OFFSET :offset"),params).mappings().all()
    items=[]
    for row in rows:
        item=dict(row); item["customer"]=json.loads(item.pop("customer_snapshot")); item["totals"]=json.loads(item.pop("totals_snapshot")); items.append(item)
    return {"items":items,"page":page,"page_size":page_size,"total":total,"pages":((total+page_size-1)//page_size)}

@router.get("/{invoice_id}")
def detail(invoice_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request); invoice=_detail(db,cid,invoice_id); log_invoice_action(db,request,cid,invoice_id,"VIEWED"); db.commit(); return invoice

@router.get("/{invoice_id}/history")
def history(invoice_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request); _invoice(db,cid,invoice_id); return [dict(x) for x in db.execute(text("SELECT * FROM invoice_history WHERE invoice_id=:id AND company_id=:cid ORDER BY id"),{"id":invoice_id,"cid":cid}).mappings().all()]

@router.post("/{invoice_id}/cancel")
def cancel(invoice_id:int,payload:InvoiceCancelRequest,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request); invoice=_invoice(db,cid,invoice_id)
    if invoice["status"]!="ISSUED": raise HTTPException(409,"Fatura iptal edilemez")
    # Compare-and-set: the UPDATE ... WHERE status='ISSUED' is the atomic gate.
    # Two concurrent cancels both read ISSUED above, but only the row-winning
    # UPDATE affects a row — check rowcount before writing audit/history so the
    # cancellation (and its audit trail) happens exactly once.
    result=db.execute(text("UPDATE invoices SET status='CANCELLED',cancelled_at=:now,cancel_reason=:reason,updated_at=:now WHERE id=:id AND company_id=:cid AND status='ISSUED'"),{"now":utcnow(),"reason":payload.reason,"id":invoice_id,"cid":cid})
    if not result.rowcount:
        db.rollback(); raise HTTPException(409,"Fatura eş zamanlı olarak iptal edildi")
    if invoice["work_order_id"] is not None:
        reconcile_service_receivable(
            db,
            cid,
            int(invoice["work_order_id"]),
            actor_id=int(request.state.user["id"]),
            allow_initial_create=False,
        )
    log_invoice_action(db,request,cid,invoice_id,"CANCELLED",payload.reason,history=True)
    totals=json.loads(invoice["totals_snapshot"]); customer=json.loads(invoice["customer_snapshot"])
    log_request_activity(db,request,cid,"invoice.cancel","invoice",invoice_id,
        f"{invoice['invoice_number']} faturasını iptal etti — "
        f"{format_money_tr(totals.get('grand_total',0),str(invoice['currency']))}, "
        f"müşteri: {customer.get('name') or '#'+str(customer.get('id'))} — gerekçe: {payload.reason}",
        {"invoice_number":invoice["invoice_number"],"reason":payload.reason,
         "status":{"old":"ISSUED","new":"CANCELLED"}})
    db.commit(); return _detail(db,cid,invoice_id)

@router.get("/{invoice_id}/pdf")
def pdf(invoice_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request); raw=_invoice(db,cid,invoice_id); items=[dict(x) for x in db.execute(text("SELECT * FROM invoice_items WHERE invoice_id=:id AND company_id=:cid ORDER BY id"),{"id":invoice_id,"cid":cid}).mappings().all()]
    content=build_invoice_pdf(raw,items); log_invoice_action(db,request,cid,invoice_id,"DOWNLOADED",metadata={"format":"pdf"}); db.commit()
    return Response(content,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{raw["invoice_number"]}.pdf"'})

EINVOICE_FIELDS=("einvoice_channel","einvoice_status","einvoice_uuid","einvoice_external_id","einvoice_last_error","einvoice_submitted_at","einvoice_updated_at","einvoice_payload")

# Fail-closed, ama uygulamayı öldürmeden: e-Fatura yapılandırılmamışsa ERP'nin
# geri kalanı normal çalışır, yalnız GÖNDERİM kapalıdır ve bunu açıkça söyler.
EINVOICE_NOT_CONFIGURED="E-fatura entegrasyonu yapılandırılmamış"

def _einvoice_view(invoice:dict)->dict:
    out={}
    for key in EINVOICE_FIELDS:
        value=invoice.get(key)
        out[key]=str(value) if value is not None and key.endswith("_at") else value
    # Frontend sinyali: gönderim bugün mümkün mü? Salt-okunur, kimlik bilgisi
    # taşımaz. Butonu gizlemek isteyen arayüz bunu okur; bu PR'da frontend
    # değişikliği YOK.
    out["einvoice_configured"]=einvoice_configuration(settings).configured
    return out

@router.post("/{invoice_id}/einvoice/submit")
def einvoice_submit(invoice_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request); invoice=_invoice(db,cid,invoice_id)
    configuration=einvoice_configuration(settings)
    if not configuration.configured:
        # Sessiz 200 kapandı. Eskiden NoOp "NONE" döner, gövdeye bir not
        # düşülür ve çağıran bunu başarı sanabilirdi. Artık: DB'ye yazılmaz,
        # denetim kaydı düşülmez (olmayan bir gönderim denetlenemez), 503.
        # Gerekçe yalnız log'a gider — istemciye yapılandırma detayı sızmaz.
        logger.warning("e-Fatura gönderimi reddedildi: yapılandırma eksik (sebep=%s)",configuration.reason)
        raise HTTPException(503,EINVOICE_NOT_CONFIGURED)
    tax_number=db.execute(text("SELECT tax_number FROM companies WHERE id=:cid"),{"cid":cid}).scalar_one_or_none()
    if not tax_number:
        raise HTTPException(400,"Firma vergi numarası tanımlı değil; Ayarlar ekranından girin.")
    provider=get_einvoice_provider(settings,company_id=cid); raw=invoice.get("einvoice_payload"); payload=json.loads(raw) if raw else {}
    channel=invoice.get("einvoice_channel"); status="NONE"; uuid=None; ext=None; err=None
    customer=payload.get("customer") if isinstance(payload.get("customer"),dict) else {}
    customer_vkn=str(customer.get("vkn_tckn") or "").strip()
    if not customer_vkn:
        status="FAILED"; err="Fatura e-Fatura biçimine uymuyor: alıcı VKN/TCKN."
    else:
        try:
            taxpayer=provider.check_taxpayer(customer_vkn)
            channel,profile_id=resolve_channel(taxpayer.get("is_efatura_user"))
            payload={**payload,"channel":channel,"profile_id":profile_id}
            result=provider.submit(payload); status=result.status; channel=result.channel or channel; uuid=result.uuid; ext=result.external_id; err=result.error
        except Exception as exc:
            status="ERROR"; err=str(exc)[:2000]
    # Spec §5 state machine: forward-only, and ACCEPTED/REJECTED are terminal — a
    # re-submit can never walk an already accepted document back. The recorded
    # ETTN/external id is likewise never cleared by a later failed attempt.
    status=advance_status(invoice.get("einvoice_status"),status)
    uuid=uuid or invoice.get("einvoice_uuid"); ext=ext or invoice.get("einvoice_external_id")
    now=utcnow(); submitted=now if status in ("PENDING","SENT","ACCEPTED") else invoice.get("einvoice_submitted_at")
    db.execute(text("""UPDATE invoices SET einvoice_payload=:payload,einvoice_channel=:c,einvoice_status=:s,einvoice_uuid=:u,einvoice_external_id=:e,
        einvoice_last_error=:err,einvoice_submitted_at=:sub,einvoice_updated_at=:now WHERE id=:id AND company_id=:cid"""),
        {"payload":json.dumps(payload,ensure_ascii=False,sort_keys=True),"c":channel,"s":status,"u":uuid,"e":ext,
         "err":err,"sub":submitted,"now":now,"id":invoice_id,"cid":cid})
    log_invoice_action(db,request,cid,invoice_id,"EINVOICE_SUBMIT",metadata={"status":status}); db.commit()
    # NoOp dalı kalktı: yapılandırma eksikse buraya hiç gelinmiyor (503).
    return _einvoice_view(_invoice(db,cid,invoice_id))

@router.get("/{invoice_id}/einvoice/status")
def einvoice_status(invoice_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request); return _einvoice_view(_invoice(db,cid,invoice_id))

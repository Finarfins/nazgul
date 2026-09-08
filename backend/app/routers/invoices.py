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
from ..einvoice import (CANCELLABLE,CANCELLED,UblBuildError,advance_status,build_invoice_xml,
    einvoice_configuration,get_einvoice_provider,resolve_channel)
from ..einvoice.ubl import CHANNEL_EARSIV,CHANNEL_EFATURA
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

# --- e-belge iptal kapısı -------------------------------------------------
# GERÇEK BİR BELGE, GERÇEK BİR SIRA. Bir fatura e-Arşiv/e-Fatura olarak
# gönderildikten sonra YEREL iptal artık tek başına bir kayıt değişikliği
# değildir: entegratörde (ve GİB'de) o belgenin bir hayatı vardır. Yereli önce
# iptal edip sonra entegratöre sormak, ikisinin AYRIŞTIĞI bir pencere açar —
# ve o pencerede ERP "iptal" derken belge hâlâ yürürlüktedir.
EARSIV_CANCEL_FAILED="EARSIV_IPTAL_BASARISIZ"

#: e-Fatura (B2B) TEK TARAFLI İPTAL EDİLEMEZ ve bu bir ürün kararı değil,
#: mevzuattır: giden bir ticari e-Faturaya alıcı, TTK 18/3 uyarınca belgenin
#: kendisine ulaşmasından itibaren SEKİZ GÜN içinde itiraz eder; iptal, o
#: itirazın (uygulama yanıtı / harici itiraz) sonucudur, göndericinin tek
#: taraflı beyanı değildir. Bu artışta uygulama yanıtı (`ApplicationResponse`)
#: YOK — bu yüzden uç, sessizce yerelde iptal etmek yerine operatöre asıl
#: yapması gerekeni SÖYLER.
EFATURA_CANCEL_REFUSED=(
    "Giden e-Fatura tek taraflı iptal edilemez. Alıcı, faturanın kendisine "
    "ulaşmasından itibaren SEKİZ GÜN içinde itiraz edebilir (TTK 18/3); iptal "
    "ancak bu itiraz sürecinin sonucunda gerçekleşir. Bu sürüm uygulama yanıtı "
    "(ApplicationResponse) göndermez; itirazı alıcıyla yürütün."
)

def _einvoice_cancel_gate(db:Session,cid:int,invoice_id:int,invoice:dict,now)->bool:
    """Yerel iptalden ÖNCE entegratörle hesaplaş. Yazdıysa ``True`` döner.

    ÜÇ YOL, VE SIRA ANLAMLI:

    * Belgenin canlı bir zarfı yoksa (`einvoice_status` üç iptal edilebilir
      durumdan birinde değilse) kapı hiç çalışmaz — iptal edilecek bir e-belge
      YOKTUR ve gönderilmemiş bir fatura için sağlayıcıya gitmek anlamsızdır.
    * `EFATURA` ⇒ 409, mevzuat gerekçesiyle. Yerelde iptal EDİLMEZ.
    * `EARSIV` ⇒ sağlayıcıya `CancelEArchiveInvoice`. BAŞARISIZSA 409 ve yerel
      iptal OLMAZ; başarılıysa `einvoice_status=CANCELLED` yazılır ve çağıran
      yerel iptale devam eder. Yazma, çağıranın işlemiyle AYNI transaction'da
      kalır: iki satır ya birlikte iner ya hiç inmez.

    EŞ ZAMANLILIK — BİLİNEN VE KABUL EDİLEN PENCERE. Yerel iptalin atomik
    kapısı aşağıdaki compare-and-set'tir (`WHERE ... status='ISSUED'`) ve o
    BU KAPIDAN SONRA koşar. Yani iki eş zamanlı iptal isteği, ikisi de
    entegratöre GİDEBİLİR; yerelde yalnız biri kazanır, diğeri 409 alır.

    Bu bir gözden kaçma değil, sıranın DOĞRUDAN SONUCUDUR: çift çağrıyı
    engellemenin tek yolu yerel CAS'ı öne almaktır ve bu, tam da bu kapının
    var olma sebebini — yerel iptalin entegratörü geçememesi — ortadan
    kaldırırdı. İki maliyet karşılaştırıldı: (a) aynı belge için ikinci bir
    iptal çağrısı, ki ETKİSİ yoktur (ikincisi belgeyi çoktan çekilmiş bulur ve
    hata döner, yerel duruma da yazmaz çünkü kaybeden istek zaten 409'a
    düşer), (b) sağlayıcı reddetmişken yerelde iptal edilmiş bir fatura, ki
    GERİ ALINAMAZ bir tutarsızlıktır. (a) seçildi.
    """
    durum=str(invoice.get("einvoice_status") or "").strip().upper()
    if durum not in CANCELLABLE:
        return False
    kanal=invoice.get("einvoice_channel")
    if kanal==CHANNEL_EFATURA:
        raise HTTPException(409,EFATURA_CANCEL_REFUSED)
    if kanal!=CHANNEL_EARSIV:
        # Kanalı bilinmeyen ama canlı görünen bir belge. Hangi iptal yolunun
        # geçerli olduğunu BİLMEDEN yerelde iptal etmek, iki yolun da yanlış
        # olabileceği bir tahmindir.
        raise HTTPException(409,f"{EARSIV_CANCEL_FAILED}: e-belge kanalı belirsiz")
    configuration=einvoice_configuration(settings)
    if not configuration.configured:
        # Entegratöre SORULAMIYOR. Yerelde iptal etmek, tam da bu uçun
        # engellemek için var olduğu ayrışmayı üretirdi.
        logger.warning("e-Arşiv iptali reddedildi: yapılandırma eksik (sebep=%s)",configuration.reason)
        raise HTTPException(503,EINVOICE_NOT_CONFIGURED)
    provider=get_einvoice_provider(settings,company_id=cid)
    ext=str(invoice.get("einvoice_external_id") or "").strip()
    try:
        result=provider.cancel(ext,channel=kanal,uuid=invoice.get("einvoice_uuid"))
    except Exception as exc:
        # Sağlayıcı katmanı `cancel` için `FAILED` döndürmeyi taahhüt ediyor;
        # yine de bir istisna sızarsa YEREL İPTAL YAPILMAZ. "Beklenmeyen hata"
        # bir iptal izni değildir.
        logger.warning("e-Arşiv iptali istisnayla düştü: %s",type(exc).__name__)
        raise HTTPException(409,f"{EARSIV_CANCEL_FAILED}: {str(exc)[:400]}") from None
    if result.status!=CANCELLED:
        raise HTTPException(409,f"{EARSIV_CANCEL_FAILED}: {(result.error or '')[:400]}")
    # İleri-yönlü makine: `CANCELLED` yalnız üç canlı durumdan kabul edilir
    # (`CANCELLABLE`), yani buraya gelen bir yanıt REJECTED/FAILED bir belgeyi
    # sessizce iptal edilmiş gösteremez.
    yeni=advance_status(invoice.get("einvoice_status"),CANCELLED)
    db.execute(text("""UPDATE invoices SET einvoice_status=:s,einvoice_last_error=NULL,einvoice_updated_at=:now
        WHERE id=:id AND company_id=:cid"""),{"s":yeni,"now":now,"id":invoice_id,"cid":cid})
    return True

@router.post("/{invoice_id}/cancel")
def cancel(invoice_id:int,payload:InvoiceCancelRequest,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request); invoice=_invoice(db,cid,invoice_id)
    if invoice["status"]!="ISSUED": raise HTTPException(409,"Fatura iptal edilemez")
    now=utcnow()
    # ENTEGRATÖR ÖNCE. Bu satırın yeri sözleşmenin kendisidir: aşağıdaki
    # compare-and-set bir kez koştuğunda fatura YERELDE iptal olmuştur ve
    # sağlayıcı reddederse geri alınacak bir şey kalmaz.
    ebelge_iptal=_einvoice_cancel_gate(db,cid,invoice_id,invoice,now)
    if ebelge_iptal:
        log_invoice_action(db,request,cid,invoice_id,"EINVOICE_CANCEL",metadata={"status":CANCELLED})
    # Compare-and-set: the UPDATE ... WHERE status='ISSUED' is the atomic gate.
    # Two concurrent cancels both read ISSUED above, but only the row-winning
    # UPDATE affects a row — check rowcount before writing audit/history so the
    # cancellation (and its audit trail) happens exactly once.
    result=db.execute(text("UPDATE invoices SET status='CANCELLED',cancelled_at=:now,cancel_reason=:reason,updated_at=:now WHERE id=:id AND company_id=:cid AND status='ISSUED'"),{"now":now,"reason":payload.reason,"id":invoice_id,"cid":cid})
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

EINVOICE_FIELDS=("einvoice_channel","einvoice_status","einvoice_uuid","einvoice_external_id","einvoice_last_error","einvoice_submitted_at","einvoice_updated_at","einvoice_payload","einvoice_web_key","einvoice_gib_status_code","einvoice_pk_alias")

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
    channel=invoice.get("einvoice_channel"); status="NONE"; uuid=None; ext=None; err=None; web_key=None; gib_code=None
    customer=payload.get("customer") if isinstance(payload.get("customer"),dict) else {}
    customer_vkn=str(customer.get("vkn_tckn") or "").strip()
    if not customer_vkn:
        status="FAILED"; err="Fatura e-Fatura bicimine uymuyor: alıcı VKN/TCKN."
    else:
        try:
            taxpayer=provider.check_taxpayer(customer_vkn)
            channel,profile_id=resolve_channel(taxpayer.get("is_efatura_user"))
            payload={**payload,"channel":channel,"profile_id":profile_id}
            result=provider.submit(payload); status=result.status; channel=result.channel or channel; uuid=result.uuid; ext=result.external_id; err=result.error
            web_key=result.web_key; gib_code=result.gib_status_code
        except Exception as exc:
            status="ERROR"; err=str(exc)[:2000]
    # Spec §5 state machine: forward-only, and ACCEPTED/REJECTED are terminal — a
    # re-submit can never walk an already accepted document back. The recorded
    # ETTN/external id is likewise never cleared by a later failed attempt.
    status=advance_status(invoice.get("einvoice_status"),status)
    uuid=uuid or invoice.get("einvoice_uuid"); ext=ext or invoice.get("einvoice_external_id")
    # WEB_KEY YALNIZ gönderim yanıtında bir kez döner ve bir daha sorulamaz.
    # `or` KASITLI: başarısız bir yeniden gönderim, önceki başarılı gönderimin
    # sakladığı anahtarı SİLMEMELİ — silseydi e-Arşiv PDF'i kalıcı olarak
    # erişilemez hâle gelirdi (ETTN'in aynı gerekçeyle korunmasının eşi).
    web_key=web_key or invoice.get("einvoice_web_key"); gib_code=gib_code or invoice.get("einvoice_gib_status_code")
    now=utcnow(); submitted=now if status in ("PENDING","SENT","ACCEPTED") else invoice.get("einvoice_submitted_at")
    db.execute(text("""UPDATE invoices SET einvoice_payload=:payload,einvoice_channel=:c,einvoice_status=:s,einvoice_uuid=:u,einvoice_external_id=:e,
        einvoice_last_error=:err,einvoice_submitted_at=:sub,einvoice_updated_at=:now,einvoice_web_key=:wk,einvoice_gib_status_code=:gsc
        WHERE id=:id AND company_id=:cid"""),
        {"payload":json.dumps(payload,ensure_ascii=False,sort_keys=True),"c":channel,"s":status,"u":uuid,"e":ext,
         "err":err,"sub":submitted,"now":now,"wk":web_key,"gsc":gib_code,"id":invoice_id,"cid":cid})
    log_invoice_action(db,request,cid,invoice_id,"EINVOICE_SUBMIT",metadata={"status":status}); db.commit()
    # NoOp dalı kalktı: yapılandırma eksikse buraya hiç gelinmiyor (503).
    return _einvoice_view(_invoice(db,cid,invoice_id))

@router.get("/{invoice_id}/einvoice/status")
def einvoice_status(invoice_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request); return _einvoice_view(_invoice(db,cid,invoice_id))

DOWNLOAD_FORMATS=("pdf","xml")
#: Gönderilmemiş bir belgenin indirilecek bir sureti YOKTUR. 404, 409 DEĞİL —
#: ve fark ölçülebilir: 409 "şu an olmaz, durumu değiştir" demektir, oysa
#: burada istenen KAYNAK hiç var olmamıştır. `einvoice/sync` 409 kullanır
#: çünkü orada YAPILACAK bir iş vardır (önce gönder); burada indirilecek bir
#: dosya yoktur.
EINVOICE_DOCUMENT_MISSING="Faturanın e-belge sureti yok; henüz gönderilmemiş."

@router.get("/{invoice_id}/einvoice/download")
def einvoice_download(invoice_id:int,request:Request,format:str=Query("pdf"),db:Session=Depends(get_db)):
    """e-Belgenin suretini indir: sağlayıcı PDF'i ya da gönderilen UBL XML'i.

    İKİ BİÇİM, İKİ FARKLI KAYNAK — ve bu ayrım kasıtlı:

    * ``xml`` **ağa çıkmaz.** Gönderilen belge, gönderim anında dondurulmuş
      ``einvoice_payload``dan bire bir yeniden üretilir (``build_invoice_xml``
      saf bir dönüşümdür). Sağlayıcıdan XML istemek, elimizde ZATEN olan ve
      belgenin gönderilmiş hâlini tanımlayan veriyi ikinci bir kaynaktan
      sormak olurdu.
    * ``pdf`` sağlayıcıdan gelir, çünkü PDF'i BİZ üretmiyoruz: e-Arşiv/e-Fatura
      görüntüsü entegratörün mühürlediği sunumdur. `GET /invoices/{id}/pdf`
      bizim İÇ faturamızı basar; bu uç ONUNLA AYNI ŞEY DEĞİLDİR.

    ANAHTAR SEÇİMİ KANALA GÖRE, ve ikisi birbirinin yerine geçmez:
    e-Arşiv `GetEArchiveInvoice` belgeyi ``WEB_VALIDATION_KEY`` ile ister
    (bizim ``einvoice_web_key``imiz), e-Fatura `GetInvoiceWithType` ise
    sağlayıcı belge kimliğiyle (``einvoice_external_id``).

    YETKİ ``sales``, ``read`` DEĞİL — ÖLÇÜLDÜ: kural yazılmadan önce
    ``required_permission("GET", ".../einvoice/download")`` "read" veriyordu
    (dosyanın genel güvenli-metot kuralı). ``read`` YANLIŞ olurdu, iki
    sebeple: (1) bu GET DIŞ BİR YAN ETKİ üretir — sağlayıcıda oturum açar ve
    kota tüketir, tıpkı ``einvoice/sync``in ``sales``ta tutulma gerekçesi
    gibi; (2) indirilen şey resmî mali belgenin kendisidir, iç PDF'in bir
    kopyası değil.
    """
    bicim=str(format or "").strip().lower()
    if bicim not in DOWNLOAD_FORMATS:
        raise HTTPException(400,f"Geçersiz bicim: yalnız {' veya '.join(DOWNLOAD_FORMATS)}")
    cid=company_id(request); invoice=_invoice(db,cid,invoice_id)
    durum=str(invoice.get("einvoice_status") or "").strip().upper()
    ext=str(invoice.get("einvoice_external_id") or "").strip()
    if durum in ("","NONE","FAILED") or not ext:
        # İki koşul da gerekli ve İKİSİ AYRI ŞEY ÖLÇÜYOR: durum belgenin bir
        # hayatı olup olmadığını, ``external_id`` ise sağlayıcıda bir karşılığı
        # olup olmadığını söyler. Durumu ilerlemiş ama kimliği olmayan bir satır
        # (ya da tersi) tutarsızdır ve indirilecek bir sureti yoktur.
        raise HTTPException(404,EINVOICE_DOCUMENT_MISSING)
    kanal=invoice.get("einvoice_channel")
    if bicim=="xml":
        ham=invoice.get("einvoice_payload")
        if not ham:
            raise HTTPException(404,EINVOICE_DOCUMENT_MISSING)
        try:
            icerik=build_invoice_xml(json.loads(ham))
        except (UblBuildError,ValueError) as exc:
            # Saklanan payload'dan belge YENİDEN ÜRETİLEMİYOR. Yarım bir XML
            # döndürmek, mali belge diye eksik bir dosya vermek olurdu.
            raise HTTPException(409,f"Gönderilen UBL yeniden üretilemedi: {str(exc)[:300]}") from None
        log_invoice_action(db,request,cid,invoice_id,"EINVOICE_DOWNLOAD",metadata={"format":"xml"}); db.commit()
        return Response(icerik,media_type="application/xml",
            headers={"Content-Disposition":f'attachment; filename="{invoice["invoice_number"]}.xml"'})
    configuration=einvoice_configuration(settings)
    if not configuration.configured:
        logger.warning("e-Belge PDF indirmesi reddedildi: yapılandırma eksik (sebep=%s)",configuration.reason)
        raise HTTPException(503,EINVOICE_NOT_CONFIGURED)
    provider=get_einvoice_provider(settings,company_id=cid)
    try:
        icerik=provider.fetch_pdf(ext,channel=kanal,web_key=invoice.get("einvoice_web_key"))
    except Exception as exc:
        # `fetch_pdf` boş `bytes` DÖNDÜRMEZ, fırlatır (sağlayıcı katmanının
        # sözleşmesi): boş bir gövde çağıran tarafta boş bir PDF'ten ayırt
        # edilemezdi. Burada da aynı çizgi: 502, çünkü hata BİZDE değil.
        logger.warning("e-Belge PDF indirmesi başarısız: %s",type(exc).__name__)
        raise HTTPException(502,f"e-Belge PDF alınamadı: {str(exc)[:400]}") from None
    log_invoice_action(db,request,cid,invoice_id,"EINVOICE_DOWNLOAD",metadata={"format":"pdf"}); db.commit()
    return Response(icerik,media_type="application/pdf",
        headers={"Content-Disposition":f'attachment; filename="{invoice["invoice_number"]}-ebelge.pdf"'})

@router.post("/{invoice_id}/einvoice/sync")
def einvoice_sync(invoice_id:int,request:Request,db:Session=Depends(get_db)):
    """Sağlayıcıya SOR ve yerel durumu tazele.

    NEDEN AYRI BİR UÇ (ölçüldü): `GET .../einvoice/status` YALNIZ yerel
    veritabanını okuyor — sağlayıcıya HİÇ gitmiyor. Yani bir belge GİB'de
    ACCEPTED olduktan sonra bile bizim tarafta PENDING görünmeye devam
    ediyordu ve durumu ilerletecek TEK yol yeniden GÖNDERMEKTİ; bu da
    idempotent olmayan bir yolu okuma amacıyla kullanmak demekti. Bu uç o
    boşluğu kapatıyor: SORAR, YAZMAZ göndermez.

    GET DEĞİL POST, ve gerekçesi ölçülmüş: bu çağrı DIŞ BİR YAN ETKİ üretiyor
    (sağlayıcıda oturum açar, kota tüketir) ve YEREL SATIRI YAZAR. GET'in
    envanterdeki anlamı "read"tir (`test_route_get_permission_inventory`) ve
    orada yazan bir uç o sözleşmeyi bozardı — yetki `submit` ile AYNI sınıfta
    kalıyor.

    Durum GERİYE yürüyemez: `advance_status` ileri-yönlüdür ve ACCEPTED /
    REJECTED terminaldir; geç gelen bir yanıt kabul edilmiş bir belgeyi
    PENDING'e döndüremez.
    """
    cid=company_id(request); invoice=_invoice(db,cid,invoice_id)
    configuration=einvoice_configuration(settings)
    if not configuration.configured:
        logger.warning("e-Fatura durum sorgusu reddedildi: yapılandırma eksik (sebep=%s)",configuration.reason)
        raise HTTPException(503,EINVOICE_NOT_CONFIGURED)
    ext=str(invoice.get("einvoice_external_id") or "").strip()
    if not ext:
        # Gönderilmemiş bir belgenin sorulacak bir kimliği YOKTUR. Sağlayıcıya
        # boş bir kimlikle gitmek boş bir yanıt üretir ve bu "belge yok" gibi
        # okunur — sessiz yanlış yerine gürültülü hata.
        raise HTTPException(409,"Fatura henüz gönderilmedi; sorgulanacak belge kimliği yok.")
    provider=get_einvoice_provider(settings,company_id=cid)
    gib_code=invoice.get("einvoice_gib_status_code"); web_key=invoice.get("einvoice_web_key"); err=None
    try:
        result=provider.query_status(ext,channel=invoice.get("einvoice_channel"),uuid=invoice.get("einvoice_uuid"))
        reported=result.status; err=result.error; gib_code=result.gib_status_code or gib_code
        # e-Arşiv durum yanıtı WEB_KEY'i tekrar veriyor: gönderimde kaçmışsa
        # burada yakalanır. `or` yine KASITLI — var olan anahtar SİLİNMEZ.
        web_key=result.web_key or web_key
    except Exception as exc:
        # UNRESOLVED: `advance_status` bunu bir GEÇİŞ SAYMAZ, belgeyi olduğu
        # yerde bırakır. Başarısız bir sorgu FAILED yazmaz — FAILED "gönderim
        # hiç inmedi, ETTN yok" demektir ve elimizdeki ETTN onu yalanlar.
        reported="UNRESOLVED"; err=str(exc)[:2000]
    status=advance_status(invoice.get("einvoice_status"),reported)
    now=utcnow()
    # KİRACI YÜKLEMİ AÇIK: `company_id=:cid` olmadan sızmış bir fatura kimliği
    # BAŞKA firmanın satırını tazeleyebilirdi. `_invoice` zaten kapsam
    # denetliyor ama yazma kendi yüklemini TAŞIR — iki koruma aynı şeyi
    # ölçmüyor (0080'in `_sahip_mi` kaydıyla aynı gerekçe).
    db.execute(text("""UPDATE invoices SET einvoice_status=:s,einvoice_last_error=:err,
        einvoice_gib_status_code=:gsc,einvoice_web_key=:wk,einvoice_updated_at=:now WHERE id=:id AND company_id=:cid"""),
        {"s":status,"err":err,"gsc":gib_code,"wk":web_key,"now":now,"id":invoice_id,"cid":cid})
    log_invoice_action(db,request,cid,invoice_id,"EINVOICE_SYNC",metadata={"status":status}); db.commit()
    return _einvoice_view(_invoice(db,cid,invoice_id))

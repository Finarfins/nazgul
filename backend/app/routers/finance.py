import logging
from datetime import date
from ..change_history import record_change
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text, insert, select
from sqlalchemy.orm import Session
from ..business_time import business_today
from ..db import get_db
from ..money import HUNDRED, ZERO_MONEY, money
from ..schemas import PaymentCreate, CustomerCreate, FinancialAccountCreate, FinancialTransactionCreate, FinancialTransferCreate, FinancialInstrumentCreate, FinancialInstrumentStatusUpdate
from ..statement import Statement, build_statement
from ..tenancy import company_id
from ..document_engine import PAYMENT_METHODS
from ..finance_engine import finance_accounts, finance_transactions, financial_instruments, ACCOUNT_TYPES, sync_payment_finance, remove_payment_finance, validate_payment_account, utcnow
from ..crm import add_contact, add_note, add_task, delete_contact, delete_note, delete_task, set_task_status
from ..entity_detail import entity_detail, entity_documents
from ..config import settings
from ..payment_allocation_engine import (
    create_payment_with_allocation,
    delete_unallocated_payment,
    ensure_charge_allocation_enabled,
    update_unallocated_payment,
)
from ..movement_references import validate_payment_reference
from ..activity_log import diff_details, format_money_tr, log_request_activity

router = APIRouter(tags=['finance'])
logger = logging.getLogger(__name__)

PAYMENT_AUDIT_FIELDS = (
    'entity_type', 'entity_id', 'amount', 'payment_date', 'payment_method',
    'account_id', 'note',
)


def _entity_name(db: Session, cid: int, entity_type: str, entity_id: int | None) -> str:
    """Aktivite özeti için cari adı. Tablo adı sabit literaldir, tenant filtresi
    her iki dalda da ``company_id=:cid`` olarak literal yazılır."""
    if entity_id is None:
        return 'bilinmiyor'
    if entity_type == 'customer':
        row = db.execute(
            text('SELECT name FROM customers WHERE id=:id AND company_id=:cid'),
            {'id': int(entity_id), 'cid': cid},
        ).first()
    elif entity_type == 'supplier':
        row = db.execute(
            text('SELECT name FROM suppliers WHERE id=:id AND company_id=:cid'),
            {'id': int(entity_id), 'cid': cid},
        ).first()
    else:
        return f'#{entity_id}'
    return str(row[0]) if row and row[0] else f'#{entity_id}'


def _payment_summary(db: Session, cid: int, verb: str, payment_id: int,
                     entity_type: str, entity_id: int | None, amount) -> str:
    kind = 'tahsilatı' if entity_type == 'customer' else 'ödemesini'
    who = _entity_name(db, cid, entity_type, entity_id)
    return (
        f'#{payment_id} numaralı {kind} {verb} — {format_money_tr(amount)}, '
        f'cari: {who}'
    )

class PaymentAccountOption(BaseModel):
    id: int
    name: str
    account_type: str
    currency: str
    is_active: bool

class EntityNoteCreate(BaseModel):
    note: str = Field(min_length=1, max_length=2000)

class EntityContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    role: str | None = None
    phone: str | None = None
    email: str | None = None
    is_primary: bool = False
    notes: str | None = None

class EntityTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    due_date: str | None = None
    priority: str = 'normal'
    assigned_to: str | None = None
    notes: str | None = None

class EntityTaskStatusUpdate(BaseModel):
    status: str

def _entity_values(payload: CustomerCreate) -> dict:
    values=payload.model_dump(); values['is_active']=bool(values.get('is_active',True)); return values


def _entity_table(entity_type: str) -> str:
    if entity_type == 'customer':
        return 'customers'
    if entity_type == 'supplier':
        return 'suppliers'
    raise HTTPException(400, 'Geçersiz cari türü')


def _ensure_entity(db: Session, cid: int, entity_type: str, entity_id: int):
    table = _entity_table(entity_type)
    if not db.execute(text(f'SELECT id FROM {table} WHERE id=:id AND company_id=:cid'), {'id': entity_id, 'cid': cid}).first():
        raise HTTPException(400, 'Cari bulunamadı')


def _validate_payment(payload: PaymentCreate):
    if payload.payment_method not in PAYMENT_METHODS:
        raise HTTPException(400, 'Geçersiz ödeme yöntemi')
    if (payload.reference_type is None) != (payload.reference_id is None):
        raise HTTPException(422, 'Ödeme belge türü ve kimliği birlikte gönderilmelidir')
    if payload.reference_type == 'late_fee':
        # Flag-OFF keeps the V2b guard verbatim on every payment write path.
        ensure_charge_allocation_enabled()
        if payload.entity_type != 'customer':
            raise HTTPException(422, 'Ödeme ile belge türü uyuşmuyor')
        return
    expected = 'order' if payload.entity_type == 'customer' else 'purchase'
    if payload.reference_type is not None and payload.reference_type != expected:
        raise HTTPException(422, 'Ödeme ile belge türü uyuşmuyor')


@router.get('/suppliers')
def suppliers(request: Request, q: str = '', sort: str = 'name_asc', active: str = 'active', db: Session = Depends(get_db)):
    from datetime import date
    cid = company_id(request); today=business_today().isoformat()
    sorts={'name_asc':'LOWER(s.name) ASC','name_desc':'LOWER(s.name) DESC','balance_asc':'current_balance ASC','balance_desc':'current_balance DESC','activity_desc':'last_activity DESC','overdue_desc':'overdue_amount DESC'}
    order=sorts.get(sort,sorts['name_asc'])
    active_sql='' if active=='all' else (' AND COALESCE(s.is_active, TRUE)=FALSE' if active=='inactive' else ' AND COALESCE(s.is_active, TRUE)=TRUE')
    rows = db.execute(text(f'''SELECT s.id,s.name,s.owner_name,s.phone,s.email,s.address,s.tax_number,s.opening_balance,
      COALESCE(s.risk_limit,0) risk_limit,COALESCE(s.payment_term_days,0) payment_term_days,CASE WHEN COALESCE(s.is_active, TRUE) THEN 1 ELSE 0 END is_active,
      COALESCE(s.opening_balance,0)+COALESCE(SUM(CASE WHEN COALESCE(pu.status,'completed') NOT IN ('draft','cancelled') THEN pu.final_total ELSE 0 END),0)+
      COALESCE(mm.total_receipts,0)-
      COALESCE(pay.total_paid,0) current_balance,
      COALESCE(SUM(CASE WHEN pu.due_date IS NOT NULL AND pu.due_date<>'' AND pu.due_date<:today
       AND COALESCE(pu.status,'completed') NOT IN ('draft','cancelled')
       THEN CASE WHEN COALESCE(pu.final_total,0)-COALESCE(pu.paid_amount,0)>0 THEN COALESCE(pu.final_total,0)-COALESCE(pu.paid_amount,0) ELSE 0 END ELSE 0 END),0) overdue_amount,
      COUNT(DISTINCT pu.id) order_count,MAX(pu.purchase_date) last_activity
      FROM suppliers s
      LEFT JOIN purchases pu ON pu.supplier_id=s.id AND pu.company_id=:cid
      LEFT JOIN (
        SELECT entity_id,SUM(amount) total_paid FROM payments
        WHERE company_id=:cid AND entity_type='supplier' GROUP BY entity_id
      ) pay ON pay.entity_id=s.id
      -- Kesilmiş müstahsil makbuzu tedarikçi BORCUDUR. `pay`in kalıbıyla
      -- AYNI: ÖNCEDEN TOPLANMIŞ alt sorgu olarak bağlanıyor, LEFT JOIN
      -- olarak DEĞİL — ikinci bir satır çoklayan birleştirme `pu` ile
      -- kartezyen üretir ve alım toplamını makbuz sayısı kadar ŞİŞİRİRDİ.
      -- NET, brüt DEĞİL: stopaj/SGK çiftçiye değil vergi dairesine borçtur.
      LEFT JOIN (
        SELECT supplier_id,SUM(net_payable) total_receipts FROM producer_receipts
        WHERE company_id=:cid AND status='issued' GROUP BY supplier_id
      ) mm ON mm.supplier_id=s.id
      WHERE s.company_id=:cid {active_sql} AND (LOWER(s.name) LIKE LOWER(:q) OR COALESCE(s.phone,'') LIKE :q
       OR LOWER(COALESCE(s.email,'')) LIKE LOWER(:q) OR COALESCE(s.tax_number,'') LIKE :q)
      GROUP BY s.id,pay.total_paid,mm.total_receipts ORDER BY {order} LIMIT 1000'''), {'cid': cid, 'q': f'%{q}%', 'today':today}).mappings().all()
    result=[]
    for item in rows:
        row=dict(item);risk=money(row.get('risk_limit'));balance=money(row.get('current_balance'))
        row['risk_exceeded']=risk>0 and balance>risk;row['risk_usage_percent']=round((balance/risk)*HUNDRED,1) if risk>0 else 0;result.append(row)
    return result


@router.get('/payments')
def payments(
    request: Request,
    entity_type: str | None = None,
    q: str = '',
    date_from: str | None = None,
    date_to: str | None = None,
    payment_method: str | None = None,
    source: str | None = None,
    sort: str = 'date_desc',
    limit: int = 1000,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    limit = max(1, min(int(limit or 1000), 3000))
    sort_sql = {
        'date_desc': 'p.payment_date DESC,p.id DESC',
        'date_asc': 'p.payment_date ASC,p.id ASC',
        'amount_desc': 'p.amount DESC,p.id DESC',
        'amount_asc': 'p.amount ASC,p.id ASC',
        'entity_asc': 'LOWER(entity_name) ASC,p.id DESC',
        'entity_desc': 'LOWER(entity_name) DESC,p.id DESC',
    }.get(sort, 'p.payment_date DESC,p.id DESC')
    sql = """SELECT p.id,p.entity_type,p.entity_id,p.amount,p.payment_date,p.note,
      COALESCE(p.payment_method,'cash') payment_method,p.reference_type,p.reference_id,p.account_id,p.financial_transaction_id,
      CASE WHEN p.entity_type='customer' THEN c.name ELSE s.name END entity_name,
      CASE WHEN p.reference_type IS NULL THEN 0 ELSE 1 END is_document_payment
      FROM payments p
      LEFT JOIN customers c ON p.entity_type='customer' AND c.id=p.entity_id AND c.company_id=:cid
      LEFT JOIN suppliers s ON p.entity_type='supplier' AND s.id=p.entity_id AND s.company_id=:cid
      WHERE p.company_id=:cid AND LOWER(COALESCE(CASE WHEN p.entity_type='customer' THEN c.name ELSE s.name END,'')) LIKE LOWER(:q)"""
    params = {'cid': cid, 'q': f'%{q}%', 'limit': limit}
    if entity_type:
        sql += ' AND p.entity_type=:t'
        params['t'] = entity_type
    if date_from:
        sql += ' AND p.payment_date>=:df'
        params['df'] = date_from
    if date_to:
        sql += ' AND p.payment_date<=:dt'
        params['dt'] = date_to
    if payment_method:
        sql += " AND COALESCE(p.payment_method,'cash')=:pm"
        params['pm'] = payment_method
    if source == 'manual':
        sql += ' AND p.reference_type IS NULL'
    elif source == 'document':
        sql += ' AND p.reference_type IS NOT NULL'
    rows = db.execute(text(sql + f' ORDER BY {sort_sql} LIMIT :limit'), params).mappings().all()
    return [dict(x) for x in rows]


@router.get('/payments/summary')
def payments_summary(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    sql = """SELECT
      COALESCE(SUM(CASE WHEN entity_type='customer' THEN amount ELSE 0 END),0) customer_total,
      COALESCE(SUM(CASE WHEN entity_type='supplier' THEN amount ELSE 0 END),0) supplier_total,
      COALESCE(SUM(CASE WHEN reference_type IS NULL THEN amount ELSE 0 END),0) manual_total,
      COALESCE(SUM(CASE WHEN reference_type IS NOT NULL THEN amount ELSE 0 END),0) document_total,
      COUNT(*) movement_count
      FROM payments WHERE company_id=:cid"""
    params = {'cid': cid}
    if date_from:
        sql += ' AND payment_date>=:df'
        params['df'] = date_from
    if date_to:
        sql += ' AND payment_date<=:dt'
        params['dt'] = date_to
    row = db.execute(text(sql), params).mappings().first()
    return dict(row)


@router.get('/payments/accounts', response_model=list[PaymentAccountOption])
def list_payment_accounts(
    request: Request,
    active_only: bool = False,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    statement = (
        select(
            finance_accounts.c.id,
            finance_accounts.c.name,
            finance_accounts.c.account_type,
            finance_accounts.c.currency,
            finance_accounts.c.is_active,
        )
        .where(finance_accounts.c.company_id == cid)
        .order_by(finance_accounts.c.account_type, finance_accounts.c.name)
    )
    if active_only:
        statement = statement.where(finance_accounts.c.is_active.is_(True))
    return [
        dict(row)
        for row in db.execute(statement).mappings().all()
    ]


@router.post('/payments', status_code=201)
def add(payload: PaymentCreate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _validate_payment(payload)
    _ensure_entity(db, cid, payload.entity_type, payload.entity_id)
    validate_payment_account(db, cid, payload.payment_method, payload.account_id)
    if payload.reference_type is not None and payload.reference_id is not None:
        validate_payment_reference(
            db,
            cid,
            payload.entity_type,
            payload.entity_id,
            payload.reference_type,
            payload.reference_id,
        )
    if settings.payment_allocation_engine_enabled:
        values = payload.model_dump()

        def _log_created(payment_id: int, _result: dict) -> None:
            # Motorun commit'inden hemen ÖNCE, aynı transaction içinde.
            log_request_activity(
                db, request, cid, 'payment.create', 'payment', payment_id,
                _payment_summary(
                    db, cid, 'oluşturdu', payment_id,
                    payload.entity_type, payload.entity_id, payload.amount,
                ),
                {'created': {
                    'amount': payload.amount,
                    'payment_date': payload.payment_date,
                    'payment_method': payload.payment_method,
                    'entity_type': payload.entity_type,
                    'entity_id': payload.entity_id,
                    'reference_type': payload.reference_type,
                    'reference_id': payload.reference_id,
                }},
            )

        try:
            return create_payment_with_allocation(
                db,
                cid,
                values,
                request.headers.get('idempotency-key', ''),
                created_by=int(request.state.user['id']),
                on_created=_log_created,
            )
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            logger.exception('Unexpected allocated payment create failure')
            raise HTTPException(500, 'Ödeme kaydedilemedi. Lütfen tekrar deneyin.') from exc
    if payload.reference_type is not None:
        raise HTTPException(409, 'Belge tahsisi için allocation motoru etkinleştirilmelidir')
    values = payload.model_dump(); values['company_id'] = cid
    values.pop('reference_type', None); values.pop('reference_id', None)
    result = db.execute(text('''INSERT INTO payments(entity_type,entity_id,amount,payment_date,note,company_id,payment_method,account_id)
      VALUES(:entity_type,:entity_id,:amount,:payment_date,:note,:company_id,:payment_method,:account_id) RETURNING id'''), values)
    payment_id = int(result.scalar_one())
    try:
        sync_payment_finance(db,cid,payment_id,payload.entity_type,payload.amount,payload.payment_date,payload.payment_method,payload.note,payload.account_id)
        log_request_activity(
            db, request, cid, 'payment.create', 'payment', payment_id,
            _payment_summary(
                db, cid, 'oluşturdu', payment_id,
                payload.entity_type, payload.entity_id, payload.amount,
            ),
            {'created': {
                'amount': payload.amount,
                'payment_date': payload.payment_date,
                'payment_method': payload.payment_method,
                'entity_type': payload.entity_type,
                'entity_id': payload.entity_id,
            }},
        )
        db.commit()
    except Exception as exc:
        db.rollback(); logger.exception('Unexpected payment create failure'); raise HTTPException(500,'Ödeme kaydedilemedi. Lütfen tekrar deneyin.') from exc
    return {'id': payment_id, **payload.model_dump()}


@router.put('/payments/{payment_id}')
def update_payment(payment_id:int,payload:PaymentCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    _validate_payment(payload)
    _ensure_entity(db,cid,payload.entity_type,payload.entity_id)
    validate_payment_account(db, cid, payload.payment_method, payload.account_id)
    existing=db.execute(text('SELECT * FROM payments WHERE id=:id AND company_id=:cid'),{'id':payment_id,'cid':cid}).mappings().first()
    if not existing: raise HTTPException(404,'Hareket bulunamadı')
    if existing['reference_type'] is not None:
        raise HTTPException(409,'Belgeye bağlı otomatik hareket belge üzerinden düzenlenmelidir.')
    values=payload.model_dump();values.update({'id':payment_id,'cid':cid})
    if settings.payment_allocation_engine_enabled:
        try:
            if payload.reference_type is not None and payload.reference_id is not None:
                validate_payment_reference(
                    db,
                    cid,
                    payload.entity_type,
                    payload.entity_id,
                    payload.reference_type,
                    payload.reference_id,
                )
            after = update_unallocated_payment(
                db,
                cid,
                payment_id,
                payload.model_dump(),
            )
            record_change(db,request,company_id=cid,entity_type='payment',entity_id=payment_id,action='update',before=dict(existing),after=after)
            log_request_activity(
                db, request, cid, 'payment.update', 'payment', payment_id,
                _payment_summary(
                    db, cid, 'güncelledi', payment_id,
                    payload.entity_type, payload.entity_id, payload.amount,
                ),
                {'changes': diff_details(dict(existing), dict(after), PAYMENT_AUDIT_FIELDS)},
            )
            db.commit()
            return {'id': payment_id}
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            logger.exception('Unexpected allocated payment update failure', extra={'payment_id': payment_id})
            raise HTTPException(500,'Ödeme güncellenemedi. Lütfen tekrar deneyin.') from exc
    values.pop('reference_type', None); values.pop('reference_id', None)
    try:
        db.execute(text('''UPDATE payments SET entity_type=:entity_type,entity_id=:entity_id,amount=:amount,
          payment_date=:payment_date,note=:note,payment_method=:payment_method,account_id=:account_id WHERE id=:id AND company_id=:cid'''),values)
        sync_payment_finance(db,cid,payment_id,payload.entity_type,payload.amount,payload.payment_date,payload.payment_method,payload.note,payload.account_id)
        after=db.execute(text('SELECT * FROM payments WHERE id=:id AND company_id=:cid'),{'id':payment_id,'cid':cid}).mappings().first()
        record_change(db,request,company_id=cid,entity_type='payment',entity_id=payment_id,action='update',before=dict(existing),after=dict(after))
        log_request_activity(
            db, request, cid, 'payment.update', 'payment', payment_id,
            _payment_summary(
                db, cid, 'güncelledi', payment_id,
                payload.entity_type, payload.entity_id, payload.amount,
            ),
            {'changes': diff_details(dict(existing), dict(after), PAYMENT_AUDIT_FIELDS)},
        )
        db.commit();return {'id':payment_id}
    except Exception as exc:
        db.rollback(); logger.exception('Unexpected payment update failure', extra={'payment_id': payment_id}); raise HTTPException(500,'Ödeme güncellenemedi. Lütfen tekrar deneyin.') from exc


@router.delete('/payments/{payment_id}', status_code=204)
def delete(payment_id: int, request: Request, db: Session = Depends(get_db)):
    cid=company_id(request)
    existing=db.execute(text('SELECT * FROM payments WHERE id=:id AND company_id=:cid'),{'id':payment_id,'cid':cid}).mappings().first()
    if not existing: raise HTTPException(404,'Hareket bulunamadı')
    if existing['reference_type'] is not None:
        raise HTTPException(409,'Belgeye bağlı otomatik hareket belge üzerinden silinmelidir.')
    if settings.payment_allocation_engine_enabled:
        try:
            before = delete_unallocated_payment(db, cid, payment_id)
            record_change(db,request,company_id=cid,entity_type='payment',entity_id=payment_id,action='delete',before=before,after=None)
            log_request_activity(
                db, request, cid, 'payment.delete', 'payment', payment_id,
                _payment_summary(
                    db, cid, 'sildi', payment_id,
                    str(existing['entity_type']), existing['entity_id'], existing['amount'],
                ),
                {'deleted': {field: existing[field] for field in PAYMENT_AUDIT_FIELDS}},
            )
            db.commit()
            return
        except HTTPException:
            db.rollback()
            raise
    remove_payment_finance(db,cid,payment_id)
    db.execute(text('DELETE FROM payments WHERE id=:id AND company_id=:cid'), {'id': payment_id, 'cid': cid})
    record_change(db,request,company_id=cid,entity_type='payment',entity_id=payment_id,action='delete',before=dict(existing),after=None)
    log_request_activity(
        db, request, cid, 'payment.delete', 'payment', payment_id,
        _payment_summary(
            db, cid, 'sildi', payment_id,
            str(existing['entity_type']), existing['entity_id'], existing['amount'],
        ),
        {'deleted': {field: existing[field] for field in PAYMENT_AUDIT_FIELDS}},
    )
    db.commit()


@router.post('/suppliers',status_code=201)
def create_supplier(payload:CustomerCreate,request:Request,db:Session=Depends(get_db)):
    values=_entity_values(payload);values['company_id']=company_id(request)
    result=db.execute(text('''INSERT INTO suppliers(name,owner_name,phone,email,address,tax_number,opening_balance,risk_limit,payment_term_days,notes,is_active,company_id)
      VALUES(:name,:owner_name,:phone,:email,:address,:tax_number,:opening_balance,:risk_limit,:payment_term_days,:notes,:is_active,:company_id) RETURNING id'''),values)
    supplier_id=int(result.scalar_one())
    db.commit();return {'id':supplier_id,**payload.model_dump()}


# Shares the customer detail projection; see app/entity_detail.py. No docstring
# on purpose -- FastAPI would publish it as the OpenAPI description.
@router.get('/suppliers/{supplier_id}')
def supplier_detail(supplier_id:int,request:Request,db:Session=Depends(get_db)):
    return entity_detail('supplier',supplier_id,request,db)


# Kartın önizleme listesi kısa tutulur; TÜM alış geçmişi buradan sayfalanır.
# Docstring yok -- FastAPI onu OpenAPI açıklaması olarak yayımlar.
@router.get('/suppliers/{supplier_id}/documents')
def supplier_documents(supplier_id:int,request:Request,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),year:str|None=Query(None,pattern=r'^\d{4}$'),db:Session=Depends(get_db)):
    return entity_documents('supplier',supplier_id,request,db,offset=offset,limit=limit,year=year)


@router.get('/suppliers/{supplier_id}/statement',response_model=Statement)
def supplier_statement(supplier_id:int,request:Request,date_from:date|None=None,date_to:date|None=None,db:Session=Depends(get_db)):
    """Yazdırılabilir cari hesap ekstresi: devir + dönem hareketleri + yürüyen bakiye."""
    return build_statement(db,company_id(request),'supplier',supplier_id,date_from,date_to)


@router.put('/suppliers/{supplier_id}')
def update_supplier(supplier_id:int,payload:CustomerCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request);before=db.execute(text('SELECT * FROM suppliers WHERE id=:id AND company_id=:cid'),{'id':supplier_id,'cid':cid}).mappings().first()
    values=_entity_values(payload);values.update({'id':supplier_id,'cid':cid})
    result=db.execute(text('''UPDATE suppliers SET name=:name,owner_name=:owner_name,phone=:phone,email=:email,address=:address,
      tax_number=:tax_number,opening_balance=:opening_balance,risk_limit=:risk_limit,payment_term_days=:payment_term_days,
      notes=:notes,is_active=:is_active WHERE id=:id AND company_id=:cid'''),values)
    if not result.rowcount: db.rollback(); raise HTTPException(404,'Tedarikçi bulunamadı')
    after=db.execute(text('SELECT * FROM suppliers WHERE id=:id AND company_id=:cid'),{'id':supplier_id,'cid':cid}).mappings().first()
    record_change(db,request,company_id=cid,entity_type='supplier',entity_id=supplier_id,action='update',before=dict(before),after=dict(after))
    db.commit();return {'id':supplier_id}


@router.post('/suppliers/{supplier_id}/notes',status_code=201)
def create_supplier_note(supplier_id:int,payload:EntityNoteCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if not db.execute(text('SELECT id FROM suppliers WHERE id=:id AND company_id=:cid'),{'id':supplier_id,'cid':cid}).first(): raise HTTPException(404,'Tedarikçi bulunamadı')
    try: result=add_note(db,cid,'supplier',supplier_id,payload.note,getattr(request.state,'user',{}).get('username'))
    except ValueError as exc: raise HTTPException(400,str(exc))
    db.commit();return result


@router.delete('/suppliers/{supplier_id}/notes/{note_id}',status_code=204)
def remove_supplier_note(supplier_id:int,note_id:int,request:Request,db:Session=Depends(get_db)):
    if not delete_note(db,company_id(request),'supplier',supplier_id,note_id): raise HTTPException(404,'Not bulunamadı')
    db.commit()



@router.post('/suppliers/{supplier_id}/contacts',status_code=201)
def create_supplier_contact(supplier_id:int,payload:EntityContactCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if not db.execute(text('SELECT id FROM suppliers WHERE id=:id AND company_id=:cid'),{'id':supplier_id,'cid':cid}).first(): raise HTTPException(404,'Tedarikçi bulunamadı')
    try: result=add_contact(db,cid,'supplier',supplier_id,payload.model_dump())
    except ValueError as exc: raise HTTPException(400,str(exc))
    db.commit();return result

@router.delete('/suppliers/{supplier_id}/contacts/{contact_id}',status_code=204)
def remove_supplier_contact(supplier_id:int,contact_id:int,request:Request,db:Session=Depends(get_db)):
    if not delete_contact(db,company_id(request),'supplier',supplier_id,contact_id): raise HTTPException(404,'Yetkili kişi bulunamadı')
    db.commit()

@router.post('/suppliers/{supplier_id}/tasks',status_code=201)
def create_supplier_task(supplier_id:int,payload:EntityTaskCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if not db.execute(text('SELECT id FROM suppliers WHERE id=:id AND company_id=:cid'),{'id':supplier_id,'cid':cid}).first(): raise HTTPException(404,'Tedarikçi bulunamadı')
    try: result=add_task(db,cid,'supplier',supplier_id,payload.model_dump())
    except ValueError as exc: raise HTTPException(400,str(exc))
    db.commit();return result

@router.patch('/suppliers/{supplier_id}/tasks/{task_id}')
def update_supplier_task_status(supplier_id:int,task_id:int,payload:EntityTaskStatusUpdate,request:Request,db:Session=Depends(get_db)):
    try: ok=set_task_status(db,company_id(request),'supplier',supplier_id,task_id,payload.status)
    except ValueError as exc: raise HTTPException(400,str(exc))
    if not ok: raise HTTPException(404,'Görev bulunamadı')
    db.commit();return {'id':task_id,'status':payload.status}

@router.delete('/suppliers/{supplier_id}/tasks/{task_id}',status_code=204)
def remove_supplier_task(supplier_id:int,task_id:int,request:Request,db:Session=Depends(get_db)):
    if not delete_task(db,company_id(request),'supplier',supplier_id,task_id): raise HTTPException(404,'Görev bulunamadı')
    db.commit()

@router.patch('/suppliers/{supplier_id}/active')
def set_supplier_active(supplier_id:int,request:Request,active:bool=True,db:Session=Depends(get_db)):
    cid=company_id(request);before=db.execute(text('SELECT * FROM suppliers WHERE id=:id AND company_id=:cid'),{'id':supplier_id,'cid':cid}).mappings().first()
    result=db.execute(text('UPDATE suppliers SET is_active=:active WHERE id=:id AND company_id=:cid'),{'active':bool(active),'id':supplier_id,'cid':cid})
    if not result.rowcount: raise HTTPException(404,'Tedarikçi bulunamadı')
    after=db.execute(text('SELECT * FROM suppliers WHERE id=:id AND company_id=:cid'),{'id':supplier_id,'cid':cid}).mappings().first()
    record_change(db,request,company_id=cid,entity_type='supplier',entity_id=supplier_id,action='update',before=dict(before),after=dict(after))
    db.commit();return {'id':supplier_id,'is_active':active}


@router.delete('/suppliers/{supplier_id}',status_code=204)
def delete_supplier(supplier_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    refs=db.execute(text("SELECT (SELECT COUNT(*) FROM purchases WHERE supplier_id=:id AND company_id=:cid)+(SELECT COUNT(*) FROM payments WHERE entity_type='supplier' AND entity_id=:id AND company_id=:cid)"),{'id':supplier_id,'cid':cid}).scalar() or 0
    if refs: raise HTTPException(409,'Bu tedarikçinin alış veya ödeme hareketleri var; silmek yerine pasife alın.')
    before=db.execute(text('SELECT * FROM suppliers WHERE id=:id AND company_id=:cid'),{'id':supplier_id,'cid':cid}).mappings().first()
    db.execute(text("DELETE FROM entity_notes WHERE company_id=:cid AND entity_type='supplier' AND entity_id=:id"),{'id':supplier_id,'cid':cid})
    result=db.execute(text('DELETE FROM suppliers WHERE id=:id AND company_id=:cid'),{'id':supplier_id,'cid':cid})
    if not result.rowcount: db.rollback(); raise HTTPException(404,'Tedarikçi bulunamadı')
    record_change(db,request,company_id=cid,entity_type='supplier',entity_id=supplier_id,action='delete',before=dict(before),after=None)
    db.commit()

ACCOUNT_TYPE_LABELS={'cash':'Kasa','bank':'Banka','pos':'POS'}
INSTRUMENT_STATUSES={'portfolio','due','collected','paid','endorsed','bounced','cancelled'}

def _account_row(db:Session,cid:int,account_id:int):
    row=db.execute(select(finance_accounts).where(finance_accounts.c.id==account_id,finance_accounts.c.company_id==cid)).mappings().first()
    if not row: raise HTTPException(404,'Finans hesabı bulunamadı')
    return row

@router.get('/finance/summary')
def finance_summary(request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    accounts=db.execute(text('''SELECT a.id,a.name,a.account_type,a.currency,a.opening_balance,a.bank_name,a.iban,a.is_active,
      COALESCE(a.opening_balance,0)+COALESCE(SUM(CASE WHEN t.direction='in' THEN t.amount ELSE -t.amount END),0) balance
      FROM finance_accounts a LEFT JOIN finance_transactions t ON t.account_id=a.id AND t.company_id=:cid
      WHERE a.company_id=:cid GROUP BY a.id ORDER BY a.account_type,a.name'''),{'cid':cid}).mappings().all()
    totals={'cash':ZERO_MONEY,'bank':ZERO_MONEY,'pos':ZERO_MONEY}
    for a in accounts: totals[a['account_type']]=totals.get(a['account_type'],ZERO_MONEY)+money(a['balance'])
    due_received=money(db.execute(text("SELECT COALESCE(SUM(amount),0) FROM financial_instruments WHERE company_id=:cid AND direction='received' AND status IN ('portfolio','due')"),{'cid':cid}).scalar())
    due_issued=money(db.execute(text("SELECT COALESCE(SUM(amount),0) FROM financial_instruments WHERE company_id=:cid AND direction='issued' AND status IN ('portfolio','due')"),{'cid':cid}).scalar())
    return {'accounts':[dict(x) for x in accounts],'totals':totals,'instruments':{'received_open':due_received,'issued_open':due_issued}}

@router.get('/finance/accounts')
def list_accounts(request:Request,active_only:bool=False,db:Session=Depends(get_db)):
    cid=company_id(request)
    sql='''SELECT a.id,a.name,a.account_type,a.currency,a.opening_balance,a.bank_name,a.iban,a.is_active,
      COALESCE(a.opening_balance,0)+COALESCE(SUM(CASE WHEN t.direction='in' THEN t.amount ELSE -t.amount END),0) balance,
      COUNT(t.id) transaction_count FROM finance_accounts a LEFT JOIN finance_transactions t ON t.account_id=a.id AND t.company_id=:cid
      WHERE a.company_id=:cid'''
    if active_only: sql+=' AND a.is_active=TRUE'
    sql+=' GROUP BY a.id ORDER BY a.account_type,a.name'
    return [dict(x) for x in db.execute(text(sql),{'cid':cid}).mappings().all()]

@router.post('/finance/accounts',status_code=201)
def create_account(payload:FinancialAccountCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if payload.account_type not in ACCOUNT_TYPES: raise HTTPException(400,'Geçersiz hesap türü')
    result=db.execute(insert(finance_accounts).values(company_id=cid,created_at=utcnow(),**payload.model_dump()))
    db.commit();return {'id':int(result.inserted_primary_key[0])}

@router.put('/finance/accounts/{account_id}')
def update_account(account_id:int,payload:FinancialAccountCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request);_account_row(db,cid,account_id)
    if payload.account_type not in ACCOUNT_TYPES: raise HTTPException(400,'Geçersiz hesap türü')
    db.execute(text('''UPDATE finance_accounts SET name=:name,account_type=:account_type,currency=:currency,opening_balance=:opening_balance,
      bank_name=:bank_name,iban=:iban,is_active=:is_active WHERE id=:id AND company_id=:cid'''),{**payload.model_dump(),'id':account_id,'cid':cid})
    db.commit();return {'id':account_id}

@router.delete('/finance/accounts/{account_id}',status_code=204)
def delete_account(account_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request);_account_row(db,cid,account_id)
    refs=int(db.execute(text('SELECT COUNT(*) FROM finance_transactions WHERE account_id=:id AND company_id=:cid'),{'id':account_id,'cid':cid}).scalar() or 0)
    if refs: raise HTTPException(409,'Hesabın hareketleri var; silmek yerine pasife alın.')
    db.execute(text('DELETE FROM finance_accounts WHERE id=:id AND company_id=:cid'),{'id':account_id,'cid':cid});db.commit()

@router.get('/finance/transactions')
def list_finance_transactions(request:Request,account_id:int|None=None,date_from:str|None=None,date_to:str|None=None,q:str='',db:Session=Depends(get_db)):
    cid=company_id(request)
    sql='''SELECT t.*,a.name account_name,a.account_type FROM finance_transactions t JOIN finance_accounts a ON a.id=t.account_id AND a.company_id=t.company_id
      WHERE t.company_id=:cid AND LOWER(COALESCE(t.description,'')) LIKE LOWER(:q)''';params={'cid':cid,'q':f'%{q}%'}
    if account_id: sql+=' AND t.account_id=:aid';params['aid']=account_id
    if date_from: sql+=' AND t.txn_date>=:df';params['df']=date_from
    if date_to: sql+=' AND t.txn_date<=:dt';params['dt']=date_to
    sql+=' ORDER BY t.txn_date DESC,t.id DESC LIMIT 3000'
    return [dict(x) for x in db.execute(text(sql),params).mappings().all()]

@router.post('/finance/transactions',status_code=201)
def create_finance_transaction(payload:FinancialTransactionCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request);_account_row(db,cid,payload.account_id)
    if payload.direction not in {'in','out'}: raise HTTPException(400,'Hareket yönü giriş veya çıkış olmalıdır')
    result=db.execute(insert(finance_transactions).values(company_id=cid,created_at=utcnow(),reference_type='manual',reference_id=None,transfer_group=None,**payload.model_dump()))
    db.commit();return {'id':int(result.inserted_primary_key[0])}

@router.delete('/finance/transactions/{transaction_id}',status_code=204)
def delete_finance_transaction(transaction_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    row=db.execute(text('SELECT reference_type,transfer_group FROM finance_transactions WHERE id=:id AND company_id=:cid'),{'id':transaction_id,'cid':cid}).mappings().first()
    if not row: raise HTTPException(404,'Hareket bulunamadı')
    if row['reference_type']!='manual': raise HTTPException(409,'Bağlı hareket kaynak belge üzerinden silinmelidir.')
    db.execute(text('DELETE FROM finance_transactions WHERE id=:id AND company_id=:cid'),{'id':transaction_id,'cid':cid});db.commit()

@router.post('/finance/transfers',status_code=201)
def transfer(payload:FinancialTransferCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if payload.source_account_id==payload.target_account_id: raise HTTPException(400,'Kaynak ve hedef hesap farklı olmalıdır')
    source=_account_row(db,cid,payload.source_account_id);target=_account_row(db,cid,payload.target_account_id)
    if source['currency']!=target['currency']: raise HTTPException(400,'Farklı para birimleri arasında kur bilgisi olmadan virman yapılamaz')
    group=f'TRF-{cid}-{int(utcnow().timestamp()*1000000)}'
    for aid,direction in [(payload.source_account_id,'out'),(payload.target_account_id,'in')]:
        db.execute(insert(finance_transactions).values(company_id=cid,account_id=aid,txn_date=payload.txn_date,direction=direction,amount=payload.amount,category='transfer',payment_method='transfer',description=payload.description or 'Hesaplar arası virman',reference_type='transfer',reference_id=None,transfer_group=group,created_at=utcnow()))
    db.commit();return {'transfer_group':group}

@router.get('/finance/instruments')
def list_instruments(request:Request,instrument_type:str|None=None,status:str|None=None,db:Session=Depends(get_db)):
    cid=company_id(request)
    sql='''SELECT i.*,CASE WHEN i.entity_type='customer' THEN c.name WHEN i.entity_type='supplier' THEN s.name ELSE NULL END entity_name,
      a.name account_name FROM financial_instruments i LEFT JOIN customers c ON i.entity_type='customer' AND c.id=i.entity_id AND c.company_id=i.company_id
      LEFT JOIN suppliers s ON i.entity_type='supplier' AND s.id=i.entity_id AND s.company_id=i.company_id LEFT JOIN finance_accounts a ON a.id=i.account_id AND a.company_id=i.company_id WHERE i.company_id=:cid''';params={'cid':cid}
    if instrument_type: sql+=' AND i.instrument_type=:t';params['t']=instrument_type
    if status: sql+=' AND i.status=:s';params['s']=status
    sql+=' ORDER BY i.due_date,i.id DESC'
    return [dict(x) for x in db.execute(text(sql),params).mappings().all()]

@router.post('/finance/instruments',status_code=201)
def create_instrument(payload:FinancialInstrumentCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if payload.instrument_type not in {'check','promissory_note'}: raise HTTPException(400,'Geçersiz evrak türü')
    if payload.direction not in {'received','issued'}: raise HTTPException(400,'Geçersiz evrak yönü')
    if payload.entity_type and payload.entity_id:_ensure_entity(db,cid,payload.entity_type,payload.entity_id)
    result=db.execute(insert(financial_instruments).values(company_id=cid,status='portfolio',created_at=utcnow(),account_id=None,financial_transaction_id=None,**payload.model_dump()))
    db.commit();return {'id':int(result.inserted_primary_key[0])}

@router.put('/finance/instruments/{instrument_id}/status')
def update_instrument_status(instrument_id:int,payload:FinancialInstrumentStatusUpdate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if payload.status not in INSTRUMENT_STATUSES: raise HTTPException(400,'Geçersiz evrak durumu')
    row=db.execute(select(financial_instruments).where(financial_instruments.c.id==instrument_id,financial_instruments.c.company_id==cid)).mappings().first()
    if not row: raise HTTPException(404,'Evrak bulunamadı')
    if row['financial_transaction_id']:
        db.execute(text('DELETE FROM finance_transactions WHERE id=:id AND company_id=:cid'),{'id':row['financial_transaction_id'],'cid':cid})
    txid=None;account_id=None
    cash_status=(row['direction']=='received' and payload.status=='collected') or (row['direction']=='issued' and payload.status=='paid')
    if cash_status:
        if not payload.account_id: raise HTTPException(400,'Tahsil/ödeme hesabı seçilmelidir')
        _account_row(db,cid,payload.account_id);account_id=payload.account_id
        direction='in' if row['direction']=='received' else 'out'
        result=db.execute(insert(finance_transactions).values(company_id=cid,account_id=account_id,txn_date=payload.transaction_date or row['due_date'],direction=direction,amount=row['amount'],category='instrument',payment_method=row['instrument_type'],description=f"{'Çek' if row['instrument_type']=='check' else 'Senet'} #{instrument_id}",reference_type='instrument',reference_id=instrument_id,transfer_group=None,created_at=utcnow()))
        txid=int(result.inserted_primary_key[0])
    db.execute(text('UPDATE financial_instruments SET status=:s,account_id=:aid,financial_transaction_id=:txid WHERE id=:id AND company_id=:cid'),{'s':payload.status,'aid':account_id,'txid':txid,'id':instrument_id,'cid':cid})
    db.commit();return {'id':instrument_id,'status':payload.status}

@router.delete('/finance/instruments/{instrument_id}',status_code=204)
def delete_instrument(instrument_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    row=db.execute(select(financial_instruments.c.financial_transaction_id).where(financial_instruments.c.id==instrument_id,financial_instruments.c.company_id==cid)).first()
    if not row: raise HTTPException(404,'Evrak bulunamadı')
    if row[0]: raise HTTPException(409,'Tahsil edilmiş/ödenmiş evrak silinemez; önce durumunu değiştirin.')
    db.execute(text('DELETE FROM financial_instruments WHERE id=:id AND company_id=:cid'),{'id':instrument_id,'cid':cid});db.commit()

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from ..change_history import record_change
from ..crm import add_contact, add_note, add_task, delete_contact, delete_note, delete_task, set_task_status
from ..business_time import business_today
from ..db import get_db
from ..entity_detail import entity_detail, entity_documents
from ..document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
from ..money import HUNDRED, money
from ..receivables_engine import charge_due_date_sql
from ..schemas import CustomerCreate
from ..statement import Statement, build_statement
from ..tenancy import company_id

router = APIRouter(prefix='/customers', tags=['customers'])
SORTS = {
    'name_asc':'LOWER(c.name) ASC', 'name_desc':'LOWER(c.name) DESC',
    'balance_asc':'current_balance ASC', 'balance_desc':'current_balance DESC',
    'activity_desc':'last_activity DESC', 'overdue_desc':'overdue_amount DESC',
}

class NoteCreate(BaseModel):
    note: str = Field(min_length=1, max_length=2000)

class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    role: str | None = None
    phone: str | None = None
    email: str | None = None
    is_primary: bool = False
    notes: str | None = None

class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    due_date: str | None = None
    priority: str = 'normal'
    assigned_to: str | None = None
    notes: str | None = None

class TaskStatusUpdate(BaseModel):
    status: str


def _values(payload: CustomerCreate) -> dict:
    values = payload.model_dump()
    # PostgreSQL's is_active column is boolean; pass a real bool (not 1/0) so the
    # INSERT/UPDATE does not raise a datatype mismatch. SQLite is unaffected.
    values['is_active'] = bool(values['is_active'])
    return values

@router.get('')
def list_customers(request: Request, q: str = '', sort: str = 'name_asc', active: str = 'active',
                   limit: int = Query(500, ge=1, le=2000), db: Session = Depends(get_db)):
    cid=company_id(request);order=SORTS.get(sort,SORTS['name_asc']);today_date=business_today();today=today_date.isoformat()
    active_sql = '' if active == 'all' else (' AND COALESCE(c.is_active, TRUE)=FALSE' if active == 'inactive' else ' AND COALESCE(c.is_active, TRUE)=TRUE')
    # ``chg`` carries the posted late-fee/service-fee charge documents that never
    # hit the ``orders`` table. Its GROSS feeds current_balance (charge payments
    # already live in ``pay.total_paid``, so netting the applied amount here too
    # would double-count them); the overdue column nets its own allocations
    # because overdue_amount is not reduced by ``pay``. Reversals cancel via the
    # negative counter-gross. Overdue uses the one shared due-date rule
    # (receivables_engine.charge_due_date_sql) and clamps
    # allocations to effective_date<=as_of. Kept identical to entity_detail so
    # the list agrees with the cari detail card. The period_end<=:as_of filter
    # below is document scope, not vade, and is left alone.
    rows=db.execute(
      text(f"""SELECT c.id,c.name,c.owner_name,c.phone,c.email,c.address,c.tax_number,c.opening_balance,
      COALESCE(c.risk_limit,0) risk_limit,COALESCE(c.payment_term_days,0) payment_term_days,CASE WHEN COALESCE(c.is_active, TRUE) THEN 1 ELSE 0 END is_active,
      COALESCE(c.opening_balance,0)+COALESCE(SUM(CASE WHEN {accounting_document_status_sql(status_column='o.status', note_column='o.note')} THEN o.final_total ELSE 0 END),0)
      +COALESCE(chg.charge_total,0)-COALESCE(pay.total_paid,0) current_balance,
      COALESCE(SUM(CASE WHEN o.due_date IS NOT NULL AND o.due_date<>'' AND o.due_date<:today
        AND {accounting_document_status_sql(status_column='o.status', note_column='o.note')}
        THEN CASE WHEN COALESCE(o.final_total,0)-COALESCE(o.paid_amount,0)>0 THEN COALESCE(o.final_total,0)-COALESCE(o.paid_amount,0) ELSE 0 END ELSE 0 END),0)
      +COALESCE(chg.charge_overdue,0) overdue_amount,
      COUNT(DISTINCT CASE WHEN {accounting_document_status_sql(status_column='o.status', note_column='o.note')} THEN o.id END) order_count,
      MAX(CASE WHEN {accounting_document_status_sql(status_column='o.status', note_column='o.note')} THEN o.order_date END) last_activity
      FROM customers c
      LEFT JOIN orders o ON o.customer_id=c.id AND o.company_id=:cid
      LEFT JOIN (
        SELECT entity_id,SUM(amount) total_paid FROM payments
        WHERE company_id=:cid AND entity_type='customer' GROUP BY entity_id
      ) pay ON pay.entity_id=c.id
      LEFT JOIN (
        SELECT d.customer_id customer_id,
          COALESCE(SUM(d.gross_amount),0) charge_total,
          COALESCE(SUM(CASE WHEN {charge_due_date_sql('d')}<:as_of
            THEN d.gross_amount-COALESCE(a.applied,0) ELSE 0 END),0) charge_overdue
        FROM receivable_charge_documents d
        LEFT JOIN (
          SELECT receivable_charge_id,COALESCE(SUM(
            CASE WHEN reversal_of_allocation_id IS NULL THEN amount ELSE -amount END),0) applied
          FROM payment_allocations
          WHERE company_id=:cid AND receivable_charge_id IS NOT NULL
            AND effective_date<=:as_of
          GROUP BY receivable_charge_id
        ) a ON a.receivable_charge_id=d.id
        WHERE d.company_id=:cid AND d.charge_type IN ('late_fee','service_fee')
          AND d.status IN ('posted','reversed') AND d.posted_at IS NOT NULL
          AND d.period_end<=:as_of
        GROUP BY d.customer_id
      ) chg ON chg.customer_id=c.id
      WHERE c.company_id=:cid {active_sql} AND (LOWER(c.name) LIKE LOWER(:q) OR COALESCE(c.phone,'') LIKE :q
       OR LOWER(COALESCE(c.email,'')) LIKE LOWER(:q) OR COALESCE(c.tax_number,'') LIKE :q)
       GROUP BY c.id,pay.total_paid,chg.charge_total,chg.charge_overdue ORDER BY {order} LIMIT :limit"""),
        {'cid': cid, 'q': f'%{q}%', 'limit': limit, 'today': today, 'as_of': today_date, 'sales_import_note': SALES_IMPORT_NOTE}
    ).mappings().all()
    result=[]
    for item in rows:
        row=dict(item);risk=money(row.get('risk_limit'));balance=money(row.get('current_balance'))
        row['risk_exceeded']=risk>0 and balance>risk
        row['risk_usage_percent']=round((balance/risk)*HUNDRED,1) if risk>0 else 0
        result.append(row)
    return result

@router.post('',status_code=201)
def create_customer(payload:CustomerCreate,request:Request,db:Session=Depends(get_db)):
    values=_values(payload);values['company_id']=company_id(request)
    result=db.execute(text('''INSERT INTO customers(name,owner_name,phone,email,address,tax_number,opening_balance,risk_limit,payment_term_days,notes,is_active,company_id)
      VALUES(:name,:owner_name,:phone,:email,:address,:tax_number,:opening_balance,:risk_limit,:payment_term_days,:notes,:is_active,:company_id) RETURNING id'''),values)
    customer_id=int(result.scalar_one())
    db.commit();return {'id':customer_id,**payload.model_dump()}

# The projection is shared with the supplier detail endpoint; see app/entity_detail.py.
# No docstring here on purpose -- FastAPI would publish it as the OpenAPI
# description and this refactor must stay invisible to the contract-drift gate.
@router.get('/{customer_id}')
def customer_detail(customer_id:int,request:Request,db:Session=Depends(get_db)):
    return entity_detail('customer',customer_id,request,db)

# Kartın önizleme listesi kısa tutulur; TÜM satış geçmişi buradan sayfalanır.
# Docstring yok -- FastAPI onu OpenAPI açıklaması olarak yayımlar.
@router.get('/{customer_id}/documents')
def customer_documents(customer_id:int,request:Request,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),year:str|None=Query(None,pattern=r'^\d{4}$'),db:Session=Depends(get_db)):
    return entity_documents('customer',customer_id,request,db,offset=offset,limit=limit,year=year)

@router.get('/{customer_id}/statement',response_model=Statement)
def customer_statement(customer_id:int,request:Request,date_from:date|None=None,date_to:date|None=None,db:Session=Depends(get_db)):
    """Yazdırılabilir cari hesap ekstresi: devir + dönem hareketleri + yürüyen bakiye."""
    return build_statement(db,company_id(request),'customer',customer_id,date_from,date_to)

@router.put('/{customer_id}')
def update_customer(customer_id:int,payload:CustomerCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    before=db.execute(text('SELECT * FROM customers WHERE id=:id AND company_id=:cid'),{'id':customer_id,'cid':cid}).mappings().first()
    values=_values(payload);values.update({'id':customer_id,'cid':cid})
    result=db.execute(text('''UPDATE customers SET name=:name,owner_name=:owner_name,phone=:phone,email=:email,address=:address,
      tax_number=:tax_number,opening_balance=:opening_balance,risk_limit=:risk_limit,payment_term_days=:payment_term_days,
      notes=:notes,is_active=:is_active WHERE id=:id AND company_id=:cid'''),values)
    if not result.rowcount:db.rollback();raise HTTPException(404,'Müşteri bulunamadı')
    after=db.execute(text('SELECT * FROM customers WHERE id=:id AND company_id=:cid'),{'id':customer_id,'cid':cid}).mappings().first()
    record_change(db,request,company_id=cid,entity_type='customer',entity_id=customer_id,action='update',before=dict(before),after=dict(after))
    db.commit();return {'id':customer_id}

@router.post('/{customer_id}/notes',status_code=201)
def create_note(customer_id:int,payload:NoteCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if not db.execute(text('SELECT id FROM customers WHERE id=:id AND company_id=:cid'),{'id':customer_id,'cid':cid}).first():
        raise HTTPException(404,'Müşteri bulunamadı')
    try: result=add_note(db,cid,'customer',customer_id,payload.note,getattr(request.state,'user',{}).get('username'))
    except ValueError as exc: raise HTTPException(400,str(exc))
    db.commit();return result

@router.delete('/{customer_id}/notes/{note_id}',status_code=204)
def remove_note(customer_id:int,note_id:int,request:Request,db:Session=Depends(get_db)):
    if not delete_note(db,company_id(request),'customer',customer_id,note_id): raise HTTPException(404,'Not bulunamadı')
    db.commit()


@router.post('/{customer_id}/contacts',status_code=201)
def create_contact(customer_id:int,payload:ContactCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if not db.execute(text('SELECT id FROM customers WHERE id=:id AND company_id=:cid'),{'id':customer_id,'cid':cid}).first(): raise HTTPException(404,'Müşteri bulunamadı')
    try: result=add_contact(db,cid,'customer',customer_id,payload.model_dump())
    except ValueError as exc: raise HTTPException(400,str(exc))
    db.commit();return result

@router.delete('/{customer_id}/contacts/{contact_id}',status_code=204)
def remove_contact(customer_id:int,contact_id:int,request:Request,db:Session=Depends(get_db)):
    if not delete_contact(db,company_id(request),'customer',customer_id,contact_id): raise HTTPException(404,'Yetkili kişi bulunamadı')
    db.commit()

@router.post('/{customer_id}/tasks',status_code=201)
def create_task(customer_id:int,payload:TaskCreate,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    if not db.execute(text('SELECT id FROM customers WHERE id=:id AND company_id=:cid'),{'id':customer_id,'cid':cid}).first(): raise HTTPException(404,'Müşteri bulunamadı')
    try: result=add_task(db,cid,'customer',customer_id,payload.model_dump())
    except ValueError as exc: raise HTTPException(400,str(exc))
    db.commit();return result

@router.patch('/{customer_id}/tasks/{task_id}')
def update_task_status(customer_id:int,task_id:int,payload:TaskStatusUpdate,request:Request,db:Session=Depends(get_db)):
    try: ok=set_task_status(db,company_id(request),'customer',customer_id,task_id,payload.status)
    except ValueError as exc: raise HTTPException(400,str(exc))
    if not ok: raise HTTPException(404,'Görev bulunamadı')
    db.commit();return {'id':task_id,'status':payload.status}

@router.delete('/{customer_id}/tasks/{task_id}',status_code=204)
def remove_task(customer_id:int,task_id:int,request:Request,db:Session=Depends(get_db)):
    if not delete_task(db,company_id(request),'customer',customer_id,task_id): raise HTTPException(404,'Görev bulunamadı')
    db.commit()

@router.patch('/{customer_id}/active')
def set_active(customer_id:int,request:Request,active:bool=True,db:Session=Depends(get_db)):
    cid=company_id(request);before=db.execute(text('SELECT * FROM customers WHERE id=:id AND company_id=:cid'),{'id':customer_id,'cid':cid}).mappings().first()
    result=db.execute(text('UPDATE customers SET is_active=:active WHERE id=:id AND company_id=:cid'),{'active':bool(active),'id':customer_id,'cid':cid})
    if not result.rowcount: raise HTTPException(404,'Müşteri bulunamadı')
    after=db.execute(text('SELECT * FROM customers WHERE id=:id AND company_id=:cid'),{'id':customer_id,'cid':cid}).mappings().first()
    record_change(db,request,company_id=cid,entity_type='customer',entity_id=customer_id,action='update',before=dict(before),after=dict(after))
    db.commit();return {'id':customer_id,'is_active':active}

@router.delete('/{customer_id}',status_code=204)
def delete_customer(customer_id:int,request:Request,db:Session=Depends(get_db)):
    cid=company_id(request)
    refs=db.execute(text("SELECT (SELECT COUNT(*) FROM orders WHERE customer_id=:id AND company_id=:cid)+(SELECT COUNT(*) FROM payments WHERE entity_type='customer' AND entity_id=:id AND company_id=:cid)"),{'id':customer_id,'cid':cid}).scalar() or 0
    if refs:raise HTTPException(409,'Bu müşterinin satış veya tahsilat hareketleri var; silmek yerine pasife alın.')
    before=db.execute(text('SELECT * FROM customers WHERE id=:id AND company_id=:cid'),{'id':customer_id,'cid':cid}).mappings().first()
    db.execute(text("DELETE FROM entity_notes WHERE company_id=:cid AND entity_type='customer' AND entity_id=:id"),{'id':customer_id,'cid':cid})
    result=db.execute(text('DELETE FROM customers WHERE id=:id AND company_id=:cid'),{'id':customer_id,'cid':cid})
    if not result.rowcount:db.rollback();raise HTTPException(404,'Müşteri bulunamadı')
    record_change(db,request,company_id=cid,entity_type='customer',entity_id=customer_id,action='delete',before=dict(before),after=None)
    db.commit()

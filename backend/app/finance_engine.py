from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import MetaData, Table, Column, Integer, String, Numeric, Boolean, DateTime, inspect, insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from fastapi import HTTPException

from .money import money

metadata = MetaData()
finance_accounts = Table(
    'finance_accounts', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('company_id', Integer, nullable=False, index=True),
    Column('name', String(180), nullable=False),
    Column('account_type', String(30), nullable=False), # cash, bank, pos
    Column('currency', String(8), nullable=False, default='TRY'),
    Column('opening_balance', Numeric(18, 2), nullable=False, default=0),
    Column('bank_name', String(160)),
    Column('iban', String(50)),
    Column('is_active', Boolean, nullable=False, default=True),
    Column('created_at', DateTime(timezone=True), nullable=False),
)
finance_transactions = Table(
    'finance_transactions', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('company_id', Integer, nullable=False, index=True),
    Column('account_id', Integer, nullable=False, index=True),
    Column('txn_date', String(30), nullable=False),
    Column('direction', String(10), nullable=False), # in/out
    Column('amount', Numeric(18, 2), nullable=False),
    Column('category', String(60)),
    Column('payment_method', String(30)),
    Column('description', String(500)),
    Column('reference_type', String(60)),
    Column('reference_id', Integer),
    Column('transfer_group', String(80)),
    Column('created_at', DateTime(timezone=True), nullable=False),
)
financial_instruments = Table(
    'financial_instruments', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('company_id', Integer, nullable=False, index=True),
    Column('instrument_type', String(30), nullable=False), # check/promissory_note
    Column('direction', String(20), nullable=False), # received/issued
    Column('status', String(30), nullable=False, default='portfolio'),
    Column('entity_type', String(20)),
    Column('entity_id', Integer),
    Column('amount', Numeric(18, 2), nullable=False),
    Column('issue_date', String(30)),
    Column('due_date', String(30), nullable=False),
    Column('serial_no', String(100)),
    Column('bank_name', String(160)),
    Column('account_no', String(100)),
    Column('note', String(500)),
    Column('account_id', Integer),
    Column('financial_transaction_id', Integer),
    Column('created_at', DateTime(timezone=True), nullable=False),
)

ACCOUNT_TYPES = {'cash', 'bank', 'pos'}
METHOD_ACCOUNT_TYPES = {'cash': 'cash', 'card': 'pos', 'bank_transfer': 'bank'}


def validate_payment_account(db: Session, company_id: int, payment_method: str, account_id: int | None) -> None:
    """Validate an explicitly selected finance account against the payment method.

    The frontend also filters compatible accounts, but this backend check is the
    authoritative guard for API and integration clients.
    """
    if account_id is None:
        return
    required_type = METHOD_ACCOUNT_TYPES.get(payment_method)
    row = db.execute(select(finance_accounts.c.account_type).where(
        finance_accounts.c.id == account_id,
        finance_accounts.c.company_id == company_id,
        finance_accounts.c.is_active == True,
    )).first()
    if required_type is None or not row or row.account_type != required_type:
        raise HTTPException(400, "Seçilen finans hesabı ödeme yöntemiyle uyumlu değil.")


def utcnow():
    return datetime.now(timezone.utc)


def _add_column(conn, table: str, column: str, ddl: str):
    cols = {c['name'] for c in inspect(conn).get_columns(table)}
    if column not in cols:
        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))


def initialize_finance(engine: Engine) -> None:
    metadata.create_all(engine)
    with engine.begin() as conn:
        insp = inspect(conn)
        if insp.has_table('payments'):
            _add_column(conn, 'payments', 'account_id', 'INTEGER')
            _add_column(conn, 'payments', 'financial_transaction_id', 'INTEGER')
        if not insp.has_table('companies'):
            return
        company_ids = [int(r[0]) for r in conn.execute(text('SELECT id FROM companies')).all()]
        defaults = [('Merkez Kasa', 'cash'), ('Ana Banka', 'bank'), ('Ana POS', 'pos')]
        for cid in company_ids:
            for name, account_type in defaults:
                exists = conn.execute(text('SELECT id FROM finance_accounts WHERE company_id=:cid AND account_type=:t LIMIT 1'), {'cid': cid, 't': account_type}).first()
                if not exists:
                    conn.execute(insert(finance_accounts).values(company_id=cid, name=name, account_type=account_type, currency='TRY', opening_balance=0, is_active=True, created_at=utcnow()))


def default_account(db: Session, company_id: int, payment_method: str, requested_account_id: int | None = None) -> int | None:
    required_type = METHOD_ACCOUNT_TYPES.get(payment_method)
    if not required_type:
        return None
    if requested_account_id:
        row = db.execute(select(finance_accounts.c.id, finance_accounts.c.account_type).where(
            finance_accounts.c.id == requested_account_id,
            finance_accounts.c.company_id == company_id,
            finance_accounts.c.is_active == True,
        )).first()
        if not row:
            raise ValueError('Finans hesabı bulunamadı')
        if row.account_type != required_type:
            raise ValueError('Ödeme yöntemi ile finans hesabı türü uyumsuz')
        return int(row.id)
    row = db.execute(select(finance_accounts.c.id).where(
        finance_accounts.c.company_id == company_id,
        finance_accounts.c.account_type == required_type,
        finance_accounts.c.is_active == True,
    ).order_by(finance_accounts.c.id)).first()
    return int(row[0]) if row else None


def remove_payment_finance(db: Session, company_id: int, payment_id: int) -> None:
    suffix = ' FOR UPDATE' if db.get_bind().dialect.name == 'postgresql' else ''
    row = db.execute(
        text(
            'SELECT financial_transaction_id FROM payments '
            'WHERE id=:id AND company_id=:cid' + suffix
        ),
        {'id': payment_id, 'cid': company_id},
    ).first()
    if row and row[0]:
        db.execute(
            text(
                'SELECT id FROM finance_transactions '
                'WHERE id=:id AND company_id=:cid' + suffix
            ),
            {'id': int(row[0]), 'cid': company_id},
        ).first()
        db.execute(text('DELETE FROM finance_transactions WHERE id=:id AND company_id=:cid'), {'id': int(row[0]), 'cid': company_id})
    db.execute(text('UPDATE payments SET financial_transaction_id=NULL WHERE id=:id AND company_id=:cid'), {'id': payment_id, 'cid': company_id})


def sync_payment_finance(db: Session, company_id: int, payment_id: int, entity_type: str, amount: Decimal, payment_date: str, payment_method: str, note: str | None, account_id: int | None = None) -> int | None:
    remove_payment_finance(db, company_id, payment_id)
    account_id = default_account(db, company_id, payment_method, account_id)
    if account_id is None:
        db.execute(text('UPDATE payments SET account_id=NULL,financial_transaction_id=NULL WHERE id=:id AND company_id=:cid'), {'id': payment_id, 'cid': company_id})
        return None
    direction = 'in' if entity_type == 'customer' else 'out'
    result = db.execute(insert(finance_transactions).values(
        company_id=company_id, account_id=account_id, txn_date=payment_date, direction=direction,
        amount=money(amount), category='collection' if direction == 'in' else 'payment',
        payment_method=payment_method, description=note or ('Müşteri tahsilatı' if direction == 'in' else 'Tedarikçi ödemesi'),
        reference_type='payment', reference_id=payment_id, transfer_group=None, created_at=utcnow(),
    ))
    txid = int(result.inserted_primary_key[0])
    db.execute(text('UPDATE payments SET account_id=:aid,financial_transaction_id=:txid WHERE id=:id AND company_id=:cid'), {'aid': account_id, 'txid': txid, 'id': payment_id, 'cid': company_id})
    return txid

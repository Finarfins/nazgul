from __future__ import annotations

from datetime import datetime
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

CRM_COLUMNS = {
    'customers': {
        'risk_limit': 'NUMERIC(18,2) DEFAULT 0',
        'payment_term_days': 'INTEGER DEFAULT 0',
        'notes': 'TEXT',
        'is_active': 'INTEGER DEFAULT 1',
    },
    'suppliers': {
        'risk_limit': 'NUMERIC(18,2) DEFAULT 0',
        'payment_term_days': 'INTEGER DEFAULT 0',
        'notes': 'TEXT',
        'is_active': 'INTEGER DEFAULT 1',
    },
}


def initialize_crm(engine: Engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in CRM_COLUMNS.items():
            existing = {c['name'] for c in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}'))
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS entity_notes(
              id INTEGER PRIMARY KEY,
              company_id INTEGER NOT NULL,
              entity_type TEXT NOT NULL,
              entity_id INTEGER NOT NULL,
              note TEXT NOT NULL,
              created_at TEXT NOT NULL,
              created_by TEXT
            )
        '''))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_entity_notes_lookup ON entity_notes(company_id,entity_type,entity_id,created_at)'))
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS entity_contacts(
              id INTEGER PRIMARY KEY,
              company_id INTEGER NOT NULL,
              entity_type TEXT NOT NULL,
              entity_id INTEGER NOT NULL,
              name TEXT NOT NULL,
              role TEXT,
              phone TEXT,
              email TEXT,
              is_primary BOOLEAN NOT NULL DEFAULT FALSE,
              notes TEXT,
              created_at TEXT NOT NULL
            )
        '''))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_entity_contacts_lookup ON entity_contacts(company_id,entity_type,entity_id,is_primary)'))
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS entity_tasks(
              id INTEGER PRIMARY KEY,
              company_id INTEGER NOT NULL,
              entity_type TEXT NOT NULL,
              entity_id INTEGER NOT NULL,
              title TEXT NOT NULL,
              due_date TEXT,
              priority TEXT NOT NULL DEFAULT 'normal',
              status TEXT NOT NULL DEFAULT 'open',
              assigned_to TEXT,
              notes TEXT,
              created_at TEXT NOT NULL,
              completed_at TEXT
            )
        '''))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_entity_tasks_lookup ON entity_tasks(company_id,entity_type,entity_id,status,due_date)'))


def list_notes(db: Session, company_id: int, entity_type: str, entity_id: int) -> list[dict]:
    rows = db.execute(text('''SELECT id,note,created_at,created_by FROM entity_notes
      WHERE company_id=:cid AND entity_type=:etype AND entity_id=:eid
      ORDER BY created_at DESC,id DESC'''), {'cid': company_id, 'etype': entity_type, 'eid': entity_id}).mappings().all()
    return [dict(x) for x in rows]


def add_note(db: Session, company_id: int, entity_type: str, entity_id: int, note: str, username: str | None) -> dict:
    clean = ' '.join((note or '').split())
    if not clean:
        raise ValueError('Not boş bırakılamaz')
    created_at = datetime.utcnow().isoformat(timespec='seconds')
    result = db.execute(text('''INSERT INTO entity_notes(company_id,entity_type,entity_id,note,created_at,created_by)
      VALUES(:cid,:etype,:eid,:note,:created_at,:created_by) RETURNING id'''), {
        'cid': company_id, 'etype': entity_type, 'eid': entity_id, 'note': clean,
        'created_at': created_at, 'created_by': username,
    })
    return {'id': int(result.scalar_one()), 'note': clean, 'created_at': created_at, 'created_by': username}


def delete_note(db: Session, company_id: int, entity_type: str, entity_id: int, note_id: int) -> bool:
    result = db.execute(text('''DELETE FROM entity_notes WHERE id=:id AND company_id=:cid
      AND entity_type=:etype AND entity_id=:eid'''), {
        'id': note_id, 'cid': company_id, 'etype': entity_type, 'eid': entity_id,
    })
    return bool(result.rowcount)


def list_contacts(db: Session, company_id: int, entity_type: str, entity_id: int) -> list[dict]:
    rows = db.execute(text("""SELECT id,name,role,phone,email,is_primary,notes,created_at
      FROM entity_contacts WHERE company_id=:cid AND entity_type=:etype AND entity_id=:eid
      ORDER BY is_primary DESC,LOWER(name),id"""), {'cid': company_id, 'etype': entity_type, 'eid': entity_id}).mappings().all()
    return [dict(x) for x in rows]


def add_contact(db: Session, company_id: int, entity_type: str, entity_id: int, payload: dict) -> dict:
    name = ' '.join(str(payload.get('name') or '').split())
    if not name:
        raise ValueError('Yetkili kişi adı zorunludur')
    created_at = datetime.utcnow().isoformat(timespec='seconds')
    if payload.get('is_primary'):
        db.execute(text("UPDATE entity_contacts SET is_primary=FALSE WHERE company_id=:cid AND entity_type=:etype AND entity_id=:eid"), {'cid': company_id, 'etype': entity_type, 'eid': entity_id})
    values = {
        'cid': company_id, 'etype': entity_type, 'eid': entity_id, 'name': name,
        'role': payload.get('role'), 'phone': payload.get('phone'), 'email': payload.get('email'),
        'is_primary': bool(payload.get('is_primary')), 'notes': payload.get('notes'), 'created_at': created_at,
    }
    result = db.execute(text("""INSERT INTO entity_contacts(company_id,entity_type,entity_id,name,role,phone,email,is_primary,notes,created_at)
      VALUES(:cid,:etype,:eid,:name,:role,:phone,:email,:is_primary,:notes,:created_at) RETURNING id"""), values)
    return {'id': int(result.scalar_one()), **{k: values[k] for k in ('name','role','phone','email','notes')}, 'is_primary': bool(values['is_primary']), 'created_at': created_at}


def delete_contact(db: Session, company_id: int, entity_type: str, entity_id: int, contact_id: int) -> bool:
    result = db.execute(text("DELETE FROM entity_contacts WHERE id=:id AND company_id=:cid AND entity_type=:etype AND entity_id=:eid"), {'id': contact_id, 'cid': company_id, 'etype': entity_type, 'eid': entity_id})
    return bool(result.rowcount)


def list_tasks(db: Session, company_id: int, entity_type: str, entity_id: int) -> list[dict]:
    rows = db.execute(text("""SELECT id,title,due_date,priority,status,assigned_to,notes,created_at,completed_at
      FROM entity_tasks WHERE company_id=:cid AND entity_type=:etype AND entity_id=:eid
      ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,COALESCE(due_date,'9999-12-31'),id DESC"""), {'cid': company_id, 'etype': entity_type, 'eid': entity_id}).mappings().all()
    return [dict(x) for x in rows]


def add_task(db: Session, company_id: int, entity_type: str, entity_id: int, payload: dict) -> dict:
    title = ' '.join(str(payload.get('title') or '').split())
    if not title:
        raise ValueError('Görev başlığı zorunludur')
    priority = payload.get('priority') or 'normal'
    if priority not in {'low','normal','high'}:
        raise ValueError('Geçersiz görev önceliği')
    created_at = datetime.utcnow().isoformat(timespec='seconds')
    values = {'cid': company_id, 'etype': entity_type, 'eid': entity_id, 'title': title, 'due_date': payload.get('due_date'), 'priority': priority, 'assigned_to': payload.get('assigned_to'), 'notes': payload.get('notes'), 'created_at': created_at}
    result = db.execute(text("""INSERT INTO entity_tasks(company_id,entity_type,entity_id,title,due_date,priority,status,assigned_to,notes,created_at)
      VALUES(:cid,:etype,:eid,:title,:due_date,:priority,'open',:assigned_to,:notes,:created_at) RETURNING id"""), values)
    return {'id': int(result.scalar_one()), 'title': title, 'due_date': values['due_date'], 'priority': priority, 'status': 'open', 'assigned_to': values['assigned_to'], 'notes': values['notes'], 'created_at': created_at, 'completed_at': None}


def set_task_status(db: Session, company_id: int, entity_type: str, entity_id: int, task_id: int, status: str) -> bool:
    if status not in {'open','completed'}:
        raise ValueError('Geçersiz görev durumu')
    completed_at = datetime.utcnow().isoformat(timespec='seconds') if status == 'completed' else None
    result = db.execute(text("""UPDATE entity_tasks SET status=:status,completed_at=:completed_at
      WHERE id=:id AND company_id=:cid AND entity_type=:etype AND entity_id=:eid"""), {'status': status, 'completed_at': completed_at, 'id': task_id, 'cid': company_id, 'etype': entity_type, 'eid': entity_id})
    return bool(result.rowcount)


def delete_task(db: Session, company_id: int, entity_type: str, entity_id: int, task_id: int) -> bool:
    result = db.execute(text("DELETE FROM entity_tasks WHERE id=:id AND company_id=:cid AND entity_type=:etype AND entity_id=:eid"), {'id': task_id, 'cid': company_id, 'etype': entity_type, 'eid': entity_id})
    return bool(result.rowcount)

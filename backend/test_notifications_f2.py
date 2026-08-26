from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.notifications.render import build_content

BACKEND = Path(__file__).resolve().parent


@pytest.mark.parametrize(
    ("channel", "recipient"),
    [
        pytest.param("SMS", "905321112233", id="sms"),
        pytest.param("EMAIL", "musteri@example.com", id="email"),
        pytest.param("WHATSAPP", "905321112233", id="whatsapp"),
    ],
)
def test_harvest_reminder_renders_localized_due_date_for_every_channel(
    channel: str,
    recipient: str,
) -> None:
    content = build_content(
        message_class="HARVEST_TRANSACTIONAL",
        channel=channel,
        recipient=recipient,
        template_id=1,
        template_version=1,
        body_template=(
            "Sayın {musteri_adi}, {belge_no} numaralı belgenizin "
            "{vade_tarihi} tarihli {tutar} tutarındaki ödemesi yaklaşmaktadır. "
            "{firma_adi}"
        ),
        variables={
            "musteri_adi": "Ayşe",
            "belge_no": "S-42",
            "vade_tarihi": "2026-12-31",
            "tutar": "1250.00",
            "firma_adi": "Sungur",
        },
    )

    assert "31.12.2026" in content["body"]
    assert "2026-12-31" not in content["body"]


def test_f2_rule_engine_creates_only_approval_waiting_rows(tmp_path: Path) -> None:
    database = tmp_path / "f2.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{database.as_posix()}", "PYTHONPATH": str(BACKEND)}
    script = r'''
from datetime import datetime, timezone
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.db import SessionLocal
from app.notifications.rules import run_enabled_rules

cfg=Config("alembic.ini")
command.upgrade(cfg,"head")
with SessionLocal() as db:
    db.execute(text("PRAGMA foreign_keys=OFF"))
    db.execute(text("INSERT INTO companies(id,name,is_active,created_at) VALUES (1,'Sungur',1,CURRENT_TIMESTAMP)"))
    db.execute(text("""INSERT INTO customers(id,company_id,name,phone)
                      VALUES (1,1,'Ayşe','05321112233')"""))
    db.execute(text("""INSERT INTO machines(id,company_id,customer_id,brand,model,created_at,updated_at)
                      VALUES (1,1,1,'John Deere','5075E',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
    db.execute(text("""INSERT INTO work_orders
      (id,company_id,machine_id,customer_id,technician_id,work_order_no,opened_at,scheduled_date,status,priority,complaint,created_by,created_at,updated_at)
      VALUES (1,1,1,1,1,'WO-1','2026-07-27','2026-07-29 09:00:00','SCHEDULED','NORMAL','Bakım',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
    db.execute(text("""INSERT INTO notification_templates
      (id,company_id,code,channel,name,body,message_class,is_active,version,created_at,updated_at)
      VALUES (1,1,'service.reminder','SMS','Servis','Sayın {musteri_adi}, {makine_adi} makinenizin servis randevusu {randevu_tarihi} tarihindedir. {firma_adi}',
      'SERVICE_TRANSACTIONAL',1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
    db.execute(text("""INSERT INTO notification_consents
      (company_id,party_type,party_id,channel,status,source,recipient_snapshot,version,created_at,updated_at)
      VALUES (1,'CUSTOMER',1,'SMS','GRANTED','MANUAL','905321112233',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
    db.execute(text("""INSERT INTO notification_rules
      (id,company_id,code,trigger_type,channel,template_id,offset_days,is_enabled,
       send_window_start,send_window_end,max_per_run,scope_filter,created_at,updated_at)
      VALUES (1,1,'service-1','SERVICE_DUE','SMS',1,-1,1,'09:00','18:00',50,
       '{"customer_ids":[1]}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
    db.commit()
    result=run_enabled_rules(db,now=datetime(2026,7,28,8,tzinfo=timezone.utc))
    assert result=={1:1}
    repeated=run_enabled_rules(db,now=datetime(2026,7,28,8,tzinfo=timezone.utc))
    assert repeated=={1:0}
    row=db.execute(text("SELECT status,dispatch_armed,approved_at,rule_id FROM notifications")).mappings().one()
    assert row["status"]=="AWAITING_APPROVAL"
    assert not row["dispatch_armed"]
    assert row["approved_at"] is None
    assert row["rule_id"]==1
    db.commit()
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_f2_migration_normalizes_only_valid_iso_dates(tmp_path: Path) -> None:
    database = tmp_path / "dates.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{database.as_posix()}", "PYTHONPATH": str(BACKEND)}
    script = r'''
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.db import engine
cfg=Config("alembic.ini")
command.upgrade(cfg,"20260728_0034")
with engine.begin() as c:
    c.execute(text("INSERT INTO companies(id,name,is_active,created_at) VALUES (1,'X',1,CURRENT_TIMESTAMP)"))
    c.execute(text("INSERT INTO customers(id,company_id,name) VALUES (1,1,'X')"))
    for i,d in enumerate((None,'','bozuk','2026-07-27','2026-07-28','2026-07-29'),1):
        c.execute(text("""INSERT INTO orders(id,customer_id,order_date,subtotal,vat_total,grand_total,discount_percent,discount_amount,final_total,due_date,company_id,status,payment_term)
                         VALUES (:id,1,'2026-07-01',1,0,1,0,0,1,:due,1,'approved','PESIN')"""),{"id":i,"due":d})
command.upgrade(cfg,"head")
with engine.connect() as c:
    rows=c.execute(text("SELECT due_date_normalized FROM orders ORDER BY id")).scalars().all()
    assert rows[:3]==[None,None,None]
    assert [str(x) for x in rows[3:]]==['2026-07-27','2026-07-28','2026-07-29']
'''
    completed = subprocess.run([sys.executable, "-c", script], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_f2_migration_upgrade_downgrade_upgrade_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "round-trip.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{database.as_posix()}", "PYTHONPATH": str(BACKEND)}
    script = r'''
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from app.db import engine

cfg=Config("alembic.ini")
command.upgrade(cfg,"head")
command.downgrade(cfg,"20260728_0034")
command.upgrade(cfg,"head")
inspector=inspect(engine)
assert inspector.has_table("notification_rules")
assert "due_date_normalized" in {column["name"] for column in inspector.get_columns("orders")}
assert "ix_orders_company_term_due_normalized" in {index["name"] for index in inspector.get_indexes("orders")}
'''
    completed = subprocess.run([sys.executable, "-c", script], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_f2_migration_can_replay_over_existing_schema(tmp_path: Path) -> None:
    database = tmp_path / "replay.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{database.as_posix()}", "PYTHONPATH": str(BACKEND)}
    script = r'''
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from app.db import engine

cfg=Config("alembic.ini")
command.upgrade(cfg,"head")
with engine.begin() as connection:
    connection.execute(text("UPDATE alembic_version SET version_num='20260728_0034'"))
command.upgrade(cfg,"20260728_0035")
inspector=inspect(engine)
assert inspector.has_table("notification_rules")
assert "ix_notification_rules_company_enabled" in {index["name"] for index in inspector.get_indexes("notification_rules")}
assert "ix_orders_company_term_due_normalized" in {index["name"] for index in inspector.get_indexes("orders")}
'''
    completed = subprocess.run([sys.executable, "-c", script], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr

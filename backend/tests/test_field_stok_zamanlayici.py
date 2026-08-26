from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


def _run_isolated(
    code: str, database: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    env["ENVIRONMENT"] = "test"
    env["FIELD_STOCK_OUTBOX_INTERVAL_SECONDS"] = "1"
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=120,
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_names(path: Path) -> set[str]:
    tree = _tree(path)
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _imports_and_calls(path: Path, module: str, names: set[str]) -> bool:
    tree = _tree(path)
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in {module, f"app.{module}"}
        for alias in node.names
        if alias.name in names
    }
    return bool(imported.intersection(_called_names(path)))


def test_every_consumer_module_has_a_production_caller() -> None:
    """Fail when a consumer module exists but no production module calls it."""
    production_files = tuple(APP.rglob("*.py"))
    consumer_modules = tuple(APP.rglob("*_tuketici.py"))
    assert consumer_modules, "At least one production consumer module must be discovered"

    missing: list[str] = []
    for consumer_path in consumer_modules:
        tree = ast.parse(
            consumer_path.read_text(encoding="utf-8"), filename=str(consumer_path)
        )
        public_functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        }
        required_functions = (
            {"tum_firmalari_isle"}
            if consumer_path.name == "field_stok_tuketici.py"
            else public_functions
        )
        has_caller = any(
            _imports_and_calls(path, consumer_path.stem, required_functions)
            for path in production_files
            if path != consumer_path
        )
        if not has_caller:
            missing.append(consumer_path.relative_to(BACKEND).as_posix())

    assert not missing, f"Production caller missing for consumer modules: {missing}"


def test_application_startup_reaches_field_stock_scheduler() -> None:
    """An orphan scheduler is not a production caller: startup must invoke it."""
    main_calls = _called_names(APP / "main.py")
    assert "baslat_field_stok_zamanlayici" in main_calls


def test_interval_has_working_default_and_environment_override(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.delenv("FIELD_STOCK_OUTBOX_INTERVAL_SECONDS", raising=False)
    assert Settings().field_stock_outbox_interval_seconds == 30
    monkeypatch.setenv("FIELD_STOCK_OUTBOX_INTERVAL_SECONDS", "45")
    assert Settings().field_stock_outbox_interval_seconds == 45


def test_cycle_logs_every_outcome_bucket(monkeypatch, caplog) -> None:
    from app import field_stok_zamanlayici as scheduler

    result = {
        "girdi": 4,
        "SENT": 2,
        "SKIPPED_SOURCE_NOT_VISIBLE": 1,
        "SKIPPED_NO_PRODUCT": 1,
        "DEAD": 0,
        "FAILED_TENANT": 0,
        "CLAIM_LOST": 0,
    }
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: nullcontext(SimpleNamespace()))
    monkeypatch.setattr(scheduler, "tum_firmalari_isle", lambda _db: result)

    with caplog.at_level(logging.INFO, logger=scheduler.LOGGER_NAME):
        assert scheduler.bir_dongu_calistir() == result

    message = caplog.records[-1].getMessage()
    for key, value in result.items():
        assert f"{key}={value}" in message


def test_empty_cycle_is_logged_explicitly(monkeypatch, caplog) -> None:
    from app import field_stok_zamanlayici as scheduler

    result = {
        "girdi": 0,
        "SENT": 0,
        "SKIPPED_SOURCE_NOT_VISIBLE": 0,
        "SKIPPED_NO_PRODUCT": 0,
        "DEAD": 0,
        "FAILED_TENANT": 0,
        "CLAIM_LOST": 0,
    }
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: nullcontext(SimpleNamespace()))
    monkeypatch.setattr(scheduler, "tum_firmalari_isle", lambda _db: result)

    with caplog.at_level(logging.INFO, logger=scheduler.LOGGER_NAME):
        scheduler.bir_dongu_calistir()

    assert "olay bulunmadi" in caplog.records[-1].getMessage()


def test_claim_lost_survives_all_company_conservation_and_cycle_log(
    tmp_path: Path,
) -> None:
    """POZİTİF bir CLAIM_LOST hem `tum_firmalari_isle`den hem DÖNGÜDEN geçmeli.

    Doğrudan tüketici düzeyinde bu zaten kanıtlı. Kaçış TÜM FİRMALAR
    denkleminde: orada `CLAIM_LOST` sayılmazsa assert patlar, zamanlayıcının
    genel `except Exception` kolluna kaçar ve `CLAIM_LOST=1` taşıyan NORMAL
    bir döngü satırı HİÇ yazılamaz. Ölçülen şey tam olarak budur.
    """
    result = _run_isolated(
        r'''
import logging

from sqlalchemy import text

import app.main  # göç zincirini koşturur
from app import field_stok_tuketici as consumer
from app import field_stok_zamanlayici as scheduler
from app.db import SessionLocal

with SessionLocal.begin() as db:
    company_id = db.execute(text(
        "SELECT id FROM companies ORDER BY id LIMIT 1"
    )).scalar_one()
    db.execute(text("""
        INSERT INTO field_integration_events
            (company_id, source_type, source_id, target, idempotency_key,
             status, attempts, created_at, updated_at)
        VALUES
            (:company_id, 'field_activity', 91951, 'stock',
             'claim-lost-cycle:91951:stock', 'PENDING', 0,
             '2026-08-24T00:00:00', '2026-08-24T00:00:00')
    """), {"company_id": company_id})

# Zaten PostgreSQL'de kanıtlanmış ATOMİK TALEP yarışının KAYBEDEN tarafını
# belirlenimci olarak yeniden kurar. Gerçek tüketici PENDING satırı yine
# seçer ve CLAIM_LOST yazar; denetlenen tek şey koşullu UPDATE'in sonucudur.
consumer._talep_et = lambda _db, _firma, _olay_id: False
records = []

class Capture(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())

logging.getLogger(scheduler.LOGGER_NAME).addHandler(Capture())

BEKLENEN = {
    'girdi': 1,
    'CLAIM_LOST': 1,
    'RETRY_SCHEDULED': 0,
    'RECOVERY_FAILED': 0,
    'RECOVERY_ESCALATED': 0,
    'SENT': 0,
    'SKIPPED_SOURCE_NOT_VISIBLE': 0,
    'SKIPPED_NO_PRODUCT': 0,
    'DEAD': 0,
    # OLAY kovasi degil FIRMA sayaci; korunum denkleminin DISINDA durur ama
    # kova satirinda GORUNUR, yani ac kalan bir dongu cikarilmaz, SAYILIR.
    'COMPANY_FAILED': 0,
}

# 1) TÜM FİRMALAR denklemi doğrudan: pozitif CLAIM_LOST assert PATLATMAMALI.
with SessionLocal() as db:
    try:
        toplam = consumer.tum_firmalari_isle(db)
    except AssertionError as exc:
        raise AssertionError(
            "ALL-COMPANY CONSERVATION OMITTED CLAIM_LOST: pozitif bir "
            "CLAIM_LOST `tum_firmalari_isle` korunum denklemini patlatti"
        ) from exc
assert toplam == BEKLENEN, toplam

# Talep KAYBEDİLDİĞİ için hicbir sey yazilmadi; olay hâlâ PENDING.
with SessionLocal() as db:
    durum = db.execute(text(
        "SELECT status FROM field_integration_events "
        "WHERE idempotency_key = 'claim-lost-cycle:91951:stock'"
    )).scalar_one()
assert durum == 'PENDING', durum

# 2) ZAMANLAYICI DÖNGÜSÜ: ayni girdi normal tamamlanmali ve kova satirini
#    yazmali — genel except koluna kacmamali.
try:
    cycle = scheduler.bir_dongu_calistir()
except AssertionError as exc:
    raise AssertionError(
        "ALL-COMPANY CONSERVATION OMITTED CLAIM_LOST: positive CLAIM_LOST "
        "must complete the scheduler cycle and reach its normal bucket log"
    ) from exc

assert cycle == BEKLENEN, cycle
normal_lines = [
    line for line in records
    if "Field stok outbox dongusu tamamlandi" in line
]
assert len(normal_lines) == 1, records
line = normal_lines[0]
for expected in (
    'girdi=1', 'CLAIM_LOST=1', 'RETRY_SCHEDULED=0', 'RECOVERY_FAILED=0',
    'RECOVERY_ESCALATED=0', 'SENT=0',
    'SKIPPED_SOURCE_NOT_VISIBLE=0', 'SKIPPED_NO_PRODUCT=0', 'DEAD=0',
    'COMPANY_FAILED=0',
):
    assert expected in line, line
assert not [r for r in records if "basarisiz" in r], records
print(line)
print("CLAIM_LOST_ALL_COMPANY_CYCLE_OK")
''',
        tmp_path / "claim-lost-cycle.db",
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "CLAIM_LOST_ALL_COMPANY_CYCLE_OK" in result.stdout
    print(result.stdout.strip())


def test_start_failure_is_not_swallowed(monkeypatch) -> None:
    from app import field_stok_zamanlayici as scheduler

    class BrokenThread:
        def start(self) -> None:
            raise RuntimeError("thread-start-failed")

    monkeypatch.setattr(scheduler, "_yeni_thread", lambda **_kwargs: BrokenThread())
    with pytest.raises(RuntimeError, match="thread-start-failed"):
        scheduler.baslat_field_stok_zamanlayici(30)


def test_first_cycle_failure_does_not_kill_application(tmp_path: Path) -> None:
    result = _run_isolated(
        r'''
import logging
import threading

from fastapi.testclient import TestClient
from app.main import app
from app import field_stok_zamanlayici as scheduler

failed = threading.Event()
retried = threading.Event()
records = []

class Capture(logging.Handler):
    def emit(self, record):
        records.append(record)
        if "dongusu basarisiz" in record.getMessage():
            failed.set()
        if "olay bulunmadi" in record.getMessage():
            retried.set()

def first_cycle_fails():
    scheduler.bir_dongu_calistir = original
    raise RuntimeError("transient-first-cycle")

original = scheduler.bir_dongu_calistir
scheduler.bir_dongu_calistir = first_cycle_fails
handler = Capture()
logging.getLogger(scheduler.LOGGER_NAME).addHandler(handler)

with TestClient(app) as client:
    assert failed.wait(5), "first cycle failure was not logged"
    response = client.get("/api/live")
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "alive"}
    assert retried.wait(5), "scheduler did not retry after the transient failure"

rendered = "\n".join(record.getMessage() for record in records)
assert "Field stok outbox dongusu basarisiz" in rendered
assert any(
    record.exc_info and "transient-first-cycle" in str(record.exc_info[1])
    for record in records
)
print("APP_STARTED status=200 body={'status': 'alive'}")
print("CYCLE_FAILURE_LOGGED transient-first-cycle")
print("NEXT_CYCLE_RETRIED empty-cycle-logged")
''',
        tmp_path / "cycle-failure.db",
        # Zamanlayici MEKANIZMASI olculuyor; varsayilan artik KAPALI oldugu
        # icin anahtar burada ACIKCA acilir.
        {"FIELD_STOCK_OUTBOX_ENABLED": "true"},
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "APP_STARTED status=200" in result.stdout
    assert "CYCLE_FAILURE_LOGGED transient-first-cycle" in result.stdout
    assert "NEXT_CYCLE_RETRIED empty-cycle-logged" in result.stdout
    print(result.stdout.strip())


def test_real_application_scheduler_moves_pending_event_to_terminal(
    tmp_path: Path,
) -> None:
    result = _run_isolated(
        r'''
import logging
import re
import time
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app
from app import field_stok_zamanlayici as scheduler

records = []

class Capture(logging.Handler):
    def emit(self, record):
        if "Field stok outbox dongusu tamamlandi" in record.getMessage():
            records.append(record.getMessage())

logging.getLogger(scheduler.LOGGER_NAME).addHandler(Capture())

with SessionLocal.begin() as db:
    company_id = db.execute(text("SELECT id FROM companies ORDER BY id LIMIT 1")).scalar_one()
    warehouse_id = db.execute(text(
        "SELECT id FROM warehouses WHERE company_id=:company_id AND is_active=1 "
        "ORDER BY is_default DESC, id LIMIT 1"
    ), {"company_id": company_id}).scalar_one()
    db.execute(text("""
        INSERT INTO products
            (id, name, purchase_price, sale_price, vat_rate, stock, unit,
             price_per, active, critical_stock, minimum_stock, company_id)
        VALUES
            (99191, 'Effect Gate Tohum', 0, 0, 0, '200.0000', 'kg',
             'unit', 1, 0, 0, :company_id)
    """), {"company_id": company_id})
    db.execute(text("""
        INSERT INTO warehouse_stocks
            (company_id, warehouse_id, product_id, quantity, critical_stock,
             reserved_quantity)
        VALUES (:company_id, :warehouse_id, 99191, '200.0000', 0, 0)
    """), {"company_id": company_id, "warehouse_id": warehouse_id})
    db.execute(text("""
        INSERT INTO farms
            (id, company_id, code, name, status, created_at, updated_at)
        VALUES
            (99191, :company_id, 'effect-gate-farm', 'Effect Gate Farm',
             'ACTIVE', '2026-08-24T00:00:00', '2026-08-24T00:00:00')
    """), {"company_id": company_id})
    db.execute(text("""
        INSERT INTO farm_parcels
            (id, company_id, farm_id, code, name, area_decare, status,
             created_at, updated_at)
        VALUES
            (99191, :company_id, 99191, 'effect-gate-parcel',
             'Effect Gate Parcel', '10.0000', 'ACTIVE',
             '2026-08-24T00:00:00', '2026-08-24T00:00:00')
    """), {"company_id": company_id})
    db.execute(text("""
        INSERT INTO crop_seasons
            (id, company_id, parcel_id, season_year, crop, status,
             created_at, updated_at)
        VALUES
            (99191, :company_id, 99191, 2026, 'Bugday', 'ACTIVE',
             '2026-08-24T00:00:00', '2026-08-24T00:00:00')
    """), {"company_id": company_id})
    db.execute(text("""
        INSERT INTO field_activities
            (id, company_id, season_id, activity_type, performed_at, status,
             created_at, updated_at)
        VALUES
            (99191, :company_id, 99191, 'SOWING', '2026-08-24T00:00:00',
             'RECORDED', '2026-08-24T00:00:00', '2026-08-24T00:00:00')
    """), {"company_id": company_id})
    db.execute(text("""
        INSERT INTO field_activity_inputs
            (id, company_id, activity_id, product_id, input_name, quantity,
             unit, created_at, updated_at)
        VALUES
            (99191, :company_id, 99191, 99191, 'Effect Gate Tohum',
             '50.0000', 'kg', '2026-08-24T00:00:00',
             '2026-08-24T00:00:00')
    """), {"company_id": company_id})
    db.execute(text("""
        INSERT INTO field_integration_events
            (company_id, source_type, source_id, target, idempotency_key,
             status, attempts, created_at, updated_at)
        VALUES
            (:company_id, 'field_activity', 99191, 'stock',
             'field_activity:99191:stock', 'PENDING', 0,
             '2026-08-24T00:00:00', '2026-08-24T00:00:00')
    """), {"company_id": company_id})

client = TestClient(app)
client.__enter__()
teardown_error = None
try:
    deadline = time.monotonic() + 8
    status = "PENDING"
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            status = db.execute(text(
                "SELECT status FROM field_integration_events "
                "WHERE idempotency_key='field_activity:99191:stock'"
            )).scalar_one()
        if status == "SENT" and records:
            break
        time.sleep(0.05)
    response = client.get("/api/live")
finally:
    try:
        client.__exit__(None, None, None)
    except BaseException as exc:
        # Result assertions below are authoritative. A teardown defect must not
        # hide whether the scheduler processed the event and moved stock.
        teardown_error = exc

assert response.status_code == 200, response.text
with SessionLocal() as db:
    movements = db.execute(text("""
        SELECT company_id, product_id, warehouse_id, quantity
        FROM stock_movements
        WHERE reference_type='field_integration_event'
          AND product_id=99191
    """)).all()
    stock = db.execute(text(
        "SELECT company_id, stock FROM products WHERE id=99191"
    )).one()
assert status == 'SENT', (
    "SCHEDULER EFFECT GATE: event is still %s after the wait; scheduler never "
    "processed it; stock did not move (movement_count=%d, stock=%s)"
    % (status, len(movements), stock.stock)
)
assert len(movements) == 1, movements
movement = movements[0]
assert movement.company_id == company_id, movement
assert movement.product_id == 99191, movement
assert movement.warehouse_id == warehouse_id, movement
assert Decimal(str(movement.quantity)) == Decimal('-50.0000'), movement
assert stock.company_id == company_id, stock
assert Decimal(str(stock.stock)) == Decimal('150.0000'), stock
assert len(records) == 1, records
cycle = records[0]
counts = {key: int(value) for key, value in re.findall(r'(\w+)=(\d+)', cycle)}
assert counts == {
    'girdi': 1,
    'CLAIM_LOST': 0,
    'RETRY_SCHEDULED': 0,
    'RECOVERY_FAILED': 0,
    'RECOVERY_ESCALATED': 0,
    'SENT': 1,
    'SKIPPED_SOURCE_NOT_VISIBLE': 0,
    'SKIPPED_NO_PRODUCT': 0,
    'DEAD': 0,
    'COMPANY_FAILED': 0,
}, counts
# `COMPANY_FAILED` bir OLAY kovasi degil FIRMA sayacidir ve uretimdeki iki
# korunum denkleminin de DISINDADIR; buradaki yerel denklem de onu disarida
# birakir. (Bu kosumda degeri zaten 0; disarida birakmak esitligi TESADUFEN
# degil YAPISAL olarak dogru kilar.)
count_out = sum(
    value for key, value in counts.items()
    if key not in ('girdi', 'COMPANY_FAILED')
)
assert count_out == counts['girdi'], (count_out, counts)
if teardown_error is not None:
    raise teardown_error
print(cycle)
print(
    f"EFFECT_GATE terminal={status} movement=-50.0000 stock=150.0000 "
    f"company={company_id} live={response.status_code}"
)
''',
        tmp_path / "effect-gate.db",
        # Zamanlayici MEKANIZMASI olculuyor; varsayilan artik KAPALI oldugu
        # icin anahtar burada ACIKCA acilir.
        {"FIELD_STOCK_OUTBOX_ENABLED": "true"},
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "EFFECT_GATE terminal=SENT movement=-50.0000 stock=150.0000" in result.stdout
    print(result.stdout.strip())


def test_shutdown_keeps_reference_to_a_stuck_thread(monkeypatch, caplog) -> None:
    from app import field_stok_zamanlayici as scheduler

    class StuckThread:
        def join(self, timeout: int) -> None:
            assert timeout == 5

        def is_alive(self) -> bool:
            return True

    stuck = StuckThread()
    bayrak = threading.Event()
    monkeypatch.setattr(scheduler, "_thread", stuck)
    monkeypatch.setattr(scheduler, "_dur", bayrak)

    with caplog.at_level(logging.ERROR, logger=scheduler.LOGGER_NAME):
        scheduler.durdur_field_stok_zamanlayici()

    assert scheduler._thread is stuck
    assert "durmadi" in caplog.records[-1].getMessage().lower()
    # DURDURAMAYAN DURDURMA BUNU SÖYLER *ve* bayrağı SET bırakır: takılı
    # thread kendi döngüsünde sonlanır, ama `_thread`/`_dur` birlikte durduğu
    # için sonraki `baslat` durumu ANLAYIP taze bir bayrakla yeni thread açar.
    assert bayrak.is_set(), "takılı thread'in bayrağı SET bırakılmalı"
    assert scheduler._dur is bayrak


def test_nested_lifespan_starts_one_thread_and_application_still_boots(
    tmp_path: Path,
) -> None:
    """İÇ İÇE `TestClient` uygulamayı ARTIK DÜŞÜRMEMELİ — ve İKİNCİ thread AÇMAMALI.

    Ölçüldü (gerçek PostgreSQL 16.4, dört dosya): dış bir `TestClient` içinde
    açılan ikinci `TestClient` lifespan'i ikinci kez koşturuyor,
    `baslat_field_stok_zamanlayici` `RuntimeError` atıyor ve `main.py` bunu
    BİLEREK yakalamadığı için uygulama BAŞLAMIYORDU. Kurulum hatasının ölümcül
    kalması gerekir; İÇ İÇE AÇILIŞ bir kurulum hatası DEĞİLDİR.
    """
    result = _run_isolated(
        r'''
import threading

from fastapi.testclient import TestClient

from app.main import app
from app import field_stok_zamanlayici as scheduler


def zamanlayici_threadleri():
    return [t for t in threading.enumerate()
            if t.name == "field-stock-outbox-scheduler" and t.is_alive()]


with TestClient(app) as dis:
    assert dis.get("/api/live").status_code == 200
    dis_thread = list(zamanlayici_threadleri())
    assert len(dis_thread) == 1, dis_thread

    # İÇ İÇE: eskiden burası RuntimeError ile patlıyordu.
    with TestClient(app) as ic:
        assert ic.get("/api/live").status_code == 200
        ic_thread = zamanlayici_threadleri()
        assert len(ic_thread) == 1, ic_thread
        assert ic_thread[0] is dis_thread[0], (ic_thread, dis_thread)

    # İç kapanış DIŞTAKİNİ durdurmamalı.
    hala = zamanlayici_threadleri()
    assert len(hala) == 1, hala
    assert hala[0] is dis_thread[0]
    assert dis.get("/api/live").status_code == 200

# En dıştaki kapanış gerçekten durdurur; thread SIZMAZ.
assert zamanlayici_threadleri() == [], threading.enumerate()
assert scheduler._thread is None, scheduler._thread
print("NESTED_LIFESPAN_OK single-thread=1 leaked=0")
''',
        tmp_path / "nested-lifespan.db",
        # Zamanlayici MEKANIZMASI olculuyor; varsayilan artik KAPALI oldugu
        # icin anahtar burada ACIKCA acilir.
        {"FIELD_STOCK_OUTBOX_ENABLED": "true"},
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "NESTED_LIFESPAN_OK single-thread=1 leaked=0" in result.stdout
    print(result.stdout.strip())


def test_concurrent_nested_clients_are_safe(tmp_path: Path) -> None:
    """Dört PostgreSQL dosyasının GERÇEK şekli: iç istemciler EŞ ZAMANLI açılır."""
    result = _run_isolated(
        r'''
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app
from app import field_stok_zamanlayici as scheduler

bariyer = threading.Barrier(4)


def ic_istemci(_n):
    with TestClient(app) as concurrent:
        bariyer.wait()
        return concurrent.get("/api/live").status_code


with TestClient(app) as dis:
    with ThreadPoolExecutor(max_workers=4) as havuz:
        kodlar = list(havuz.map(ic_istemci, range(4)))
    assert kodlar == [200, 200, 200, 200], kodlar
    canli = [t for t in threading.enumerate()
             if t.name == "field-stock-outbox-scheduler" and t.is_alive()]
    assert len(canli) == 1, canli

assert scheduler._thread is None, scheduler._thread
assert scheduler._derinlik == 0, scheduler._derinlik
print("CONCURRENT_NESTED_OK codes=[200, 200, 200, 200] threads=1 leaked=0")
''',
        tmp_path / "nested-concurrent.db",
        # Zamanlayici MEKANIZMASI olculuyor; varsayilan artik KAPALI oldugu
        # icin anahtar burada ACIKCA acilir.
        {"FIELD_STOCK_OUTBOX_ENABLED": "true"},
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "CONCURRENT_NESTED_OK" in result.stdout
    print(result.stdout.strip())


_TOHUM = r'''
with SessionLocal.begin() as db:
    company_id = db.execute(text("SELECT id FROM companies ORDER BY id LIMIT 1")).scalar_one()
    warehouse_id = db.execute(text(
        "SELECT id FROM warehouses WHERE company_id=:c AND is_active=1 "
        "ORDER BY is_default DESC, id LIMIT 1"
    ), {"c": company_id}).scalar_one()
    db.execute(text("""
        INSERT INTO products
            (id, name, purchase_price, sale_price, vat_rate, stock, unit,
             price_per, active, critical_stock, minimum_stock, company_id)
        VALUES (99192,'Anahtar Tohum',0,0,0,'200.0000','kg','unit',1,0,0,:c)
    """), {"c": company_id})
    db.execute(text("""
        INSERT INTO warehouse_stocks
            (company_id, warehouse_id, product_id, quantity, critical_stock,
             reserved_quantity)
        VALUES (:c,:w,99192,'200.0000',0,0)
    """), {"c": company_id, "w": warehouse_id})
    db.execute(text("""
        INSERT INTO farms (id,company_id,code,name,status,created_at,updated_at)
        VALUES (99192,:c,'anahtar-farm','Anahtar Farm','ACTIVE',
                '2026-08-24T00:00:00','2026-08-24T00:00:00')
    """), {"c": company_id})
    db.execute(text("""
        INSERT INTO farm_parcels (id,company_id,farm_id,code,name,area_decare,
                                  status,created_at,updated_at)
        VALUES (99192,:c,99192,'anahtar-parcel','Anahtar Parcel','10.0000',
                'ACTIVE','2026-08-24T00:00:00','2026-08-24T00:00:00')
    """), {"c": company_id})
    db.execute(text("""
        INSERT INTO crop_seasons (id,company_id,parcel_id,season_year,crop,
                                  status,created_at,updated_at)
        VALUES (99192,:c,99192,2026,'Bugday','ACTIVE',
                '2026-08-24T00:00:00','2026-08-24T00:00:00')
    """), {"c": company_id})
    db.execute(text("""
        INSERT INTO field_activities (id,company_id,season_id,activity_type,
                                      performed_at,status,created_at,updated_at)
        VALUES (99192,:c,99192,'SOWING','2026-08-24T00:00:00','RECORDED',
                '2026-08-24T00:00:00','2026-08-24T00:00:00')
    """), {"c": company_id})
    db.execute(text("""
        INSERT INTO field_activity_inputs (id,company_id,activity_id,product_id,
                                           input_name,quantity,unit,created_at,
                                           updated_at)
        VALUES (99192,:c,99192,99192,'Anahtar Tohum','50.0000','kg',
                '2026-08-24T00:00:00','2026-08-24T00:00:00')
    """), {"c": company_id})
    db.execute(text("""
        INSERT INTO field_integration_events
            (company_id,source_type,source_id,target,idempotency_key,status,
             attempts,created_at,updated_at)
        VALUES (:c,'field_activity',99192,'stock','anahtar:99192:stock',
                'PENDING',0,'2026-08-24T00:00:00','2026-08-24T00:00:00')
    """), {"c": company_id})
'''


def test_switch_off_starts_no_thread_and_moves_no_stock(tmp_path: Path) -> None:
    """KAPALI yön: thread HİÇ açılmaz ve envanter KIMILDAMAZ.

    Bugüne kadar tek ayar aralıktı ve doğrulayıcı [1,3600] dışını reddettiği
    için `0` tüketiciyi kapatmıyor, UYGULAMAYI AÇTIRMIYORDU.
    """
    result = _run_isolated(
        r'''
import threading
import time
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.main import app

assert settings.field_stock_outbox_enabled is False, settings.field_stock_outbox_enabled
''' + _TOHUM + r'''
with TestClient(app) as client:
    assert client.get("/api/live").status_code == 200
    # Aralık 1 saniye; kapalı olmasaydı bu pencerede KESİNLİKLE işlerdi.
    time.sleep(3)
    canli = [t for t in threading.enumerate()
             if t.name == "field-stock-outbox-scheduler" and t.is_alive()]
    assert canli == [], canli

with SessionLocal() as db:
    durum = db.execute(text(
        "SELECT status, attempts FROM field_integration_events "
        "WHERE idempotency_key='anahtar:99192:stock'"
    )).one()
    hareketler = db.execute(text(
        "SELECT id FROM stock_movements WHERE product_id=99192"
    )).all()
    stok = db.execute(text("SELECT stock FROM products WHERE id=99192")).scalar_one()

assert durum.status == 'PENDING', durum
assert durum.attempts == 0, durum
assert hareketler == [], hareketler
assert Decimal(str(stok)) == Decimal('200.0000'), stok
print("SWITCH_OFF thread=0 status=PENDING movements=0 stock=200.0000")
''',
        tmp_path / "switch-off.db",
        {"FIELD_STOCK_OUTBOX_ENABLED": "false"},
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "SWITCH_OFF thread=0 status=PENDING movements=0 stock=200.0000" in result.stdout
    print(result.stdout.strip())


def test_switch_off_is_the_default_and_moves_no_stock(tmp_path: Path) -> None:
    """VARSAYILAN yön: ayar YAPILMAZSA tüketici KAPALIDIR ve envanter KIMILDAMAZ.

    (Onceki adi `test_switch_on_is_the_default_and_still_moves_stock` idi ve
    ESKI varsayilani — ACIK — ayni siklikta pinliyordu. Varsayilan BILEREK
    cevrildi: bugun hasattan urune yol yok, her hasat olayi terminal ve
    gorunmez `SKIPPED_NO_PRODUCT` kovasina duser ve geri alinamaz; acik
    varsayilan her hasadi sessizce cope atmakti. Bu test YENI varsayilani
    ayni siklikta pinler: env YOKKEN thread acilmaz, olay PENDING kalir,
    attempts artmaz, hicbir hareket yazilmaz ve stok yerinden oynamaz.)
    """
    result = _run_isolated(
        r'''
import threading
import time
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.main import app

# Hicbir sey ayarlamayan icin varsayilan KAPALI.
assert settings.field_stock_outbox_enabled is False, settings.field_stock_outbox_enabled
''' + _TOHUM + r'''
with TestClient(app) as client:
    assert client.get("/api/live").status_code == 200
    # Aralik 1 saniye; varsayilan acik olsaydi bu pencerede KESINLIKLE islerdi.
    time.sleep(3)
    canli = [t for t in threading.enumerate()
             if t.name == "field-stock-outbox-scheduler" and t.is_alive()]
    assert canli == [], canli

with SessionLocal() as db:
    durum = db.execute(text(
        "SELECT status, attempts FROM field_integration_events "
        "WHERE idempotency_key='anahtar:99192:stock'"
    )).one()
    hareketler = db.execute(text(
        "SELECT id FROM stock_movements WHERE product_id=99192"
    )).all()
    stok = db.execute(text("SELECT stock FROM products WHERE id=99192")).scalar_one()

assert durum.status == 'PENDING', durum
assert durum.attempts == 0, durum
assert hareketler == [], hareketler
assert Decimal(str(stok)) == Decimal('200.0000'), stok
print("SWITCH_DEFAULT_OFF thread=0 status=PENDING movements=0 stock=200.0000")
''',
        tmp_path / "switch-default-off.db",
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "SWITCH_DEFAULT_OFF thread=0 status=PENDING movements=0 stock=200.0000" in result.stdout
    print(result.stdout.strip())


def test_switch_on_EXPLICIT_true_still_moves_stock(tmp_path: Path) -> None:
    """AÇIK yön artık AÇIKÇA istenmelidir: env=true stok HAREKET ETTİRİR.

    Varsayilan cevrilmeden once bu ucin kapsamini varsayilan-acik testi
    tasiyordu; anahtar acildiginda tuketicinin GERCEKTEN calistigini pinleyen
    tek uctan uca olcum artik budur.
    """
    result = _run_isolated(
        r'''
import time
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.main import app

assert settings.field_stock_outbox_enabled is True, settings.field_stock_outbox_enabled
''' + _TOHUM + r'''
with TestClient(app) as client:
    son = time.monotonic() + 8
    durum = 'PENDING'
    while time.monotonic() < son:
        with SessionLocal() as db:
            durum = db.execute(text(
                "SELECT status FROM field_integration_events "
                "WHERE idempotency_key='anahtar:99192:stock'"
            )).scalar_one()
        if durum == 'SENT':
            break
        time.sleep(0.05)
    assert client.get("/api/live").status_code == 200

with SessionLocal() as db:
    miktar = db.execute(text(
        "SELECT quantity FROM stock_movements WHERE product_id=99192"
    )).scalars().all()
    stok = db.execute(text("SELECT stock FROM products WHERE id=99192")).scalar_one()

assert durum == 'SENT', durum
assert [Decimal(str(m)) for m in miktar] == [Decimal('-50.0000')], miktar
assert Decimal(str(stok)) == Decimal('150.0000'), stok
print("SWITCH_ON_EXPLICIT status=SENT movement=-50.0000 stock=150.0000")
''',
        tmp_path / "switch-on.db",
        {"FIELD_STOCK_OUTBOX_ENABLED": "true"},
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "SWITCH_ON_EXPLICIT status=SENT movement=-50.0000 stock=150.0000" in result.stdout
    print(result.stdout.strip())


def test_switch_default_is_todays_behaviour(monkeypatch) -> None:
    """Bugünün davranışı: varsayılan KAPALI; iki yön de env ile açılıp kapanır.

    (Bu test onceden ESKI varsayilani — ACIK — pinliyordu. Varsayilan bilerek
    cevrildi; pin ayni siklikta, YENI degere: env yokken False, "true" acar,
    "false" kapali birakir. Iki deger de tolere EDILMEZ.)
    """
    from app.config import Settings

    monkeypatch.delenv("FIELD_STOCK_OUTBOX_ENABLED", raising=False)
    assert Settings().field_stock_outbox_enabled is False
    monkeypatch.setenv("FIELD_STOCK_OUTBOX_ENABLED", "true")
    assert Settings().field_stock_outbox_enabled is True
    monkeypatch.setenv("FIELD_STOCK_OUTBOX_ENABLED", "false")
    assert Settings().field_stock_outbox_enabled is False


def test_durduramayan_durdurmadan_SONRA_baslat_GERCEKTEN_tuketici_kosturur(
    tmp_path: Path,
) -> None:
    """Takılı bir thread'den sonra yeniden başlatma KOŞAN bir tüketici bırakmalı.

    ÖLÇÜLEN KUSUR: durdurma bayrağı modül düzeyinde PAYLAŞILAN tek bir
    Event'ti. `join(timeout=5)` zaman aşımına uğradığında (`wait()` yalnızca
    döngüler ARASINDA uyanır; gerçek bir birikim 5 saniyeyi aşar) thread SET
    bayrakla hayatta kalıyordu. Sonraki `baslat` onu "yaşıyor" diye
    SAHİPLENİYOR, derinliği 1 yapıyor ve bayrağı TEMİZLEMİYORDU; eski thread
    ise bir sonraki kontrolde sonlanıyordu. Sonuç: derinlik 1, elde ÖLÜ bir
    thread nesnesi ve koşan HİÇBİR tüketici — sessizce.

    ÖLÇÜLEN ŞEY İÇ ALAN DEĞİL, ETKİ: yeniden başlatmadan sonra GERÇEKTEN kaç
    döngü koştu. `_thread`/`_derinlik` okumak kusuru göremezdi; kusurlu kodda
    da `_thread` doluydu ve `_derinlik` 1'di. Ayrı süreç şart: modül düzeyi
    thread durumu süreç boyu yaşar.

    Döngü SÜRESİ enjekte edilir (gerçek bir birikimin 5 saniyeyi aşmasının
    modeli); threading, sayaç ve bayrak mantığı TAMAMEN gerçektir.
    """
    result = _run_isolated(
        r'''
import time, threading, logging
from contextlib import nullcontext
from types import SimpleNamespace

from app import field_stok_zamanlayici as sch

SAYAC = {"n": 0}
OLCUM = {}

# ON KOSULU YAKALA: `join(timeout=5)` GERCEKTEN sure doldurdu mu? Bunu
# olcmeden `YENI_DONGU > 0` sinamasi bos kalir; bkz. testin govdesi.
KAYITLAR = []

class Yakala(logging.Handler):
    def emit(self, record):
        KAYITLAR.append(record.getMessage())

_gunlukcu = logging.getLogger(sch.LOGGER_NAME)
_gunlukcu.addHandler(Yakala())
_gunlukcu.setLevel(logging.INFO)

def _sahte(_db):
    SAYAC["n"] += 1
    n = SAYAC["n"]
    if n == 1:
        OLCUM["c1_basla"] = time.monotonic()
    # ILK dongu join(timeout=5)'i ASAR; sonrakiler hizlidir.
    time.sleep(8 if n == 1 else 0.05)
    if n == 1:
        OLCUM["c1_bit"] = time.monotonic()
    return {"girdi": 0}

sch.SessionLocal = lambda: nullcontext(SimpleNamespace())
sch.tum_firmalari_isle = _sahte

sch.baslat_field_stok_zamanlayici(1)
time.sleep(1.0)                      # thread uzun dongunun ICINDE
_t0 = time.monotonic()
sch.durdur_field_stok_zamanlayici()  # join ZAMAN ASIMINA ugrar
_durdurma = time.monotonic() - _t0

# ON KOSUL: durduramayan durdurma BUNU SOYLEDI MI?
print("DURMADI %d" % len([k for k in KAYITLAR if "DURMADI" in k]))
print("DURDURMA_SURESI %.2f" % _durdurma)

onceki = SAYAC["n"]
sch.baslat_field_stok_zamanlayici(1) # SONRAKI BASLAT
time.sleep(3.0)
print("YENI_DONGU %d" % (SAYAC["n"] - onceki))

# MARJ: yetim thread `durdur` dondukten SONRA daha ne kadar yasadi? Pozitif
# olmasi, join'in gercekten SURE DOLDURDUGUNU olcer.
_bit = OLCUM.get("c1_bit")
if _bit is not None:
    print("MARJ_JOIN %.2f" % (_bit - (_t0 + _durdurma)))

sch.durdur_field_stok_zamanlayici()
''',
        tmp_path / "adopted-thread.db",
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    # --- ÖN KOŞUL PİNLENİR -------------------------------------------------
    #
    # `kosan > 0` tek başına BOŞ bir sınamadır: enjekte edilen `sleep(8)` bir
    # gün `join(timeout=5)`i AŞMAZ olursa (daha hızlı ya da daha yavaş bir
    # makine, değişen bir timeout) `durdur` thread'i TEMİZ toplar,
    # `_thread=None` olur, sonraki `baslat` zaten TAZE bir thread açar ve
    # `kosan > 0` yine tutar — SAHİPLENME yoluna HİÇ girilmeden. O hâlde test
    # yeşil kalır ama ölçtüğünü sandığı şeyi ölçmez. Bu yüzden önce ÖN KOŞUL
    # ölçülür: durduramayan durdurma GERÇEKTEN oldu mu?
    assert "DURMADI 1" in result.stdout, (
        "ÖN KOŞUL SAĞLANMADI: `join(timeout=5)` süre DOLDURMADI, yani takılı "
        "bir thread hiç oluşmadı ve SAHİPLENME yoluna hiç girilmedi. Bu "
        "koşumda `YENI_DONGU > 0` kusuru göremezdi; ölçüm boştur ve yeşili "
        f"anlamsızdır. çıktı={result.stdout!r}"
    )

    satir = [s for s in result.stdout.splitlines() if s.startswith("YENI_DONGU")]
    assert satir, f"prob YENI_DONGU yazmadı: {result.stdout!r}"
    kosan = int(satir[0].split()[1])
    assert kosan > 0, (
        "SESSİZ TÜKETİCİSİZ SÜREÇ: durduramayan bir durdurmadan sonraki "
        "`baslat` ÖLMEKTE OLAN thread'i sahiplendi; üç saniyede HİÇ döngü "
        "koşmadı. Süreç tüketicisi olduğunu sanıyor, olaylar PENDING "
        f"birikiyor ve bunu söyleyen tek bir satır yok. YENI_DONGU={kosan}"
    )
    print(result.stdout.strip())

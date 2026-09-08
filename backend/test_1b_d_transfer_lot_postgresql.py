"""Faz 1B-D lot-aware warehouse transfers on the production dialect."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parent
YARIS_TURU = 20


def _acilisa_cek() -> None:
    """Admin sifresini ACILIS DURUMUNA (`admin123` + `must_change_password`) yaz.

    1B-B ikizinden DEVRALINDI ve gerekcesi BU DOSYADA YENIDEN OLCULDU:
    PostgreSQL ikizleri CI'da AYNI veritabanini paylasiyor ve her biri
    girisden sonra admin sifresini KENDI sabitine cekiyor. Bu dosya cekilmis
    sifreyi geri birakmiyordu; ayni veritabanina IKINCI kosu

        AssertionError: (401, 'Kullanici adi veya sifre hatali')

    ile dustu -- iddia degil, olculdu. Tek yonlu bir care (yalniz teardown)
    dosyayi iyi bir komsu yapar ama KENDISINI korumaz, cunku sifreyi bozan
    ONCEKI dosya olabilir. Bu yuzden IKI UCTAN cagriliyor.
    """
    from app.auth import hash_password
    from app.db import SessionLocal, engine
    from sqlalchemy import text as _text

    # DİYALEKT KAPISI ve ATLANAMAZ: `to_regclass` PostgreSQL'e ÖZGÜDÜR ve
    # SQLite'ta `OperationalError` atar. Bu dosya PG-ikizidir ama modülü
    # kanonik koşucu SQLite altında da TOPLAR; kapı olmasaydı ATLAMA
    # (`skip`) yerine KURULUM HATASI verirdi -- ÖLÇÜLDÜ.
    if engine.dialect.name != "postgresql":
        return

    with SessionLocal() as db:
        if db.execute(_text("SELECT to_regclass('public.app_users')")).scalar() is None:
            return
        db.execute(
            _text(
                "UPDATE app_users SET password_hash=:h, "
                "must_change_password=true WHERE username='admin'"
            ),
            {"h": hash_password("admin123")},
        )
        db.commit()


@pytest.fixture()
def acilis():
    """Acilis sifresi, IKI UCTAN. Bkz. `_acilisa_cek`.

    ATLAMA ÖNCE gelir: `_postgres_url` yapılandırma yoksa testi ATLAR ve
    fixture hiçbir şeye dokunmaz.
    """
    _postgres_url()
    from tests.pg_ikiz_yardimci import parti_temizle

    parti_temizle(lot_code_prefixes=["LOT-", "PG-AKTAR-"])
    _acilisa_cek()
    try:
        yield
    finally:
        parti_temizle(lot_code_prefixes=["LOT-", "PG-AKTAR-"])
        _acilisa_cek()


def _postgres_url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is not configured")
    assert url.startswith("postgresql"), "1B-D PostgreSQL twin must use PostgreSQL"
    return url


def _sqlite_twin_source() -> str:
    path = BACKEND / "tests" / "test_1b_d_transfer_lot.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_BEHAVIOUR" for target in item.targets)
    )
    return ast.literal_eval(node.value)


@pytest.mark.postgresql
def test_TRANSFER_PARTI_DAVRANISI_ve_ESZAMANLILIK_postgresql(acilis) -> None:
    env = os.environ.copy()
    url = _postgres_url()
    env["DATABASE_URL"] = url
    env["APP_TEST_DATABASE_URL"] = url
    env["REQUIRE_PG"] = "1"
    env["PYTHONPATH"] = str(BACKEND)
    script = _sqlite_twin_source() + _RACE.replace("__YARIS_TURU__", str(YARIS_TURU))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "1B-D POSTGRESQL RACE OK" in completed.stdout


_RACE = r'''
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


YARIS_TURU = __YARIS_TURU__

with TestClient(app) as setup_client:
    race_login = ok(setup_client.post(
        '/api/auth/login', json={'username':'admin','password':'TransferLot!123'}))
    race_headers = {'Authorization':'Bearer '+race_login['access_token'],
                    'X-Company-ID':str(cid)}

    for turn in range(YARIS_TURU):
        pid = ok(setup_client.post('/api/products', headers=race_headers, json={
            'name':f'1B-D PG Yaris {turn}','purchase_price':10,'sale_price':20,
            'vat_rate':20,'stock':0,'unit':'Adet'}))['id']
        seeded = setup_client.post(f'/api/products/{pid}/stock', headers=race_headers, json={
            'mode':'add','warehouse_id':source,'quantity':6,
            'movement_date':'2026-09-12','lot_code':f'PG-AKTAR-{turn}',
            'expiry_date':'2098-12-31'})
        assert seeded.status_code == 200, seeded.text
        extra = setup_client.post(f'/api/products/{pid}/stock', headers=race_headers, json={
            'mode':'add','warehouse_id':source,'quantity':6,
            'movement_date':'2026-09-12'})
        assert extra.status_code == 200, extra.text
        barrier = Barrier(2)

        def one_transfer():
            with TestClient(app) as peer:
                barrier.wait(timeout=30)
                response = peer.post('/api/warehouses/transfers', headers=race_headers, json={
                    'source_warehouse_id':source,'target_warehouse_id':target,
                    'transfer_date':'2026-09-12','items':[
                        {'product_id':pid,'quantity':4,'lot_code':f'PG-AKTAR-{turn}'}
                    ]})
                code = response.json().get('detail', {}).get('code') if response.status_code == 409 else None
                return response.status_code, code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                (future.result(timeout=90) for future in
                 (pool.submit(one_transfer), pool.submit(one_transfer))),
                key=lambda item: item[0],
            )
        assert [item[0] for item in results] == [201, 409], (turn, results)
        assert results[1][1] == 'LOT_MIKTARI_EKSIYE_DUSER', (turn, results)

        with SessionLocal() as db:
            lot_rows = db.execute(text(
                'SELECT warehouse_id,quantity,expiry_date FROM product_lots '
                'WHERE company_id=:cid AND product_id=:pid AND lot_code=:code '
                'ORDER BY warehouse_id'
            ), {'cid':cid,'pid':pid,'code':f'PG-AKTAR-{turn}'}).mappings().all()
            stocks = {int(row.warehouse_id): q(row.quantity) for row in db.execute(text(
                'SELECT warehouse_id,quantity FROM warehouse_stocks '
                'WHERE company_id=:cid AND product_id=:pid'
            ), {'cid':cid,'pid':pid}).mappings()}
            transfer_items = db.execute(text(
                'SELECT count(*) FROM stock_transfer_items WHERE company_id=:cid AND product_id=:pid'
            ), {'cid':cid,'pid':pid}).scalar_one()
            transfer_headers = db.execute(text(
                'SELECT count(DISTINCT t.id) FROM stock_transfers t '
                'JOIN stock_transfer_items i ON i.company_id=t.company_id AND i.transfer_id=t.id '
                'WHERE t.company_id=:cid AND i.product_id=:pid'
            ), {'cid':cid,'pid':pid}).scalar_one()
            movements = db.execute(text(
                "SELECT h.warehouse_id,l.warehouse_id lot_warehouse_id FROM stock_movements h "
                "JOIN product_lots l ON l.company_id=h.company_id AND l.id=h.lot_id "
                "WHERE h.company_id=:cid AND h.product_id=:pid AND h.reference_type='transfer' "
                "ORDER BY h.warehouse_id"
            ), {'cid':cid,'pid':pid}).mappings().all()
        by_warehouse = {int(row.warehouse_id): q(row.quantity) for row in lot_rows}
        assert by_warehouse == {source:Decimal('2'), target:Decimal('4')}, (turn, lot_rows)
        assert all(row.expiry_date.isoformat() == '2098-12-31' for row in lot_rows)
        assert stocks[source] == 8 and stocks[target] == 4, (turn, stocks)
        assert int(transfer_items) == 1 and int(transfer_headers) == 1, (
            turn, transfer_items, transfer_headers)
        assert [(int(row.warehouse_id), int(row.lot_warehouse_id)) for row in movements] == [
            (source, source), (target, target)
        ], (turn, movements)

print('1B-D POSTGRESQL RACE OK')
'''

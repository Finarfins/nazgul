"""Depo transferi adli partiyi ayni SKT ile tasir (Faz 1B-D)."""
from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from app.schemas import StockTransferItem


BACKEND = Path(__file__).resolve().parents[1]
WAREHOUSES = BACKEND / "app" / "routers" / "warehouses.py"
DEFTER = BACKEND / "app" / "parti_defteri.py"

# Uygulama geldikten sonra normalize AST yeniden olculup sabitlenecek.
WAREHOUSES_AST_SHA256 = "9796b386ccdff064168e6cf952d077c0fb192f88e23f5e753cccf49ecba6fd04"


def _create_transfer_tree() -> ast.FunctionDef:
    tree = ast.parse(WAREHOUSES.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_transfer"
    )


def _calls(name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(_create_transfer_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


def _keyword(call: ast.Call, name: str) -> str:
    value = next(item.value for item in call.keywords if item.arg == name)
    return ast.unparse(value)


def test_transfer_kalemi_opsiyonel_lot_code_tasir() -> None:
    assert StockTransferItem(product_id=1, quantity="1").lot_code is None
    assert StockTransferItem(product_id=1, quantity="1", lot_code="  L-1  ").lot_code == "L-1"
    assert StockTransferItem(product_id=1, quantity="1", lot_code="   ").lot_code is None


def test_hedef_parti_SKT_KOPYASI_olmadan_ACILAMAZ() -> None:
    calls = _calls("_parti_ac")
    assert len(calls) == 1, "transfer tam bir hedef parti upsert'i yapmali"
    assert _keyword(calls[0], "expiry_date") == "kaynak_parti.expiry_date"


def test_kaynak_parti_DUSULMEDEN_transfer_yazilamaz() -> None:
    calls = _calls("_parti_dus")
    assert len(calls) == 1, "kaynak parti yarista da yalniz bir kez dusulmeli"
    assert _keyword(calls[0], "lot_id") == "kaynak_parti.id"
    assert _keyword(calls[0], "miktar") == "item.quantity"


def test_parti_cagrilari_DOGRU_DEPOLARI_kullanir() -> None:
    bul, ac = _calls("_parti_bul"), _calls("_parti_ac")
    assert len(bul) >= 2 and len(ac) == 1
    assert _keyword(bul[0], "warehouse_id") == "payload.source_warehouse_id"
    assert _keyword(ac[0], "warehouse_id") == "payload.target_warehouse_id"


def test_IKINCI_YAZICI_parti_defterini_ATLAYAMAZ() -> None:
    writers: list[str] = []
    for path in (BACKEND / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and (body := getattr(node, "body", []))
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and "product_lots" in node.value.lower()
                and any(verb in node.value.upper() for verb in ("INSERT", "UPDATE", "DELETE"))
            ):
                writers.append(path.relative_to(BACKEND).as_posix())
                break
    assert writers == ["app/parti_defteri.py"]


def test_warehouses_fingerprint_ve_parti_cagri_sayisi_OLCULDU() -> None:
    tree = ast.parse(WAREHOUSES.read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256(
        ast.dump(tree, include_attributes=False).encode("utf-8")
    ).hexdigest()
    assert fingerprint == WAREHOUSES_AST_SHA256
    dynamic_text = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == "text" or getattr(node.func, "attr", None) == "text")
        and node.args
        and not isinstance(node.args[0], ast.Constant)
    ]
    assert dynamic_text == [], "warehouses.py dinamik SQL yuzeyi olculen sifiri asmamali"
    assert len(_calls("_parti_bul")) == 2
    assert len(_calls("_parti_dus")) == 1
    assert len(_calls("_parti_ac")) == 1


def test_transfer_partiyi_tasir_FEFOya_verir_ve_hatalari_geri_alir(tmp_path: Path) -> None:
    database = tmp_path / "1b-d-transfer-lot.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _BEHAVIOUR],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "1B-D SQLITE OK" in completed.stdout


_BEHAVIOUR = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


def ok(response):
    assert response.status_code < 300, (response.status_code, response.text)
    return response.json() if response.content else None


def q(value):
    return Decimal(str(value))


PAROLA = 'TransferLot!123'

with TestClient(app) as client:
    # GIRIS IKI ADAYI DA DENER ve denemesi ZORUNLUDUR: PostgreSQL ikizleri
    # CI'da AYNI veritabanini paylasiyor, yani acilis parolasini BASKA bir
    # dosya (ya da bu dosyanin onceki kosusu) coktan degistirmis olabilir.
    # Tek adaya bagli bir giris, sirasi degisen ilk gunde 401 ile duserdi --
    # OLCULDU: ayni veritabanina ikinci kosu 401 verdi.
    for aday in ('admin123', PAROLA):
        giris = client.post(
            '/api/auth/login', json={'username':'admin','password':aday})
        if giris.status_code == 200:
            break
    login = ok(giris)
    headers = {'Authorization':'Bearer '+login['access_token'],
               'X-Company-ID':str(login['companies'][0]['id'])}
    if aday != PAROLA:
        changed = ok(client.post('/api/auth/change-password', headers=headers, json={
            'current_password':aday,'new_password':PAROLA}))
        headers['Authorization'] = 'Bearer ' + changed['access_token']
    cid = int(headers['X-Company-ID'])
    source = ok(client.get('/api/warehouses', headers=headers))[0]['id']
    target = ok(client.post('/api/warehouses', headers=headers,
                            json={'name':'1B-D Hedef','code':'1BDH'}))['id']
    customer = ok(client.post('/api/customers', headers=headers,
                              json={'name':'1B-D Musteri'}))['id']

    def product(name):
        return ok(client.post('/api/products', headers=headers, json={
            'name':name,'purchase_price':10,'sale_price':20,'vat_rate':20,
            'stock':0,'unit':'Adet'}))['id']

    def add(pid, warehouse, amount, lot_code=None, expiry=None):
        body = {'mode':'add','warehouse_id':warehouse,'quantity':amount,
                'movement_date':'2026-09-10'}
        if lot_code is not None:
            body['lot_code'] = lot_code
        if expiry is not None:
            body['expiry_date'] = expiry
        return ok(client.post(f'/api/products/{pid}/stock', headers=headers, json=body))

    def transfer(pid, amount, lot_code=None, *, using=headers):
        item = {'product_id':pid,'quantity':amount}
        if lot_code is not None:
            item['lot_code'] = lot_code
        return client.post('/api/warehouses/transfers', headers=using, json={
            'source_warehouse_id':source,'target_warehouse_id':target,
            'transfer_date':'2026-09-10','items':[item]})

    def lots(pid):
        with SessionLocal() as db:
            return {
                (row.lot_code, int(row.warehouse_id)):
                    (int(row.id), q(row.quantity),
                     row.expiry_date.isoformat() if hasattr(row.expiry_date, 'isoformat') else row.expiry_date)
                for row in db.execute(text(
                    'SELECT id,lot_code,warehouse_id,quantity,expiry_date FROM product_lots '
                    'WHERE company_id=:cid AND product_id=:pid'
                ), {'cid':cid,'pid':pid}).mappings()
            }

    def stock(pid):
        payload = ok(client.get(f'/api/products/{pid}/warehouse-stock', headers=headers))
        return {int(row['warehouse_id']): q(row['quantity']) for row in payload['warehouses']}

    moved = product('Partili Transfer')
    add(moved, source, 10, 'LOT-AKTAR', '2098-01-31')
    response = transfer(moved, 6, 'LOT-AKTAR')
    assert response.status_code == 201, response.text
    transfer_id = response.json()['id']
    rows = lots(moved)
    source_lot = rows[('LOT-AKTAR', source)]
    target_lot = rows[('LOT-AKTAR', target)]
    assert source_lot[1:] == (Decimal('4'), '2098-01-31'), rows
    assert target_lot[1:] == (Decimal('6'), '2098-01-31'), rows
    detail = ok(client.get(f'/api/warehouse-transfers/{transfer_id}', headers=headers))
    movements = {row['movement_type']: row for row in detail['movements']}
    assert movements['transfer_out']['lot_id'] == source_lot[0], movements
    assert movements['transfer_in']['lot_id'] == target_lot[0], movements

    # The moved lot is earlier than the locally opened lot and must feed FEFO.
    add(moved, target, 5, 'LOT-GEC', '2099-01-31')
    sale = client.post('/api/orders', headers=headers, json={
        'entity_id':customer,'transaction_date':'2026-09-11','due_date':'2026-09-30',
        'warehouse_id':target,
        'items':[{'product_id':moved,'quantity':7,'unit_price':20,'vat_rate':20}]})
    assert sale.status_code == 201, sale.text
    after_sale = lots(moved)
    assert after_sale[('LOT-AKTAR', target)][1] == 0, after_sale
    assert after_sale[('LOT-GEC', target)][1] == 4, after_sale

    # Named-lot shortage must roll back warehouse stock, target lot, document,
    # and movements even though aggregate source stock is sufficient.
    short = product('Parti Yetersiz Transfer')
    add(short, source, 2, 'LOT-KISA', '2098-06-30')
    add(short, source, 8)
    before_lots, before_stock = lots(short), stock(short)
    with SessionLocal() as db:
        before_docs = db.execute(text('SELECT count(*) FROM stock_transfers WHERE company_id=:cid'), {'cid':cid}).scalar_one()
        before_moves = db.execute(text("SELECT count(*) FROM stock_movements WHERE company_id=:cid AND reference_type='transfer'"), {'cid':cid}).scalar_one()
    rejected = transfer(short, 3, 'LOT-KISA')
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()['detail']['code'] == 'LOT_MIKTARI_EKSIYE_DUSER', rejected.text
    assert lots(short) == before_lots
    assert stock(short) == before_stock
    with SessionLocal() as db:
        assert db.execute(text('SELECT count(*) FROM stock_transfers WHERE company_id=:cid'), {'cid':cid}).scalar_one() == before_docs
        assert db.execute(text("SELECT count(*) FROM stock_movements WHERE company_id=:cid AND reference_type='transfer'"), {'cid':cid}).scalar_one() == before_moves

    # The coded lot error must also win when aggregate warehouse stock is short.
    both_short = product('Parti ve Depo Yetersiz Transfer')
    add(both_short, source, 2, 'LOT-IKISI-KISA', '2098-06-30')
    both_before = lots(both_short), stock(both_short)
    both_rejected = transfer(both_short, 3, 'LOT-IKISI-KISA')
    assert both_rejected.status_code == 409, both_rejected.text
    assert both_rejected.json()['detail']['code'] == 'LOT_MIKTARI_EKSIYE_DUSER'
    assert (lots(both_short), stock(both_short)) == both_before

    # 0073 permits per-warehouse rows, so a conflicting target SKT is
    # representable. The ledger must reject it and the outer transaction must
    # restore every source/target write already attempted.
    conflict = product('Hedef SKT Celiskili Transfer')
    add(conflict, source, 5, 'LOT-CELISKI', '2098-01-31')
    add(conflict, target, 1, 'LOT-CELISKI', '2099-01-31')
    conflict_before = lots(conflict), stock(conflict)
    with SessionLocal() as db:
        counts_before = (
            db.execute(text('SELECT count(*) FROM stock_transfers WHERE company_id=:cid'), {'cid':cid}).scalar_one(),
            db.execute(text('SELECT count(*) FROM stock_transfer_items WHERE company_id=:cid'), {'cid':cid}).scalar_one(),
            db.execute(text("SELECT count(*) FROM stock_movements WHERE company_id=:cid AND reference_type='transfer'"), {'cid':cid}).scalar_one(),
        )
    conflict_response = transfer(conflict, 2, 'LOT-CELISKI')
    assert conflict_response.status_code == 422, conflict_response.text
    assert conflict_response.json()['detail']['code'] == 'LOT_SKT_CELISKI'
    assert (lots(conflict), stock(conflict)) == conflict_before
    with SessionLocal() as db:
        counts_after = (
            db.execute(text('SELECT count(*) FROM stock_transfers WHERE company_id=:cid'), {'cid':cid}).scalar_one(),
            db.execute(text('SELECT count(*) FROM stock_transfer_items WHERE company_id=:cid'), {'cid':cid}).scalar_one(),
            db.execute(text("SELECT count(*) FROM stock_movements WHERE company_id=:cid AND reference_type='transfer'"), {'cid':cid}).scalar_one(),
        )
    assert counts_after == counts_before, (counts_before, counts_after)

    # Omitting lot_code preserves the existing two-movement, nullable-lot path.
    plain = product('Partisiz Transfer')
    add(plain, source, 4)
    plain_response = transfer(plain, 3)
    assert plain_response.status_code == 201, plain_response.text
    plain_detail = ok(client.get(
        f"/api/warehouse-transfers/{plain_response.json()['id']}", headers=headers))
    assert len(plain_detail['movements']) == 2, plain_detail
    assert all(row['lot_id'] is None for row in plain_detail['movements']), plain_detail
    assert stock(plain)[source] == 1 and stock(plain)[target] == 3

    # A company cannot transfer through another tenant's warehouses.
    foreign_company = ok(client.post('/api/companies', headers=headers,
                                     json={'name':'1B-D Diger Firma'}))['id']
    foreign_headers = {**headers, 'X-Company-ID':str(foreign_company)}
    foreign = transfer(plain, 1, using=foreign_headers)
    assert 400 <= foreign.status_code < 500, foreign.text

print('1B-D SQLITE OK')
'''

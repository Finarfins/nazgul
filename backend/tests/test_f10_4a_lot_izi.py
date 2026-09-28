"""F10-4a — PARTİ GERİ ÇAĞIRMA ÖNİZLEMESİ + PARTİSİZ TRANSFER KAPISI. GÖÇ YOK.

Konu: `app/lot_izi.py` (üç hop, SQL), `app/lot_izi_ozet.py` (saf birleştirme),
`app/routers/lots.py` (`GET /api/lots/{lot_id}/recall-preview`),
`app/auth.py` (`/api/lots/` GET -> `sales`) ve `app/routers/warehouses.py`
(G4: partili ürünün partisiz transferi -> 409). PostgreSQL ikizi:
`test_f10_4a_lot_izi_postgresql.py` (`GROUP BY`, `IN` genişlemesi, `date`
tipli SKT gerçek diyalektte).

Keşif `docs/f10-4-lot-geri-cagirma-kesif-2026-09-27.md` §2 (sonda), §3 (G1–G5),
§5.1, K2/K3/K6/K8.

--- DAVRANIŞ SENARYOSU (sondanın aynısı + G2/G5) ---------------------------

  L-RECALL 100 alış (A) · Ali 30 satış (A) · Berk 20 irsaliye (A) · POS
  perakende 5 (A) · partili transfer A->B 10 · Cem 10 satış (B) · Ali'nin
  satışına bağlı iade 5 · tedarikçiye alış iadesi 3 · KAYNAKSIZ iade 2 (G2,
  partisiz) · tarla faaliyeti tüketimi 4 (G5, partisiz).

  Beklenen: Ali 25 (bildirilebilir), Berk 20 (telefon var rıza yok ->
  elle), Cem 10 (telefonsuz -> elle); perakende 5 / 1 belge; kardeşler A 37,
  B 0; `gaps` = {tarla_hareketi -4, kaynaksiz_iade +2}; denge farkı 0.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

  * `lot_izi`nin bir sorgusundan `company_id` yüklemini düşürmek
        -> `test_core_tenant_scoping_guard` KIRMIZI (tarayıcı) + burada
           `TENANT_YUKLEMI_ILK` KIRMIZI
  * `lot_izi_ozet`te perakende ayrımını kaldırmak (sistem carisini alıcı
    saymak) -> `PERAKENDE_ALICI_DEGIL` + davranış `pos_retail` KIRMIZI
  * `warehouses.py`de partisiz transfer kapısını kaldırmak
        -> davranış G4 bölümü KIRMIZI (201 döner) + `TRANSFER_KAPISI` KIRMIZI
  * Kardeş satır dalını kaldırmak (yalnız kök `id`) -> Cem kaybolur
  * İade düşmeyi kaldırmak -> Ali 30 okunur
  * Uçtan `response_model`i kaldırmak -> `OPENAPI_cevap_semasi` KIRMIZI
    (`$ref` yok); `ozetle`ye şemada olmayan bir anahtar eklemek -> davranış
    KIRMIZI (`extra="forbid"`, cevap doğrulaması 500)
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
IZ = APP / "lot_izi.py"
OZET = APP / "lot_izi_ozet.py"
DEPOLAR = APP / "routers" / "warehouses.py"

sys.path.insert(0, str(BACKEND))


# --------------------------------------------------------------- statik ---


def _where_ilk_yuklemleri(yol: Path) -> list[tuple[int, str]]:
    """Her `.where(...)` çağrısının İLK yüklemi (satır, kaynak)."""
    agac = ast.parse(yol.read_text(encoding="utf-8"))
    sonuc = []
    for dugum in ast.walk(agac):
        if (
            isinstance(dugum, ast.Call)
            and isinstance(dugum.func, ast.Attribute)
            and dugum.func.attr == "where"
        ):
            ilk = dugum.args[0] if dugum.args else None
            sonuc.append((dugum.lineno, ast.unparse(ilk) if ilk is not None else ""))
    return sonuc


def test_TENANT_YUKLEMI_ILK_her_sorguda() -> None:
    """`lot_izi`nin her `where`ü `<tablo>.c.company_id == cid` ile BAŞLAR."""
    yuklemler = _where_ilk_yuklemleri(IZ)
    # H116/H117: +4 sorgu (örnekler, defter_bosaldi, stok, parti toplamı).
    assert len(yuklemler) == 14, yuklemler
    for satir, ilk in yuklemler:
        assert ilk.endswith(".c.company_id == cid"), (satir, ilk)


def test_OZET_SQL_GORMEZ_ve_parti_tablosu_YEREL() -> None:
    """Saf modül sqlalchemy ithal etmez; parti tablosu `core_schema`da DEĞİL."""
    agac = ast.parse(OZET.read_text(encoding="utf-8"))
    ithaller = set()
    for d in ast.walk(agac):
        if isinstance(d, ast.ImportFrom):
            ithaller.add(d.module or "")
        elif isinstance(d, ast.Import):
            ithaller.update(a.name for a in d.names)
    assert not any("sqlalchemy" in i for i in ithaller), ithaller

    from app import core_schema, lot_izi

    assert "product_lots" not in core_schema.metadata.tables
    assert lot_izi.product_lots.metadata is not core_schema.metadata


def test_IZIN_sales_ve_read_geri_dususunun_USTUNDE() -> None:
    from app.auth import required_permission

    assert required_permission("GET", "/api/lots/1/recall-preview") == "sales"
    assert required_permission("HEAD", "/api/lots/1/recall-preview") == "sales"
    # Komşu GET'ler `read`te KALIR — kural önek `/api/lots/`e bağlı.
    assert required_permission("GET", "/api/products/1/lots") == "read"


def test_TRANSFER_KAPISI_partisiz_dalda_iki_depo_icin() -> None:
    """`create_transfer` partisiz dalda `_lotsuz_yazmayi_reddet` çağırır."""
    agac = ast.parse(DEPOLAR.read_text(encoding="utf-8"))
    fonk = next(
        d for d in ast.walk(agac)
        if isinstance(d, ast.FunctionDef) and d.name == "create_transfer"
    )
    cagrilar = [
        d for d in ast.walk(fonk)
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
        and d.func.id == "_lotsuz_yazmayi_reddet"
    ]
    assert len(cagrilar) == 1, "partisiz transfer kapısı yok"
    depo = next(k.value for k in cagrilar[0].keywords if k.arg == "warehouse_id")
    assert ast.unparse(depo) == "depo_id"
    kaynak = ast.unparse(fonk)
    assert (
        "for depo_id in (payload.source_warehouse_id, payload.target_warehouse_id)"
        in kaynak
    )


# ---------------------------------------------------------- saf birim ---


def _iz(**fazla):
    """Tek alıcılı, tek perakende satışlı sentetik iz."""
    temel = dict(
        kok={"id": 1, "lot_code": "L-1", "expiry_date": "2098-01-31"},
        urun={"id": 7, "name": "NPK", "unit": "Kg"},
        kardesler=[{"id": 1, "warehouse_id": 1, "quantity": Decimal("82")}],
        depolar={1: "Merkez"},
        hareketler=[
            {"reference_type": "purchases", "reference_id": 1, "movement_type": "purchase",
             "quantity": Decimal("100"), "movement_date": "2026-09-01"},
            {"reference_type": "orders", "reference_id": 10, "movement_type": "sale",
             "quantity": Decimal("-12"), "movement_date": "2026-09-02"},
            {"reference_type": "orders", "reference_id": 11, "movement_type": "sale",
             "quantity": Decimal("-8"), "movement_date": "2026-09-03"},
            {"reference_type": "returns", "reference_id": 5, "movement_type": "sale_return",
             "quantity": Decimal("2"), "movement_date": "2026-09-04"},
        ],
        partisiz=[],
        siparisler={
            10: {"id": 10, "customer_id": 100, "order_date": "2026-09-02", "document_no": "S-1"},
            11: {"id": 11, "customer_id": 999, "order_date": "2026-09-03", "document_no": "S-2"},
        },
        irsaliyeler={},
        iadeler={5: {"id": 5, "return_type": "sale_return", "entity_id": 100,
                     "return_date": "2026-09-04", "document_no": "I-1",
                     "source_type": "order", "source_id": 10}},
        cariler={100: {"id": 100, "name": "Ali", "phone": "05321112233"}},
        ornekler=[],
        mutabakat=[],
        bosaldi=[],
    )
    temel.update(fazla)
    return SimpleNamespace(**temel)


def test_PERAKENDE_ALICI_DEGIL_ve_iade_dusuluyor() -> None:
    from app.lot_izi_ozet import ozetle

    govde = ozetle(
        _iz(), perakende_cari_id=999,
        iletisim={100: {"has_phone": True, "has_consent": True, "consent_reason": None}},
    )
    assert [m["customer_id"] for m in govde["customers"]] == [100]
    ali = govde["customers"][0]
    assert (ali["quantity_out"], ali["quantity_returned"], ali["quantity_net"]) == (
        Decimal("12"), Decimal("2"), Decimal("10"))
    assert ali["status"] == "notifiable"
    assert govde["pos_retail"] == {
        "quantity_out": Decimal("8"), "quantity_returned": Decimal("0"),
        "quantity_net": Decimal("8"), "document_count": 1,
    }
    assert govde["balance"]["difference"] == Decimal("0"), govde["balance"]


def test_RIZA_ya_da_TELEFON_yoksa_ELLE() -> None:
    from app.lot_izi_ozet import ozetle

    for bilgi in (
        {"has_phone": True, "has_consent": False, "consent_reason": "NO_RECORD"},
        {"has_phone": False, "has_consent": False, "consent_reason": "RECIPIENT_INVALID"},
        {},
    ):
        govde = ozetle(_iz(), perakende_cari_id=999, iletisim={100: bilgi})
        assert govde["customers"][0]["status"] == "manual_pending", bilgi


def test_MASKELI_ROL_telefonu_MASKELI_gorur() -> None:
    """Uçun maske zinciri: `ozetle` -> `maskele_cari(m, rol)`."""
    from app.alan_maskeleme import maskele_cari, maskele_telefon
    from app.lot_izi_ozet import ozetle

    govde = ozetle(_iz(), perakende_cari_id=999, iletisim={})
    ham = govde["customers"][0]
    assert maskele_cari(ham, "depo")["phone"] == maskele_telefon("05321112233")
    assert maskele_cari(ham, "depo")["phone"] != "05321112233"
    assert maskele_cari(ham, "satis")["phone"] == "05321112233"
    assert maskele_cari(ham, "depo")["name"] == "Ali"


# ------------------------------------------------ toplu rıza (tur 3) ---
#
# Runtime lens: `_iletisim` cari başına `evaluate_consent` çağırıyordu (N+1;
# 1.999 cari -> 2.012 sorgu). `evaluate_consents_bulk` TEK okuma yapar ve
# kararı `_karar`a bırakır — fail-closed mantığın ikinci kopyası YOK.

_RIZA_DDL = """CREATE TABLE notification_consents (
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL,
    party_type TEXT NOT NULL, party_id INTEGER NOT NULL, channel TEXT NOT NULL,
    status TEXT NOT NULL, granted_at TEXT, revoked_at TEXT, source TEXT,
    source_ref TEXT, recipient_snapshot TEXT, version INTEGER NOT NULL,
    created_by INTEGER, created_at TEXT, updated_at TEXT)"""


def _riza_db():
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session

    motor = create_engine("sqlite://")
    sayac = {"n": 0}

    @event.listens_for(motor, "before_cursor_execute")
    def _say(*_a, **_k):  # noqa: ANN001
        sayac["n"] += 1

    db = Session(motor)
    db.execute(text(_RIZA_DDL))
    return db, sayac


def _riza_yaz(db, cid, pid, durum, snapshot, kanal="SMS", surum=1):
    from sqlalchemy import text

    db.execute(
        text(
            "INSERT INTO notification_consents (company_id, party_type, party_id,"
            " channel, status, recipient_snapshot, version)"
            " VALUES (:c, 'CUSTOMER', :p, :k, :s, :r, :v)"
        ),
        {"c": cid, "p": pid, "k": kanal, "s": durum, "r": snapshot, "v": surum},
    )


_KARSILASTIRILAN = ("allowed", "decision", "reason", "message", "consent_id", "consent_version")


def test_TOPLU_RIZA_tekli_ile_BIREBIR_ayni() -> None:
    """GRANTED / REVOKED / kayıt yok / numara değişmiş / görüntüsüz (H90) /
    geçersiz telefon / başka firmanın rızası / e-posta kanalı satırı; artı
    bilinmeyen kanal. Her id için `evaluate_consent` ile AYNI karar."""
    from app.notifications.consents import evaluate_consent, evaluate_consents_bulk

    db, _ = _riza_db()
    _riza_yaz(db, 1, 1, "GRANTED", "+905321112233", surum=3)
    _riza_yaz(db, 1, 2, "REVOKED", "+905321112234", surum=2)
    _riza_yaz(db, 1, 4, "GRANTED", "+905329999999")
    _riza_yaz(db, 1, 5, "GRANTED", None)
    _riza_yaz(db, 2, 7, "GRANTED", "+905321112237")
    _riza_yaz(db, 1, 8, "GRANTED", "+905321112238", kanal="WHATSAPP")
    alicilar = {
        1: "0532 111 22 33",
        2: "05321112234",
        3: "05321112235",
        4: "05321112236",
        5: "05321112239",
        6: "bilinmiyor",
        7: "05321112237",
        8: "05321112238",
        9: "",
    }
    for kanal in ("SMS", "sms", "FAX"):
        toplu = evaluate_consents_bulk(
            db, company_id=1, party_type="CUSTOMER", channel=kanal, recipients=alicilar
        )
        assert set(toplu) == set(alicilar)
        for pid, alici in alicilar.items():
            tekli = evaluate_consent(
                db, company_id=1, party_type="CUSTOMER", party_id=pid,
                channel=kanal, recipient=alici,
            )
            assert {k: toplu[pid][k] for k in _KARSILASTIRILAN} == {
                k: tekli[k] for k in _KARSILASTIRILAN
            }, (kanal, pid, toplu[pid], tekli)

    sms = evaluate_consents_bulk(
        db, company_id=1, party_type="CUSTOMER", channel="SMS", recipients=alicilar
    )
    assert {pid: sms[pid]["reason"] for pid in alicilar} == {
        1: None, 2: "REVOKED", 3: "NO_RECORD", 4: "RECIPIENT_CHANGED",
        5: "RECIPIENT_INVALID", 6: "RECIPIENT_INVALID", 7: "NO_RECORD",
        8: "NO_RECORD", 9: "RECIPIENT_INVALID",
    }
    assert (sms[1]["allowed"], sms[1]["consent_id"], sms[1]["consent_version"]) == (True, 1, 3)


def test_TOPLU_RIZA_bos_girdi_SORGUSUZ_ve_500lu_PARCALAR() -> None:
    from app.notifications import consents

    db, sayac = _riza_db()
    once = sayac["n"]
    assert consents.evaluate_consents_bulk(
        db, company_id=1, party_type="CUSTOMER", channel="SMS", recipients={}
    ) == {}
    assert sayac["n"] == once

    adet = 2 * consents.TOPLU_PARCA + 1
    for pid in range(1, adet + 1, 2):
        _riza_yaz(db, 1, pid, "GRANTED", f"+90532{pid:07d}")
    alicilar = {pid: f"0532{pid:07d}" for pid in range(1, adet + 1)}
    once = sayac["n"]
    sonuc = consents.evaluate_consents_bulk(
        db, company_id=1, party_type="CUSTOMER", channel="SMS", recipients=alicilar
    )
    # 1.001 kimlik -> ÜÇ parça (500 + 500 + 1), cari başına sorgu YOK.
    assert sayac["n"] - once == 3
    assert len(sonuc) == adet
    assert all(sonuc[p]["allowed"] is (p % 2 == 1) for p in alicilar)
    assert {sonuc[p]["reason"] for p in alicilar if p % 2 == 0} == {"NO_RECORD"}


def test_ILETISIM_cari_sayisindan_BAGIMSIZ_tek_okuma() -> None:
    """`lots._iletisim` 5 cari de 50 cari de AYNI sayıda ifade yürütür."""
    from app.routers.lots import _iletisim

    db, sayac = _riza_db()
    _riza_yaz(db, 1, 1, "GRANTED", "+905320000001")

    def olc(n):
        cariler = {i: {"phone": f"0532{i:07d}"} for i in range(1, n + 1)}
        once = sayac["n"]
        sonuc = _iletisim(db, 1, cariler)
        assert sonuc[1] == {"has_phone": True, "has_consent": True, "consent_reason": None}
        assert sonuc[n]["consent_reason"] == "NO_RECORD"
        return sayac["n"] - once

    assert olc(5) == olc(50) == 1


# ------------------------------------------------------------ davranış ---


def test_GERI_CAGIRMA_ONIZLEMESI_ve_PARTISIZ_TRANSFER(tmp_path: Path) -> None:
    """Uçtan uca, gerçek şema (alt süreçte `app.main` alembic'i sürer)."""
    veritabani = tmp_path / "f10-4a.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    env["SUNGUR_DATA_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(BACKEND)
    tamamlandi = subprocess.run(
        [sys.executable, "-c", _DAVRANIS], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    assert tamamlandi.returncode == 0, tamamlandi.stdout + "\n" + tamamlandi.stderr
    assert "F10-4A OK" in tamamlandi.stdout


#: Cevap şemasının üst anahtarları (AGY tur-1: `response_model` yoktu,
#: `types.gen.ts` gövdeyi `unknown` yazıyordu).
UST_ANAHTARLAR = {
    "lot", "siblings", "customers", "pos_retail", "gaps", "other_movements", "balance",
    "uyarilar",
}

_SEMA = r'''
import json
from app.main import app

sema = app.openapi()
cevap = sema['paths']['/api/lots/{lot_id}/recall-preview']['get']['responses']['200']
govde = cevap['content']['application/json']['schema']
ad = govde['$ref'].rsplit('/', 1)[-1]
bilesen = sema['components']['schemas'][ad]
print(json.dumps({'ad': ad, 'ozellikler': sorted(bilesen['properties']),
                  'zorunlu': sorted(bilesen.get('required', []))}))
'''


def test_OPENAPI_cevap_semasi_UST_ANAHTARLARI_yazar(tmp_path: Path) -> None:
    """Uç `response_model` taşır; şema üst anahtarları gövdenin anahtarlarıdır."""
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'sema.db').as_posix()}"
    env["SUNGUR_DATA_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(BACKEND)
    tamamlandi = subprocess.run(
        [sys.executable, "-c", _SEMA], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    assert tamamlandi.returncode == 0, tamamlandi.stdout + "\n" + tamamlandi.stderr
    sema = json.loads(tamamlandi.stdout.strip().splitlines()[-1])
    assert sema["ad"] == "GeriCagirmaOnizleme", sema
    assert set(sema["ozellikler"]) == UST_ANAHTARLAR, sema
    assert set(sema["zorunlu"]) == UST_ANAHTARLAR, sema


_DAVRANIS = r'''
import random
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.alan_maskeleme import maskele_telefon
from app.auth import ROLE_PERMISSIONS, hash_password
from app.db import SessionLocal
from app.field_stok_tuketici import _hareket_yaz
from app.main import app
from app.notifications.consents import set_consent

client = TestClient(app)
# KOŞU EKİ: PG ikizi aynı betiği PAYLAŞILAN bir veritabanında yeniden koşar;
# tekil adlar (depo kodu, kullanıcı, komşu firma, POS anahtarı) çakışmasın.
EK = uuid.uuid4().hex[:6]
YAKIN = '2098-01-31'
UZAK = '2099-01-31'


def ok(cevap):
    assert cevap.status_code < 300, (cevap.status_code, cevap.text)
    return cevap.json() if cevap.content else None


def D(deger):
    return Decimal(str(deger))


cevap = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
assert cevap.status_code == 200, cevap.text
govde = cevap.json()
baslik = {'Authorization': 'Bearer ' + govde['access_token'],
          'X-Company-ID': str(govde['companies'][0]['id'])}
cid = int(govde['companies'][0]['id'])
degisti = client.post('/api/auth/change-password', headers=baslik,
                      json={'current_password': 'admin123', 'new_password': 'GeriCagir!123'})
assert degisti.status_code == 200, degisti.text
baslik['Authorization'] = 'Bearer ' + degisti.json()['access_token']

ok(client.put('/api/company-settings', headers=baslik,
              json={'negative_stock_policy': 'allow', 'credit_limit_policy': 'block'}))

depo_a = ok(client.get('/api/warehouses', headers=baslik))[0]['id']
depo_b = ok(client.post('/api/warehouses', headers=baslik,
                        json={'name': f'Geri Çağırma B {EK}', 'code': f'GC{EK}'}))['id']
tedarikci = ok(client.post('/api/suppliers', headers=baslik, json={'name': 'GC Tedarikçi'}))['id']
ali = ok(client.post('/api/customers', headers=baslik,
                     json={'name': 'Çiftçi Ali', 'phone': '05321112233'}))['id']
berk = ok(client.post('/api/customers', headers=baslik,
                      json={'name': 'Çiftçi Berk', 'phone': '05324445566'}))['id']
cem = ok(client.post('/api/customers', headers=baslik, json={'name': 'Çiftçi Cem'}))['id']

with SessionLocal() as db:
    set_consent(db, company_id=cid, party_type='CUSTOMER', party_id=ali, channel='SMS',
                granted=True, source='FORM', source_ref='f10-4a', recipient='05321112233',
                user_id=None)
    db.commit()


def urun_ac(ad):
    return ok(client.post('/api/products', headers=baslik,
                          json={'name': ad, 'purchase_price': 10, 'sale_price': 20,
                                'vat_rate': 20, 'stock': 0, 'unit': 'Kg'}))['id']


def kalem(pid, adet, kod=None, skt=None, fiyat=10):
    satir = {'product_id': pid, 'quantity': adet, 'unit_price': fiyat, 'vat_rate': 20}
    if kod is not None:
        satir['lot_code'] = kod
    if skt is not None:
        satir['expiry_date'] = skt
    return satir


def alis(depo, kalemler):
    return ok(client.post('/api/purchases', headers=baslik, json={
        'entity_id': tedarikci, 'transaction_date': '2026-09-01', 'warehouse_id': depo,
        'items': kalemler}))


def satis(cari, adet, depo, tarih):
    return ok(client.post('/api/orders', headers=baslik, json={
        'entity_id': cari, 'transaction_date': tarih, 'due_date': '2026-10-30',
        'warehouse_id': depo, 'items': [kalem(npk, adet, fiyat=20)]}))


def transfer(pid, adet, kod=None):
    item = {'product_id': pid, 'quantity': adet}
    if kod is not None:
        item['lot_code'] = kod
    return client.post('/api/warehouses/transfers', headers=baslik, json={
        'source_warehouse_id': depo_a, 'target_warehouse_id': depo_b,
        'transfer_date': '2026-09-04', 'items': [item]})


npk = urun_ac('NPK 15-15-15')
alis(depo_a, [kalem(npk, 100, 'L-RECALL', YAKIN)])
alis(depo_a, [kalem(npk, 50, 'L-OTEKI', UZAK)])

satis_ali = satis(ali, 30, depo_a, '2026-09-02')
ok(client.post('/api/workflow/delivery', headers=baslik, json={
    'entity_id': berk, 'document_date': '2026-09-03', 'status': 'completed',
    'warehouse_id': depo_a, 'items': [kalem(npk, 20, fiyat=20)]}))
ok(client.post('/api/pos/sale', headers={**baslik, 'Idempotency-Key': f'f10-4a-pos-{EK}'}, json={
    'items': [{'product_id': npk, 'quantity': 5, 'unit_price': 20}],
    'payment_type': 'cash', 'warehouse_id': depo_a}))
ok(transfer(npk, 10, 'L-RECALL'))
satis(cem, 10, depo_b, '2026-09-05')
ok(client.post('/api/workflow/sale_return', headers=baslik, json={
    'entity_id': ali, 'document_date': '2026-09-06', 'status': 'completed',
    'warehouse_id': depo_a, 'source_type': 'order', 'source_id': satis_ali['id'],
    'items': [kalem(npk, 5, fiyat=20)]}))
ok(client.post('/api/workflow/purchase_return', headers=baslik, json={
    'entity_id': tedarikci, 'document_date': '2026-09-07', 'status': 'completed',
    'warehouse_id': depo_a, 'items': [kalem(npk, 3, fiyat=10)]}))
# G2: KAYNAKSIZ satış iadesi — partiye DÖNMEZ (`lot_id` NULL).
kaynaksiz = ok(client.post('/api/workflow/sale_return', headers=baslik, json={
    'entity_id': berk, 'document_date': '2026-09-08', 'status': 'completed',
    'warehouse_id': depo_a, 'items': [kalem(npk, 2, fiyat=20)]}))
# G5: tarla faaliyeti tüketimi — GERÇEK yazıcı (`field_stok_tuketici._hareket_yaz`).
with SessionLocal() as db:
    _hareket_yaz(db, cid, npk, depo_a, Decimal('-4'), random.randint(10 ** 8, 2 ** 31 - 1),
                'F10-4a tarla girdisi')
    db.commit()


def parti_kimligi(depo):
    with SessionLocal() as db:
        return db.execute(text(
            "SELECT id FROM product_lots WHERE company_id=:cid AND product_id=:pid "
            "AND lot_code='L-RECALL' AND warehouse_id=:wid"),
            {'cid': cid, 'pid': npk, 'wid': depo}).scalar_one()


kok_id = parti_kimligi(depo_a)
b_id = parti_kimligi(depo_b)


def onizleme(lot_id, basliklar=None):
    return client.get(f'/api/lots/{lot_id}/recall-preview', headers=basliklar or baslik)


rapor = ok(onizleme(kok_id))

# ----- PARTİ ve KARDEŞLER (HOP 1) -----
assert rapor['lot']['lot_code'] == 'L-RECALL', rapor['lot']
assert rapor['lot']['expiry_date'] == YAKIN, rapor['lot']
assert rapor['lot']['product_id'] == npk
assert [(s['lot_id'], s['warehouse_id'], D(s['quantity'])) for s in rapor['siblings']] == [
    (kok_id, depo_a, Decimal('37')), (b_id, depo_b, Decimal('0'))], rapor['siblings']

# ----- ALICILAR (HOP 2-3) -----
alicilar = {m['customer_id']: m for m in rapor['customers']}
assert [m['customer_id'] for m in rapor['customers']] == [ali, berk, cem], rapor['customers']
beklenen = {
    ali: (Decimal('30'), Decimal('5'), Decimal('25'), ['orders'], 'notifiable'),
    berk: (Decimal('20'), Decimal('0'), Decimal('20'), ['delivery_notes'], 'manual_pending'),
    cem: (Decimal('10'), Decimal('0'), Decimal('10'), ['orders'], 'manual_pending'),
}
for cari, (cikan, iade, net, kanal, durum) in beklenen.items():
    m = alicilar[cari]
    assert (D(m['quantity_out']), D(m['quantity_returned']), D(m['quantity_net'])) == (
        cikan, iade, net), m
    assert m['channels'] == kanal, m
    assert m['status'] == durum, m
assert alicilar[ali]['has_phone'] and alicilar[ali]['has_consent'], alicilar[ali]
assert alicilar[berk]['has_phone'] and not alicilar[berk]['has_consent'], alicilar[berk]
assert alicilar[berk]['consent_reason'] == 'NO_RECORD', alicilar[berk]
assert not alicilar[cem]['has_phone'] and not alicilar[cem]['has_consent'], alicilar[cem]
assert (alicilar[ali]['first_date'], alicilar[ali]['last_date']) == ('2026-09-02', '2026-09-02')
assert alicilar[cem]['first_date'] == '2026-09-05'
# admin maskesiz: telefon TAM.
assert alicilar[ali]['phone'] == '05321112233'

# ----- POS PERAKENDE (G1, K8) -----
assert {k: D(v) for k, v in rapor['pos_retail'].items()} == {
    'quantity_out': Decimal('5'), 'quantity_returned': Decimal('0'),
    'quantity_net': Decimal('5'), 'document_count': Decimal('1')}, rapor['pos_retail']
assert 'Perakende Satış' not in {m['name'] for m in rapor['customers']}

# ----- BOŞLUKLAR (G2, G5) -----
assert [(g['code'], g['reference_type'], g['movement_count'], D(g['quantity']))
        for g in rapor['gaps']] == [
    ('tarla_hareketi', 'field_integration_event', 1, Decimal('-4')),
    ('kaynaksiz_iade', 'returns', 1, Decimal('2')),
], rapor['gaps']

# ----- H117: boşluk satırı ARKASINDAKİ belge (kimlikle) -----
tarla_gap, iade_gap = rapor['gaps']
assert len(tarla_gap['ornekler']) == 1 and not tarla_gap['ornek_kesildi'], tarla_gap
assert tarla_gap['ornekler'][0]['reference_type'] == 'field_integration_event'
assert tarla_gap['ornekler'][0]['customer_id'] is None, tarla_gap
assert tarla_gap['ornekler'][0]['document_no'] is None, tarla_gap
with SessionLocal() as db:
    iade_no = db.execute(text(
        "SELECT document_no FROM returns WHERE company_id=:cid AND id=:id"),
        {'cid': cid, 'id': kaynaksiz['id']}).scalar_one()
iade_ornegi = iade_gap['ornekler'][0]
assert len(iade_gap['ornekler']) == 1 and iade_gap['ornek_kesildi'] is False, iade_gap
assert (iade_ornegi['reference_type'], iade_ornegi['reference_id'], iade_ornegi['document_no'],
        iade_ornegi['customer_id'], iade_ornegi['date'], D(iade_ornegi['quantity'])) == (
    'returns', kaynaksiz['id'], iade_no, berk, '2026-09-08', Decimal('2')), iade_ornegi
# Cari ADI/telefonu örnekte YOK (yalnız kimlik).
assert set(iade_ornegi) == {
    'reference_type', 'reference_id', 'document_no', 'customer_id', 'date', 'quantity'}


# ----- H116: uyarilar — A deposu SAPMA (kaynaksız +2, tarla −4 => −2) -----
def uyari_ozeti(govde):
    return [(u['kod'], u['warehouse_id'], None if u['miktar'] is None else D(u['miktar']))
            for u in govde['uyarilar']]


assert uyari_ozeti(rapor) == [
    ('mutabakat_sapma', depo_a, Decimal('-2')),
    ('kaynaksiz_iade', None, Decimal('2')),
], rapor['uyarilar']
assert all(u['mesaj'] for u in rapor['uyarilar'])

# ----- DENGE: giren = alıcılar + perakende + tedarikçiye iade + eldeki -----
denge = {k: D(v) for k, v in rapor['balance'].items()}
assert denge == {
    'received': Decimal('100'), 'customers_net': Decimal('55'),
    'pos_retail_net': Decimal('5'), 'returned_to_supplier': Decimal('3'),
    'other': Decimal('0'), 'on_hand': Decimal('37'), 'difference': Decimal('0'),
}, denge

# Kardeş satırdan sorulan önizleme AYNI partiyi verir (B deposundaki kimlik).
rapor_b = ok(onizleme(b_id))
assert rapor_b['customers'] == rapor['customers']
assert rapor_b['balance'] == rapor['balance']

# ----- KİRACI: komşu firma 404 -----
komsu = ok(client.post('/api/companies', headers=baslik,
                       json={'name': f'Geri Çağırma Komşu {EK}'}))['id']
komsu_baslik = {**baslik, 'X-Company-ID': str(komsu)}
assert onizleme(kok_id, komsu_baslik).status_code == 404
assert onizleme(10 ** 9).status_code == 404

# ----- ROLLER: depo 403, satis TAM, maskeli rol MASKELİ -----
simdi = datetime.now(timezone.utc)
with SessionLocal() as db:
    for rol in ('depo', 'satis'):
        uid = db.execute(text(
            "INSERT INTO app_users(username,email,display_name,password_hash,role,"
            "is_active,must_change_password,email_verified,created_at) "
            "VALUES(:k,:e,:d,:p,:r,:a,:m,:v,:t) RETURNING id"),
            {'k': f'gc_{rol}_{EK}', 'e': f'gc_{rol}_{EK}@ornek.test', 'd': rol,
             'p': hash_password('GeriCagir!Rol1'), 'r': rol, 'a': True, 'm': False,
             'v': True, 't': simdi}).scalar_one()
        db.execute(text(
            "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
            "VALUES(:u,:c,true,:t)"), {'u': uid, 'c': cid, 't': simdi})
    db.commit()


def rol_basligi(rol):
    giris = client.post('/api/auth/login',
                        json={'username': f'gc_{rol}_{EK}', 'password': 'GeriCagir!Rol1'})
    assert giris.status_code == 200, giris.text
    return {'Authorization': 'Bearer ' + giris.json()['access_token'],
            'X-Company-ID': str(cid)}


depo_basligi = rol_basligi('depo')
assert onizleme(kok_id, depo_basligi).status_code == 403
satis_rapor = ok(onizleme(kok_id, rol_basligi('satis')))
assert {m['customer_id']: m['phone'] for m in satis_rapor['customers']}[ali] == '05321112233'
# Maskeli bir rol `sales` taşısaydı (bugün hiçbiri taşımıyor) telefon MASKELİ
# gelirdi: maske UÇTA bağlı mı, çalışma anında ölçülüyor.
eski = ROLE_PERMISSIONS['depo']
ROLE_PERMISSIONS['depo'] = set(eski) | {'sales'}
try:
    maskeli = ok(onizleme(kok_id, depo_basligi))
finally:
    ROLE_PERMISSIONS['depo'] = eski
maskeli_tel = {m['customer_id']: m['phone'] for m in maskeli['customers']}
assert maskeli_tel[ali] == maskele_telefon('05321112233') != '05321112233', maskeli_tel
assert maskeli_tel[berk] == maskele_telefon('05324445566') != '05324445566', maskeli_tel
assert maskeli_tel[cem] is None, maskeli_tel

# ----- SORGU BÜTÇESİ (tur 3): önizleme cari sayısından BAĞIMSIZ -----
# Runtime lens N+1'i ölçtü (cari başına bir rıza SELECT'i). Aynı partiden
# 5 sonra 50 telefonlu alıcıya satış; iki önizleme AYNI sayıda ifade yürütür.
from sqlalchemy import event
from app.db import engine

butce_urun = urun_ac(f'Bütçe Gübresi {EK}')
alis(depo_a, [kalem(butce_urun, 100, 'L-BUTCE', UZAK)])
with SessionLocal() as db:
    butce_lot = db.execute(text(
        "SELECT id FROM product_lots WHERE company_id=:cid AND product_id=:pid "
        "AND lot_code='L-BUTCE'"), {'cid': cid, 'pid': butce_urun}).scalar_one()


def butce_alicisi(i):
    cari = ok(client.post('/api/customers', headers=baslik, json={
        'name': f'Bütçe Alıcı {EK} {i}', 'phone': f'0533{i:07d}'}))['id']
    ok(client.post('/api/orders', headers=baslik, json={
        'entity_id': cari, 'transaction_date': '2026-09-09', 'due_date': '2026-10-30',
        'warehouse_id': depo_a, 'items': [kalem(butce_urun, 1, fiyat=20)]}))
    if i % 2:
        with SessionLocal() as db:
            set_consent(db, company_id=cid, party_type='CUSTOMER', party_id=cari,
                        channel='SMS', granted=True, source='FORM', source_ref='butce',
                        recipient=f'0533{i:07d}', user_id=None)
            db.commit()


def ifade_say(lot=None):
    sayac = [0]

    def _say(*_a, **_k):
        sayac[0] += 1

    event.listen(engine, 'before_cursor_execute', _say)
    try:
        govde = ok(onizleme(butce_lot if lot is None else lot))
    finally:
        event.remove(engine, 'before_cursor_execute', _say)
    return sayac[0], govde


for i in range(1, 6):
    butce_alicisi(i)
az, govde_az = ifade_say()
assert len(govde_az['customers']) == 5
for i in range(6, 51):
    butce_alicisi(i)
cok, govde_cok = ifade_say()
assert len(govde_cok['customers']) == 50
assert az == cok, ('cari basina sorgu', az, cok)
assert sum(m['has_consent'] for m in govde_cok['customers']) == 25
print('SORGU BUTCESI', az, cok)

# ----- G4: PARTİLİ ürünün PARTİSİZ transferi REDDEDİLİR -----
def hareket_sayisi(pid):
    with SessionLocal() as db:
        return db.execute(text(
            "SELECT COUNT(*) FROM stock_movements WHERE company_id=:cid AND product_id=:pid"),
            {'cid': cid, 'pid': pid}).scalar_one()


once = hareket_sayisi(npk)
red = transfer(npk, 4)
assert red.status_code == 409, (red.status_code, red.text)
assert red.json()['detail']['code'] == 'LOT_TAKIPLI_URUN_LOTSUZ_YAZILAMAZ', red.text
assert hareket_sayisi(npk) == once
# YALNIZ HEDEFTE defteri açık ürün: kaynakta (A) parti yok, hedefte (B) var.
yalniz_hedef = urun_ac('Yalnız Hedefte Partili')
alis(depo_b, [kalem(yalniz_hedef, 5, 'L-HEDEF', UZAK)])
alis(depo_a, [kalem(yalniz_hedef, 5)])
red_hedef = transfer(yalniz_hedef, 2)
assert red_hedef.status_code == 409, (red_hedef.status_code, red_hedef.text)
# PARTİSİZ ürün (hiç defter açılmamış) serbestçe transfer edilir.
duz = urun_ac('Partisiz Gübre')
alis(depo_a, [kalem(duz, 10)])
gecti = transfer(duz, 4)
assert gecti.status_code == 201, (gecti.status_code, gecti.text)
# Partili transfer hâlâ çalışıyor.
assert transfer(npk, 1, 'L-RECALL').status_code == 201

# ----- H116 / G4: ESKİ VERİ — kapıdan (#179) ÖNCE yazılmış partisiz transfer -----
# Üretimde kapı öncesi satırlar DURUYOR; aynı yazıcı kapısız koşturularak
# üretilir (kod yolu gerçek, yalnız kapı o an yok).
import app.routers.warehouses as depo_modulu

kapi = depo_modulu._lotsuz_yazmayi_reddet
depo_modulu._lotsuz_yazmayi_reddet = lambda *a, **k: None
try:
    eski = transfer(npk, 4)
finally:
    depo_modulu._lotsuz_yazmayi_reddet = kapi
assert eski.status_code == 201, (eski.status_code, eski.text)
eski_transfer = eski.json()['id']
rapor_g4 = ok(onizleme(kok_id))
# A: önceki −2, partisiz çıkış −4 => −6. B: partisiz giriş +4 (parti 1, stok 5).
assert [u for u in uyari_ozeti(rapor_g4) if u[0] == 'mutabakat_sapma'] == [
    ('mutabakat_sapma', depo_a, Decimal('-6')),
    ('mutabakat_sapma', depo_b, Decimal('4')),
], rapor_g4['uyarilar']
transfer_gaps = [g for g in rapor_g4['gaps'] if g['code'] == 'partisiz_transfer']
assert sorted((g['movement_type'], D(g['quantity'])) for g in transfer_gaps) == [
    ('transfer_in', Decimal('4')), ('transfer_out', Decimal('-4'))], transfer_gaps
for g in transfer_gaps:
    assert [(o['reference_type'], o['reference_id'], o['customer_id'])
            for o in g['ornekler']] == [('transfer', eski_transfer, None)], g

# ----- H116: `defter_bosaldi` — partiler TÜKENMİŞKEN satış partisiz çıkar -----
bos_urun = urun_ac(f'Boşaldı Gübresi {EK}')
alis(depo_a, [kalem(bos_urun, 5, 'L-BOSALDI', UZAK)])
ok(client.post('/api/orders', headers=baslik, json={
    'entity_id': ali, 'transaction_date': '2026-09-10', 'due_date': '2026-10-30',
    'warehouse_id': depo_a, 'items': [kalem(bos_urun, 5, fiyat=20)]}))
bos_satis = ok(client.post('/api/orders', headers=baslik, json={
    'entity_id': ali, 'transaction_date': '2026-09-11', 'due_date': '2026-10-30',
    'warehouse_id': depo_a, 'items': [kalem(bos_urun, 2, fiyat=20)]}))
with SessionLocal() as db:
    bos_lot = db.execute(text(
        "SELECT id FROM product_lots WHERE company_id=:cid AND product_id=:pid"),
        {'cid': cid, 'pid': bos_urun}).scalar_one()
rapor_bos = ok(onizleme(bos_lot))
assert uyari_ozeti(rapor_bos) == [
    ('mutabakat_sapma', depo_a, Decimal('-2')),
    ('defter_bosaldi', depo_a, Decimal('-2')),
], rapor_bos['uyarilar']
[bos_gap] = rapor_bos['gaps']
assert (bos_gap['code'], bos_gap['reference_type'], bos_gap['movement_count']) == (
    'partisiz_hareket', 'orders', 1), bos_gap
assert [(o['reference_id'], o['customer_id']) for o in bos_gap['ornekler']] == [
    (bos_satis['id'], ali)], bos_gap

# ----- H117: >20 boşluk hareketi -> 20 örnek + `ornek_kesildi`, (tarih, id) -----
# İadeler TERS tarih sırasıyla yazılır: kimlik sırası ile tarih sırası ZIT,
# yani `ORDER BY` düşerse (kimlik sırası) başka 20'si gelir.
ornek_urun = urun_ac(f'Örnek Gübresi {EK}')
alis(depo_a, [kalem(ornek_urun, 100, 'L-ORNEK', UZAK)])
# Bir alıcı: rıza okuması bütçe partisindeki gibi koşsun (boş girdi sorgusuzdur).
ok(client.post('/api/orders', headers=baslik, json={
    'entity_id': ali, 'transaction_date': '2026-09-05', 'due_date': '2026-10-30',
    'warehouse_id': depo_a, 'items': [kalem(ornek_urun, 1, fiyat=20)]}))
ornek_iadeler = []
for gun in range(30, 5, -1):
    ornek_iadeler.append(ok(client.post('/api/workflow/sale_return', headers=baslik, json={
        'entity_id': berk, 'document_date': f'2026-09-{gun:02d}', 'status': 'completed',
        'warehouse_id': depo_a, 'items': [kalem(ornek_urun, 1, fiyat=20)]}))['id'])
with SessionLocal() as db:
    ornek_lot = db.execute(text(
        "SELECT id FROM product_lots WHERE company_id=:cid AND product_id=:pid"),
        {'cid': cid, 'pid': ornek_urun}).scalar_one()
ornek_sayi, rapor_ornek = ifade_say(ornek_lot)
[ornek_gap] = rapor_ornek['gaps']
assert ornek_gap['movement_count'] == 25 and ornek_gap['ornek_kesildi'] is True, ornek_gap
assert [o['reference_id'] for o in ornek_gap['ornekler']] == ornek_iadeler[::-1][:20], [
    (o['reference_id'], o['date']) for o in ornek_gap['ornekler']]
assert [o['date'] for o in ornek_gap['ornekler']] == [
    f'2026-09-{gun:02d}' for gun in range(6, 26)], ornek_gap['ornekler']
assert {o['customer_id'] for o in ornek_gap['ornekler']} == {berk}
assert ok(onizleme(ornek_lot)) == rapor_ornek
assert uyari_ozeti(rapor_ornek) == [
    ('mutabakat_sapma', depo_a, Decimal('25')),
    ('kaynaksiz_iade', None, Decimal('25')),
], rapor_ornek['uyarilar']
# Sorgu sayısı örnek/boşluk sayısından BAĞIMSIZ (boşluksuz bütçe partisiyle aynı).
assert ornek_sayi == cok, ('ornek basina sorgu', ornek_sayi, cok)

# ----- H116: TEMİZ parti -> uyarilar BOŞ -----
temiz_urun = urun_ac(f'Temiz Gübre {EK}')
alis(depo_a, [kalem(temiz_urun, 10, 'L-TEMIZ', UZAK)])
with SessionLocal() as db:
    temiz_lot = db.execute(text(
        "SELECT id FROM product_lots WHERE company_id=:cid AND product_id=:pid"),
        {'cid': cid, 'pid': temiz_urun}).scalar_one()
rapor_temiz = ok(onizleme(temiz_lot))
assert rapor_temiz['uyarilar'] == [] and rapor_temiz['gaps'] == [], rapor_temiz

print('F10-4A OK')
'''

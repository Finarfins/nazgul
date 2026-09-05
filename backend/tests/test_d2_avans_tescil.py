"""D2 — avans, borsa tescili, net ödeme ve stopaj defteri.

Konu: göç `20260906_0071`, `app/avans_engine.py`, `app/avans_schemas.py`,
`app/routers/avans.py` ve `app/routers/mustahsil.py`in D1'e eklenen iki
kancası (`issue` sonrası yükümlülük/mahsup, `cancel` öncesi engeller).

--- BU DOSYANIN İDDİA ETTİĞİ ŞEY -------------------------------------------

D1 makbuzu KESTİ ve bir BORÇ doğurdu; D2 o borcun KAPANMASIDIR. Üç olgu
sınanıyor ve üçü de SATIRDAN okunuyor, yanıttan DEĞİL:

  1. Avans kasadan ÇIKAR (`payments` + `finance_transactions` satırı).
  2. `issue` iki yükümlülük satırı yazar ve açık avansları FIFO ile mahsup
     eder; `cash_due` = net − mahsup.
  3. İptal, dış dünyaya çıkmış bir iz varsa REDDEDİLİR; yoksa kapanmamış
     yükümlülükleri AYNI İŞLEMDE siler.

--- STATİK KAPILAR DAVRANIŞTAN ÖNCE KIRILIR --------------------------------

`issue` kancası kaldırıldığında ya da izin kuralı `read`in altına
kaydırıldığında, davranış testi kırmızı olmadan ÖNCE ilgili statik kapı
kırmızı olur ve SEBEBİNİ adıyla söyler.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MOTOR = BACKEND / "app" / "avans_engine.py"
ROUTER = BACKEND / "app" / "routers" / "avans.py"
D1_ROUTER = BACKEND / "app" / "routers" / "mustahsil.py"
GOC = BACKEND / "alembic" / "versions" / "20260906_0071_avans_tescil_vergi.py"


# ---------------------------------------------------------------------------
# STATİK KAPILAR — veritabanı YOK.
# ---------------------------------------------------------------------------


def test_yazma_semasinda_sunucunun_turettigi_alanlar_YOK() -> None:
    """Türev tutarlar hiçbir yazma şemasında YOK; `extra=forbid` onları 422 yapar.

    Alan şemada olsaydı bir istemci "avansımdan 10.000 mahsup ettim"
    diyebilir ve çiftçiye ödenecek net SESSİZCE sıfırlanabilirdi. D1'in
    aynı adlı kapısının D2 alanlarıyla eşi.
    """
    from app.avans_schemas import (
        ExchangeRegistrationWrite,
        ProducerReceiptPaymentWrite,
        SupplierAdvanceWrite,
    )

    avans = set(SupplierAdvanceWrite.model_fields)
    for turev in ("remaining_amount", "receipt_id", "applied_at", "payment_id"):
        assert turev not in avans, (turev, sorted(avans))

    odeme = set(ProducerReceiptPaymentWrite.model_fields)
    for turev in ("cash_due", "net_payable", "advance_applied_total"):
        assert turev not in odeme, (turev, sorted(odeme))

    tescil = set(ExchangeRegistrationWrite.model_fields)
    assert "receipt_id" not in tescil, sorted(tescil)

    for model in (
        SupplierAdvanceWrite,
        ProducerReceiptPaymentWrite,
        ExchangeRegistrationWrite,
    ):
        assert model.model_config.get("extra") == "forbid", model.__name__


def test_ucret_SIFIR_olabilir_ama_tutarlar_OLAMAZ() -> None:
    """`fee_amount` `ge=0`, `amount`lar `gt=0` — ayrım BİLEREK.

    Ücretsiz tescil GERÇEK bir hâldir ve yasaklanması onu kaydedilemez
    yapardı. Sıfır tutarlı bir avans ise kasadan hiçbir şey çıkmadan
    defterde satır bırakırdı.
    """
    from decimal import Decimal

    import pytest
    from pydantic import ValidationError

    from app.avans_schemas import ExchangeRegistrationWrite, SupplierAdvanceWrite

    tescil = ExchangeRegistrationWrite(
        registration_no="T-1",
        exchange_name="Borsa",
        registered_on="2026-09-06",
        fee_amount=Decimal("0"),
    )
    assert tescil.fee_amount == Decimal("0")

    with pytest.raises(ValidationError):
        SupplierAdvanceWrite(
            amount=Decimal("0"),
            payment_method="cash",
            payment_date="2026-09-06",
        )


def test_NaN_ve_sonsuzluk_bir_avansa_GIREMEZ() -> None:
    """`_sonlu` kapısı D2'nin her tutar alanında da duruyor."""
    from decimal import Decimal

    import pytest
    from pydantic import ValidationError

    from app.avans_schemas import (
        ProducerReceiptPaymentWrite,
        SupplierAdvanceWrite,
    )

    for ham in ("NaN", "Infinity", "-Infinity", "sNaN"):
        with pytest.raises(ValidationError):
            SupplierAdvanceWrite(
                amount=Decimal(ham),
                payment_method="cash",
                payment_date="2026-09-06",
            )
        with pytest.raises(ValidationError):
            ProducerReceiptPaymentWrite(
                amount=Decimal(ham),
                payment_method="cash",
                payment_date="2026-09-06",
            )


def test_hareket_haritasi_iki_yeni_cifti_TANIYOR_yanlisi_REDDEDIYOR() -> None:
    """Kapalı küme İKİ giriş büyüdü; yanlış eşleşme hâlâ 422.

    Kapının DAR olduğunu göstermek için yanlış çiftler de sınanıyor:
    kümeyi büyütmek onu AÇMAK değildir.
    """
    import pytest
    from fastapi import HTTPException

    from app.movement_references import validate_payment_reference

    kaynak = (BACKEND / "app" / "movement_references.py").read_text(
        encoding="utf-8"
    )
    assert '("supplier", "supplier_advance"): ("supplier_advances"' in kaynak
    assert '("supplier", "producer_receipt"): ("producer_receipts"' in kaynak

    # YANLIŞ ÇİFT: müşteri carisi bir müstahsil makbuzuna bağlanamaz. Kapı
    # tabloya HİÇ gitmeden 422 verir, bu yüzden oturum None geçilebiliyor.
    for tur in ("producer_receipt", "supplier_advance"):
        with pytest.raises(HTTPException) as hata:
            validate_payment_reference(None, 1, "customer", 1, tur, 1)
        assert hata.value.status_code == 422, tur


def test_uclar_purchases_iznine_bagli_GET_DAHIL() -> None:
    """Avans ve vergi defteri okumaları da `purchases` — `read` DEĞİL.

    Kural temel ``read`` kuralının ÜSTÜNDE durmalı. Altında kalsaydı
    çiftçiye ne ödendiği, okuma yetkisi olan HERKESE görünürdü.
    """
    from app.auth import required_permission

    assert required_permission("GET", "/api/tax-liabilities") == "purchases"
    assert required_permission("POST", "/api/tax-liabilities") == "purchases"
    assert required_permission("GET", "/api/suppliers/5/advances") == "purchases"
    assert required_permission("POST", "/api/suppliers/5/advances") == "purchases"
    assert (
        required_permission("POST", "/api/producer-receipts/7/pay") == "purchases"
    )
    assert (
        required_permission(
            "POST", "/api/producer-receipts/7/exchange-registration"
        )
        == "purchases"
    )
    # Kapının GERÇEKTEN bu satırlar sayesinde kapalı olduğunu göster:
    # tedarikçinin BAŞKA bir alt yolu hâlâ temel `read` kuralına düşer.
    assert required_permission("GET", "/api/suppliers/5/notes") == "read"


def test_yukumluluk_kapatma_ucu_YOK() -> None:
    """`settled_at`i dolduran bir uç YOK — kapatma D2'de ÖLÇÜLMEDİ.

    Kapatma bir ödemeyi vergi dairesine bağlamayı ister ve
    `payments.entity_type` kapalı kümesinde böyle bir cari YOKTUR. Uydurmak
    yerine yazılmadı; bu kapı "sessizce eklenmesini" engeller.
    """
    kaynak = ROUTER.read_text(encoding="utf-8")
    assert "/settle" not in kaynak, (
        "Yükümlülük kapatma ucu eklenmiş: kapatmanın hangi cariye "
        "bağlanacağı ÖLÇÜLMEDİ (bkz. PR gövdesi)."
    )
    assert "@router.delete" not in kaynak, (
        "Silme ucu eklenmiş: avans ve tescil belgeye bağlı olgulardır."
    )
    assert "settled_at=" not in MOTOR.read_text(encoding="utf-8")


def test_motorun_SQLi_TAMAMEN_SABIT_METIN() -> None:
    """`avans_engine.py` ve `routers/avans.py`de `text()` hep SABİT metin alır.

    Bu kapı, kiracı yükleminin bir gün değişkene çevrilmesini davranış
    testinden ÖNCE yakalar: sabit metin, `company_id=:cid`in sorgudan
    ÇIKARILAMAYACAĞI anlamına gelir.
    """
    for dosya in (MOTOR, ROUTER):
        agac = ast.parse(dosya.read_text(encoding="utf-8"))
        for dugum in ast.walk(agac):
            if not (
                isinstance(dugum, ast.Call)
                and isinstance(dugum.func, ast.Name)
                and dugum.func.id == "text"
                and dugum.args
            ):
                continue
            arg = dugum.args[0]
            # Bitişik dizgi literalleri AST'de tek `Constant`a katlanır;
            # f-string (JoinedStr) ve birleştirme (BinOp) KATLANMAZ.
            assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), (
                f"{dosya.name}:{dugum.lineno} — text() sabit olmayan bir "
                "argüman alıyor; kiracı yüklemi SQL'den çıkarılabilir hâle "
                "gelir."
            )


def test_kiraci_yuklemi_motorun_HER_sorgusunda() -> None:
    """Motorun her `text()` metni kiracıya BAĞLI — iki biçimden biriyle.

    SORGULARDA yüklem `company_id=:cid` olarak LİTERAL geçer. INSERT'lerde
    yüklem OLMAZ; orada kiracı SÜTUN LİSTESİNDE durur ve değeri `:cid`e
    bağlanır. Kapı ikisini de kabul eder ama BAŞKA hiçbir şeyi kabul etmez:
    `company_id`i hiç anmayan ya da `:cid`e bağlamayan bir metin kırmızıdır.

    İlk yazımda kapı yalnız `company_id=:cid` arıyordu ve `tax_liabilities`
    INSERT'inde KIRMIZI oldu — kapı DARDI, kod değil. Ayrım burada, adıyla
    duruyor ki sonraki okuyucu INSERT'i "yüklemsiz" diye MUAF sanmasın.
    """
    agac = ast.parse(MOTOR.read_text(encoding="utf-8"))
    metinler = [
        dugum.args[0].value
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.Call)
        and isinstance(dugum.func, ast.Name)
        and dugum.func.id == "text"
        and dugum.args
        and isinstance(dugum.args[0], ast.Constant)
    ]
    assert metinler, "motorda hiç text() yok — kapı boşa düşüyor"
    for sql in metinler:
        if sql.lstrip().upper().startswith("INSERT"):
            assert "company_id" in sql and ":cid" in sql, sql
        else:
            assert "company_id=:cid" in sql, sql


def test_D1_kancalarinin_ikisi_de_CAS_ISLEMININ_ICINDE() -> None:
    """`makbuz_kesildi` commit'ten ÖNCE, `iptal_engelleri` de öyle.

    Kanca commit'ten SONRA çağrılsaydı, numarası verilmiş ama stopajı
    yazılmamış bir makbuz ARA HÂL olarak var olabilirdi; iptal engeli
    commit'ten sonra çalışsaydı iptal geri ALINAMAZDI.
    """
    kaynak = D1_ROUTER.read_text(encoding="utf-8")
    for kanca in ("makbuz_kesildi(", "iptal_engelleri("):
        yer = kaynak.index(kanca)
        commit = kaynak.index("db.commit()", yer)
        assert "db.commit()" not in kaynak[yer:commit], kanca
        assert commit - yer < 400, (
            f"{kanca} ile commit arası büyümüş; kancanın aynı işlemde "
            "kaldığı artık okunamıyor."
        )


def test_gocun_kapali_kumeleri_kodla_AYNI() -> None:
    """Göçün `kind` CHECK'i ile motorun tür listesi BİREBİR aynı iki değer.

    İkisi ayrışsaydı kod, şemanın reddedeceği bir satır yazmayı deneyip
    500 verirdi.
    """
    from app.avans_engine import VERGI_TURLERI
    from app.avans_schemas import TAX_LIABILITY_KINDS

    goc = GOC.read_text(encoding="utf-8")
    assert "kind IN ('withholding', 'social_security')" in goc
    assert {k for k, _ in VERGI_TURLERI} == {"withholding", "social_security"}
    assert TAX_LIABILITY_KINDS == {"withholding", "social_security"}
    assert [s for _, s in VERGI_TURLERI] == [
        "withholding_total",
        "social_security_total",
    ]


def test_gocte_mahsup_sutunu_CHECKLI_ve_downgrade_KISITI_ONCE_dusuruyor() -> None:
    """`advance_applied_total >= 0` şemada; downgrade kısıtı ÖNCE düşürüyor.

    İkisi de ÖLÇÜLEREK bulundu: CHECK'in atlanma gerekçesi ("batch yeniden
    kurulumu kısmi indeksi kaybeder") ÖLÇÜLÜNCE ÇIKMADI; ve yalnız
    `drop_column` çağıran bir downgrade SQLite'ta
    `no such column: advance_applied_total` veriyordu.
    """
    goc = GOC.read_text(encoding="utf-8")
    assert "ck_producer_receipts_advance_applied_nonneg" in goc
    assert "create_check_constraint(" in goc
    dusur = goc.index("drop_constraint(CK_MAHSUP")
    sutun = goc.index("drop_column(MAHSUP_SUTUNU")
    assert dusur < sutun, (
        "downgrade sütunu kısıttan ÖNCE düşürüyor; batch yansıtılan CHECK'i "
        "taşır ve göç kırılır."
    )


# ---------------------------------------------------------------------------
# DAVRANIŞ — gerçek şema, gerçek uçlar (alt süreç, kendi veritabanı).
# ---------------------------------------------------------------------------


def run_d2_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "D2 AVANS TESCIL TAMAM" in completed.stdout, completed.stdout


def test_d2_avans_tescil_sqlite(tmp_path: Path) -> None:
    run_d2_smoke(f"sqlite:///{(tmp_path / 'd2.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.db import SessionLocal
from app.main import app

ADMIN_PW = 'Avans!123'
URUN_ID = 6101


def admin_headers(client):
    """Bootstrap sifresi ile ADMIN_PW'yi SIRAYLA dener.

    Sabit 'admin123' yazmak, sifre bir kez degistikten sonra ters sirada
    duserdi; D1 ikizinde olculmus kusur, cozumu ORADAN aliniyor.
    """
    for candidate in ('admin123', ADMIN_PW):
        login = client.post('/api/auth/login',
                            json={'username':'admin','password':candidate})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    if candidate != ADMIN_PW:
        ch = client.post('/api/auth/change-password', headers=h,
                         json={'current_password':candidate,'new_password':ADMIN_PW})
        assert ch.status_code == 200, ch.text
        h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h, int(body['companies'][0]['id'])


def urun_yaz(db, urun_id, cid, taban):
    """`active` BOOLEAN olarak baglaniyor, 1 olarak DEGIL: PostgreSQL
    boolean sutununa tamsayi kabul etmez (kantar ikizinde olculmus kusur)."""
    db.execute(_sql(
        "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
        "stock,unit,price_per,active,critical_stock,minimum_stock,company_id,"
        "base_unit) VALUES (:i,:n,0,0,0,0,'KG',1,:a,0,0,:c,:b)"),
        {'i':urun_id,'n':'D2 urunu %d' % urun_id,'a':True,'c':cid,'b':taban})


def makbuz_kur(client, h, supplier_id, miktar, fiyat, stopaj, sgk):
    r = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id': supplier_id,
        'items': [{
            'product_id': URUN_ID, 'entered_quantity': miktar,
            'entered_unit': 'KG', 'unit_price': fiyat,
            'withholding_rate': stopaj, 'social_security_rate': sgk,
        }]})
    assert r.status_code == 201, r.text
    return r.json()['id']


def say(db, sql, **kw):
    return int(db.execute(_sql(sql), kw).scalar_one())


with TestClient(app) as client:
    h, cid = admin_headers(client)

    with SessionLocal() as db:
        urun_yaz(db, URUN_ID, cid, 'KG')
        db.execute(_sql(
            "INSERT INTO companies(name,is_active,created_at)"
            " VALUES(:n,:a,:t)"),
            {'n':'D2 Yabanci AS','a':True,'t':'2026-01-01T00:00:00+00:00'})
        yabanci_cid = int(db.execute(_sql(
            "SELECT id FROM companies WHERE name='D2 Yabanci AS'")).scalar_one())
        assert yabanci_cid != cid
        for c_, ad in ((cid,'D2 Ciftci'), (yabanci_cid,'Yabanci Ciftci')):
            db.execute(_sql(
                "INSERT INTO suppliers(name,company_id,opening_balance,"
                "is_active) VALUES(:n,:c,0,:a)"),
                {'n':ad,'c':c_,'a':True})
        ciftci = int(db.execute(_sql(
            "SELECT id FROM suppliers WHERE company_id=:c AND name='D2 Ciftci'"),
            {'c':cid}).scalar_one())
        yabanci_ciftci = int(db.execute(_sql(
            "SELECT id FROM suppliers WHERE company_id=:c"),
            {'c':yabanci_cid}).scalar_one())
        db.commit()

    # === SENARYO 1: avans kasadan CIKAR ================================
    with SessionLocal() as db:
        odeme_once = say(db, "SELECT COUNT(*) FROM payments WHERE company_id=:c", c=cid)
        finans_once = say(db, "SELECT COUNT(*) FROM finance_transactions WHERE company_id=:c", c=cid)

    a1 = client.post('/api/suppliers/%d/advances' % ciftci, headers=h, json={
        'amount':'100.00','payment_method':'cash','payment_date':'2026-09-01',
        'note':'ilk avans'})
    assert a1.status_code == 201, a1.text
    avans1 = a1.json()
    assert avans1['amount'] == '100.00', avans1
    assert avans1['remaining_amount'] == '100.00', avans1
    assert avans1['receipt_id'] is None, avans1

    with SessionLocal() as db:
        assert say(db, "SELECT COUNT(*) FROM payments WHERE company_id=:c", c=cid) == odeme_once + 1
        assert say(db, "SELECT COUNT(*) FROM finance_transactions WHERE company_id=:c", c=cid) == finans_once + 1
        # CIFT YONLU BAG: reference_id yer tutucu DEGIL, avansin GERCEK kimligi.
        bag = db.execute(_sql(
            "SELECT reference_type,reference_id,entity_type,entity_id "
            "FROM payments WHERE company_id=:c AND id=:p"),
            {'c':cid,'p':avans1['payment_id']}).mappings().one()
        assert bag['reference_type'] == 'supplier_advance', dict(bag)
        assert int(bag['reference_id']) == avans1['id'], dict(bag)
        assert bag['entity_type'] == 'supplier', dict(bag)
        assert int(bag['entity_id']) == ciftci, dict(bag)
        yon = db.execute(_sql(
            "SELECT direction FROM finance_transactions WHERE company_id=:c "
            "AND reference_type='payment' AND reference_id=:p"),
            {'c':cid,'p':avans1['payment_id']}).scalar_one()
        assert yon == 'out', yon

    # GENEL ODEME UCU BU SATIRI SILEMEZ (mevcut davranis, D2 ona yaslaniyor).
    sil = client.delete('/api/payments/%d' % avans1['payment_id'], headers=h)
    assert sil.status_code == 409, sil.text

    # === SENARYO 2: FIFO — iki avans, en eski once =====================
    a2 = client.post('/api/suppliers/%d/advances' % ciftci, headers=h, json={
        'amount':'200.00','payment_method':'cash','payment_date':'2026-09-02'})
    assert a2.status_code == 201, a2.text
    avans2 = a2.json()

    liste = client.get('/api/suppliers/%d/advances?open_only=true' % ciftci, headers=h)
    assert liste.status_code == 200, liste.text
    acik = liste.json()['items']
    assert [x['id'] for x in acik] == [avans1['id'], avans2['id']], acik

    # 10 KG * 25.00 = 250 brut; stopaj %4 = 10.00; sgk %2 = 5.00; net = 235.00
    m1 = makbuz_kur(client, h, ciftci, '10', '25.00', '4', '2')
    kes = client.post('/api/producer-receipts/%d/issue' % m1, headers=h)
    assert kes.status_code == 200, kes.text
    gov = kes.json()
    assert gov['net_payable'] == '235.00', gov
    assert gov['advance_applied_total'] == '235.00', gov
    assert gov['cash_due'] == '0.00', gov

    with SessionLocal() as db:
        s1 = db.execute(_sql(
            "SELECT remaining_amount,receipt_id,applied_at FROM "
            "supplier_advances WHERE company_id=:c AND id=:a"),
            {'c':cid,'a':avans1['id']}).mappings().one()
        assert Decimal(str(s1['remaining_amount'])) == Decimal('0'), dict(s1)
        assert int(s1['receipt_id']) == m1, dict(s1)
        assert s1['applied_at'] is not None, dict(s1)
        # KISMI: avans2 ACIK kalir ve receipt_id NULL DURUR (bilinen sinir).
        s2 = db.execute(_sql(
            "SELECT remaining_amount,receipt_id FROM supplier_advances "
            "WHERE company_id=:c AND id=:a"),
            {'c':cid,'a':avans2['id']}).mappings().one()
        assert Decimal(str(s2['remaining_amount'])) == Decimal('65'), dict(s2)
        assert s2['receipt_id'] is None, dict(s2)

    # === SENARYO 3: issue IKI yukumluluk yazdi ==========================
    with SessionLocal() as db:
        yuk = db.execute(_sql(
            "SELECT kind,amount,due_period,settled_at FROM tax_liabilities "
            "WHERE company_id=:c AND receipt_id=:r ORDER BY kind"),
            {'c':cid,'r':m1}).mappings().all()
        assert len(yuk) == 2, [dict(x) for x in yuk]
        by = {x['kind']: x for x in yuk}
        assert Decimal(str(by['social_security']['amount'])) == Decimal('5'), [dict(x) for x in yuk]
        assert Decimal(str(by['withholding']['amount'])) == Decimal('10'), [dict(x) for x in yuk]
        for x in yuk:
            assert x['settled_at'] is None, dict(x)
            assert len(x['due_period']) == 7 and x['due_period'][4] == '-', dict(x)

    defter = client.get('/api/tax-liabilities', headers=h)
    assert defter.status_code == 200, defter.text
    assert len(defter.json()['items']) == 2, defter.text
    donem = defter.json()['items'][0]['due_period']
    suzgec = client.get('/api/tax-liabilities?period=%s' % donem, headers=h)
    assert suzgec.status_code == 200 and len(suzgec.json()['items']) == 2, suzgec.text
    bos = client.get('/api/tax-liabilities?period=1999-01', headers=h)
    assert bos.status_code == 200 and bos.json()['items'] == [], bos.text
    kotu = client.get('/api/tax-liabilities?period=ABC', headers=h)
    assert kotu.status_code == 422, kotu.text

    # === SENARYO 4: SIFIR kesintili makbuz yukumluluk YAZMAZ ===========
    m0 = makbuz_kur(client, h, ciftci, '1', '10.00', '0', '0')
    k0 = client.post('/api/producer-receipts/%d/issue' % m0, headers=h)
    assert k0.status_code == 200, k0.text
    with SessionLocal() as db:
        assert say(db, "SELECT COUNT(*) FROM tax_liabilities WHERE company_id=:c "
                       "AND receipt_id=:r", c=cid, r=m0) == 0
    # KESINTI YOK ama AVANS YINE MAHSUP EDILIR: mahsup net odenecege bakar,
    # kesintiye DEGIL. avans2 65 -> 55. (Ilk yazimda bu atlanmisti ve
    # senaryo 5'in aritmetigi tutmadi; kusur TESTTEydi, kodda degil.)
    assert k0.json()['net_payable'] == '10.00', k0.text
    assert k0.json()['advance_applied_total'] == '10.00', k0.text
    assert k0.json()['cash_due'] == '0.00', k0.text
    with SessionLocal() as db:
        kalan = db.execute(_sql(
            "SELECT remaining_amount FROM supplier_advances "
            "WHERE company_id=:c AND id=:a"),
            {'c':cid,'a':avans2['id']}).scalar_one()
        assert Decimal(str(kalan)) == Decimal('55'), kalan

    # === SENARYO 5: odeme <= cash_due, asma 422 ========================
    # avans2'de 55 kalmisti; 4 KG * 25 = 100 brut, net 94.00 -> 55 mahsup, 39 nakit
    m2 = makbuz_kur(client, h, ciftci, '4', '25.00', '4', '2')
    k2 = client.post('/api/producer-receipts/%d/issue' % m2, headers=h)
    assert k2.status_code == 200, k2.text
    g2 = k2.json()
    assert g2['net_payable'] == '94.00', g2
    assert g2['advance_applied_total'] == '55.00', g2
    assert g2['cash_due'] == '39.00', g2

    asir = client.post('/api/producer-receipts/%d/pay' % m2, headers=h, json={
        'amount':'39.01','payment_method':'cash','payment_date':'2026-09-06'})
    assert asir.status_code == 422, asir.text
    assert asir.json()['detail']['code'] == 'MAKBUZ_ODEME_ASIYOR', asir.text

    ode = client.post('/api/producer-receipts/%d/pay' % m2, headers=h, json={
        'amount':'20.00','payment_method':'cash','payment_date':'2026-09-06'})
    assert ode.status_code == 200, ode.text
    assert ode.json()['remaining'] == '19.00', ode.text

    asir2 = client.post('/api/producer-receipts/%d/pay' % m2, headers=h, json={
        'amount':'19.01','payment_method':'cash','payment_date':'2026-09-06'})
    assert asir2.status_code == 422, asir2.text

    # TASLAGA odeme YAPILAMAZ.
    taslak = makbuz_kur(client, h, ciftci, '1', '10.00', '0', '0')
    t_ode = client.post('/api/producer-receipts/%d/pay' % taslak, headers=h, json={
        'amount':'1.00','payment_method':'cash','payment_date':'2026-09-06'})
    assert t_ode.status_code == 409, t_ode.text

    # === SENARYO 6: borsa tescili BIR kez ==============================
    t1 = client.post('/api/producer-receipts/%d/exchange-registration' % m1,
                     headers=h, json={'registration_no':'BRS-1',
                     'exchange_name':'Ticaret Borsasi',
                     'registered_on':'2026-09-05','fee_amount':'12.50'})
    assert t1.status_code == 201, t1.text
    assert t1.json()['fee_amount'] == '12.50', t1.text
    t2 = client.post('/api/producer-receipts/%d/exchange-registration' % m1,
                     headers=h, json={'registration_no':'BRS-2',
                     'exchange_name':'Baska Borsa',
                     'registered_on':'2026-09-05','fee_amount':'0'})
    assert t2.status_code == 409, t2.text
    assert t2.json()['detail']['code'] == 'MAKBUZ_ZATEN_TESCILLI', t2.text
    gor = client.get('/api/producer-receipts/%d' % m1, headers=h)
    assert gor.status_code == 200, gor.text
    assert gor.json()['exchange_registration'] is not None, gor.text

    # === SENARYO 7: IPTAL ENGELLERI ====================================
    ip = client.post('/api/producer-receipts/%d/cancel' % m2, headers=h)
    assert ip.status_code == 409, ip.text
    assert ip.json()['detail']['code'] == 'MAKBUZ_ODENMIS', ip.text

    ip2 = client.post('/api/producer-receipts/%d/cancel' % m1, headers=h)
    assert ip2.status_code == 409, ip2.text
    assert ip2.json()['detail']['code'] in ('MAKBUZ_TESCILLI','MAKBUZ_AVANS_MAHSUPLU'), ip2.text

    # TEMIZ makbuz iptal EDILIR ve kapanmamis yukumlulukler SILINIR.
    m3 = makbuz_kur(client, h, ciftci, '2', '50.00', '4', '2')
    k3 = client.post('/api/producer-receipts/%d/issue' % m3, headers=h)
    assert k3.status_code == 200, k3.text
    assert k3.json()['advance_applied_total'] == '0.00', k3.text
    with SessionLocal() as db:
        assert say(db, "SELECT COUNT(*) FROM tax_liabilities WHERE company_id=:c "
                       "AND receipt_id=:r", c=cid, r=m3) == 2
    ip3 = client.post('/api/producer-receipts/%d/cancel' % m3, headers=h)
    assert ip3.status_code == 200, ip3.text
    assert ip3.json()['status'] == 'cancelled', ip3.text
    with SessionLocal() as db:
        assert say(db, "SELECT COUNT(*) FROM tax_liabilities WHERE company_id=:c "
                       "AND receipt_id=:r", c=cid, r=m3) == 0
        # NUMARA DURUR: iptal seride yerini korur (D1 kurali BOZULMADI).
        no = db.execute(_sql("SELECT receipt_no FROM producer_receipts "
                             "WHERE company_id=:c AND id=:r"),
                        {'c':cid,'r':m3}).scalar_one()
        assert no, no

    # ENGEL TAKILDIGINDA MAKBUZ 'issued' KALIR (rollback calisti).
    with SessionLocal() as db:
        durum = db.execute(_sql("SELECT status FROM producer_receipts "
                                "WHERE company_id=:c AND id=:r"),
                           {'c':cid,'r':m2}).scalar_one()
        assert durum == 'issued', durum
        # Reddedilen iptal yukumlulukleri SILMEDI.
        assert say(db, "SELECT COUNT(*) FROM tax_liabilities WHERE company_id=:c "
                       "AND receipt_id=:r", c=cid, r=m2) == 2

    # === SENARYO 8: KIRACI SINIRI ======================================
    yb = client.post('/api/suppliers/%d/advances' % yabanci_ciftci, headers=h,
                     json={'amount':'1.00','payment_method':'cash',
                           'payment_date':'2026-09-06'})
    assert yb.status_code == 404, yb.text

    with SessionLocal() as db:
        db.execute(_sql(
            "INSERT INTO producer_receipts(company_id,supplier_id,gross_amount,"
            "withholding_total,social_security_total,net_payable,status,"
            "created_at,updated_at,advance_applied_total) VALUES(:c,:s,100,0,0,"
            "100,'draft',:t,:t,0)"),
            {'c':yabanci_cid,'s':yabanci_ciftci,'t':'2026-01-01T00:00:00+00:00'})
        yabanci_makbuz = int(db.execute(_sql(
            "SELECT id FROM producer_receipts WHERE company_id=:c"),
            {'c':yabanci_cid}).scalar_one())
        db.commit()
    for yol in ('pay','exchange-registration'):
        govde = ({'amount':'1.00','payment_method':'cash','payment_date':'2026-09-06'}
                 if yol=='pay' else
                 {'registration_no':'X','exchange_name':'Y',
                  'registered_on':'2026-09-06','fee_amount':'0'})
        r = client.post('/api/producer-receipts/%d/%s' % (yabanci_makbuz, yol),
                        headers=h, json=govde)
        assert r.status_code == 404, (yol, r.text)

    with SessionLocal() as db:
        db.execute(_sql(
            "INSERT INTO tax_liabilities(company_id,kind,receipt_id,amount,"
            "due_period,created_at) VALUES(:c,'withholding',:r,1,'2026-01',:t)"),
            {'c':yabanci_cid,'r':yabanci_makbuz,'t':'2026-01-01T00:00:00+00:00'})
        db.commit()
    hepsi = client.get('/api/tax-liabilities', headers=h).json()['items']
    assert all(x['receipt_id'] != yabanci_makbuz for x in hepsi), hepsi

    yl = client.get('/api/suppliers/%d/advances' % yabanci_ciftci, headers=h)
    assert yl.status_code == 404, yl.text

    # === SENARYO 9: TEDARIKCI BAKIYESI — makbuz BORCTUR =================
    # Kural: bakiye = acilis + SUM(alimlar) + SUM(makbuz net_payable)
    #                 - SUM(odemeler)
    # Kanit akisi: avans 100 -> makbuz net 300 (mahsup 100, cash_due 200)
    # -> odeme 200  =>  bakiye 0. Borc terimi OLMASAYDI ayni akis -300
    # okunurdu (yalnizca odemeler dusulur, karsiliginda hicbir borc yok).
    with SessionLocal() as db:
        db.execute(_sql(
            "INSERT INTO suppliers(name,company_id,opening_balance,is_active)"
            " VALUES('Bakiye Ciftcisi',:c,0,:a)"), {'c':cid,'a':True})
        bakiye_ciftci = int(db.execute(_sql(
            "SELECT id FROM suppliers WHERE company_id=:c AND "
            "name='Bakiye Ciftcisi'"), {'c':cid}).scalar_one())
        db.commit()

    pano_once = Decimal(str(client.get('/api/dashboard', headers=h)
                            .json()['supplier_payables']))

    av = client.post('/api/suppliers/%d/advances' % bakiye_ciftci, headers=h,
                     json={'amount':'100.00','payment_method':'cash',
                           'payment_date':'2026-09-10'})
    assert av.status_code == 201, av.text

    # 16 KG * 25.00 = 400 brut; stopaj %20 = 80.00; sgk %5 = 20.00
    # -> net_payable = 300.00.  BRUT ile NET burada AYRISIYOR: borc terimi
    # yanlislikla brutu toplasaydi bakiye 100 fazla cikardi.
    mb = makbuz_kur(client, h, bakiye_ciftci, '16', '25.00', '20', '5')
    kb = client.post('/api/producer-receipts/%d/issue' % mb, headers=h)
    assert kb.status_code == 200, kb.text
    gb = kb.json()
    assert gb['net_payable'] == '300.00', gb
    assert gb['gross_amount'] == '400.00', gb
    assert gb['advance_applied_total'] == '100.00', gb
    assert gb['cash_due'] == '200.00', gb

    pb = client.post('/api/producer-receipts/%d/pay' % mb, headers=h, json={
        'amount':'200.00','payment_method':'cash','payment_date':'2026-09-11'})
    assert pb.status_code == 200, pb.text
    assert pb.json()['remaining'] == '0.00', pb.text

    # (a) finance.py GET /suppliers -> current_balance
    liste = client.get('/api/suppliers?q=Bakiye', headers=h)
    assert liste.status_code == 200, liste.text
    satirlar = [x for x in liste.json() if x['id'] == bakiye_ciftci]
    assert len(satirlar) == 1, liste.text
    assert Decimal(str(satirlar[0]['current_balance'])) == Decimal('0'), (
        'finance.py bakiyesi 0 degil: %s (borc terimi dustu mu?)'
        % satirlar[0]['current_balance'])

    # (b) statement.py build_statement -> kapanis
    # PENCERE ACIKCA VERILIYOR: varsayilan donem AY BASI -> BUGUN'dur ve
    # yukaridaki odemeler ileri tarihli oldugu icin disarida kalirdi;
    # o zaman ekstre makbuzu (bugun kesildi) sayar, odemeleri saymaz ve
    # kapanis 300 okunurdu. Kusur TESTTEydi: pencere test tarihine gore
    # KAYIYORDU.
    ekstre = client.get(
        '/api/suppliers/%d/statement?date_from=2026-01-01&date_to=2026-12-31'
        % bakiye_ciftci, headers=h)
    assert ekstre.status_code == 200, ekstre.text
    e = ekstre.json()
    assert Decimal(str(e['closing_balance'])) == Decimal('0'), (
        'statement.py kapanisi 0 degil: %s' % e['closing_balance'])
    # SATIRLAR TOPLAMLA TUTUYOR: makbuz ekstrede GORUNUYOR ve BORC tarafinda.
    makbuz_satirlari = [x for x in e['lines']
                        if x['kind'] == 'producer_receipt']
    assert len(makbuz_satirlari) == 1, e['lines']
    assert Decimal(str(makbuz_satirlari[0]['debit'])) == Decimal('300'),         makbuz_satirlari
    assert Decimal(str(makbuz_satirlari[0]['credit'])) == Decimal('0'),         makbuz_satirlari

    # (c) dashboard.py supplier_payables -> akis SIFIR toplamli
    pano_sonra = Decimal(str(client.get('/api/dashboard', headers=h)
                             .json()['supplier_payables']))
    assert pano_sonra - pano_once == Decimal('0'), (
        'dashboard.py supplier_payables deltasi %s; +300 borc ve -300 odeme '
        'birbirini goturmeliydi' % (pano_sonra - pano_once))

    # IPTAL BORCU KALDIRIR: cancelled makbuz bakiyeye GIRMEZ.
    mi = makbuz_kur(client, h, bakiye_ciftci, '4', '25.00', '0', '0')
    ki = client.post('/api/producer-receipts/%d/issue' % mi, headers=h)
    assert ki.status_code == 200, ki.text
    ara = client.get('/api/suppliers?q=Bakiye', headers=h).json()
    ara_bakiye = Decimal(str([x for x in ara
                              if x['id'] == bakiye_ciftci][0]['current_balance']))
    assert ara_bakiye == Decimal('100'), ara_bakiye
    ii = client.post('/api/producer-receipts/%d/cancel' % mi, headers=h)
    assert ii.status_code == 200, ii.text
    son = client.get('/api/suppliers?q=Bakiye', headers=h).json()
    son_bakiye = Decimal(str([x for x in son
                              if x['id'] == bakiye_ciftci][0]['current_balance']))
    assert son_bakiye == Decimal('0'), (
        'iptal edilen makbuz bakiyeden DUSMEDI: %s' % son_bakiye)

print('D2 AVANS TESCIL TAMAM')
'''

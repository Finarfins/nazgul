"""SEC-3 — `read` daraltmasinin CALISMA ZAMANI kaniti.

Konu: `docs/SEC-3-olcum-2026-09-09.md` (FAZ 1 olcumu) ve bu PR'in `auth.py`
degisikligi.

--- BU DOSYA NIYE VAR -------------------------------------------------------

FAZ 1 raporunun §7'si kendi sinirini yaziyor: "hicbir rol icin GERCEK HTTP
istegi atilmadi; tum izin sonuclari `required_permission` / handler kapisi
OKUNARAK ve SIMULE EDILEREK cikarildi." Diger uc kapi
(`test_route_get_permission_inventory.py`, `test_route_security_contracts.py`,
`test_authorization_population_reconciliation.py`) o simulasyonun DEVAMIDIR:
uculu de `required_permission`i DOGRUDAN cagirir, yani middleware'in o degeri
gercekten uyguladigini, jetonun rolunun gercekten cozuldugunu ve 403'un
gercekten dondugunu HIC OLCMEZ. `required_permission` dogru cevabi verip
middleware onu kullanmasa uc kapi da YESIL kalirdi.

Bu dosya o bosluga tek bir sey koyuyor: TOHUMLANMIS bir uygulamaya, BES ROLUN
BESIYLE de GERCEKTEN giris yapip, 19 yolun her birine GERCEK bir GET atmak ve
donen durum kodunu 5x19'luk bir matriste dondurmak.

--- MATRISIN OKUNMASI -------------------------------------------------------

Her hucre iki degerden biridir:

* **403** — rol, yolun yeni iznini TASIMIYOR. Bu iddia TOHUMLAMADAN BAGIMSIZDIR:
  yetki kapisi middleware'dedir ve handler'a HIC girilmez, yani kaynak var da
  olsa yok da olsa sonuc 403'tur. SEC-3'un GUVENLIK IDDIASI budur.
* **`IZINLI_DURUM[yol]`** — rol izni TASIYOR ve istek kapidan GECIYOR. Beklenen
  deger ELLE YAZILMADI, `admin` (izni `*`) ile OLCULDU ve buraya CIVILENDI.

TOHUMLAMA GERCEK, 19/19. Matrisin izinli hucrelerinin hepsi 200'dur ve bu
OLCULDU, umut edilmedi: musteri, tedarikci, depo, urun, ALIS belgesi, SATIS
belgesi, tahsilat, makine, is emri ve ONDAN URETILEN GERCEK FATURA
yaratiliyor. `/api/payment-allocations/charges/{id}`in de 200 donmesi bu
zincirin yan urunudur — is emri faturalandiginda bir alacak kalemi dogar.
Bu onemli: 200'u 404'ten ayirmak, kapinin izinli rolu yalnizca GECIRDIGINI
degil, handler'in GERCEKTEN calistigini da gosterir. Bir sonraki katkici
tohumlamayi bozarsa `test_izinli_durumlar_admin_ile_olculdu` bunu ADIYLA
soyler ve matris sessizce 404'lerin uzerinde kosmaz.

--- UC MUTASYON -------------------------------------------------------------

Matrisin "bugunu tekrar eden" bir fotograf degil, YUK TASIYAN bir kapi
oldugunu gostermek icin `auth.py`ye uygulanabilecek uc somut bozulma burada
CANLANDIRILIYOR (`app.main.required_permission` monkeypatch'lenerek — `main.py`
adi kendi ad alanina import ediyor ve her istekte oradan cagiriyor) ve her
mutantin HANGI matris hucresini oldurdugu adiyla yaziliyor. Uc mutasyon uc
AYRI kirilma bicimini kapsiyor: kuralin SILINMESI, kuralin YERI ve kuralin
KAPSAMI.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="sec3-read-daraltma-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "sec3.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "Sec3Yonetim!2026"
ROL_PAROLASI = "Sec3Rolleri!2026"

#: Sevk edilmis roller (`admin` HARIC — o `*` tasir ve matriste ORAKUL olarak
#: kullaniliyor, satir olarak DEGIL).
ROLLER = ("yonetici", "muhasebe", "satis", "depo", "rapor")

#: SEC-3'un `read`ten cikardigi 19 YOL ve yeni izinleri. Degerler
#: `test_route_get_permission_inventory.py`nin envanteriyle AYNI olmak
#: zorunda; asagidaki `test_matris_envanterle_ayni_izinleri_soyluyor` bunu
#: uc uce dogruluyor, yani iki dosya birbirinden BAGIMSIZ kayamaz.
YOLLAR: dict[str, tuple[str, str]] = {
    # --- purchases (7 envanter satiri) ---
    "/api/purchases": ("purchases", "/api/purchases"),
    "/api/purchases/last-purchase-price": (
        "purchases", "/api/purchases/last-purchase-price?supplier_id=1&product_id=1",
    ),
    "/api/suppliers": ("purchases", "/api/suppliers"),
    "/api/suppliers/{id}": ("purchases", "/api/suppliers/1"),
    "/api/suppliers/{id}/documents": ("purchases", "/api/suppliers/1/documents"),
    "/api/suppliers/{id}/statement": ("purchases", "/api/suppliers/1/statement"),
    "/api/suppliers/{id}/statement.pdf": ("purchases", "/api/suppliers/1/statement.pdf"),
    # --- sales (8 envanter satiri) ---
    "/api/invoices": ("sales", "/api/invoices"),
    "/api/invoices/{id}": ("sales", "/api/invoices/1"),
    "/api/invoices/{id}/history": ("sales", "/api/invoices/1/history"),
    "/api/invoices/{id}/pdf": ("sales", "/api/invoices/1/pdf"),
    "/api/invoices/{id}/einvoice/status": ("sales", "/api/invoices/1/einvoice/status"),
    "/api/customers/{id}/statement": ("sales", "/api/customers/1/statement"),
    "/api/customers/{id}/statement.pdf": ("sales", "/api/customers/1/statement.pdf"),
    "/api/orders/last-sale-price": (
        "sales", "/api/orders/last-sale-price?customer_id=1&product_id=1",
    ),
    # --- payments (3 envanter satiri) ---
    "/api/payment-allocations/payments/{id}": (
        "payments", "/api/payment-allocations/payments/1",
    ),
    "/api/payment-allocations/orders/{id}": (
        "payments", "/api/payment-allocations/orders/1",
    ),
    "/api/payment-allocations/charges/{id}": (
        "payments", "/api/payment-allocations/charges/1",
    ),
    # --- stock (1 envanter satiri) ---
    "/api/warehouses/replenishment": ("stock", "/api/warehouses/replenishment"),
}

#: IZINLI bir rolun aldigi durum kodu. `admin` (izni `*`) ile OLCULDU, tahmin
#: EDILMEDI; `test_izinli_durumlar_admin_ile_olculdu` her kosuda yeniden
#: dogruluyor. Ondokuzunun ondokuzu da 200: tohumlama her yolun handler'ini
#: gercekten calistiracak kadar defter aciyor.
IZINLI_DURUM = {yol: 200 for yol in YOLLAR}

#: `read`te KALAN kontrol yollari. `depo` ve `rapor` BUNLARI HALA 200
#: almalidir; daralmanin gunluk is yuzeyini kesmedigi buradan olculuyor.
#: Bir sonraki katkici `auth.py`ye `/api/customers` ya da `/api/orders` oneki
#: yazarsa bu satirlar kirmizi yanar.
KONTROL_YOLLARI = (
    "/api/customers",
    "/api/orders",
    "/api/products",
    "/api/work-orders",
)


def _rolun_izinleri(rol: str) -> set[str]:
    from app.auth import ROLE_PERMISSIONS

    return ROLE_PERMISSIONS[rol]


def _beklenen(rol: str, yol: str) -> int:
    izin, _ = YOLLAR[yol]
    return IZINLI_DURUM[yol] if izin in _rolun_izinleri(rol) else 403


# ---------------------------------------------------------------------------
# TOHUMLAMA — uygulama GERCEKTEN ayaga kalkiyor, veri GERCEKTEN yaziliyor.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def istemci():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def admin_basliklari(istemci):
    giris = istemci.post(
        "/api/auth/login",
        json={"username": "admin", "password": ACILIS_PAROLASI},
    )
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    basliklar = {
        "Authorization": "Bearer " + govde["access_token"],
        "X-Company-ID": str(govde["companies"][0]["id"]),
    }
    degis = istemci.post(
        "/api/auth/change-password",
        headers=basliklar,
        json={
            "current_password": ACILIS_PAROLASI,
            "new_password": ADMIN_PAROLASI,
        },
    )
    assert degis.status_code == 200, degis.text
    basliklar["Authorization"] = "Bearer " + degis.json()["access_token"]
    return basliklar


@pytest.fixture(scope="module")
def tohum(istemci, admin_basliklari):
    """19 yolun 18'inin GERCEK 200 dondurebilmesi icin asgari defter.

    Kimlikler BILEREK 1'e sabitleniyor (`YOLLAR` somut `/1` yollarini tasiyor);
    her varlik olusturulduktan sonra kimligi DOGRULANIYOR, yoksa matris
    sessizce 404'lerin uzerinde kosardi.
    """
    h = admin_basliklari

    def olustur(yol: str, govde: dict, beklenen_kimlik: int | None = 1) -> dict:
        yanit = istemci.post(yol, headers=h, json=govde)
        assert yanit.status_code in (200, 201), (yol, yanit.status_code, yanit.text)
        veri = yanit.json()
        if beklenen_kimlik is not None:
            assert veri["id"] == beklenen_kimlik, (yol, veri)
        return veri

    musteri = olustur("/api/customers", {"name": "SEC3 Musteri"})
    tedarikci = olustur("/api/suppliers", {"name": "SEC3 Tedarikci"})
    # Depo kimligi 1 DEGIL: acilis zaten bir varsayilan depo yaratiyor.
    depo = olustur("/api/warehouses", {"name": "SEC3 Depo"}, beklenen_kimlik=None)
    urun = olustur("/api/products", {"name": "SEC3 Urun", "sku": "SEC3-1"})

    satir = {
        "product_id": urun["id"],
        "quantity": "2",
        "unit_price": "100",
        "discount": "0",
        "vat_rate": "0",
        "warehouse_id": depo["id"],
    }
    # ALIS belgesi: `/api/purchases`, `/api/purchases/1` ve
    # `last-purchase-price` bununla gercek veri donduruyor.
    olustur(
        "/api/purchases",
        {"entity_id": tedarikci["id"], "transaction_date": "2026-09-09",
         "items": [satir]},
    )
    # SATIS belgesi: `/api/orders/1` kontrolu ve `last-sale-price` bununla.
    olustur(
        "/api/orders",
        {"entity_id": musteri["id"], "transaction_date": "2026-09-09",
         "items": [satir]},
    )
    # Tahsilat: `/api/payment-allocations/payments/1` bununla 404 yerine 200.
    olustur(
        "/api/payments",
        {"entity_type": "customer", "entity_id": musteri["id"], "amount": "50",
         "payment_date": "2026-09-09", "payment_method": "cash"},
    )
    makine = olustur(
        "/api/machines",
        {"brand": "SEC3", "model": "M1", "customer_id": musteri["id"]},
    )
    # GERCEK FATURA: bes fatura yolunun 200 donmesi buna bagli. Is emri
    # COMPLETED'a getirilmeden `POST /api/invoices/generate` 409 verir.
    is_emri = olustur(
        "/api/work-orders",
        {"customer_id": musteri["id"], "machine_id": makine["id"],
         "technician_id": 1, "title": "SEC3 Is Emri", "description": "SEC3",
         "priority": "NORMAL"},
    )
    for durum in ("IN_PROGRESS", "COMPLETED"):
        gecis = istemci.patch(
            f"/api/work-orders/{is_emri['id']}/status",
            headers=h,
            json={"status": durum},
        )
        assert gecis.status_code == 200, (durum, gecis.text)
    fatura = istemci.post(
        "/api/invoices/generate", headers=h, json={"work_order_id": is_emri["id"]}
    )
    assert fatura.status_code in (200, 201), fatura.text
    assert fatura.json()["id"] == 1, fatura.json()
    return {"company_id": int(h["X-Company-ID"])}


@pytest.fixture(scope="module")
def rol_basliklari(istemci, admin_basliklari, tohum):
    """Bes rol icin GERCEK kullanici + GERCEK giris.

    Kullanicilar dogrudan SQL ile aciliyor: kullanici yonetimi ucu `users`
    iznine bagli ve bu dosyanin konusu o degil. `must_change_password` FALSE
    yaziliyor, yoksa her rol ilk istekte parola degistirme kapisina takilir ve
    matris yetkiyi degil o kapiyi olcerdi.
    """
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    company_id = tohum["company_id"]
    simdi = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for rol in ROLLER:
            uid = db.execute(
                text(
                    "INSERT INTO app_users(username,email,display_name,"
                    "password_hash,role,is_active,must_change_password,"
                    "email_verified,created_at)"
                    " VALUES(:k,:e,:d,:p,:r,:a,:m,:v,:t) RETURNING id"
                ),
                {
                    "k": f"sec3_{rol}", "e": f"sec3_{rol}@ornek.test",
                    "d": f"SEC3 {rol}", "p": hash_password(ROL_PAROLASI),
                    "r": rol, "a": True, "m": False, "v": True, "t": simdi,
                },
            ).scalar_one()
            db.execute(
                text(
                    "INSERT INTO user_company_memberships"
                    "(user_id,company_id,is_default,created_at)"
                    " VALUES(:u,:c,true,:t)"
                ),
                {"u": uid, "c": company_id, "t": simdi},
            )
        db.commit()

    basliklar = {}
    for rol in ROLLER:
        giris = istemci.post(
            "/api/auth/login",
            json={"username": f"sec3_{rol}", "password": ROL_PAROLASI},
        )
        assert giris.status_code == 200, (rol, giris.text)
        basliklar[rol] = {
            "Authorization": "Bearer " + giris.json()["access_token"],
            "X-Company-ID": str(company_id),
        }
    return basliklar


# ---------------------------------------------------------------------------
# KURULUMUN KENDISI DOGRULANIYOR — bos bir tarama matrisi sahte biçimde yesil
# yapardi.
# ---------------------------------------------------------------------------


def test_matris_gercekten_bes_role_ve_ondokuz_yola_bakiyor() -> None:
    assert len(ROLLER) == 5
    assert len(YOLLAR) == 19
    izinler = [izin for izin, _ in YOLLAR.values()]
    assert izinler.count("purchases") == 7
    assert izinler.count("sales") == 8
    assert izinler.count("payments") == 3
    assert izinler.count("stock") == 1


def test_matris_envanterle_ayni_izinleri_soyluyor() -> None:
    """19 satirin izni `EXPECTED_GET_PERMISSIONS` ile UC UCA esit olmali.

    Iki dosya birbirinden BAGIMSIZ kayamaz: envanter degisip burasi kalirsa
    (ya da tersi) bu test kirmizi yanar ve hangi yolun ayrildigini yazar.
    Envanter SABLON anahtar tuttugu icin `{id}` -> gercek parametre adiyla
    esleniyor.
    """
    from tests.test_route_get_permission_inventory import EXPECTED_GET_PERMISSIONS

    sablon = {
        "/api/suppliers/{id}": "/api/suppliers/{supplier_id}",
        "/api/suppliers/{id}/documents": "/api/suppliers/{supplier_id}/documents",
        "/api/suppliers/{id}/statement": "/api/suppliers/{supplier_id}/statement",
        "/api/suppliers/{id}/statement.pdf":
            "/api/suppliers/{supplier_id}/statement.pdf",
        "/api/invoices/{id}": "/api/invoices/{invoice_id}",
        "/api/invoices/{id}/history": "/api/invoices/{invoice_id}/history",
        "/api/invoices/{id}/pdf": "/api/invoices/{invoice_id}/pdf",
        "/api/invoices/{id}/einvoice/status":
            "/api/invoices/{invoice_id}/einvoice/status",
        "/api/customers/{id}/statement": "/api/customers/{customer_id}/statement",
        "/api/customers/{id}/statement.pdf":
            "/api/customers/{customer_id}/statement.pdf",
        "/api/payment-allocations/payments/{id}":
            "/api/payment-allocations/payments/{payment_id}",
        "/api/payment-allocations/orders/{id}":
            "/api/payment-allocations/orders/{order_id}",
        "/api/payment-allocations/charges/{id}":
            "/api/payment-allocations/charges/{receivable_charge_id}",
    }
    ayrisan = []
    for yol, (izin, _) in YOLLAR.items():
        anahtar = ("GET", sablon.get(yol, yol))
        if EXPECTED_GET_PERMISSIONS.get(anahtar) != izin:
            ayrisan.append(f"{anahtar} envanter={EXPECTED_GET_PERMISSIONS.get(anahtar)} matris={izin}")
    assert not ayrisan, "\n  ".join(ayrisan)


def test_izinli_durumlar_admin_ile_olculdu(istemci, admin_basliklari, tohum) -> None:
    """`IZINLI_DURUM` elle uydurulmus degil; `*` tasiyan hesapla ol&#231;uluyor.

    Bu test olmasaydi biri `IZINLI_DURUM`u 403'e cevirip TUM matrisi sahte
    bicimde yesil yapabilirdi.
    """
    sapan = []
    for yol, (_, somut) in YOLLAR.items():
        yanit = istemci.get(somut, headers=admin_basliklari)
        if yanit.status_code != IZINLI_DURUM[yol]:
            sapan.append(f"{yol}: bekleniyordu {IZINLI_DURUM[yol]}, olculdu {yanit.status_code}")
        assert yanit.status_code != 403, f"`*` tasiyan hesap {yol} icin 403 aldi"
    assert not sapan, "\n  ".join(sapan)


def test_tohum_ondokuz_yolun_HEPSINDE_gercek_veri_uretti(
    istemci, admin_basliklari, tohum
) -> None:
    """Matrisin izinli tarafi 19/19 GERCEK 200 — hicbir hucre 404 uzerinde kosmuyor.

    Ayri bir test olarak duruyor cunku olctugu sey ayri: yukaridaki test
    "beklenen ile olculen tutuyor mu" diye sorar ve BEKLENENLERIN HEPSI 404
    OLSAYDI da yesil kalirdi. Bu satir beklenenin KENDISINI civiliyor.
    """
    assert set(IZINLI_DURUM.values()) == {200}, IZINLI_DURUM
    assert len(IZINLI_DURUM) == 19


# ---------------------------------------------------------------------------
# 5 x 19 MATRIS — GERCEK HTTP.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol", ROLLER)
@pytest.mark.parametrize("yol", sorted(YOLLAR))
def test_daralan_yollarin_rol_matrisi(istemci, rol_basliklari, tohum, rol, yol) -> None:
    """Bes rolun her biri, 19 yolun her birine GERCEK bir GET atiyor."""
    izin, somut = YOLLAR[yol]
    yanit = istemci.get(somut, headers=rol_basliklari[rol])
    beklenen = _beklenen(rol, yol)
    assert yanit.status_code == beklenen, (
        f"{rol} -> GET {somut}: bekleniyordu {beklenen}, olculdu "
        f"{yanit.status_code}. Yolun izni `{izin}`; rolun izinleri: "
        f"{sorted(_rolun_izinleri(rol))}"
    )


@pytest.mark.parametrize("rol", ROLLER)
@pytest.mark.parametrize("yol", KONTROL_YOLLARI)
def test_read_te_kalan_kontrol_yollari_hicbir_rolu_kaybetmedi(
    istemci, rol_basliklari, tohum, rol, yol
) -> None:
    """DARALMANIN SINIRI. `depo` ve `rapor` bunlari HALA 200 almali.

    SEC-3'un en kolay yanlisi fazla daraltmaktir: `/api/customers` ya da
    `/api/orders` bir oneke dususeydi depo ve rapor gunluk is yuzeyini
    kaybederdi ve bunu hicbir sayac gostermezdi (`EXPECTED_READ` yine duserdi,
    yani "iyi" gorunurdu). Bu satirlar o yanlisi yakalar.
    """
    yanit = istemci.get(yol, headers=rol_basliklari[rol])
    assert yanit.status_code == 200, (
        f"{rol} -> GET {yol}: `read`te KALMASI gereken bir uc {yanit.status_code} "
        "dondu; daralma gunluk is yuzeyini kesti."
    )


def test_tahsis_motoru_bayragi_read_te_KALDI(istemci, rol_basliklari, tohum) -> None:
    """`engine-state` `payments`a DUSMEDI — kural sirasi bunu garanti ediyor.

    `auth.py`de `engine-state` kurali tahsis kuralinin USTUNDE duruyor. Altina
    kaydirilsaydi onek onu da yutar ve `depo`/`rapor` "ozellik kapali" ile "hic
    tahsis yok"u ayirt edemez hale gelirdi. Bu, `/tahsis-defteri` ekraninin
    bos tabloyu YANLIS yorumlamasi demekti.
    """
    for rol in ("depo", "rapor"):
        yanit = istemci.get(
            "/api/payment-allocations/engine-state", headers=rol_basliklari[rol]
        )
        assert yanit.status_code == 200, (rol, yanit.status_code, yanit.text)
        assert set(yanit.json()) == {"enabled"}, yanit.json()


# ---------------------------------------------------------------------------
# `{kind}` — SAYAC KANITI YOK, GERCEK ISTEK KANITI VAR.
# ---------------------------------------------------------------------------


def test_kind_daralmasi_purchases_kapali_orders_ACIK(
    istemci, rol_basliklari, tohum
) -> None:
    """`GET /api/purchases/1` -> 403 ama `GET /api/orders/1` -> 200 (rapor).

    Iki istek de AYNI rotaya gidiyor: `/api/{kind}/{transaction_id}`
    (`transactions.py:1443`). Ayrilan tek sey `kind`in DEGERI, ve
    `required_permission` yol parametresinin degerini bilmez — SOMUT dizgiyi
    gorur. Kural bu yuzden somut `/api/purchases` onegine yazildi, sablona
    DEGIL.
    Ne GET envanteri ne de `_populations()` bu daralmayi GOREBILIR (birincisi
    HAM sablonu sorar, ikincisi `{kind}`i "orders"a cevirir), yani SAYACLARDA
    HICBIR IZ YOKTUR. Kanit yalnizca burada ve
    `test_route_security_contracts.py`nin `DYNAMIC_PERMISSION_CASES`indedir.
    """
    rapor = rol_basliklari["rapor"]
    alis = istemci.get("/api/purchases/1", headers=rapor)
    assert alis.status_code == 403, (
        f"rapor rolu ALIS belgesini okuyabildi ({alis.status_code}); "
        "somut `/api/purchases` kurali genel guvenli-metot kuralinin ALTINA "
        "dusmus olabilir."
    )
    satis = istemci.get("/api/orders/1", headers=rapor)
    assert satis.status_code == 200, (
        f"rapor rolu SATIS belgesini kaybetti ({satis.status_code}); daralma "
        "sablonun tamamini yutmus, oysa yalniz `purchases` kolunu kesmeliydi."
    )


def test_belge_ciktilari_kind_a_gore_ayriliyor(istemci, rol_basliklari, tohum) -> None:
    """`/api/documents/{kind}/...` HANDLER kapisinda ayriliyor, middleware'de degil.

    Bu uc SEC-3'te DEGISMEDI ve bu BILINCLI: `outputs._kind(kind)["permission"]`
    zaten `orders -> sales`, `purchases -> purchases` diyor. Burada olculen
    sey, o handler kapisinin GERCEKTEN ateslendigi: `rapor` ikisini de
    kaybediyor, `depo` alis ciktisini alabiliyor ama satis ciktisini ALAMIYOR.
    """
    rapor = istemci.get("/api/documents/purchases/1/pdf", headers=rol_basliklari["rapor"])
    assert rapor.status_code == 403, rapor.status_code
    depo_satis = istemci.get("/api/documents/orders/1/pdf", headers=rol_basliklari["depo"])
    assert depo_satis.status_code == 403, depo_satis.status_code
    depo_alis = istemci.get("/api/documents/purchases/1/pdf", headers=rol_basliklari["depo"])
    assert depo_alis.status_code != 403, depo_alis.status_code


# ---------------------------------------------------------------------------
# MUTASYONLAR — her biri BIR matris hucresini olduruyor.
# ---------------------------------------------------------------------------


def _mutasyonu_uygula(monkeypatch, bozucu) -> None:
    """`app.main`in kendi ad alanindaki cozucuyu degistirir.

    `main.py:27` adi kendi modulune import ediyor ve `:447`de her istekte
    oradan cagiriyor; yama bu yuzden `app.main`e uygulaniyor, `app.auth`a
    DEGIL — `app.auth.required_permission`i yamalamak middleware'i HIC
    etkilemezdi ve mutasyon sessizce olmezdi.
    """
    import app.main as ana

    gercek = ana.required_permission

    def bozuk(method: str, path: str) -> str:
        sonuc = bozucu(method, path)
        return gercek(method, path) if sonuc is None else sonuc

    monkeypatch.setattr(ana, "required_permission", bozuk)


def test_MUTANT_1_invoices_kurali_SILINIRSE_rapor_fatura_listesini_okur(
    istemci, rol_basliklari, tohum, monkeypatch
) -> None:
    """MUTANT: `auth.py`deki `/api/invoices` SEC-3 kurali SILINIR.

    OLEN HUCRE: `test_daralan_yollarin_rol_matrisi[/api/invoices-rapor]`
    (ve ayni yolun `depo` hucresi). Mutasyon altinda `rapor` fatura listesini
    200 ile okuyor — yani firmanin TUM faturalarinin musteri VKN'si ve adresi
    yine `read` tasiyan her role acik olurdu.
    """
    # ONCE: kural yerinde, kapi kapali.
    assert istemci.get("/api/invoices", headers=rol_basliklari["rapor"]).status_code == 403

    # SONRA: kural silinmis gibi — GET `/api/invoices*` yine `read`e duser.
    _mutasyonu_uygula(
        monkeypatch,
        lambda m, p: "read"
        if m == "GET" and p.startswith("/api/invoices") and not p.endswith("/einvoice/download")
        else None,
    )
    bozuk = istemci.get("/api/invoices", headers=rol_basliklari["rapor"])
    assert bozuk.status_code == 200, (
        "MUTANT OLMEDI: kural silindiginde bile 403 donuyor. O halde matrisin "
        "`/api/invoices` satiri bu kurali degil baska bir seyi olcuyor."
    )


def test_MUTANT_2_purchases_kurali_read_in_ALTINA_kayarsa_satis_alis_defterini_okur(
    istemci, rol_basliklari, tohum, monkeypatch
) -> None:
    """MUTANT: `/api/purchases` kurali genel guvenli-metot kuralinin ALTINA kayar.

    Bu mutant KURALIN VARLIGINI degil YERINI olcuyor ve tam da bugun `auth.py`
    dosyasinin DIBINDE duran `/api/purchases` kuralinin durumu budur: kural
    ORADA da yaziyor, ama `read` kuralinin altinda oldugu icin GET'i HIC
    gormuyor.

    OLEN HUCRE: `test_daralan_yollarin_rol_matrisi[/api/purchases-satis]`.
    `depo` hucresi OLMEZ ve bu MUTANTIN KENDISININ KANITIDIR: `depo` `read`
    de `purchases` de tasidigi icin iki dunyada da 200 alir, yani "hala 200"
    olmak kuralin calistigini KANITLAMAZ. Ayrimi yapan rol `satis`tir.
    """
    depo_once = istemci.get("/api/purchases", headers=rol_basliklari["depo"])
    satis_once = istemci.get("/api/purchases", headers=rol_basliklari["satis"])
    assert depo_once.status_code == 200, depo_once.status_code
    assert satis_once.status_code == 403, satis_once.status_code

    _mutasyonu_uygula(
        monkeypatch,
        lambda m, p: "read" if m == "GET" and p.startswith("/api/purchases") else None,
    )
    depo_sonra = istemci.get("/api/purchases", headers=rol_basliklari["depo"])
    satis_sonra = istemci.get("/api/purchases", headers=rol_basliklari["satis"])
    assert depo_sonra.status_code == 200, (
        "depo mutasyondan ETKILENMEMELIYDI; `read` ve `purchases` izinlerinin "
        "ikisini de tasiyor."
    )
    assert satis_sonra.status_code == 200, (
        "MUTANT OLMEDI: kural `read`in altina kaydirildiginda bile `satis` 403 "
        "aliyor. O halde 403'u ureten sey kuralin YERI degil baska bir sey."
    )


def test_MUTANT_3_sales_kuralindan_statement_DUSERSE_depo_musteri_ekstresini_okur(
    istemci, rol_basliklari, tohum, monkeypatch
) -> None:
    """MUTANT: musteri ekstresi kuralindan `/statement` soneki dusurulur.

    Bu mutant kuralin KAPSAMINI olcuyor: `/api/customers` onegi BILEREK
    daraltilmadi (liste ve detay `read`te kaliyor), daraltilan sey YALNIZ
    ekstredir. Sonek dusurse kural hicbir seyi yakalamaz.

    OLEN HUCRE:
    `test_daralan_yollarin_rol_matrisi[/api/customers/{id}/statement-depo]`.
    Mutasyon altinda `depo` TUM cari defteri + VKN/adres/telefon/e-posta ile
    ekstreyi 200 okuyor — ustelik ayni belgenin PDF'i handler kapisinda 403
    vermeye devam ederdi, yani sistem yine "ayni veri, iki farkli kapi"
    durumuna donerdi.
    """
    assert (
        istemci.get("/api/customers/1/statement", headers=rol_basliklari["depo"]).status_code
        == 403
    )

    _mutasyonu_uygula(
        monkeypatch,
        lambda m, p: "read"
        if m == "GET" and p.startswith("/api/customers/") and p.endswith("/statement")
        else None,
    )
    bozuk = istemci.get("/api/customers/1/statement", headers=rol_basliklari["depo"])
    assert bozuk.status_code == 200, (
        "MUTANT OLMEDI: sonek dusuruldugunde bile 403 donuyor."
    )
    # Kontrol: musteri LISTESI mutasyondan bagimsiz olarak zaten aciktir —
    # yani bu hucrenin 403'u `/api/customers` onegine degil ekstre sonekine
    # bagliydi.
    assert istemci.get("/api/customers", headers=rol_basliklari["depo"]).status_code == 200

"""H16 — `GET /api/invoices` IS EMRI SUZGECI (N+1 kapanisinin kaniti).

Konu: `app/routers/invoices.py::list_invoices` ve onun tek tuketicisi
`frontend/src/pages/WorkOrderDetail.tsx` (yinelenen fatura yolu).

--- OLCULEN EKSIK ----------------------------------------------------------

`POST /api/invoices/generate` ayni is emri icin ikinci kez cagrildiginda 409
verir ("Bu is emri icin fatura zaten olusturulmus.", `invoice_service.py`).
Istemci o noktada MEVCUT faturaya yonlendirmek ister ama ucun onu bulmasini
saglayacak bir suzgeci YOKTU: `work_order_id` ne sorgu parametresiydi ne de
liste yanitinin bir alaniydi. Arayuz bu yuzden butun listeyi SAYFA SAYFA
geziyor (`page_size=200`), sonra HER kalem icin ayrica `/invoices/{id}`
cagiriyordu. 5 000 faturali bir firmada tek bir sayfayi acmanin bedeli 25
liste + 5 000 detay istegiydi.

REDDEDILEN ALTERNATIF: 409'un `detail` metnine mevcut faturanin kimligini
gommek. O, makine tarafindan okunacak bir kimligi INSAN icin yazilmis bir hata
dizgesine karistirir; metin degistigi gun istemci sessizce bozulur.

--- BU DOSYADAKI KAPILARIN MUTASYON TABLOSU --------------------------------

Her kapi, HANGI degisikligin onu kirmizi yapacagini ADIYLA soyluyor:

  * `list_invoices`ten `work_order_id` suzgecini (`conditions.append(
    "work_order_id=:work_order_id")`) DUSURMEK
        -> `test_suzgec_YALNIZ_o_is_emrinin_faturasini_donduruyor` KIRMIZI
           (suzgec yok sayilir, IKI fatura birden doner)

  * `conditions`in ILK ogesi olan kiraci yuklemini (`"company_id=:cid"`)
    DUSURMEK  **ya da**  `params["cid"]`i baska bir firmaya baglamak
        -> `test_baska_firmanin_is_emri_kimligi_BOS_liste_donduruyor` KIRMIZI
           (B firmasinin faturasi A firmasina sizar)
        Bu, ADIYLA istenen mutasyondur ve bu turda ELLE KOSULDU; sonucu PR
        govdesinde raporlanmistir.

  * `work_order_id:int|None=Query(None,gt=0)`daki `gt=0`i kaldirmak
        -> `test_sifir_ve_negatif_kimlik_422` KIRMIZI (0 / negatif sorguya
           ulasir ve sessizce suzgecsiz/bos yanit dondurur)

  * Bilinmeyen kimlikte 404 dondurmek (bos liste yerine)
        -> `test_bilinmeyen_is_emri_kimligi_200_ve_BOS` KIRMIZI. Liste ucu bir
           KOLEKSIYONdur: eslesme yoklugu bir hata degil, bos bir kumedir.

TOHUMLAMA GERCEK: uygulama ayaga kalkiyor, IKI firma aciliyor, her birinde
GERCEK is emri COMPLETED'a getirilip GERCEK fatura uretiliyor. Kimlikler
umut edilmiyor, olusturma yanitindan OKUNUYOR.
"""
from __future__ import annotations

import os
import tempfile

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h16-fatura-suzgeci-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h16.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "H16Yonetim!2026"

#: Deponun HIC uretmedigi bir is emri kimligi. Tohumlama en fazla bir avuc
#: satir aciyor; bu sayi onlarin HEPSININ ustundedir.
YOK_OLAN_IS_EMRI = 999_999


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


def _faturali_is_emri(istemci, basliklar, ad: str) -> tuple[int, int]:
    """Bir musteri + makine + is emri acar, COMPLETED'a getirir, faturalar.

    `(is_emri_kimligi, fatura_kimligi)` doner. Kimlikler SABITLENMIYOR,
    yanitlardan okunuyor: iki firma ayni dizide id uretir ve bu testin
    iddiasi id aritmetigine DEGIL, kiraci yuklemine dayanmalidir.
    """
    musteri = istemci.post(
        "/api/customers", headers=basliklar, json={"name": ad + " Musteri"}
    )
    assert musteri.status_code in (200, 201), musteri.text
    makine = istemci.post(
        "/api/machines",
        headers=basliklar,
        json={"brand": "H16", "model": ad, "customer_id": musteri.json()["id"]},
    )
    assert makine.status_code in (200, 201), makine.text
    is_emri = istemci.post(
        "/api/work-orders",
        headers=basliklar,
        json={
            "customer_id": musteri.json()["id"],
            "machine_id": makine.json()["id"],
            "technician_id": 1,
            "title": ad + " Is Emri",
            "description": ad,
            "priority": "NORMAL",
        },
    )
    assert is_emri.status_code in (200, 201), is_emri.text
    woid = int(is_emri.json()["id"])
    for durum in ("IN_PROGRESS", "COMPLETED"):
        gecis = istemci.patch(
            "/api/work-orders/" + str(woid) + "/status",
            headers=basliklar,
            json={"status": durum},
        )
        assert gecis.status_code == 200, (durum, gecis.text)
    fatura = istemci.post(
        "/api/invoices/generate", headers=basliklar, json={"work_order_id": woid}
    )
    assert fatura.status_code in (200, 201), fatura.text
    return woid, int(fatura.json()["id"])


@pytest.fixture(scope="module")
def tohum(istemci, admin_basliklari):
    """A firmasinda IKI faturali is emri, B firmasinda BIR tane.

    IKI tane, cunku tek fatura varken bir suzgec her zaman "dogru" gorunur:
    ikinci fatura, suzgecin GERCEKTEN eledigini gosteren sey.
    """
    a = admin_basliklari
    birinci_wo, birinci_fatura = _faturali_is_emri(istemci, a, "H16-A1")
    ikinci_wo, ikinci_fatura = _faturali_is_emri(istemci, a, "H16-A2")
    assert birinci_wo != ikinci_wo and birinci_fatura != ikinci_fatura

    b_firma = istemci.post("/api/companies", headers=a, json={"name": "H16 Kiraci B"})
    assert b_firma.status_code in (200, 201), b_firma.text
    b = {**a, "X-Company-ID": str(b_firma.json()["id"])}
    b_wo, b_fatura = _faturali_is_emri(istemci, b, "H16-B1")
    # Iki firmanin is emirleri AYNI diziden id aliyor: B'nin kimligi A'da
    # ASLA gorulmemeli ve bu ancak kimlikler GERCEKTEN farkliysa bir sey
    # soyler.
    assert b_wo not in (birinci_wo, ikinci_wo)
    return {
        "a": a,
        "b": b,
        "birinci_wo": birinci_wo,
        "birinci_fatura": birinci_fatura,
        "ikinci_wo": ikinci_wo,
        "ikinci_fatura": ikinci_fatura,
        "b_wo": b_wo,
        "b_fatura": b_fatura,
    }


def _liste(istemci, basliklar, **parametreler):
    return istemci.get("/api/invoices", headers=basliklar, params=parametreler)


# --------------------------------------------------------------- (a) ------


def test_suzgec_YALNIZ_o_is_emrinin_faturasini_donduruyor(istemci, tohum):
    """Suzgecsiz IKI fatura, suzgecli TAM BIRI — ve dogru olani.

    MUTASYON: `work_order_id` kosulunu `conditions`e eklememek. O zaman
    suzgecli cagri da IKI fatura dondurur ve bu kapi kirmizi yanar.
    """
    suzgecsiz = _liste(istemci, tohum["a"], page=1, page_size=200)
    assert suzgecsiz.status_code == 200, suzgecsiz.text
    assert suzgecsiz.json()["total"] == 2
    assert {kalem["id"] for kalem in suzgecsiz.json()["items"]} == {
        tohum["birinci_fatura"],
        tohum["ikinci_fatura"],
    }

    for wo_alani, fatura_alani in (
        ("birinci_wo", "birinci_fatura"),
        ("ikinci_wo", "ikinci_fatura"),
    ):
        yanit = _liste(
            istemci, tohum["a"], work_order_id=tohum[wo_alani], page=1, page_size=1
        )
        assert yanit.status_code == 200, yanit.text
        govde = yanit.json()
        assert govde["total"] == 1, (wo_alani, govde)
        assert [kalem["id"] for kalem in govde["items"]] == [tohum[fatura_alani]]


def test_page_size_1_tek_cagriyla_yetiyor(istemci, tohum):
    """Arayuzun attigi TEK cagrinin sekli: `page=1,page_size=1` -> `items[0]`.

    `pages` de 1 olmali: aksi halde istemci "daha var" diye ikinci bir sayfa
    ister ve N+1 baska bir kilikta geri gelir.
    """
    yanit = _liste(
        istemci, tohum["a"], work_order_id=tohum["birinci_wo"], page=1, page_size=1
    )
    assert yanit.status_code == 200, yanit.text
    govde = yanit.json()
    assert govde["pages"] == 1 and len(govde["items"]) == 1
    assert govde["items"][0]["id"] == tohum["birinci_fatura"]


# --------------------------------------------------------------- (b) ------


def test_baska_firmanin_is_emri_kimligi_BOS_liste_donduruyor(istemci, tohum):
    """KIRACI KAPISI. B'nin is emri kimligi A'da HICBIR SEY acmaz.

    MUTASYON (bu turda ELLE KOSULDU): `conditions`in ilk ogesi olan
    `"company_id=:cid"` yuklemini dusurmek. O zaman A firmasi B'nin faturasini
    gorur ve bu kapi kirmizi yanar. Karsi yon de olculuyor: B kendi kimligiyle
    sordugunda faturasini GORUYOR, yani bos liste "suzgec hic calismiyor"un
    degil KIRACI SINIRININ sonucu.
    """
    sizinti = _liste(istemci, tohum["a"], work_order_id=tohum["b_wo"])
    assert sizinti.status_code == 200, sizinti.text
    assert sizinti.json()["total"] == 0 and sizinti.json()["items"] == []

    kendi = _liste(istemci, tohum["b"], work_order_id=tohum["b_wo"])
    assert kendi.status_code == 200, kendi.text
    assert [kalem["id"] for kalem in kendi.json()["items"]] == [tohum["b_fatura"]]

    # Ve simetrik: A'nin is emri B'de gorunmez.
    ters = _liste(istemci, tohum["b"], work_order_id=tohum["birinci_wo"])
    assert ters.status_code == 200, ters.text
    assert ters.json()["total"] == 0


# --------------------------------------------------------------- (c) ------


def test_bilinmeyen_is_emri_kimligi_200_ve_BOS(istemci, tohum):
    """Eslesme yoklugu bir HATA degil, BOS bir kumedir.

    MUTASYON: bilinmeyen kimlikte 404 dondurmek bu kapiyi kirmizi yapar.
    """
    yanit = _liste(istemci, tohum["a"], work_order_id=YOK_OLAN_IS_EMRI)
    assert yanit.status_code == 200, yanit.text
    govde = yanit.json()
    assert govde["total"] == 0 and govde["items"] == [] and govde["pages"] == 0


# --------------------------------------------------- dogrulama (422) ------


@pytest.mark.parametrize("kimlik", [0, -1, -999])
def test_sifir_ve_negatif_kimlik_422(istemci, tohum, kimlik):
    """`gt=0` kapisi: gecersiz kimlik SORGUYA HIC ULASMAZ.

    MUTASYON: `Query(None,gt=0)`daki `gt=0`i kaldirmak bu kapiyi kirmizi
    yapar — 0 ve negatifler 200 ile bos liste dondurmeye baslar.
    """
    yanit = _liste(istemci, tohum["a"], work_order_id=kimlik)
    assert yanit.status_code == 422, (kimlik, yanit.status_code, yanit.text)


def test_metin_kimlik_422(istemci, tohum):
    """Tamsayi olmayan deger de kapida durur; SQL'e bir dizge girmez."""
    yanit = _liste(istemci, tohum["a"], work_order_id="1 OR 1=1")
    assert yanit.status_code == 422, yanit.text

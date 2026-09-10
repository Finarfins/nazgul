"""SEC-3b — cari alan maskelemesinin GERÇEK isteklerle kanıtı.

--- BU DOSYA NIYE VAR -------------------------------------------------------

SEC-3 (#102) ondokuz GET'i daralttı ama `auth.py` kendi notunda sınırını
yazdı: `/api/customers` ve `/api/customers/{id}` BİLEREK `read`te kaldı,
"alan maskeleme ayrı bir iş olarak (SEC-3b) açıldı". Yani bu PR'dan önce
`depo` ve `rapor` rolleri müşteri listesini ve kartını TAM okuyordu --
telefon, e-posta, adres ve VERGİ NUMARASI dahil.

Bu dosya iki şeyi ölçüyor ve ikisini de SİMÜLE ETMİYOR:

1. Maskeleme fonksiyonlarının kendisi (saf birim testleri, veritabanısız).
2. BEŞ ROLÜN BEŞİYLE de gerçekten giriş yapıp, cari taşıyan uçlara GERÇEK
   GET atmak ve dönen GÖVDEYİ okumak. `required_permission` okunup sonuç
   çıkarılmıyor; 403'ler ve maskeli değerler ÖLÇÜLÜYOR.

--- MATRİSİN OKUNMASI -------------------------------------------------------

* **F** — rol tam veri görüyor (`yonetici`, `muhasebe`, `satis`).
* **M** — rol maskeli veri görüyor (`depo`, `rapor`).
* **D** — rol iznden dolayı giremiyor; 403 ölçülüyor.

`D` hücreleri TOHUMLAMADAN BAĞIMSIZDIR: yetki kapısı ara katmandadır,
handler'a hiç girilmez. `F`/`M` hücreleri ise handler'ın GERÇEKTEN koştuğunu
gösterir, çünkü gövdedeki değer tek tek karşılaştırılıyor.

--- İZİN HARİTASI (ÖLÇÜLDÜ, VARSAYILMADI) -----------------------------------

`/api/customers*`      -> `read`      : beş rol de girer  -> F,F,F,M,M
`/api/customers/{id}/statement` -> `sales` : `depo`/`rapor` 403
`/api/suppliers*`      -> `purchases` : `satis`/`rapor` 403, **`depo` GİRER**

Sondaki satır bu işin en kolay kaçırılan yeridir ve bu yüzden ayrıca
yazılıyor: `depo` rolü `purchases` iznini TAŞIR (`auth.ROLE_PERMISSIONS`),
yani maskeli bir rol tedarikçi kartına ve TEDARİKÇİ EKSTRESİNE gerçekten
girebiliyor. Ekstre başlığı VKN + adres + telefon + e-posta taşır. Maskeleme
oraya bağlanmasaydı `depo` için hiçbir şey değişmezdi.

--- ARAMA ORAKÜLÜ: BİLİNÇLİ, ÖLÇÜLEN VE RAPORLANAN SINIR --------------------

`test_maskeli_rol_arama_ile_ham_deger_alamaz` bir SINIR çiziyor ve o sınırın
neresi olduğunu açıkça yazıyor. Ayrıntı testin kendi docstring'indedir.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="sec3b-maskeleme-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "sec3b.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "Sec3bYonetim!2026"
ROL_PAROLASI = "Sec3bRolleri!2026"

ROLLER = ("yonetici", "muhasebe", "satis", "depo", "rapor")
MASKELI_ROLLER = ("depo", "rapor")
MASKESIZ_TEST_ROLLERI = ("yonetici", "muhasebe", "satis")

# --- TOHUM DEĞERLERİ ve BEKLENEN MASKELERİ ----------------------------------
# Beklenen maskeler ELLE yazıldı (fonksiyonun çıktısı tekrar edilmedi): test,
# fonksiyonun BUGÜN ne yaptığını değil, ÜRÜN KARARININ ne dediğini ölçmeli.
HAM_TELEFON = "05551234512"
HAM_EPOSTA = "ahmet@ornek.com"
HAM_VKN = "1234567890"
# İlk satır 27 karakter (>24) ve Türkçe harf içeriyor: kırpmanın KARAKTER
# bazlı olduğu (bayt değil) buradan ölçülüyor.
HAM_ADRES = "Atatürk Caddesi No 5 Daire 3\nKadıköy/İstanbul"

MASKE_TELEFON = "05** *** ** 12"
MASKE_EPOSTA = "a***@ornek.com"
MASKE_VKN = "*******890"
MASKE_ADRES = "Atatürk Caddesi No 5 Dai…"

HAM_DEGERLER = (HAM_TELEFON, HAM_EPOSTA, HAM_VKN, "Atatürk Caddesi No 5 Daire 3")

CARI_GOVDE = {
    "name": "SEC3B Müşteri",
    "phone": HAM_TELEFON,
    "email": HAM_EPOSTA,
    "address": HAM_ADRES,
    "tax_number": HAM_VKN,
}
TEDARIKCI_GOVDE = dict(CARI_GOVDE, name="SEC3B Tedarikçi")


# ===========================================================================
# 1) BİRİM TESTLERİ — veritabanısız, `Request`siz, saf.
# ===========================================================================

@pytest.mark.parametrize(
    "girdi,beklenen",
    [
        ("05551234512", "05** *** ** 12"),
        ("0555 123 45 12", "05** *** ** 12"),          # ayraçlar temizlenir
        # 12 hane (ülke kodlu): 11 OLMADIĞI için TR gruplaması UYGULANMAZ.
        ("+90 555 123 45 12", "90" + "*" * 8 + "12"),
        ("1234", "1234"[:2] + "" + "1234"[-2:]),        # tam 4 hane: orta yok
        ("123", "***"),                                  # 4'ten kısa: tamamı
        ("", ""),                                        # boş: DOKUNULMAZ
        ("   ", "   "),                                  # boşluk: DOKUNULMAZ
        (None, None),                                    # yok: yok kalır
    ],
)
def test_maskele_telefon(girdi, beklenen):
    from app.alan_maskeleme import maskele_telefon

    assert maskele_telefon(girdi) == beklenen


@pytest.mark.parametrize(
    "girdi,beklenen",
    [
        ("ahmet@ornek.com", "a***@ornek.com"),
        ("a@b.co", "a***@b.co"),                    # tek harfli yerel kısım
        ("çiğdem@örnek.com.tr", "ç***@örnek.com.tr"),  # Unicode yerel + alan
        ("ahmet@ornek@com", "a***@ornek@com"),      # ilk @ ayırır
        ("@ornek.com", "***"),                      # yerel kısım yok
        ("ahmet@", "***"),                          # alan adı yok
        ("duz-metin", "***"),                       # @ yok: e-posta değil
        ("", ""),
        (None, None),
    ],
)
def test_maskele_eposta(girdi, beklenen):
    from app.alan_maskeleme import maskele_eposta

    assert maskele_eposta(girdi) == beklenen


@pytest.mark.parametrize(
    "girdi,beklenen",
    [
        ("1234567890", "*******890"),
        ("12345678901", "********901"),   # 11 hane TCKN
        ("1234", "*234"),
        ("123", "***"),                    # 3 hane: son 3 = tamamı, gizle
        ("12", "***"),
        ("", ""),
        (None, None),
    ],
)
def test_maskele_vergi_no(girdi, beklenen):
    from app.alan_maskeleme import maskele_vergi_no

    assert maskele_vergi_no(girdi) == beklenen


@pytest.mark.parametrize(
    "girdi,beklenen",
    [
        # 27 karakterlik ilk satır + ikinci satır -> kırpılır ve … eklenir
        ("Atatürk Caddesi No 5 Daire 3\nKadıköy/İstanbul", "Atatürk Caddesi No 5 Dai…"),
        # Kısa TEK satır: hiçbir şey düşmedi, … EKLENMEZ
        ("Kısa Sokak 1", "Kısa Sokak 1"),
        # Kısa ilk satır ama İKİNCİ satır var: içerik düştü, … eklenir
        ("Kısa Sokak 1\nAnkara", "Kısa Sokak 1…"),
        # Tam 24 karakter: sınır, kırpılmaz
        ("123456789012345678901234", "123456789012345678901234"),
        # 25 karakter: sınırın bir fazlası, kırpılır
        ("1234567890123456789012345", "123456789012345678901234…"),
        ("", ""),
        (None, None),
    ],
)
def test_maskele_adres(girdi, beklenen):
    from app.alan_maskeleme import maskele_adres

    assert maskele_adres(girdi) == beklenen


@pytest.mark.parametrize(
    "girdi,beklenen",
    [
        ("TR330006100519786457841326", "*" * 22 + "1326"),
        ("12345", "*2345"),   # SON dört hane kalır, ilk değil
        ("1234", "***"),
        ("", ""),
        (None, None),
    ],
)
def test_maskele_iban(girdi, beklenen):
    from app.alan_maskeleme import maskele_iban

    assert maskele_iban(girdi) == beklenen


def test_maskele_cari_girdiyi_degistirmez():
    """Maskeleme YERİNDE değiştirmez.

    Önemli, çünkü aynı satır hem maskeli yanıtta hem MASKESİZ denetim
    kaydında (`change_history.record_change`) kullanılabiliyor. Yerinde
    değiştirme, denetim kaydının "önce/sonra" görüntüsünü sessizce bozardı.
    """
    from app.alan_maskeleme import maskele_cari

    girdi = {"id": 1, "phone": HAM_TELEFON, "tax_number": HAM_VKN}
    cikti = maskele_cari(girdi, "depo")
    assert girdi == {"id": 1, "phone": HAM_TELEFON, "tax_number": HAM_VKN}
    assert cikti["phone"] == MASKE_TELEFON
    assert cikti is not girdi


def test_maskele_cari_bilinmeyen_rol_maskeler():
    """Deny-by-default: listede olmayan HER rol maskelidir."""
    from app.alan_maskeleme import maskele_cari

    for rol in ("", None, "yeni_rol", "DEPO", "yonetıcı"):
        assert maskele_cari({"phone": HAM_TELEFON}, rol)["phone"] == MASKE_TELEFON


def test_maskesiz_roller_dokunulmamis_satir_dondurur():
    from app.alan_maskeleme import maskele_cari

    satir = {"phone": HAM_TELEFON, "email": HAM_EPOSTA, "tax_number": HAM_VKN}
    for rol in ("admin",) + MASKESIZ_TEST_ROLLERI:
        assert maskele_cari(satir, rol) == satir


def test_firma_kendi_vergi_numarasi_maskelenmez():
    """`company_tax_number` CARİ verisi DEĞİLDİR; önek soyma onu yakalamamalı.

    Fatura ve ekstre başlıkları FİRMANIN KENDİ VKN'sini taşıyor. Onu
    maskelemek, kullanıcının kendi şirketinin numarasını kendinden gizlerdi.
    """
    from app.alan_maskeleme import maskele_cari

    satir = {"company_tax_number": HAM_VKN, "customer_tax_number": HAM_VKN}
    cikti = maskele_cari(satir, "depo")
    assert cikti["company_tax_number"] == HAM_VKN
    assert cikti["customer_tax_number"] == MASKE_VKN


# ===========================================================================
# 2) TOHUMLAMA — uygulama gerçekten ayağa kalkıyor.
# ===========================================================================

@pytest.fixture(scope="module")
def istemci():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def admin_basliklari(istemci):
    giris = istemci.post(
        "/api/auth/login", json={"username": "admin", "password": ACILIS_PAROLASI}
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
        json={"current_password": ACILIS_PAROLASI, "new_password": ADMIN_PAROLASI},
    )
    assert degis.status_code == 200, degis.text
    basliklar["Authorization"] = "Bearer " + degis.json()["access_token"]
    return basliklar


@pytest.fixture(scope="module")
def tohum(istemci, admin_basliklari):
    """Hassas alanları DOLU bir müşteri ve bir tedarikçi.

    Kimlikler doğrulanıyor: matris somut `/1` yollarını kullanıyor ve
    tohumlama bozulursa test sessizce 404'lerin üzerinde koşardı.
    """
    h = admin_basliklari
    musteri = istemci.post("/api/customers", headers=h, json=CARI_GOVDE)
    assert musteri.status_code in (200, 201), musteri.text
    tedarikci = istemci.post("/api/suppliers", headers=h, json=TEDARIKCI_GOVDE)
    assert tedarikci.status_code in (200, 201), tedarikci.text
    m_id, t_id = musteri.json()["id"], tedarikci.json()["id"]
    assert m_id == 1 and t_id == 1, (musteri.json(), tedarikci.json())
    return {
        "company_id": int(h["X-Company-ID"]),
        "musteri_id": m_id,
        "tedarikci_id": t_id,
    }


@pytest.fixture(scope="module")
def rol_basliklari(istemci, admin_basliklari, tohum):
    """Beş rol için GERÇEK kullanıcı + GERÇEK giriş."""
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
                    "k": f"sec3b_{rol}", "e": f"sec3b_{rol}@ornek.test",
                    "d": f"SEC3B {rol}", "p": hash_password(ROL_PAROLASI),
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
            json={"username": f"sec3b_{rol}", "password": ROL_PAROLASI},
        )
        assert giris.status_code == 200, (rol, giris.text)
        basliklar[rol] = {
            "Authorization": "Bearer " + giris.json()["access_token"],
            "X-Company-ID": str(company_id),
        }
    return basliklar


def _govde_metni(yanit) -> str:
    """Yanıt gövdesini ARANABİLİR tek metne indirger.

    JSON'ı alan alan gezmek yerine metin araması yapılıyor: iç içe bir
    yapıda ham VKN'nin NEREDE göründüğü değil, HİÇ görünmediği ölçülmek
    isteniyor. Yeni bir alan eklenip ham değeri oradan sızdırsa, alan bazlı
    bir iddia bunu kaçırırdı.
    """
    return yanit.text


# ===========================================================================
# 3) ROL MATRİSİ — gerçek istek, gerçek gövde.
# ===========================================================================

#: (yol, izin) -- izin `auth.required_permission`den ÖLÇÜLÜYOR, elle
#: yazılmıyor; `test_matris_izinleri_auth_ile_ayni` bunu doğruluyor.
MASKELI_YOLLAR = (
    "/api/customers",
    "/api/customers/1",
    "/api/suppliers",
    "/api/suppliers/1",
    "/api/suppliers/1/statement",
    "/api/search?q=SEC3B",
)

#: Maskeli rollerin GİREMEDİĞİ cari yolları ve beklenen kod.
YASAK_YOLLAR = {
    "/api/customers/1/statement": ("sales", 403),
}


@pytest.mark.parametrize("rol", MASKESIZ_TEST_ROLLERI)
@pytest.mark.parametrize("yol", MASKELI_YOLLAR)
def test_maskesiz_roller_tam_veri_gorur(istemci, rol_basliklari, tohum, rol, yol):
    """`yonetici`/`muhasebe`/`satis` ya TAM görür ya da 403 alır -- maskeli ASLA."""
    from app.auth import ROLE_PERMISSIONS, required_permission

    yanit = istemci.get(yol, headers=rol_basliklari[rol])
    izin = required_permission("GET", yol.split("?")[0])
    if izin not in ROLE_PERMISSIONS[rol]:
        assert yanit.status_code == 403, (rol, yol, yanit.status_code, yanit.text)
        return
    assert yanit.status_code == 200, (rol, yol, yanit.status_code, yanit.text)
    metin = _govde_metni(yanit)
    # `/api/search` altyazıda yalnız telefonu gösterir; hepsini değil.
    beklenen = HAM_TELEFON if "search" in yol else HAM_TELEFON
    assert beklenen in metin, (rol, yol, "ham telefon YOK", metin[:400])
    assert MASKE_VKN not in metin, (rol, yol, "maskeli VKN sızdı", metin[:400])


@pytest.mark.parametrize("rol", MASKELI_ROLLER)
@pytest.mark.parametrize("yol", MASKELI_YOLLAR)
def test_maskeli_roller_ham_deger_gormez(istemci, rol_basliklari, tohum, rol, yol):
    """`depo`/`rapor`: ya 403 ya da gövdede HİÇBİR ham hassas değer YOK.

    İddia "maskeli değer var" değil, "HAM DEĞER YOK" biçiminde kuruldu.
    Birincisi maskelemenin bir yerde çalıştığını gösterir; ikincisi ham
    değerin BAŞKA BİR ALANDAN da çıkmadığını gösterir. Cari kartı aynı satırı
    iki anahtar altında döndürüyor (`customer` ve `entity`) -- ilk biçimdeki
    bir iddia, ikincisi maskelenmemişken de yeşil kalırdı.
    """
    from app.auth import ROLE_PERMISSIONS, required_permission

    yanit = istemci.get(yol, headers=rol_basliklari[rol])
    izin = required_permission("GET", yol.split("?")[0])
    if izin not in ROLE_PERMISSIONS[rol]:
        assert yanit.status_code == 403, (rol, yol, yanit.status_code, yanit.text)
        return
    assert yanit.status_code == 200, (rol, yol, yanit.status_code, yanit.text)
    metin = _govde_metni(yanit)
    for ham in HAM_DEGERLER:
        assert ham not in metin, (rol, yol, f"HAM DEĞER SIZDI: {ham}", metin[:400])


@pytest.mark.parametrize("rol", MASKELI_ROLLER)
def test_maskeli_rol_musteri_listesinde_maskeli_degerleri_gorur(
    istemci, rol_basliklari, tohum, rol
):
    """Maskeleme veriyi SİLMİYOR, GİZLİYOR: alanlar yerinde ve maskeli.

    Bu ayrı bir testtir çünkü "ham değer yok" iddiası, alanların tamamen
    DÜŞÜRÜLMESİYLE de sağlanırdı -- ve o, ekranı bozan sessiz bir davranış
    değişikliği olurdu.
    """
    yanit = istemci.get("/api/customers", headers=rol_basliklari[rol])
    assert yanit.status_code == 200, yanit.text
    satir = next(s for s in yanit.json() if s["id"] == tohum["musteri_id"])
    assert satir["name"] == CARI_GOVDE["name"]      # ad ve kimlik AÇIK kalır
    assert satir["phone"] == MASKE_TELEFON
    assert satir["email"] == MASKE_EPOSTA
    assert satir["tax_number"] == MASKE_VKN
    assert satir["address"] == MASKE_ADRES


@pytest.mark.parametrize("rol", MASKELI_ROLLER)
def test_maskeli_rol_cari_kartinda_IKI_anahtari_da_maskeli_gorur(
    istemci, rol_basliklari, tohum, rol
):
    """Kart aynı satırı `customer` VE `entity` altında döndürüyor; ikisi de maskeli."""
    yanit = istemci.get(
        f"/api/customers/{tohum['musteri_id']}", headers=rol_basliklari[rol]
    )
    assert yanit.status_code == 200, yanit.text
    govde = yanit.json()
    for anahtar in ("customer", "entity"):
        assert govde[anahtar]["tax_number"] == MASKE_VKN, anahtar
        assert govde[anahtar]["phone"] == MASKE_TELEFON, anahtar


def test_depo_tedarikci_ekstresine_GIRER_ama_maskeli_gorur(
    istemci, rol_basliklari, tohum
):
    """Bu işin en kolay kaçırılan hücresi.

    `depo` rolü `purchases` iznini TAŞIR, yani `/api/suppliers/{id}/statement`
    ona 403 DEĞİL 200 döner. Ekstre başlığı VKN + adres + telefon + e-posta
    taşıyor. `statement.py` maskelemeye bağlanmasaydı SEC-3b `depo` için
    hiçbir şey değiştirmemiş olurdu.
    """
    from app.auth import ROLE_PERMISSIONS

    assert "purchases" in ROLE_PERMISSIONS["depo"], (
        "İzin haritası değişmiş: `depo` artık `purchases` taşımıyor. "
        "Bu testin ölçtüğü hücre ortadan kalkmış olabilir."
    )
    yanit = istemci.get(
        f"/api/suppliers/{tohum['tedarikci_id']}/statement",
        headers=rol_basliklari["depo"],
    )
    assert yanit.status_code == 200, yanit.text
    entity = yanit.json()["entity"]
    assert entity["tax_number"] == MASKE_VKN
    assert entity["phone"] == MASKE_TELEFON
    assert entity["email"] == MASKE_EPOSTA
    assert entity["address"] == MASKE_ADRES
    assert entity["name"] == TEDARIKCI_GOVDE["name"]


def test_maskesiz_rol_tedarikci_ekstresinde_tam_veri_gorur(
    istemci, rol_basliklari, tohum
):
    """Karşı hücre: `muhasebe` aynı ekstrede HAM değeri görür."""
    yanit = istemci.get(
        f"/api/suppliers/{tohum['tedarikci_id']}/statement",
        headers=rol_basliklari["muhasebe"],
    )
    assert yanit.status_code == 200, yanit.text
    entity = yanit.json()["entity"]
    assert entity["tax_number"] == HAM_VKN
    assert entity["phone"] == HAM_TELEFON


@pytest.mark.parametrize("rol", ROLLER)
def test_musteri_ekstresi_izin_matrisi(istemci, rol_basliklari, tohum, rol):
    """`/api/customers/{id}/statement` -> `sales`: `depo`/`rapor` 403 ALIR.

    Maskeleme burada İKİNCİ savunma hattıdır, tek hat değil: maskeli roller
    zaten kapıdan geçemiyor. Kod ölçülüyor ki bir gün izin gevşetilirse bu
    test, maskelemenin devraldığını değil, kapının açıldığını söylesin.
    """
    from app.auth import ROLE_PERMISSIONS

    yanit = istemci.get(
        f"/api/customers/{tohum['musteri_id']}/statement",
        headers=rol_basliklari[rol],
    )
    if "sales" in ROLE_PERMISSIONS[rol]:
        assert yanit.status_code == 200, (rol, yanit.text)
        assert HAM_VKN in yanit.text
    else:
        assert yanit.status_code == 403, (rol, yanit.status_code, yanit.text)


@pytest.mark.parametrize("rol", ROLLER)
def test_musteri_belgeleri_hassas_alan_tasimiyor(istemci, rol_basliklari, tohum, rol):
    """`/api/customers/{id}/documents` cari İLETİŞİM verisi döndürmüyor (ÖLÇÜLDÜ).

    Uç `read` iznindedir, yani beş rol de girer. Maskeleme gerekmiyor çünkü
    yanıt yalnız belge satırlarını taşıyor. Bu bir iddia değil ölçüm: gövdede
    hiçbir ham hassas değer aranıyor ve bulunmuyor. Yarın uca cari kartı
    eklenirse burası kırmızı yanar.
    """
    yanit = istemci.get(
        f"/api/customers/{tohum['musteri_id']}/documents",
        headers=rol_basliklari[rol],
    )
    assert yanit.status_code == 200, (rol, yanit.text)
    for ham in HAM_DEGERLER:
        assert ham not in yanit.text, (rol, f"belgelerde ham değer: {ham}")


@pytest.mark.parametrize("rol", ROLLER)
def test_pos_ve_quick_pick_cari_hassas_alan_tasimiyor(
    istemci, rol_basliklari, tohum, rol
):
    """POS ve hızlı seçim uçları cari iletişim/vergi verisi DÖNDÜRMÜYOR (ÖLÇÜLDÜ).

    `pos.py`nin müşteri çözümü yalnız `id,name` seçiyor, `quick_pick.py` ise
    yalnız ürün satırı döndürüyor. Ölçüm burada dondurulıyor ki bu uçlara
    ileride cari kartı eklenirse maskeleme kararı yeniden verilsin.
    """
    for yol in ("/api/pos/customers?q=SEC3B", "/api/quick-pick"):
        yanit = istemci.get(yol, headers=rol_basliklari[rol])
        if yanit.status_code in (403, 404, 422):
            continue  # uç yok ya da rol giremiyor: ölçülecek gövde yok
        assert yanit.status_code == 200, (rol, yol, yanit.status_code, yanit.text)
        for ham in (HAM_TELEFON, HAM_EPOSTA, HAM_VKN):
            assert ham not in yanit.text, (rol, yol, f"ham değer: {ham}")


def test_matris_izinleri_auth_ile_ayni():
    """Bu dosyadaki izin varsayımları `auth.py` ile UÇ UCA aynı mı?

    Matris kendi izin haritasını taşımıyor; `required_permission`den
    okuyor. Bu test o okumanın beklenen değerleri verdiğini dondurarak,
    izin bir gün değişirse matrisin SESSİZCE başka bir şey ölçmesini
    engelliyor.
    """
    from app.auth import required_permission

    assert required_permission("GET", "/api/customers") == "read"
    assert required_permission("GET", "/api/customers/1") == "read"
    assert required_permission("GET", "/api/customers/1/statement") == "sales"
    assert required_permission("GET", "/api/suppliers") == "purchases"
    assert required_permission("GET", "/api/suppliers/1/statement") == "purchases"
    assert required_permission("GET", "/api/search") == "read"


# ===========================================================================
# 4) ARAMA ORAKÜLÜ — bilinçli sınır, açıkça ölçülüyor.
# ===========================================================================

@pytest.mark.parametrize("rol", MASKELI_ROLLER)
def test_maskeli_rol_arama_ile_ham_deger_alamaz(istemci, rol_basliklari, tohum, rol):
    """Arama ÇALIŞMAYA DEVAM EDER, YANIT MASKELİ KALIR -- ve sınır budur.

    SEÇİLEN DAVRANIŞ (iki seçenek vardı, biri seçildi ve yazıldı):

    `q` parametresi bugün `name`, `phone`, `email` ve `tax_number` üzerinde
    eşleşiyor. Maskeli roller için bu eşleşme DARALTILMADI. Gerekçe:

    1. Depo görevlisinin telefonla müşteri araması GÜNLÜK İŞTİR; aramayı ada
       indirmek SEC-3'ün korumayı hedeflediği iş yüzeyini keserdi.
    2. Daraltma, `musteri_satirlari`nin SQL'ini role göre değiştirmeyi
       gerektirirdi. O fonksiyonu WhatsApp kanalı da çağırıyor ve gövdesi
       `test_tenant_scoping_guard` içinde parmak iziyle sabitlenmiş durumda;
       yani daraltma iki yüzeyi ve bir pini birden hareket ettirirdi.

    ÖLÇÜLEN VE KABUL EDİLEN KALAN SINIR: `q` bir ORAKÜL'dür. Ham VKN'yi
    ZATEN BİLEN bir `depo` kullanıcısı `?q=1234567890` yazıp dönen satırdan
    o VKN'nin BU müşteriye ait olduğunu DOĞRULAYABİLİR. Doğrulama, elde
    etmek değildir -- kullanıcının değeri başka bir yerden bilmesi gerekir --
    ama sıfır da değildir. Bu, mekanizmanın değil MATRİSİN kararıdır:
    daraltma istenirse `musteri_satirlari`na tek bir `maskeli` bayrağı
    eklemek yeterlidir.

    Test HER İKİ yarıyı da ölçüyor: arama gerçekten çalışıyor VE yanıt
    gerçekten maskeli. Biri bozulursa hangisinin bozulduğu adıyla görünür.
    """
    yanit = istemci.get(
        f"/api/customers?q={HAM_VKN}", headers=rol_basliklari[rol]
    )
    assert yanit.status_code == 200, yanit.text
    satirlar = yanit.json()
    # (a) Arama ÇALIŞIYOR: tam VKN ile aranan müşteri bulunuyor.
    assert [s for s in satirlar if s["id"] == tohum["musteri_id"]], (
        "arama daraltılmadı deniyor ama satır gelmedi; davranış değişmiş"
    )
    # (b) YANIT MASKELİ: eşleşmeyi doğrulayan gövde ham değeri TAŞIMIYOR.
    for ham in HAM_DEGERLER:
        assert ham not in yanit.text, f"arama yanıtında ham değer: {ham}"


@pytest.mark.parametrize("rol", MASKELI_ROLLER)
def test_maskeli_rol_siralama_ile_ham_deger_alamaz(istemci, rol_basliklari, tohum, rol):
    """Sıralama parametresi de ham değer sızdırmıyor.

    `sort` yalnız ad/bakiye/hareket sütunlarını kabul ediyor ve tanınmayan
    değer sessizce `name_asc`a düşüyor -- yani hassas sütuna göre sıralama
    İSTENSE BİLE olmuyor. Ölçüm dondurulıyor: biri `SORTS`a
    `tax_number_asc` eklerse maskeli rol satırları VKN sırasına dizip
    değeri ikili aramayla çıkarabilirdi.
    """
    from app.routers.customers import SORTS

    hassas = [a for a in SORTS if "tax" in a or "phone" in a or "mail" in a]
    assert not hassas, f"SORTS hassas sütuna göre sıralama sunuyor: {hassas}"

    yanit = istemci.get(
        "/api/customers?sort=tax_number_asc", headers=rol_basliklari[rol]
    )
    assert yanit.status_code == 200, yanit.text
    for ham in HAM_DEGERLER:
        assert ham not in yanit.text


# ===========================================================================
# 5) KİRACI YALITIMI — maskeleme onu DEĞİŞTİRMEDİ.
# ===========================================================================

def test_capraz_kiraci_hala_404(istemci, admin_basliklari, rol_basliklari, tohum):
    """Başka kiracının carisi maskeli DEĞİL, YOK.

    Maskeleme okuma yolunun sonuna eklendi; kiracı yüklemi sorguda ve
    ondan ÖNCE. Bu test o sıranın bozulmadığını ölçüyor: maskeleme bir gün
    yanlışlıkla kiracı kontrolünün ÖNÜNE geçseydi, komşunun carisi 404
    yerine MASKELİ 200 dönerdi -- sızıntının en sinsi biçimi.
    """
    from sqlalchemy import text

    from app.db import SessionLocal

    with SessionLocal() as db:
        komsu_id = db.execute(
            text(
                "INSERT INTO companies(name,is_active,created_at)"
                " VALUES(:n,:a,:t) RETURNING id"
            ),
            {
                "n": "SEC3B Komşu Firma", "a": True,
                "t": datetime.now(timezone.utc),
            },
        ).scalar_one()
        yabanci_id = db.execute(
            text(
                "INSERT INTO customers(name,phone,email,address,tax_number,"
                "opening_balance,risk_limit,payment_term_days,is_active,company_id)"
                " VALUES(:n,:p,:e,:a,:v,0,0,0,true,:c) RETURNING id"
            ),
            {
                "n": "KOMŞU Müşteri", "p": HAM_TELEFON, "e": HAM_EPOSTA,
                "a": HAM_ADRES, "v": HAM_VKN, "c": komsu_id,
            },
        ).scalar_one()
        db.commit()

    for rol in ROLLER:
        yanit = istemci.get(
            f"/api/customers/{yabanci_id}", headers=rol_basliklari[rol]
        )
        assert yanit.status_code == 404, (
            rol, yanit.status_code,
            "komşu kiracının carisi MASKELİ 200 döndü; kiracı yüklemi "
            "maskelemenin arkasına düşmüş olabilir",
        )


# ===========================================================================
# 6) YAZMA TUZAĞI — okuma maskesinin yarattığı SESSİZ VERİ KAYBI kapısı.
# ===========================================================================

def test_yazma_izinleri_olculdu_varsayilmadi():
    """Brief'in "maskeli roller zaten yazamaz" varsayımı ÖLÇÜLDÜ ve YANLIŞ.

    `depo` rolü `purchases` iznini TAŞIR, yani tedarikçi POST/PUT/DELETE'ine
    GİREBİLİR. Yani "maskeleme yalnız okuma yolundadır, yazma zaten kapalı"
    cümlesi MÜŞTERİ için doğru, TEDARİKÇİ için YANLIŞTIR. Bu test o ölçümü
    donduruyor; izin haritası değişirse yazma korumasının gerekçesi de
    yeniden okunmalıdır.
    """
    from app.auth import ROLE_PERMISSIONS, required_permission

    # Müşteri yazma: maskeli rollerin ikisi de DIŞARIDA.
    musteri_izni = required_permission("PUT", "/api/customers/1")
    assert musteri_izni == "sales"
    for rol in MASKELI_ROLLER:
        assert musteri_izni not in ROLE_PERMISSIONS[rol], rol

    # Tedarikçi yazma: `depo` İÇERİDE. Varsayımın kırıldığı yer burası.
    tedarikci_izni = required_permission("PUT", "/api/suppliers/1")
    assert tedarikci_izni == "purchases"
    assert tedarikci_izni in ROLE_PERMISSIONS["depo"], (
        "`depo` artık `purchases` taşımıyor: yazma tuzağı ortadan kalkmış "
        "olabilir, `maskeyi_geri_al`ın gerekçesi yeniden okunmalı."
    )
    assert tedarikci_izni not in ROLE_PERMISSIONS["rapor"]


def test_depo_tedarikciyi_duzenleyince_ham_vergi_no_KORUNUR(
    istemci, rol_basliklari, admin_basliklari, tohum
):
    """Maskeli değeri geri gönderen form GERÇEK veriyi EZMEZ.

    ÖLÇÜLEN SENARYO (uydurma değil, arayüzün bugünkü davranışı):
    `frontend/src/components/EntityDialog.tsx` formu `GET /suppliers/{id}`
    ile dolduruyor ve kaydederken gördüğü nesnenin TAMAMINI geri PUT ediyor.
    `depo` rolü tedarikçi PUT'una girebildiği için, yalnızca ADI değiştiren
    bir `depo` kullanıcısı maskeli `tax_number`ı sunucuya geri gönderir.

    Koruma olmasaydı gerçek VKN `"*******890"` ile EZİLİRDİ: hata yok, 200
    döner, ham değer hiçbir yerde kalmaz. Test tam bu turu koşuyor -- kartı
    `depo` gözüyle OKUYOR, dönen gövdeyi olduğu gibi geri YAZIYOR, sonra
    `admin` gözüyle ham değerin YERİNDE olduğunu doğruluyor.
    """
    h_depo = rol_basliklari["depo"]
    sid = tohum["tedarikci_id"]

    kart = istemci.get(f"/api/suppliers/{sid}", headers=h_depo)
    assert kart.status_code == 200, kart.text
    govde = dict(kart.json()["supplier"])
    assert govde["tax_number"] == MASKE_VKN, "önkoşul: kart maskeli gelmeli"

    govde["name"] = "SEC3B Tedarikçi (depo düzenledi)"
    yazma = istemci.put(f"/api/suppliers/{sid}", headers=h_depo, json=govde)
    assert yazma.status_code == 200, yazma.text

    sonra = istemci.get(f"/api/suppliers/{sid}", headers=admin_basliklari)
    assert sonra.status_code == 200, sonra.text
    varlik = sonra.json()["supplier"]
    assert varlik["name"] == "SEC3B Tedarikçi (depo düzenledi)"  # düzenleme GEÇTİ
    assert varlik["tax_number"] == HAM_VKN, "GERÇEK VKN maskeyle EZİLDİ"
    assert varlik["phone"] == HAM_TELEFON, "GERÇEK telefon maskeyle EZİLDİ"
    assert varlik["email"] == HAM_EPOSTA, "GERÇEK e-posta maskeyle EZİLDİ"
    assert varlik["address"] == HAM_ADRES, "GERÇEK adres maskeyle EZİLDİ"


def test_depo_gercekten_yeni_deger_yazabilir(
    istemci, rol_basliklari, admin_basliklari, tohum
):
    """Koruma MEŞRU düzenlemeyi ENGELLEMEZ.

    Karşı hücre: `depo` alana GERÇEKTEN yeni bir numara yazarsa o yazılır.
    Bu test olmasaydı `maskeyi_geri_al` "maskeli rol bu alanları hiç
    değiştiremez" gibi çok daha geniş bir davranışa kayabilir ve kimse
    fark etmezdi.
    """
    h_depo = rol_basliklari["depo"]
    sid = tohum["tedarikci_id"]

    kart = istemci.get(f"/api/suppliers/{sid}", headers=h_depo)
    govde = dict(kart.json()["supplier"])
    govde["phone"] = "05329998877"
    yazma = istemci.put(f"/api/suppliers/{sid}", headers=h_depo, json=govde)
    assert yazma.status_code == 200, yazma.text

    sonra = istemci.get(f"/api/suppliers/{sid}", headers=admin_basliklari)
    assert sonra.json()["supplier"]["phone"] == "05329998877"
    # Dokunulmayan alan hâlâ ham.
    assert sonra.json()["supplier"]["tax_number"] == HAM_VKN


def test_maskeyi_geri_al_birim():
    """`maskeyi_geri_al`ın kuralı: "maskesinin aynısı" = "değişmedi"."""
    from app.alan_maskeleme import maskeyi_geri_al

    mevcut = {"phone": HAM_TELEFON, "tax_number": HAM_VKN, "name": "Eski"}

    # (a) Maskeli değer geri geldi -> ham korunur.
    gelen = {"phone": MASKE_TELEFON, "tax_number": MASKE_VKN, "name": "Yeni"}
    sonuc = maskeyi_geri_al(gelen, mevcut, "depo")
    assert sonuc["phone"] == HAM_TELEFON
    assert sonuc["tax_number"] == HAM_VKN
    assert sonuc["name"] == "Yeni"          # maskelenmeyen alan serbest

    # (b) GERÇEKTEN yeni değer -> yazılır.
    gelen = {"phone": "05329998877", "tax_number": HAM_VKN}
    sonuc = maskeyi_geri_al(gelen, mevcut, "depo")
    assert sonuc["phone"] == "05329998877"

    # (c) Maskesiz rol -> fonksiyon hiçbir şey yapmaz.
    gelen = {"phone": MASKE_TELEFON}
    assert maskeyi_geri_al(gelen, mevcut, "yonetici")["phone"] == MASKE_TELEFON

    # (d) Mevcut satır yoksa (yeni kayıt) -> dokunulmaz.
    assert maskeyi_geri_al({"phone": MASKE_TELEFON}, None, "depo") == {
        "phone": MASKE_TELEFON
    }

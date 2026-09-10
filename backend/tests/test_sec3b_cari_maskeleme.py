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

--- ARAMA ORAKÜLÜ: ÖLÇÜLDÜ VE KAPATILDI --------------------------------------

d7b8930'da `q` maskeli rol için de telefon/e-posta/VKN üzerinde eşleşiyordu ve
bu "doğrulama, elde etmek değildir" diye kabul edilmişti. Çalışma zamanı
merceği o cümleyi YANLIŞLADI: `depo` maskeli `*******596`yı görüp `q=1596`,
`q=31596`, ... diye soneki büyüterek 80 gerçek istekte TAM VKN'yi geri çıkardı
(her adımda tek eşleşme). Şef kararı: maskeli rol için `q` yalnız `name` ve
`owner_name` üzerinde eşleşir -- `/api/customers`, `/api/suppliers` ve
`/api/search` üçünde de. Bölüm 4 hem daraltmayı hem de yürütmenin İLK ADIMDA
sıfır eşleşme verdiğini ölçüyor; maskesiz roller tam süzgeci korur ve karşı
hücreleri aynı bölümdedir.

--- YAZMA: MASKELİ ROL MASKELİ ALANA HİÇ YAZAMAZ ---------------------------

d7b8930'daki `maskeyi_geri_al` yalnız "gelen == maske(saklanan)" iken saklanan
değeri koruyordu; sözleşme merceği `depo` ile `tax_number="*******891"`
gönderip 200 aldı ve saklanan değer `"*******891"` oldu. Şef kararı: maskeli
rol için `MASKELENEN_ALANLAR`daki HER anahtarda SAKLANAN KAZANIR, gelen ne
olursa olsun; 4xx yok çünkü form nesnenin tamamını geri gönderiyor. Bölüm 6.
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
    # `owner_name` MASKELENMEZ (ürün kararı) ve daraltılmış `q` süzgecinin
    # ikinci sütunudur; dolu olması o sütunun gerçekten arandığını ölçtürür.
    "owner_name": "Ahmet Yetkili",
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
# 4) ARAMA ORAKÜLÜ — ölçüldü ve KAPATILDI: maskeli rol için `q` yalnız ad.
# ===========================================================================

#: Maskeli değerden HERKESİN okuyabildiği VKN soneki (`*******890` -> `890`).
#: Sonek yürütmesi bu üç rakamdan başlar ve her adımda bir rakam ekler.
VKN_SONEKI = HAM_VKN[-3:]
#: Maskeli telefondan okunabilen sonek (`05** *** ** 12` -> `12`).
TELEFON_SONEKI = HAM_TELEFON[-2:]
RAKAMLAR = "0123456789"


def _eslesen_kimlikler(istemci, basliklar, yol, q) -> set[int]:
    """`yol?q=<q>` yanıtındaki cari kimlikleri; liste ucu ve `/api/search` için."""
    yanit = istemci.get(yol, params={"q": q}, headers=basliklar)
    assert yanit.status_code == 200, (yol, q, yanit.text)
    govde = yanit.json()
    if isinstance(govde, dict):  # /api/search -> {"items": [...]}
        return {s["id"] for s in govde["items"] if s["type"] == "customer"}
    return {s["id"] for s in govde}


def _sonek_ilk_adimi(istemci, basliklar, yol, sonek, kimlik) -> list[str]:
    """Yürütmenin İLK adımı: on rakamın her birini sonekin önüne koy, sor.

    Merceğin ölçtüğü saldırı tam bu döngüdür (`q=<rakam>596`): bir adayda
    tek eşleşme gelirse rakam kesinleşir ve bir sonraki adıma geçilir. Dönen
    liste, seçilen cariyi DÖNDÜREN adayların listesidir; boş liste yürütmenin
    daha ilk adımda tıkandığı anlamına gelir. On istek atılır, sonuç ölçülür.
    """
    return [
        rakam + sonek
        for rakam in RAKAMLAR
        if kimlik in _eslesen_kimlikler(istemci, basliklar, yol, rakam + sonek)
    ]


@pytest.mark.parametrize("rol", MASKELI_ROLLER)
def test_maskeli_rol_arama_ile_ham_deger_alamaz(istemci, rol_basliklari, tohum, rol):
    """Maskeli rol için `q` YALNIZ ad ve yetkili adında arar; hassas alanda ARAMAZ.

    ÖNCEKİ DAVRANIŞ VE NEDEN YANLIŞTI: d7b8930'da `q` maskeli rol için de
    `phone`/`email`/`tax_number` üzerinde eşleşiyordu ve bu docstring
    "doğrulama, elde etmek değildir" diyordu. Çalışma zamanı merceği bu
    cümleyi ÖLÇEREK yanlışladı: `q` bir İÇERİR süzgecidir (`LIKE %q%`), yani
    maskede görünen üç sonek rakamından başlayıp her adımda bir rakam ekleyen
    `depo`, 80 gerçek istekte TAM VKN'yi geri çıkardı -- her adımda tek
    eşleşme. "Doğrulama" değil, TAM ÇIKARMA orakülüydü.

    YENİ DAVRANIŞ (şef kararı): maskeli roller için süzgeç `name` ve
    `owner_name` ile sınırlı. Telefonla müşteri bulmak isteyen `depo` bunu
    artık ada göre yapar; bu, SEC-3b'nin koruduğu verinin fiyatıdır ve
    bilinçli ödenmiştir. Maskesiz roller tam süzgeci korur
    (`test_maskesiz_rol_vkn_ve_telefonla_arayabilir`).

    Test iki yarıyı da ölçüyor: hassas alanlarla arama cariyi GETİRMEZ, ad ve
    yetkili adıyla arama GETİRİR ve getirdiği satır MASKELİDİR.
    """
    h = rol_basliklari[rol]
    mid = tohum["musteri_id"]

    # (a) Hassas alanların HİÇBİRİ süzgeçte değil: tam değerle bile bulunmaz.
    for hassas in (HAM_VKN, HAM_TELEFON, HAM_EPOSTA, "Atatürk Caddesi"):
        assert mid not in _eslesen_kimlikler(istemci, h, "/api/customers", hassas), (
            f"maskeli rol hassas alanla aradı ve buldu: {hassas!r}"
        )

    # (b) Ad ve yetkili adıyla arama ÇALIŞIR ve yanıt MASKELİDİR.
    for ad in ("SEC3B", "sec3b müşteri", "Yetkili"):
        yanit = istemci.get("/api/customers", params={"q": ad}, headers=h)
        assert yanit.status_code == 200, yanit.text
        satir = [s for s in yanit.json() if s["id"] == mid]
        assert satir, f"ad/yetkili aramasi daraltmadan etkilendi: {ad!r}"
        assert satir[0]["tax_number"] == MASKE_VKN
        assert satir[0]["phone"] == MASKE_TELEFON
        for ham in HAM_DEGERLER:
            assert ham not in yanit.text, f"arama yanıtında ham değer: {ham}"


@pytest.mark.parametrize("rol", MASKESIZ_TEST_ROLLERI)
def test_maskesiz_rol_vkn_ve_telefonla_arayabilir(istemci, rol_basliklari, tohum, rol):
    """Karşı hücre: maskesiz rol tam süzgeci korur ve ham satırı alır."""
    h = rol_basliklari[rol]
    mid = tohum["musteri_id"]
    for hassas in (HAM_VKN, HAM_TELEFON, HAM_EPOSTA):
        yanit = istemci.get("/api/customers", params={"q": hassas}, headers=h)
        assert yanit.status_code == 200, yanit.text
        satir = [s for s in yanit.json() if s["id"] == mid]
        assert satir, f"maskesiz rol icin suzgec daraldi: {hassas!r}"
        assert satir[0]["tax_number"] == HAM_VKN
        assert satir[0]["phone"] == HAM_TELEFON


@pytest.mark.parametrize("rol", MASKELI_ROLLER)
def test_sonek_yurutmesi_ilk_adimda_sifir_eslesme(istemci, rol_basliklari, tohum, rol):
    """Merceğin saldırısı, düzeltmeden sonra: İLK ADIMDA SIFIR eşleşme.

    Mercek d7b8930'da `q=1596 -> q=31596 -> ... -> q=4820731596` ile 80
    istekte tam VKN'yi çıkardı. Aynı yürütme burada `*******890` maskesine
    karşı koşturuluyor: on aday (`0890`..`9890`), on istek. Beklenen: hiçbir
    aday seçilen cariyi döndürmez, yani ikinci adıma geçilecek rakam yoktur
    ve yürütme başlayamaz.

    KONTROL HÜCRESİ aynı testte: `yonetici` için aynı adım TAM OLARAK bir
    adayı (`7890`) döndürür. Bu, adımın gerçek bir orakül olduğunu ve
    sıfırın "test yanlış soruyu soruyor"dan değil daraltmadan geldiğini
    kanıtlar.
    """
    mid = tohum["musteri_id"]
    maskeli = _sonek_ilk_adimi(
        istemci, rol_basliklari[rol], "/api/customers", VKN_SONEKI, mid
    )
    assert maskeli == [], f"sonek yürütmesi ilk adımda ilerledi: {maskeli}"

    kontrol = _sonek_ilk_adimi(
        istemci, rol_basliklari["yonetici"], "/api/customers", VKN_SONEKI, mid
    )
    assert kontrol == [HAM_VKN[-4:]], kontrol


def test_depo_tedarikci_aramasi_daraltildi(istemci, rol_basliklari, tohum):
    """`/api/suppliers?q=` de aynı orakülü taşıyordu; `depo` için aynı daraltma.

    `depo` `purchases` taşır ve tedarikçi listesine GİRER (`rapor` 403 alır,
    o yüzden yalnız `depo` ölçülüyor). Sorgu `finance.suppliers` içinde ayrı
    bir metin olduğu için ayrı ölçülmesi şarttır: `customers.py` düzeltilip
    `finance.py` unutulsa VKN ikinci uçtan aynı yürütmeyle çıkardı.
    """
    h_depo = rol_basliklari["depo"]
    h_yon = rol_basliklari["yonetici"]
    sid = tohum["tedarikci_id"]

    for hassas in (HAM_VKN, HAM_TELEFON, HAM_EPOSTA):
        assert sid not in _eslesen_kimlikler(istemci, h_depo, "/api/suppliers", hassas), hassas

    yanit = istemci.get("/api/suppliers", params={"q": "SEC3B"}, headers=h_depo)
    satir = [s for s in yanit.json() if s["id"] == sid]
    assert satir and satir[0]["tax_number"] == MASKE_VKN
    assert sid in _eslesen_kimlikler(istemci, h_depo, "/api/suppliers", "Yetkili")

    # Sonek yürütmesi: `depo` ilk adımda sıfır; `yonetici` kontrolü tek aday.
    assert _sonek_ilk_adimi(istemci, h_depo, "/api/suppliers", VKN_SONEKI, sid) == []
    assert _sonek_ilk_adimi(istemci, h_yon, "/api/suppliers", VKN_SONEKI, sid) == [
        HAM_VKN[-4:]
    ]
    yanit = istemci.get("/api/suppliers", params={"q": HAM_VKN}, headers=h_yon)
    satir = [s for s in yanit.json() if s["id"] == sid]
    assert satir and satir[0]["tax_number"] == HAM_VKN


@pytest.mark.parametrize("rol", MASKELI_ROLLER)
def test_global_arama_maskeli_rolde_telefon_ve_eposta_eslesmez(
    istemci, rol_basliklari, tohum, rol
):
    """`/api/search` de daraltıldı: maskeli rol için yalnız ad/yetkili adı.

    Mercek `/api/search`ün VKN'de eşleşmediğini ölçmüştü, ama telefon ve
    e-posta üzerinde `_like` İÇERİR kalıbıyla eşleşiyordu -- yani maskeli
    `05** *** ** 12`den `q=012`, `q=4512`, ... diye telefon geri çıkarılırdı.
    Aynı orakül, aynı çare. `subtitle` hâlâ maskeli değerden kurulur.
    """
    h = rol_basliklari[rol]
    mid = tohum["musteri_id"]
    for hassas in (HAM_TELEFON, HAM_EPOSTA, HAM_TELEFON[-6:]):
        assert mid not in _eslesen_kimlikler(istemci, h, "/api/search", hassas), hassas

    yanit = istemci.get("/api/search", params={"q": "SEC3B"}, headers=h)
    oge = [o for o in yanit.json()["items"] if o["type"] == "customer" and o["id"] == mid]
    assert oge and oge[0]["subtitle"] == MASKE_TELEFON
    assert mid in _eslesen_kimlikler(istemci, h, "/api/search", "Yetkili")

    # Telefon soneki yürütmesi ilk adımda sıfır; `yonetici` kontrolü eşleşir.
    assert _sonek_ilk_adimi(istemci, h, "/api/search", TELEFON_SONEKI, mid) == []
    assert mid in _eslesen_kimlikler(
        istemci, rol_basliklari["yonetici"], "/api/search", HAM_TELEFON
    )


@pytest.mark.parametrize("rol", ROLLER)
def test_global_arama_vkn_ile_hic_eslesmez(istemci, rol_basliklari, tohum, rol):
    """Merceğin ölçümü dondurulıyor: `/api/search` VKN'de HİÇBİR rol için eşleşmez."""
    assert tohum["musteri_id"] not in _eslesen_kimlikler(
        istemci, rol_basliklari[rol], "/api/search", HAM_VKN
    )


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


@pytest.mark.parametrize(
    "alan,gelen",
    [
        # Merceğin ölçtüğü değer: maskeden TEK HARF sapma, d7b8930'da YAZILIYORDU.
        ("tax_number", "*******891"),
        ("tax_number", "9999999999"),
        ("phone", "05329998877"),
        ("email", "baska@ornek.com"),
        ("address", "Yeni Mahalle No 1"),
    ],
)
def test_depo_maskeli_alana_HIC_yazamaz(
    istemci, rol_basliklari, admin_basliklari, tohum, alan, gelen
):
    """Maskeli rol maskeli alana HİÇ yazamaz: gelen ne olursa olsun SAKLANAN KAZANIR.

    d7b8930'daki kural "gelen == maske(saklanan) ise koru" idi ve sözleşme
    merceği bunu `tax_number="*******891"` ile deldi: 200 döndü, saklanan
    değer `"*******891"` oldu. Bu test o ölçümü ve dört hassas alanın
    tamamını koşuyor: `depo` alanı değiştirip nesnenin tamamını geri
    yazıyor, yanıt 200 (4xx DEĞİL -- form nesnenin tamamını gönderdiği için
    reddetmek adı bile düzenletmezdi), AMA `admin` gözüyle okunan saklanan
    değer DEĞİŞMEMİŞ; aynı PUT'taki ad değişikliği ise YAZILMIŞ, yani 200
    gerçek bir yazmadır, yutulmuş bir istek değil.
    """
    h_depo = rol_basliklari["depo"]
    sid = tohum["tedarikci_id"]

    kart = istemci.get(f"/api/suppliers/{sid}", headers=h_depo)
    assert kart.status_code == 200, kart.text
    govde = dict(kart.json()["supplier"])
    govde[alan] = gelen
    yeni_ad = f"SEC3B Tedarikçi ({alan} denemesi)"
    govde["name"] = yeni_ad

    yazma = istemci.put(f"/api/suppliers/{sid}", headers=h_depo, json=govde)
    assert yazma.status_code == 200, yazma.text

    sonra = istemci.get(f"/api/suppliers/{sid}", headers=admin_basliklari)
    assert sonra.status_code == 200, sonra.text
    varlik = sonra.json()["supplier"]
    assert varlik["name"] == yeni_ad, "maskelenmeyen alan yazılmadı; 200 sahte"
    assert varlik[alan] == {
        "tax_number": HAM_VKN, "phone": HAM_TELEFON,
        "email": HAM_EPOSTA, "address": HAM_ADRES,
    }[alan], f"maskeli rol {alan} alanını YAZDI: {varlik[alan]!r}"
    for anahtar, ham in (
        ("tax_number", HAM_VKN), ("phone", HAM_TELEFON),
        ("email", HAM_EPOSTA), ("address", HAM_ADRES),
    ):
        assert varlik[anahtar] == ham, anahtar


def test_yonetici_maskeli_alani_yazabilir(istemci, rol_basliklari, admin_basliklari):
    """Karşı hücre: maskesiz rol için `maskeyi_geri_al` hiçbir şey yapmaz.

    Kendi tedarikçisini açıyor ki tohum kaydının ham değerleri değişmesin ve
    diğer testler koşum sırasından bağımsız kalsın.
    """
    h = rol_basliklari["yonetici"]
    olustur = istemci.post(
        "/api/suppliers", headers=admin_basliklari,
        json=dict(TEDARIKCI_GOVDE, name="SEC3B Yönetici Yazma Tedarikçisi"),
    )
    assert olustur.status_code in (200, 201), olustur.text
    sid = olustur.json()["id"]

    kart = istemci.get(f"/api/suppliers/{sid}", headers=h)
    govde = dict(kart.json()["supplier"])
    assert govde["tax_number"] == HAM_VKN, "önkoşul: yonetici ham görür"
    govde["tax_number"] = "9876543210"
    govde["phone"] = "05329998877"
    yazma = istemci.put(f"/api/suppliers/{sid}", headers=h, json=govde)
    assert yazma.status_code == 200, yazma.text

    sonra = istemci.get(f"/api/suppliers/{sid}", headers=admin_basliklari).json()["supplier"]
    assert sonra["tax_number"] == "9876543210"
    assert sonra["phone"] == "05329998877"


def test_maskeyi_geri_al_birim():
    """`maskeyi_geri_al` kuralı: maskeli rol için SAKLANAN KAZANIR."""
    from app.alan_maskeleme import MASKELENEN_ALANLAR, maskeyi_geri_al

    mevcut = {"phone": HAM_TELEFON, "tax_number": HAM_VKN, "email": None, "name": "Eski"}

    # (a) Maskeli değer geri geldi -> saklanan korunur.
    gelen = {"phone": MASKE_TELEFON, "tax_number": MASKE_VKN, "name": "Yeni"}
    sonuc = maskeyi_geri_al(gelen, mevcut, "depo")
    assert sonuc["phone"] == HAM_TELEFON
    assert sonuc["tax_number"] == HAM_VKN
    assert sonuc["name"] == "Yeni"          # maskelenmeyen alan serbest

    # (b) BAMBAŞKA değer geldi -> YİNE saklanan. d7b8930'daki eşitlik
    #     kuralından fark tam burasıdır: "*******891" de, yeni bir numara da
    #     yazılmaz.
    gelen = {"phone": "05329998877", "tax_number": "*******891"}
    sonuc = maskeyi_geri_al(gelen, mevcut, "depo")
    assert sonuc["phone"] == HAM_TELEFON
    assert sonuc["tax_number"] == HAM_VKN

    # (c) Saklanan None ise gelen de None olur: maskeli rol boş alanı DOLDURAMAZ.
    assert maskeyi_geri_al({"email": "x@ornek.com"}, mevcut, "depo")["email"] is None

    # (d) Önekli anahtar (`customer_phone`) da tablodan çözülür.
    assert maskeyi_geri_al(
        {"customer_phone": "0"}, {"customer_phone": "1"}, "rapor"
    )["customer_phone"] == "1"

    # (e) Tablonun TAMAMI: MASKELENEN_ALANLAR'daki her anahtarda saklanan kazanır.
    for anahtar in MASKELENEN_ALANLAR:
        assert maskeyi_geri_al({anahtar: "gelen"}, {anahtar: "saklanan"}, "depo") == {
            anahtar: "saklanan"
        }, anahtar

    # (f) Maskesiz rol -> fonksiyon hiçbir şey yapmaz.
    gelen = {"phone": "05329998877"}
    assert maskeyi_geri_al(gelen, mevcut, "yonetici")["phone"] == "05329998877"

    # (g) Mevcut satır yoksa (yeni kayıt / 404 yolu) -> dokunulmaz.
    assert maskeyi_geri_al({"phone": MASKE_TELEFON}, None, "depo") == {
        "phone": MASKE_TELEFON
    }

    # (h) Girdi yerinde DEĞİŞMEZ.
    gelen = {"phone": "05329998877"}
    maskeyi_geri_al(gelen, mevcut, "depo")
    assert gelen == {"phone": "05329998877"}

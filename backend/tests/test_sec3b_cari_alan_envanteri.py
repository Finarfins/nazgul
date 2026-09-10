"""SEC-3b — cari HASSAS ALAN envanteri: hangi rota hangi alanı döndürüyor?

--- BU KAPI NE YAPIYOR ------------------------------------------------------

`test_sec3b_cari_maskeleme.py` maskelemenin BİLDİĞİMİZ uçlarda çalıştığını
GERÇEK isteklerle ölçüyor. Bu dosya farklı bir soruyu soruyor ve cevabı
listeyle değil TARAMAYLA veriyor:

    "`phone`/`email`/`address`/`tax_number` alanlarını döndüren BAŞKA bir
     rota var mı ve varsa kimin gördüğü DÜŞÜNÜLDÜ mü?"

İkisi ayrı kapıdır. Davranış testi yalnız yazdığım uçları korur; yarın
eklenen bir uç oradan geçmez, buradan geçer. SEC-3'ün kendi notu bu tuzağı
adıyla yazıyor: ``/api/field-safety`` ucu ``/api/field`` önekine düşüp
düşünülmemiş bir izne bağlanmıştı, tarla modülünde AYNI tuzağa İKİ KEZ
düşüldü.

--- ENVANTER NASIL ÜRETİLİYOR ----------------------------------------------

Elle YAZILMIYOR, ÜRETİLİYOR:

1. Uygulama gerçekten kuruluyor ve `app.routes` üzerinden HER GET/HEAD
   operasyonu geziliyor (yol şablonu + metot).
2. Her rotanın uç fonksiyonundan başlayarak, `app` paketi içinde çözülebilen
   çağrılar BFS ile takip ediliyor (derinlik sınırı `_DERINLIK`). Böylece
   `customers.py -> entity_detail.py -> crm.py` gibi dolaylı yüzeyler de
   görülüyor; alanın hangi DOSYADA seçildiği değil, hangi ROTADAN çıktığı
   önemlidir.
3. Ulaşılan fonksiyonların metin sabitlerinde (SQL) ve sözlük anahtarlarında
   maskelenen alan adları aranıyor.
4. `response_model` varsa modelin ÜRETEBİLECEĞİ alan adları çıkarılıyor.
   Model o alanı taşımıyorsa FastAPI onu serileştirmeden ÖNCE düşürür; rota
   `S` (süzülüyor) diye sınıflanır. Bu teorik değil ölçülmüş bir durumdur:
   `GET /api/work-orders/{id}/invoice` gövdesinde `customer_tax_number` ve
   `customer_address` SEÇİLİYOR ama `InvoiceParty` yalnız `id`+`name`
   taşıdığı için yanıta HİÇ çıkmıyor.
5. İznden ve `ROLE_PERMISSIONS`tan beş rol için sonuç hesaplanıyor.

--- SATIR BİÇİMİ ------------------------------------------------------------

    METOT YOL<TAB>izin<TAB>alanlar<TAB>yonetici,muhasebe,satis,depo,rapor

Hücreler:

* ``F`` — rol giriyor ve TAM veri görüyor.
* ``M`` — rol giriyor ve MASKELİ veri görüyor.
* ``D`` — rol iznden dolayı giremiyor (403).
* ``S`` — alan `response_model` tarafından süzülüyor; hiçbir rol görmüyor.

--- KAPI NASIL KIRILIR ------------------------------------------------------

Hassas alan döndüren YENİ bir rota eklenirse envanterde olmadığı için
``eksik`` listesinde çıkar ve mesaj hangi alanları taşıdığını yazar. Bir
rotanın izni ya da maskeleme durumu değişirse ``degisen`` listesinde çıkar.
Rota silinirse ``bayat`` listesinde çıkar. Üçü de "bu bilinçli mi?" sorusunu
incelemeye zorlar; hiçbiri kendiliğinden doğru cevabı bilmez.

--- BU KAPI NE İDDİA ETMEZ --------------------------------------------------

1. Maskelemenin DOĞRU olduğunu iddia etmez — onu davranış testi ölçüyor.
   Burada ölçülen, maskelemenin BAĞLANMIŞ olduğudur.
2. Statik tarama tam değildir: `getattr` ile çözülen dolaylı çağrılar ve
   çalışma anında kurulan SQL takip EDİLMEZ. Yani envanter YANLIŞ POZİTİF
   vermez ama teorik olarak yanlış negatif verebilir. Bu yüzden davranış
   testiyle BİRLİKTE durur, onun YERİNE değil.

--- 3.12 KARARLILIĞI --------------------------------------------------------

Üretim `ast.dump` KULLANMIYOR (parmak izi yok), yalnız düğüm TÜRLERİNE ve
metin sabitlerine bakıyor. 3.12'nin `type_params` alanı çıktıyı etkilemez.
Tüm kümeler yazılmadan önce `sorted()` ile sıralanır, sözlükler sıralı
gezilir; çıktı yorumlayıcı `hash` tohumundan BAĞIMSIZDIR (`PYTHONHASHSEED`
değişse bile aynı dosya üretilir).
"""
from __future__ import annotations

import ast
import inspect
import os
import tempfile
from pathlib import Path

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="sec3b-envanter-")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(_CALISMA_ALANI, "envanter.db").replace(os.sep, "/"),
)
os.environ.setdefault("SUNGUR_DATA_DIR", _CALISMA_ALANI)
os.environ.setdefault("AUTO_MIGRATE", "true")

PIN = Path(__file__).resolve().parent / "pins" / "cari_alan_envanteri.txt"

#: Çağrı takibinin derinliği. 3, ölçülen en uzun cari zincirini
#: (`routers/customers.py` -> `entity_detail.py` -> `crm.py`) kapsıyor.
_DERINLIK = 3

#: Bu rollerin sırası satır biçiminin parçasıdır; DEĞİŞTİRİLEMEZ.
ROLLER = ("yonetici", "muhasebe", "satis", "depo", "rapor")

#: Maskelemeyi "bağlanmış" sayan çağrı adları.
_MASKE_CAGRILARI = frozenset(
    {"maskele_cari", "maskele_cari_listesi", "cari_liste_satirlari"}
)


#: CARI OLMAYAN eslesmeler. Bu rotalar maskelenen alan ADLARINDAN birini
#: tasiyor ama tasidiklari veri bir CARIYE ait DEGIL; maskelemek yanlis olurdu.
#: Her satirin gerekcesi OLCULMUSTUR, varsayilmamistir. Matris hucreleri
#: `H` (haric) yazilir -- boylece pin onlari SINIFLANDIRILMIS sayar, ama
#: kimse onlara bakip "cari verisi tam gorunuyor" diye yanilmaz.
MUAFLAR: dict[str, str] = {
    # Kullanicinin KENDI e-postasinin dogrulanmasi; ortada cari yok.
    "GET /api/auth/verify-email":
        "kullanicinin kendi e-postasi (email_verification), cari degil",
    # `finance_accounts.iban` FIRMANIN KENDI kasa/banka hesabidir
    # (`finance_engine.py`); `customers`/`suppliers` tablolarinda iban YOKTUR.
    "GET /api/finance/accounts":
        "firmanin kendi banka hesabi (finance_accounts.iban), cari degil",
    "GET /api/finance/summary":
        "firmanin kendi banka hesabi (finance_accounts.iban), cari degil",
    # Eslestirme defterindeki numara KULLANICININ WhatsApp numarasidir.
    "GET /api/whatsapp/links":
        "kullanicinin WhatsApp numarasi (whatsapp eslestirme), cari degil",
}


def _yildiz_alanlari() -> frozenset[str]:
    """`SELECT * FROM customers/suppliers` durumunda ACILAN sutunlar.

    Envanterin DURUSTLUGU icin sart: `entity_detail` ve `statement` cari
    satirini `SELECT *` ile cekiyor, yani alan adlari kaynakta metin olarak
    HIC GECMIYOR. Yildizi genisletmeseydik `GET /api/customers/{id}` satiri
    yalnizca `email,phone` (CRM yetkililerinden gelen adlar) der, tasidigi
    `address` ve `tax_number`i SOYLEMEZDI -- pin dogru olur ama EKSIK okunur.
    Sutunlar semadan OKUNUR, elle yazilmaz.
    """
    from app.alan_maskeleme import MASKELENEN_ALANLAR
    from app.core_schema import customers, suppliers

    sutunlar = {c.name for c in customers.columns} | {c.name for c in suppliers.columns}
    return frozenset(sutunlar & set(MASKELENEN_ALANLAR))


def _yildiz_var_mi(metin: str, cari_yolu: bool) -> bool:
    """Metin bir CARI satirini `SELECT *` ile mi cekiyor?

    Iki bicim var ve ikisi de AYRI ele alinmak zorunda:

    1. Tablo adi ACIK: ``SELECT * FROM customers`` / ``suppliers``.
    2. Tablo adi INTERPOLE: `entity_detail._entity_row` ve `statement`
       ``FROM {config['entity_table']}`` yaziyor, yani metin sabiti
       ``"SELECT * FROM "`` diye BITIYOR ve tablo adi kaynakta HIC GECMIYOR.

    Ikinci bicim yalnizca rota ZATEN bir cari yolundaysa (`/api/customers`,
    `/api/suppliers`) sayilir. Bu sinir olculerek konuldu: ciplak
    ``SELECT *``i kosulsuz saymak, kendi tablosundan yildizla okuyan ONBES
    ilgisiz rotayi (hayvan, tarla, urun, push cihazlari) cari sizintisi gibi
    gosteriyordu -- pin gurultuye bogulur ve gurultulu pin okunmaz.
    """
    d = " ".join(metin.split()).upper()
    if "SELECT * FROM CUSTOMERS" in d or "SELECT * FROM SUPPLIERS" in d:
        return True
    return cari_yolu and d.endswith("SELECT * FROM")


def _maskelenen_alanlar() -> frozenset[str]:
    from app.alan_maskeleme import MASKELENEN_ALANLAR

    return frozenset(MASKELENEN_ALANLAR)


def _app_fonksiyonlari() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """`app` paketindeki her fonksiyonu ADIYLA indeksler.

    Ad çakışması olabilir (iki modülde aynı adlı fonksiyon). Bilinçli olarak
    HEPSİ tutulur ve tarama hepsini gezer: bu, taramayı GENİŞ tarafta
    yanıltır (fazladan alan görebilir), DAR tarafta değil. Güvenlik kapısında
    yanlış pozitif incelenir, yanlış negatif sızar.
    """
    import app

    kok = Path(app.__file__).resolve().parent
    tablo: dict[str, list] = {}
    for yol in sorted(kok.rglob("*.py")):
        try:
            agac = ast.parse(yol.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for dugum in ast.walk(agac):
            if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)):
                tablo.setdefault(dugum.name, []).append(dugum)
    return tablo


def _cagrilan_adlar(dugum: ast.AST) -> set[str]:
    adlar: set[str] = set()
    for alt in ast.walk(dugum):
        if not isinstance(alt, ast.Call):
            continue
        hedef = alt.func
        if isinstance(hedef, ast.Name):
            adlar.add(hedef.id)
        elif isinstance(hedef, ast.Attribute):
            adlar.add(hedef.attr)
    return adlar


def _metin_sabitleri(dugum: ast.AST) -> list[str]:
    """Fonksiyondaki tüm metin sabitleri (f-string parçaları DAHİL)."""
    parcalar: list[str] = []
    for alt in ast.walk(dugum):
        if isinstance(alt, ast.Constant) and isinstance(alt.value, str):
            parcalar.append(alt.value)
    return parcalar


def _alan_gecer_mi(metin: str, alan: str) -> bool:
    """Alan adı metinde SÖZCÜK olarak geçiyor mu?

    Sözcük sınırı elle kontrol ediliyor çünkü `customer_phone` da sayılmalı
    ama `telephone` sayılmamalı. Öncesindeki karakter `_` ya da harf/rakam
    ise ve o ön ek bir CARİ öneki değilse eşleşme reddedilir.
    """
    from app.alan_maskeleme import CARI_ONEKLERI

    bas = 0
    while True:
        i = metin.find(alan, bas)
        if i < 0:
            return False
        bas = i + 1
        son = i + len(alan)
        if son < len(metin) and (metin[son].isalnum() or metin[son] == "_"):
            continue
        if i == 0:
            return True
        onceki = metin[:i]
        if not (onceki[-1].isalnum() or onceki[-1] == "_"):
            return True
        if any(onceki.endswith(o) for o in CARI_ONEKLERI):
            return True
    return False


def _model_alanlari(model, derinlik: int = 4) -> set[str]:
    """Bir pydantic modelinin üretebileceği TÜM alan adları (iç içe dahil)."""
    from pydantic import BaseModel

    if derinlik <= 0 or not (isinstance(model, type) and issubclass(model, BaseModel)):
        return set()
    adlar: set[str] = set()
    for ad, alan in model.model_fields.items():
        adlar.add(ad)
        tur = alan.annotation
        for aday in (tur,) + tuple(getattr(tur, "__args__", ()) or ()):
            adlar |= _model_alanlari(aday, derinlik - 1)
    return adlar


def _rotalar():
    """(yol, metotlar, uc_fonksiyon, response_model) dizisi.

    FastAPI bu surumde `include_router` ile baglanan rotalari DOGRUDAN
    `app.routes` icine koymuyor: araya `_IncludedRouter` giriyor ve gercek
    rotalar `effective_route_contexts()` altinda tembel cozuluyor. Naif bir
    `isinstance(r, APIRoute)` dongusu bu uygulamada 63 rotanin yalnizca
    4'unu gorur (olculdu) -- yani envanter SESSIZCE bos kalir ve kapi hicbir
    sey korumaz. Asagidaki gezinti her iki bicimi de ele aliyor ve ic ice
    `_IncludedRouter` olasiligina karsi OZYINELI.

    Ic isim `_IncludedRouter` bir FastAPI AYRINTISIDIR; surum degisiminde
    kaybolabilir. Bu yuzden import KORUMALI ve `test_rota_gezintisi_bos_degil`
    gezintinin gercekten rota buldugunu AYRICA olcuyor: gezinti bozulursa
    envanter bos kalip yesil gorunmez, o test kirmizi yanar.
    """
    from fastapi.routing import APIRoute

    from app.main import app

    try:
        from fastapi.routing import _IncludedRouter
    except ImportError:  # pragma: no cover - eski/yeni FastAPI
        _IncludedRouter = ()

    def gez(rotalar, derinlik=6):
        if derinlik <= 0:  # pragma: no cover
            return
        for rota in rotalar:
            if isinstance(rota, APIRoute):
                yield rota.path, rota.methods or (), rota.endpoint, rota.response_model
            elif _IncludedRouter and isinstance(rota, _IncludedRouter):
                for ctx in rota.effective_route_contexts():
                    yield ctx.path, ctx.methods or (), ctx.endpoint, ctx.response_model

    yield from gez(app.routes)


def envanter_uret() -> list[str]:
    """Envanter satırlarını üretir; SIRALI ve yorumlayıcıdan bağımsız."""
    from app.auth import ROLE_PERMISSIONS, required_permission

    alanlar = _maskelenen_alanlar()
    tablo = _app_fonksiyonlari()
    satirlar: list[str] = []

    for yol, ham_metotlar, uc, response_model in _rotalar():
        metotlar = sorted(set(ham_metotlar) & {"GET", "HEAD"})
        if not metotlar:
            continue
        try:
            kaynak = inspect.getsource(uc)
            baslangic = ast.parse(inspect.cleandoc(kaynak))
        except (OSError, TypeError, SyntaxError, IndentationError):  # pragma: no cover
            continue

        # --- ulaşılabilir fonksiyon kümesi (BFS) ---------------------------
        gorulen: set[int] = set()
        dugumler = [baslangic]
        sinir = {baslangic}
        for _ in range(_DERINLIK):
            sonraki = set()
            for dugum in sinir:
                for ad in _cagrilan_adlar(dugum):
                    for hedef in tablo.get(ad, ()):
                        if id(hedef) in gorulen:
                            continue
                        gorulen.add(id(hedef))
                        sonraki.add(hedef)
            if not sonraki:
                break
            dugumler.extend(sonraki)
            sinir = sonraki

        bulunan: set[str] = set()
        maskeli = False
        yildiz = _yildiz_alanlari()
        cari_yolu = yol.startswith("/api/customers") or yol.startswith("/api/suppliers")
        for dugum in dugumler:
            for metin in _metin_sabitleri(dugum):
                for alan in alanlar:
                    if _alan_gecer_mi(metin, alan):
                        bulunan.add(alan)
                if _yildiz_var_mi(metin, cari_yolu):
                    bulunan |= yildiz
            if _MASKE_CAGRILARI & _cagrilan_adlar(dugum):
                maskeli = True
        if not bulunan:
            continue

        # --- response_model süzgeci ----------------------------------------
        model_adlari = _model_alanlari(response_model)
        suzuluyor = bool(response_model) and not (
            {a for a in bulunan if a in model_adlari}
            or {f"{o}{a}" for a in bulunan for o in ("customer_", "supplier_")}
            & model_adlari
        )

        for metot in metotlar:
            izin = required_permission(metot, yol)
            if f"{metot} {yol}" in MUAFLAR:
                satirlar.append(
                    "	".join(
                        (
                            f"{metot} {yol}",
                            izin,
                            ",".join(sorted(bulunan)),
                            ",".join("H" for _ in ROLLER),
                        )
                    )
                )
                continue
            hucreler = []
            for rol in ROLLER:
                izinler = ROLE_PERMISSIONS.get(rol, frozenset())
                if izin not in izinler:
                    hucreler.append("D")
                elif suzuluyor:
                    hucreler.append("S")
                elif maskeli and rol not in _maskesiz():
                    hucreler.append("M")
                else:
                    hucreler.append("F")
            satirlar.append(
                "\t".join(
                    (
                        f"{metot} {yol}",
                        izin,
                        ",".join(sorted(bulunan)),
                        ",".join(hucreler),
                    )
                )
            )
    return sorted(set(satirlar))


def _maskesiz() -> frozenset[str]:
    from app.alan_maskeleme import MASKESIZ_ROLLER

    return MASKESIZ_ROLLER


def _pin_oku() -> list[str]:
    if not PIN.exists():  # pragma: no cover
        return []
    return [
        s for s in PIN.read_text(encoding="utf-8").splitlines()
        if s.strip() and not s.startswith("#")
    ]


def test_cari_alan_envanteri_pinle_ayni():
    """Hassas alan döndüren HER GET rotası pinde SINIFLANDIRILMIŞ olmalı."""
    olculen = envanter_uret()
    pinli = _pin_oku()
    assert pinli, (
        "pins/cari_alan_envanteri.txt BOŞ ya da yok. Üretmek için:\n"
        "  python -m pytest backend/tests/test_sec3b_cari_alan_envanteri.py "
        "--envanteri-yaz"
    )

    o_anahtar = {s.split("\t")[0]: s for s in olculen}
    p_anahtar = {s.split("\t")[0]: s for s in pinli}

    eksik = sorted(set(o_anahtar) - set(p_anahtar))
    bayat = sorted(set(p_anahtar) - set(o_anahtar))
    degisen = sorted(
        f"{y}\n     pin: {p_anahtar[y]}\n  olculen: {o_anahtar[y]}"
        for y in set(o_anahtar) & set(p_anahtar)
        if o_anahtar[y] != p_anahtar[y]
    )

    assert not eksik, (
        "Cari HASSAS ALAN dondururen ve pinde OLMAYAN rota(lar):\n  "
        + "\n  ".join(o_anahtar[y] for y in eksik)
        + "\n\nBu rotanin hangi rolce gorulecegi DUSUNULDU mu? Maskeleme "
        "gerekiyorsa `entity_detail.cari_liste_satirlari` / "
        "`alan_maskeleme.maskele_cari` dikisinden gecirin; gerekmiyorsa "
        "gerekcesiyle pine ekleyin."
    )
    assert not bayat, (
        "Pinde olup artik hassas alan DONDURMEYEN rota(lar):\n  "
        + "\n  ".join(bayat)
        + "\n\nRota silindiyse ya da alan kaldirildiysa pinden de silin."
    )
    assert not degisen, (
        "Izin ya da maskeleme durumu DEGISEN rota(lar):\n  "
        + "\n  ".join(degisen)
    )


def test_envanter_uretimi_deterministik():
    """Aynı koşuda iki üretim BYTE BYTE aynı olmalı.

    Küme gezinmesi sıralanmasaydı `PYTHONHASHSEED` değiştikçe pin kayardı ve
    kapı, kod değişmeden kırmızı yanardı.
    """
    assert envanter_uret() == envanter_uret()


def test_pin_sirali_ve_tekil():
    pinli = _pin_oku()
    assert pinli == sorted(set(pinli)), (
        "pins/cari_alan_envanteri.txt SIRALI ve TEKİL olmalı."
    )


def test_maskelenen_alanlar_semayla_ayni():
    """Maskeleme tablosu, `customers`/`suppliers` şemasını GERÇEKTEN kapsıyor.

    Şemaya yeni bir iletişim/vergi sütunu eklenip maskeleme tablosuna
    yazılmazsa burası kırmızı yanar. Kapsam iddiası ŞEMADAN okunur, elle
    yazılmış bir listeden değil.
    """
    from app.alan_maskeleme import MASKELENEN_ALANLAR
    from app.core_schema import customers, suppliers

    hassas = {"phone", "email", "address", "tax_number"}
    for tablo in (customers, suppliers):
        sutunlar = {c.name for c in tablo.columns}
        eksik = (hassas & sutunlar) - set(MASKELENEN_ALANLAR)
        assert not eksik, (
            f"{tablo.name} tablosundaki {sorted(eksik)} sütunu maskeleme "
            "tablosunda YOK."
        )


def test_maskeli_rol_hicbir_cari_rotasinda_tam_veri_gormuyor():
    """SEC-3b'nin TEK CÜMLELİK güvenlik iddiası, envanterin TAMAMI üzerinden.

    Yukarıdaki pin testi "değişiklik bilinçli mi" diye sorar; bu test
    "sonuç doğru mu" diye sorar ve cevabı tek tek uçlardan değil, üretilen
    envanterin HER satırından okur. Yeni bir cari ucu maskelenmeden eklenirse
    pin testi `eksik` der, bu test de `F` hücresini ADIYLA gösterir.

    `H` (cari olmayan) ve `S` (response_model süzüyor) satırları dışarıdadır;
    ikisinin de gerekçesi `MUAFLAR` ile üretim kodundadır.
    """
    from app.alan_maskeleme import MASKESIZ_ROLLER

    maskeli_roller = [r for r in ROLLER if r not in MASKESIZ_ROLLER]
    assert maskeli_roller == ["depo", "rapor"], (
        "Ürün kararı değişmiş: maskeli roller artık " f"{maskeli_roller}. "
        "Matris değişebilir ama bu testin varsayımı güncellenmeli."
    )

    ihlaller = []
    for satir in envanter_uret():
        yol, _izin, alanlar, matris = satir.split("\t")
        hucreler = matris.split(",")
        for rol, hucre in zip(ROLLER, hucreler):
            if rol in MASKESIZ_ROLLER:
                continue
            if hucre == "F":
                ihlaller.append(f"{yol} -> {rol} TAM görüyor ({alanlar})")
    assert not ihlaller, (
        "Maskeli rol(ler) cari hassas alanları TAM görüyor:\n  "
        + "\n  ".join(sorted(ihlaller))
    )


def test_rota_gezintisi_bos_degil():
    """`_rotalar()` gerçekten rota buluyor mu?

    Bu test yokken kapı SESSİZCE çürüyebilirdi: FastAPI iç yapısı değişip
    `_IncludedRouter` kaybolsaydı gezinti boş dönerdi, envanter boş üretilirdi
    ve pin testi "hiçbir şey eksik değil" diyip YEŞİL kalırdı. Bu tam olarak
    ölçüldü -- naif `isinstance(r, APIRoute)` döngüsü 393 rotanın 4'ünü
    görüyordu. Alt sınır (200) kaba bilerek: rota sayısı normalde değişir,
    kapının koruduğu şey "gezinti tamamen koptu" durumudur.
    """
    rotalar = list(_rotalar())
    assert len(rotalar) > 200, (
        f"Rota gezintisi yalnız {len(rotalar)} rota buldu. FastAPI iç yapısı "
        "değişmiş olabilir; `_rotalar()` güncellenmeden envanter GÜVENİLİR "
        "DEĞİLDİR."
    )
    yollar = {y for y, _m, _u, _r in rotalar}
    assert "/api/customers" in yollar and "/api/suppliers" in yollar


if __name__ == "__main__":  # pragma: no cover - pini elle uretmek icin
    import sys

    if "--yaz" in sys.argv:
        satirlar = envanter_uret()
        PIN.write_text(
            "\n".join(satirlar) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"{PIN} yazildi: {len(satirlar)} satir")

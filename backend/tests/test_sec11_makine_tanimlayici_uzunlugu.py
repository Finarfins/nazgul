"""SEC-11: makine tanımlayıcıları sütun uzunluğuyla SINIRLI.

Konu: `app/schemas.py` (`MachineCreate`/`MachineUpdate`) ve `app/machines.py`.
GÖÇ YOK, ROTA YOK, ŞEMA DEĞİŞMEDİ — sınır YALNIZ giriş doğrulamasına eklendi.

--- ÖLÇÜLEN AÇIK ---------------------------------------------------------

`app/machines.py` `serial_number`ı `String(120)` tanımlar; `MachineCreate`te
o alanın `max_length`i YOKTU. SQLite `String(N)` uzunluğunu ZORLAMAZ, yani
sınır iki katmanın HİÇBİRİNDE durmuyordu. Bu depoda ÖLÇÜLDÜ:

  * `POST /api/machines` 5 000 karakterlik `serial_number` ile -> **201**,
    ve okunan değer 5 000 karakter (kırpılmadı, reddedilmedi).
  * O makineyi anan faturada `GET /api/invoices/{id}/pdf` ->
    `reportlab.platypus.doctemplate.LayoutError`, yani **500**.
    (`app/invoice_pdf.py`: seri no 60 mm'lik sabit genişlikli bir hücrede
    `Paragraph` gövdesine girer; hücre sayfadan uzun olunca reportlab
    yerleştiremez.) Veri KAYITLI olduğu için o faturanın PDF'i KALICI
    olarak kapanır — her istek yeniden 500 döner.
  * PostgreSQL'de aynı gövde PDF'e hiç varmadan INSERT'te `DataError` ->
    yine 500.

İkisi de 422 olması gereken yerde 500'dür: gövde İSTEMCİ hatasıdır.

Sınırın YETERLİ olduğu da ÖLÇÜLDÜ: faturanın o hücresi kesintisiz 1412
karaktere kadar yerleşiyor (boşluklu metinde 2001). 120'lik sınır kırılma
eşiğinin ~11 katı içeride kalır.

--- ÇARE -----------------------------------------------------------------

Sınırsız kalan HER isteğe bağlı tanımlayıcıya sütunla BİREBİR aynı
`Field(default=None, max_length=N)`. Deponun ZATEN kullandığı kalıp
(`base_unit`, `lot_code`): `Field(max_length=...)` + "after" kipli
`_clean_optional` doğrulayıcısı. `clean_optional_identifiers` DEĞİŞMEDİ —
kırpma ve "boş -> None" davranışı aynen duruyor.

`status` bu süpürmenin dışındadır ve bu bir atlama değil: o alan
`MACHINE_STATUSES` SAYIMIYLA sınırlıdır (en uzun üye 11 karakter,
sütun 40), yani serbest metin değildir. `created_at`/`updated_at` de
dışarıdadır: onları SUNUCU yazar, `MachineCreate`te YOKTURlar. Kapı bu iki
gerekçeyi de ADIYLA ölçer, susarak geçmez.

--- MUTASYON TABLOSU -----------------------------------------------------

  * `serial_number`dan `max_length=120`yi düşürmek
        -> `test_UC_121_karakterlik_seri_no_422` KIRMIZI (uç 201 döner)
           VE `test_KAPI_her_String_sutunu_MachineCreate_te_SINIRLI` KIRMIZI
           (sütun 120, alan sınırsız).
  * Herhangi bir tanımlayıcıdan `max_length`i düşürmek
        -> `test_KAPI_...SINIRLI` KIRMIZI (o alan adıyla raporlanır) ve o
           alanın `test_UC_HER_ALAN_sinirinda_201_bir_fazlasinda_422` ile
           `test_SEMA_sinirda_KABUL_bir_fazlasinda_RED` satırları KIRMIZI.
  * Sınırı sütundan BÜYÜK yazmak (ör. `max_length=200`)
        -> `test_KAPI_...SINIRLI` KIRMIZI (eşitlik aranıyor, "var mı" değil).
  * `app/machines.py`de bir sütunun uzunluğunu değiştirmek ya da YENİ bir
    `String(N)` sütunu eklemek
        -> `test_OLCULEN_sutun_uzunluklari_SABIT` KIRMIZI, ve yeni sütun
           `MachineCreate`te sınırsızsa `test_KAPI_...SINIRLI` de KIRMIZI.
           (Sessiz gerileme YOLU YOK: gelecekteki sütun kapıdan geçemez.)
  * KAPIYI ETKİSİZLEŞTİRMEK (süzgeci boşaltmak, `_max_length`i her zaman bir
    sayı döndürür yapmak)
        -> `test_KAPI_KENDINI_DOGRULUYOR_sentetik_sinirsizi_yakaliyor`
           KIRMIZI: tarayıcı sentetik bir sınırsız alanda İHLAL BULMAK
           zorunda.
  * `MachineUpdate`in `MachineCreate`ten türemesini bozmak
        -> `test_MachineUpdate_SINIRLARI_MIRAS_ALIYOR` KIRMIZI (PUT yolu
           POST ile aynı sınırı görmezdi).
  * `clean_optional_identifiers`ı düşürmek
        -> `test_KIRPMA_ve_BOS_None_DAVRANISI_DURUYOR` KIRMIZI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import annotated_types
import pytest
from sqlalchemy import String

from app.machines import machines
from app.schemas import MACHINE_STATUSES, MachineCreate, MachineUpdate

BACKEND = Path(__file__).resolve().parents[1]

#: `machines` tablosundaki HER `String(N)` sütunu ve N. Bu tablo ÖLÇÜMDÜR:
#: `app/machines.py` değişirse kapı kırmızıya döner ve sınırlar yeniden
#: gözden geçirilir.
OLCULEN_SUTUNLAR = {
    "brand": 160,
    "manufacturer": 160,
    "model": 160,
    "variant": 160,
    "serial_number": 120,
    "chassis_number": 120,
    "registration_number": 120,
    "engine_number": 120,
    "status": 40,
    "created_at": 40,
    "updated_at": 40,
}

#: SUNUCU yazar; istemci gövdesinde YOKTURlar.
SUNUCUNUN_YAZDIGI = frozenset({"created_at", "updated_at"})

#: Serbest metin DEĞİL: `MACHINE_STATUSES` sayımıyla sınırlı.
SAYIMLA_SINIRLI = frozenset({"status"})

#: İstemcinin serbest metin yazdığı, sınırı sütundan gelen alanlar.
SINIRLI_ALANLAR = {
    ad: n
    for ad, n in OLCULEN_SUTUNLAR.items()
    if ad not in SUNUCUNUN_YAZDIGI and ad not in SAYIMLA_SINIRLI
}

#: Bunlar isteğe bağlı (`None` olabilir) tanımlayıcılar — ÖLÇÜLEN açık tam
#: buradaydı. `brand`/`model` zorunludur ve sınırları ZATEN vardı.
ISTEGE_BAGLI_TANIMLAYICILAR = (
    "manufacturer",
    "variant",
    "serial_number",
    "chassis_number",
    "registration_number",
    "engine_number",
)


# ==========================================================================
# 1) KAPI — sütun uzunluğu <-> şema `max_length` eşlemesi
# ==========================================================================

def _string_sutunlari() -> dict[str, int]:
    """`machines`teki uzunluklu `String` sütunları.

    `Text` de `String` TÜREVİDİR ama `length`i `None`dur; `notes` böylece
    kendiliğinden dışarıda kalır — sınırsız serbest metin olması KASITLIDIR.
    """
    return {
        sutun.name: sutun.type.length
        for sutun in machines.columns
        if isinstance(sutun.type, String) and sutun.type.length
    }


def _max_length(model, ad: str) -> int | None:
    """`model`in `ad` alanındaki `max_length` (yoksa `None`)."""
    alan = model.model_fields.get(ad)
    if alan is None:
        return None
    for veri in alan.metadata:
        if isinstance(veri, annotated_types.MaxLen):
            return veri.max_length
    return None


def _sinirsizlar(model, sutunlar: dict[str, int]) -> list[str]:
    """Sütunu olup şemada eşit `max_length`i OLMAYAN alanlar."""
    ihlaller: list[str] = []
    for ad, n in sorted(sutunlar.items()):
        if ad in SUNUCUNUN_YAZDIGI or ad in SAYIMLA_SINIRLI:
            continue
        olcu = _max_length(model, ad)
        if olcu is None:
            ihlaller.append(
                f"machines.{ad} String({n}) -> {model.__name__}.{ad} "
                f"max_length YOK: SQLite sınırı ZORLAMAZ, gövde sınırsız girer."
            )
        elif olcu != n:
            ihlaller.append(
                f"machines.{ad} String({n}) -> {model.__name__}.{ad} "
                f"max_length={olcu}: sütunla EŞİT DEĞİL."
            )
    return ihlaller


def test_OLCULEN_sutun_uzunluklari_SABIT() -> None:
    """Ölçüm tablosunun kendisi çivili.

    MUTASYON: `app/machines.py`de bir uzunluğu değiştirmek ya da yeni bir
    `String(N)` sütunu eklemek burayı KIRMIZI yapar — sınırın yeniden
    ölçülmesi GEREKİR, sessizce kaymaz.
    """
    assert _string_sutunlari() == OLCULEN_SUTUNLAR


def test_KAPI_her_String_sutunu_MachineCreate_te_SINIRLI() -> None:
    """HER `String(N)` sütununun `MachineCreate` karşılığı `max_length == N`.

    Bu kapı gelecekteki bir sütunu da kapsar: `machines`e sınırsız bir alan
    eklenirse (ve muafiyet listelerine girmezse) burada ADIYLA düşer.
    """
    sutunlar = _string_sutunlari()
    # Süzgeç GERÇEKTEN bir şey görüyor: boş sözlük kapıyı sessizce yeşile
    # boyardı.
    assert len(sutunlar) >= 11, sutunlar
    assert set(SINIRLI_ALANLAR) <= set(sutunlar)

    ihlaller = _sinirsizlar(MachineCreate, sutunlar)
    assert not ihlaller, "\n".join(ihlaller)


def test_KAPI_KENDINI_DOGRULUYOR_sentetik_sinirsizi_yakaliyor() -> None:
    """Tarayıcı sınırsızı BULMAK, sınırlıyı GEÇİRMEK zorunda.

    Bu olmadan `test_KAPI_...SINIRLI` her zaman yeşil dönen boş bir döngüye
    indirgenebilirdi.
    """
    from pydantic import BaseModel, Field

    class Sinirsiz(BaseModel):
        serial_number: str | None = None

    class Yanlis(BaseModel):
        serial_number: str | None = Field(default=None, max_length=200)

    class Dogru(BaseModel):
        serial_number: str | None = Field(default=None, max_length=120)

    class Eksik(BaseModel):
        baska: str | None = None

    sutun = {"serial_number": 120}
    assert _sinirsizlar(Sinirsiz, sutun), "sınırsız alan YAKALANMADI"
    assert _sinirsizlar(Yanlis, sutun), "sütundan BÜYÜK sınır YAKALANMADI"
    assert _sinirsizlar(Eksik, sutun), "eksik alan YAKALANMADI"
    assert not _sinirsizlar(Dogru, sutun), "doğru sınır yanlışlıkla düştü"


def test_status_SAYIMLA_SINIRLI_ve_sutuna_SIGIYOR() -> None:
    """`status`ün muafiyeti ÖLÇÜLÜYOR, varsayılmıyor.

    MUTASYON: `MACHINE_STATUSES`e 40 karakterden uzun bir üye eklemek burayı
    KIRMIZI yapar — o an muafiyet geçersizdir, `max_length` gerekir.
    """
    from pydantic import ValidationError

    assert MACHINE_STATUSES
    en_uzun = max(len(durum) for durum in MACHINE_STATUSES)
    assert en_uzun <= OLCULEN_SUTUNLAR["status"], en_uzun
    # Ve alan gerçekten sayımla kapalı: sayım dışı bir değer reddediliyor.
    with pytest.raises(ValidationError):
        MachineCreate(brand="M", model="Mo", status="x" * 41)


def test_SUNUCUNUN_YAZDIGI_alanlar_MachineCreate_te_YOK() -> None:
    """`created_at`/`updated_at` istemci gövdesinde YOK — muafiyetin gerekçesi.

    MUTASYON: bunlardan birini `MachineCreate`e eklemek KIRMIZI yapar; o an
    istemciden gelir ve sınırlanması gerekir.
    """
    for ad in SUNUCUNUN_YAZDIGI:
        assert ad not in MachineCreate.model_fields, ad


def test_MachineUpdate_SINIRLARI_MIRAS_ALIYOR() -> None:
    """PUT yolu POST ile AYNI sınırı görüyor.

    MUTASYON: `MachineUpdate`i `BaseModel`den türetmek (mirası kesmek) burayı
    KIRMIZI yapar — PUT sınırsız kalırdı ve açık o kapıdan geri gelirdi.
    """
    assert issubclass(MachineUpdate, MachineCreate)
    assert not _sinirsizlar(MachineUpdate, _string_sutunlari())
    for ad, n in SINIRLI_ALANLAR.items():
        assert _max_length(MachineUpdate, ad) == n, ad


# ==========================================================================
# 2) ŞEMA DAVRANIŞI — sınırda kabul, bir fazlasında alan düzeyinde hata
# ==========================================================================

@pytest.mark.parametrize("ad,n", sorted(SINIRLI_ALANLAR.items()))
def test_SEMA_sinirda_KABUL_bir_fazlasinda_RED(ad: str, n: int) -> None:
    """Her sınırlı alan tek tek: N geçer, N+1 `ValidationError` üretir."""
    from pydantic import ValidationError

    govde = {"brand": "Marka", "model": "Model"}

    kabul = MachineCreate(**{**govde, ad: "S" * n})
    assert len(getattr(kabul, ad)) == n

    with pytest.raises(ValidationError) as hata:
        MachineCreate(**{**govde, ad: "S" * (n + 1)})
    # Hata ALAN DÜZEYİNDE: istemci hangi alanı kısaltacağını bilir.
    yerler = [tuple(h["loc"]) for h in hata.value.errors()]
    assert (ad,) in yerler, yerler
    turler = {h["type"] for h in hata.value.errors()}
    assert "string_too_long" in turler, turler


def test_KIRPMA_ve_BOS_None_DAVRANISI_DURUYOR() -> None:
    """`clean_optional_identifiers` DEĞİŞMEDİ: kırpma + "boş -> None".

    Sınır eklemek temizleyiciyi ETKİSİZLEŞTİRMEMELİ. `Field` kısıtı "after"
    kipli doğrulayıcıdan ÖNCE koşar (deponun `base_unit`/`lot_code`
    kalıbıyla aynı), yani sınır HAM uzunluğa bakar; temizleyici sonra
    kırpar.
    """
    m = MachineCreate(
        brand="Marka", model="Model",
        serial_number="  SR-1  ", chassis_number="", registration_number="   ",
    )
    assert m.serial_number == "SR-1"      # kırpıldı
    assert m.chassis_number is None       # boş -> None
    assert m.registration_number is None  # yalnız boşluk -> None
    # Sınıra dayanmış bir değer kırpmadan sonra da AYNEN duruyor.
    tam = MachineCreate(brand="M", model="Mo", serial_number="S" * 120)
    assert tam.serial_number == "S" * 120


def test_ISTEGE_BAGLI_TANIMLAYICILAR_hepsi_SINIRLI() -> None:
    """ÖLÇÜLEN açığın tam kümesi: altı alanın altısı da sınırlı."""
    for ad in ISTEGE_BAGLI_TANIMLAYICILAR:
        assert _max_length(MachineCreate, ad) == OLCULEN_SUTUNLAR[ad], ad


# ==========================================================================
# 3) UÇ — GERÇEK ŞEMADA 422/201 ve max sınırda PDF 200
# ==========================================================================
#
# `app.config.Settings` modül düzeyinde TEK KOPYA olduğu için taze
# `DATABASE_URL` ancak AYRI SÜREÇTE görülür (`test_pdf_paragraf_kacisi.py`
# ile AYNI gerekçe).

_UC_SMOKE = r'''
import json, os
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import MetaData, insert, select

from app.main import app
from app.config import settings
from app.db import engine

CIKTI = Path(os.environ["CIKTI_DIZINI"])
YENI_PAROLA = "Sinir!2026Sec11"

SINIRLAR = json.loads(os.environ["SINIRLAR"])


def admin_headers(client):
    # Bootstrap parolasi KODA GOMULMEZ: sabit bir giris, parolayi degistiren
    # baska bir smoke'tan sonra kosunca duserdi.
    adaylar = (settings.effective_bootstrap_admin_password, YENI_PAROLA, "admin123")
    for aday in adaylar:
        giris = client.post("/api/auth/login",
                            json={"username": "admin", "password": aday})
        if giris.status_code == 200:
            break
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    h = {"Authorization": "Bearer " + govde["access_token"],
         "X-Company-ID": str(govde["companies"][0]["id"])}
    if aday != YENI_PAROLA:
        ch = client.post("/api/auth/change-password", headers=h,
                         json={"current_password": aday, "new_password": YENI_PAROLA})
        assert ch.status_code == 200, ch.text
        h["Authorization"] = "Bearer " + ch.json()["access_token"]
    return h, govde


sonuc = {"alanlar": {}, "pdf": None, "asiri": None}

with TestClient(app) as client:
    h, govde = admin_headers(client)
    cid = int(govde["companies"][0]["id"])

    # --- (a) her sinirli alan: N -> 201, N+1 -> 422 (alan duzeyinde) -----
    for sira, (ad, n) in enumerate(sorted(SINIRLAR.items())):
        # Bir fazlasi: 422 ve hata ALAN adini tasiyor.
        fazla = client.post("/api/machines", headers=h, json={
            "brand": "Marka", "model": "Model", ad: "S" * (n + 1)})
        yerler = []
        if fazla.status_code == 422:
            ayrinti = fazla.json().get("detail")
            if isinstance(ayrinti, list):
                yerler = [list(p.get("loc", [])) for p in ayrinti]
        # Tam sinir: 201. Seri/sasi firma icinde TEKIL oldugundan her alan
        # icin AYRI bir deger uretiliyor (cakisma 409 olurdu).
        govde_tam = {"brand": "Marka-%d" % sira, "model": "Model"}
        govde_tam[ad] = ("A%d-" % sira).ljust(n, "S")[:n]
        tam = client.post("/api/machines", headers=h, json=govde_tam)
        sonuc["alanlar"][ad] = {
            "n": n,
            "fazla_durum": fazla.status_code,
            "fazla_yerler": yerler,
            "tam_durum": tam.status_code,
            "tam_uzunluk": (
                len((tam.json() or {}).get(ad) or "")
                if tam.status_code == 201 else None
            ),
            "tam_govde": None if tam.status_code == 201 else tam.text[:400],
        }

    # --- asiri gövde (OLCULEN 5 000) artik kapida durduruluyor ----------
    asiri = client.post("/api/machines", headers=h, json={
        "brand": "Marka", "model": "Model", "serial_number": "S" * 5000})
    sonuc["asiri"] = asiri.status_code

    # --- (b) max sinirdaki seri no ile PDF yolu -------------------------
    seri = "S" * SINIRLAR["serial_number"]
    makine = client.post("/api/machines", headers=h, json={
        "brand": "PDF Marka", "model": "PDF Model", "serial_number": seri})
    assert makine.status_code == 201, makine.text
    m = makine.json()
    assert len(m["serial_number"]) == SINIRLAR["serial_number"], len(m["serial_number"])

    md = MetaData(); md.reflect(bind=engine)
    faturalar = md.tables["invoices"]
    kullanicilar = md.tables["app_users"]
    simdi = datetime.now(timezone.utc)
    with engine.begin() as conn:
        admin_id = int(conn.execute(select(kullanicilar.c.id).where(
            kullanicilar.c.username == "admin")).scalar_one())
        fatura_id = int(conn.execute(insert(faturalar).values(
            company_id=cid, work_order_id=None,
            invoice_number="FTR-SEC11-1", invoice_type="INVOICE",
            status="ISSUED", currency="TRY", exchange_rate=1,
            customer_snapshot=json.dumps({"id": 1, "name": "Musteri"}),
            # Anlik goruntu MAKINENIN KENDISINDEN: sinir tuttuysa PDF de tutar.
            machine_snapshot=json.dumps({"brand": m["brand"], "model": m["model"],
                                         "serial_number": m["serial_number"]}),
            work_order_snapshot=json.dumps({"work_order_no": "IE-1",
                                            "status": "TAMAMLANDI"}),
            company_snapshot=json.dumps({"name": "Firma", "tax_number": "12"}),
            technician_snapshot=json.dumps({"display_name": "Teknisyen"}),
            warranty_snapshot=json.dumps({"type": "YOK"}),
            totals_snapshot=json.dumps({
                "labor": "100.00", "parts": "50.00", "grand_total": "150.00",
                "customer_amount": "150.00", "warranty_amount": "0.00",
                "global_discount": "0.00"}),
            tax_snapshot=json.dumps({"lines": []}),
            payment_terms=None, notes=None,
            created_by=admin_id, created_at=simdi, updated_at=simdi,
        )).inserted_primary_key[0])

    cevap = client.get("/api/invoices/%d/pdf" % fatura_id, headers=h)
    sonuc["pdf"] = {"durum": cevap.status_code,
                    "tur": cevap.headers.get("content-type", ""),
                    "uzunluk": len(cevap.content),
                    "pdf_mi": cevap.content[:5] == b"%PDF-"}

json.dump(sonuc, open(CIKTI / "uc.json", "w", encoding="utf-8"))
print("UC TAMAM")
'''


def _kos(betik: str, veritabani: Path, ciktilar: Path):
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = "sqlite:///" + veritabani.as_posix()
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    ortam["PYTHONUTF8"] = "1"
    ortam["CIKTI_DIZINI"] = str(ciktilar)
    ortam["SUNGUR_DATA_DIR"] = str(ciktilar / "veri")
    ortam["SINIRLAR"] = json.dumps(SINIRLI_ALANLAR)
    (ciktilar / "veri").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, "-c", betik], cwd=str(BACKEND), env=ortam,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )


@pytest.fixture(scope="module")
def _uc_sonucu(tmp_path_factory) -> dict:
    """Uç smoke'u BİR kez koşar; üç test aynı çıktıyı okur."""
    dizin = tmp_path_factory.mktemp("sec11")
    tamam = _kos(_UC_SMOKE, dizin / "uc.db", dizin)
    assert tamam.returncode == 0, (
        tamam.stdout[-6000:] + "\n" + tamam.stderr[-6000:]
    )
    return json.loads((dizin / "uc.json").read_text(encoding="utf-8"))


def test_UC_HER_ALAN_sinirinda_201_bir_fazlasinda_422(_uc_sonucu: dict) -> None:
    """Gerçek uçta her sınırlı alan: N -> 201, N+1 -> 422 (alan düzeyinde).

    MUTASYON: herhangi bir alandan `max_length`i düşürmek o alanın satırını
    KIRMIZI yapar — `fazla_durum` 201 olur, yani sütunu aşan gövde KABUL
    edilir.
    """
    alanlar = _uc_sonucu["alanlar"]
    assert set(alanlar) == set(SINIRLI_ALANLAR), alanlar.keys()
    for ad, olcu in sorted(alanlar.items()):
        assert olcu["fazla_durum"] == 422, (ad, olcu)
        # Hata O ALANI gösteriyor: "bir yerde 422 aldık" yetmez.
        assert any(ad in yer for yer in olcu["fazla_yerler"]), (ad, olcu)
        assert olcu["tam_durum"] == 201, (ad, olcu)
        assert olcu["tam_uzunluk"] == olcu["n"], (ad, olcu)


def test_UC_121_karakterlik_seri_no_422(_uc_sonucu: dict) -> None:
    """ÖLÇÜLEN açığın tam gövdesi: 121 karakterlik `serial_number`.

    MUTASYON: `serial_number`dan `max_length=120`yi düşürmek bu testi KIRMIZI
    yapar — uç 201 döner ve 121 karakter kaydedilir.
    """
    seri = _uc_sonucu["alanlar"]["serial_number"]
    assert seri["n"] == 120, seri
    assert seri["fazla_durum"] == 422, seri
    assert any("serial_number" in yer for yer in seri["fazla_yerler"]), seri
    assert seri["tam_durum"] == 201, seri
    # ÖLÇÜLEN 5 000'lik gövde de artık kapıda duruyor (eskiden 201'di).
    assert _uc_sonucu["asiri"] == 422, _uc_sonucu["asiri"]


def test_UC_max_sinirdaki_seri_no_ile_PDF_200(_uc_sonucu: dict) -> None:
    """Sınır YETERLİ: 120 karakterlik seri no ile PDF YERLEŞİYOR.

    Bu, sınırın yalnız "kapatıyor" değil "doğru yerde kapatıyor" olduğunu
    ölçer: 120 kabul edilir ve `GET /api/invoices/{id}/pdf` 200 döner.
    ÖLÇÜLDÜ: aynı hücre kesintisiz 1412 karaktere kadar yerleşir, yani
    120'lik sınır kırılma eşiğinin ~11 katı içeridedir.

    MUTASYON: sınırı 1500 gibi eşiğin ÜSTÜNE çıkarmak `test_KAPI_...SINIRLI`
    kapısını KIRMIZI yapar (sütunla eşit değil) — ve o sınır bu PDF yolunu
    yeniden 500'e açardı.
    """
    pdf = _uc_sonucu["pdf"]
    assert pdf["durum"] == 200, pdf
    assert pdf["tur"] == "application/pdf", pdf
    assert pdf["pdf_mi"], pdf
    assert pdf["uzunluk"] > 1000, pdf

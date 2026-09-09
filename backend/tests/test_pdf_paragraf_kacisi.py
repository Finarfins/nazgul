"""SEC-2: reportlab ``Paragraph`` gövdesine giren kiracı verisi KAÇIŞLI.

Konu: `app/invoice_pdf.py` (`build_invoice_pdf`) ve `app/routers/outputs.py`.
GÖÇ YOK, ROTA YOK.

--- ÖLÇÜLEN AÇIK ---------------------------------------------------------

`Paragraph`ın ilk argümanı DÜZ METİN DEĞİLDİR: reportlab onu bir mini-XML
belgesi olarak ayrıştırır (`reportlab.platypus.paraparser`). `invoice_pdf.py`
on bir kiracı dizgesini (firma adı/vergi no, müşteri adı, makine marka/model/
seri, iş emri no/durum, para birimi, garanti türü, ödeme koşulları, notlar,
teknisyen adı) bu gövdeye KAÇIŞSIZ gömüyordu. Üç ayrı sonuç ÖLÇÜLDÜ:

  * `<`  -> paraparser `ValueError` -> `GET /api/invoices/{id}/pdf` 500.
           Tek bir "Ac<me" müşteri adı o faturanın PDF'ini KALICI olarak
           kapatır: veri kayıtlı, uç her istekte patlar.
  * `<b>` -> yorumlanır; kiracı verisi belgenin BİÇİMİNİ ele geçirir.
  * `<img src='/etc/hosts'/>` -> reportlab `ImageReader` o yolu AÇAR.
           Bu bir sunucu dosyası okumasıdır (yerel dosya ifşası).

--- ÇARE -----------------------------------------------------------------

Gövdeye giren HER değer `xml.sax.saxutils.escape(str(...))` ile sarılır —
`app/routers/outputs.py`in ZATEN uyguladığı kalıp (`safe_note`,
`company_name`, `company_tax`). `escape` yalnız `&`, `<`, `>` çevirir;
değerler öğe METNİNE girdiği için (öznitelik değil) bu yeterlidir.

`app/labels.py` bu süpürmenin DIŞINDADIR ve bu bir atlama değil: orası
`Paragraph` KULLANMAZ, `canvas.drawString` ile çizer — düz metin API'si
mini-XML ayrıştırmaz, kaçış GEREKMEZ. Kapı bunu adıyla ölçüyor.

--- MUTASYON TABLOSU -----------------------------------------------------

  * `invoice_pdf.py`de müşteri adının `escape(...)` sarmalını düşürmek
        -> `test_KAPI_appte_kacissiz_Paragraph_YOK` KIRMIZI (file:line ile)
           VE `test_MUSTERI_ADINDA_kucuktur_PDF_URETIYOR` KIRMIZI
           (paraparser `ValueError`; PDF hiç üretilmez).
  * Notların `escape(...)` sarmalını düşürmek
        -> `test_KAPI_...` KIRMIZI ve `test_NOTLARDA_img_DOSYA_ACMIYOR`
           KIRMIZI (reportlab yolu açmaya kalkar).
  * KAPIYI ETKİSİZLEŞTİRMEK (tarayıcıyı her şeye güvenli dedirtmek, `escape`
    yerine herhangi bir çağrıyı kabul ettirmek, ya da dosya süzgecini
    boşaltmak)
        -> `test_KAPI_KENDINI_DOGRULUYOR_sentetik_ihlali_yakaliyor` KIRMIZI:
           tarayıcı sentetik bir kaçışsız örnekte İHLAL BULMAK ZORUNDA, ve
           süzgeç `invoice_pdf.py` ile `outputs.py`i GÖRMEK zorunda.
  * `outputs.py`de `safe_note = escape(...)` satırını `safe_note = str(...)`
    yapmak
        -> `test_KAPI_...` KIRMIZI (dolaylı ad artık güvenli sayılmaz).
"""
from __future__ import annotations

import ast
import io
import json
from pathlib import Path

import pdfplumber
import pytest

from app.invoice_pdf import build_invoice_pdf

BACKEND = Path(__file__).resolve().parents[1]
UYGULAMA = BACKEND / "app"


# ==========================================================================
# 1) KAPI — AST taraması: Paragraph gövdesindeki kaçışsız enterpolasyon
# ==========================================================================

def _escape_cagrisi(dugum: ast.AST) -> bool:
    """`escape(...)` / `saxutils.escape(...)` çağrısı mı?"""
    if not isinstance(dugum, ast.Call):
        return False
    islev = dugum.func
    if isinstance(islev, ast.Name):
        return islev.id == "escape"
    if isinstance(islev, ast.Attribute):
        return islev.attr == "escape"
    return False


#: Dizge ÜRETMEYEN, dolayısıyla mini-XML'e sızamayan çağrılar. Kasten DAR:
#: `str`/`format`/`join` burada YOKTUR — onlar kiracı verisini taşır.
SAYISAL_CAGRILAR = frozenset({"len", "abs", "round", "int", "float"})


def _sayisal_cagri(dugum: ast.AST) -> bool:
    return (
        isinstance(dugum, ast.Call)
        and isinstance(dugum.func, ast.Name)
        and dugum.func.id in SAYISAL_CAGRILAR
    )


def _guvenli_adlar(kapsam: ast.AST) -> set[str]:
    """Bu kapsamda `ad = escape(...)` ile bağlanmış adlar.

    `outputs.py` kalıbı: `safe_note = escape(str(head["note"]))` ve bir
    sonraki satırda `f"...{safe_note}"`. Ad DOĞRUDAN `escape(...)`tan
    gelmiyorsa güvenli SAYILMAZ.
    """
    adlar: set[str] = set()
    for dugum in ast.walk(kapsam):
        if isinstance(dugum, ast.Assign) and _escape_cagrisi(dugum.value):
            for hedef in dugum.targets:
                if isinstance(hedef, ast.Name):
                    adlar.add(hedef.id)
        elif isinstance(dugum, ast.AnnAssign) and dugum.value is not None \
                and _escape_cagrisi(dugum.value):
            if isinstance(dugum.target, ast.Name):
                adlar.add(dugum.target.id)
    return adlar


def _paragraph_cagrisi(dugum: ast.AST) -> bool:
    if not isinstance(dugum, ast.Call):
        return False
    islev = dugum.func
    return (isinstance(islev, ast.Name) and islev.id == "Paragraph") or (
        isinstance(islev, ast.Attribute) and islev.attr == "Paragraph"
    )


def _kacissiz_enterpolasyonlar(kaynak: str, ad: str) -> list[str]:
    """`Paragraph(<f-string | .format | %>)` içindeki kaçışsız değerler.

    Kapsam: ilk argümanı f-string olan, `.format(...)` ile kurulan ya da `%`
    ile biçimlenen her `Paragraph` çağrısı. Argümanı düz sabit ya da modül
    sabiti olan çağrılar enterpolasyon içermez; onlar için ayrı bir çivi var
    (`test_outputs_baslik_sabiti_MODUL_SABITI`).
    """
    agac = ast.parse(kaynak, filename=ad)
    ihlaller: list[str] = []

    # Kapsam başına güvenli adlar: her fonksiyon kendi `escape(...)`
    # bağlamalarını görür, modül düzeyi de kendi bağlamalarını.
    kapsamlar: list[ast.AST] = [agac]
    kapsamlar += [
        d for d in ast.walk(agac)
        if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    guvenli: dict[int, set[str]] = {id(k): _guvenli_adlar(k) for k in kapsamlar}
    kapsam_uyeleri = [(k, {id(x) for x in ast.walk(k)}) for k in kapsamlar]

    def kapsam_adlari(dugum: ast.AST) -> set[str]:
        birlesik: set[str] = set()
        for kapsam, uyeler in kapsam_uyeleri:
            if id(dugum) in uyeler:
                birlesik |= guvenli[id(kapsam)]
        return birlesik

    for dugum in ast.walk(agac):
        if not _paragraph_cagrisi(dugum) or not dugum.args:
            continue
        arg = dugum.args[0]

        # `.format(...)` / `%` — kaçış yeri YOK, doğrudan ihlal.
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) \
                and arg.func.attr == "format":
            ihlaller.append(
                f"{ad}:{arg.lineno}: Paragraph(...) gövdesi .format(...) ile "
                f"kuruluyor — kaçış YOK. Çare: escape(str(...))."
            )
            continue
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod):
            ihlaller.append(
                f"{ad}:{arg.lineno}: Paragraph(...) gövdesi % ile "
                f"biçimleniyor — kaçış YOK. Çare: escape(str(...))."
            )
            continue

        if not isinstance(arg, ast.JoinedStr):
            continue

        adlar = kapsam_adlari(dugum)
        for parca in arg.values:
            if not isinstance(parca, ast.FormattedValue):
                continue
            deger = parca.value
            if _escape_cagrisi(deger) or _sayisal_cagri(deger):
                continue
            if isinstance(deger, ast.Name) and deger.id in adlar:
                continue
            if isinstance(deger, ast.Constant):
                continue
            ihlaller.append(
                f"{ad}:{parca.lineno}: Paragraph(...) f-string'inde KAÇIŞSIZ "
                f"`{ast.unparse(deger)}` — reportlab gövdeyi mini-XML olarak "
                f"ayrıştırır. Çare: escape(str(...))."
            )
    return ihlaller


def _paragraph_dosyalari() -> list[Path]:
    return sorted(
        yol for yol in UYGULAMA.rglob("*.py")
        if "Paragraph(" in yol.read_text(encoding="utf-8")
    )


def test_KAPI_appte_kacissiz_Paragraph_YOK() -> None:
    """`app/**` içindeki HER `Paragraph(...)` f-string'i kaçışlı olmalı."""
    dosyalar = _paragraph_dosyalari()
    # Süzgeç GERÇEKTEN bir şey buluyor: boş küme kapıyı sessizce yeşile
    # boyardı.
    adlar = {yol.name for yol in dosyalar}
    assert {"invoice_pdf.py", "outputs.py"} <= adlar, adlar

    ihlaller: list[str] = []
    for yol in dosyalar:
        ihlaller += _kacissiz_enterpolasyonlar(
            yol.read_text(encoding="utf-8"),
            str(yol.relative_to(BACKEND)).replace("\\", "/"),
        )
    assert not ihlaller, "\n".join(ihlaller)


def test_KAPI_KENDINI_DOGRULUYOR_sentetik_ihlali_yakaliyor() -> None:
    """Tarayıcı kaçışsızı BULMAK, kaçışlıyı GEÇİRMEK zorunda.

    Bu kapı olmadan `test_KAPI_appte_...` her zaman yeşil dönen boş bir
    döngüye indirgenebilirdi.
    """
    kirli = "Paragraph(f\"<b>{musteri['name']}</b>\", stil)"
    assert _kacissiz_enterpolasyonlar(kirli, "sentetik.py")

    temiz = "Paragraph(f\"<b>{escape(str(musteri['name']))}</b>\", stil)"
    assert not _kacissiz_enterpolasyonlar(temiz, "sentetik.py")

    # Dolaylı ad: YALNIZ `escape(...)`tan gelirse güvenli.
    dolayli_temiz = (
        "def f():\n"
        "    s = escape(str(x))\n"
        '    Paragraph(f"{s}", stil)\n'
    )
    assert not _kacissiz_enterpolasyonlar(dolayli_temiz, "sentetik.py")
    dolayli_kirli = (
        "def f():\n"
        "    s = str(x)\n"
        '    Paragraph(f"{s}", stil)\n'
    )
    assert _kacissiz_enterpolasyonlar(dolayli_kirli, "sentetik.py")

    # `.format` ve `%` de yakalanıyor.
    assert _kacissiz_enterpolasyonlar('Paragraph("{}".format(ad), s)', "x.py")
    assert _kacissiz_enterpolasyonlar('Paragraph("%s" % ad, s)', "x.py")


def test_labels_Paragraph_KULLANMIYOR_drawString_ile_ciziyor() -> None:
    """`app/labels.py` süpürmenin dışında — ve NEDEN dışında.

    MUTASYON: `labels.py`e bir `Paragraph(...)` eklemek bu testi KIRMIZI
    yapar; o dosya o an kaçış sözleşmesine girer.
    """
    kaynak = (UYGULAMA / "labels.py").read_text(encoding="utf-8")
    assert "Paragraph(" not in kaynak
    assert "drawString(" in kaynak


def test_outputs_baslik_sabiti_MODUL_SABITI() -> None:
    """`Paragraph(config["title"], ...)` enterpolasyon değil, modül sabiti.

    Tarayıcı yalnız f-string/format gövdelerine bakar; bu tek "sabit
    olmayan görünümlü" argümanı ADIYLA çivileyerek boşluğu kapatıyoruz.
    """
    kaynak = (UYGULAMA / "routers" / "outputs.py").read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    sabitler = {
        deger.value
        for sozluk in ast.walk(agac) if isinstance(sozluk, ast.Dict)
        for anahtar, deger in zip(sozluk.keys, sozluk.values)
        if isinstance(anahtar, ast.Constant) and anahtar.value == "title"
        and isinstance(deger, ast.Constant)
    }
    assert sabitler, "config['title'] artık düz sabit değil — kaçış gerekir"
    for metin in sabitler:
        assert "<" not in metin and "&" not in metin, metin


# ==========================================================================
# 2) DAVRANIŞ — düşmanca kiracı verisiyle PDF ÜRETİLİYOR ve METİN DÜZ
# ==========================================================================

def _fatura(**degisiklik) -> dict:
    fatura = {
        "invoice_number": "FTR-2026-0001",
        "currency": "TRY",
        "created_at": "2026-09-09 10:00:00",
        "company_snapshot": json.dumps(
            {"name": "Harman", "tax_number": "1234567890"}
        ),
        "customer_snapshot": json.dumps({"name": "Normal Musteri"}),
        "machine_snapshot": json.dumps(
            {"brand": "Marka", "model": "Model", "serial_number": "SR-1"}
        ),
        "work_order_snapshot": json.dumps(
            {"work_order_no": "IE-1", "status": "TAMAMLANDI"}
        ),
        "totals_snapshot": json.dumps({
            "labor": "100.00", "parts": "50.00", "grand_total": "150.00",
            "customer_amount": "150.00", "warranty_amount": "0.00",
            "global_discount": "0.00",
        }),
        "warranty_snapshot": json.dumps({"type": "YOK"}),
        "technician_snapshot": json.dumps({"display_name": "Teknisyen"}),
        "payment_terms": None,
        "notes": None,
    }
    fatura.update(degisiklik)
    return fatura


def _metin_pdf(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as belge:
        return "\n".join(sayfa.extract_text() or "" for sayfa in belge.pages)


def test_MUSTERI_ADINDA_kucuktur_PDF_URETIYOR() -> None:
    """ÖLÇÜLEN 500: kaçış olmadan paraparser `ValueError` atardı.

    MUTASYON: `customer['name']`ten `escape(...)` sarmalını düşürmek bu
    testi KIRMIZI yapar (PDF hiç üretilmez).
    """
    pdf = build_invoice_pdf(
        _fatura(customer_snapshot=json.dumps({"name": "Ac<me & Söhne"})), []
    )
    assert pdf.startswith(b"%PDF-")
    metin = _metin_pdf(pdf)
    # Metin DÜZ: `<` ve `&` etiket/varlık değil, harf olarak duruyor.
    assert "Ac<me" in metin, metin
    assert "& Söhne" in metin, metin


def test_NOTLARDA_img_DOSYA_ACMIYOR() -> None:
    """ÖLÇÜLEN yerel dosya ifşası: reportlab `<img src=...>`i AÇAR.

    MUTASYON: `notes`tan `escape(...)` sarmalını düşürmek bu testi KIRMIZI
    yapar — reportlab `/etc/hosts` yolunu açmaya kalkar.
    """
    pdf = build_invoice_pdf(_fatura(notes="<img src='/etc/hosts'/>"), [])
    assert pdf.startswith(b"%PDF-")
    metin = _metin_pdf(pdf)
    assert "/etc/hosts" in metin, metin
    # Etiket HARF olarak duruyor; yorumlansaydı metinde HİÇ görünmezdi.
    assert "<img" in metin, metin
    # Belgede gömülü tek görüntü QR koddur; `<img>` yorumlansaydı İKİ olurdu.
    with pdfplumber.open(io.BytesIO(pdf)) as belge:
        goruntuler = sum(len(sayfa.images) for sayfa in belge.pages)
    assert goruntuler == 1, goruntuler


def test_KALIN_ETIKET_YORUMLANMIYOR_harfi_harfine() -> None:
    """MUTASYON: `payment_terms`ten `escape(...)`i düşürmek KIRMIZI."""
    pdf = build_invoice_pdf(_fatura(payment_terms="<b>x</b>"), [])
    metin = _metin_pdf(pdf)
    assert "<b>x</b>" in metin, metin


@pytest.mark.parametrize(
    "alan,govde",
    [
        ("company_snapshot", {"name": "F<irma", "tax_number": "1<2"}),
        ("machine_snapshot",
         {"brand": "M<arka", "model": "M<odel", "serial_number": "S<1"}),
        ("work_order_snapshot", {"work_order_no": "IE<1", "status": "A<B"}),
        ("warranty_snapshot", {"type": "TAM<GARANTI"}),
        ("technician_snapshot", {"display_name": "Ali<Veli"}),
    ],
)
def test_HER_ANLIK_GORUNTU_ALANI_kucuktur_ISARETINI_TASIYABILIR(
    alan, govde
) -> None:
    """Onbir alanın hepsi tek tek: biri kaçışsız kalırsa o satır KIRMIZI."""
    pdf = build_invoice_pdf(_fatura(**{alan: json.dumps(govde)}), [])
    assert pdf.startswith(b"%PDF-")


def test_NULL_ALANLAR_None_YAZMIYOR_ama_SIFIR_KAYBOLMUYOR() -> None:
    """Anlık görüntüde açık ``null`` -> BOŞ; ``0`` -> "0".

    İKİ AYRI SESSİZ YANLIŞ tek testte: (a) ``str(None)`` faturaya "None"
    basardı — Türkçe bir belgede bir Python artığı; (b) düzeltmeyi ``or ""``
    ile yapmak sıfırı yutardı, yani sıfır tutarlı bir indirim ya da sıfır
    garanti payı BOŞ görünürdü ki bu "bilinmiyor" gibi okunur.

    MUTASYON: ``_metin``i ``lambda v: str(v or "")`` yapmak -> SIFIR iddiaları
    KIRMIZI. ``_metin``i düşürüp ``str``e dönmek -> "None" iddiaları KIRMIZI.
    """
    pdf = build_invoice_pdf(
        _fatura(
            customer_snapshot=json.dumps({"name": None}),
            machine_snapshot=json.dumps(
                {"brand": None, "model": None, "serial_number": None}
            ),
            work_order_snapshot=json.dumps({"work_order_no": None, "status": None}),
            warranty_snapshot=json.dumps({"type": None}),
            totals_snapshot=json.dumps({
                "labor": "0", "parts": "0", "grand_total": 0,
                "customer_amount": 0, "warranty_amount": 0,
                "global_discount": 0,
            }),
        ),
        [],
    )
    metin = _metin_pdf(pdf)
    assert "None" not in metin, metin
    # SIFIRLAR DURUYOR: hem tam sayı 0 hem `money()` çıktısı olan Decimal.
    assert "Global İndirim: 0" in metin, metin
    assert "Garanti: 0" in metin, metin


def test_METIN_YARDIMCISI_SIFIRI_KORUYOR_None_I_BOSALTIYOR() -> None:
    """Yardımcının kendisi ADIYLA ölçülüyor — davranış testinin dayanağı."""
    from decimal import Decimal

    from app.invoice_pdf import _metin

    assert _metin(None) == ""
    assert _metin(0) == "0"
    assert _metin(Decimal("0.00")) == "0.00"
    assert _metin("") == ""
    assert _metin("Ac<me") == "Ac<me"


def test_PARA_BIRIMI_kucuktur_ISARETINI_TASIYABILIR() -> None:
    """`currency` de gövdeye giriyor (İKİ kez) — o da kaçışlı."""
    pdf = build_invoice_pdf(_fatura(currency="T<Y"), [])
    assert pdf.startswith(b"%PDF-")


# ==========================================================================
# 3) UÇ — GERÇEK ŞEMADA `GET /api/invoices/{id}/pdf` 500 DEĞİL 200
# ==========================================================================
#
# Bölüm 2 `build_invoice_pdf`i doğrudan çağırır; ÖLÇÜLEN kusur ise HTTP
# ucunda görülmüştü. Uç, ham satırı `SELECT *` ile okuyup doğrudan bu
# fonksiyona verir, yani kaçışsız bir anlık görüntü 500 üretirdi. Bu smoke
# o yolu baştan sona koşar. `app.config.Settings` modül düzeyinde TEK
# KOPYA olduğu için taze `DATABASE_URL` ancak AYRI SÜREÇTE görülür
# (`test_kiraci_imha.py` ile AYNI gerekçe).

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
YENI_PAROLA = "Kacis!2026Pdf"

# Kiraci verisi olarak gelen DUSMANCA degerler.
MUSTERI = "Ac<me & Sohne"
NOTLAR = "<img src='/etc/hosts'/>"
KOSULLAR = "<b>x</b>"


def admin_headers(client):
    # Bootstrap parolasi KODA GOMULMEZ: sabit `admin123` yazan bir giris,
    # parolayi degistiren baska bir smoke'tan sonra kosunca duserdi.
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


with TestClient(app) as client:
    h, govde = admin_headers(client)
    cid = int(govde["companies"][0]["id"])

    md = MetaData(); md.reflect(bind=engine)
    faturalar = md.tables["invoices"]
    kullanicilar = md.tables["app_users"]
    simdi = datetime.now(timezone.utc)
    with engine.begin() as conn:
        admin_id = int(conn.execute(select(kullanicilar.c.id).where(
            kullanicilar.c.username == "admin")).scalar_one())
        fatura_id = int(conn.execute(insert(faturalar).values(
            company_id=cid, work_order_id=None,
            invoice_number="FTR-KACIS-1", invoice_type="INVOICE",
            status="ISSUED", currency="TRY", exchange_rate=1,
            customer_snapshot=json.dumps({"id": 1, "name": MUSTERI}),
            machine_snapshot=json.dumps({"brand": "M<arka", "model": "Model",
                                         "serial_number": "S<1"}),
            work_order_snapshot=json.dumps({"work_order_no": "IE<1",
                                            "status": "TAMAMLANDI"}),
            company_snapshot=json.dumps({"name": "F<irma", "tax_number": "1<2"}),
            technician_snapshot=json.dumps({"display_name": "Ali<Veli"}),
            warranty_snapshot=json.dumps({"type": "TAM<GARANTI"}),
            totals_snapshot=json.dumps({
                "labor": "100.00", "parts": "50.00", "grand_total": "150.00",
                "customer_amount": "150.00", "warranty_amount": "0.00",
                "global_discount": "0.00"}),
            tax_snapshot=json.dumps({"lines": []}),
            payment_terms=KOSULLAR, notes=NOTLAR,
            created_by=admin_id, created_at=simdi, updated_at=simdi,
        )).inserted_primary_key[0])

    cevap = client.get("/api/invoices/%d/pdf" % fatura_id, headers=h)
    (CIKTI / "uc.pdf").write_bytes(cevap.content if cevap.status_code == 200 else b"")
    json.dump({"durum": cevap.status_code,
               "tur": cevap.headers.get("content-type", ""),
               "uzunluk": len(cevap.content)},
              open(CIKTI / "uc.json", "w", encoding="utf-8"))
    print("UC TAMAM", cevap.status_code)
'''


def _kos(betik: str, veritabani: Path, ciktilar: Path):
    import os
    import subprocess
    import sys

    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    ortam["CIKTI_DIZINI"] = str(ciktilar)
    ortam["SUNGUR_DATA_DIR"] = str(ciktilar / "veri")
    (ciktilar / "veri").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, "-c", betik], cwd=BACKEND, env=ortam,
        capture_output=True, text=True, encoding="utf-8", timeout=900,
    )


def test_UC_dusmanca_anlik_goruntuyle_PDF_200(tmp_path: Path) -> None:
    """`GET /api/invoices/{id}/pdf` — ÖLÇÜLEN 500'ün ta kendisi.

    MUTASYON: `invoice_pdf.py`deki HERHANGİ bir `escape(...)` sarmalını
    düşürmek bu testi KIRMIZI yapar: uç 500 döner ve o faturanın PDF'i
    KALICI olarak kapanır.
    """
    tamam = _kos(_UC_SMOKE, tmp_path / "uc.db", tmp_path)
    assert tamam.returncode == 0, (
        tamam.stdout[-6000:] + "\n" + tamam.stderr[-6000:]
    )
    sonuc = json.loads((tmp_path / "uc.json").read_text(encoding="utf-8"))
    assert sonuc["durum"] == 200, sonuc
    assert sonuc["tur"] == "application/pdf", sonuc

    metin = _metin_pdf((tmp_path / "uc.pdf").read_bytes())
    # Düşmanca değerler DÜZ METİN olarak duruyor: `<` harf, `<img>` etiket
    # değil, `<b>` biçim değil.
    assert "Ac<me & Sohne" in metin, metin
    assert "/etc/hosts" in metin and "<img" in metin, metin
    assert "<b>x</b>" in metin, metin
    assert "F<irma" in metin and "Ali<Veli" in metin, metin

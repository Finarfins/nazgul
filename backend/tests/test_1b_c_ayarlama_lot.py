"""AYARLAMA VE SAYIM PARTİ FARKINDA (FAZ 1B-C) — defterin İKİNCİ ve ÜÇÜNCÜ çağıranı.

Konu: `app/parti_defteri.py` (1B-A'nın yazıcısının TAŞINDIĞI yer),
`app/core_schema.py` (`stock_movements.lot_id` BİLDİRİMİ),
`alembic/versions/20260903_0067_parti_skt.py` (sütun/kısıt koşulunun İKİYE
AYRILMASI), `app/routers/products.py` (`POST /api/products/{id}/stock`),
`app/routers/warehouse_counts.py` (`POST /api/warehouses/counts`),
`app/schemas.py` (`StockAdjust`).

GÖÇ YOK. `stock_movements.lot_id` 0067'den beri veritabanında VAR; bu dilim
yeni bir sütun getirmiyor, VAR OLANI yazılabilir yapıyor.

--- BU DİLİM NEYİ KAPATIYOR ------------------------------------------------

1B-A alışın parti AÇMASINI getirdi ve defterin TEK yazıcısı
`app/routers/transactions.py` idi. Ama stok defterine yazan BAŞKA yollar da
vardı ve onlar partiyi GÖRMÜYORDU:

  * `POST /api/products/{id}/stock` (elle stok düzeltmesi)
  * `POST /api/warehouses/counts` (fiziksel sayım)

Sonuç ÖLÇÜLEBİLİR bir yalandı: bir operatör partili malı elle düşürdüğünde
`products.stock` azalıyor, `product_lots.quantity` AZALMIYORDU. İki defter
sessizce ayrışıyor ve geri çağırma sorusu ("bu partiden ne kadar çıktı")
YANLIŞ cevap veriyordu.

--- 0067'NİN KARARI TERSİNE ÇEVRİLDİ: NE ÖLÇÜLDÜ ---------------------------

0067 `core_schema.stock_movements`a DOKUNMAMIŞTI ve gerekçesi SAYISAL GÖÇ
MUTABAKAT KAPISIYDI (`app/numeric_manifest.py`, `app/reconciliation.py`).
Bu dilim o gerekçeyi VARSAYMADI, ÖLÇTÜ ve İKİ AYRI SONUÇ buldu:

1. SAYISAL KAPI İÇİN GEREKÇE GEÇERSİZDİR. `iter_numeric_columns()` elle
   yazılmış İKİ SÖZLÜKTEN (`MONEY_COLUMNS`, `QUANTITY_COLUMNS`) türer ve
   `metadata`yı HİÇ OKUMAZ. `lot_id` `Integer`dır, ne para ne miktar
   ailesindendir, iki sözlüğün HİÇBİRİNDE yoktur — bildirimden önce de sonra
   da toplam 90 sütun. `capture_numeric_snapshot` onu GÖRMEZ. Bu, aşağıda
   `test_lot_id_SAYISAL_MANIFESTOYA_GIRMEZ_olculdu` ile ÇİVİLİ.

2. AMA GERÇEK BİR BEDEL VARDI VE KİMSE ONU ÖLÇMEMİŞTİ. 0067 `lot_id`i
   `if "lot_id" not in ...` koşuluyla ekliyordu ve BİLEŞİK YABANCI ANAHTARI
   (`fk_stock_movements_lot_same_company`) AYNI koşulun İÇİNDE kuruyordu.
   Sütun `core_schema`da bildirilince TAZE veritabanında onu artık
   `20260712_0000`ın `create_all`ı açıyor, koşul YANLIŞ oluyor ve FK HİÇ
   KURULMUYORDU — sessizce. Ölçüm: taze SQLite + `alembic upgrade head`,
   bildirimden ÖNCE `stock_movements` üzerinde 1 yabancı anahtar, tek
   koşulla 0. Çapraz kiracı `lot_id` artık veritabanı seviyesinde
   engellenmezdi ve hiçbir kırmızı bunu söylemezdi.

   ÇARE GÖÇ EKLEMEK DEĞİL, 0067'NİN KOŞULUNU İKİYE AYIRMAKTIR (sütun ayrı
   sorulur, kısıt ayrı). Bu, zaten 0067'yi geçmiş veritabanlarında HİÇBİR
   ŞEY yapmaz (ikisi de vardır) ve taze veritabanında yalnız eksik olanı
   kurar. `test_TAZE_VERITABANINDA_bilesik_FK_HALA_duruyor` bunu gerçek bir
   göç zinciriyle ölçer.

SIRA ANLAMLIDIR: (1) olmasaydı bildirim yapılamazdı; (2) ölçülmeseydi
bildirim yapılır ve bir kiracı savunması SESSİZCE düşerdi.

--- YAZICI SINIRI TAŞINDI, GEVŞEMEDİ ---------------------------------------

1B-A'nın kapısı "yazıcı YALNIZ `app/routers/transactions.py`" diyordu. Üç
çağıranla o cümle sürdürülemez; iki seçenek vardı ve BİRİ KUSURDU:

  (a) `_parti_ac`/`_parti_geri_al`ı ÜÇ dosyaya kopyalamak — üç ayrı SKT
      çatışma kuralı, üç ayrı "eksiye düşmez" eşiği. `app/parti.py`nin
      başlığında ADIYLA reddedilen kusurun ta kendisi.
  (b) Yazıcıyı `app/parti_defteri.py`ye TAŞIMAK ve kapıyı onunla taşımak.

(b) seçildi ve yeni kapı DAHA DARDIR: eski kapıda yazıcı bir YÖNLENDİRİCİ
idi (1755 satır, onlarca yol) ve içine dördüncü bir yazma yolu eklemek kapıyı
KIRMIYORDU; yenisinde yazma yüzeyi tek işi bu olan bir modüldür ve ONU
ÇAĞIRANLARIN kümesi de AYRICA kapalıdır.

--- 1B-B (SATIŞ/FEFO) İLE ORTAK KARAR --------------------------------------

`app/parti_defteri.py` 1B-B'nin (`feat/1b-b-satis-fefo`) tüketim yazıcılarını
da barındıracak. ÇAĞIRAN KÜMESİ bu yüzden `app/workflow.py`yi de İÇERİR ve bu
bir GEVŞEME DEĞİL, sahip kararıdır: küme İKİ dilimin toplamıdır ve ikinci
birleşen onu GENİŞLETMEK zorunda kalmaz — genişletme anı, kapının en kolay
sessizce kırıldığı andır. `test_CAGIRAN_KUMESI_BOS_DEGIL` kümenin vaat
edilmiş ama boş bir izin listesine dönüşmesini engeller.

`fefo_sec` kapısı da aynı sebeple BURADA duruyor: seçiciye YALNIZ
`app/parti_defteri.py` bağlanabilir. Bugün ölçüm SIFIR referanstır (1B-C
parti TÜKETMEZ) ve `tests/test_1b_a_alis_lot.py::test_fefo_sec_HALA_CAGIRANSIZ`
o sıfırı ayrıca çiviliyor; 1B-B tüketimi getirdiğinde o kapı emekli olur ve
BURASI ayakta kalır.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
GOC_0067 = BACKEND / "alembic" / "versions" / "20260903_0067_parti_skt.py"
DEFTER = BACKEND / "app" / "parti_defteri.py"
SEMA = BACKEND / "app" / "core_schema.py"
URUNLER = BACKEND / "app" / "routers" / "products.py"
SAYIM = BACKEND / "app" / "routers" / "warehouse_counts.py"

#: Parti defterine YAZMASINA izin verilen TEK dosya. Yol `backend/`e görelidir.
YAZICI = "app/parti_defteri.py"

_YAZMA_FIILLERI = ("INSERT", "UPDATE", "DELETE")


def _calistirilabilir_sabitler(agac: ast.AST):
    """Belge dizgisi OLMAYAN `Constant` dizgeleri (1B-A'nın ölçülmüş dersi).

    Ham `grep` `app/parti.py`yi ve bu dosyanın KENDİ düzyazısını yakalardı,
    çünkü ikisi de tabloyu tarif ediyor. Düzyazıda anmak ÇAĞIRMAK DEĞİLDİR.
    """
    belgeler: set[int] = set()
    for dugum in ast.walk(agac):
        if isinstance(
            dugum, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            govde = getattr(dugum, "body", [])
            if (
                govde
                and isinstance(govde[0], ast.Expr)
                and isinstance(govde[0].value, ast.Constant)
                and isinstance(govde[0].value.value, str)
            ):
                belgeler.add(id(govde[0].value))
    for dugum in ast.walk(agac):
        if (
            isinstance(dugum, ast.Constant)
            and isinstance(dugum.value, str)
            and id(dugum) not in belgeler
        ):
            yield dugum


# --------------------------------------------------------- yazıcı sınırı ---


# ---------------------------------------------------------------------------
# YAZICI / ÇAĞIRAN / SEÇİCİ KAPILARI BU DOSYADA DEĞİL — TEK EVLERİ
# `tests/test_1b_a_alis_lot.py`DİR.
#
# Bu dilim onların ÜÇÜNÜ DE kendi dosyasında taşıyordu ve 1B-B (#63) aynı üç
# iddiayı 1B-A'nın dosyasında kuruyordu. İKİ EV, İKİ KAPALI KÜME demektir ve
# ikisi SESSİZCE ayrışır: bu dosyanınki `workflow.py`yi tanımıyordu, ötekinin
# ki tanıyordu — yani aynı soruya iki farklı cevap veren iki kapı vardı ve
# hangisinin doğru olduğu SORULAMAZDI. Tam olarak bu kapıların engellemek
# için var olduğu kusur şekli.
#
# Birleşme kararı: iddialar `tests/test_1b_a_alis_lot.py`de BİRLEŞTİ ve
# oradaki kümeler bu dilimin çağıranlarını (products.py, warehouse_counts.py)
# İÇERİYOR. Buradaki kopyalar kaldırıldı; bu dosya artık YALNIZ 1B-C'ye özgü
# olanı ölçüyor (sayım/ayarlama davranışı, 0067 koşul ayrımı, manifesto).
# ---------------------------------------------------------------------------


def test_lot_id_SAYISAL_MANIFESTOYA_GIRMEZ_olculdu() -> None:
    """0067'NİN KARARINI TERSİNE ÇEVİREN ÖLÇÜMÜN KENDİSİ.

    0067 `core_schema`ya dokunmadı çünkü bildirimin sayısal göç mutabakat
    kapısında bir VARLIK FARKI üreteceğini düşündü. Bu kapı o düşünceyi
    ÖLÇÜYOR ve YANLIŞ olduğunu gösteriyor: manifesto `metadata`dan
    TÜRETİLMEZ, elle yazılmış iki sütun ADI sözlüğüdür ve `lot_id` ikisinde
    de yoktur.

    KAPININ SAVUNDUĞU ŞEY GELECEKTEKİ BİR HATA: biri `lot_id`i (ya da başka
    bir tamsayı bağı) `QUANTITY_COLUMNS`a eklerse `capture_numeric_snapshot`
    onu bir MİKTAR sanar, `NUMERIC(18,4)`e yuvarlamaya çalışır ve mutabakat
    çıktısı bir kimlik alanını para gibi raporlar. O gün burası kırmızı olur.
    """
    from app.core_schema import stock_movements
    from app.numeric_manifest import iter_numeric_columns

    sutunlar = [(t, c) for t, c, _, _, _ in iter_numeric_columns()]
    assert "lot_id" in {sutun.name for sutun in stock_movements.c}, (
        "`stock_movements.lot_id` core_schema'da BİLDİRİLMEMİŞ — sayım yolu "
        "Core `insert()` ile ona değer YAZAMAZ."
    )
    assert [c for t, c in sutunlar if t == "stock_movements"] == ["quantity"], (
        "`stock_movements` için sayısal manifesto artık `quantity`den fazlasını "
        "söylüyor; `lot_id` bir KİMLİKTİR, miktar değil."
    )
    assert not [(t, c) for t, c in sutunlar if c == "lot_id"], (
        "bir `lot_id` sütunu sayısal manifestoya girmiş — mutabakat onu para/"
        "miktar gibi yuvarlayıp raporlar."
    )
    assert len(sutunlar) == 90, (
        f"sayısal manifesto genişledi ({len(sutunlar)}); 1B-C'nin ölçümü 90 idi "
        "ve bu dilim manifestoya HİÇBİR ŞEY eklemedi."
    )


# ------------------------------------------------------------ göç kapısı ---


def test_0067_SUTUNU_ve_KISITI_AYRI_soruyor() -> None:
    """Tek koşul, bildirimle birlikte FK'yi SESSİZCE düşürüyordu.

    Bir dizge sırası ölçüyor ama savunduğu şey bir DAVRANIŞ ve tersi
    SESSİZDİR: iki soru tek koşulda birleştirilirse taze veritabanında sütun
    `create_all`dan gelir, koşul yanlış olur ve bileşik yabancı anahtar HİÇ
    kurulmaz. Hiçbir test kırmızı olmazdı — kısıt yokken hiçbir yazma
    reddedilmez.

    KARDEŞİ `test_TAZE_VERITABANINDA_bilesik_FK_HALA_duruyor` aynı şeyi
    gerçek bir göç zinciriyle ölçüyor. İkisi ayrı çünkü ayrı şeyler kırılır:
    biri koşulun biçimini, öteki sonucun kendisini.
    """
    kaynak = GOC_0067.read_text(encoding="utf-8")
    yukari = kaynak[kaynak.index("def upgrade"):kaynak.index("def downgrade")]
    assert 'sutun_var = "lot_id" in _sutunlar(inspector, HAREKET)' in yukari
    assert "kisit_var = FK_HAREKET_PARTI in {" in yukari
    assert "if not sutun_var or not kisit_var:" in yukari
    assert "if not sutun_var:" in yukari
    assert "if not kisit_var:" in yukari


def test_TAZE_VERITABANINDA_bilesik_FK_HALA_duruyor(tmp_path: Path) -> None:
    """Taze veritabanı + `alembic upgrade head` -> FK GERÇEKTEN VAR.

    ÖLÇÜLEN KUSUR: `core_schema`ya `lot_id` bildirildiği an sütunu artık
    `20260712_0000`ın `create_all`ı açıyor ve 0067'nin TEK koşulu yanlış
    oluyordu. Bu dosya yazılırken önce/sonra ölçüldü: bildirimden ÖNCE 1 FK,
    tek koşulla 0, koşul ikiye ayrıldıktan sonra yine 1.

    Alt süreçte koşuyor çünkü `app.config.Settings` modül düzeyinde donuyor
    ve `DATABASE_URL` süreç İÇİNDE değiştirilemez.
    """
    veritabani = tmp_path / "1b-c-taze.db"
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    ortam["SUNGUR_DATA_DIR"] = str(tmp_path)
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    tamamlandi = subprocess.run(
        [sys.executable, "-c", _TAZE_FK], cwd=BACKEND, env=ortam,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    assert tamamlandi.returncode == 0, tamamlandi.stdout + "\n" + tamamlandi.stderr
    assert "TAZE FK TAMAM" in tamamlandi.stdout, tamamlandi.stdout


_TAZE_FK = r'''
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

yapilandirma = Config("alembic.ini")
yapilandirma.set_main_option("script_location", "alembic")
yapilandirma.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
command.upgrade(yapilandirma, "head")

denetci = inspect(create_engine(os.environ["DATABASE_URL"]))
sutunlar = {s["name"] for s in denetci.get_columns("stock_movements")}
assert "lot_id" in sutunlar, sorted(sutunlar)

kisitlar = {
    k["name"]: (tuple(k["constrained_columns"]), k["referred_table"],
                tuple(k["referred_columns"]))
    for k in denetci.get_foreign_keys("stock_movements")
}
assert "fk_stock_movements_lot_same_company" in kisitlar, (
    "BILESIK YABANCI ANAHTAR TAZE VERITABANINDA YOK: capraz kiraci lot_id "
    "artik veritabani seviyesinde ENGELLENMIYOR. 0067'nin sutun/kisit kosulu "
    "yeniden BIRLESTIRILMIS olabilir. Bulunan: " + repr(sorted(kisitlar))
)
assert kisitlar["fk_stock_movements_lot_same_company"] == (
    ("company_id", "lot_id"), "product_lots", ("company_id", "id")
), kisitlar["fk_stock_movements_lot_same_company"]
print("TAZE FK TAMAM")
'''


# ----------------------------------------------------- sınır ve şekil kapıları ---


def test_lot_kodu_sinirlari_UC_YERDE_de_AYNI() -> None:
    """80 karakter: göç, alış kalemi, ayarlama ve sayım — DÖRDÜ de aynı.

    İki yerde iki farklı sınır olsaydı büyük olan sessizce kesilir ve kesilen
    kod defterdeki partiyle EŞLEŞMEZDİ — aynı parti için İKİNCİ bir satır
    açılırdı ve miktar ikiye bölünürdü.
    """
    semalar = (BACKEND / "app" / "schemas.py").read_text(encoding="utf-8")
    goc = (
        BACKEND / "alembic" / "versions" / "20260908_0073_parti_depo_alis.py"
    ).read_text(encoding="utf-8")
    assert "KALEM_KODU_UZUNLUK = 80" in goc
    assert semalar.count('lot_code: str | None = Field(default=None, max_length=80)') == 2
    assert (
        'lot_code: str | None = Field(default=None, max_length=80)'
        in SAYIM.read_text(encoding="utf-8")
    )


def test_ayarlama_ISARETE_bakar_mode_a_DEGIL() -> None:
    """Parti kararı `diff`in İŞARETİNDEDİR, `payload.mode`da DEĞİL.

    `mode='set'` hem artı hem eksi bir fark üretebilir ve kararı `mode`a
    bağlamak ölçülebilir bir kusurdur: sayılan bir AZALMA (`set`, eski
    stoktan küçük) partiye EKLENİRDİ. Kapı, yardımcının gövdesinde `mode`
    okunmadığını ölçüyor.
    """
    agac = ast.parse(URUNLER.read_text(encoding="utf-8"))
    govde = [
        dugum
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.FunctionDef) and dugum.name == "_ayarlama_partisi"
    ]
    assert len(govde) == 1, "`_ayarlama_partisi` bulunamadı"
    okunanlar = {
        dugum.attr
        for dugum in ast.walk(govde[0])
        if isinstance(dugum, ast.Attribute)
        and isinstance(dugum.value, ast.Name)
        and dugum.value.id == "payload"
    }
    assert "mode" not in okunanlar, (
        f"parti kararı `payload.mode`u okuyor: {sorted(okunanlar)}. Karar "
        "İŞARETE bağlıdır; `mode='set'` her iki yönde de fark üretir."
    )
    assert {"lot_code", "expiry_date"} <= okunanlar, sorted(okunanlar)


def test_SAYIM_SKT_BEYAN_ETMEZ_argumani_HIC_yazmaz() -> None:
    """Sayım `_parti_ac`e `expiry_date` GEÇMEZ ve bu bir unutma DEĞİLDİR.

    `expiry_date=None` geçmek "bu partinin SKT'si YOKTUR" BEYANIDIR ve var
    olan tarihli bir partiyle ÇELİŞİR (422 `LOT_SKT_CELISKI`). Sayım tarih
    beyan etmez; argümanı yazmamak `SKT_SORULMADI` sentineline düşer ve
    çatışma denetimi HİÇ çalışmaz.

    TERSİ SESSİZ DEĞİL, GÜRÜLTÜLÜDÜR ama YANLIŞ YERDE gürültü yapar: SKT'si
    olan her partinin sayımı 422 ile ölür ve defteri düzeltmek için var olan
    yol, defterin kendisi yüzünden kapanırdı.
    """
    agac = ast.parse(SAYIM.read_text(encoding="utf-8"))
    cagrilar = [
        dugum
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.Call)
        and isinstance(dugum.func, ast.Name)
        and dugum.func.id == "_parti_ac"
    ]
    assert len(cagrilar) == 1, "sayım yolunda tam bir `_parti_ac` çağrısı bekleniyor"
    anahtarlar = {kelime.arg for kelime in cagrilar[0].keywords}
    assert "expiry_date" not in anahtarlar, (
        "sayım yolu `_parti_ac`e SKT geçiyor; sayım tarih BEYAN ETMEZ."
    )
    assert {"product_id", "warehouse_id", "lot_code", "miktar"} == anahtarlar


def test_HER_PARTI_DUSMESI_KENDI_KORUMASINI_TASIYOR() -> None:
    """1B-C'NİN "TEK YER" KAPISI YENİDEN ÇERÇEVELENDİ — DAHA DAR OLARAK.

    Bu kapı `parti_dus` DIŞINDA hiçbir `quantity=quantity-...` yazması
    olmadığını iddia ediyordu ve o iddia 1B-B (#63) ile ARTIK YANLIŞTIR:
    defterde ÜÇ düşme var ve üçünün SÖZLEŞMESİ FARKLIDIR. Yanlış bir iddiayı
    doğru sayıya çekmek (3 bekle) kapıyı hiçbir şey savunmaz hale getirirdi;
    onun yerine SAVUNULAN ŞEY yazıldı.

    YENİ İDDİA: eksiye düşebilen HER yazma, korumasını KENDİ `WHERE`ünde
    taşır. Koruma `WHERE`de değil de önceden bir `SELECT` ile yapılırsa iki
    eşzamanlı düşme aynı satırı okur ve ikisi de "yetiyor" der — 1B-B'nin
    `_parti_tuket` için ADIYLA ölçtüğü kusur.

    TEK İSTİSNA `_parti_geri_al`DIR VE İSTİSNA OLMASI BİR KARARDIR: onun
    `miktar`ı hareketin İŞARETLİ miktarıdır, yani satış geri alınırken
    NEGATİFTİR ve çıkarma EKLEMEYE döner. Bir alt sınır koruması orada eksiye
    düşmeyi engellemez, TERSİNE geri vermeyi engellerdi. İstisna burada ADIYLA
    sayılıyor ki dördüncü bir korumasız düşme onun arkasına saklanamasın.
    """
    KORUMASIZ_MESRU = {"_parti_geri_al"}

    kaynak = DEFTER.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)

    # Düşme metnini TAŞIYAN fonksiyonu bul: sabit hangi `FunctionDef`in
    # gövdesindeyse o. Metni dosya düzeyinde saymak, hangi sözleşmenin
    # gevşediğini SÖYLEYEMEZDİ.
    bulunan: dict[str, bool] = {}
    for dugum in ast.walk(agac):
        if not isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sabit in _calistirilabilir_sabitler(dugum):
            metin = " ".join(sabit.value.split())
            if "UPDATE product_lots SET quantity=quantity-" not in metin:
                continue
            bulunan[dugum.name] = "quantity>=" in metin

    assert set(bulunan) == {"_parti_geri_al", "_parti_tuket", "_parti_dus"}, (
        f"parti düşüren fonksiyon kümesi değişti: {sorted(bulunan)}. Yeni bir "
        "düşme eklendiyse sözleşmesi ADIYLA incelenmelidir."
    )
    korumasiz = {ad for ad, korumali in bulunan.items() if not korumali}
    assert korumasiz == KORUMASIZ_MESRU, (
        f"korumasız düşme(ler): {sorted(korumasiz)}. Eksiye düşebilen her "
        "yazma koşulunu KENDİ `WHERE`ünde taşımalıdır; tek meşru istisna "
        f"{sorted(KORUMASIZ_MESRU)} ve gerekçesi bu kapının düzyazısındadır."
    )

    # İKİNCİ YARI DEĞİŞMEDİ: düşme metinleri YALNIZ yazıcıdadır.
    dusenler: list[str] = []
    for yol in sorted((BACKEND / "app").rglob("*.py")):
        yer = yol.relative_to(BACKEND).as_posix()
        for dugum in _calistirilabilir_sabitler(ast.parse(yol.read_text(encoding="utf-8"))):
            metin = " ".join(dugum.value.split())
            if "UPDATE product_lots SET quantity=quantity-" in metin:
                dusenler.append(f"{yer}:{dugum.lineno}")
    assert dusenler and all(yer.startswith(YAZICI) for yer in dusenler), dusenler

    # `LOT_MIKTARI_EKSIYE_DUSER` kodunu ÜRETEN tek UPDATE hâlâ `_parti_dus`tur.
    dus = kaynak[kaynak.index("def _parti_dus"):kaynak.index("def _parti_geri_al")]
    assert dus.count("LOT_MIKTARI_EKSIYE_DUSER") == 1, dus.count("LOT_MIKTARI_EKSIYE_DUSER")


# ---------------------------------------------------------------------------
# DAVRANIŞ — taze veritabanı, göç zinciri, HTTP katmanı.
# ---------------------------------------------------------------------------


def test_ayarlama_ve_sayim_parti_defterini_YAZIYOR(tmp_path: Path) -> None:
    """Uçtan uca: aç, ekle, düş, eksiye direne, say, karıştırma, komşuya gösterme.

    Alt süreçte GERÇEK ŞEMA ile koşuyor (deponun mevcut kalıbı): `app.main`
    açılışta alembic'i sürüyor, yani 0067'nin AYRILMIŞ koşulu da bu turda
    uygulanıyor.
    """
    veritabani = tmp_path / "1b-c-ayarlama-lot.db"
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    ortam["SUNGUR_DATA_DIR"] = str(tmp_path)
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    tamamlandi = subprocess.run(
        [sys.executable, "-c", _DAVRANIS], cwd=BACKEND, env=ortam,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    assert tamamlandi.returncode == 0, tamamlandi.stdout + "\n" + tamamlandi.stderr
    assert "AYARLAMA LOT TAMAM" in tamamlandi.stdout, tamamlandi.stdout


_DAVRANIS = r'''
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app

client = TestClient(app)


def ok(cevap):
    assert cevap.status_code < 300, (cevap.status_code, cevap.text)
    return cevap.json() if cevap.content else None


def giris(kullanici, sifre, yeni=None):
    cevap = client.post('/api/auth/login',
                        json={'username': kullanici, 'password': sifre})
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    baslik = {'Authorization': 'Bearer ' + govde['access_token'],
              'X-Company-ID': str(govde['companies'][0]['id'])}
    if yeni:
        degisti = client.post('/api/auth/change-password', headers=baslik,
                              json={'current_password': sifre, 'new_password': yeni})
        assert degisti.status_code == 200, degisti.text
        baslik['Authorization'] = 'Bearer ' + degisti.json()['access_token']
    return baslik, int(govde['companies'][0]['id'])


baslik, cid = giris('admin', 'admin123', 'AyarlamaLot!123')

depo_a = ok(client.get('/api/warehouses', headers=baslik))[0]['id']
depo_b = ok(client.post('/api/warehouses', headers=baslik,
                        json={'name': 'Ayarlama B Deposu', 'code': 'ABD'}))['id']


def urun_ac(ad):
    return ok(client.post('/api/products', headers=baslik,
                          json={'name': ad, 'purchase_price': 10, 'sale_price': 20,
                                'vat_rate': 20, 'stock': 0, 'unit': 'Adet'}))['id']


urun = urun_ac('Ayarlanan Urun')
urun_sayim = urun_ac('Sayilan Urun')
urun_ciftli = urun_ac('Iki Partili Urun')
urun_partisiz = urun_ac('Partisiz Urun')


def ayarla(pid, mod, adet, depo=None, kod=None, skt='YAZMA'):
    govde = {'mode': mod, 'quantity': adet, 'movement_date': '2026-09-08',
             'warehouse_id': depo or depo_a, 'note': 'olcum'}
    if kod is not None:
        govde['lot_code'] = kod
    if skt != 'YAZMA':
        govde['expiry_date'] = skt
    return client.post('/api/products/' + str(pid) + '/stock',
                       headers=baslik, json=govde)


def say(satirlar, depo=None):
    return client.post('/api/warehouses/counts', headers=baslik, json={
        'warehouse_id': depo or depo_a, 'count_date': '2026-09-08',
        'note': None, 'items': satirlar})


def partiler(pid):
    cevap = ok(client.get('/api/products/' + str(pid) + '/lots', headers=baslik))
    return {(s['lot_code'], s['warehouse_id']): Decimal(str(s['quantity']))
            for s in cevap['lots']}


def hareketler(pid):
    with SessionLocal() as db:
        return [dict(r) for r in db.execute(text(
            "SELECT id,quantity,movement_type,lot_id FROM stock_movements "
            "WHERE company_id=:c AND product_id=:p ORDER BY id"),
            {'c': cid, 'p': pid}).mappings().all()]


def depo_stogu(pid, depo):
    with SessionLocal() as db:
        satir = db.execute(text(
            "SELECT quantity FROM warehouse_stocks WHERE company_id=:c "
            "AND warehouse_id=:w AND product_id=:p"),
            {'c': cid, 'w': depo, 'p': pid}).scalar_one_or_none()
    return Decimal('0') if satir is None else Decimal(str(satir))


# === 1. PARTILI EKLEME: parti ACILIR, hareket lot_id TASIR ================
ok(ayarla(urun, 'add', 5, kod='AY-1', skt='2027-03-31'))
assert partiler(urun) == {('AY-1', depo_a): Decimal('5')}, partiler(urun)
son = hareketler(urun)[-1]
assert son['lot_id'] is not None and Decimal(str(son['quantity'])) == Decimal('5'), son

# === 2. AYNI KOD, AYNI DEPO -> EKLENIR (ikinci satir DEGIL) ===============
ok(ayarla(urun, 'add', 3, kod='AY-1', skt='2027-03-31'))
assert partiler(urun) == {('AY-1', depo_a): Decimal('8')}, partiler(urun)

# === 3. AYNI KOD, BASKA DEPO -> AYRI SATIR (yanlis-depo mutasyonu) ========
ok(ayarla(urun, 'add', 2, depo=depo_b, kod='AY-1', skt='2027-03-31'))
assert partiler(urun) == {('AY-1', depo_a): Decimal('8'),
                          ('AY-1', depo_b): Decimal('2')}, partiler(urun)
# Depo stoklari da AYRI: parti deposu hareketin deposudur.
assert depo_stogu(urun, depo_a) == Decimal('8'), depo_stogu(urun, depo_a)
assert depo_stogu(urun, depo_b) == Decimal('2'), depo_stogu(urun, depo_b)

# === 4. PARTILI DUSME -> parti AZALIR =====================================
ok(ayarla(urun, 'remove', 3, kod='AY-1'))
assert partiler(urun)[('AY-1', depo_a)] == Decimal('5'), partiler(urun)
son = hareketler(urun)[-1]
assert Decimal(str(son['quantity'])) == Decimal('-3') and son['lot_id'] is not None, son

# === 5. PARTI YETMEZ ama DEPO YETER -> 409 ve HICBIR SEY DEGISMEZ ========
# IKINCI PARTI ZORUNLU, sus DEGIL: depoda yalniz AY-1 olsaydi "99 dus"
# istegini NEGATIF STOK POLITIKASI reddederdi (`adjust_warehouse_stock`
# parti defterinden ONCE kosar) ve bu senaryo BASKA bir kurali olcerdi.
# OLCULDU: o yolun cevabi da 409'dur ama govdesi DUZ METINDIR, kod TASIMAZ.
# AY-2 acilinca depo 15 olur; AY-1'den 8 dusmek DEPO icin mesru (15 -> 7),
# PARTI icin degil (5 < 8) — yani reddi veren YALNIZ parti kuralidir.
ok(ayarla(urun, 'add', 10, kod='AY-2'))
assert depo_stogu(urun, depo_a) == Decimal('15'), depo_stogu(urun, depo_a)
onceki_parti = partiler(urun)
onceki_stok = depo_stogu(urun, depo_a)
onceki_hareket = len(hareketler(urun))
red = ayarla(urun, 'remove', 8, kod='AY-1')
assert red.status_code == 409, (red.status_code, red.text)
assert red.json()['detail']['code'] == 'LOT_MIKTARI_EKSIYE_DUSER', red.text
# GERI ALMA TAM: depo stogu da, hareket defteri de, parti defteri de duruyor.
assert partiler(urun) == onceki_parti, partiler(urun)
assert depo_stogu(urun, depo_a) == onceki_stok, depo_stogu(urun, depo_a)
assert len(hareketler(urun)) == onceki_hareket, hareketler(urun)

# === 5b. IKI RED AYNI DEGILDIR: politika reddi KOD TASIMAZ ===============
politika = ayarla(urun, 'remove', 999, kod='AY-1')
assert politika.status_code == 409, (politika.status_code, politika.text)
assert isinstance(politika.json()['detail'], str), politika.text

# === 6. VAR OLMAYAN PARTIDEN DUSME -> 409, sessiz acilis YOK ==============
red = ayarla(urun, 'remove', 1, kod='HIC-YOK')
assert red.status_code == 409, (red.status_code, red.text)
assert red.json()['detail']['code'] == 'LOT_MIKTARI_EKSIYE_DUSER', red.text
assert ('HIC-YOK', depo_a) not in partiler(urun), partiler(urun)

# === 7. SKT CATISMASI -> 422, defter KIMILDAMAZ ===========================
catisma = ayarla(urun, 'add', 1, kod='AY-1', skt='2028-12-31')
assert catisma.status_code == 422, (catisma.status_code, catisma.text)
assert catisma.json()['detail']['code'] == 'LOT_SKT_CELISKI', catisma.text
assert partiler(urun)[('AY-1', depo_a)] == Decimal('5'), partiler(urun)

# === 8. SKT ALANI HIC GONDERILMEZSE CATISMA DENETIMI CALISMAZ ============
# `SKT_SORULMADI` sentinelinin ta kendisi: tarihli bir partiye SKT yazmadan
# eklemek MESRUDUR, cunku beyan YOKTUR.
ok(ayarla(urun, 'add', 1, kod='AY-1'))
assert partiler(urun)[('AY-1', depo_a)] == Decimal('6'), partiler(urun)

# === 9. PARTISIZ AYARLAMA -> defter HIC dokunulmaz, lot_id NULL ==========
ok(ayarla(urun_partisiz, 'add', 7))
assert partiler(urun_partisiz) == {}, partiler(urun_partisiz)
son = hareketler(urun_partisiz)[-1]
assert son['lot_id'] is None, son
ok(ayarla(urun_partisiz, 'remove', 2))
assert partiler(urun_partisiz) == {}, partiler(urun_partisiz)
assert hareketler(urun_partisiz)[-1]['lot_id'] is None, hareketler(urun_partisiz)

# === 10. SIFIR FARKLI PARTILI AYARLAMA -> parti ACILIR, miktar 0 =========
ok(ayarla(urun_partisiz, 'add', 0, kod='SIFIR'))
assert partiler(urun_partisiz) == {('SIFIR', depo_a): Decimal('0')}, partiler(urun_partisiz)
assert hareketler(urun_partisiz)[-1]['lot_id'] is not None, hareketler(urun_partisiz)

# === 11. SAYIM, PARTILI: fark PARTIYE uygulanir ==========================
ok(ayarla(urun_sayim, 'add', 10, kod='SY-1', skt='2027-05-31'))
sonuc = ok(say([{'product_id': urun_sayim, 'counted_quantity': 7,
                 'lot_code': 'SY-1'}]))
assert sonuc['items'][0]['lot_code'] == 'SY-1', sonuc
assert Decimal(str(sonuc['items'][0]['system_quantity'])) == Decimal('10'), sonuc
assert Decimal(str(sonuc['items'][0]['variance'])) == Decimal('-3'), sonuc
assert partiler(urun_sayim) == {('SY-1', depo_a): Decimal('7')}, partiler(urun_sayim)
assert depo_stogu(urun_sayim, depo_a) == Decimal('7'), depo_stogu(urun_sayim, depo_a)
son = hareketler(urun_sayim)[-1]
assert son['movement_type'] == 'set' and son['lot_id'] is not None, son

# === 12. SAYIM ARTIRIRSA parti ARTAR ve SKT CATISMASI OLMAZ =============
# Parti TARIHLIDIR ve sayimin SKT alani YOKTUR; 422 gelseydi defteri
# duzeltmek icin var olan yol defterin kendisi yuzunden kapanirdi.
ok(say([{'product_id': urun_sayim, 'counted_quantity': 12, 'lot_code': 'SY-1'}]))
assert partiler(urun_sayim) == {('SY-1', depo_a): Decimal('12')}, partiler(urun_sayim)
assert depo_stogu(urun_sayim, depo_a) == Decimal('12'), depo_stogu(urun_sayim, depo_a)

# === 13. AYNI URUNUN IKI PARTISI AYRI SATIRLARDA SAYILIR ================
ok(ayarla(urun_ciftli, 'add', 4, kod='CF-A'))
ok(ayarla(urun_ciftli, 'add', 6, kod='CF-B'))
assert depo_stogu(urun_ciftli, depo_a) == Decimal('10')
ok(say([{'product_id': urun_ciftli, 'counted_quantity': 3, 'lot_code': 'CF-A'},
        {'product_id': urun_ciftli, 'counted_quantity': 9, 'lot_code': 'CF-B'}]))
assert partiler(urun_ciftli) == {('CF-A', depo_a): Decimal('3'),
                                 ('CF-B', depo_a): Decimal('9')}, partiler(urun_ciftli)
# Depo stogu IKI farkin TOPLAMI kadar kimildadi: 10 - 1 + 3 = 12.
assert depo_stogu(urun_ciftli, depo_a) == Decimal('12'), depo_stogu(urun_ciftli, depo_a)

# === 14. AYNI (URUN, PARTI) IKI SATIRDA -> 422 ==========================
cift = say([{'product_id': urun_ciftli, 'counted_quantity': 1, 'lot_code': 'CF-A'},
            {'product_id': urun_ciftli, 'counted_quantity': 2, 'lot_code': 'CF-A'}])
assert cift.status_code == 422, (cift.status_code, cift.text)

# === 15. PARTILI + PARTISIZ AYNI URUNDE -> 422 ==========================
karisik = say([{'product_id': urun_ciftli, 'counted_quantity': 1, 'lot_code': 'CF-A'},
               {'product_id': urun_ciftli, 'counted_quantity': 2}])
assert karisik.status_code == 422, (karisik.status_code, karisik.text)
assert 'partili' in karisik.text or 'partisiz' in karisik.text, karisik.text

# === 16. PARTISIZ SAYIM: 1B-C ONCESI DAVRANIS AYNEN DURUYOR =============
onceki = partiler(urun_partisiz)
ok(say([{'product_id': urun_partisiz, 'counted_quantity': 3}]))
assert depo_stogu(urun_partisiz, depo_a) == Decimal('3'), depo_stogu(urun_partisiz, depo_a)
assert partiler(urun_partisiz) == onceki, partiler(urun_partisiz)
assert hareketler(urun_partisiz)[-1]['lot_id'] is None, hareketler(urun_partisiz)

# === 17. KIRACI SINIRI: komsunun partisi ne GORUNUR ne DEGISIR ==========
# Komsu SATIRDAN kuruluyor, uctan DEGIL: olculen sey firma kurma akisi degil,
# yazma ve okuma uclarinin kiraci yuklemidir.
with SessionLocal() as db:
    komsu_cid = db.execute(text(
        "INSERT INTO companies (name, is_active, created_at) "
        "VALUES ('Ayarlama Komsu A.S.', 1, :simdi) RETURNING id"),
        {'simdi': datetime.now(timezone.utc)}).scalar_one()
    komsu_depo = db.execute(text(
        "INSERT INTO warehouses (company_id, name, is_active, is_default) "
        "VALUES (:cid, 'Komsu Deposu', 1, 1) RETURNING id"),
        {'cid': komsu_cid}).scalar_one()
    komsu_urun = db.execute(text(
        "INSERT INTO products (company_id, name, unit, sale_price, active) "
        "VALUES (:cid, 'Komsu Urunu', 'Adet', 10, 1) RETURNING id"),
        {'cid': komsu_cid}).scalar_one()
    db.execute(text(
        "INSERT INTO product_lots (company_id, product_id, lot_code, expiry_date,"
        " quantity, warehouse_id, created_at) "
        "VALUES (:cid, :pid, 'AY-1', '2027-03-31', 42, :wid, :simdi)"),
        {'cid': komsu_cid, 'pid': komsu_urun, 'wid': komsu_depo,
         'simdi': datetime.now(timezone.utc)})
    db.commit()

assert komsu_cid != cid
# Komsunun URUNUNE ayarlama 404: parti yolu urun kapisinin ARKASINDADIR.
kacak = ayarla(komsu_urun, 'add', 1, kod='AY-1')
assert kacak.status_code == 404, (kacak.status_code, kacak.text)
# Komsunun DEPOSUNA ayarlama da 404.
kacak = ayarla(urun, 'add', 1, depo=komsu_depo, kod='AY-1')
assert kacak.status_code == 404, (kacak.status_code, kacak.text)
# Komsunun sayimi da gecmez.
kacak = say([{'product_id': komsu_urun, 'counted_quantity': 1, 'lot_code': 'AY-1'}])
assert kacak.status_code == 400, (kacak.status_code, kacak.text)
# AYNI KODLU komsu partisi bizim defterimizde HIC kimildamadi.
with SessionLocal() as db:
    komsu_miktar = db.execute(text(
        "SELECT quantity FROM product_lots WHERE company_id=:c AND product_id=:p"),
        {'c': komsu_cid, 'p': komsu_urun}).scalar_one()
assert Decimal(str(komsu_miktar)) == Decimal('42'), komsu_miktar
assert partiler(urun)[('AY-1', depo_a)] == Decimal('6'), partiler(urun)

print('AYARLAMA LOT TAMAM')
'''

"""FAZ 4 outbox — YAZICI dilimi. Her faaliyet yazımı TAM BİR olay üretir.

--- KUSUR ----------------------------------------------------------------------

``field_integration_events`` 0044'te bir outbox olarak açıldı: kiracı sütunu,
bileşik anahtar, ``TENANT_TABLES`` üyeliği, ``(company_id, idempotency_key)``
benzersizliği. Ve bugüne kadar **tek satır üretmedi**. Ölçüldü: adı
``backend/app`` altında **sıfır** kez geçiyordu.

Boş bir outbox'ın en tehlikeli tarafı sessiz olması: tablo var, şema doğru,
testler yeşil — ve hiçbir tarla olayı stok hareketine ya da muhasebe fişine
dönüşmüyor. Dört bağımsız alan araştırması (ikisi tarla, ikisi hayvancılık)
aynı boşlukta buluştu.

--- YAZMA YOLLARI: ÖLÇÜLDÜ, VARSAYILMADI ---------------------------------------

  A) AST — ``backend/app`` altındaki HER string sabitinde
     ``INSERT INTO field_activities`` ve Core ``insert(field_activities)``:
     **1 nokta** (``routers/farm.py``, ``create_activity()`` içinde).
  B) Rota — ``/field-activities`` üzerindeki yazan uçlar: **1 uç** (``POST``).
     ``PUT``/``PATCH``/``DELETE`` YOK. Çevrimdışı kuyruk (``_KUYRUK_TABLOSU``)
     bilerek ``activity`` içermiyor; tekrar gönderim ``_tekrar_mi`` ile ERKEN
     dönüyor, ikinci INSERT üretmiyor.

--- KURALIN KAPSAMI: DARALTILDI ve İMKÂNSIZLIK SABİTLENDİ ----------------------

İnceleme haklı bir ayrım yaptı: aşağıdaki keşif **iki sözdizimsel biçimi**
tanıyor (``text()`` içinde ``INSERT INTO field_activities`` ve Core
``insert(field_activities)``). ``db.add(FieldActivity(...))`` biçiminde bir ORM
yazımı da bu tabloya satır yazardı ama keşfe GİRMEZDİ. Sözleşme "her yazan
fonksiyon" diyorsa, uygulama "bu iki yazılış" diyorsa, ikisi aynı şey değildir
— ve bu, kapının önlemek için var olduğu görünmez-yazıcı sınıfının ta kendisi.

Genişletmek yerine DARALTILDI, çünkü ölçüm ORM yazımının bu depoda
**yapısal olarak imkânsız** olduğunu gösterdi:

  * ``backend/app`` altında ``declarative_base``/``DeclarativeBase``/
    ``registry``/``mapped_column``/``Mapped`` **yok**; tek bir eşlenmiş sınıf
    tanımlı değil. Ölçüldü.
  * ``Session.add(...)`` **yok**; koddaki tüm ``.add(`` çağrıları Python
    ``set.add``. Ölçüldü.
  * ``field_activities`` için uygulamaya ait bir Core ``Table`` nesnesi de
    tanımlı değil.

Yani bugün bir satır veritabanına YALNIZ ``Session.execute()`` üzerinden
girebilir. HİÇBİR YOKLUK VARSAYIM OLARAK BIRAKILMADI — dördü de çapalandı,
sonuncusu ötekilerin arkasını kapatıyor:

* ``test_no_mapped_orm_class_exists`` — yüklü ``app.*`` sınıflarına SQLAlchemy
  ile bakıp EŞLEME KAYDININ boş olduğunu ölçer.
* ``test_no_core_table_for_the_source_exists`` — Core'un karşılığı olan
  METADATA kayıtlarına bakar. İkisi de sözdizimi listelemez.

CORE ÇAPASI NEDEN ORM'İNKİNİN AYNISI DEĞİL. İnceleme haklı olarak "aynı
biçimde sabitle" dedi, ama simetrinin naif hâli ÖLÇÜLDÜĞÜNDE tutmadı:
``gc`` ile canlı ``Table`` nesneleri tarandığında ``field_activities`` BULUNUYOR
— 206 canlı ``Table``, 97 farklı ad. Kaynağı uygulama değil, göç: Alembic
şemayı yansıtırken kendi ``MetaData``sına tablo nesneleri kuruyor (yanındaki
``_alembic_tmp_*`` adları bunu ele veriyor). Naif bir canlı-nesne taraması bu
yüzden KALICI YANLIŞ POZİTİF verirdi ve kapı uygulama koduyla ilgisiz bir
sebeple hep kırmızı kalırdı.

Bu yüzden çapa UYGULAMAYA AİT kayıtlara bakıyor ve üç katmandan oluşuyor —
üçü de TABLO ADINA bakar, değişken adına değil:

1. ``app.*`` modüllerinin niteliği olan ``MetaData`` örneklerinin ``tables``
   kayıtları. Ölçüldü: 10 örnek, 46 tablo adı. İncelemenin verdiği saldırı
   şekli — ``fa_table = Table("field_activities", metadata, ...)`` — değişken
   adı ne olursa olsun buraya kaydolur.
2. ``app.*`` modüllerinin niteliği olan ``Table`` nesneleri; ``MetaData``sı
   modül niteliği olmayan bir tabloyu da yakalar.
3. AST: ``backend/app`` altında ilk argümanı kaynak tablo adı olan herhangi bir
   ``Table(...)`` inşası. Fonksiyon içinde, özel bir ``MetaData`` ile kurulan
   ve içe aktarma anında görünmeyen bir tabloyu bu katman yakalar.

BEYAN EDİLEN SINIR — VE NEDEN ARTIK BOŞLUK DEĞİL. Üç katman da ``Table``
nesnesinin NASIL kurulduğunu arar, ve iki şey onlardan kaçar: (a) adı çalışma
zamanında üretilen bir tablo (``"field_" + "activities"`` AST sabiti değildir),
(b) yansıtmayla (``autoload_with``/Alembic) kurulan tablo — yansıtma bilerek
dışarıda, çünkü göç şemayı yansıtırken ``field_activities`` için canlı bir
``Table`` üretiyor ve dahil edilseydi kapı uygulama koduyla ilgisiz bir sebeple
hep kırmızı kalırdı.

Bu iki sınıf ÖLÇÜLDÜ, tahmin edilmedi: ikisi de ``create_activity``nin işlemine
fazladan bir kaynak satırı yazacak biçimde kuruldu ve ÜÇ KATMAN DA İKİSİNİ DE
KAÇIRDI. Kovalamaca bu yüzden burada bitiyor — ``Table``ın nasıl kurulduğunu
aramak yerine SONUÇ ölçülüyor:

4. ``test_no_committed_transaction_writes_the_source_without_the_outbox`` —
   çalışma zamanında motora bağlanır ve cursor'a ULAŞAN INSERT'leri işlem işlem
   sayar. Commit edilmiş her işlemde ``field_activities`` satır sayısı
   ``field_integration_events`` satır sayısına EŞİT olmak zorunda. İnşayı hiç
   tanımaz, dolayısıyla inşadan kaçılamaz. Ölçüldü — ham ``text()``, dinamik
   adlı ``Table``, fabrikayla üretilmiş ``Table`` ve ``autoload_with`` ile
   yansıtılmış ``Table``: dördü de aynı biçimde görünüyor. Yukarıdaki iki
   mutasyonu da bu katman kırmızıya çevirdi.

Üç statik katman yine de duruyor: aynı hatayı ÇALIŞTIRMADAN, dosyayı okurken
yakalarlar ve hata mesajları daha doğrudan. Savunma derinliği, yedek değil.

SONUÇ KATMANININ KENDİ SINIRI, açıkça: yalnız AÇIK BİR İŞLEM içinde ve bu
motor üzerinden geçen yazmaları görür. Ham DBAPI bağlantısıyla ya da başka bir
süreçten (``psql``, ayrı bir servis) yazılan satır görünmez. AÇILMA KOŞULU:
uygulama ``app.db`` motorunu atlayan bir yazma yolu edinirse bu çapa
genişletilmek zorunda. Gözlemcinin kör olmadığı ayrıca sabitlendi — hiçbir şey
görmeyen bir gözlemci kuralı sessizce boşa düşürürdü.

--- KAPSAM DIŞI: BU DOSYA NE SÖYLEMİYOR ---------------------------------------

Bu kural YALNIZ ``field_activities``ı kapsar. Alan tabloları göç dosyalarından
TÜRETİLİYOR (``create_table("field_*")``), elle sayılmıyor: yeni bir alan
tablosu eklenirse aşağıdaki çapa kırmızı olur ve karar vermeye zorlar.

Ölçüm (bu head'de) — canlı yazma yolu OLAN ama bu kapının HİÇBİR ŞEY
SÖYLEMEDİĞİ tablolar:

    field_activity_inputs   farm.py: add_activity_input, create_activity
    field_harvests          farm.py: create_harvest
    field_operations        field.py: field_add_attachment, field_add_labor,
                                      field_add_part, field_change_status
    field_tasks             farm.py: create_task

``field_operations`` bilhassa dikkat çekici: BAŞKA bir router'da ve DÖRT
yazıcısı var. Bu dilim onların olay üretmesini gerektirmiyor — ürün kararı
FAZ 4'ün sonraki dilimlerine ait. Ama boşluk yalnız bir PR raporunda kalsaydı,
bilinen bir kör nokta belgesiz bir kör noktaya dönerdi.

--- ANTİ-BOŞLUK ----------------------------------------------------------------

Bugünün durumu ZATEN "sıfır satırlı outbox". Yalnız satırın ŞEKLİNİ doğrulayan
bir kapı, yazıcı hiç çağrılmasa da yeşil kalırdı. Bu yüzden çalışma zamanı
kapısı **tam sayı** ölçüyor: faaliyet başına olay sayısı TAM 1.

--- SATIR NEYİ TAŞIMALI (2. DİLİM İÇİN) ---------------------------------------

* ``idempotency_key = "field_activity:<id>:stock"`` — **yalnız kaynak satırdan
  türetilebilir**. Geri doldurma ile canlı yazıcı AYNI anahtarı üretir; ikinci
  fiş yazılamaz. Rastgele/zamana bağlı anahtar tüketiciyi idempotent OLAMAZ
  hale getirir ve 2. dilimi imkânsız kılardı.
* **Hedef anahtarın içinde**, çünkü bir faaliyet ileride hem stok hem muhasebe
  fişi doğurabilir; hedef anahtarda olmasaydı ikinci hedef aynı
  ``(company_id, idempotency_key)`` çiftine düşerdi.
* **Hayvancılık aynı yolu kullanabilir.** ``herd_integration_events`` 0049'da
  aynı sütun sözleşmesiyle açıldı; anahtar biçimi tablo adı içermiyor, yani
  ``animal_movement:<id>:stock`` aynı kalıba oturur.

--- BEYAN EDİLEN ANLAM SINIRI --------------------------------------------------

``target='stock'`` her faaliyet için yazılıyor, GİRDİSİ OLMAYAN faaliyet için
de. Bu satır "bu faaliyetin stok etkisi vardır"ın KANITI DEĞİLDİR; yalnız
"stok tarafının bu faaliyete bakması gerekir" der. Etkisi olmadığına tüketici
karar verir ve satırı ``SKIPPED``a düşürür. Alternatif — hedefi tüketiciye
bırakmak — şemadaki ``target NOT NULL`` yüzünden mümkün değildi.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys

import pytest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "sqlite:///./__outbox_gate.db")

#: Kaynak tablo, outbox ve yazıcının adı. ELLE yazıldı: ``farm.py``dan
#: türetilseydi, yazıcıyı silen bir mutasyon çapayı da silerdi.
KAYNAK_TABLO = "field_activities"
HASAT_TABLO = "field_harvests"
OUTBOX_TABLO = "field_integration_events"
YAZICI_ADI = "_entegrasyon_olayi_yaz"

#: Bu kuralın kapsadığı kaynak tablolar GÖÇTEN TÜRETİLİR; tanım dosyanın
#: sonunda, `_kapsanan_kaynaklar()` ile bağlanır. Elle liste TUTULMAZ.

#: Kapsam DIŞI bırakılan alan tabloları, gerekçesiyle. Elle yazıldı; aşağıdaki
#: test bunu göçlerden TÜRETİLEN kümeyle karşılaştırır, yani liste bayatlayamaz.
KAPSAM_DISI_KAYNAKLAR: dict[str, str] = {
    "field_activity_inputs": "faaliyetin parçası; olayı faaliyet taşıyor",
    "field_operations": "BAŞKA router (field.py), DÖRT yazıcı — sonraki dilim",
    "field_tasks": "görev bir plan kaydı; stok/muhasebe etkisi tartışmalı",
}

#: Outbox'ın kendisi bir kaynak değil.
OUTBOX_KAYNAK_DEGIL = frozenset({OUTBOX_TABLO})

#: SQLAlchemy'nin eşleme kurma yüzeyi. Sözdizimi listesi DEĞİL: bunlar
#: kütüphanenin ORM eşlemesi kurmak için sunduğu KAPALI API kümesi. Biri
#: kullanılırsa ``db.add(Model(...))`` mümkün hale gelir ve kaynak keşfi
#: genişletilmek zorundadır.
ORM_ESLEME_YUZEYI = (
    "declarative_base", "DeclarativeBase", "registry",
    "mapped_column", "Mapped", "as_declarative",
)


def _alan_tablolari_gocten() -> frozenset[str]:
    """``field_*`` tablolarını GÖÇ dosyalarından türetir — elle sayılmaz."""
    desen = re.compile(r"""create_table\(\s*["'](field_[a-z_]+)["']""")
    bulunan: set[str] = set()
    for yol in sorted((BACKEND / "alembic" / "versions").glob("*.py")):
        bulunan.update(desen.findall(yol.read_text(encoding="utf-8")))
    return frozenset(bulunan)


def _insert_eden_fonksiyonlar(tablo: str = KAYNAK_TABLO) -> list[tuple[Path, str]]:
    """``tablo``ya INSERT eden HER fonksiyonu döndürür (dosya, ad)."""
    desen = re.compile(r"INSERT\s+INTO\s+" + tablo + r"\b", re.I)
    bulunan: list[tuple[Path, str]] = []
    for yol in sorted((BACKEND / "app").rglob("*.py")):
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        for dugum in ast.walk(agac):
            if not isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            govde = ast.unparse(dugum)
            if desen.search(govde) or f"insert({tablo})" in govde:
                bulunan.append((yol, dugum.name))
    return bulunan


def test_source_table_is_written_from_at_least_one_place() -> None:
    """Kapı BOŞA DÜŞMESİN: kaynak hiç yazılmıyorsa ölçüm anlamsızdır."""
    assert _insert_eden_fonksiyonlar(), (
        f"{KAYNAK_TABLO} hiçbir yerde yazılmıyor; bu kapı hiçbir şey ölçmüyor"
    )


def test_no_mapped_orm_class_exists() -> None:
    """DARALTILAN SÖZLEŞMENİN ÇAPASI — imkânsızlık ölçülür, varsayılmaz.

    Kaynak keşfi ``execute()`` tabanlı iki yazılışı tanır. Bu, ORM kalıcılığı
    OLMADIĞI sürece eksiksizdir: ``db.add(X(...))`` eşlenmiş bir sınıf ister.
    Burada eşleme KAYDI ölçülüyor — sözdizimi değil. Biri ORM getirirse bu
    test kırmızı olur ve keşfin genişletilmesi gerektiğini söyler.
    """
    import importlib

    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.orm import Mapper

    importlib.import_module("app.main")  # bütün router'ları yükler

    eslenmis: list[str] = []
    for ad, modul in list(sys.modules.items()):
        if not ad.startswith("app.") and ad != "app":
            continue
        for nitelik, nesne in list(vars(modul).items()):
            if not isinstance(nesne, type):
                continue
            try:
                if isinstance(sa_inspect(nesne, raiseerr=False), Mapper):
                    eslenmis.append(f"{ad}.{nitelik}")
            except Exception:  # noqa: BLE001 - eşlenmemiş sınıf: sessiz geç
                continue

    assert not eslenmis, (
        "ORM eşlenmiş sınıf(lar) bulundu: " + repr(sorted(set(eslenmis))) + ". "
        f"Bu kapının kaynak keşfi YALNIZ execute() tabanlı yazılışları tanıyor; "
        f"db.add(...) ile {KAYNAK_TABLO} satırı yazılabilir hale geldiyse keşif "
        "GENİŞLETİLMEK zorunda, yoksa yazıcı görünmez kalır."
    )

    kaynak = "\n".join(
        yol.read_text(encoding="utf-8")
        for yol in sorted((BACKEND / "app").rglob("*.py"))
    )
    kullanilan = [ad for ad in ORM_ESLEME_YUZEYI
                  if re.search(r"\b" + ad + r"\b", kaynak)]
    assert not kullanilan, (
        "SQLAlchemy ORM eşleme yüzeyi kullanılmaya başlanmış: "
        + repr(kullanilan) + ". Eşlenmiş sınıf henüz yoksa bile kaynak keşfi "
        "gözden geçirilmeli."
    )


def _uygulamaya_ait_metadata() -> dict[str, object]:
    """``app.*`` modüllerinin NİTELİĞİ olan MetaData örnekleri (yer -> nesne).

    Yansıtmayla oluşan MetaData'lar bilerek dışarıda: bkz. başlıktaki BEYAN
    EDİLEN SINIR. Ölçüldü — göç yansıtması ``field_activities`` için canlı bir
    Table üretiyor ve naif tarama kalıcı yanlış pozitif verirdi.
    """
    from sqlalchemy import MetaData

    bulunan: dict[str, object] = {}
    for ad, modul in list(sys.modules.items()):
        if not (ad == "app" or ad.startswith("app.")):
            continue
        for nitelik, nesne in list(vars(modul).items()):
            if isinstance(nesne, MetaData):
                bulunan[f"{ad}.{nitelik}"] = nesne
    return bulunan


def _modul_duzeyi_tablolar() -> dict[str, str]:
    """``app.*`` modül niteliği olan Table nesneleri: tablo adı -> yer."""
    from sqlalchemy import Table as _Table

    bulunan: dict[str, str] = {}
    for ad, modul in list(sys.modules.items()):
        if not (ad == "app" or ad.startswith("app.")):
            continue
        for nitelik, nesne in list(vars(modul).items()):
            if isinstance(nesne, _Table):
                bulunan.setdefault(nesne.name, f"{ad}.{nitelik}")
    return bulunan


def _table_insalari(tablo: str) -> list[str]:
    """İlk argümanı ``tablo`` olan HER ``Table(...)`` inşası (AST)."""
    bulunan: list[str] = []
    for yol in sorted((BACKEND / "app").rglob("*.py")):
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Call) or not dugum.args:
                continue
            ad = getattr(dugum.func, "id", None) or getattr(dugum.func, "attr", None)
            if ad != "Table":
                continue
            ilk = dugum.args[0]
            if isinstance(ilk, ast.Constant) and ilk.value == tablo:
                bulunan.append(f"{yol.relative_to(BACKEND)}:{dugum.lineno}")
    return bulunan


def test_no_core_table_for_the_source_exists() -> None:
    """DARALTMANIN İKİNCİ ÇAPASI — Core yazılışı da imkânsız olmalı.

    ORM çapası eşleme kaydına bakıyor; bunun Core karşılığı METADATA'dır.
    Üç katman da TABLO ADINA bakar, değişken adına değil: incelemenin verdiği
    ``fa_table = Table("field_activities", ...)`` şekli hangi ada bağlanırsa
    bağlansın yakalanır.
    """
    import importlib

    importlib.import_module("app.main")

    ihlaller: list[str] = []

    for yer, metadata in _uygulamaya_ait_metadata().items():
        if KAYNAK_TABLO in getattr(metadata, "tables", {}):
            ihlaller.append(f"MetaData {yer} içinde kayıtlı")

    modul_tablolari = _modul_duzeyi_tablolar()
    if KAYNAK_TABLO in modul_tablolari:
        ihlaller.append(f"modül düzeyi Table: {modul_tablolari[KAYNAK_TABLO]}")

    for yer in _table_insalari(KAYNAK_TABLO):
        ihlaller.append(f"Table(...) inşası: {yer}")

    assert not ihlaller, (
        f"{KAYNAK_TABLO} için Core Table nesnesi bulundu: {ihlaller!r}. "
        f"Bu kapının kaynak keşfi YALNIZ execute() tabanlı iki yazılışı tanıyor; "
        f"bir Table nesnesi üzerinden insert() ile {KAYNAK_TABLO} satırı "
        "yazılabilir hale geldiyse keşif GENİŞLETİLMEK zorunda, yoksa yazıcı "
        "görünmez kalır."
    )


def test_the_core_anchor_is_not_vacuous() -> None:
    """Çapa BOŞA DÜŞMESİN: uygulamanın Core kayıtları gerçekten okunuyor mu.

    Üç katmanın da hiçbir şey görmediği bir dünyada yukarıdaki test her zaman
    yeşil olurdu ve hiçbir şey ölçmezdi.
    """
    metadata = _uygulamaya_ait_metadata()
    assert metadata, "app.* altında hiç MetaData bulunamadı; çapa boşa düştü"
    kayitli = {ad for m in metadata.values() for ad in getattr(m, "tables", {})}
    assert len(kayitli) > 20, (
        f"uygulamaya ait MetaData'larda yalnız {len(kayitli)} tablo görünüyor; "
        "tarama beklenenden dar"
    )
    modul_tablolari = _modul_duzeyi_tablolar()
    assert len(modul_tablolari) > 20, (
        f"modül düzeyi Table taraması yalnız {len(modul_tablolari)} tablo buldu"
    )


def test_every_write_path_also_writes_the_outbox() -> None:
    """SINIF KURALI — varsayılan RED.

    ``field_activities``a INSERT eden bir fonksiyon outbox'a yazmıyorsa
    kırmızıdır. Yeni bir yazma yolu, kapı onu tanımasa bile yakalanır.
    """
    eksik = [
        (tablo, str(yol.relative_to(BACKEND)), ad)
        for tablo in sorted(KAPSANAN_KAYNAKLAR)
        for yol, ad in _insert_eden_fonksiyonlar(tablo)
        if YAZICI_ADI not in ast.unparse(
            next(
                d for d in ast.walk(ast.parse(yol.read_text(encoding="utf-8")))
                if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d.name == ad
            )
        )
    ]
    assert not eksik, (
        f"kapsanan kaynağa yazan ama outbox'a YAZMAYAN yol(lar): {eksik!r}. "
        f"Her yazma yolu aynı işlemde {OUTBOX_TABLO} satırı üretmek zorunda."
    )


def test_out_of_scope_field_tables_are_declared_not_forgotten() -> None:
    """KAPSAM SINIRI KAPIDA YAŞAR — PR raporunda değil.

    Alan tabloları göçlerden türetilir; kapsanan ve outbox'ın kendisi düşülür.
    Kalan küme, elle yazılmış gerekçeli listeyle EŞİT olmak zorunda. Yeni bir
    alan tablosu eklenirse kırmızı olur ve "olay üretmeli mi" sorusu sorulmuş
    olur — sessizce kör nokta büyümez.
    """
    turetilen = _alan_tablolari_gocten()
    assert KAPSANAN_KAYNAKLAR <= turetilen, (
        f"kapsanan kaynak göçlerde yok: {sorted(KAPSANAN_KAYNAKLAR - turetilen)}"
    )
    beklenen_disi = turetilen - KAPSANAN_KAYNAKLAR - OUTBOX_KAYNAK_DEGIL
    assert beklenen_disi == frozenset(KAPSAM_DISI_KAYNAKLAR), (
        "Kapsam dışı alan tabloları listesi göçlerle uyuşmuyor.\n"
        f"  göçlerden türetilen kapsam dışı : {sorted(beklenen_disi)}\n"
        f"  dosyada beyan edilen            : {sorted(KAPSAM_DISI_KAYNAKLAR)}\n"
        "Yeni bir alan tablosu eklendiyse: olay üretmesi gerekip gerekmediğine "
        "karar verin ve ya KAPSANAN_KAYNAKLARa ya da gerekçesiyle "
        "KAPSAM_DISI_KAYNAKLARa yazın."
    )
    for tablo in KAPSAM_DISI_KAYNAKLAR:
        assert KAPSAM_DISI_KAYNAKLAR[tablo].strip(), f"{tablo}: gerekçe boş"


def test_out_of_scope_tables_really_have_writers_today() -> None:
    """Sınır ÖLÇÜLÜR: kapsam dışı bırakılanların canlı yazıcısı var mı.

    Hiçbirinin yazıcısı olmasaydı sınır teorik olurdu; olduğu için bu kapının
    SUSTUĞU yer gerçek bir yüzeydir ve öyle yazılıdır.
    """
    yazicisi_olan = {
        tablo: sorted(f"{yol.name}:{ad}" for yol, ad in _insert_eden_fonksiyonlar(tablo))
        for tablo in sorted(KAPSAM_DISI_KAYNAKLAR)
    }
    assert any(yazicisi_olan.values()), (
        "kapsam dışı tabloların hiçbirinin yazıcısı yok; sınır ölçümü boşa düştü: "
        + repr(yazicisi_olan)
    )


def test_writer_does_not_commit_on_its_own() -> None:
    """Yazıcı commit ÇAĞIRMAMALI: olay faaliyetle aynı işlemde kalmalı.

    Kendi commit'ini çağırsaydı, faaliyet sonradan geri alındığında yetim bir
    olay kalırdı — 2. dilimin tüketicisi olmayan bir faaliyet için fiş yazardı.
    """
    farm = (BACKEND / "app" / "routers" / "farm.py").read_text(encoding="utf-8")
    agac = ast.parse(farm)
    yazici = next(
        (d for d in ast.walk(agac)
         if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d.name == YAZICI_ADI),
        None,
    )
    assert yazici is not None, f"{YAZICI_ADI} bulunamadı"
    assert ".commit()" not in ast.unparse(yazici), (
        f"{YAZICI_ADI} kendi commit'ini çağırıyor; olay faaliyetten AYRI bir "
        "işleme düşer ve geri alma yetim olay bırakır"
    )


_ORTAK_KURULUM = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

import app.routers.farm as farm
from app.db import SessionLocal
from app.main import app

ADMIN_PW = 'Outbox!2026'
ZAMAN = '2026-05-10T09:00:00+03:00'


def sayilar(cid):
    with SessionLocal() as o:
        f = o.execute(_sql('SELECT COUNT(*) FROM field_activities WHERE company_id=:c'),
                      {'c': cid}).scalar_one()
        e = o.execute(_sql('SELECT COUNT(*) FROM field_integration_events WHERE company_id=:c'),
                      {'c': cid}).scalar_one()
    return int(f), int(e)


c = TestClient(app)
login = c.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, ('KURULUM login', login.status_code, login.text)
body = login.json()
cid = body['companies'][0]['id']
h = {'Authorization':'Bearer ' + body['access_token'], 'X-Company-ID': str(cid)}
rot = c.post('/api/auth/change-password', headers=h,
             json={'current_password':'admin123','new_password':ADMIN_PW})
assert rot.status_code == 200, ('KURULUM rotation', rot.status_code, rot.text)
h['Authorization'] = 'Bearer ' + rot.json()['access_token']

ciftlik = c.post('/api/farms', headers=h, json={'code':'o1','name':'Outbox Çiftliği'})
assert ciftlik.status_code == 201, ('KURULUM ciftlik', ciftlik.status_code, ciftlik.text)
parsel = c.post('/api/farm-parcels', headers=h,
                json={'farm_id':ciftlik.json()['id'],'code':'op','name':'Outbox Parseli',
                      'area_decare':'40.0000'})
assert parsel.status_code == 201, ('KURULUM parsel', parsel.status_code, parsel.text)
sezon = c.post('/api/crop-seasons', headers=h,
               json={'parcel_id':parsel.json()['id'],'season_year':2026,'crop':'Buğday',
                     'started_on':'2026-03-01','planted_area_decare':'40.0000'})
assert sezon.status_code == 201, ('KURULUM sezon', sezon.status_code, sezon.text)
TABAN = {'season_id':sezon.json()['id'],'activity_type':'TILLAGE','performed_at':ZAMAN}
HASAT_TABAN = {'season_id':sezon.json()['id'],'harvested_on':'2026-08-15',
               'quantity':'12.5000','unit':'kg'}
'''


_SMOKE = _ORTAK_KURULUM + r'''

f0, e0 = sayilar(cid)
assert e0 == 0, ('KURULUM: outbox bos baslamali', e0)

# --- A) TAM BİR OLAY, ve içeriği ------------------------------------------
bir = c.post('/api/field-activities', headers=h, json={**TABAN, 'labor_hours':'3'})
assert bir.status_code == 201, ('A faaliyet', bir.status_code, bir.text)
aid = bir.json()['id']
f1, e1 = sayilar(cid)
assert f1 == f0 + 1, ('A faaliyet sayisi', f0, f1)
assert e1 == e0 + 1, ('A OLAY SAYISI TAM 1 OLMALI', e0, e1)

with SessionLocal() as o:
    satir = o.execute(_sql(
        'SELECT source_type,source_id,target,idempotency_key,status,attempts '
        'FROM field_integration_events WHERE company_id=:c ORDER BY id DESC'),
        {'c': cid}).mappings().first()
assert satir['source_type'] == 'field_activity', ('A source_type', dict(satir))
assert int(satir['source_id']) == int(aid), ('A source_id', dict(satir))
assert satir['target'] == 'stock', ('A target', dict(satir))
assert satir['status'] == 'PENDING', ('A status', dict(satir))
assert int(satir['attempts']) == 0, ('A attempts', dict(satir))
# Anahtar KAYNAKTAN türetilebilir olmalı — geri doldurma aynısını üretecek.
beklenen = 'field_activity:%d:stock' % int(aid)
assert satir['idempotency_key'] == beklenen, ('A anahtar', satir['idempotency_key'], beklenen)

# --- B) İKİNCİ FAALİYET: yine TAM 1, anahtarlar ÇAKIŞMAZ ------------------
iki = c.post('/api/field-activities', headers=h, json={**TABAN, 'labor_hours':'4'})
assert iki.status_code == 201, ('B faaliyet', iki.status_code, iki.text)
f2, e2 = sayilar(cid)
assert f2 == f1 + 1 and e2 == e1 + 1, ('B faaliyet basina TAM 1 olay', f1, f2, e1, e2)

# --- C) GERİ ALMA: yetim olay kalmamalı -----------------------------------
# Girdi doğrulaması INSERT'ten SONRA koşuyor; başarısızlık tüm işlemi geri alır.
kotu = c.post('/api/field-activities', headers=h, json={
    **TABAN, 'inputs':[{'product_id':999999,'input_name':'Yok','quantity':'1','unit':'kg'}]})
assert kotu.status_code >= 400, ('C reddedilmeliydi', kotu.status_code, kotu.text)
f3, e3 = sayilar(cid)
assert f3 == f2, ('C faaliyet geri alinmadi', f2, f3)
assert e3 == e2, ('C YETIM OLAY KALDI', e2, e3)

# --- D) OUTBOX YAZILAMAZSA FAALİYET DE YAZILMAZ ---------------------------
gercek = farm._entegrasyon_olayi_yaz


def patlat(*a, **k):
    raise RuntimeError('outbox yazilamadi')


farm._entegrasyon_olayi_yaz = patlat
try:
    try:
        bozuk = c.post('/api/field-activities', headers=h, json={**TABAN, 'labor_hours':'5'})
        kod = bozuk.status_code
    except RuntimeError:
        kod = 500  # istisna middleware'e kadar cikti: istek BASARISIZ
    assert kod >= 400, ('D outbox patladi ama istek BASARILI dondu', kod)
finally:
    farm._entegrasyon_olayi_yaz = gercek

f4, e4 = sayilar(cid)
assert f4 == f3, ('D FAALIYET SESSIZCE YAZILDI - outbox patlamasina ragmen', f3, f4)
assert e4 == e3, ('D olay sayisi degisti', e3, e4)

print('OUTBOX-YAZICI-TAMAM')
'''


_GOZLEMCI = r"""
# SONUÇ DÜZEYİ GÖZLEMCİ — ifadenin NASIL kurulduğuna bakmaz, cursor'a NE
# ulaştığına bakar. Ölçüldü: ham text(), dinamik adlı Core Table, fabrikayla
# üretilmiş Table ve YANSITMAYLA (autoload_with) kurulmuş Table — dördü de
# burada AYNI biçimde görünüyor.
import re as _re

from sqlalchemy import Engine as _Engine, event as _event

KAYNAK = 'field_activities'
HASAT = 'field_harvests'
OUTBOX = 'field_integration_events'
# TÜRETİLMİŞ — test modülü göç ağacından çıkarıp buraya ENJEKTE eder.
KAYNAKLAR = @@KAYNAKLAR@@
KAYNAK_TIPI = @@KAYNAK_TIPI@@

KESIN = []   # yalnız COMMIT edilmiş işlemler: [{tablo adı: satır adedi}]
_DESEN = _re.compile(r"INSERT\s+INTO\s+[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)", _re.I)


@_event.listens_for(_Engine, 'begin')
def _olcum_basla(conn):
    conn.info['olcum'] = {}


# ÖLÇÜLEN NİCELİK = ETKİLENEN SATIR SAYISI (ifade sayısı DEĞİL).
#
# ESKİ HÂLİ `before_cursor_execute` idi ve `len(parameters) if executemany
# else 1` sayıyordu. Bu, İDDİA EDİLEN nicelik ("N satır -> N olay") ile
# ÖLÇÜLEN niceliği ayırıyordu. Gerçek veritabanında ölçüldü:
#
#   ifade                              beklenen   ESKİ   ROWCOUNT
#   tek INSERT                                1      1          1
#   executemany N=3                           3      3          3
#   INSERT ... SELECT (3 satır)               3      1 X        3
#   INSERT ... ON CONFLICT DO NOTHING (0)     0      1 X        0
#   UPDATE eşleşme yok                        0      -          0
#   DELETE 2 satır                            2      -          2
#
# `before_` kancası ÇALIŞMADAN ÖNCE ateşlendiği için etkilenen satırı
# BİLEMEZ; bu yapısal bir sınırdı, ayar meselesi değil. Kanca
# `after_cursor_execute`e alındı ve nicelik `cursor.rowcount` oldu.
#
# --- BİÇİM SEÇİCİ: VARSAYILAN SAYILAMAZ (fail-closed) ---------------------
#
# ÖNCEKİ HÂLİN KUSURU ÖLÇÜLDÜ (gerçek PostgreSQL 16):
#     INSERT INTO t (v) VALUES ('r1'), ('r2') RETURNING id
#     executemany False -> len(parameters) if executemany else 1 -> 1
#     tablo 2 satır yazdı.
# Yani TANINMAYAN bir biçim SAYAN dala düşüyordu ve N-1 kaynak yazımı
# olaysız commit edebilirdi. Bu yüzden mantık TERSİNE çevrildi: bir biçim
# ancak sayılabilirliği POZİTİF olarak kurulduğunda sayma dalına girer;
# kurulamayan her şey SAYILAMAZ'dır.
#
# Sayma dalına giriş koşulları (hepsi ÖLÇÜLEREK belirlendi):
#   1. RETURNING YOK            -> cursor.rowcount (sürücü bildiriyorsa)
#   2. RETURNING + executemany  -> kardinalite parametre sayısıdır
#   3. RETURNING + TEK demetli, sade INSERT..VALUES -> 1
#   4. başka her şey            -> SAYILAMAZ
#
# RETURNING İSTİSNASININ SEBEBİ ÖLÇÜLDÜ: sqlite3 INSERT ... RETURNING
# için satır eklense bile rowcount=0 bildiriyor (düz INSERT'te 1). psycopg
# doğru bildiriyor. Uygulamanın kendi yazma yolu tam bu biçim olduğu için
# "her şeyi rowcount ile say" tek başına kapıyı YANLIŞ KIRMIZI yapıyordu.
#
# TOKEN ARAMASI METİN ÜZERİNDE DEĞİL, MASKELENMİŞ METİN ÜZERİNDE yapılır:
# string sabitleri ve yorumlar boşlukla doldurulur. Ölçüldü -- maskesiz
# arama iki yönde de yanlıştı: VALUES ('RETURNING a'),('RETURNING b')
# (2 satır) 1 sayılıyordu, VALUES (' SELECT ') ise haksız yere
# REDDEDİLİYORDU.
#
# NEDEN DEMET SAYMIYORUZ (ölçerek elendi): "demet gruplarını say" adayı
# gerçek veritabanında ÇÜRÜTÜLDÜ --
#     BEFORE INSERT tetikleyicisi bir satırı düşürünce
#     VALUES ('engel'),('gecer') -> aday 2 dedi, tablo 1 yazdı.
# Demet sayısı ile yazılan satır sayısı ÖZDEŞ DEĞİL. Çok demetli VALUES
# bu yüzden sayılmaz, REDDEDİLİR. Bedeli ölçüldü: SQLAlchemy 2.0.51 bu
# yığındaki toplu yazımı executemany=True + TEK demetli VALUES olarak
# yayıyor (hem PG16 hem sqlite), yani uygulamanın kendi yolu bu redde
# TAKILMIYOR.
SAYILAMAYAN = []   # [(tablo, ifade)] -- sayılabilirliği kurulamayan yazmalar

_VALUES_RE = _re.compile(r'\bVALUES\b', _re.I)
_RETURNING_RE = _re.compile(r'\bRETURNING\b', _re.I)
_SELECT_RE = _re.compile(r'\bSELECT\b', _re.I)
_DML_RE = _re.compile(r'\b(INSERT|UPDATE|DELETE|MERGE|WITH)\b', _re.I)
# Çatışma çözümü demet-satır özdeşliğini bozar: ölçüldü, ON CONFLICT DO
# NOTHING ile tek demetli VALUES 0 satır yazdı ama aday "1" diyordu.
_CATISMA_RE = _re.compile(
    r'\bON\s+CONFLICT\b|\bON\s+DUPLICATE\b|\bOR\s+(IGNORE|REPLACE)\b', _re.I)


def _maskele(ifade):
    # String sabitlerini ve yorumları BOŞLUKLA doldur, uzunluğu koru.
    cikti = []
    i, n = 0, len(ifade)
    while i < n:
        c = ifade[i]
        if c == "'":
            cikti.append(' ')
            i += 1
            while i < n:
                if ifade[i] == "'":
                    if i + 1 < n and ifade[i + 1] == "'":
                        cikti.append('  ')
                        i += 2
                        continue
                    cikti.append(' ')
                    i += 1
                    break
                cikti.append('\n' if ifade[i] == '\n' else ' ')
                i += 1
            continue
        if c == '"':
            cikti.append(' ')
            i += 1
            while i < n:
                cikti.append(' ')
                if ifade[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == '-' and i + 1 < n and ifade[i + 1] == '-':
            while i < n and ifade[i] != '\n':
                cikti.append(' ')
                i += 1
            continue
        if c == '/' and i + 1 < n and ifade[i + 1] == '*':
            while i < n and not (ifade[i] == '*' and i + 1 < n and ifade[i + 1] == '/'):
                cikti.append('\n' if ifade[i] == '\n' else ' ')
                i += 1
            for _ in range(min(2, n - i)):
                cikti.append(' ')
            i += 2
            continue
        cikti.append(c)
        i += 1
    return ''.join(cikti)


def _values_demet_sayisi(ifade):
    # VALUES yan tümcesindeki demet sayısı; okunamazsa None.
    m = _maskele(ifade)
    e = _VALUES_RE.search(m)
    if not e:
        return None
    i, n = e.end(), len(m)
    derinlik, grup = 0, 0
    while i < n:
        c = m[i]
        if c == '(':
            if derinlik == 0:
                grup += 1
            derinlik += 1
        elif c == ')':
            derinlik -= 1
            if derinlik < 0:
                return None
            if derinlik == 0:
                j = i + 1
                while j < n and m[j].isspace():
                    j += 1
                if j >= n or m[j] != ',':
                    break
        i += 1
    if derinlik != 0 or grup == 0:
        return None
    return grup


def _sade_insert_values(ifade):
    # Sayılabilirliği POZİTİF kur: tek hedefli, dallanmasız INSERT..VALUES.
    m = _maskele(ifade)
    if _CATISMA_RE.search(m):
        return False
    if len(_DML_RE.findall(m)) != 1:
        return False           # CTE, ikinci DML ya da WITH -> sayılamaz
    ilk = _DML_RE.search(m)
    if ilk is None or ilk.group(1).upper() != 'INSERT':
        return False
    if m[:ilk.start()].strip():
        return False
    if _SELECT_RE.search(m):
        return False           # kaynak bir sorgu -> kardinalite okunamaz
    return True


@_event.listens_for(_Engine, 'after_cursor_execute')
def _olcum_yaz(conn, cursor, statement, parameters, context, executemany):
    sayac = conn.info.get('olcum')
    if sayac is None:
        return   # açık işlem yok; böyle bir yazma zaten kurala giremez
    m = _DESEN.search(statement or '')
    if not m:
        return
    ad = m.group(1).lower()
    ifade = statement or ''
    maskeli = _maskele(ifade)

    if not _RETURNING_RE.search(maskeli):
        adet = cursor.rowcount
        if adet is None or adet < 0:
            SAYILAMAYAN.append((ad, ifade[:120]))
            return
    elif executemany:
        if parameters is None:
            SAYILAMAYAN.append((ad, ifade[:120]))
            return
        adet = len(parameters)
    elif _sade_insert_values(ifade) and _values_demet_sayisi(ifade) == 1:
        adet = 1
    else:
        # SAYILABİLİRLİK KURULAMADI. Varsayılan budur.
        SAYILAMAYAN.append((ad, ifade[:120]))
        return
    if ad == OUTBOX:
        # KORUNUM (conservation), YÖNLENDİRME DEĞİL. Her outbox satırı MUTLAKA
        # bir kovaya yazılır; ayrıştırılamayan satır `olay:?` kovasına gider ve
        # kural onu İHLAL sayar. Sayımdan DÜŞÜRÜLMEZ.
        #
        # ÖNCEKİ HÂLİN KUSURU ÖLÇÜLDÜ: ayrıştırılamayan satır `return` ile
        # atlanıyordu, yani ne kovaya ne de toplama giriyordu. Böylece
        # "dağıtılan == toplam" korunumu KENDİLİĞİNDEN sağlanıyor ve aynı
        # işlemdeki DOĞRU bir olayın yanında gizlenebiliyordu. Ölçüm: 1 hasat
        # satırı + 1 doğru hasat olayı + 1 kapsanmayan olay -> kapı YEŞİLDİ.
        # Dengeleyen terim kaynaklar ARASINDAN kovaya taşınmıştı; aynı
        # aritmetik, bir kat aşağıda.
        derlenmis = getattr(context, 'compiled_parameters', None)
        if derlenmis and len(derlenmis) == adet:
            _tipler = []
            for _satir in derlenmis:
                _t = _satir.get('source_type') if hasattr(_satir, 'get') else None
                _tipler.append(str(_t) if _t else '?')
        else:
            _tipler = ['?'] * adet
        for _t in _tipler:
            _anahtar = 'olay:' + _t
            sayac[_anahtar] = sayac.get(_anahtar, 0) + 1
        if '?' in _tipler:
            SAYILAMAYAN.append((ad, ifade[:120]))
    sayac[ad] = sayac.get(ad, 0) + adet


@_event.listens_for(_Engine, 'commit')
def _olcum_commit(conn):
    sayac = conn.info.pop('olcum', None)
    if sayac:
        KESIN.append(sayac)


@_event.listens_for(_Engine, 'rollback')
def _olcum_rollback(conn):
    # Geri alınan işlem HİÇBİR ŞEY yazmadı. Kurala sokulsaydı kapı kendi
    # negatif senaryolarında (outbox'ı patlatma) yanlış kırmızı verirdi.
    conn.info.pop('olcum', None)


"""


_KURAL_GOVDESI = r'''
# --- KURAL: commit edilmiş HİÇBİR işlem kaynağı outbox'sız yazmasın -------
for etiket, govde in (
    ('basit', {**TABAN, 'labor_hours': '3'}),
    ('girdili', {**TABAN, 'labor_hours': '2', 'inputs': [
        {'input_name': 'Gübre', 'quantity': '5', 'unit': 'kg'}]}),
):
    y = c.post('/api/field-activities', headers=h, json=govde)
    assert y.status_code == 201, (etiket, y.status_code, y.text)

# 2. DİLİM: HASAT. Hasat STOK GİRİŞİdir; olayı düşen bir hasat, fiziken var
# olan ürünün hiç kaydedilmemesi demektir.
_hs = c.post('/api/field-harvests', headers=h, json=HASAT_TABAN)
assert _hs.status_code == 201, ('hasat', _hs.status_code, _hs.text)

# GÖZLEMCİ KÖR MÜ. Bu çapa olmasaydı hiçbir şey görmeyen bir gözlemci de
# yeşil kalırdı ve aşağıdaki kural boşa düşerdi.
assert len(KESIN) >= 4, ('GÖZLEMCİ KÖR: kurulum yazmaları bile görünmedi', KESIN)
kaynakli = [t for t in KESIN if any(t.get(k) for k in KAYNAKLAR)]
assert len(kaynakli) >= 3, (
    'GÖZLEMCİ KÖR: commit edilmiş işlemlerde beklenen %r INSERT bulunamadı: %r'
    % (KAYNAKLAR, KESIN))
# HER KAPSANAN KAYNAK AYRI AYRI görünmeli: yalnız faaliyet görülüp hasat hiç
# görülmeseydi hasat kuralı VAKUMDA geçerdi.
for _k in KAYNAKLAR:
    assert any(t.get(_k) for t in KESIN), (
        'GÖZLEMCİ KÖR: %s için commit edilmiş INSERT yok; bu kaynağın kuralı '
        'vakumda geçerdi: %r' % (_k, KESIN))

# ASIL KURAL — SAYI EŞİTLİĞİ. "en az bir olay var" DEĞİL: aynı işlemde
# FAZLADAN bir kaynak satırı yazan bir yol da böylece kırmızı olur.
# ASIL KURAL — KAYNAK BAŞINA EŞİTLİK. TOPLAM ÜZERİNDEN DENETİM FAIL-OPEN'DIR:
# bir kaynakta EKSİK olay, başka bir kaynakta FAZLA olayla dengelenir ve
# toplam tutar. `Σ kaynak = Σ olay`, `∀k: kaynak_k = olay_k` ifadesinin ZAYIF
# bir SONUCUDUR; kapı tümel olanı taşımak zorunda. Toplam yalnız raporda.
ihlal = []
for _k in KAYNAKLAR:
    _tip = KAYNAK_TIPI[_k]
    # Kapsam, ÖNCEKİ HÂLLE AYNI: kapsanan bir kaynağa yazan işlemler. Kaynak
    # satırı olmayan bir işlemdeki yetim olay AYRI bir kusur sınıfıdır ve bu
    # turun kusuru değildir; kapsamı sessizce genişletmiyorum.
    for t in kaynakli:
        _kaynak_adedi = t.get(_k, 0)
        _olay_adedi = t.get('olay:' + _tip, 0)
        if _kaynak_adedi != _olay_adedi:
            ihlal.append({'kaynak': _k, 'kaynak_satiri': _kaynak_adedi,
                          'olay_satiri': _olay_adedi, 'islem': t})
assert not ihlal, (
    'SONUÇ İHLALİ (KAYNAK BAŞINA): commit edilmiş bir işlemde bir KAPSANAN '
    'KAYNAĞIN satır sayısı, O KAYNAĞA ait outbox olayı sayısına eşit değil: '
    '%r. Denetim kaynak başınadır; toplam üzerinden bakılsaydı bir kaynaktaki '
    'eksik olay diğerindeki fazlayla dengelenir ve kapı yeşil kalırdı.'
    % (ihlal,))

# KAPSAM KURALI — HER OLAY BİR KAPSANAN KAYNAĞA AİT OLMALI.
# Bir outbox satırı ya kapsanan bir kaynağın tipini taşır ya da İHLALDİR.
# "Ayrıştırılamayan" bir kova DEĞİL, kuralın ta kendisidir: kovaya taşınan
# terim, sayımdan düşürülmediği için burada görünür.
KAPSANAN_TIPLER = frozenset(KAYNAK_TIPI.values())
kapsam_disi_olay = []
for t in kaynakli:
    for _k2, _v in t.items():
        if not _k2.startswith('olay:') or not _v:
            continue
        _tip = _k2[len('olay:'):]
        if _tip not in KAPSANAN_TIPLER:
            kapsam_disi_olay.append(
                {'olay_tipi': _tip, 'adet': _v, 'islem': t})
assert not kapsam_disi_olay, (
    'KAPSAM İHLALİ: commit edilmiş bir işlemde HİÇBİR KAPSANAN KAYNAĞA ait '
    'olmayan outbox olayı var: %r. Kapsanan tipler: %r. Bir olay ya kapsanan '
    'bir kaynağa aittir ya da ihlaldir; "ayrıştırılamayan" ayrı bir kova '
    'DEĞİLDİR. Böyle bir olay sayımdan düşürülseydi, aynı işlemdeki doğru bir '
    'olayın yanında gizlenirdi.' % (kapsam_disi_olay, sorted(KAPSANAN_TIPLER)))

# TOPLAM YALNIZ RAPOR: kural DEĞİL. Ayrıca ayrıştırılamamış olay kalmadığını
# gösterir — kaynak tiplerine dağıtılan olay sayısı toplam olay sayısını
# tutmuyorsa bir olay hiçbir kaynağa yazılmamış demektir.
for t in KESIN:
    _dagitilmis = sum(v for k2, v in t.items() if k2.startswith('olay:'))
    assert _dagitilmis == t.get(OUTBOX, 0), (
        'OLAY AYRIŞTIRILAMADI: işlemdeki %s satırı %d, kaynaklara dağıtılan '
        '%d: %r' % (OUTBOX, t.get(OUTBOX, 0), _dagitilmis, t))

# SAYILAMAYAN YAZMA BİÇİMİ = İHLAL. Sayamadığını 1 diye saymak, ölçülmemiş
# bir şeyi ölçülmüş göstermek olurdu.
assert not SAYILAMAYAN, (
    'SAYILAMAYAN YAZMA: sürücü bu ifadeler için rowcount bildirmedi, yani '
    'etkilenen satır sayısı ÖLÇÜLEMEDİ. Kapı sayamadığı biçimi geçiremez: %r'
    % (SAYILAMAYAN,))

# --- KİRACI KİMLİĞİ — SAYI EŞİTLİĞİ YETMEZ -------------------------------
# Sayılar birebir tutarken kimlik kayabilir: 1:1 koruyup YANLIŞ company_id
# yazan bir yazıcı yukarıdaki kuraldan geçerdi. Bu depoda RLS yok; statik
# kiracı kapısı onun bildirilmiş yerine geçiyor, bu yüzden yanlış firmaya
# yazılan bir outbox olayı küçük bir şey değildir.
from sqlalchemy import text as _text
from app.db import SessionLocal as _SL

with _SL() as _db:
    _eslesmeyen = _db.execute(_text(
        'SELECT e.id, e.company_id, a.company_id '
        'FROM field_integration_events e '
        'JOIN field_activities a ON a.id = e.source_id '
        "WHERE e.source_type = 'field_activity' "
        'AND e.company_id <> a.company_id'
    )).fetchall()
    _olay = _db.execute(_text(
        "SELECT COUNT(*) FROM field_integration_events e "
        "JOIN field_activities a ON a.id = e.source_id "
        "WHERE e.source_type = 'field_activity'"
    )).scalar_one()
    _eslesmeyen_h = _db.execute(_text(
        'SELECT e.id, e.company_id, x.company_id '
        'FROM field_integration_events e '
        'JOIN field_harvests x ON x.id = e.source_id '
        "WHERE e.source_type = 'field_harvest' "
        'AND e.company_id <> x.company_id'
    )).fetchall()
    _olay_h = _db.execute(_text(
        "SELECT COUNT(*) FROM field_integration_events e "
        "JOIN field_harvests x ON x.id = e.source_id "
        "WHERE e.source_type = 'field_harvest'"
    )).scalar_one()

assert _olay > 0, 'KİMLİK KAPISI VAKUMDA: hiç outbox olayı yok, kural boşa düşer'
assert not _eslesmeyen, (
    'KİRACI KİMLİĞİ İHLALİ: outbox olayının company_id değeri kaynak '
    'faaliyetinkinden farklı: %r' % (_eslesmeyen,))
assert _olay_h > 0, (
    'HASAT KİMLİK KAPISI VAKUMDA: hiç hasat olayı yok, kural boşa düşer')
assert not _eslesmeyen_h, (
    'KİRACI KİMLİĞİ İHLALİ (HASAT): outbox olayının company_id değeri kaynak '
    'hasadınkinden farklı: %r' % (_eslesmeyen_h,))
print('KIMLIK-EslESME-TAMAM olay=%d hasat=%d' % (_olay, _olay_h))

# --- CANLI KATALOG: DOLAYLILIK VE GÖÇ DIŞI NESNE -------------------------
#
# GARANTİ CÜMLESİ (kapının VAAT ETTİĞİ şey, tam olarak bu kadarı):
#
#     BU KAPININ KOŞTUĞU VERİTABANINDA, kapsanan tabloların hiçbiri
#     tetikleyici, görünüm, kural (RULE) ya da bölüm (partition) ilişkisi
#     TAŞIMAZ.
#
# Cümle bilerek DAR yazılmıştır. "Üretimde taşımaz" DEMEZ; kapı üretimi
# gözlemez ve gözlemediği bir şeyi vaat etmesi cümleyi YANLIŞ yapardı.
# Üretimin göç dışında böyle bir nesne kazanması bu vaadin DIŞINDADIR:
# canlı kataloğa karşı bir OPERASYON denetimidir, CI kapısı değil, ve bu
# PR'ın kapsamında değildir.
#
# NEDEN BU SINIR VAR. Kapı ifadenin HEDEFİNE bakarak sayıyor. Satırlar
# kapsanan tabloya BAŞKA bir hedef üzerinden inerse bu sayım yanlıştır.
# ÖLÇÜLDÜ (kapının KENDİ gözlemci kaynağıyla, gerçek veritabanlarında):
#
#   aparat            ifade hedefi                   inen satır  kapı saydı
#   SQLite 3.49.1     görünüm (INSTEAD OF tetikl.)            1           0
#   PostgreSQL 16.4   görünüm (INSTEAD OF tetikl.)            1           0
#   PostgreSQL 16.4   RULE ile yönlendirme                    1           0
#   PostgreSQL 16.4   BÖLÜMLENMİŞ ebeveyne yazım              1           0
#   PostgreSQL 16.4   elle tetikleyici + RETURNING            0           1
#
# İlk dördü EKSİK SAYIM (kaynak satırı olay borcu doğurmadan commit eder),
# sonuncusu FAZLA SAYIM (outbox tarafında eksik olayı gizler).
#
# Bölümlenmiş ebeveyn şekli, kapı `pg_inherits` sormadığı sürece SESSİZCE
# geçiyordu: bir önceki hâl (e5a2729) bu eksik sayımı yakalamıyordu.
#
# Elle eklenen tetikleyici GÖÇ AĞACINDAN GÖRÜNMEZ: veritabanına elle
# eklendiğinde göç taraması onu bulamadı (ölçüldü: False), CANLI KATALOG
# buldu (['trg_elle']). Bu yüzden sınır DEPO DOSYALARINI değil, kapının
# koştuğu VERİTABANININ KENDİSİNİ gözlüyor.
#
# KATALOG OKUNAMAZSA: ölçüldü — sorgu hata veriyor, çocuk süreç düşüyor,
# kapı KIRMIZI oluyor. Sessizce yeşil kalmıyor.
_dialect = _db.get_bind().dialect.name
if _dialect == 'sqlite':
    _nesneler = _db.execute(_text(
        "SELECT type, name, COALESCE(tbl_name,''), COALESCE(sql,'') "
        "FROM sqlite_master WHERE type IN ('trigger','view')")).fetchall()
else:
    _nesneler = _db.execute(_text(
        "SELECT 'trigger', t.tgname, c.relname, COALESCE(pg_get_triggerdef(t.oid),'') "
        "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE NOT t.tgisinternal "
        "UNION ALL "
        "SELECT 'rule', r.rulename, r.tablename, COALESCE(r.definition,'') "
        "FROM pg_rules r WHERE r.schemaname NOT IN ('pg_catalog','information_schema') "
        "UNION ALL "
        "SELECT 'view', v.viewname, v.viewname, COALESCE(v.definition,'') "
        "FROM pg_views v WHERE v.schemaname NOT IN ('pg_catalog','information_schema') "
        "UNION ALL "
        "SELECT 'bolum', c.relname, p.relname, 'bolumlenmis ebeveyn: ' || p.relname "
        "FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent "
        "JOIN pg_class c ON c.oid = i.inhrelid"
    )).fetchall()

# VAKUM KARŞITI: katalog sorgusu ÇALIŞIYOR mu? Hiçbir nesne göremeyen bir
# sorgu da "temiz" der. Depoda tetikleyici olduğu ÖLÇÜLDÜ, o yüzden bu
# veritabanında en az bir tetikleyici görünmeli.
_tetik = [n for n in _nesneler if n[0] == 'trigger']
assert _tetik, (
    'KATALOG SORGUSU VAKUMDA: %s lehçesinde hiç tetikleyici görülmedi. '
    'Depoda tetikleyici tanımlı olduğu ölçüldü; sorgu bir şey ölçmüyorsa '
    'bu sınır bildirimi anlamsızdır.' % (_dialect,))

_KAPSANAN = KAYNAKLAR + (OUTBOX,)
_dolayli = []
for _tur, _ad, _hedef, _tanim in _nesneler:
    _h = (_hedef or '').lower()
    _t = (_tanim or '').lower()
    _a = (_ad or '').lower()
    if _a in _KAPSANAN and _tur == 'bolum':
        # ÖLÇÜLDÜ (PG 16.4): kapsanan tablo bölümlenmiş bir ebeveynin BÖLÜMÜ
        # olduğunda ebeveyne yazılan satır çocuğa iner; ifadenin hedefi
        # ebeveyndir ve kapı 1 yerine 0 sayar (EKSİK SAYIM).
        _dolayli.append((_tur, _ad, _hedef, 'kapsanan tablo BIR BOLUM'))
    elif _h in _KAPSANAN:
        _dolayli.append((_tur, _ad, _hedef, 'kapsanan tabloyu HEDEFLIYOR'))
    elif any(k in _t for k in _KAPSANAN):
        _dolayli.append((_tur, _ad, _hedef, 'tanimi kapsanan tabloya DEGINIYOR'))

assert not _dolayli, (
    'CANLI KATALOGDA DOLAYLILIK: %r. GARANTİ CÜMLESİ: bu kapının koştuğu '
    'veritabanında kapsanan tablolar tetikleyici, görünüm, kural ya da '
    'bölüm ilişkisi TAŞIMAZ. Kapı ifadenin hedefine bakarak sayıyor; '
    'görünüm, kural ya da tetikleyici araya girdiğinde saydığı sayı ile '
    'kapsanan tabloya inen satır sayısı AYRIŞIR (ölçüldü: görünüm üzerinden '
    'inen 1 satır 0 sayıldı; elle eklenen tetikleyici altında inen 0 satır '
    '1 sayıldı). Bu nesneler varken kapının yeşili bir şey KANITLAMAZ.'
    % (_dolayli,))
print('KATALOG-TEMIZ nesne=%d tetikleyici=%d' % (len(_nesneler), len(_tetik)))

print('OUTBOX-SONUC-TAMAM')
'''


def _gozlemci_kaynagi() -> str:
    """Gözlemciyi TÜRETİLMİŞ kaynak kümesiyle doldurur."""
    kaynaklar = tuple(sorted(_kapsanan_kaynaklar()))
    harita = {k: v for k, v in sorted(_kaynak_tipi_haritasi().items())
              if k in kaynaklar}
    eksik = [k for k in kaynaklar if k not in harita]
    assert not eksik, (
        f"kaynak tipi türetilemeyen kapsanan tablo(lar): {eksik}; kaynak "
        "başına kural kurulamaz"
    )
    return (
        _GOZLEMCI
        .replace("@@KAYNAKLAR@@", repr(kaynaklar))
        .replace("@@KAYNAK_TIPI@@", repr(harita))
    )


def _smoke_gozlem() -> str:
    return _gozlemci_kaynagi() + _ORTAK_KURULUM + _KURAL_GOVDESI


def _kos(kaynak: str, database_url: str, imza: str) -> None:
    """Smoke'u AYRI süreçte koşar; gözlemci ana test sürecini kirletmesin."""
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", kaynak],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert imza in completed.stdout, completed.stdout


def test_no_committed_transaction_writes_the_source_without_the_outbox(
    tmp_path: Path,
) -> None:
    """DİNAMİK İNŞA SINIFINI kapatan çapa — inşayı değil SONUCU ölçer.

    Yukarıdaki üç statik katman ``Table`` nesnesinin NASIL kurulduğunu arıyor
    ve inceleme haklı sordu: adı çalışma zamanında üretilen bir tablo üçünden
    de kaçar. Bu test kovalamacayı tersine çeviriyor — cursor'a ulaşan her
    ``field_activities`` INSERT'i, AYNI işlemde aynı SAYIDA outbox INSERT'iyle
    birlikte olmak zorunda.

    ÖLÇÜLDÜ (prob, dört yazılış): ham ``text()``, dinamik adlı ``Table``,
    fabrikayla üretilmiş ``Table`` ve ``autoload_with`` ile YANSITILMIŞ
    ``Table`` — dördü de bu gözlemcide aynı biçimde görünüyor. Yani tek
    mekanizma hem dinamik inşa sınıfını hem statik katmanların yansıtma
    boşluğunu kapatıyor.
    """
    _kos(_smoke_gozlem(), f"sqlite:///{(tmp_path / 'gozlem.db').as_posix()}",
         "OUTBOX-SONUC-TAMAM")


def _run_smoke(database_url: str) -> None:
    _kos(_SMOKE, database_url, "OUTBOX-YAZICI-TAMAM")


def test_every_activity_write_produces_exactly_one_outbox_row(tmp_path: Path) -> None:
    _run_smoke(f"sqlite:///{(tmp_path / 'outbox.db').as_posix()}")


# ---------------------------------------------------------------------------
# HAM DBAPI SINIRI — YÜRÜTÜLEBİLİR BİLDİRİM (PR gövdesinde düzyazı DEĞİL)
#
# Sonuç gözlemcisi SQLAlchemy Engine kancalarına dayanır.
# `engine.raw_connection().cursor()` bu kancaları ATLAR — runtime lens bunu
# canlı ölçtü. Sözleşme lens'i ise şunu söyledi: bu sınır ancak uygulama yazma
# sözleşmesinin AÇIKÇA dışındaysa COST'tur, aksi hâlde HOLE.
#
# BU YÜZDEN GARANTİ BURADA, YÜRÜTÜLEBİLİR BİÇİMDE BİLDİRİLİYOR:
#   "Bu kapının garantisi, UYGULAMA ENGINE'İNE ulaşan yazmaları kapsar."
# ve bildirimin doğru kalması aşağıdaki testle ZORLANIR: uygulamada ham DBAPI
# yüzeyi ölçülür ve kapsanan tablolara ham yoldan yazan bir yol belirirse
# kırmızı yanar. Tasarım zaten iki inşa katmanını yasaklıyor (eşlenmiş ORM
# sınıfı KIRMIZI, Core Table KIRMIZI); üçüncü katman artık ne yasaksız ne
# bildirimsiz.
# ---------------------------------------------------------------------------

KAPSANAN_TABLOLAR = ("field_activities", "field_harvests",
                     "field_integration_events")

# Ölçüldü (AST, backend/app): ham DBAPI yalnız yedekleme yolunda ve hiçbiri
# kapsanan tablolara yazmıyor — hepsi dosya kopyalama / PRAGMA / alembic_version.
BEKLENEN_HAM_DBAPI_DOSYALARI = ("app/database_backup.py",)


def _ham_dbapi_siteleri() -> list[tuple[str, int, str]]:
    import ast as _ast
    kok = BACKEND / "app"
    bulunan: list[tuple[str, int, str]] = []
    for yol in sorted(kok.rglob("*.py")):
        agac = _ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        bagil = yol.relative_to(BACKEND).as_posix()
        for dugum in _ast.walk(agac):
            if not (isinstance(dugum, _ast.Call) and isinstance(dugum.func, _ast.Attribute)):
                continue
            if dugum.func.attr == "raw_connection":
                bulunan.append((bagil, dugum.lineno, "raw_connection"))
            elif dugum.func.attr == "connect":
                taban = dugum.func.value
                if isinstance(taban, _ast.Name) and taban.id in {"sqlite3", "psycopg", "psycopg2"}:
                    bulunan.append((bagil, dugum.lineno, f"{taban.id}.connect"))
    return bulunan


def test_ham_dbapi_siniri_BILDIRILDI_ve_kapsanan_tabloya_dokunmuyor() -> None:
    """Sınır YÜRÜTÜLEBİLİR: ham DBAPI kapsanan tablolara yazmamalı.

    Bu test kırmızıya döndüğünde bildirim artık doğru değildir; o an ya yol
    kaldırılmalı ya da garanti yeniden yazılmalıdır.
    """
    siteler = _ham_dbapi_siteleri()
    dosyalar = sorted({d for d, _, _ in siteler})
    assert dosyalar == sorted(BEKLENEN_HAM_DBAPI_DOSYALARI), (
        "Ham DBAPI yüzeyi değişti; sonuç gözlemcisi bu yolu GÖRMEZ. Yeni yol "
        "ya kaldırılmalı ya da garanti yeniden bildirilmelidir: %r" % (siteler,)
    )
    for bagil in dosyalar:
        kaynak = (BACKEND / bagil).read_text(encoding="utf-8")
        for tablo in KAPSANAN_TABLOLAR:
            assert tablo not in kaynak, (
                "%s ham DBAPI kullanıyor VE kapsanan tabloya (%s) değiniyor; "
                "bu bir COST değil HOLE olurdu." % (bagil, tablo)
            )


# ---------------------------------------------------------------------------
# TETİKLEYİCİ SINIRI — ÖLÇÜLEREK BULUNDU, YÜRÜTÜLEBİLİR OLARAK BİLDİRİLİR
# ---------------------------------------------------------------------------
# Gerçek PostgreSQL 16'da ölçüldü: BEFORE INSERT tetikleyicisi NULL
# döndürerek satırı düşürdüğünde
#     INSERT INTO f81 (v) VALUES ('engel') RETURNING id
# tabloya 0 satır yazdı, gözlemci ise 1 saydı. Yani bir tetikleyici,
# "ifade tek demetli INSERT..VALUES ise tam 1 satır yazar" özdeşliğini
# BOZAR. Kaynak tarafında bu YANLIŞ KIRMIZI üretir (zararsız), ama OUTBOX
# tarafında sayıyı şişirerek EKSİK OLAYI GİZLERDİ — yani HOLE olurdu.
#
# Bu yüzden sınır burada YÜRÜTÜLEBİLİR biçimde bildiriliyor: göç ağacında
# kapsanan tablolara tanımlanmış tetikleyici YOKTUR. Ölçüldü: depoda
# tetikleyici var (activity_logs, notifications_archive) ama kapsanan iki
# tablonun ikisinde de yok. Biri eklenirse bu test kırmızıya döner ve
# garanti yeniden yazılmak zorunda kalır.
# NE SAYILIYOR (tanım açıkça yazılıyor — eşiği bir sayı olan kapının
# saydığı şey tanımsız kalamaz). `_tetikleyici_siteleri()` GÖÇ AĞACINDAKİ
# `CREATE TRIGGER` METİN GEÇİŞLERİNİ döndürür; aynı tetikleyici hem
# `upgrade()` hem `downgrade()` içinde yazıldığı için İKİ KEZ sayılır.
# Ölçüldü, develop 20253bfc:
#     göç dosyası                      : 2
#     CREATE TRIGGER geçişi (bu sayı)  : 8
#     ayrık tetikleyici adı            : 4
#     ayrık (ad, tablo) çifti          : 4
# Bu test bir EŞİK kullanmaz; yalnızca kapsanan tabloya değen geçiş olup
# olmadığına bakar, o yüzden tanım seçimi sonucu değiştirmez — ama sayı
# raporlandığında hangisi olduğu belli olsun diye burada sabitlenmiştir.
#
# BU TARAMA İKİNCİL BİR ERKEN UYARIDIR. Ölçüldü: veritabanına elle
# eklenen bir tetikleyiciyi bu tarama BULAMAZ (False). Asıl sınır, akış
# sırasında CANLI KATALOĞU gözleyen kapıdır (bkz. `CANLI KATALOG`
# bölümü); orası aynı tetikleyiciyi buldu.
_TETIKLEYICI_DESEN = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\s+"
    r"(\w+)[\s\S]{0,400}?\bON\s+[\"']?(\w+)",
    re.IGNORECASE,
)


def _tetikleyici_siteleri() -> list[tuple[str, str, str]]:
    """(dosya, tetikleyici adı, hedef tablo) — göç ağacının tamamı taranır."""
    bulunan: list[tuple[str, str, str]] = []
    for yol in sorted((BACKEND / "alembic" / "versions").rglob("*.py")):
        if "__pycache__" in yol.parts:
            continue
        metin = yol.read_text(encoding="utf-8", errors="replace")
        for ad, tablo in _TETIKLEYICI_DESEN.findall(metin):
            bulunan.append((yol.name, ad, tablo.lower()))
    return bulunan


def test_kapsanan_tabloda_TETIKLEYICI_YOK() -> None:
    """Sayım özdeşliğinin dayandığı varsayım YÜRÜTÜLEBİLİR olarak bildirilir."""
    siteler = _tetikleyici_siteleri()
    assert siteler, (
        "Tetikleyici taraması HİÇBİR ŞEY bulmadı; bu testin vakumda geçmesi "
        "demek olurdu. Desen ya da göç ağacı değişti."
    )
    carpisan = [s for s in siteler if s[2] in {t.lower() for t in KAPSANAN_TABLOLAR}]
    assert not carpisan, (
        "Kapsanan tabloya tetikleyici tanımlanmış: %r. Gözlemcinin 'tek "
        "demetli INSERT tam 1 satır yazar' özdeşliği bir tetikleyiciyle "
        "BOZULUR; outbox tarafında bu, eksik olayı gizler. Bu bir COST "
        "değil HOLE olurdu." % (carpisan,)
    )


# ---------------------------------------------------------------------------
# SÖZLEŞME CÜMLESİNİN ÖNKOŞULU — YÜRÜTÜLEBİLİR
# ---------------------------------------------------------------------------
# Bu kapının cümlesi: "kapsanan bir kaynağa yapılan HER YAZMA, aynı işlemde
# TAM BİR olay üretir." Kapı yazmayı `INSERT INTO <tablo>` üzerinden görüyor.
# Bu, cümlenin bir ÖNKOŞULU olduğu anlamına gelir: kapsanan tabloda satır
# INSERT'ten sonra DEĞİŞMİYOR olmalı.
#
# ÖLÇÜLDÜ (AST, backend/app/**/*.py, ağaç 70022a11):
#     tablo                  INSERT  UPDATE  DELETE
#     field_activities            1       0       0
#     field_harvests              1       0       0
#     field_activity_inputs       2       0       0
#     field_operations            4       0       0
#     field_tasks                 1       1       0   <- kapsam DIŞI
#
# Yani cümle bugün hasat için AYNEN geçerli: hasat satırı yazıldıktan sonra
# değişmiyor, dolayısıyla "her INSERT bir olay" ile "her yazma bir olay"
# ÇAKIŞIYOR. Bu bir varsayım değil, ölçüm.
#
# NEDEN ÇAPALANIYOR: hasat STOK GİRİŞİdir. Yarın biri `sold_quantity` ya da
# `quantity` güncelleyen bir yol eklerse, stok etkisi olan bir yazma INSERT
# olmadığı için bu kapıya GÖRÜNMEZ ve cümle sessizce yanlışlaşır. O gün bu
# test kırmızıya döner ve cümlenin yeniden yazılmasını ZORLAR.
#
# `field_tasks`in UPDATE yazıcısı bilerek listede: kapsam dışı olduğu için
# bugün bir şey ifade etmiyor, ama o dilimi alacak olan bunu ölçülmüş
# bulsun diye yazılı.
_FIIL_DESENLERI = {
    "UPDATE": re.compile(r"\bUPDATE\s+[\"'`\[]?(\w+)", re.IGNORECASE),
    "DELETE": re.compile(r"DELETE\s+FROM\s+[\"'`\[]?(\w+)", re.IGNORECASE),
}


def _fiil_eden_fonksiyonlar(tablo: str, fiil: str) -> list[tuple[str, str]]:
    """``tablo`` üzerinde ``fiil`` çalıştıran fonksiyonlar (dosya, ad)."""
    desen = _FIIL_DESENLERI[fiil]
    bulunan: list[tuple[str, str]] = []
    for yol in sorted((BACKEND / "app").rglob("*.py")):
        if "__pycache__" in yol.parts:
            continue
        try:
            agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        except SyntaxError:
            continue
        for dugum in ast.walk(agac):
            if not isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for ic in ast.walk(dugum):
                metin = None
                if isinstance(ic, ast.Constant) and isinstance(ic.value, str):
                    metin = ic.value
                elif isinstance(ic, ast.JoinedStr):
                    metin = "".join(
                        v.value for v in ic.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    )
                if metin and tablo in desen.findall(metin):
                    bulunan.append((yol.name, dugum.name))
                    break
    return sorted(set(bulunan))


def test_kapsanan_kaynak_INSERT_SONRASI_DEGISMIYOR() -> None:
    """Cümlenin önkoşulu: kapsanan tabloda UPDATE/DELETE yazıcısı YOK."""
    ihlal = {
        f"{tablo}/{fiil}": _fiil_eden_fonksiyonlar(tablo, fiil)
        for tablo in sorted(KAPSANAN_KAYNAKLAR)
        for fiil in ("UPDATE", "DELETE")
        if _fiil_eden_fonksiyonlar(tablo, fiil)
    }
    assert not ihlal, (
        "Kapsanan bir kaynak INSERT'ten SONRA değiştiriliyor: %r. Bu kapı "
        "yazmayı INSERT üzerinden görüyor; satırı sonradan değiştiren bir yol "
        "stok etkisi yaratıp kapıya GÖRÜNMEZ kalır. Ya o yol da olay üretmeli "
        "ya da sözleşme cümlesi yeniden yazılmalıdır." % (ihlal,)
    )


def test_fiil_taramasi_VAKUMDA_DEGIL() -> None:
    """Tarayıcı çalışıyor mu: ölçülmüş bir UPDATE yazıcısını bulabilmeli.

    `field_tasks` kapsam dışıdır ama UPDATE yazıcısı ÖLÇÜLDÜ. Tarayıcı onu
    bulamıyorsa yukarıdaki test hiçbir şey ölçmüyor demektir.
    """
    assert _fiil_eden_fonksiyonlar("field_tasks", "UPDATE"), (
        "UPDATE tarayıcısı `field_tasks` üzerinde ölçülmüş yazıcıyı bulamadı; "
        "bu tarayıcıyla yapılan 'UPDATE yok' ölçümü anlamsız olurdu."
    )


# ---------------------------------------------------------------------------
# KİMLİK PİNİ GERÇEKTEN ULAŞILABİLİR Mİ — ÖLÇÜLÜR, HATIRLANMAZ
# ---------------------------------------------------------------------------
# #81 raporu "kimlik pini `cid + 1` ile KIRMIZI oluyor" diyordu ve üç taraf da
# bunu kayda geçti. YENİDEN KOŞULDU (bu depoda, aynı mutasyon):
#
#     sqlite3.IntegrityError: FOREIGN KEY constraint failed
#
# `field_integration_events.company_id` -> `companies.id` yabancı anahtarı
# `cid + 1`i veritabanı seviyesinde reddediyor; pin HİÇ ÇALIŞMIYOR. Kırmızı
# gerçekti, ATFI uydurmaydı. Yani pin, İNMİŞ bir PR'da KANITSIZDI.
#
# Pine ULAŞAN mutasyon GEÇERLİ ama BAŞKA bir firma ister: olay geçerli bir
# firmaya ait görünür (FK sağlanır), gösterdiği kaynak satır BAŞKA firmanındır.
# Olay KENDİ işleminde yazılır; kaynak satırı olmayan bir işlem sayı kuralına
# girmez, böylece kırmızı TAM OLARAK pinden gelir.
#
# GENEL KURAL (bu turdan): "mutasyon kapıya ULAŞTI" ayrı bir ÖLÇÜMDÜR.
# Kırmızı; veritabanı kısıtından, uygulama doğrulamasından ya da çökmeden de
# gelebilir. Kanıt, kapının KENDİ assert metnini görmektir.
_KIMLIK_MUTASYONU = r'''
# Geçerli ama BAŞKA bir firma: FK sağlanır, kimlik uyuşmazlığı KALIR.
import datetime as _dtm
_mut_zaman = _dtm.datetime.now(_dtm.timezone.utc).isoformat(sep=' ')
_mut_yanit = c.post('@@YOL@@', headers=h, json=@@GOVDE@@)
assert _mut_yanit.status_code == 201, ('MUTASYON kaynak', _mut_yanit.text)

with SessionLocal() as _mut_db:
    _mut_r = _mut_db.execute(_sql('SELECT * FROM companies LIMIT 1'))
    _mut_sut = list(_mut_r.keys())
    _mut_satir = dict(zip(_mut_sut, _mut_r.fetchone()))
    _mut_satir.pop('id', None)
    for _mut_k in list(_mut_satir):
        if isinstance(_mut_satir[_mut_k], str) and _mut_k in (
                'name', 'code', 'tax_no', 'slug', 'email'):
            _mut_satir[_mut_k] = 'MUT-' + str(_mut_satir[_mut_k])[:20]
    _mut_db.execute(_sql('INSERT INTO companies (%s) VALUES (%s)' % (
        ','.join(_mut_satir), ','.join(':' + _k for _k in _mut_satir))), _mut_satir)
    _mut_db.commit()
    _mut_cid = _mut_db.execute(_sql('SELECT max(id) FROM companies')).scalar_one()
    _mut_sid = _mut_db.execute(
        _sql('SELECT max(id) FROM @@TABLO@@')).scalar_one()
    _mut_db.execute(_sql(
        'INSERT INTO field_integration_events (company_id,source_type,source_id,'
        'target,idempotency_key,status,attempts,created_at,updated_at) '
        "VALUES (:c,:source_type,:s,'stock',:ik,'PENDING',0,:n,:n)"),
        {'c': _mut_cid, 'source_type': '@@KAYNAK_TIPI@@', 's': _mut_sid,
         'ik': 'MUT-@@KAYNAK_TIPI@@', 'n': _mut_zaman})
    _mut_db.commit()
print('MUTASYON-KURULDU kaynak=@@KAYNAK_TIPI@@ cid=%s sid=%s' % (_mut_cid, _mut_sid))
'''


def _kimlik_mutasyonu(kaynak_tipi: str, tablo: str, yol: str, govde: str) -> str:
    return (
        _KIMLIK_MUTASYONU
        .replace("@@KAYNAK_TIPI@@", kaynak_tipi)
        .replace("@@TABLO@@", tablo)
        .replace("@@YOL@@", yol)
        .replace("@@GOVDE@@", govde)
    )


def _kos_kirmizi_bekle(kaynak: str, database_url: str, beklenen: str) -> None:
    """Smoke KIRMIZI olmalı VE kırmızı, kapının KENDİ metnini taşımalı."""
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", kaynak],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=600,
    )
    cikti = completed.stdout + "\n" + completed.stderr
    assert "MUTASYON-KURULDU" in completed.stdout, (
        "Mutasyon UYGULANMADI: kaynak satırı ve uyuşmayan olay yazılamadı, "
        "yani bu koşum pini sınamıyor.\n" + cikti
    )
    assert completed.returncode != 0, (
        "Kimlik pini uyuşmayan company_id'yi YAKALAMADI; smoke yeşil kaldı.\n"
        + cikti
    )
    assert beklenen in cikti, (
        "Kırmızı geldi ama kapının KENDİ metni yok — bu kırmızı başka bir "
        "yerden (kısıt, doğrulama, çökme) gelmiş olabilir. Beklenen metin: "
        f"{beklenen!r}\n" + cikti
    )


@pytest.mark.parametrize(
    ("kaynak_tipi", "tablo", "yol", "govde", "beklenen"),
    [
        (
            "field_activity", "field_activities", "/api/field-activities",
            "{**TABAN, 'labor_hours': '1'}", "faaliyetinkinden farklı",
        ),
        (
            "field_harvest", "field_harvests", "/api/field-harvests",
            "HASAT_TABAN", "hasadınkinden farklı",
        ),
    ],
)
def test_kimlik_pini_ULASILABILIR_ve_kirmizi(
    tmp_path: Path, kaynak_tipi: str, tablo: str, yol: str, govde: str,
    beklenen: str,
) -> None:
    """Uyuşmayan company_id, kapının KENDİ pin metniyle kırmızı olmalı."""
    kaynak = (
        _gozlemci_kaynagi() + _ORTAK_KURULUM
        + _kimlik_mutasyonu(kaynak_tipi, tablo, yol, govde)
        + _KURAL_GOVDESI
    )
    _kos_kirmizi_bekle(
        kaynak, f"sqlite:///{(tmp_path / ('pin-' + kaynak_tipi + '.db')).as_posix()}",
        beklenen,
    )


# ---------------------------------------------------------------------------
# KAPSANAN KAYNAK KÜMESİ **TÜRETİLİR** — LİSTE DEĞİL
# ---------------------------------------------------------------------------
# Önceki hâl `frozenset({KAYNAK_TABLO, HASAT_TABLO})` idi: elle yazılmış bir
# liste. Liste, yarın eklenen bir alan tablosu hakkında HİÇBİR ŞEY söylemez.
#
# Türetme zinciri, her halkası zaten ölçülü:
#   1. `_alan_tablolari_gocten()` — `field_*` tabloları GÖÇ ağacından gelir.
#   2. `KAPSAM_DISI_KAYNAKLAR` — kapsam dışı bırakılanlar, GEREKÇESİYLE.
#   3. `OUTBOX_KAYNAK_DEGIL` — outbox'ın kendisi kaynak değildir.
#   kapsanan = (1) - (2) - (3)
#
# SONRADAN EKLENEN BİR KAYNAK NE OLUR: göç ağacına yeni bir `field_*` tablosu
# girdiği anda (1) büyür. O tablo `KAPSAM_DISI_KAYNAKLAR`a gerekçesiyle
# yazılmadıysa KAPSANAN olur ve sonuç kuralı onu da ARAR; yazıcısı olay
# üretmiyorsa kırmızı olur. Kapsam dışı yazıldıysa
# `test_out_of_scope_field_tables_are_declared_not_forgotten` onu göçten
# türetilen kümeyle karşılaştırır. Yani yeni bir kaynak ya KAPSANIR ya da
# TESTİ KIRMIZIYA ÇEVİRİR; sessizce kapsam dışında kalamaz.
def _kapsanan_kaynaklar() -> frozenset[str]:
    """Kapsanan kaynak tabloları — göçten TÜRETİLİR, elle sayılmaz."""
    return frozenset(
        _alan_tablolari_gocten()
        - frozenset(KAPSAM_DISI_KAYNAKLAR)
        - OUTBOX_KAYNAK_DEGIL
    )


# ---------------------------------------------------------------------------
# TABLO -> OUTBOX KAYNAK TİPİ EŞLEMESİ DE **TÜRETİLİR**
# ---------------------------------------------------------------------------
# Sonuç kuralı artık KAYNAK BAŞINA denetleniyor. Bunun için bir outbox
# satırının HANGİ kaynağa ait olduğu bilinmeli: satırdaki `source_type`.
# Tablo ile `source_type` arasındaki eşleme ELLE YAZILMIYOR — yazıcının
# kendi kodundan çıkarılıyor: tabloya INSERT eden fonksiyonun içindeki
# ``_entegrasyon_olayi_yaz(db, cid, <KAYNAK_TİPİ>, ...)`` çağrısının üçüncü
# argümanı okunur ve modül sabiti çözülür.
#
# Eşleme kurulamayan bir kapsanan tablo İHLALDİR: o tablo için kural
# kurulamaz demektir ve aşağıdaki test kırmızı olur.
#: Belirsiz eşleme işareti — kaynak tipi olarak ASLA eşleşmez.
_BELIRSIZ = "BELIRSIZ:%r"
_YAZICI_KAYNAK_TIPI_ARG = 2   # _entegrasyon_olayi_yaz(db, cid, kaynak_tipi, ...)


def _modul_sabitleri(agac: ast.AST) -> dict[str, str]:
    """Modül düzeyindeki `AD = "deger"` string sabitleri."""
    sabitler: dict[str, str] = {}
    for dugum in getattr(agac, "body", []):
        if isinstance(dugum, ast.Assign) and isinstance(dugum.value, ast.Constant):
            if isinstance(dugum.value.value, str):
                for hedef in dugum.targets:
                    if isinstance(hedef, ast.Name):
                        sabitler[hedef.id] = dugum.value.value
    return sabitler


def _kaynak_tipi_haritasi() -> dict[str, str]:
    """{kapsanan tablo: outbox source_type} — yazıcı kodundan TÜRETİLİR."""
    harita: dict[str, str] = {}
    adaylar: dict[str, set[str]] = {}
    for tablo in sorted(_kapsanan_kaynaklar()):
        for yol, ad in _insert_eden_fonksiyonlar(tablo):
            agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
            sabitler = _modul_sabitleri(agac)
            for dugum in ast.walk(agac):
                if not isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if dugum.name != ad:
                    continue
                for ic in ast.walk(dugum):
                    if not isinstance(ic, ast.Call):
                        continue
                    if getattr(ic.func, "id", None) != YAZICI_ADI:
                        continue
                    if len(ic.args) <= _YAZICI_KAYNAK_TIPI_ARG:
                        continue
                    arg = ic.args[_YAZICI_KAYNAK_TIPI_ARG]
                    deger = None
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        deger = arg.value
                    elif isinstance(arg, ast.Name):
                        deger = sabitler.get(arg.id)
                    if deger:
                        adaylar.setdefault(tablo, set()).add(deger)
    # BELİRSİZLİK FAIL-CLOSED: bir yazıcı iki FARKLI kaynak tipiyle olay
    # üretiyorsa hangisinin o tabloya ait olduğu OKUNAMAZ. Sessizce birini
    # seçmek (önceki hâl: sonuncuyu yazıyordu) eşlemeyi bir mutasyonun
    # bozabileceği anlamına geliyordu — ölçüldü: karma vaka mutasyonu
    # `field_harvests`i `field_task`e eşledi ve kapsam kuralının mesajı
    # YANLIŞ kaynağı suçladı.
    for tablo, kume in adaylar.items():
        if len(kume) == 1:
            harita[tablo] = next(iter(kume))
        else:
            harita[tablo] = _BELIRSIZ % sorted(kume)
    return harita


def test_kapsanan_kaynaklar_TURETILIYOR_ve_bos_degil() -> None:
    """Küme göçten türemeli; boş türeme kuralı sessizce boşa düşürürdü."""
    turetilmis = _kapsanan_kaynaklar()
    assert turetilmis, (
        "Kapsanan kaynak kümesi BOŞ türedi; sonuç kuralı hiçbir tabloyu "
        "aramaz ve vakumda geçerdi. Göç taraması ya da kapsam dışı beyanı "
        f"bozuk: göçten gelen={sorted(_alan_tablolari_gocten())}"
    )
    assert turetilmis == KAPSANAN_KAYNAKLAR, (
        f"türetilen={sorted(turetilmis)} modülde kullanılan="
        f"{sorted(KAPSANAN_KAYNAKLAR)}"
    )
    # Türetme GERÇEKTEN göçe bağlı mı: outbox ve kapsam dışı olanlar DIŞARIDA.
    assert OUTBOX_TABLO not in turetilmis, "outbox kendini kaynak sanıyor"
    for disi in KAPSAM_DISI_KAYNAKLAR:
        assert disi not in turetilmis, f"{disi} kapsam dışı ama kapsanmış"


def test_her_kapsanan_kaynagin_KAYNAK_TIPI_TURETILEBILIYOR() -> None:
    """Kural kaynak başına; o yüzden her kaynağın tipi ÇÖZÜLEBİLMELİ."""
    harita = _kaynak_tipi_haritasi()
    eksik = sorted(_kapsanan_kaynaklar() - set(harita))
    assert not eksik, (
        f"Şu kapsanan kaynaklar için outbox `source_type` TÜRETİLEMEDİ: "
        f"{eksik}. Kaynak başına kural kurulamaz; toplam üzerinden denetim "
        "fail-open olurdu. Yazıcı çağrısı bulunamadı ya da kaynak tipi bir "
        "modül sabiti değil."
    )
    assert len(set(harita.values())) == len(harita), (
        f"iki kapsanan kaynak AYNI source_type'ı paylaşıyor: {harita!r}; "
        "kaynak başına ayrıştırma imkânsız olurdu"
    )


#: Kapsanan kaynaklar — GÖÇTEN TÜRETİLİR (yukarıdaki gerekçe).
KAPSANAN_KAYNAKLAR = _kapsanan_kaynaklar()


# ---------------------------------------------------------------------------
# KAPSAM DIŞI BEYANI **DONDURULMUŞ BİR KARARDIR** — gerekçe bir çapa değildir
# ---------------------------------------------------------------------------
# `KAPSANAN_KAYNAKLAR` göçten türetiliyor, ama türetme
# `KAPSAM_DISI_KAYNAKLAR`ı ÇIKARIYOR. Sözlük elle büyüyebildiği sürece
# türetme bir güvence vermez: yeni bir `field_*` tabloyu sözlüğe eklemek onu
# ölçümden çıkarır ve bugüne kadar hiçbir test bunu kırmıyordu — yalnız
# GEREKÇE bekleniyordu. Yazılı gerekçe bir karardır; ÇAPA değildir.
#
# Bu yüzden hem ÜYELİK hem BÜYÜKLÜK iki yönde donduruluyor: bir tablo
# eklemek de çıkarmak da bu testi kırar ve kararı GÖRÜNÜR kılar.
BEKLENEN_KAPSAM_DISI = frozenset({
    "field_activity_inputs",
    "field_operations",
    "field_tasks",
})
BEKLENEN_KAPSAM_DISI_SAYISI = 3


def test_kapsam_disi_beyani_DONDURULDU() -> None:
    """Kapsam dışı küme İKİ YÖNDE dondurulmuştur."""
    mevcut = frozenset(KAPSAM_DISI_KAYNAKLAR)
    eklenen = sorted(mevcut - BEKLENEN_KAPSAM_DISI)
    cikarilan = sorted(BEKLENEN_KAPSAM_DISI - mevcut)
    assert mevcut == BEKLENEN_KAPSAM_DISI, (
        "KAPSAM DIŞI BEYANI DEĞİŞTİ. Bu bir ÖLÇÜM değil KARAR: bir tabloyu "
        "buraya eklemek onu bu kapının ölçümünden ÇIKARIR ve gerçek bir "
        "shortfall sessizce kapsam dışına alınabilir. "
        f"eklenen={eklenen} çıkarılan={cikarilan}. Değişiklik bilinçliyse "
        "BEKLENEN_KAPSAM_DISI ve BEKLENEN_KAPSAM_DISI_SAYISI birlikte "
        "güncellenmeli ve gerekçesi PR'da savunulmalıdır."
    )
    assert len(KAPSAM_DISI_KAYNAKLAR) == BEKLENEN_KAPSAM_DISI_SAYISI, (
        f"kapsam dışı BÜYÜKLÜĞÜ değişti: bildirilen "
        f"{BEKLENEN_KAPSAM_DISI_SAYISI}, ölçülen {len(KAPSAM_DISI_KAYNAKLAR)}"
    )

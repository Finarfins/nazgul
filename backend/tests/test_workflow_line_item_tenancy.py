"""Sipariş ve irsaliye SATIRI kiracıya bağlı — ve bağ veritabanında.

Satır tabloları sözleşmesi, dilim 2: ``sales_order_items`` ve
``delivery_note_items``. Desen dilim 3'te (``stock_transfer_items``)
incelendi; burada iki çifte uygulanıyor.

Bu dilimde ölçüm ELLE YAZILMIŞ BEKLENTİ DEĞİL, SAYIM:
``tests/tenant_schema_snapshot.py`` tablonun ``inspect()`` üzerinden
görülebilen her şeyini topluyor ve yükseltmeden sonra "önceki + BEYAN EDİLEN
eklemeler" ile karşılaştırıyor. Fark önemli — sayım kimsenin hatırlamadığı
bir kısıtı da yakalar. Somut örnek: ``stock_transfers`` adlı bir CHECK
taşıyor, ``sales_orders`` ve ``delivery_notes`` HİÇ CHECK taşımıyor. Dilim
3'ten kopyalanmış "adlı CHECK'i doğrula" iddiası burada sessizce vakuma
düşerdi; sayımda "hiç yoktu, hiç yok" doğru şekilde korunmuş sayılıyor.

Kapılar (her iki çift, her iki diyalekt, upgrade VE downgrade sonrası):

1. Çapraz kiracı satır REDDEDİLİR — bileşik yabancı anahtar. Tek sütunluk
   bir FK yakalamazdı: ``sales_order_id`` geçerli olurdu, yalnız yanlış
   şirkete ait olurdu.
2. ``company_id``siz satır REDDEDİLİR — NOT NULL.
3. Mevcut satırlar migration'dan DOĞRU şirketle çıkar; boş tablo
   varsayılmıyor.
4. Yeniden kurulum yapıyı KORUR — indeksler SÜTUN DEMETİYLE, birincil
   anahtar, kimlik üretimi, tekillik, yabancı anahtar, CHECK kümesi ve sütun
   tipleri. Beyan edilmemiş bir EKLEME de hatadır.
5. Satırlar açık ``id`` olmadan yazılır ve üretilmiş kimlik alır.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _kos(script: str, database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def run_workflow_line_item_smoke(database_url: str) -> None:
    """Kapı 1, 2 ve 5: taze şemada kısıtlar gerçekten uyguluyor mu."""
    _kos(_SMOKE, database_url)


def run_workflow_line_item_migration(database_url: str) -> None:
    """Kapı 3, 4 ve 5: VERİSİ OLAN gerçek şemayı indirip yükselt."""
    _kos(_MIGRATION, database_url)


def test_workflow_line_item_tenancy_sqlite(tmp_path: Path) -> None:
    run_workflow_line_item_smoke(f"sqlite:///{(tmp_path / 'wf-line-tenancy.db').as_posix()}")


def test_workflow_line_item_migration_sqlite(tmp_path: Path) -> None:
    run_workflow_line_item_migration(f"sqlite:///{(tmp_path / 'wf-line-migration.db').as_posix()}")


_ORTAK = r'''
import importlib.util as _iu
import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text

_spec = _iu.spec_from_file_location("snap", "tests/tenant_schema_snapshot.py")
snap = _iu.module_from_spec(_spec); _spec.loader.exec_module(snap)

#: DEĞİŞİKLİK ÖNCESİ TABAN — BAĞIMSIZ ÇAPA.
#:
#: Türetilmiş fixture tek başına yetmez ve bunun somut kanıtı var: fixture,
#: test edilen migration'ın kendi downgrade'iyle üretiliyor. Migration
#: YUKARI çıkarken bir indeksi düşürürse, hasar "önceki" anlık görüntüye de
#: işlenir (``import app.main`` zinciri zaten koşturmuştur), önce ve sonra
#: birbirine uyar ve karşılaştırma HİÇBİR ŞEY göremez. Ölçüldü: indeks
#: düşüren bir mutasyon bu çapa olmadan YEŞİL geçiyordu.
#:
#: Bu değerler develop'tan (0056 var olmadan ÖNCE) ``inspect()`` ile
#: sayılarak alındı, yani test edilen koddan bağımsız.
TABAN_INDEKS = {
    "sales_orders": {(("company_id",), False)},
    "sales_order_items": {(("sales_order_id",), False)},
    "delivery_notes": {(("company_id",), False)},
    "delivery_note_items": {(("delivery_note_id",), False)},
}

#: (çocuk, ebeveyn, FK sütunu, UNIQUE adı, FK adı, indeks adı)
CIFTLER = (
    ("sales_order_items", "sales_orders", "sales_order_id",
     "uq_sales_orders_company_id", "fk_sales_order_items_order_same_company",
     "ix_sales_order_items_company_order"),
    ("delivery_note_items", "delivery_notes", "delivery_note_id",
     "uq_delivery_notes_company_id", "fk_delivery_note_items_note_same_company",
     "ix_delivery_note_items_company_note"),
)

motor = create_engine(os.environ["DATABASE_URL"])
pg = motor.dialect.name == "postgresql"


def reddedilmeli(fn, aciklama):
    """Her deneme KENDİ işleminde: PG'de hatalı statement işlemi iptal eder,
    SQLite yabancı anahtarları pragma olmadan zorlamaz ve pragma BAĞLANTI
    başınadır."""
    try:
        with motor.begin() as ayri:
            if not pg:
                ayri.execute(text("PRAGMA foreign_keys=ON"))
            fn(ayri)
    except Exception:
        return
    raise AssertionError(f"REDDEDİLMELİYDİ: {aciklama}")


def kimlik_uretimi_dogrula(ebeveyn, cocuk, fk_sutun, asama, *, cocuk_kiracili):
    """Kapı 5: açık ``id`` VERİLMEDEN yazılabiliyor mu.

    Yeniden kurulum dizi bağını koparırsa tablo "çalışır" görünmeye devam
    eder; kayıp yalnız açık id vermeyen ilk INSERT'te ortaya çıkar. Ham
    sürücü hatası aşama adıyla yeniden atılıyor — kazara kırılan bir kapı
    kasten kırılandan kötü okunur.
    """
    with motor.begin() as c:
        if not pg:
            c.execute(text("PRAGMA foreign_keys=ON"))
        try:
            ust = c.execute(text(
                f"INSERT INTO {ebeveyn}(company_id,customer_id,{'order_date' if ebeveyn=='sales_orders' else 'delivery_date'},"
                f"status{',warehouse_id' if ebeveyn=='delivery_notes' else ''}) "
                f"VALUES(9401,1,'2026-08-12','draft'{',1' if ebeveyn=='delivery_notes' else ''}) RETURNING id"
            )).scalar_one()
        except Exception as hata:
            raise AssertionError(
                f"{asama}: {ebeveyn}.id KENDILIGINDEN URETILMEDI — yeniden kurulum "
                f"dizi (sequence) bagini kopardi: {type(hata).__name__}: {hata}"
            ) from hata
        sutunlar = f"{fk_sutun},product_name,quantity,unit_price,line_total"
        degerler = ":t,'X',1,1,1"
        if cocuk_kiracili:
            sutunlar = "company_id," + sutunlar
            degerler = "9401," + degerler
        try:
            satir = c.execute(text(
                f"INSERT INTO {cocuk}({sutunlar}) VALUES({degerler}) RETURNING id"
            ), {"t": ust}).scalar_one()
        except Exception as hata:
            raise AssertionError(
                f"{asama}: {cocuk}.id KENDILIGINDEN URETILMEDI — yeniden kurulum "
                f"dizi (sequence) bagini kopardi: {type(hata).__name__}: {hata}"
            ) from hata
        c.execute(text(f"DELETE FROM {cocuk} WHERE id=:i"), {"i": satir})
        c.execute(text(f"DELETE FROM {ebeveyn} WHERE id=:i"), {"i": ust})
'''


_SMOKE = _ORTAK + r'''
import app.main  # noqa: F401  (migration zincirini koşturur)

denetci = inspect(motor)

for cocuk, ebeveyn, fk_sutun, uq_ad, fk_ad, index_ad in CIFTLER:
    cocuk_iz = snap.yapisal_iz(denetci, cocuk)
    ebeveyn_iz = snap.yapisal_iz(denetci, ebeveyn)

    assert "company_id" in cocuk_iz["sutunlar"], f"{cocuk} company_id taşımıyor"
    assert cocuk_iz["sutunlar"]["company_id"][1] is False, f"{cocuk}.company_id NULL kabul ediyor"
    assert ("company_id", "id") in ebeveyn_iz["tekillik"], (
        f"{ebeveyn}: bileşik FK'nın hedefi UNIQUE(company_id,id) yok: {ebeveyn_iz['tekillik']}"
    )
    # Bileşik OLMASI şart: tek sütunluk FK yanlış şirketi yakalamaz.
    assert (("company_id", fk_sutun), ebeveyn, ("company_id", "id")) in cocuk_iz["yabanci_anahtar"], (
        f"{cocuk}: bileşik yabancı anahtar yok: {sorted(cocuk_iz['yabanci_anahtar'], key=str)}"
    )

    with motor.begin() as c:
        c.execute(text(
            f"INSERT INTO {ebeveyn}(id,company_id,customer_id,"
            f"{'order_date' if ebeveyn=='sales_orders' else 'delivery_date'},status"
            f"{',warehouse_id' if ebeveyn=='delivery_notes' else ''}) "
            f"VALUES(94001,9401,1,'2026-08-12','draft'"
            f"{',1' if ebeveyn=='delivery_notes' else ''})"))

    def satir(c, cid, ust_id, _cocuk=cocuk, _fk=fk_sutun):
        c.execute(text(
            f"INSERT INTO {_cocuk}(company_id,{_fk},product_name,quantity,unit_price,line_total) "
            "VALUES(:c,:t,'X',1,1,1)"), {"c": cid, "t": ust_id})

    # KAPI 1: başka firmanın satırı bu belgeye bağlanamaz.
    reddedilmeli(lambda c, s=satir: s(c, 9402, 94001),
                 f"{cocuk}: baska firmanin satiri kabul edildi")
    # KAPI 2: company_id'siz satır reddedilir.
    reddedilmeli(lambda c, _cocuk=cocuk, _fk=fk_sutun: c.execute(text(
        f"INSERT INTO {_cocuk}({_fk},product_name,quantity,unit_price,line_total) "
        "VALUES(94001,'X',1,1,1)")),
        f"{cocuk}: company_id'siz satir kabul edildi")
    # Kendi belgesine yazmak ÇALIŞMALI — kapı geçerli işi engellemiyor.
    with motor.begin() as c:
        if not pg:
            c.execute(text("PRAGMA foreign_keys=ON"))
        satir(c, 9401, 94001)

    # KAPI 5
    kimlik_uretimi_dogrula(ebeveyn, cocuk, fk_sutun, "taze şema", cocuk_kiracili=True)

print("SMOKE OK")
'''


_MIGRATION = _ORTAK + r'''
# Değişiklik öncesi şekil ELLE YAZILMIYOR: gerçek şema migration zinciriyle
# kuruluyor, sonra BU migration'ın downgrade'i ile değişiklik öncesine
# indiriliyor. Böylece dizi, tip, nullability, indeks — hepsi gerçeğin
# kendisi. Fixture'ın kendisi de doğrulanıyor: downgrade sessizce bir şeyi
# düşürseydi sonraki "korundu" iddiaları vakuma düşerdi.
#
# SINIR: bu iz BAĞIMSIZ bir parmak izi değildir; yalnız inspect()in iki
# diyalektte gösterebildiğini sabitler. Kapsam dışı bırakılanlar için bkz.
# tests/tenant_schema_snapshot.py (collation, depolama parametreleri, indeks
# yöntemi, kısmi indeks yüklemi, tetikleyiciler).
import importlib.util

from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.main  # noqa: F401

_gspec = importlib.util.spec_from_file_location(
    "gecis", "alembic/versions/20260812_0056_workflow_line_items_tenant.py")
gecis = importlib.util.module_from_spec(_gspec); _gspec.loader.exec_module(gecis)


def gecisi_kostur(asama):
    with motor.begin() as baglanti:
        onceki = gecis.op
        gecis.op = Operations(MigrationContext.configure(baglanti))
        try:
            getattr(gecis, asama)()
        finally:
            gecis.op = onceki


# --- Gerçekten türetilmiş değişiklik öncesi şekil -------------------------
gecisi_kostur("downgrade")
denetci = inspect(motor)

ONCE = {}
for cocuk, ebeveyn, fk_sutun, uq_ad, fk_ad, index_ad in CIFTLER:
    ONCE[cocuk] = snap.yapisal_iz(denetci, cocuk)
    ONCE[ebeveyn] = snap.yapisal_iz(denetci, ebeveyn)
    # FIXTURE DOĞRULAMASI (fail-closed): aşağıdakiler gerçekten yoksa
    # sonraki "eklendi/korundu" iddiaları anlamsız olurdu.
    assert "company_id" not in ONCE[cocuk]["sutunlar"], (
        f"türetilen eski şekil {cocuk} için hâlâ company_id taşıyor")
    assert ("company_id", "id") not in ONCE[ebeveyn]["tekillik"], (
        f"türetilen eski şekil {ebeveyn} için hâlâ UNIQUE(company_id,id) taşıyor")
    # BAĞIMSIZ ÇAPA: türetilmiş şekil, bağımsız olarak ölçülmüş tabanı
    # taşımak zorunda. Taşımıyorsa fixture KİRLENMİŞTİR (migration yukarı
    # çıkarken bozmuş olabilir) ve sonraki "korundu" iddiaları anlamsızdır.
    for _t in (cocuk, ebeveyn):
        snap.capa_dogrula(denetci, _t, TABAN_INDEKS[_t])

    # Kimlik üretimi burada YAPISAL olarak iddia EDİLMİYOR: ölçüldü ki SQLite
    # inspect() bu sütunlarda autoincrement=None döndürüyor, üretim çalışsa
    # bile. Yapısal iddia SQLite'ta sessizce vakuma düşerdi. Gerçek kapı bir
    # satır aşağıda ve DAVRANIŞSAL: açık id vermeden INSERT.
    kimlik_uretimi_dogrula(ebeveyn, cocuk, fk_sutun, "değişiklik öncesi", cocuk_kiracili=False)

# --- Veri: açık id VERİLMİYOR --------------------------------------------
# Kapı 3 "MEVCUT satırlar doğru şirketle çıkıyor mu" sorusu; ölçümün
# başlangıcı belirli olsun diye kendi kümemizi kuruyoruz. Boş tablo
# VARSAYILMIYOR — her çifte iki firma ve üç satır yazılıyor.
BEKLENEN = {}
for cocuk, ebeveyn, fk_sutun, *_ in CIFTLER:
    with motor.begin() as c:
        c.execute(text(f"DELETE FROM {cocuk}"))
        c.execute(text(f"DELETE FROM {ebeveyn}"))
        ust = {}
        for etiket, fid in (("A", 9401), ("B", 9402)):
            ust[etiket] = c.execute(text(
                f"INSERT INTO {ebeveyn}(company_id,customer_id,"
                f"{'order_date' if ebeveyn=='sales_orders' else 'delivery_date'},status"
                f"{',warehouse_id' if ebeveyn=='delivery_notes' else ''}) "
                f"VALUES(:c,1,'2026-08-12','draft'"
                f"{',1' if ebeveyn=='delivery_notes' else ''}) RETURNING id"), {"c": fid}).scalar_one()
        for etiket, urun in (("A", "P1"), ("A", "P2"), ("B", "P3")):
            c.execute(text(
                f"INSERT INTO {cocuk}({fk_sutun},product_name,quantity,unit_price,line_total) "
                "VALUES(:t,:p,1,1,1)"), {"t": ust[etiket], "p": urun})
        BEKLENEN[cocuk] = [("P1", 9401, ust["A"]), ("P2", 9401, ust["A"]), ("P3", 9402, ust["B"])]
        BEKLENEN[ebeveyn] = ust

# --- Yükselt --------------------------------------------------------------
gecisi_kostur("upgrade")
denetci = inspect(motor)

for cocuk, ebeveyn, fk_sutun, uq_ad, fk_ad, index_ad in CIFTLER:
    # KAPI 3: mevcut satırlar DOĞRU şirketle sağ çıktı.
    with motor.connect() as c:
        satirlar = c.execute(text(
            f"SELECT product_name, company_id, {fk_sutun} FROM {cocuk} ORDER BY product_name")).all()
    assert satirlar == BEKLENEN[cocuk], (cocuk, satirlar, BEKLENEN[cocuk])

    # KAPI 4: yapı korundu + YALNIZ beyan edilen eklemeler.
    # PostgreSQL'de UNIQUE KISITI arkasında bir indeks yaratır ve
    # get_indexes() onu gösterir; SQLite'ta göstermez — kısıt yalnız
    # get_unique_constraints()'te görünür. Ölçülen fark, varsayılan değil:
    # beyan bu yüzden diyalekte bağlı.
    snap.korunmus_mu(
        ebeveyn, ONCE[ebeveyn], snap.yapisal_iz(denetci, ebeveyn), "yükseltme sonrası",
        eklenen_tekillik=frozenset({("company_id", "id")}),
        eklenen_indeks_sutunlari=frozenset({(("company_id", "id"), True)}) if pg else frozenset(),
        eklenen_indeks_adlari=frozenset({uq_ad}) if pg else frozenset(),
    )
    snap.korunmus_mu(
        cocuk, ONCE[cocuk], snap.yapisal_iz(denetci, cocuk), "yükseltme sonrası",
        eklenen_yabanci_anahtar=frozenset({(("company_id", fk_sutun), ebeveyn, ("company_id", "id"))}),
        eklenen_indeks_sutunlari=frozenset({(("company_id", fk_sutun), False)}),
        eklenen_indeks_adlari=frozenset({index_ad}),
        eklenen_sutunlar={"company_id": (ONCE[ebeveyn]["sutunlar"]["company_id"][0], False)},
    )
    # KAPI 5 ve davranış
    kimlik_uretimi_dogrula(ebeveyn, cocuk, fk_sutun, "yükseltme sonrası", cocuk_kiracili=True)
    reddedilmeli(lambda c, _c=cocuk, _f=fk_sutun, _u=BEKLENEN[ebeveyn]["A"]: c.execute(text(
        f"INSERT INTO {_c}(company_id,{_f},product_name,quantity,unit_price,line_total) "
        "VALUES(9402,:t,'X',1,1,1)"), {"t": _u}),
        f"{cocuk}: yukseltilmis semada capraz kiraci satir kabul edildi")
    reddedilmeli(lambda c, _c=cocuk, _f=fk_sutun, _u=BEKLENEN[ebeveyn]["A"]: c.execute(text(
        f"INSERT INTO {_c}({_f},product_name,quantity,unit_price,line_total) "
        "VALUES(:t,'X',1,1,1)"), {"t": _u}),
        f"{cocuk}: yukseltilmis semada company_id'siz satir kabul edildi")

# --- Geri alma: yapı BAŞLANGIÇTAKİNE dönmeli ------------------------------
gecisi_kostur("downgrade")
denetci = inspect(motor)
for cocuk, ebeveyn, fk_sutun, *_ in CIFTLER:
    snap.korunmus_mu(ebeveyn, ONCE[ebeveyn], snap.yapisal_iz(denetci, ebeveyn), "geri alma sonrası")
    snap.korunmus_mu(cocuk, ONCE[cocuk], snap.yapisal_iz(denetci, cocuk), "geri alma sonrası")
    kimlik_uretimi_dogrula(ebeveyn, cocuk, fk_sutun, "geri alma sonrası", cocuk_kiracili=False)

# Şemayı head'e geri getir.
gecisi_kostur("upgrade")

print("MIGRATION OK")
'''


# ---------------------------------------------------------------------------
# İSKELE KENDİ KENDİNİ SÖKMEK ZORUNDA
# ---------------------------------------------------------------------------


def _iskele_kullanimlari() -> list[str]:
    """``routers/workflow.py`` AST'sinde iskeleden geriye ne kalmış.

    Kaynak metninde ``grep`` yapmak yerine AST taranıyor: bir yorum satırında
    geçen ``tenant_line`` kelimesi kapıyı kırmızıya döndürmemeli, gerçek bir
    kullanım döndürmeli.
    """
    import ast

    kaynak = (BACKEND / "app" / "routers" / "workflow.py").read_text(encoding="utf-8")
    agac = ast.parse(kaynak)

    ebeveyn = {}
    for dugum in ast.walk(agac):
        for cocuk in ast.iter_child_nodes(dugum):
            ebeveyn[cocuk] = dugum

    YASAK = {"tenant_line", "_satir_kiraci", "_satir_kiraci_bagi"}
    bulunan: list[str] = []

    for dugum in ast.walk(agac):
        # İsim, çağrı, tanım, öznitelik.
        ad = None
        if isinstance(dugum, ast.Name):
            ad = dugum.id
        elif isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ad = dugum.name
        elif isinstance(dugum, ast.Attribute):
            ad = dugum.attr
        elif isinstance(dugum, ast.keyword):
            ad = dugum.arg
        elif isinstance(dugum, ast.Constant) and isinstance(dugum.value, str):
            # ``config["tenant_line"]`` ve ``config.get("tenant_line", True)``
            # ikisi de string sabit taşır.
            ad = dugum.value
        if ad in YASAK:
            bulunan.append(f"{ad} (satır {getattr(dugum, 'lineno', '?')})")

    # KOŞULLU satır-seviyesi company_id ataması: ``item_values["company_id"]``
    # bir ``if`` içinde kalmamalı. Bayrak gitse bile koşul kalabilir.
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Assign):
            continue
        for hedef in dugum.targets:
            if (
                isinstance(hedef, ast.Subscript)
                and isinstance(hedef.value, ast.Name)
                and hedef.value.id == "item_values"
                and isinstance(hedef.slice, ast.Constant)
                and hedef.slice.value == "company_id"
            ):
                ust = ebeveyn.get(dugum)
                while ust is not None and not isinstance(
                    ust, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)
                ):
                    if isinstance(ust, ast.If):
                        bulunan.append(
                            f"item_values['company_id'] hâlâ KOŞULLU "
                            f"(satır {dugum.lineno}, if satır {ust.lineno})"
                        )
                        break
                    ust = ebeveyn.get(ust)

    return sorted(bulunan)


def test_tenant_line_iskelesi_sokulmus_ve_geri_gelmiyor() -> None:
    """``tenant_line`` iskelesi sökülmüş olmalı ve sökülü kalmalı.

    İskele, satır tabloları sözleşmesi dilim dilim inerken vardı: taşımayan
    bir tabloya ``company_id=:cid`` yazmak "no such column" ile patladığı
    için yüklem KOŞULLU olmak zorundaydı. 1. dilim (20260812_0058) ile
    CONFIG'teki bütün satır tabloları company_id taşıyor, koşul her zaman
    doğru, iskele ölü ağırlık — ve sökülmüştür.

    ANAHTARLARIN gitmesi YETMEZ. Anahtarları silip yardımcıları
    ``config.get("tenant_line", True)``e çevirmek bu kapıyı yeşile
    döndürürdü ve `_satir_kiraci`, `_satir_kiraci_bagi`, koşullu dal ve
    koşullu company_id ataması OLDUĞU GİBİ kalırdı. Sözleşme "anahtarlar
    yaşayamaz" değil, "KULLANIMLAR yaşayamaz".

    SİLİNEN DAL — neden burada değil:
        Bu test eskiden iki dallıydı. İkinci dal, hâlâ kiracı taşımayan bir
        satır tablosu varken bayrağın YALAN SÖYLEYEMEYECEĞİNİ sınıyordu:
        değeri ``TENANT_TABLES`` üyeliğiyle birebir aynı olmalıydı. 1. dilim
        indikten sonra o dal ULAŞILAMAZ hâle geldi — koruduğu durum ancak
        iskele geri gelirse doğabilir, ve o durumda aşağıdaki tek iddia
        ZATEN ÖNCE ateşler. Yani dal hiçbir şey korumuyordu ama okuyana
        koruma gibi görünüyordu; bir kapıdaki ölü kod, hiç kod olmamasından
        daha kötüdür. Bu yüzden silindi. Geçmişi git'te duruyor.
    """
    import importlib.util
    import sys

    sys.path.insert(0, str(BACKEND))
    from app.routers.workflow import CONFIG

    kalanlar = sorted(k for k, cfg in CONFIG.items() if "tenant_line" in cfg)
    kullanimlar = _iskele_kullanimlari()
    assert not kalanlar and not kullanimlar, (
        "`tenant_line` iskelesi geri gelmiş. Satır tablolarının hepsi "
        "company_id taşıdığı için kiracı yüklemi KOŞULSUZ olmalı — bayrak, "
        "`_satir_kiraci`, `_satir_kiraci_bagi`, koşullu dal ve INSERT'teki "
        "koşullu company_id ataması yaşayamaz.\n"
        f"  bayrak taşıyan CONFIG kayıtları: {kalanlar}\n"
        f"  duran KULLANIMLAR: {kullanimlar}"
    )

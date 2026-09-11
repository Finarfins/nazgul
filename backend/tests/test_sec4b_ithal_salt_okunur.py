"""SEC-4b — İTHAL SALT OKUNUR OLUR: son yazma penceresi kapanıyor.

Konu: `app/main.py` (modül gövdesindeki `maintenance_status` çağrısı,
`_readiness_probe`), `app/maintenance.py` (`maintenance_status`,
`_recover_stale_row`, `_record_recovery`), `app/bootstrap_data.py` (`main`
docstring'indeki sözleşme).

--- KUSUR NEYDİ ------------------------------------------------------------

SEC-4 (#96) ithalden GÖÇÜ kaldırdı: `AUTO_MIGRATE=false` iken
`_semayi_hazirla()` artık koşmuyor. Ama modül gövdesinde HEMEN ARDINDAN
duran satır KOŞULSUZDU:

    maintenance_status(engine)          # varsayılan: recover_stale=True

`maintenance_status` PostgreSQL'de `platform_maintenance` 1 numaralı satırını
AKTİF bulduğunda ve advisory kilidi ALABİLDİĞİNDE (yani satır SAHİPSİZ, onu
yazan süreç ölmüş) satırı UPDATE eder ve `_record_recovery` `activity_logs`a
bir satır INSERT eder. Yani `AUTO_MIGRATE=false` ile açılan ÜRETİM işçisi,
`app/bootstrap_data.py`nin "app.main ithalinde artık hiçbir DML koşmaz"
sözleşmesine rağmen ithalde İKİ DML koşuyordu.

ÖLÇÜLDÜ, VARSAYILMADI (develop ef4639d, PG 16.4, bayat satır + AUTO_MIGRATE=
false ile `import app.main`): `before_cursor_execute` iki yazma gördü, satır
`active=true` -> `active=false` oldu ve kurtarma günlüğü 0 -> 1 arttı.

--- ÇÖZÜM -----------------------------------------------------------------

    maintenance_status(engine, recover_stale=settings.auto_migrate)

Kurtarma KAYBOLMUYOR, YERİ SABİTLENİYOR: `_readiness_probe` aynı çağrıyı
varsayılan `recover_stale=True` ile yapar, yani sahipsiz satır İLK HAZIRLIK
PROBUNDA temizlenir. Bu yer ithalden ÜSTÜNDÜR — prob süre bütçelidir, işçi
başına değil İSTEK başına koşar ve düştüğünde açılışı değil TEK BİR PROBU
düşürür.

--- BU DOSYANIN ÖLÇTÜĞÜ SINIR / ÖLÇEMEDİĞİ ---------------------------------

KUSURUN KENDİSİ BURADA GÖRÜNMEZ ve bu cümle dosyanın en önemli cümlesidir:
`maintenance_status` PostgreSQL DIŞINDA kurtarmaya HİÇ girmez
(`engine.dialect.name != "postgresql"` erken dönüşü), yani SQLite'ta bayat bir
satır zaten hiçbir zaman kurtarılmazdı. Davranış ölçümü — bayat satır, ithal,
DML sayımı, advisory kilit, hazırlık probunun kurtarması — PG İKİZİNDEDİR:
`backend/test_sec4_acilis_migrasyon_postgresql.py`, "SEC-4b" bölümü, mutantı
dahil.

Buradaki iş iki tanedir ve ikisi de PG ikizinin ölçemediği şeyler:

1. SQLite yarısı: erken dönüş DURUYOR mu (aşağıdaki iki davranış testi).
   Erken dönüş kalksaydı SQLite yolu `pg_try_advisory_lock` ile patlar ve
   `maintenance_status` dış `except` bloğunda "aktif" diye FAIL-CLOSED olurdu
   — yani her SQLite açılışı bakım modunda görünürdü.
2. YAPISAL kapı: kurtarmanın ithalde KOŞULA, hazırlık probunda VARSAYILANA
   bağlı olduğu AST ile çivileniyor. Davranış testi tek başına bunu
   söyleyemez: iki çağrı da varsayılana dönseydi PG ikizi kırmızı yanar ama
   `recover_stale`i probdan SİLMEK (ya da orada `False` yapmak) hiçbir SQLite
   testini kımıldatmazdı.
"""
from __future__ import annotations

import ast
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
MAIN_PY = BACKEND / "app" / "main.py"
MAINTENANCE_PY = BACKEND / "app" / "maintenance.py"
BOOTSTRAP_PY = BACKEND / "app" / "bootstrap_data.py"

_ITHAL = "import app.main\nprint('ITHAL_TAMAM')\n"

#: Bayat = AKTİF ama sahibi ölmüş. `heartbeat_at` 2 saat geride: kurtarma
#: yolunun sahipsizlik ölçütü advisory kilit olsa da, satırın gerçekten bayat
#: göründüğü bir kurulum ölçüyoruz.
_BAYATLAT = (
    "UPDATE platform_maintenance SET active=1, operation_id='SEC4B-BAYAT',"
    " operation_kind='backup.restore', owner='sec4b-olcum',"
    " started_at=datetime('now','-2 hours'),"
    " heartbeat_at=datetime('now','-2 hours') WHERE id=1"
)


def _ortam(veritabani: Path, *, auto_migrate: str, veri_dizini: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    env["AUTO_MIGRATE"] = auto_migrate
    env["SUNGUR_DATA_DIR"] = str(veri_dizini)
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    env["FIELD_STOCK_OUTBOX_ENABLED"] = "false"
    env["WHATSAPP_WORKER_ENABLED"] = "false"
    # REQUIRE_PG alt sürece TAŞINMAMALI: alt süreç pytest değildir ve
    # conftest'in kapısı orada koşmaz.
    env.pop("REQUIRE_PG", None)
    return env


def _kos(kod: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", kod],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
        # Windows'ta `text=True` yerel kod sayfasina duser ve Turkce hata
        # metnini COZEMEZ; olcum, olcmek istedigi seyden once cozucude patlardi.
        encoding="utf-8", errors="replace",
    )


def _bakim_satiri(veritabani: Path) -> dict[str, object]:
    baglanti = sqlite3.connect(veritabani)
    try:
        baglanti.row_factory = sqlite3.Row
        satir = baglanti.execute(
            "SELECT active, operation_id, operation_kind, owner, heartbeat_at"
            " FROM platform_maintenance WHERE id=1"
        ).fetchone()
        return dict(satir)
    finally:
        baglanti.close()


def _kurtarma_gunlugu(veritabani: Path) -> int:
    baglanti = sqlite3.connect(veritabani)
    try:
        # H32 sonrası kurtarma olayı firmasız denetim satırıdır; eski yer
        # (kiracı defteri) de sayılır ki iki yoldan biri yazsa ölçüm görsün.
        return baglanti.execute(
            "SELECT (SELECT count(*) FROM activity_logs"
            "        WHERE action_type='backup.maintenance_recovered')"
            "     + (SELECT count(*) FROM security_audit_logs"
            "        WHERE action='platform.mt_recover')"
        ).fetchone()[0]
    finally:
        baglanti.close()


@pytest.fixture()
def bayat_veritabani(tmp_path: Path) -> Path:
    """Şeması kurulmuş, 1 numaralı bakım satırı BAYAT bir SQLite veritabanı.

    Şema AYRI bir süreçte ve `AUTO_MIGRATE=true` ile kurulur (üretimdeki
    deploy adımının aynısı); bayatlatma ondan SONRA gelir ki ölçülen ithal,
    şemayı kuran ithal OLMASIN.
    """
    veritabani = tmp_path / "sec4b.db"
    hazirlik = _kos(_ITHAL, _ortam(veritabani, auto_migrate="true", veri_dizini=tmp_path))
    assert hazirlik.returncode == 0, hazirlik.stdout + "\n" + hazirlik.stderr

    baglanti = sqlite3.connect(veritabani)
    try:
        assert baglanti.execute(_BAYATLAT).rowcount == 1
        baglanti.commit()
    finally:
        baglanti.close()
    return veritabani


# ------------------------------------------------------------- davranış ---

def test_SQLITE_AUTO_MIGRATE_KAPALI_ITHAL_BAYAT_SATIRA_DOKUNMUYOR(
    bayat_veritabani: Path, tmp_path: Path
) -> None:
    """Üretim şekli: `AUTO_MIGRATE=false` ithal bakım satırını KIMILDATMAZ."""
    once = _bakim_satiri(bayat_veritabani)
    assert once["active"] == 1 and once["operation_id"] == "SEC4B-BAYAT"

    sonuc = _kos(
        _ITHAL, _ortam(bayat_veritabani, auto_migrate="false", veri_dizini=tmp_path)
    )
    assert sonuc.returncode == 0, sonuc.stdout + "\n" + sonuc.stderr
    assert "ITHAL_TAMAM" in sonuc.stdout

    assert _bakim_satiri(bayat_veritabani) == once, "ithal bakım satırını değiştirdi"
    assert _kurtarma_gunlugu(bayat_veritabani) == 0


def test_SQLITE_AUTO_MIGRATE_ACIK_OLSA_BILE_DIALECT_KAPISI_KURTARMIYOR(
    bayat_veritabani: Path, tmp_path: Path
) -> None:
    """Erken dönüş DURUYOR: SQLite'ta kurtarma `AUTO_MIGRATE=true` ile de yok.

    Bu test SEC-4b'nin yeni kwarg'ını DEĞİL, ondan önce gelen
    `engine.dialect.name != "postgresql"` kapısını çiviliyor. İkisi ayrı ayrı
    ölçülmeli: yeni kwarg `True` olsa bile SQLite yolu yazmamalı, çünkü
    kurtarmanın tamamı (advisory kilit dahil) PostgreSQL'e özgüdür.
    """
    once = _bakim_satiri(bayat_veritabani)

    sonuc = _kos(
        _ITHAL, _ortam(bayat_veritabani, auto_migrate="true", veri_dizini=tmp_path)
    )
    assert sonuc.returncode == 0, sonuc.stdout + "\n" + sonuc.stderr

    assert _bakim_satiri(bayat_veritabani) == once, (
        "SQLite'ta kurtarma koştu — `maintenance_status` erken dönüşü kalkmış"
    )
    assert _kurtarma_gunlugu(bayat_veritabani) == 0


# -------------------------------------------------------------- yapısal ---

def _modul_govdesindeki_bakim_cagrilari(agac: ast.Module) -> list[ast.Call]:
    """Modül gövdesinde (fonksiyon/sınıf DIŞINDA) duran `maintenance_status`lar."""
    return [
        dugum.value
        for dugum in agac.body
        if isinstance(dugum, ast.Expr)
        and isinstance(dugum.value, ast.Call)
        and isinstance(dugum.value.func, ast.Name)
        and dugum.value.func.id == "maintenance_status"
    ]


def test_ITHALDEKI_BAKIM_CAGRISI_KURTARMAYI_AUTO_MIGRATE_e_BAGLIYOR() -> None:
    """Modül gövdesinde TAM BİR çağrı ve `recover_stale=settings.auto_migrate`.

    "Tam bir" kısmı gereksiz değil: ikinci, koşulsuz bir çağrı davranış
    ölçümünün gördüğü tek yolu es geçer ve pencereyi sessizce geri açardı.
    """
    agac = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    cagrilar = _modul_govdesindeki_bakim_cagrilari(agac)
    assert len(cagrilar) == 1, f"modül gövdesinde {len(cagrilar)} bakım çağrısı"

    (kwarg,) = [k for k in cagrilar[0].keywords if k.arg == "recover_stale"]
    assert ast.dump(kwarg.value) == ast.dump(
        ast.parse("settings.auto_migrate", mode="eval").body
    ), ast.dump(kwarg.value)


def test_HAZIRLIK_PROBU_KURTARMAYI_KAPATMIYOR() -> None:
    """`_readiness_probe` varsayılanı KORUR — kurtarmanın yeni yeri orasıdır.

    Kwarg'ı oraya `False` diye yazmak (ya da çağrıyı silmek) hiçbir SQLite
    davranış testini kımıldatmazdı; sahipsiz bakım satırı bir daha ASLA
    temizlenmez ve örnek kalıcı olarak trafik dışı kalırdı.
    """
    agac = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    (islev,) = [
        d for d in agac.body
        if isinstance(d, ast.FunctionDef) and d.name == "_readiness_probe"
    ]
    cagrilar = [
        d for d in ast.walk(islev)
        if isinstance(d, ast.Call)
        and isinstance(d.func, ast.Name)
        and d.func.id == "maintenance_status"
    ]
    assert len(cagrilar) == 1, f"hazırlık probunda {len(cagrilar)} bakım çağrısı"
    assert [k.arg for k in cagrilar[0].keywords] == [], (
        "hazırlık probu `recover_stale`i AÇIKÇA veriyor; varsayılanı "
        "(kurtarma AÇIK) korumalı"
    )


def test_MAINTENANCE_STATUS_VARSAYILANI_KURTARMA_ACIK_KALDI() -> None:
    """Varsayılan `True` kalmalı: prob onu AÇIKÇA vermeyerek kullanıyor."""
    import inspect

    from app.maintenance import maintenance_status

    parametre = inspect.signature(maintenance_status).parameters["recover_stale"]
    assert parametre.kind is inspect.Parameter.KEYWORD_ONLY
    assert parametre.default is True


def test_KURTARMA_YOLU_DIALECT_KAPISININ_ARDINDA() -> None:
    """`_recover_stale_row` yalnız PostgreSQL'de çağrılır.

    Kapı METİNLE değil AST ile ölçülüyor: kaynakta geçen bir dize (docstring
    dahil) aramayı yanlış yeşile boyardı.
    """
    agac = ast.parse(MAINTENANCE_PY.read_text(encoding="utf-8"))
    (islev,) = [
        d for d in agac.body
        if isinstance(d, ast.FunctionDef) and d.name == "maintenance_status"
    ]
    kapilar = [
        d for d in ast.walk(islev)
        if isinstance(d, ast.Compare)
        and ast.dump(d.left) == ast.dump(
            ast.parse("engine.dialect.name", mode="eval").body
        )
    ]
    assert len(kapilar) == 1, "dialect kapısı yok ya da birden fazla"
    (sag,) = kapilar[0].comparators
    assert isinstance(sag, ast.Constant) and sag.value == "postgresql", ast.dump(sag)

    cagrilar = [
        d for d in ast.walk(islev)
        if isinstance(d, ast.Call)
        and isinstance(d.func, ast.Name)
        and d.func.id == "_recover_stale_row"
    ]
    assert len(cagrilar) == 1, f"{len(cagrilar)} adet `_recover_stale_row` çağrısı"


def test_BOOTSTRAP_SOZLESMESI_ARTIK_LAFZEN_DOGRU() -> None:
    """İthalde hiçbir DML koşmadığını söyleyen cümle yerinde DURUYOR.

    Cümle SEC-4'te yazıldı ama o gün PostgreSQL'de YANLIŞTI; SEC-4b onu
    doğrulayan değişikliktir. Cümle silinirse bu dilimin neyi garanti ettiği
    de kaybolur.
    """
    kaynak = BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert "ithalinde artık hiçbir DML" in kaynak, (
        "bootstrap_data.main docstring'indeki ithal-DML sözleşmesi kaybolmuş"
    )

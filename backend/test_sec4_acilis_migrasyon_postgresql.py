"""SEC-4 PG İKİZİ — AÇILIŞ YARIŞI GERÇEK POSTGRESQL'DE ÖLÇÜLÜR.

`tests/test_sec4_acilis_migrasyon_kapisi.py` kapının SQLite üzerindeki
davranışını çiviliyor. Kusurun KENDİSİ ise SQLite'ta GÖRÜNMEZ ve bu ikizin var
olma sebebi tam olarak budur: `database_bootstrap_lock` PostgreSQL DIŞINDA bir
no-op'tur (`app/runtime_migrations.py`), yani aynı anda açılan iki sürecin
serileştirilmesi de, kilidi 120 sn içinde alamayan sürecin çökmesi de yalnız
burada olur.

İKİ ÖLÇÜM, İKİ AYRI ŞEY:

1. `test_ESZAMANLI_ITHAL_...`  — ÜRETİM ŞEKLİNDEKİ iddia. Boş bir şemada iki
   süreç `app.main`i AYNI ANDA ithal eder (AUTO_MIGRATE=true). İKİSİ DE 0 ile
   çıkmalı ve sonuçta `alembic_version` TEK satır, bootstrap satırları TEK
   kopya olmalı. Kilit çalışmasaydı ikinci süreç ya "relation already exists"
   ile ya da TimeoutError ile düşerdi.

2. `test_KILIT_ALTINDA_YALNIZ_BIRI_GOCU_SURUYOR` — "tam olarak biri" iddiası.
   (1) numaralı ölçüm bunu SÖYLEYEMEZ: her iki süreç de 0 ile çıktığında,
   göçü hangisinin sürdüğü dışarıdan görünmez. Burada her süreç kilidi ALDIKTAN
   SONRA `current_revision`ı okuyup bildiriyor; `None` gören SÜRDÜ, gören
   görmeyen bulmuş demektir. Tam olarak bir "SURDUM" beklenir.

Her ikisi de KENDİ ŞEMASINDA koşar (`search_path`), çünkü paylaşılan bir
veritabanında "boş şema" varsayımı komşu dosyaların bıraktığı satırlarla
çürür. Advisory kilit ise cluster genelindedir ve şema başına DEĞİLDİR — bu
ikizin ölçtüğü şey zaten o kilidin ta kendisidir.
"""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql


def _taban_url() -> str:
    url = (
        os.getenv("SEC4_TEST_DATABASE_URL")
        or os.getenv("APP_TEST_DATABASE_URL")
        or ""
    )
    if not url:
        pytest.skip("SEC4_TEST_DATABASE_URL (ya da APP_TEST_DATABASE_URL) gerekli")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("SEC4_TEST_DATABASE_URL PostgreSQL'i göstermeli")
    return url


@pytest.fixture()
def temiz_sema() -> tuple[str, str]:
    """Boş bir şema aç, testten sonra CASCADE ile düşür."""
    taban = _taban_url()
    yonetici = create_engine(taban, pool_pre_ping=True)
    ad = f"sec4_{uuid4().hex}"
    tirnakli = yonetici.dialect.identifier_preparer.quote(ad)
    with yonetici.begin() as baglanti:
        baglanti.execute(text(f"CREATE SCHEMA {tirnakli}"))
    url = (
        make_url(taban)
        .update_query_dict({"options": f"-csearch_path={ad}"})
        .render_as_string(hide_password=False)
    )
    try:
        yield ad, url
    finally:
        with yonetici.begin() as baglanti:
            baglanti.execute(text(f"DROP SCHEMA IF EXISTS {tirnakli} CASCADE"))
        yonetici.dispose()


def _ortam(url: str, tmp_path: Path, *, auto_migrate: str = "true") -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["AUTO_MIGRATE"] = auto_migrate
    env["SUNGUR_DATA_DIR"] = str(tmp_path)
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
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=900,
        encoding="utf-8", errors="replace",
    )


def _eszamanli(kod: str, env: dict[str, str], adet: int = 2):
    with ThreadPoolExecutor(max_workers=adet) as havuz:
        return [f.result() for f in [havuz.submit(_kos, kod, env) for _ in range(adet)]]


_ITHAL = "import app.main\nprint('ITHAL_TAMAM')\n"

# Kilidi ALDIKTAN SONRA okur: `None` gören süreç göçü SÜREN süreçtir.
_KILIT_ALTINDA = (
    "from app.db import engine\n"
    "from app.runtime_migrations import (\n"
    "    current_revision, database_bootstrap_lock, run_database_migrations,\n"
    ")\n"
    "with database_bootstrap_lock(engine):\n"
    "    once = current_revision(engine)\n"
    "    run_database_migrations(engine, acquire_lock=False)\n"
    "print('SURDUM' if once is None else 'BULDUM')\n"
)


def test_ESZAMANLI_ITHAL_IKI_SURECI_de_ACIYOR_ve_TEK_KOPYA_birakiyor(
    temiz_sema: tuple[str, str], tmp_path: Path
) -> None:
    ad, url = temiz_sema
    sonuclar = _eszamanli(_ITHAL, _ortam(url, tmp_path))

    for i, sonuc in enumerate(sonuclar):
        assert sonuc.returncode == 0, (
            f"eşzamanlı ithal #{i} düştü — advisory kilit yarışı:\n"
            + sonuc.stdout + sonuc.stderr
        )
        assert "ITHAL_TAMAM" in sonuc.stdout

    motor = create_engine(url, pool_pre_ping=True)
    try:
        with motor.connect() as baglanti:
            surumler = baglanti.execute(
                text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            # Bootstrap tohumu DML'dir; kilit olmadan iki süreç aynı satırları
            # birlikte yazabilirdi. Firma ve depo TEK kopya olmalı.
            firmalar = baglanti.execute(
                text("SELECT count(*) FROM companies")
            ).scalar_one()
            depolar = baglanti.execute(
                text("SELECT count(*) FROM warehouses")
            ).scalar_one()
            hesaplar = baglanti.execute(
                text("SELECT count(*) FROM finance_accounts")
            ).scalar_one()
    finally:
        motor.dispose()

    assert surumler == 1, f"alembic_version {surumler} satır ({ad})"
    assert firmalar == 1, f"bootstrap firması {firmalar} kopya ({ad})"
    assert depolar == 1, f"bootstrap deposu {depolar} kopya ({ad})"
    assert hesaplar == 3, f"bootstrap finans hesabı {hesaplar} adet ({ad})"


def test_KILIT_ALTINDA_YALNIZ_BIRI_GOCU_SURUYOR(
    temiz_sema: tuple[str, str], tmp_path: Path
) -> None:
    _ad, url = temiz_sema
    sonuclar = _eszamanli(_KILIT_ALTINDA, _ortam(url, tmp_path))

    for i, sonuc in enumerate(sonuclar):
        assert sonuc.returncode == 0, (
            f"kilit altındaki süreç #{i} düştü:\n" + sonuc.stdout + sonuc.stderr
        )

    surenler = [s for s in sonuclar if "SURDUM" in s.stdout]
    bulanlar = [s for s in sonuclar if "BULDUM" in s.stdout]
    assert len(surenler) == 1 and len(bulanlar) == 1, (
        "TAM OLARAK BİRİ sürmeli: "
        + repr([s.stdout.strip() for s in sonuclar])
    )


# =========================================================== SEC-4b ==========
#
# SEC-4 (#96) ithalden GÖÇÜ kaldırdı. Bu bölüm ithalde KALAN son yazmayı
# ölçüyor ve o yazma yalnız PostgreSQL'de vardı — SQLite ikizinde GÖRÜNMEZ,
# çünkü `maintenance_status` PostgreSQL DIŞINDA kurtarmadan ÖNCE döner.
#
# KUSUR: `app/main.py` ithalde `maintenance_status(engine)` çağırıyordu ve o
# çağrının varsayılanı `recover_stale=True`. `platform_maintenance` 1 numaralı
# satırı SAHİPSİZ (aktif ama kimse advisory kilidi tutmuyor) olduğunda
# `_recover_stale_row` satırı UPDATE ediyor, `_record_recovery` de
# `activity_logs`a bir satır INSERT ediyordu — AUTO_MIGRATE=false ile açılan
# ÜRETİM işçisinde, `app/bootstrap_data.py`nin "ithalde artık HİÇBİR DML yok"
# sözleşmesine rağmen.
#
# ÇÖZÜM: `maintenance_status(engine, recover_stale=settings.auto_migrate)`.
# Kurtarma KAYBOLMAZ; `_readiness_probe` içindeki çağrı varsayılanı koruyor,
# yani sahipsiz satır İLK HAZIRLIK PROBUNDA temizleniyor. Aşağıdaki (a)
# ölçümü bu iki yarıyı TEK testte zincirliyor: ithal DOKUNMAZ, prob TEMİZLER.

_BAYATLAT = """UPDATE platform_maintenance
   SET active=true, operation_id='SEC4B-BAYAT',
       operation_kind='backup.restore', owner='sec4b-olcum',
       started_at=now() - interval '2 hours',
       heartbeat_at=now() - interval '2 hours'
 WHERE id=1"""

#: İthal SIRASINDA motordan geçen HER ifadeyi yakalar. "Yazmadı" bir niyet
#: değil bir ÖLÇÜM olsun diye: `pg_stat_statements` bu kurulumda yüklü değil ve
#: sunucu günlüğü testten okunamaz, ama `before_cursor_execute` sürücünün
#: gönderdiği metnin TAMAMINI görür — advisory kilit çağrıları DAHİL. Kilidi
#: ölçmek için `pg_locks`u örneklemek YETMEZDİ: kilit ithal içinde alınıp yine
#: ithal içinde bırakılıyor, yani ithalden sonra bakan bir sorgu onu GÖREMEZ.
_ITHAL_IZLEYICI = (
    "import json\n"
    "from sqlalchemy import event\n"
    "import app.db as _db\n"
    "_ifadeler = []\n"
    "@event.listens_for(_db.engine, 'before_cursor_execute')\n"
    "def _yakala(conn, cur, stmt, params, ctx, many):\n"
    "    _ifadeler.append(stmt)\n"
    "import app.main\n"
    "print('IFADELER=' + json.dumps(_ifadeler))\n"
)

#: (a)'nın ikinci yarısı: aynı ayarlarla ithal + hazırlık probu.
_ITHAL_SONRA_HAZIRLIK = (
    "from fastapi.testclient import TestClient\n"
    "from app.main import app\n"
    "with TestClient(app) as istemci:\n"
    "    yanit = istemci.get('/api/ready')\n"
    "print('HAZIRLIK=' + str(yanit.status_code))\n"
)


def _sema_kur(url: str, tmp_path: Path) -> None:
    """Şemayı AYRI bir süreçte sür (üretimdeki deploy adımının aynısı)."""
    hazirlik = _kos(_ITHAL, _ortam(url, tmp_path, auto_migrate="true"))
    assert hazirlik.returncode == 0, hazirlik.stdout + "\n" + hazirlik.stderr


def _bayatlat(url: str) -> None:
    motor = create_engine(url, pool_pre_ping=True)
    try:
        with motor.begin() as baglanti:
            assert baglanti.execute(text(_BAYATLAT)).rowcount == 1
    finally:
        motor.dispose()


def _bakim_ve_gunluk(url: str) -> tuple[dict, int]:
    """(bakım satırı, kurtarma günlüğü satır sayısı)."""
    motor = create_engine(url, pool_pre_ping=True)
    try:
        with motor.connect() as baglanti:
            satir = dict(
                baglanti.execute(
                    text(
                        "SELECT active, operation_id, operation_kind, owner"
                        " FROM platform_maintenance WHERE id=1"
                    )
                ).mappings().one()
            )
            gunluk = baglanti.execute(
                # H32: kurtarma olayı firmasız denetim satırıdır.
                text(
                    "SELECT count(*) FROM security_audit_logs"
                    " WHERE action='platform.mt_recover' AND company_id IS NULL"
                )
            ).scalar_one()
    finally:
        motor.dispose()
    return satir, int(gunluk)


def _ifadeleri_coz(cikti: str) -> list[str]:
    import json

    satirlar = [s for s in cikti.splitlines() if s.startswith("IFADELER=")]
    assert len(satirlar) == 1, (
        "alt süreç ifade listesini bildirmedi — ölçüm aracı çalışmadı:\n" + cikti[-3000:]
    )
    return json.loads(satirlar[0][len("IFADELER=") :])


def _yazan_ifadeler(cikti: str) -> list[str]:
    """Bildirilen ifadelerden veriyi ya da şemayı DEĞİŞTİRENLER."""
    yazan = []
    for ifade in _ifadeleri_coz(cikti):
        bas = ifade.lstrip().upper()
        if bas.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")):
            yazan.append(ifade.strip()[:160])
    return yazan


def _kilit_ifadeleri(cikti: str) -> list[str]:
    return [
        ifade.strip()[:160]
        for ifade in _ifadeleri_coz(cikti)
        if "advisory_lock" in ifade.lower()
    ]


def test_AUTO_MIGRATE_KAPALI_ITHAL_BAYAT_SATIRI_KURTARMIYOR_prob_KURTARIYOR(
    temiz_sema: tuple[str, str], tmp_path: Path
) -> None:
    """SEC-4b (a): ithal SALT OKUNUR, kurtarma HAZIRLIK PROBUNA taşındı.

    TEK testte iki yarı, ve bilerek: "ithal yazmıyor" tek başına bir
    REGRESYON olurdu — sahipsiz bakım satırı hiç temizlenmiyorsa örnek
    sonsuza dek bakım modunda kalır ve trafik ALMAZDI. İddia, yazmanın
    KAYBOLMASI değil YER DEĞİŞTİRMESİ.
    """
    _ad, url = temiz_sema
    _sema_kur(url, tmp_path)
    _bayatlat(url)

    once, gunluk_once = _bakim_ve_gunluk(url)
    assert once["active"] is True and once["operation_id"] == "SEC4B-BAYAT"
    assert gunluk_once == 0

    ithal = _kos(_ITHAL_IZLEYICI, _ortam(url, tmp_path, auto_migrate="false"))
    assert ithal.returncode == 0, ithal.stdout + "\n" + ithal.stderr

    yazan = _yazan_ifadeler(ithal.stdout)
    assert yazan == [], (
        "AUTO_MIGRATE=false iken ithal DML koştu — ithal SALT OKUNUR olmalı:\n"
        + "\n".join(yazan)
    )
    kilitler = _kilit_ifadeleri(ithal.stdout)
    assert kilitler == [], (
        "AUTO_MIGRATE=false iken ithal advisory kilit ALDI:\n" + "\n".join(kilitler)
    )

    sonra, gunluk_sonra = _bakim_ve_gunluk(url)
    assert sonra == once, f"ithal bakım satırını değiştirdi: {once} -> {sonra}"
    assert gunluk_sonra == 0, "ithal kurtarma günlüğü yazdı"

    # --- ikinci yarı: kurtarma HAZIRLIK PROBUNDA olur -----------------------
    prob = _kos(_ITHAL_SONRA_HAZIRLIK, _ortam(url, tmp_path, auto_migrate="false"))
    assert prob.returncode == 0, prob.stdout + "\n" + prob.stderr
    assert "HAZIRLIK=200" in prob.stdout, prob.stdout + "\n" + prob.stderr

    temiz, gunluk_prob = _bakim_ve_gunluk(url)
    assert temiz["active"] is False and temiz["operation_id"] is None, (
        "hazırlık probu sahipsiz bakım satırını TEMİZLEMEDİ: " + repr(temiz)
    )
    assert gunluk_prob == 1, f"kurtarma günlüğü {gunluk_prob} satır (1 bekleniyordu)"


def test_AUTO_MIGRATE_ACIK_ITHAL_BAYAT_SATIRI_HALA_KURTARIYOR(
    temiz_sema: tuple[str, str], tmp_path: Path
) -> None:
    """SEC-4b (b): AUTO_MIGRATE=true davranışı DEĞİŞMEDİ — regresyon kapısı.

    Kwarg'ı `False` sabitine bağlamak (a)'yı yine yeşil bırakırdı ve
    geliştirme/CI yolundaki kurtarmayı SESSİZCE kaldırırdı.
    """
    _ad, url = temiz_sema
    _sema_kur(url, tmp_path)
    _bayatlat(url)

    ithal = _kos(_ITHAL_IZLEYICI, _ortam(url, tmp_path, auto_migrate="true"))
    assert ithal.returncode == 0, ithal.stdout + "\n" + ithal.stderr

    yazan = _yazan_ifadeler(ithal.stdout)
    assert any("platform_maintenance" in y for y in yazan), (
        "AUTO_MIGRATE=true iken ithal bakım satırını KURTARMADI: " + repr(yazan)
    )

    satir, gunluk = _bakim_ve_gunluk(url)
    assert satir["active"] is False and satir["operation_id"] is None, repr(satir)
    assert gunluk == 1, gunluk


# ----------------------------------------------------------- (d) MUTANT ---
#
# Mutasyon KAYNAK AĞACA DEĞİL, tek kullanımlık bir KOPYAYA uygulanır: kanonik
# koşucu dosyaları PARALEL koşturuyor ve yerinde mutasyon komşu dosyaları
# nedensizce kırmızıya çevirirdi (gerekçenin tamamı
# `tests/test_sec4_acilis_migrasyon_kapisi.py` içindedir).


def _sanal_agac(hedef: Path) -> Path:
    """`app/` + `alembic/` + `alembic.ini`den ibaret tek kullanımlık kopya."""
    import shutil

    hedef.mkdir(parents=True, exist_ok=True)
    yoksay = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(BACKEND / "app", hedef / "app", ignore=yoksay)
    shutil.copytree(BACKEND / "alembic", hedef / "alembic", ignore=yoksay)
    shutil.copy2(BACKEND / "alembic.ini", hedef / "alembic.ini")
    return hedef


def test_MUTANT_ITHALDE_KOSULSUZ_KURTARMA_kirmizi_yanar(
    temiz_sema: tuple[str, str], tmp_path: Path
) -> None:
    """`recover_stale=settings.auto_migrate` -> koşulsuz `maintenance_status(engine)`.

    Bu, kusurun TA KENDİSİDİR ve mutant hayatta kalırsa yukarıdaki (a) ölçümü
    hiçbir şey görmüyor demektir. Mutant, (a)'nın İLK yarısında ölür: bayat
    satır ithalde kurtarılır, DML listesi boş kalmaz.
    """
    _ad, url = temiz_sema
    _sema_kur(url, tmp_path)
    _bayatlat(url)

    agac = _sanal_agac(tmp_path / "agac")
    hedef = agac / "app" / "main.py"
    metin = hedef.read_text(encoding="utf-8")
    capa = "maintenance_status(engine, recover_stale=settings.auto_migrate)"
    assert capa in metin, "mutant çapası bulunamadı"
    hedef.write_text(metin.replace(capa, "maintenance_status(engine)", 1), encoding="utf-8")

    env = _ortam(url, tmp_path, auto_migrate="false")
    env["PYTHONPATH"] = str(agac)
    sonuc = subprocess.run(
        [sys.executable, "-c", _ITHAL_IZLEYICI], cwd=agac, env=env,
        capture_output=True, text=True, timeout=900,
        encoding="utf-8", errors="replace",
    )
    assert sonuc.returncode == 0, sonuc.stdout + "\n" + sonuc.stderr

    yazan = _yazan_ifadeler(sonuc.stdout)
    assert any("platform_maintenance" in y for y in yazan), (
        "MUTANT HAYATTA KALDI: kurtarma ithale koşulsuz döndü ama ithal yine "
        "yazmadı — `test_AUTO_MIGRATE_KAPALI_ITHAL_BAYAT_SATIRI_KURTARMIYOR_"
        "prob_KURTARIYOR` bu mutantı GÖRMÜYOR demektir. Bulunan yazmalar: "
        + repr(yazan)
    )

    satir, gunluk = _bakim_ve_gunluk(url)
    assert satir["active"] is False and gunluk == 1, (satir, gunluk)

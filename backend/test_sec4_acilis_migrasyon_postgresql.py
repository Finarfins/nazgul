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


def _ortam(url: str, tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["AUTO_MIGRATE"] = "true"
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

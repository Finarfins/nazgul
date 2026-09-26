"""H75 düzeltme 1 — göç 0092 atladığı kaynak sütunu SESSİZ geçemez.

Göç ``20260925_0092`` kaynak kolonu olmayan bir şemada o kolonun ``_katli``
sütununu atlar ve yine de 0092'yi damgalar. Normal şemada bu dal hiç
çalışmaz; kaymış şema yalnız test fikstürlerinde var. Atlama sessizse
kaymış bir üretim şeması arama kaybını hiçbir iz bırakmadan taşır; bu
yüzden her atlanan ``(tablo, kolon)`` göç günlüğüne uyarı olarak düşer.

Kapı: boş SQLite'ı 0091'e yükselt, ``customers.email``i yeniden adlandır,
0092'ye yükselt. Uyarı metni caplog'da GÖRÜNÜR ve on iki ``_katli``
sütunundan YALNIZ ``customers.email_katli`` eksiktir. MUTASYON: uyarı
satırını silmek KIRMIZI; atlamayı tablo düzeyine genişletmek KIRMIZI.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h75-atlama-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h75-atlama.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI

import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.arama_katli import KATLI_SUTUNLAR  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


def _yukselt(baglanti, hedef: str) -> None:
    # Ini dosyası BİLEREK verilmez: env.py'deki `fileConfig` kök günlükçünün
    # işleyicilerini değiştirir ve caplog'un işleyicisini söker.
    ayar = Config()
    ayar.set_main_option("script_location", str(BACKEND / "alembic"))
    ayar.attributes["connection"] = baglanti
    command.upgrade(ayar, hedef)


def _katli_sutunlar(motor) -> set[tuple[str, str]]:
    denetci = sa.inspect(motor)
    return {
        (tablo, sutun["name"])
        for tablo in denetci.get_table_names()
        for sutun in denetci.get_columns(tablo)
        if sutun["name"].endswith("_katli")
    }


def test_eksik_kaynak_sutunu_uyari_verir_ve_YALNIZ_o_katli_atlanir(caplog, tmp_path) -> None:
    motor = sa.create_engine(f"sqlite:///{(tmp_path / 'kayma.db').as_posix()}")
    try:
        with motor.begin() as baglanti:
            _yukselt(baglanti, "20260920_0091")
        with motor.begin() as baglanti:
            baglanti.exec_driver_sql("ALTER TABLE customers RENAME COLUMN email TO eposta")

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            with motor.begin() as baglanti:
                _yukselt(baglanti, "20260925_0092")

        uyarilar = [
            k.getMessage() for k in caplog.records
            if k.name == "alembic.runtime.migration" and k.levelno == logging.WARNING
        ]
        assert uyarilar == ["0092: customers.email kaynak sütunu yok — email_katli atlandı"]

        beklenen = {
            (tablo, f"{kolon}_katli")
            for tablo, kolonlar in KATLI_SUTUNLAR.items()
            for kolon in kolonlar
        }
        assert len(beklenen) == 12
        assert _katli_sutunlar(motor) == beklenen - {("customers", "email_katli")}

        with motor.connect() as baglanti:
            damga = baglanti.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        assert damga == "20260925_0092"
    finally:
        motor.dispose()

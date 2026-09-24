"""H74 — `harvest_scheduling._calendar_candidates`in harf duyarsız eşlemesi.

`UPPER(season_code)=UPPER(:season_code)` iki yanlıdır ve İKİ yanın da ölçen
testi YOKTU: hem takvim hem kural yazıcısı (`harvest_scheduling_schemas`,
`clean_season_code`) kodu büyük harfe çevirerek yazıyor, yani uçtan açılan
her satır zaten büyük harfli ve UPPER olmadan da eşitlik tutuyordu. UPPER
yalnız ESKİ (doğrulayıcı öncesi ya da doğrudan yazılmış) küçük harfli
satırlarda anlam taşır; bu dosya o satırları doğrudan yazar.

MUTASYON TABLOSU (ELLE KOŞULDU, PR gövdesinde):
  * sütun yanındaki `UPPER(season_code)`ı düz `season_code` yapmak
        -> `test_ESKI_kucuk_harfli_TAKVIM_buyuk_harfli_kurala_eslesir` KIRMIZI
  * parametre yanındaki `UPPER(:season_code)`ı düz `:season_code` yapmak
        -> `test_ESKI_kucuk_harfli_KURAL_buyuk_harfli_takvime_eslesir` KIRMIZI

Doğrudan fonksiyon çağrısı, HTTP değil: sözleşme SQL'in kendisidir ve
`GET /preview` aynı fonksiyona müşteri + ürün + kategori + kural kurulumuyla
ulaşır; o kurulum bu iki eşitliği ölçmeye bir şey katmaz.
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h74-hasat-sezon-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h74.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

ISLEM_TARIHI = date(2026, 9, 1)


@pytest.fixture(scope="module")
def firma():
    """Şemayı uygulamanın kendisi kurar; firma ve takvimler doğrudan yazılır."""
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.main import app

    with TestClient(app):
        pass
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        cid = db.execute(
            text(
                "INSERT INTO companies(name,is_active,created_at)"
                " VALUES('H74 Hasat',:aktif,:t) RETURNING id"
            ),
            {"aktif": True, "t": an},
        ).scalar_one()
        for kod in ("guz2026", "BAHAR2026"):
            db.execute(
                text(
                    "INSERT INTO harvest_calendars(company_id,season_code,name,"
                    "season_year,due_date,active,created_at,updated_at)"
                    " VALUES(:cid,:kod,:kod,2026,:vade,:aktif,:t,:t)"
                ),
                {"cid": cid, "kod": kod, "vade": date(2026, 10, 15), "aktif": True, "t": an},
            )
        db.commit()
    return cid


def _adaylar(cid: int, kod: str) -> list[str]:
    from app.db import SessionLocal
    from app.harvest_scheduling import _calendar_candidates

    with SessionLocal() as db:
        return [str(x["season_code"]) for x in _calendar_candidates(db, cid, kod, ISLEM_TARIHI)]


def test_ESKI_kucuk_harfli_TAKVIM_buyuk_harfli_kurala_eslesir(firma) -> None:
    """Depoda `guz2026`, kural (doğrulayıcıdan geçmiş) `GUZ2026` arıyor."""
    assert _adaylar(firma, "GUZ2026") == ["guz2026"]


def test_ESKI_kucuk_harfli_KURAL_buyuk_harfli_takvime_eslesir(firma) -> None:
    """Depoda `BAHAR2026`, eski bir kural `bahar2026` taşıyor."""
    assert _adaylar(firma, "bahar2026") == ["BAHAR2026"]


def test_farkli_kod_ESLESMEZ(firma) -> None:
    """Harf duyarsızlık bir joker değil: başka bir sezon boş döner."""
    assert _adaylar(firma, "KIS2026") == []

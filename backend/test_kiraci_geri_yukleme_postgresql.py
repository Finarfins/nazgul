"""PostgreSQL ikizi: 5.1c kiracı geri yüklemesinin GERÇEK kısıtlarla yuvarlak yolculuğu.

SQLite ikizi ``tests/test_kiraci_geri_yukleme.py`` akışın tamamını ölçüyor; bu
dosya AYNI senaryo betiğini gerçek PostgreSQL 16 üzerinde koşturur ve yalnız
GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN dört şeyi ölçer:

1. **Yabancı anahtarlar GERÇEKTEN uygulanır.** SQLite FK'ları varsayılan
   olarak DENETLEMEZ; yanlış haritalanmış bir kimlik orada sessizce geçer,
   burada ``ForeignKeyViolation`` ile işlemin tamamını düşürür. Yuvarlak
   yolculuğun 200 dönmesi bu yüzden burada AYRI bir kanıttır.
2. **Tek işlem gerçekten tek.** Enjekte edilen hata sonrası hiçbir tablo
   satır kazanmadı — PostgreSQL'de işlem yarıda düşünce geri alma gerçek.
3. **``yerine`` kipi serial SIRALARI ilerletir.** Açık kimlikle yazılan
   tabloya sonraki NORMAL ekleme aynı kimliği üretmemeli; SQLite'ta bu sorun
   HİÇ yoktur (rowid), yani yalnız burada ölçülebilir.
4. **``timestamptz`` farkındalığı.** Zip'teki UTC damgalar farkında sütuna
   farkında, farkındasız sütuna naive UTC yazılır; dönüşüm hatası burada patlar.

Şema TAZE olmalı (CI her ikiz dosyasından önce ``DROP SCHEMA``): uygulama
bootstrap'ı senaryo betiğinin içindeki ``TestClient(app)`` ile koşar.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

if os.environ.get("APP_TEST_DATABASE_URL", "").startswith("postgresql"):
    os.environ["DATABASE_URL"] = os.environ["APP_TEST_DATABASE_URL"]


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL ikizi APP_TEST_DATABASE_URL ister")
    return url


def _sqlite_ikizi():
    spec = importlib.util.spec_from_file_location(
        "_gy_sqlite", BACKEND / "tests" / "test_kiraci_geri_yukleme.py"
    )
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture(scope="module")
def hazir(tmp_path_factory) -> dict:
    url = _url()
    ikiz = _sqlite_ikizi()
    dizin = tmp_path_factory.mktemp("geri-yukleme-pg")
    tamam = ikiz._kos(ikiz._HAZIRLIK, dizin / "unused.db", dizin, url=url)
    ikiz._basarili(tamam)
    sonuc = json.loads((dizin / "sonuc.json").read_text(encoding="utf-8"))
    sonuc["_url"] = url
    return sonuc


def test_pg_yuvarlak_yolculuk_gercek_fklarla(hazir) -> None:
    """FK'lar gerçek: haritalama yanlışsa 200 değil 500 olurdu."""
    ikiz = _sqlite_ikizi()
    manifest = hazir["manifest_row_counts"]
    c = hazir["sonraki_c"]
    from app.kiraci_geri_yukleme import KURESEL_TEKIL_ATLANIR

    for tablo, beklenen in manifest.items():
        if tablo in KURESEL_TEKIL_ATLANIR or tablo == "user_company_memberships":
            continue
        assert c[tablo] == beklenen, (tablo, c[tablo], beklenen)
    assert hazir["fk_ihlal"] == []
    assert hazir["rapor"]["row_total"] > 100
    assert sorted(t for t, n in manifest.items() if n == 0) == sorted(ikiz.BOS_KABUL)


def test_pg_enjekte_hata_hicbir_sey_birakmaz(hazir) -> None:
    assert hazir["enjekte_status"] == 500
    assert hazir["enjekte_sonrasi"] == hazir["imha_sonrasi_a"]


def test_pg_kurcalama_ve_kuru_kosu_yazmaz(hazir) -> None:
    assert hazir["kurcalama"]["sayi"]["code"] == "RESTORE_ROW_COUNT_MISMATCH"
    assert hazir["kurcalama"]["sema"]["code"] == "RESTORE_SCHEMA_MISMATCH"
    assert hazir["kuru_sonrasi"] == hazir["imha_sonrasi_a"]


def test_pg_yerine_kimlik_korur_ve_sirayi_ilerletir(hazir) -> None:
    """MUTASYON: ``_sirayi_ilerlet`` çağrısını silmek bunu KIRMIZI yapar —
    sonraki normal ekleme ``duplicate key`` ile düşerdi."""
    y = hazir["yerine_bos"]
    assert y["status"] == 200, y
    assert hazir["y_urun"] == hazir["zipteki_urun_kimlikleri"]
    engine = create_engine(hazir["_url"])
    try:
        with engine.begin() as conn:
            yeni = conn.execute(
                text(
                    "INSERT INTO user_company_memberships(user_id, company_id, is_default, created_at) "
                    "VALUES (:u, :c, false, now()) RETURNING id"
                ),
                {"u": hazir["admin_id"], "c": hazir["b_id"]},
            ).scalar_one()
            assert int(yeni) > max(hazir["y_urun"]), (yeni, hazir["y_urun"])
            conn.execute(text("DELETE FROM user_company_memberships WHERE id = :i"), {"i": yeni})
    finally:
        engine.dispose()

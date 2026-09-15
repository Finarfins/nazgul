"""PostgreSQL ikizi: 5.1c kiracı geri yüklemesinin GERÇEK kısıtlarla yuvarlak yolculuğu.

SQLite ikizi ``tests/test_kiraci_geri_yukleme.py`` akışın tamamını ölçüyor; bu
dosya AYNI senaryo betiğini gerçek PostgreSQL 16 üzerinde koşturur ve yalnız
GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN üç şeyi ölçer:

1. **Yabancı anahtarlar GERÇEKTEN uygulanır.** SQLite FK'ları varsayılan
   olarak DENETLEMEZ; yanlış haritalanmış bir kimlik orada sessizce geçer,
   burada ``ForeignKeyViolation`` ile işlemin tamamını düşürür. Yuvarlak
   yolculuğun 200 dönmesi bu yüzden burada AYRI bir kanıttır.
2. **Tek işlem gerçekten tek.** Enjekte edilen hata sonrası hiçbir tablo
   satır kazanmadı — PostgreSQL'de işlem yarıda düşünce geri alma gerçek.
3. **``timestamptz`` farkındalığı.** Zip'teki UTC damgalar farkında sütuna
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


def test_pg_sevk_satirlari_uc_referansla_yeniden_eslendi(hazir) -> None:
    """E4b-1 (göç 20260915_0087): SQLite ikiziyle AYNI iddia, GERÇEK bileşik
    FK altında — yanlış eşlenen `despatch_id` burada 500 olurdu."""
    _sqlite_ikizi().test_sevk_satirlari_uc_referansla_yeniden_eslendi(hazir)


def test_pg_enjekte_hata_hicbir_sey_birakmaz(hazir) -> None:
    assert hazir["enjekte_status"] == 500
    assert hazir["enjekte_sonrasi"] == hazir["imha_sonrasi_a"]


def test_pg_kurcalama_ve_kuru_kosu_yazmaz(hazir) -> None:
    assert hazir["kurcalama"]["sayi"]["code"] == "RESTORE_ROW_COUNT_MISMATCH"
    assert hazir["kurcalama"]["sema"]["code"] == "RESTORE_SCHEMA_MISMATCH"
    assert hazir["kuru_sonrasi"] == hazir["imha_sonrasi_a"]


def test_pg_aktif_kiracinin_ustune_yazmaz(hazir) -> None:
    """Gerçek FK'larla da: aktif B'nin kimliğini taşıyan zip B'ye dokunmaz."""
    u = hazir["ustune"]
    assert u["status"] == 200, u
    assert u["company_id"] not in (hazir["a_id"], hazir["b_id"]), u
    # `__companies__` TÜM firmaların sayısıdır: yeni firma onu +1 yapar.
    once = {k: v for k, v in hazir["ustune_oncesi_b"].items() if k != "__companies__"}
    sonra = {k: v for k, v in hazir["ustune_sonrasi_b"].items() if k != "__companies__"}
    assert sonra == once
    assert hazir["ustune_sonrasi_b"]["__companies__"] == hazir["ustune_oncesi_b"]["__companies__"] + 1
    assert hazir["b_aktif_sonra"] is True and hazir["b_ad"] == hazir["b_ad_once"]
    assert hazir["eski_kip"]["status"] == 422


def test_pg_kip_matrisi(hazir) -> None:
    """H23: alan YOK -> 200 `yeni`; alan VAR ve boş/boşluk/geçersiz -> 422; 500 yok."""
    m = hazir["kip_matrisi"]
    assert m["eksik"]["status"] == 200 and m["eksik"]["mode"] == "yeni", m["eksik"]
    for etiket in ("bos", "bosluk", "yerine", "cop"):
        assert m[etiket]["status"] == 422, (etiket, m[etiket])
        assert m[etiket]["detail"] == "mode yalnız 'yeni' olabilir", (etiket, m[etiket])
    assert all(v["status"] != 500 for v in m.values()), m
    assert all(v["sonrasi"] == hazir["imha_sonrasi_a"] for v in m.values()), m

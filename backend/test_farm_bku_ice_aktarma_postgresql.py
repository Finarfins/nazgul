"""PostgreSQL ikizi: BKÜ kataloğu içe aktarma ve satır kökeni (göç 20260902_0065).

BU İKİZ DEKORATİF DEĞİL — ve bu dosyada ikizin gerekçesi, kardeşi
`test_farm_bku_katalogu_postgresql.py`ninkinden DAHA GÜÇLÜDÜR, çünkü içe
aktarma yolunun ANA KURALI iki diyalektte FARKLI davranan bir mekanizmaya
dayanıyor:

1. **BİR BOZUK SATIR DOSYAYI DÜŞÜRMEZ — ama bunu ayakta tutan şey SAVEPOINT.**
   PostgreSQL'de başarısız bir deyimden sonra işlem `ABORTED` duruma düşer ve
   aynı işlemdeki SONRAKİ HER sorgu, kendisi kusursuz olsa bile hata verir.
   SQLite'ta böyle bir hâl yoktur: kısıt ihlali yalnız o deyimi düşürür ve
   koşu hiçbir şey olmamış gibi devam eder.

   Yani `db.begin_nested()` KALDIRILSA, SQLite koşusu YEŞİL KALIR ve üretim
   diyalektinde tek bozuk satır dosyanın kalanını da sessizce yutardı — tam
   olarak bu dilimin engellemeye çalıştığı zarar, yalnız üretimde görünür
   hâlde. Bu ikiz o boşluğu kapatıyor.

2. **Yarış hâlindeki tekillik ihlali.** Çakışma önce `SELECT` ile aranıyor;
   `SELECT` ile `INSERT` arasında başkası aynı satırı yazarsa son sözü
   `uq_ppp_company_product_crop` söylüyor ve `IntegrityError` yakalanıyor.
   O yakalamanın işlemi kullanılabilir bırakması yine savepoint'e bağlı ve
   yine yalnız PostgreSQL'de ölçülebilir.

3. **`crop` BOŞ DİZE.** İçe aktarma "bütün bitkiler" satırını `crop=''` ile
   yazıyor ve çakışma araması da `crop=:crop` ile boş dizeyi arıyor. 0063'ün
   ikizinde kurulan gerekçenin aynısı: boş dize bir gün NULL'a düşerse
   `UNIQUE` bütün "bütün bitkiler" satırlarını BİRBİRİNDEN FARKLI sayar,
   çakışma bulunamaz ve içe aktarma aynı ürüne sınırsız satır yazar.

4. **`LOWER()` ürün eşlemesinde.** Ürün kodu ve adı SQL'de `LOWER()` ile
   karşılaştırılıyor ve Türkçe İ/ı eşlemesi diyalekte bağlıdır: SQLite'ın
   `LOWER`ı ASCII dışına dokunmaz, PostgreSQL'inki yerel ayara göre davranır.
   Bitki karşılaştırması bu yüzden Python'da tutuluyor (0063, `_bitki_esit`);
   ürün eşlemesi SQL'de kaldı çünkü belirsizlik burada TAHMİNE değil REDDE
   çıkıyor — iki diyalekt farklı sayıda eşleşme bulsa bile sonuç ya doğru
   üründür ya da adıyla söylenen bir REDDİR, sessiz bir yanlış bağlama değil.
   İddia yine de üretim diyalektinde koşulmalı.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_bku_ice_aktarma", BACKEND / "tests" / "test_farm_bku_ice_aktarma.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_ice_aktarma_smoke = _contract.run_ice_aktarma_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The BKU import test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_bku_ice_aktarma_postgresql() -> None:
    run_ice_aktarma_smoke(_pg_url())

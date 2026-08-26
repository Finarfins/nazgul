"""Hayvancılık V1 — FAZ 1 sözleşmesi: veri modeli, kiracı izolasyonu, RBAC.

Konu: mobil-erp#17.

Bu fazda uç YOK; testler ŞEMANIN ve YETKİ HARİTASININ iddialarını çiviliyor.
Beşi de araştırmadan çıkan kararlar ve hepsi sessizce bozulabilir:

1. **Aynı küpe iki AKTİF hayvanda olamaz — ama geçmişte olabilir.** Küpe
   numarası yeniden kullanılıyor (hayvan satılır/ölür, numara döner). Koşulsuz
   tekillik, satılmış bir hayvanın numarasının bir daha girilememesine yol
   açardı; koşulsuz serbestlik ise iki canlı hayvanı aynı kimlikle karıştırırdı.

2. **Küpe biçimi veritabanında DAYATILMIYOR.** 28.06.2026 yönetmelik
   değişikliğiyle küpe standardı geçişte (karekod/turkuaz; il kodlu küpeler
   31.12.2026'ya kadar) ve kontrol basamağı algoritması açık kaynaklarda yok.
   Katı bir CHECK, aylar içinde GEÇERLİ bir küpeyi reddederdi — ve yanlış bir
   veritabanı kısıtını geri almak migration gerektirir.

3. **Çapraz kiracı bağ veritabanı seviyesinde imkânsız.** B firmasının
   hayvanını A firmasının sürüsüne bağlamak bileşik yabancı anahtarla
   engelleniyor; uygulama katmanına bırakılsaydı tek bir unutulmuş filtre
   yeterdi.

4. **Süt kaydı hayvana YA DA gruba ait, ikisine birden değil.** İkisi de dolu
   olsaydı aynı süt iki kez sayılır ve hayvan başına verim yanlış çıkardı.

5. **Aşı kaydı AYRI izin.** Veteriner aşı girebilmeli ama hayvan alım/satımı
   yapamamalı; tek bir `herd` izni bunu ayıramazdı.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_herd_endpoints_do_not_fall_into_another_permission() -> None:
    """`/api/animal...` önekleri DOĞRU izne bağlı.

    Tarla modülünde bu tuzak İKİ KEZ yaşandı: yeni bir uç önek listesine
    eklenmeyince sessizce başka bir role düştü. Burada baştan çiviliyoruz.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert required_permission("GET", "/api/animals") == "herd.view"
    assert required_permission("POST", "/api/animals") == "herd.manage"
    assert required_permission("GET", "/api/animal-groups") == "herd.view"
    assert required_permission("POST", "/api/animal-births") == "herd.manage"
    assert required_permission("GET", "/api/herd-dashboard") == "herd.view"
    # Aşı AYRI izin: veteriner girebilir, sürü yapısını değiştiremez.
    assert required_permission("POST", "/api/animal-vaccinations") == "herd.health"
    # Tarla ve saha uçları ETKİLENMEMELİ.
    assert required_permission("GET", "/api/farms") == "farm.view"
    assert required_permission("GET", "/api/field-safety") == "farm.view"
    assert required_permission("GET", "/api/field/snapshot") == "field_service"


def test_health_permission_is_separate_from_manage() -> None:
    """Aşı yetkisi olan herkes hayvan satamaz.

    Bu ayrımın anlamı: `herd.health` taşıyan bir rol `herd.manage`
    taşımayabilir. Test bunu rol haritasından okuyor.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import ROLE_PERMISSIONS

    yazanlar = {r for r, izin in ROLE_PERMISSIONS.items() if "herd.manage" in izin}
    saglik = {r for r, izin in ROLE_PERMISSIONS.items() if "herd.health" in izin}
    assert yazanlar == {"yonetici"}, yazanlar
    assert saglik == {"yonetici"}, saglik
    # Okuma herkeste; admin "*" ile kapsıyor.
    okuyanlar = {r for r, izin in ROLE_PERMISSIONS.items() if "herd.view" in izin}
    assert okuyanlar == {"yonetici", "muhasebe", "satis", "depo", "rapor"}, okuyanlar
    # Depo aşı GİREMEZ — veterinerlik sorumluluğu.
    assert "herd.health" not in ROLE_PERMISSIONS["depo"]


def run_schema_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_herd_schema_sqlite(tmp_path: Path) -> None:
    run_schema_smoke(f"sqlite:///{(tmp_path / 'herd-schema.db').as_posix()}")


_SMOKE = r'''
import os
from datetime import date

from sqlalchemy import create_engine, inspect, text

import app.main  # noqa: F401  (import migration'ları koşturur)

motor = create_engine(os.environ["DATABASE_URL"])
denetci = inspect(motor)

BEKLENEN = {
    "animals", "animal_groups", "animal_vaccinations", "animal_breedings",
    "animal_births", "animal_weights", "milk_yields", "animal_movements",
    "herd_integration_events",
}
eksik = BEKLENEN - set(denetci.get_table_names())
assert not eksik, f"eksik tablo: {sorted(eksik)}"

# --- HER TABLO company_id TAŞIR ------------------------------------------
for tablo in BEKLENEN:
    sutunlar = {c["name"] for c in denetci.get_columns(tablo)}
    assert "company_id" in sutunlar, f"{tablo} company_id taşımıyor"

# --- KÜPE BİÇİMİ DAYATILMIYOR --------------------------------------------
# Şemada ear_tag üzerinde biçim kısıtı OLMAMALI (bkz. modül başlığı).
kupe = [c for c in denetci.get_columns("animals") if c["name"] == "ear_tag"][0]
assert kupe["nullable"] is True, "küçükbaş sürülerinde bireysel küpe zorunlu olmamalı"
soy_fk = {fk["name"] for fk in denetci.get_foreign_keys("animals")}
assert {"fk_animals_mother_same_company", "fk_animals_father_same_company"} <= soy_fk, soy_fk

def reddedilmeli(fn, aciklama):
    """Her deneme KENDİ işleminde koşar.

    İki sebep, ikisi de tarla modülünde ölçüldü:
    * PostgreSQL'de başarısız bir statement işlemin TAMAMINI iptal eder;
      aynı işlemde devam etmek sonraki denemeleri anlamsız kılar.
    * SQLite yabancı anahtarları `PRAGMA foreign_keys=ON` olmadan ZORLAMAZ ve
      pragma BAĞLANTI başınadır — her işlemde yeniden açılmalı.
    """
    try:
        with motor.begin() as ayri:
            ayri.execute(text("PRAGMA foreign_keys=ON") if motor.dialect.name == "sqlite" else text("SELECT 1"))
            fn(ayri)
    except Exception:
        return
    raise AssertionError(f"REDDEDİLMELİYDİ: {aciklama}")


with motor.begin() as baglanti:
    def firma(fid, ad):
        baglanti.execute(text(
            # Boolean sütunlara BAĞLI PARAMETRE veriliyor, düz 1 DEĞİL:
            # PostgreSQL boolean sütuna tamsayı kabul etmez (ölçüldü). Aynı
            # tuzağa tarla FAZ 9'da da düşülmüştü.
            "INSERT INTO companies(id,name,is_active,negative_stock_policy,"
            "credit_limit_policy,farm_area_override_policy,farm_early_harvest_policy,"
            "farm_spraying_dose_required,created_at) VALUES(:i,:a,:aktif,'block','block',"
            "'require_reason','require_reason',:doz,:t)"),
            {"i": fid, "a": ad, "aktif": True, "doz": True, "t": date(2026, 8, 8)})

    def hayvan(fid, tag, durum="ACTIVE", grup=None):
        return baglanti.execute(text(
            "INSERT INTO animals(company_id,ear_tag,species,sex,acquisition,status,"
            "group_id,created_at,updated_at) VALUES(:c,:t,'CATTLE','FEMALE','BORN',"
            ":s,:g,:n,:n) RETURNING id"),
            {"c": fid, "t": tag, "s": durum, "g": grup, "n": date(2026, 8, 8)}).scalar_one()

    firma(9001, "Hayvan A")
    firma(9002, "Hayvan B")

    grup_a = baglanti.execute(text(
        "INSERT INTO animal_groups(company_id,code,name,status,created_at,updated_at) "
        "VALUES(9001,'S1','Sürü 1','ACTIVE',:n,:n) RETURNING id"),
        {"n": date(2026, 8, 8)}).scalar_one()

    # --- 1) KÜPE TEKİLLİĞİ KOŞULLU ---------------------------------------
    hayvan(9001, "TR0001", "SOLD")
    hayvan(9001, "TR0001", "ACTIVE")   # satılmışın numarası tekrar kullanılabilir


reddedilmeli(lambda c: c.execute(text(
    "INSERT INTO animals(company_id,ear_tag,species,sex,acquisition,status,created_at,updated_at) "
    "VALUES(9001,'TR0001','CATTLE','FEMALE','BORN','ACTIVE',:n,:n)"), {"n": date(2026, 8, 8)}),
    "aynı küpeyle İKİNCİ aktif hayvan")

# --- 2) ÇAPRAZ KİRACI BAĞ VERİTABANI SEVİYESİNDE İMKÂNSIZ ----------------
with motor.begin() as c:
    grup_a = c.execute(text("SELECT id FROM animal_groups WHERE company_id=9001")).scalar_one()

reddedilmeli(lambda c: c.execute(text(
    "INSERT INTO animals(company_id,ear_tag,species,sex,acquisition,status,group_id,created_at,updated_at) "
    "VALUES(9002,'TR9999','CATTLE','FEMALE','BORN','ACTIVE',:g,:n,:n)"),
    {"g": grup_a, "n": date(2026, 8, 8)}),
    "B firmasının hayvanı A firmasının sürüsüne bağlanamaz")

# Anne ve baba bağları da aynı bileşik kiracı kuralını veritabanında taşır.
with motor.begin() as c:
    anne_a = c.execute(text(
        "SELECT id FROM animals WHERE company_id=9001 AND ear_tag='TR0001' "
        "AND status='ACTIVE'"
    )).scalar_one()
    baba_a = c.execute(text(
        "INSERT INTO animals(company_id,ear_tag,species,sex,acquisition,status,created_at,updated_at) "
        "VALUES(9001,'TRBABA','CATTLE','MALE','BORN','ACTIVE',:n,:n) RETURNING id"
    ), {"n": date(2026, 8, 8)}).scalar_one()

reddedilmeli(lambda c: c.execute(text(
    "INSERT INTO animals(company_id,ear_tag,species,sex,acquisition,status,mother_id,created_at,updated_at) "
    "VALUES(9002,'TRBCHILD1','CATTLE','FEMALE','BORN','ACTIVE',:p,:n,:n)"),
    {"p": anne_a, "n": date(2026, 8, 8)}),
    "B firmasının yavrusu A firmasının annesine bağlanamaz")

reddedilmeli(lambda c: c.execute(text(
    "INSERT INTO animals(company_id,ear_tag,species,sex,acquisition,status,father_id,created_at,updated_at) "
    "VALUES(9002,'TRBCHILD2','CATTLE','FEMALE','BORN','ACTIVE',:p,:n,:n)"),
    {"p": baba_a, "n": date(2026, 8, 8)}),
    "B firmasının yavrusu A firmasının babasına bağlanamaz")

# Nullable soy semantiği korunur: ebeveynleri bilinmeyen kayıt geçerlidir.
with motor.begin() as c:
    c.execute(text(
        "INSERT INTO animals(company_id,ear_tag,species,sex,acquisition,status,mother_id,father_id,created_at,updated_at) "
        "VALUES(9002,'TRNULLPARENTS','CATTLE','FEMALE','BORN','ACTIVE',NULL,NULL,:n,:n)"
    ), {"n": date(2026, 8, 8)})

# --- 3) SÜT KAYDI: HAYVAN YA DA GRUP, İKİSİ BİRDEN DEĞİL -----------------
with motor.begin() as c:
    a_id = c.execute(text(
        "SELECT id FROM animals WHERE company_id=9001 AND ear_tag='TR0001' AND status='ACTIVE'"
    )).scalar_one()

reddedilmeli(lambda c: c.execute(text(
    "INSERT INTO milk_yields(company_id,animal_id,group_id,milked_on,quantity_liters,created_at,updated_at) "
    "VALUES(9001,:a,:g,:d,10,:n,:n)"),
    {"a": a_id, "g": grup_a, "d": date(2026, 8, 8), "n": date(2026, 8, 8)}),
    "süt kaydı hem hayvana hem gruba bağlanamaz")

reddedilmeli(lambda c: c.execute(text(
    "INSERT INTO milk_yields(company_id,milked_on,quantity_liters,created_at,updated_at) "
    "VALUES(9001,:d,10,:n,:n)"), {"d": date(2026, 8, 8), "n": date(2026, 8, 8)}),
    "süt kaydı ne hayvana ne gruba bağlı olamaz")

# İkisinden BİRİ dolu olan kayıt GEÇMELİ.
with motor.begin() as c:
    c.execute(text(
        "INSERT INTO milk_yields(company_id,animal_id,milked_on,quantity_liters,created_at,updated_at) "
        "VALUES(9001,:a,:d,25.5,:n,:n)"),
        {"a": a_id, "d": date(2026, 8, 8), "n": date(2026, 8, 8)})

# --- 4) MİKTARLAR POZİTİF ------------------------------------------------
reddedilmeli(lambda c: c.execute(text(
    "INSERT INTO animal_weights(company_id,animal_id,weighed_on,weight_kg,created_at,updated_at) "
    "VALUES(9001,:a,:d,0,:n,:n)"),
    {"a": a_id, "d": date(2026, 8, 8), "n": date(2026, 8, 8)}),
    "sıfır canlı ağırlık")

# --- 5) DOĞUM GÜÇLÜĞÜ 1-4 ARALIĞINDA -------------------------------------
reddedilmeli(lambda c: c.execute(text(
    "INSERT INTO animal_births(company_id,mother_id,birth_date,outcome,difficulty,created_at,updated_at) "
    "VALUES(9001,:m,:d,'LIVE',7,:n,:n)"),
    {"m": a_id, "d": date(2026, 8, 8), "n": date(2026, 8, 8)}),
    "geçersiz doğum güçlüğü derecesi")

print("HAYVANCILIK FAZ-1 SEMA SOZLESMESI TAMAM")
'''

"""Tarla Yönetimi V1 — konu #2 "Değişmezler"inin KAPIYA bağlanması.

Bu dosyadaki üç kural bugün zaten doğru. Test yazma sebebi de tam bu: doğru
oldukları için değil, YARIN SESSİZCE BOZULABİLECEKLERİ için. Kodda karşılığı
olmayan bir değişmez, değişmez değildir — bir niyet beyanıdır.

Üçü de TEK TEK uç saymak yerine TÜRETİLİYOR; böylece modüle eklenecek yeni
bir uç ya da yeni bir sütun kendiliğinden kapsama giriyor.

1. **Silme yerine yaşam döngüsü.** Konu: *"Silme yerine yaşam döngüsü
   durumu/iptal kullanılacak; geçmiş maliyet ve verim korunacak."* Bir kayıt
   silinirse ona bağlı maliyet ve verim geçmişi de gider; sezonun dekar başına
   maliyeti geriye dönük DEĞİŞİR. Rapor tutarlılığı silmeyi kaldıramaz.

2. **Para ve miktar NUMERIC.** Konu: *"Parsel alanı ve miktarlar
   Decimal/Numeric; float kullanılmayacak."* Bir sütunun REAL'e düşmesi
   sessizdir: hata vermez, yalnız kuruş kaybettirir ve dekar başına hesapları
   kaydırır.

3. **`float()` çağrısı yok.** Sütun doğru tipte olsa bile kodda bir
   `float(...)` para hesabını kayan noktaya düşürür. Sütun tipi tek başına
   yetmez; ikisi ayrı savunma.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FARM_ROUTER = BACKEND / "app" / "routers" / "farm.py"


def _farm_yollari() -> dict:
    """`farm` etiketli uçlar — OpenAPI'den türetiliyor, elle sayılmıyor."""
    sys.path.insert(0, str(BACKEND))
    from app.main import app

    return {
        yol: islemler
        for yol, islemler in app.openapi()["paths"].items()
        if any("farm" in (tanim.get("tags") or []) for tanim in islemler.values())
    }


def test_farm_module_exposes_no_delete_endpoint() -> None:
    """Tarla modülünde SİLME ucu olamaz.

    Silinen bir faaliyet ya da hasat, sezonun geçmiş maliyet/verim hesabını
    GERİYE DÖNÜK değiştirir: dün 305,56 ₺/dekar diye rapor edilen sezon bugün
    başka bir sayı gösterir ve kimse neden değiştiğini bilemez. Yaşam döngüsü
    durumu (ARCHIVED / CANCELLED) hem kaydı hem geçmişi korur.
    """
    yollar = _farm_yollari()
    assert len(yollar) >= 14, f"tarla uçları eksik toplandı ({len(yollar)})"

    silenler = sorted(yol for yol, islemler in yollar.items() if "delete" in islemler)
    assert silenler == [], (
        "Tarla modülünde SİLME ucu var; konu #2 silme yerine yaşam döngüsü "
        f"durumu istiyor: {silenler}"
    )


def test_farm_lifecycle_columns_exist() -> None:
    """Silme yerine kullanılacak durum sütunları GERÇEKTEN var.

    "Silme ucu yok" tek başına yetmez: silmeye alternatif bir yol yoksa kural
    uygulanabilir değildir ve ilk ihtiyaçta silme ucu eklenir.
    """
    sys.path.insert(0, str(BACKEND))
    from app.farm_schemas import LIFECYCLE_STATUSES, SEASON_STATUSES, TASK_STATUSES

    assert "ARCHIVED" in LIFECYCLE_STATUSES, LIFECYCLE_STATUSES
    assert "CANCELLED" in SEASON_STATUSES, SEASON_STATUSES
    assert "CANCELLED" in TASK_STATUSES, TASK_STATUSES


def test_farm_router_never_calls_float() -> None:
    """`float(...)` çağrısı YOK — para ve miktar Decimal kalmalı.

    Sütun tipi doğru olsa bile koddaki tek bir `float()` hesabı kayan noktaya
    düşürür ve hata vermeden kuruş kaybettirir. Bu yüzden ayrı bir savunma.
    """
    agac = ast.parse(FARM_ROUTER.read_text(encoding="utf-8"))
    cagrilar = [
        dugum.lineno
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.Call)
        and isinstance(dugum.func, ast.Name)
        and dugum.func.id == "float"
    ]
    assert cagrilar == [], f"farm.py'de float() çağrısı var (satır): {cagrilar}"


def run_column_type_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_money_and_quantity_columns_are_numeric_sqlite(tmp_path: Path) -> None:
    run_column_type_smoke(f"sqlite:///{(tmp_path / 'farm-types.db').as_posix()}")


_SMOKE = r'''
from sqlalchemy import create_engine, inspect
import os

# app.main import edilince migration'lar koşar ve şema oluşur.
import app.main  # noqa: F401

TABLOLAR = (
    "farms", "farm_parcels", "crop_seasons", "field_activities",
    "field_activity_inputs", "field_harvests", "field_tasks",
)
# Adı bu parçaları içeren her sütun para ya da miktardır. Liste yerine DESEN
# kullanılıyor: modüle yeni bir tutar sütunu eklendiğinde kendiliğinden
# kapsama girsin, birinin listeyi güncellemesi gerekmesin.
DESENLER = ("_decare", "quantity", "_cost", "amount", "percent", "price")
# Bunlar isimce eşleşiyor ama sayı değil.
ISTISNA = {"area_override_reason", "safety_override_reason"}

motor = create_engine(os.environ["DATABASE_URL"])
denetci = inspect(motor)

hatalar = []
bakilan = 0
for tablo in TABLOLAR:
    for sutun in denetci.get_columns(tablo):
        ad = sutun["name"]
        if ad in ISTISNA or not any(d in ad for d in DESENLER):
            continue
        bakilan += 1
        tip = str(sutun["type"]).upper()
        if not tip.startswith("NUMERIC") and not tip.startswith("DECIMAL"):
            hatalar.append(f"{tablo}.{ad}: {tip}")

assert bakilan >= 10, f"beklenenden az sütun tarandı ({bakilan}) — desen bozulmuş olabilir"
assert not hatalar, "Para/miktar sütunu NUMERIC değil:\n" + "\n".join(hatalar)
print(f"TARLA SUTUN TIPLERI TAMAM ({bakilan} sütun NUMERIC)")
'''

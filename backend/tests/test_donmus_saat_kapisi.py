"""`donmus_saat` sertleştirmesinin KAPISI — gelenek değil, ölçüm.

Üç iddia burada kapıya bağlanır:

1. `app/` bu aleti BİLMEZ: ne `app.main` import grafiği onu çeker, ne de
   `app/` altındaki herhangi bir `.py` dosyasının AST'si onu import eder.
2. `ENVIRONMENT=production` iken `uygula()` hiçbir şey yamalamadan 97 ile
   ölür; kullanıcı kodu koşmaz.
3. Yama ERKEN BAĞLANAN takma adlara (`app.labels.business_now`,
   `app.routers.analytics.business_now`) da ulaşır; ulaşmazsa alet 97 ile
   ölür, sessizce YARI DONMUŞ kalmaz.

Her iddianın KIRMIZI yönü de ölçülür (mutasyon kanıtı): hiç kırmızı
gösterilmemiş bir kapı kapı değildir. Ölümcül yollar `os._exit` kullandığı
için pytest yakalaması onları yutar; bu yüzden HER ölümcül iddia bir ALT
SÜREÇTE koşar ve stderr oradan okunur.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
APP = BACKEND / "app"
ALET = "donmus_saat"
ETKIN_ISARETI = "DONMUS_SAAT_ETKIN"
OLUMCUL_ISARETI = "DONMUS_SAAT_OLUMCUL"
DONMUS_GUN = "2097-06-15"


def _aleti_import_eden_dosyalar(kok: Path) -> list[str]:
    """`kok` altındaki her `.py` dosyasının AST'sinde `donmus_saat` importu arar."""

    bulunanlar: list[str] = []
    for dosya in sorted(kok.rglob("*.py")):
        if "__pycache__" in dosya.parts:
            continue
        agac = ast.parse(dosya.read_text(encoding="utf-8"), filename=str(dosya))
        for dugum in ast.walk(agac):
            if isinstance(dugum, ast.Import):
                for takma in dugum.names:
                    if takma.name == ALET or takma.name.startswith(f"{ALET}."):
                        bulunanlar.append(f"{dosya.relative_to(kok)}:{dugum.lineno}")
            elif isinstance(dugum, ast.ImportFrom):
                modul = dugum.module or ""
                adlar = {takma.name for takma in dugum.names}
                if modul == ALET or modul.startswith(f"{ALET}.") or ALET in adlar:
                    bulunanlar.append(f"{dosya.relative_to(kok)}:{dugum.lineno}")
    return bulunanlar


def _alt_surec(kod: str, ortam: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", kod],
        env=ortam,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _temiz_ortam(tmp_path: Path, veritabani_adi: str) -> dict[str, str]:
    ortam = os.environ.copy()
    ortam.pop("NAZGUL_DONMUS_GUN", None)
    ortam.pop("NAZGUL_DONMUS_SAAT_ALIASSIZ", None)
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["DATABASE_URL"] = f"sqlite:///{(tmp_path / veritabani_adi).as_posix()}"
    return ortam


def test_app_import_grafigi_donmus_saat_cekmez(tmp_path: Path) -> None:
    """Uygulama girişi (`app.main`) aleti ÇEKMEZ; `app/` onu import ETMEZ.

    `app.main` gerçek giriş modülüdür: rotaları o toplar (ölçüldü, bu ağaç:
    içe aktarımdan sonra `app.routers.` önekli 49 modül, ayrıca `app.labels`
    ve `app.routers.analytics` yüklü). 49 statik olarak da doğrulanır:
    `ls backend/app/routers/*.py` eksi `__init__.py` = 49. Bu docstring'in
    önceki sürümü 50 diyordu; o sayı öneki NOKTASIZ (`app.routers`) sayan bir
    ölçümden geliyordu ve paketin KENDİSİNİ de sayıyordu — testin okuduğu
    ifade noktalıdır. Alet üretim imajında diskte durduğu
    için "app onu import etmiyor" iddiası ancak makine tarafından
    doğrulanabildiğinde güvenlik sağlar.
    """

    ortam = _temiz_ortam(tmp_path, "import-grafigi.db")
    kod = (
        "import sys\n"
        "import app.main\n"
        "print('ALET_YUKLU', 'donmus_saat' in sys.modules)\n"
        "print('ROTA_SAYISI', len([m for m in sys.modules if m.startswith('app.routers.')]))\n"
    )
    tamamlanan = _alt_surec(kod, ortam, BACKEND)
    assert tamamlanan.returncode == 0, tamamlanan.stdout + tamamlanan.stderr
    assert "ALET_YUKLU False" in tamamlanan.stdout, tamamlanan.stdout
    assert "ROTA_SAYISI 0" not in tamamlanan.stdout, tamamlanan.stdout

    assert _aleti_import_eden_dosyalar(APP) == []


def test_statik_tarama_mutasyonda_kirmizi_olur(tmp_path: Path) -> None:
    """Mutasyon kanıtı: `app/` kopyasına tek satır eklenince tarama KIRMIZI.

    Hiç kırmızı gösterilmemiş bir tarama, hiçbir şey taramayan bir taramadan
    ayırt edilemez.
    """

    kopya = tmp_path / "app"
    shutil.copytree(APP, kopya, ignore=shutil.ignore_patterns("__pycache__"))
    assert _aleti_import_eden_dosyalar(kopya) == [], "kopya mutasyondan ÖNCE temiz olmalı"

    mutasyon = kopya / "labels.py"
    mutasyon.write_text(
        mutasyon.read_text(encoding="utf-8") + "\nimport donmus_saat\n", encoding="utf-8"
    )
    bulunanlar = _aleti_import_eden_dosyalar(kopya)
    assert bulunanlar, "mutasyona uğramış kopyada tarama KIRMIZI olmalıydı"
    assert any(bulunan.startswith("labels.py:") for bulunan in bulunanlar), bulunanlar


def test_production_ortaminda_uygula_97_ile_olur(tmp_path: Path) -> None:
    """`ENVIRONMENT=production` iken alet yamalamaz: 97, kullanıcı kodu koşmaz.

    Karşı yön aynı testte ölçülür: `ENVIRONMENT=test` ile aynı kod 0 ile
    biter ve `DONMUS_SAAT_ETKIN` yazar — yani red, aletin genel olarak
    bozulmasından değil ORTAM ADINDAN geliyor.
    """

    kod = "import donmus_saat\ndonmus_saat.uygula()\nprint('KULLANICI KODU KOSTU')\n"

    ortam = _temiz_ortam(tmp_path, "uretim-red.db")
    ortam["ENVIRONMENT"] = "production"
    ortam["NAZGUL_DONMUS_GUN"] = DONMUS_GUN
    tamamlanan = _alt_surec(kod, ortam, BACKEND)
    assert tamamlanan.returncode == 97, (
        tamamlanan.returncode,
        tamamlanan.stdout,
        tamamlanan.stderr,
    )
    assert OLUMCUL_ISARETI in tamamlanan.stderr, tamamlanan.stderr
    assert "KULLANICI KODU KOSTU" not in tamamlanan.stdout, tamamlanan.stdout

    karsi_ortam = _temiz_ortam(tmp_path, "uretim-degil.db")
    karsi_ortam["ENVIRONMENT"] = "test"
    karsi_ortam["NAZGUL_DONMUS_GUN"] = DONMUS_GUN
    karsi = _alt_surec(kod, karsi_ortam, BACKEND)
    assert karsi.returncode == 0, (karsi.returncode, karsi.stdout, karsi.stderr)
    assert ETKIN_ISARETI in karsi.stderr, karsi.stderr
    assert "KULLANICI KODU KOSTU" in karsi.stdout, karsi.stdout


def test_erken_baglanan_business_now_da_donar(tmp_path: Path) -> None:
    """ÖNCE import edilen `business_now` takma adları da donar.

    `app/labels.py:31` ve `app/routers/analytics.py:6` adı erken bağlar.
    Alet onlardan SONRA çağrıldığında tek modül yaması onlara ulaşmazdı;
    bu test o iki çağrı yerini doğrudan okur.
    """

    ortam = _temiz_ortam(tmp_path, "erken-baglama.db")
    ortam["ENVIRONMENT"] = "test"
    ortam["NAZGUL_DONMUS_GUN"] = DONMUS_GUN
    kod = (
        "import app.routers.analytics as analytics\n"
        "import app.labels as labels\n"
        "import donmus_saat\n"
        "donmus_saat.uygula()\n"
        "print('ANALYTICS', analytics.business_now().date())\n"
        "print('LABELS', labels.business_now().date())\n"
    )
    tamamlanan = _alt_surec(kod, ortam, BACKEND)
    assert tamamlanan.returncode == 0, tamamlanan.stdout + tamamlanan.stderr
    assert f"ANALYTICS {DONMUS_GUN}" in tamamlanan.stdout, tamamlanan.stdout
    assert f"LABELS {DONMUS_GUN}" in tamamlanan.stdout, tamamlanan.stdout
    assert ETKIN_ISARETI in tamamlanan.stderr, tamamlanan.stderr


def test_takma_ad_yamasi_kapatilinca_97_ile_olur(tmp_path: Path) -> None:
    """Mutasyon kanıtı: takma ad yaması kapatılınca öz-denetim KIRMIZI.

    `NAZGUL_DONMUS_SAAT_ALIASSIZ=1` yalnızca yeniden bağlama adımını atlar;
    takma adlar yine BULUNUR ve yine ÇAĞRILARAK doğrulanır. Denetim eksik
    olsaydı alet burada "ETKIN" yazıp 0 ile çıkardı — yani bu kırmızı,
    denetimin gerçekten iş yaptığının delilidir.
    """

    ortam = _temiz_ortam(tmp_path, "takma-adsiz.db")
    ortam["ENVIRONMENT"] = "test"
    ortam["NAZGUL_DONMUS_GUN"] = DONMUS_GUN
    ortam["NAZGUL_DONMUS_SAAT_ALIASSIZ"] = "1"
    kod = (
        "import app.routers.analytics as analytics\n"
        "import app.labels as labels\n"
        "import donmus_saat\n"
        "donmus_saat.uygula()\n"
        "print('KULLANICI KODU KOSTU')\n"
    )
    tamamlanan = _alt_surec(kod, ortam, BACKEND)
    assert tamamlanan.returncode == 97, (
        tamamlanan.returncode,
        tamamlanan.stdout,
        tamamlanan.stderr,
    )
    assert OLUMCUL_ISARETI in tamamlanan.stderr, tamamlanan.stderr
    assert "erken bağlanan business_now yamalanamadı" in tamamlanan.stderr, tamamlanan.stderr
    assert "KULLANICI KODU KOSTU" not in tamamlanan.stdout, tamamlanan.stdout

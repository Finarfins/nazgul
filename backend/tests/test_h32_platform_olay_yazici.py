"""H32 — istek dışı platform olayları artık kiracı defterine düşmüyor (göç YOK).

Konu: ``app/maintenance.py::_record_recovery``,
``app/backup_cli.py::_force_clear_activity``,
``app/platform_denetim.py::platform_olayi_yaz`` (``sistem=True`` yolu).

--- H32 ÖNCESİ ÖLÇÜLEN (#122 çalışma zamanı merceği, develop 50b0dba) -------

İki yazıcı ``backup.maintenance_recovered`` / ``backup.maintenance_force_cleared``
olaylarını ``SELECT id FROM companies ORDER BY id LIMIT 1`` ile İLK kiracının
``activity_logs``una yazıyordu. PP1 (#122) aynı deseni API yolundan kaldırmıştı
(``platform_olayi_yaz`` -> ``security_audit_logs``, firmasız); bu iki yazıcı
geride kalmıştı ve katalogda ``maintenance_*`` girdisi yoktu.

--- BU DOSYANIN MUTASYON TABLOSU --------------------------------------------

  * ``_record_recovery``i eski ``log_activity`` çağrısına döndürmek
                         -> (a) KIRMIZI (activity_logs +1, security_audit +0)
  * ``_force_clear_activity``i eski çağrıya döndürmek
                         -> (b) KIRMIZI ve (c) KIRMIZI
  * ``sistem=True``yi ``aktor=sistem`` notu yazmadan geçmek
                         -> (a)/(b) KIRMIZI (aktör notu yok)
  * ``platform_olayi_yaz``daki istek/bayrak dışlamasını silmek
                         -> (d) KIRMIZI
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ANAHTAR = "h32-platform-olay-yazici-anahtar-h32-platform-olay-yazici"
PAROLA = "H32PlatformOlay!2026x"


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


@contextmanager
def _uygulama(tmp_path: Path):
    """``app`` paketini bu dosyanın veritabanıyla TAZE içe aktarır, sonra geri
    koyar (``test_pp1_platform_paneli`` ile aynı desen)."""
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    mp = pytest.MonkeyPatch()
    for ad in list(_app_anahtarlari()):
        del sys.modules[ad]
    mp.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'h32.db').as_posix()}")
    mp.setenv("SUNGUR_DATA_DIR", str(tmp_path))
    mp.setenv("SECRET_KEY", ANAHTAR)
    mp.setenv("BOOTSTRAP_ADMIN_PASSWORD", PAROLA)
    mp.setenv("AUTO_MIGRATE", "true")
    mp.syspath_prepend(str(BACKEND))
    try:
        import app.main  # noqa: F401  (şema + önyükleme firması)
        from app.db import engine

        yield engine, mp
    finally:
        mp.undo()
        for ad in list(_app_anahtarlari()):
            del sys.modules[ad]
        sys.modules.update(onceki)


@pytest.fixture()
def ortam(tmp_path: Path):
    with _uygulama(tmp_path) as (engine, mp):
        yield {"engine": engine, "mp": mp, "dizin": tmp_path}


def _sayim(engine) -> tuple[int, int]:
    from sqlalchemy import text

    with engine.connect() as c:
        faaliyet = c.execute(text("SELECT COUNT(*) FROM activity_logs")).scalar_one()
        denetim = c.execute(text("SELECT COUNT(*) FROM security_audit_logs")).scalar_one()
    return int(faaliyet), int(denetim)


def _son_denetim(engine) -> dict:
    from sqlalchemy import text

    with engine.connect() as c:
        return dict(c.execute(text(
            "SELECT company_id, user_id, username, action, outcome, failure_reason"
            " FROM security_audit_logs ORDER BY id DESC LIMIT 1")).mappings().one())


def _firma_var(engine) -> None:
    """Ölçüm ANLAMLI olsun: eski yazıcı ancak bir firma varsa yazabiliyordu."""
    from sqlalchemy import text

    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM companies")).scalar_one() >= 1


# ------------------------------------------------------------------- (a) ---

def test_a_KURTARMA_kiraci_defterine_degil_firmasiz_denetime(ortam) -> None:
    from app import maintenance

    engine = ortam["engine"]
    _firma_var(engine)
    faaliyet_once, denetim_once = _sayim(engine)

    maintenance._record_recovery({
        "operation_id": "h32-bayat", "operation_kind": "backup.restore",
        "owner": "olmus-isci", "started_at": None,
    })

    faaliyet, denetim = _sayim(engine)
    assert faaliyet - faaliyet_once == 0, "kurtarma olayı bir kiracının activity_logs'una düştü"
    assert denetim - denetim_once == 1
    satir = _son_denetim(engine)
    assert satir["company_id"] is None and satir["user_id"] is None and satir["username"] is None
    assert satir["action"] == "platform.mt_recover"
    assert satir["outcome"] == "success"
    assert "olay=backup.maintenance_recovered" in satir["failure_reason"], satir
    assert "aktor=sistem" in satir["failure_reason"], satir
    assert "islem=h32-bayat" in satir["failure_reason"], satir
    # Journal (dış, yetkili iz) aynen yazılıyor.
    gunluk = (ortam["dizin"] / "restore_journal.jsonl").read_text("utf-8").splitlines()
    assert json.loads(gunluk[-1])["event"] == "backup.maintenance_recovered"


# ------------------------------------------------------------------- (b) ---

def test_b_CLI_ZORLA_TEMIZLEME_kiraci_defterine_degil_firmasiz_denetime(ortam) -> None:
    from app import backup_cli

    engine = ortam["engine"]
    _firma_var(engine)
    ortam["mp"].setattr(backup_cli.settings, "sungur_data_dir", str(ortam["dizin"]))
    ortam["mp"].setenv("SUNGUR_OPERATOR_ID", "h32-operator-7")
    ortam["mp"].setattr(
        backup_cli, "clear_maintenance",
        lambda *_a, **_k: {"cleared": True, "operation_id": "h32-zorla", "forced": True},
    )
    faaliyet_once, denetim_once = _sayim(engine)

    sonuc = backup_cli._maintenance_clear("h32-zorla", True, "Doğrulanmış geri alma sonrası")

    faaliyet, denetim = _sayim(engine)
    assert faaliyet - faaliyet_once == 0, "zorla temizleme bir kiracının activity_logs'una düştü"
    assert denetim - denetim_once == 1
    satir = _son_denetim(engine)
    assert satir["company_id"] is None and satir["user_id"] is None and satir["username"] is None
    assert satir["action"] == "platform.mt_force"
    assert "olay=backup.maintenance_force_cleared" in satir["failure_reason"], satir
    assert "aktor=sistem" in satir["failure_reason"], satir
    assert "islem=h32-zorla" in satir["failure_reason"], satir
    assert "operator_id=h32-operator-7" in satir["failure_reason"], satir
    assert "gerekce=Doğrulanmış geri alma sonrası" in satir["failure_reason"], satir
    gunluk = (ortam["dizin"] / "restore_journal.jsonl").read_text("utf-8").splitlines()
    kayit = json.loads(gunluk[-1])
    assert kayit["event"] == "backup.maintenance_force_cleared"
    assert kayit["details"]["caller"] == sonuc["caller"]


# ------------------------------------------------------------------- (c) ---

@pytest.mark.parametrize("modul", ["app/maintenance.py", "app/backup_cli.py"])
def test_c_ILK_FIRMA_GERI_DONUSU_KAYNAKTA_YOK(modul: str) -> None:
    """#122 öncesi desen iki modülde de YOK: ne ilk-firma sorgusu ne ``log_activity``."""
    kaynak = (BACKEND / modul).read_text(encoding="utf-8")
    assert "ORDER BY id LIMIT 1" not in kaynak, modul
    assert "log_activity" not in kaynak, modul
    assert "platform_olayi_yaz" in kaynak, modul


# ------------------------------------------------------------------- (d) ---

def test_d_ISTEKSIZ_CAGRI_BAYRAKSIZ_REDDEDILIR_ve_SATIR_YAZILMAZ(ortam) -> None:
    from types import SimpleNamespace

    from app.platform_denetim import platform_olayi_yaz

    engine = ortam["engine"]
    _, once = _sayim(engine)
    with pytest.raises(ValueError, match="sistem=True yalnız request=None"):
        platform_olayi_yaz(None, "backup.maintenance_recovered", "bayraksız")
    istek = SimpleNamespace(state=SimpleNamespace(user={"id": 1}), client=None,
                            headers={}, url=SimpleNamespace(path="/api/platform/backups"))
    with pytest.raises(ValueError, match="sistem=True yalnız request=None"):
        platform_olayi_yaz(istek, "backup.maintenance_recovered", "ikisi birden", sistem=True)
    assert _sayim(engine)[1] == once

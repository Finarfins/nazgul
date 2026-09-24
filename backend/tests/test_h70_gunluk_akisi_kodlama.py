"""H70 — `yerel_hesap` günlüğü cp1252 konsolda Türkçe satırı KAYBETMEZ.

Ölçülen kusur (8a6e3e1, `PYTHONIOENCODING=cp1252 pytest -s
test_platform_backup_security.py`): `--- Logging error ---` /
`UnicodeEncodeError: 'charmap' codec can't encode character '\\u0131'` —
"Veritabanı migration seviyesi doğrulandı" satırı hiç yazılmıyordu. Sebep
`app/main.py`deki `logging.StreamHandler(sys.stdout)`: stdout KATI kodlar,
cp1252'de `ı/ğ/ş/İ` yok. cp1254 ve UTF-8'de görünmez (CI yeşil).

MUTASYON: `app/main.py`de tutucuyu yeniden `logging.StreamHandler(sys.stdout)`
yapmak -> `test_main_yerel_hesap_tutucusu_dayanikli_akis` KIRMIZI;
`KodlamayaDayanikliAkis.emit`teki `except UnicodeEncodeError` dalını silmek
-> `test_cp1252_akista_turkce_satir_KACISLA_yazilir` KIRMIZI.
"""
from __future__ import annotations

import ast
import io
import logging
from pathlib import Path

from app.gunluk_akisi import KodlamayaDayanikliAkis

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"
SATIR = "Veritabanı migration seviyesi doğrulandı — İş"


def _akis(kodlama: str) -> tuple[io.BytesIO, io.TextIOWrapper]:
    ham = io.BytesIO()
    return ham, io.TextIOWrapper(ham, encoding=kodlama, errors="strict", newline="\n")


def _yaz(tutucu: logging.Handler) -> list[logging.LogRecord]:
    """Tek bir satır yaz; `handleError`a düşen kayıtları döndür."""
    hatalar: list[logging.LogRecord] = []
    tutucu.handleError = hatalar.append  # type: ignore[method-assign]
    tutucu.setFormatter(logging.Formatter("%(message)s"))
    tutucu.emit(logging.LogRecord("yerel_hesap", logging.INFO, __file__, 1, SATIR, None, None))
    return hatalar


def test_KUSUR_duz_StreamHandler_cp1252de_satiri_KAYBEDER() -> None:
    """Önkabulün kanıtı: düz tutucu cp1252'de hataya düşer, satır yazılmaz."""
    ham, akis = _akis("cp1252")
    hatalar = _yaz(logging.StreamHandler(akis))
    akis.flush()
    assert len(hatalar) == 1
    assert ham.getvalue() == b""


def test_cp1252_akista_turkce_satir_KACISLA_yazilir() -> None:
    ham, akis = _akis("cp1252")
    assert _yaz(KodlamayaDayanikliAkis(akis)) == []
    akis.flush()
    yazilan = ham.getvalue().decode("cp1252")
    assert yazilan == SATIR.encode("cp1252", "backslashreplace").decode("cp1252") + "\n"
    assert "Veritaban\\u0131" in yazilan and "do\\u011fruland\\u0131" in yazilan


def test_utf8_ve_cp1254_akista_satir_AYNEN_yazilir() -> None:
    """Türkçe Windows (cp1254) ve UTF-8: kaçış YOK, satır birebir."""
    for kodlama in ("utf-8", "cp1254"):
        ham, akis = _akis(kodlama)
        assert _yaz(KodlamayaDayanikliAkis(akis)) == [], kodlama
        akis.flush()
        assert ham.getvalue().decode(kodlama) == SATIR + "\n", kodlama


def test_main_yerel_hesap_tutucusu_dayanikli_akis() -> None:
    """AST: `app/main.py` stdout'a DÜZ `StreamHandler` kurmuyor."""
    agac = ast.parse(MAIN.read_text(encoding="utf-8"))
    cagrilar = [
        ast.unparse(d.func) for d in ast.walk(agac) if isinstance(d, ast.Call)
    ]
    assert "logging.StreamHandler" not in cagrilar
    assert "StreamHandler" not in cagrilar
    assert "KodlamayaDayanikliAkis" in cagrilar

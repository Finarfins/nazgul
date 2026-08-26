from __future__ import annotations

from pathlib import Path


BACKEND = Path(__file__).resolve().parent
MOJIBAKE_MARKERS = ("Ã", "Å", "Ä", "Â", "â€", "ï»¿", "�", "ţ", "đ")
RESTORE_MESSAGES = (
    "Geri yüklenebilir silme kaydı bulunamadı",
    "Bu kayıt türü otomatik geri yüklemeyi desteklemiyor",
    "Firma kapsamı uyuşmuyor",
    "Aynı kimlikte kayıt zaten mevcut",
)


def test_backend_python_sources_are_utf8_without_mojibake() -> None:
    failures: list[str] = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        markers = sorted({marker for marker in MOJIBAKE_MARKERS if marker in source})
        if markers:
            failures.append(f"{path.relative_to(BACKEND)}: {markers}")

    assert not failures, "Mojibake detected:\n" + "\n".join(failures)


def test_restore_error_messages_remain_exact_turkish_utf8() -> None:
    source = (BACKEND / "app" / "change_history.py").read_text(encoding="utf-8")
    for message in RESTORE_MESSAGES:
        assert source.count(message) == 1

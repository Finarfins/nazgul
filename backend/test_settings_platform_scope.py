from __future__ import annotations

import ast
import re
from pathlib import Path


BACKEND = Path(__file__).resolve().parent
# `settings` PLATFORM GENELIDIR ve bu kapi onu KIRACI verisinden korur:
# tabloda `company_id` sutunu YOKTUR, yani oraya yazilan firma verisi
# kiracilar arasinda PAYLASILIR. Listeye giren her yazicinin PLATFORM
# DUZEYINDE bir olguyu yazdigi burada gerekcesiyle yazilir.
#
# `app/field_stok_zamanlayici.py` — ACILIS KOSULU 4 (canlilik/gecikme
# sinyali). Tek anahtar: `field_stok_zamanlayici.heartbeat`. Zamanlayici
# SUREC DUZEYINDE tek bir thread'dir ve TUM firmalari TEK dongude gezer;
# kalp atisi o thread'in olgusudur, bir firmanin DEGIL. `company_id`
# tasiyan bir kalp atisi kiracisi olmayan bir seye kiraci UYDURMAK olurdu.
# Satirin govdesi (started_at, finished_at, companies_processed,
# companies_total, events_processed, last_error) TEK BIR FIRMANIN verisini
# DEGIL, dongunun TAMAMININ sayaclarini tasir. Yeni bir tablo alternatifi
# GOC demekti ve bu dilim goc acmiyor; `platform_maintenance` ise BAKIM
# ISLEMININ kalp atisidir (goc 20260728_0034) ve oraya yazmak kilit
# sahipligi hakkinda YALAN soylerdi.
ALLOWED_SETTINGS_WRITERS = {
    Path("seed_demo_data.py"),
    Path("app/field_stok_zamanlayici.py"),
}
RAW_SETTINGS_WRITE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)"
    r"\s+[\"'`\[]?settings\b",
    re.IGNORECASE,
)
WRITE_FUNCTIONS = {"insert", "update", "delete"}
EXCLUDED_DIRECTORIES = {"site-packages", "node_modules", "build", "dist"}


def _production_python_files() -> list[Path]:
    files: list[Path] = []
    for path in BACKEND.rglob("*.py"):
        relative = path.relative_to(BACKEND)
        if path.name.startswith("test_") or "tests" in relative.parts:
            continue
        if any(
            part.startswith(".") or part in EXCLUDED_DIRECTORIES
            for part in relative.parts
        ):
            continue
        files.append(path)
    return files


def _writes_settings_table(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    settings_names = {"settings_table"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "settings_table":
                    settings_names.add(alias.asname or alias.name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if RAW_SETTINGS_WRITE.search(node.value):
                return True
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in WRITE_FUNCTIONS:
            if node.args and isinstance(node.args[0], ast.Name):
                if node.args[0].id in settings_names:
                    return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in WRITE_FUNCTIONS:
            if node.args and isinstance(node.args[0], ast.Name):
                if node.args[0].id in settings_names:
                    return True
            if isinstance(node.func.value, ast.Name):
                if node.func.value.id in settings_names:
                    return True
    return False


def test_only_demo_seed_writes_platform_settings_table() -> None:
    writers = {
        path.relative_to(BACKEND)
        for path in _production_python_files()
        if _writes_settings_table(path)
    }
    assert writers == ALLOWED_SETTINGS_WRITERS, (
        "settings platform-genelidir; firma verisi companies tablosuna veya "
        f"company_id tasiyan bir tabloya yazilmalidir. Bulunan yazicilar: {writers}"
    )

"""`conftest.collect_ignore` karantina listesinin PİNİ.

`run_isolated_tests.py` listeyi AST ile okuyup o adları manifestten düşürür.
Listeyi çivileyen bir kapı yoktu: yeni bir dosya adı eklemek testleri
SESSİZCE her koşumdan çıkarırdı. Bu dosya o sessizliği kapatır.

LEGACY_TEST_MIGRATION_PLAN.md karantinayı SINIF olarak anlatır; 21 dosya
adından HİÇBİRİNİ anmaz. Dosya başına gerekçe uydurulmadı — hepsi
`GEREKÇESİZ`. Plan ile liste ayrışması RAPOR edilir, belge DÜZELTİLMEZ.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from run_isolated_tests import _collect_ignored_files, discover_active_test_files

BACKEND = Path(__file__).resolve().parents[1]
CONFTEST = BACKEND / "conftest.py"
PLAN = BACKEND / "LEGACY_TEST_MIGRATION_PLAN.md"

# Plan dosya adı ANMAZ. Gerekçe uydurulmadı.
PINLI_KARANTINA: frozenset[tuple[str, str]] = frozenset(
    {
        ("test_detail_workflows.py", "GEREKÇESİZ"),
        ("test_document_engine.py", "GEREKÇESİZ"),
        ("test_e2e_browser.py", "GEREKÇESİZ"),
        ("test_finance_core.py", "GEREKÇESİZ"),
        ("test_imports.py", "GEREKÇESİZ"),
        ("test_inventory_reports.py", "GEREKÇESİZ"),
        ("test_operations.py", "GEREKÇESİZ"),
        ("test_outputs.py", "GEREKÇESİZ"),
        ("test_performance_filters.py", "GEREKÇESİZ"),
        ("test_search_analytics.py", "GEREKÇESİZ"),
        ("test_stabilization.py", "GEREKÇESİZ"),
        ("test_stabilization2.py", "GEREKÇESİZ"),
        ("test_tenancy_notifications.py", "GEREKÇESİZ"),
        ("test_transaction_integrity.py", "GEREKÇESİZ"),
        ("test_transaction_warehouse.py", "GEREKÇESİZ"),
        ("test_v2_2_validations.py", "GEREKÇESİZ"),
        ("test_v2_3_payment_lists.py", "GEREKÇESİZ"),
        ("test_v2_4_dashboard.py", "GEREKÇESİZ"),
        ("test_v2_5_cari_crm.py", "GEREKÇESİZ"),
        ("test_v2_6_quick_actions.py", "GEREKÇESİZ"),
        ("test_v2_7_tenant_security.py", "GEREKÇESİZ"),
    }
)
PINLI_ADLAR: frozenset[str] = frozenset(ad for ad, _ in PINLI_KARANTINA)

GERCEK_EK_AD = "test_bulk_price_contract.py"
BAYAT_AD = "test_bayat_karantina_dosyasi_yok.py"


def _collect_ignore_adlari(yol: Path) -> list[str]:
    """`run_isolated_tests._collect_ignored_files` ile AYNI AST yürüyüşü."""
    agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
    for dugum in agac.body:
        if isinstance(dugum, ast.Assign) and any(
            isinstance(hedef, ast.Name) and hedef.id == "collect_ignore"
            for hedef in dugum.targets
        ):
            deger = ast.literal_eval(dugum.value)
            return [Path(oge).name for oge in deger]
    raise RuntimeError("conftest.py içinde collect_ignore listesi bulunamadı")


def _kesfedilen_test_dosyalari() -> list[Path]:
    kok = list(BACKEND.glob("test_*.py"))
    icice = BACKEND / "tests"
    ic = list(icice.glob("test_*.py")) if icice.is_dir() else []
    return kok + ic


def _aktif_sayisi(yoksayilan: set[str]) -> int:
    kok = [yol for yol in BACKEND.glob("test_*.py") if yol.name not in yoksayilan]
    icice = BACKEND / "tests"
    ic = list(icice.glob("test_*.py")) if icice.is_dir() else []
    return len(kok) + len(ic)


def _atilacak_conftest(tmp_path: Path, adlar: list[str]) -> Path:
    yol = tmp_path / "conftest.py"
    govde = "collect_ignore = [\n" + "".join(f'    "{ad}",\n' for ad in adlar) + "]\n"
    yol.write_text(govde, encoding="utf-8")
    return yol


def _iddia_capaya_bagli(adlar: set[str]) -> None:
    eklenen = sorted(adlar - PINLI_ADLAR)
    eksik = sorted(PINLI_ADLAR - adlar)
    assert eklenen == [] and eksik == [], (
        "collect_ignore çapadan ayrıştı; eklenen ad sessizce koşumdan düşer, "
        f"eksik ad sessizce geri döner. eklenen={eklenen} eksik={eksik}"
    )


def _iddia_diskte_var(adlar: list[str], kok: Path = BACKEND) -> None:
    bayat = sorted(ad for ad in adlar if not (kok / ad).is_file())
    assert bayat == [], (
        "collect_ignore bayat ad taşıyor; diskte olmayan girdi bir kusurdur: "
        f"{bayat}"
    )


def _iddia_manifest_sayisi(yoksayilan: list[str]) -> None:
    kesfedilen = len(_kesfedilen_test_dosyalari())
    aktif = _aktif_sayisi(set(yoksayilan))
    beklenen = kesfedilen - len(yoksayilan)
    assert aktif == beklenen, (
        f"izole koşucu manifesti {aktif}, keşfedilen {kesfedilen} − "
        f"len(collect_ignore)={len(yoksayilan)} = {beklenen}"
    )


def test_karantina_kumesi_capaya_bagli() -> None:
    adlar = set(_collect_ignore_adlari(CONFTEST))
    assert len(PINLI_ADLAR) == 21
    _iddia_capaya_bagli(adlar)
    assert _collect_ignored_files() == PINLI_ADLAR


def test_karantina_gerekceleri_uydurulmadi() -> None:
    """Plandaki dosya-başı gerekçe yoktur; uydurulmaz, GEREKÇESİZ yazılır."""
    plan = PLAN.read_text(encoding="utf-8")
    anilan = sorted(ad for ad in PINLI_ADLAR if ad in plan)
    assert anilan == [], (
        "LEGACY_TEST_MIGRATION_PLAN.md dosya adı anıyor; gerekçe oradan "
        f"alınmalı, GEREKÇESİZ bırakılmamalı: {anilan}"
    )
    uydurma = sorted(
        f"{ad}:{gerekce}"
        for ad, gerekce in PINLI_KARANTINA
        if gerekce != "GEREKÇESİZ"
    )
    assert uydurma == [], f"plan anmadığı dosyaya gerekçe uydurulmuş: {uydurma}"


def test_karantina_dosyalari_diskte_var() -> None:
    _iddia_diskte_var(_collect_ignore_adlari(CONFTEST))


def test_izole_kosucu_manifesti_karantina_kadar_kucuk() -> None:
    yoksayilan = _collect_ignore_adlari(CONFTEST)
    kesfedilen = _kesfedilen_test_dosyalari()
    aktif = discover_active_test_files()
    assert len(aktif) == len(kesfedilen) - len(yoksayilan)
    _iddia_manifest_sayisi(yoksayilan)


def test_gercek_dosya_eklemek_adiyla_kirmizi(tmp_path: Path) -> None:
    """Listeye gerçek bir aktif dosya eklemek, o ADI söyleyerek kırmızı olur."""
    assert (BACKEND / GERCEK_EK_AD).is_file()
    assert GERCEK_EK_AD not in PINLI_ADLAR
    kopya = _atilacak_conftest(
        tmp_path, _collect_ignore_adlari(CONFTEST) + [GERCEK_EK_AD]
    )
    adlar = set(_collect_ignore_adlari(kopya))
    with pytest.raises(AssertionError) as dusen:
        _iddia_capaya_bagli(adlar)
    assert GERCEK_EK_AD in str(dusen.value)


def test_bayat_adi_listeden_silmek_kirmizi(tmp_path: Path) -> None:
    """Listedeki bir adı (bayat sandığı için) çıkarmak çapayı kırar."""
    silinen = "test_e2e_browser.py"
    assert silinen in PINLI_ADLAR
    kalan = [ad for ad in _collect_ignore_adlari(CONFTEST) if ad != silinen]
    kopya = _atilacak_conftest(tmp_path, kalan)
    adlar = set(_collect_ignore_adlari(kopya))
    with pytest.raises(AssertionError) as dusen:
        _iddia_capaya_bagli(adlar)
    assert silinen in str(dusen.value)


def test_bayat_ad_diskte_yoksa_kirmizi(tmp_path: Path) -> None:
    """Diskte olmayan (bayat) bir girdi varlık ve sayım kapısını kırar."""
    assert not (BACKEND / BAYAT_AD).is_file()
    kopya = _atilacak_conftest(
        tmp_path, _collect_ignore_adlari(CONFTEST) + [BAYAT_AD]
    )
    adlar = _collect_ignore_adlari(kopya)
    with pytest.raises(AssertionError) as disk:
        _iddia_diskte_var(adlar)
    assert BAYAT_AD in str(disk.value)
    with pytest.raises(AssertionError) as sayim:
        _iddia_manifest_sayisi(adlar)
    assert BAYAT_AD in str(sayim.value) or "collect_ignore" in str(sayim.value)

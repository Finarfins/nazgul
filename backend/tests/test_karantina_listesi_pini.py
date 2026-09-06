"""`conftest.collect_ignore` karantina listesinin PİNİ.

`run_isolated_tests.py` listeyi AST ile okuyup o adları manifestten düşürür.
Listeyi çivileyen bir kapı yoktu: yeni bir dosya adı eklemek testleri
SESSİZCE her koşumdan çıkarırdı. Bu dosya o sessizliği kapatır.

`backend/LEGACY_TEST_MIGRATION_PLAN.md` karantinayı SINIF olarak anlatır ve
21 dosya adından HİÇBİRİNİ anmaz. Dosya-başı gerekçe
`docs/LEGACY_TEST_MIGRATION_PLAN.md` ek tablosunda ÖLÇÜLDÜ (yalnız koşum,
temiz ağaç). Pin'deki gerekçe o tablonun `measured failure class`
sütunudur; boş ya da `GEREKÇESİZ` kırmızıdır.
"""
from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

from run_isolated_tests import _collect_ignored_files, discover_active_test_files

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CONFTEST = BACKEND / "conftest.py"
PLAN = BACKEND / "LEGACY_TEST_MIGRATION_PLAN.md"
EK_PLAN = REPO / "docs" / "LEGACY_TEST_MIGRATION_PLAN.md"

# Gerekçe = docs/LEGACY_TEST_MIGRATION_PLAN.md ek tablosunun measured class sütunu.
PINLI_KARANTINA: frozenset[tuple[str, str]] = frozenset(
    {
        ("test_detail_workflows.py", "executable smoke script"),
        ("test_document_engine.py", "missing fixture"),
        ("test_e2e_browser.py", "ImportError"),
        ("test_finance_core.py", "missing fixture"),
        ("test_imports.py", "missing fixture"),
        ("test_inventory_reports.py", "missing fixture"),
        ("test_operations.py", "missing fixture"),
        ("test_outputs.py", "executable smoke script"),
        ("test_performance_filters.py", "executable smoke script"),
        ("test_search_analytics.py", "missing fixture"),
        ("test_stabilization.py", "missing fixture"),
        ("test_stabilization2.py", "missing fixture"),
        ("test_tenancy_notifications.py", "missing fixture"),
        ("test_transaction_integrity.py", "executable smoke script"),
        ("test_transaction_warehouse.py", "executable smoke script"),
        ("test_v2_2_validations.py", "missing fixture"),
        ("test_v2_3_payment_lists.py", "missing fixture"),
        ("test_v2_4_dashboard.py", "passes!"),
        ("test_v2_5_cari_crm.py", "missing fixture"),
        ("test_v2_6_quick_actions.py", "missing fixture"),
        ("test_v2_7_tenant_security.py", "missing fixture"),
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


def _iddia_gerekce_dolu(karantina: Iterable[tuple[str, str]]) -> None:
    """Yeni ad gerekçesiz (boş / GEREKÇESİZ) eklenemez."""
    bos = sorted(
        ad
        for ad, gerekce in karantina
        if not str(gerekce).strip() or str(gerekce).strip() == "GEREKÇESİZ"
    )
    assert bos == [], (
        "karantina adı gerekçesiz eklendi; boş ya da GEREKÇESİZ gerekçe "
        f"kırmızıdır: {bos}"
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
    bayat = sorted(ad for ad in yoksayilan if not (BACKEND / ad).is_file())
    assert aktif == beklenen, (
        f"izole koşucu manifesti {aktif}, keşfedilen {kesfedilen} − "
        f"len(collect_ignore)={len(yoksayilan)} = {beklenen}"
        + (f"; bayat adlar={bayat}" if bayat else "")
    )


def test_karantina_kumesi_capaya_bagli() -> None:
    adlar = set(_collect_ignore_adlari(CONFTEST))
    assert len(PINLI_ADLAR) == 21
    _iddia_capaya_bagli(adlar)
    assert _collect_ignored_files() == PINLI_ADLAR


def test_karantina_gerekceleri_bos_olamaz() -> None:
    """Her pin girdisinin gerekçesi dolu olmalı; GEREKÇESİZ artık kapı değil."""
    _iddia_gerekce_dolu(PINLI_KARANTINA)


def test_yeni_ad_gerekcesiz_kirmizi() -> None:
    """Listeye gerekçesiz yeni ad eklemek, o ADI söyleyerek kırmızı olur."""
    with pytest.raises(AssertionError) as bos:
        _iddia_gerekce_dolu({*PINLI_KARANTINA, (GERCEK_EK_AD, "")})
    assert GERCEK_EK_AD in str(bos.value)
    with pytest.raises(AssertionError) as bosluk:
        _iddia_gerekce_dolu({*PINLI_KARANTINA, (GERCEK_EK_AD, "   ")})
    assert GERCEK_EK_AD in str(bosluk.value)
    with pytest.raises(AssertionError) as placeholder:
        _iddia_gerekce_dolu({*PINLI_KARANTINA, (GERCEK_EK_AD, "GEREKÇESİZ")})
    assert GERCEK_EK_AD in str(placeholder.value)


def test_sinif_plani_dosya_adi_anmaz() -> None:
    """Sınıf planı hâlâ addan arınık; ölçüm ek belgededir."""
    plan = PLAN.read_text(encoding="utf-8")
    anilan = sorted(ad for ad in PINLI_ADLAR if ad in plan)
    assert anilan == [], (
        "backend/LEGACY_TEST_MIGRATION_PLAN.md dosya adı anıyor; gerekçe "
        f"docs/LEGACY_TEST_MIGRATION_PLAN.md ek tablosuna aittir: {anilan}"
    )


def test_olculen_gerekceler_ek_tabloda() -> None:
    """Pin gerekçesi ek tablonun measured-class sütunuyla aynı satırda durur."""
    satirlar = EK_PLAN.read_text(encoding="utf-8").splitlines()
    eksik = sorted(
        f"{ad}:{gerekce}"
        for ad, gerekce in PINLI_KARANTINA
        if not any(ad in satir and gerekce in satir for satir in satirlar)
    )
    assert eksik == [], (
        "docs/LEGACY_TEST_MIGRATION_PLAN.md ek tablosu pin gerekçesini "
        f"taşımıyor: {eksik}"
    )


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
    assert BAYAT_AD in str(sayim.value)

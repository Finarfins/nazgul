"""H94 kapısı: PG ikizleri libpq `options`ını YALNIZ `pg_secenekleri` ile kurar.

KUSUR (#164 tur 1, ÖLÇÜLDÜ): libpq `PGOPTIONS`u yalnız bağlantı dizesinde
`options` YOKSA okur. Yedi ikiz kendi `-csearch_path=…`ını
`connect_args={"options": …}`, URL'deki `?options=` ya da alt süreçte yeniden
yazılan `PGOPTIONS` ile veriyordu; conftest'in koyduğu `Europe/Istanbul`
bu bağlantılarda düşüyor, ikiz sunucunun (CI'da UTC) diliminde koşuyordu.

Bu dosya DB'ye bağlanmaz; kaynağı AST ile tarar. Kırmızı yakan şekiller:
  * `"options"` sözlük anahtarı ya da `options=` anahtar kelimesi, değeri
    `pg_secenekleri(...)` çağrısı DEĞİLSE;
  * `"PGOPTIONS"`e yazma (`x["PGOPTIONS"] = v`, `PGOPTIONS=` anahtar
    kelimesi), değer `pg_secenekleri(...)` DEĞİLSE; ve `PGOPTIONS`u silen
    her kullanım (`pop`, `del`) — alt süreç yine sunucu diliminde açılır;
  * belge dizesi DIŞINDA `options=` içeren her dize (elle yazılmış URL).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.pg_ikiz_yardimci import PG_OTURUM_DILIMI, pg_secenekleri

BACKEND = Path(__file__).resolve().parents[1]
YARDIMCI = "pg_secenekleri"

#: Tur 1'de düzeltilen yedi ikiz: her biri kapıdan GEÇEN en az bir yol taşır.
DUZELTILEN_IKIZLER = (
    "test_numeric_migration_postgresql.py",
    "test_postgresql_app_smoke.py",
    "test_pos_system_customer_tenant_default_postgresql.py",
    "test_sec4_acilis_migrasyon_postgresql.py",
    "test_security_audit_untenanted_check_postgresql.py",
    "test_transactions_postgresql.py",
    "test_workflow_postgresql.py",
)


PG_TWINS_PIN = BACKEND / "tests" / "pins" / "pg_twins.txt"


def _taranan_dosyalar() -> list[Path]:
    dosyalar: set[Path] = set()
    for satir in PG_TWINS_PIN.read_text(encoding="utf-8").splitlines():
        satir = satir.strip()
        if not satir:
            continue
        yol = BACKEND / satir
        assert yol.is_file(), f"pg_twins envanterindeki dosya diskte yok: {satir}"
        dosyalar.add(yol)
    dosyalar |= set(BACKEND.glob("*_postgresql.py"))
    dosyalar |= set((BACKEND / "tests").rglob("*_postgresql.py"))
    dosyalar.add(BACKEND / "tests" / "pg_ikiz_yardimci.py")
    dosyalar.add(BACKEND / "conftest.py")
    return sorted(dosyalar)


def _yardimci_cagrisi(dugum: ast.AST | None) -> bool:
    if not isinstance(dugum, ast.Call):
        return False
    f = dugum.func
    ad = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
    return ad == YARDIMCI


def _belge_dizeleri(agac: ast.AST) -> set[int]:
    kimlikler: set[int] = set()
    for d in ast.walk(agac):
        if isinstance(d, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            govde = d.body
            if govde and isinstance(govde[0], ast.Expr) and isinstance(govde[0].value, ast.Constant):
                kimlikler.add(id(govde[0].value))
    return kimlikler


def _ihlaller(kaynak: str) -> tuple[list[str], int]:
    """(ihlaller, kapıdan geçen yol sayısı)."""
    agac = ast.parse(kaynak)
    belgeler = _belge_dizeleri(agac)
    ihlal: list[str] = []
    gecen = 0
    izinli_pgoptions: set[int] = set()

    for d in ast.walk(agac):
        # x["PGOPTIONS"] = v
        if isinstance(d, ast.Assign):
            for hedef in d.targets:
                if (
                    isinstance(hedef, ast.Subscript)
                    and isinstance(hedef.slice, ast.Constant)
                    and hedef.slice.value == "PGOPTIONS"
                ):
                    izinli_pgoptions.add(id(hedef.slice))
                    if _yardimci_cagrisi(d.value):
                        gecen += 1
                    else:
                        ihlal.append(f"{d.lineno}: PGOPTIONS {YARDIMCI} olmadan yazılıyor")
        # env.get("PGOPTIONS") yalnız OKUR: zararsız.
        elif (
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "get"
            and d.args
            and isinstance(d.args[0], ast.Constant)
            and d.args[0].value == "PGOPTIONS"
        ):
            izinli_pgoptions.add(id(d.args[0]))
        elif isinstance(d, ast.Dict):
            for anahtar, deger in zip(d.keys, d.values):
                if isinstance(anahtar, ast.Constant) and anahtar.value in ("options", "PGOPTIONS"):
                    izinli_pgoptions.add(id(anahtar))
                    if _yardimci_cagrisi(deger):
                        gecen += 1
                    else:
                        ihlal.append(f"{anahtar.lineno}: '{anahtar.value}' {YARDIMCI} olmadan veriliyor")
        elif isinstance(d, ast.keyword) and d.arg in ("options", "PGOPTIONS"):
            if _yardimci_cagrisi(d.value):
                gecen += 1
            else:
                ihlal.append(f"{d.value.lineno}: {d.arg}= {YARDIMCI} olmadan veriliyor")

    for d in ast.walk(agac):
        if not (isinstance(d, ast.Constant) and isinstance(d.value, str)):
            continue
        if id(d) in belgeler:
            continue
        if d.value == "PGOPTIONS" and id(d) not in izinli_pgoptions:
            ihlal.append(f"{d.lineno}: PGOPTIONS yazma/okuma dışında kullanılıyor (silme?)")
        elif "options=" in d.value:
            ihlal.append(f"{d.lineno}: elle yazılmış 'options=' dizesi")
    return sorted(ihlal), gecen


def test_yardimci_dilimi_sona_ekler_ve_cagiraninkini_korur() -> None:
    assert pg_secenekleri("-csearch_path=s1") == (
        f"-csearch_path=s1 -c timezone={PG_OTURUM_DILIMI}"
    )
    assert pg_secenekleri() == f"-c timezone={PG_OTURUM_DILIMI}"
    assert pg_secenekleri("", "  ") == f"-c timezone={PG_OTURUM_DILIMI}"
    assert PG_OTURUM_DILIMI == "Europe/Istanbul"


def test_her_ikiz_options_ve_PGOPTIONS_u_yardimcidan_alir() -> None:
    dosyalar = _taranan_dosyalar()
    # Boş eşleşmede yeşil yanmasın: ikiz nüfusu 100'ün üstünde.
    assert len(dosyalar) > 100, len(dosyalar)
    bulgular: dict[str, list[str]] = {}
    for yol in dosyalar:
        ihlal, _ = _ihlaller(yol.read_text(encoding="utf-8"))
        if ihlal:
            bulgular[yol.relative_to(BACKEND).as_posix()] = ihlal
    assert not bulgular, (
        "H94: libpq `options`/`PGOPTIONS` `tests.pg_ikiz_yardimci.pg_secenekleri` "
        f"olmadan veriliyor — bu bağlantı sunucunun diliminde (CI'da UTC) açılır:\n{bulgular}"
    )


def test_duzeltilen_yedi_ikiz_yardimciyi_GERCEKTEN_kullanir() -> None:
    for ad in DUZELTILEN_IKIZLER:
        ihlal, gecen = _ihlaller((BACKEND / ad).read_text(encoding="utf-8"))
        assert not ihlal, (ad, ihlal)
        assert gecen >= 1, f"{ad}: kapıdan geçen yol yok — tarayıcı kör mü?"


def test_tarayici_pg_twins_envanterinin_tamamini_ve_uc_istisnayi_kapsar() -> None:
    dosyalar = set(_taranan_dosyalar())
    satirlar = [
        s.strip()
        for s in PG_TWINS_PIN.read_text(encoding="utf-8").splitlines()
        if s.strip()
    ]
    eksikler = [satir for satir in satirlar if (BACKEND / satir) not in dosyalar]
    assert not eksikler, f"Envanterde olup taranmayan dosyalar: {eksikler}"

    uc_istisna = (
        "test_postgresql_app_smoke.py",
        "tests/test_ci_playwright_hazirlik.py",
        "tests/test_company_id_default_contract.py",
    )
    for ad in uc_istisna:
        assert (BACKEND / ad) in dosyalar, f"{ad} taranan dosyalar kümesinde yok"


def test_tarayici_diskte_olmayan_envanter_satirinda_hata_verir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sahte_pin = tmp_path / "pg_twins.txt"
    sahte_pin.write_text("olmayan_ikiz_test.py\n", encoding="utf-8")
    monkeypatch.setattr(f"{__name__}.PG_TWINS_PIN", sahte_pin)
    with pytest.raises(AssertionError, match="olmayan_ikiz_test.py"):
        _taranan_dosyalar()


# --- Tarayıcının kendisi: her kaçak şekil KIRMIZI, doğru şekil YEŞİL. ---

def _tek(kaynak: str) -> tuple[list[str], int]:
    return _ihlaller(kaynak)


def test_tarayici_connect_args_kacagini_yakalar() -> None:
    ihlal, _ = _tek('create_engine(u, connect_args={"options": f"-csearch_path={s}"})')
    assert ihlal


def test_tarayici_url_query_kacagini_yakalar() -> None:
    ihlal, _ = _tek('make_url(u).update_query_dict({"options": "-csearch_path=x"})')
    assert ihlal
    ihlal, _ = _tek('u = "postgresql://h/db?options=-csearch_path%3Dx"')
    assert ihlal


def test_tarayici_PGOPTIONS_ezmesini_ve_silmesini_yakalar() -> None:
    assert _tek('env["PGOPTIONS"] = f"-csearch_path={s}"')[0]
    assert _tek('env.pop("PGOPTIONS", None)')[0]
    assert _tek('del env["PGOPTIONS"]')[0]
    assert _tek('env.update(PGOPTIONS="-csearch_path=x")')[0]
    assert _tek('psycopg.connect(dsn, options="-csearch_path=x")')[0]


def test_tarayici_yardimciyla_kurulani_gecirir() -> None:
    for kaynak in (
        'create_engine(u, connect_args={"options": pg_secenekleri(f"-csearch_path={s}")})',
        'make_url(u).update_query_dict({"options": pg_secenekleri("-csearch_path=x")})',
        'env["PGOPTIONS"] = pg_secenekleri(f"-csearch_path={s}")',
        'os.environ["PGOPTIONS"] = pg_secenekleri(os.environ.get("PGOPTIONS", ""))',
        'psycopg.connect(dsn, options=yardimci.pg_secenekleri("-csearch_path=x"))',
        '"""Belge: connect_args={"options": ...} ve ?options= anlatılır."""',
    ):
        ihlal, _ = _tek(kaynak)
        assert not ihlal, (kaynak, ihlal)

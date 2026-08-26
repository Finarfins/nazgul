"""Göç zinciri BİRLEŞME SONUCUNDA da tutarlı olmak zorunda.

Ölçülmüş kusur (#56 + #60, 2026-08-12): iki dal aynı revision id'sini
(``20260812_0056``) ilan etti. Bu İKİ BAŞ değil — alembic'in düpedüz
reddettiği bir ÇAKIŞMA. İkisi de CI'dan yeşil geçti, çünkü **tek başına her
dalda tek id ve tek baş var**. Çakışma yalnız BİRLEŞME SONUCUNDA görünüyor ve
birleşme sonucunu kimse denetlemiyordu.

`pull_request` olaylarında GitHub Actions çalışma ağacı zaten HEAD ile
base'in BİRLEŞMESİdir (``refs/pull/N/merge``); ayrıca ``alembic-chain`` işi
bunu AÇIKÇA yapıyor — checkout semantiğine güvenmek yerine base'i çekip
birleştiriyor.

**Neden alembic'in kendisi çağrılmıyor:** yinelenen id'de alembic ÇÖKER
(``CommandError``/``KeyError``). Çökmeden gelen kırmızı, kapının ateşlemesi
değildir; hangi id'lerin çakıştığını da söylemez.

AYRIŞTIRICI ARTIK BURADA DEĞİL — ``scripts/goc_zinciri.py``DE.
Bu dosya bir zamanlar KENDİ zincir yürütücüsünü taşıyordu; depoda ondan
BAŞKA bir tane daha vardı ve ikisi de ayrı ayrı yanlış ayrıştırdı (biri tek
satırlık ilanı, öteki demet ``down_revision``ı kaçırdı). Aynı sınıf kusurun
üçüncü kez yazılmaması için yürütücü tek bir modüle çekildi ve DEĞİŞMEZLER
onun içine gömüldü: ``zinciri_coz`` ya değişmezleri geçen bir zincir döner ya
da fırlatır. Bu dosya artık yalnız o aracı ÇAĞIRIR ve ihlali CI'ın okuyacağı
dille yazar. Aracın kendi kapıları ``tests/test_goc_zinciri.py`` içindedir.

Kapı DÜZELTİCİ DEĞİL: hiçbir şeyi yeniden numaralandırmaz. Çakışmayı ve
başları rapor eder; kararı insan verir.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

DEPO_KOKU = Path(__file__).resolve().parents[2]
ARAC = DEPO_KOKU / "scripts" / "goc_zinciri.py"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _arac():
    """TEK zincir yürütücüsü. İkinci bir ayrıştırıcı yazmak yasaktır."""
    if "goc_zinciri" in sys.modules:
        return sys.modules["goc_zinciri"]
    spec = importlib.util.spec_from_file_location("goc_zinciri", ARAC)
    assert spec and spec.loader, "scripts/goc_zinciri.py yüklenemedi"
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul          # dataclass + PEP 563 için ZORUNLU
    spec.loader.exec_module(modul)
    return modul


def test_hicbir_revision_id_iki_kez_ilan_edilmiyor() -> None:
    """#56 + #60'ın düştüğü kusur: aynı id'yi iki dosya ilan ediyor."""
    m = _arac()
    try:
        m.zinciri_coz()
    except m.YinelenenIdHatasi as red:
        raise AssertionError(
            "AYNI revision id'sini birden fazla göç dosyası ilan ediyor. "
            "Alembic bunu reddeder ve tek başına her dal yeşil geçtiği için "
            "ancak BİRLEŞME SONUCUNDA görülür.\n" + str(red)
            + "\nKapı bir şey YENİDEN NUMARALANDIRMAZ; hangi dosyanın "
              "taşınacağı insan kararıdır."
        ) from None
    except m.ZincirHatasi:
        # Başka bir değişmez kırık; ONU kendi kapısı bildirsin. Bu kapı
        # yalnız KENDİ iddiası için ateşler.
        pass


def test_zincirin_tek_basi_var() -> None:
    """Birleşme sonucu iki baş üretiyorsa alembic 'Multiple head revisions' der."""
    m = _arac()
    try:
        m.zinciri_coz()
    except m.BasSayisiHatasi as red:
        raise AssertionError(
            str(red) + "\nKapı hiçbir şeyi otomatik birleştirmez; hangi başın "
            "hangisinin üzerine alınacağı insan kararıdır."
        ) from None
    except m.YinelenenIdHatasi as red:
        # REDDEDİLDİ, "iki baş var" DEĞİL. Yinelenen id'de baş kümesi
        # TANIMSIZDIR; sayı uydurulmaz. Asıl tanı yukarıdaki kapıdadır.
        raise AssertionError(str(red)) from None
    except m.ZincirHatasi:
        pass


def test_zincirin_TAMAMI_bastan_ERISILEBILIR() -> None:
    """Tek baş YETMEZ: ayrık bir ada tek-baş kapısını GEÇER.

    Adanın en üst düğümü bir ebeveyn olarak gösteriliyorsa baş sayılmaz;
    baş sayısı 1 kalır ama o göçler `upgrade head` ile hiç koşmaz. Bu iddia
    aracın içinde zorlanıyor, burada ADIYLA bildiriliyor.
    """
    m = _arac()
    try:
        m.zinciri_coz()
    except m.ErisilebilirlikHatasi as red:
        raise AssertionError(str(red)) from None
    except m.ZincirHatasi:
        pass


def test_kapi_gercekten_dosya_tariyor() -> None:
    """Tarayıcı sessizce hiçbir şey bulmaz hale gelirse her kapı boşa geçer."""
    m = _arac()
    dosyalar = m.goc_dosyalari(VERSIONS)
    assert len(dosyalar) > 40, f"göç dosyası bulunamadı ya da çok az: {len(dosyalar)}"
    try:
        zincir = m.zinciri_coz()
    except m.SayimHatasi as red:
        raise AssertionError(str(red)) from None
    except m.KopukIsaretHatasi as red:
        raise AssertionError(str(red)) from None
    except m.ZincirHatasi:
        return
    assert zincir.dosya_sayisi == len(dosyalar)
    assert len(zincir.sira) == len(dosyalar)


def test_TEK_YURUTUCU_bu_dosyada_IKINCI_bir_ayristirici_YOK() -> None:
    """Bu görevin asıl iddiası: ayrıştırıcı BİR TANE.

    İki ajan iki ayrı gün kendi yürütücüsünü yazdı ve ikisi de yanlıştı.
    Kural yazıyla değil, ÖLÇÜMLE korunur. Ölçülen şey ÇIKARMA PRİMİTİFİDİR:
    bir göç dosyasından ilan edilen değeri almanın yolu ya `ast.literal_eval`
    ya bir DESENDİR. İkisi de bu dosyada geçmemeli. (Sentetik fikstür YAZMAK
    serbesttir; yasak olan OKUMAK.)
    """
    agac = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    cagrilar = {
        d.func.attr for d in ast.walk(agac)
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
    }
    assert "literal_eval" not in cagrilar, (
        "bu dosya modül sabiti ÇIKARIYOR; ayrıştırma aracın işi"
    )
    for desen_cagrisi in ("search", "match", "findall", "finditer", "compile"):
        assert desen_cagrisi not in cagrilar or "re" not in {
            a.name for d in ast.walk(agac) if isinstance(d, ast.Import)
            for a in d.names
        }, f"bu dosya desenle ayrıştırıyor: re.{desen_cagrisi}"
    # Ve zincir olguları YALNIZ araçtan geliyor.
    assert "_arac" in {
        d.name for d in ast.walk(agac) if isinstance(d, ast.FunctionDef)
    }


# ---------------------------------------------------------------------------
# MUTASYON — ESKİ algoritmanın kusuru, KANIT olarak saklanıyor.
#
# Aşağıdaki `_eski_algoritma_baslari`, düzeltmeden önceki hesabı BİLEREK
# yeniden üretir: revision başına ``setdefault`` ile YALNIZ ilk ilanı tutmak.
# Yeni araç bunu yapmaz — yinelenen id'de hesabı REDDEDER. Kusurun ölçülebilir
# kalması için karşı örnek burada duruyor.
# ---------------------------------------------------------------------------

def _goc_yaz(kok: Path, dosya: str, rev: str, asagi) -> None:
    kok.mkdir(parents=True, exist_ok=True)
    deger = "None" if asagi is None else repr(asagi)
    (kok / dosya).write_text(
        f'revision = {rev!r}\ndown_revision = {deger}\n', encoding="utf-8"
    )


def _celiskili_yinelenme(kok: Path, ilk: str, ikinci: str) -> None:
    """``REV_DUP`` bir dosyada ``BASE_A``'nın, ötekinde ``BASE_B``'nin çocuğu."""
    _goc_yaz(kok, "0001_base_a.py", "BASE_A", None)
    _goc_yaz(kok, "0002_base_b.py", "BASE_B", None)
    _goc_yaz(kok, ilk, "REV_DUP", "BASE_A")
    _goc_yaz(kok, ikinci, "REV_DUP", "BASE_B")


def _eski_algoritma_baslari(versions: Path) -> list[str]:
    """Düzeltmeden ÖNCEKİ hesap — karşı örnek olarak korunuyor."""
    m = _arac()
    alt: dict[str, object] = {}
    for goc in m.gocleri_oku(versions):
        alt.setdefault(goc.revision, goc.ebeveynler)
    isaret = {e for v in alt.values() for e in v}
    return sorted(set(alt) - isaret)


def test_MUTASYON_eski_algoritma_dosya_adi_sirasina_BAGIMLIYDI(tmp_path: Path) -> None:
    """Kusurun kendisi: aynı içerik, iki farklı adlandırma, İKİ FARKLI baş kümesi."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _celiskili_yinelenme(a, "0003_dup_x.py", "0004_dup_y.py")
    _celiskili_yinelenme(b, "0004_dup_y.py", "0003_dup_x.py")

    eski_a = _eski_algoritma_baslari(a)
    eski_b = _eski_algoritma_baslari(b)
    assert eski_a != eski_b, (
        "mutasyon kurulamadı: eski algoritma bu girdide zaten sıraya bağlı değil"
    )
    assert eski_a == ["BASE_B", "REV_DUP"], eski_a
    assert eski_b == ["BASE_A", "REV_DUP"], eski_b
    print(f"MUTATION_RED defect2/order-sensitivity: eski algoritma {eski_a} vs {eski_b}")


def test_MUTASYON_yinelenen_id_de_bas_kumesi_HESAPLANMAZ(tmp_path: Path) -> None:
    """YÖN 1 — yinelenen id: hesap REDDEDİLİR, sıraya bağlı cevap üretilmez."""
    m = _arac()
    kok = tmp_path / "dup"
    _celiskili_yinelenme(kok, "0003_dup_x.py", "0004_dup_y.py")
    with pytest.raises(m.YinelenenIdHatasi) as red:
        m.zinciri_coz(kok)
    mesaj = str(red.value)
    assert "hesaplanmadı" in mesaj.lower(), mesaj
    assert "0003_dup_x.py" in mesaj and "0004_dup_y.py" in mesaj, mesaj
    print(f"MUTATION_RED defect2/refusal: {mesaj.splitlines()[0]}")


def test_MUTASYON_reddetme_dosya_adi_sirasindan_BAGIMSIZ(tmp_path: Path) -> None:
    """YÖN 2 — aynı içerik, ters adlandırma: aynı red, aynı dosya listesi."""
    m = _arac()
    a = tmp_path / "a"
    b = tmp_path / "b"
    _celiskili_yinelenme(a, "0003_dup_x.py", "0004_dup_y.py")
    _celiskili_yinelenme(b, "0004_dup_y.py", "0003_dup_x.py")

    kirmizilar = []
    for kok in (a, b):
        with pytest.raises(m.YinelenenIdHatasi) as red:
            m.zinciri_coz(kok)
        kirmizilar.append(str(red.value))
    assert kirmizilar[0] == kirmizilar[1], kirmizilar
    assert "0003_dup_x.py, 0004_dup_y.py" in kirmizilar[0], kirmizilar[0]
    print("MUTATION_RED defect2/order-independent refusal: aynı mesaj, iki adlandırma")


def test_CONTROL_temiz_zincirde_bas_hesaplanir(tmp_path: Path) -> None:
    """CONTROL — yinelenme yoksa hesap yapılır ve tek baş bulunur."""
    m = _arac()
    kok = tmp_path / "temiz"
    _goc_yaz(kok, "0001_base.py", "BASE", None)
    _goc_yaz(kok, "0002_orta.py", "ORTA", "BASE")
    _goc_yaz(kok, "0003_bas.py", "BAS", "ORTA")
    zincir = m.zinciri_coz(kok)
    assert zincir.bas == "BAS", zincir.bas
    print(f"GATE_CONTROL defect2: temiz zincir -> tek baş {zincir.bas}")

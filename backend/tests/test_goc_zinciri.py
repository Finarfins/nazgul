"""`scripts/goc_zinciri.py` — TEK zincir yürütücüsünün kendi kapıları.

NİYE BU DOSYA VAR: bu zinciri iki ayrı gün iki ayrı ajan yeniden yazdı ve
ikisi de yanlış ayrıştırdı — biri tek satırlık ilanı kaçırdı ve sahte bir kök
uydurdu, öteki demet ``down_revision``ı kaçırdı sonra fazla düzeltip her
revizyonu baş gösterdi. İkisini de MUTASYON yakalamadı; ikisini de TAMLIK
DEĞİŞMEZİ yakaladı. Bu dosya o değişmezleri aracın içine çivilenmiş hâlde
sınar ve her birini İKİ YÖNDE mutasyona uğratır.

Ayrıştırmanın sınandığı beş biçim — beşi de bu depoda GERÇEKTEN var ya da
gerçekten olabilir:
  1. tek satırlık ilan (``revision="X"; down_revision="Y"``) — 2 dosya
  2. demet ``down_revision`` — birleşme revizyonu ``20260719_0013``
  3. gerçek birleşme, İKİ ebeveyniyle
  4. gerçek çatallanma noktası (``20260716_0008``)
  5. dosya adı ilan edilen id ile ÇELİŞEN revizyon (sentetik)
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEPO_KOKU = Path(__file__).resolve().parents[2]
ARAC = DEPO_KOKU / "scripts" / "goc_zinciri.py"
VERSIONS = DEPO_KOKU / "backend" / "alembic" / "versions"


def _arac():
    """Aracı modül olarak yükler — kapı ile üretim AYNI kodu okusun."""
    if "goc_zinciri" in sys.modules:
        return sys.modules["goc_zinciri"]
    spec = importlib.util.spec_from_file_location("goc_zinciri", ARAC)
    assert spec and spec.loader, "scripts/goc_zinciri.py yüklenemedi"
    modul = importlib.util.module_from_spec(spec)
    # `sys.modules`e KAYIT ZORUNLU, süsleme değil: modül `@dataclass` ile
    # `from __future__ import annotations` kullanıyor ve dataclasses, dizge
    # anotasyonlarında `ClassVar`/`InitVar` ayıklaması için
    # `sys.modules[cls.__module__]`a bakar. Kayıtsız yüklemede orası None
    # döner ve HER alan `AttributeError` verir. importlib'in belgelenmiş
    # sırası da budur: kaydet, sonra çalıştır.
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


def _yaz(kok: Path, dosya: str, govde: str) -> None:
    kok.mkdir(parents=True, exist_ok=True)
    (kok / dosya).write_text(govde, encoding="utf-8")


def _goc(kok: Path, dosya: str, rev: str, asagi=None, *, tek_satir=False,
         annassign=False) -> None:
    """Bir göç dosyası yazar; ilanın BİÇİMİ seçilebilir."""
    deger = "None" if asagi is None else repr(asagi)
    if tek_satir:
        govde = f'revision={rev!r}; down_revision={deger}; branch_labels=None\n'
    elif annassign:
        govde = (f'from typing import Union\n'
                 f'revision: str = {rev!r}\n'
                 f'down_revision: Union[str, None] = {deger}\n')
    else:
        govde = f'revision = {rev!r}\ndown_revision = {deger}\n'
    _yaz(kok, dosya, govde)


def _saglam_zincir(kok: Path) -> None:
    """A <- B <- C; üç ilan BİÇİMİ de bilerek karıştırılmış."""
    _goc(kok, "0001_a.py", "A", None)
    _goc(kok, "0002_b.py", "B", "A", tek_satir=True)
    _goc(kok, "0003_c.py", "C", "B", annassign=True)


# ===========================================================================
# 1. AYRIŞTIRMA — beş biçimin her biri için bir test
# ===========================================================================

def test_AYRISTIRMA_tek_satirlik_ilan_GORULUYOR(tmp_path: Path) -> None:
    """KUSUR 1: `^revision` deseni bunu göremedi ve zinciri KESTİ."""
    m = _arac()
    _goc(tmp_path, "0001_a.py", "A", None)
    _goc(tmp_path, "0002_b.py", "B", "A", tek_satir=True)
    zincir = m.zinciri_coz(tmp_path)
    assert zincir.bas == "B"
    assert zincir.gocler["B"].ebeveynler == ("A",), (
        "tek satırlık ilan görülmedi; zincir kesilir ve B sahte KÖK olur"
    )
    assert zincir.kokler == ["A"], zincir.kokler


def test_AYRISTIRMA_demet_down_revision_GORULUYOR(tmp_path: Path) -> None:
    """KUSUR 2: demet kaçırılınca birleşme ya bölünür ya her düğüm baş olur."""
    m = _arac()
    _goc(tmp_path, "0001_a.py", "A", None)
    _goc(tmp_path, "0002_b.py", "B", "A")
    _goc(tmp_path, "0003_c.py", "C", "A")
    _goc(tmp_path, "0004_m.py", "M", ("B", "C"), annassign=True)
    zincir = m.zinciri_coz(tmp_path)
    assert zincir.bas == "M"
    assert zincir.gocler["M"].ebeveynler == ("B", "C"), zincir.gocler["M"]
    assert zincir.birlesmeler == {"M": ("B", "C")}
    assert len(zincir.sira) == 4, "demet kaçırılsa erişilebilirlik düşerdi"


def test_AYRISTIRMA_GERCEK_birlesme_revizyonu_IKI_EBEVEYNIYLE() -> None:
    """3. biçim: deponun GERÇEK birleşmesi, sentetik değil."""
    zincir = _arac().zinciri_coz()
    assert zincir.birlesmeler == {
        "20260719_0013": ("20260716_0009", "20260718_0012")
    }, zincir.birlesmeler
    for ebeveyn in ("20260716_0009", "20260718_0012"):
        assert zincir.atasi_mi(ebeveyn, "20260719_0013"), ebeveyn


def test_AYRISTIRMA_GERCEK_catallanma_noktasi() -> None:
    """4. biçim: deponun GERÇEK dallanma noktası ve İKİ çocuğu."""
    zincir = _arac().zinciri_coz()
    assert zincir.catallanmalar == {
        "20260716_0008": ["20260716_0009", "20260717_0009"]
    }, zincir.catallanmalar


def test_DOSYA_ADI_ilan_edilen_id_ile_CELISSE_ILAN_KAZANIR(tmp_path: Path) -> None:
    """5. biçim: kimlik dosya ADINDA değil, dosyanın İÇİNDE ilan edilendir.

    Dosya adına bakan bir araç burada `ZZZZ`yi düğüm sanar ve `B`nin
    ebeveynini bulamaz. Alembic de adı değil ilanı okur.
    """
    m = _arac()
    _goc(tmp_path, "9999_yanlis_ad.py", "A", None)      # ad "A" DEMİYOR
    _goc(tmp_path, "0002_b.py", "B", "A")
    zincir = m.zinciri_coz(tmp_path)
    assert zincir.bas == "B"
    assert set(zincir.gocler) == {"A", "B"}
    assert zincir.gocler["A"].dosya == "9999_yanlis_ad.py"
    assert zincir.gocler["A"].ad_kimlikle_uyusuyor is False
    assert zincir.gocler["B"].ad_kimlikle_uyusuyor is False   # "0002_b" != "B"
    assert zincir.atasi_mi("A", "B"), "ilan edilen id ile bağ kurulamadı"


def test_SIRA_dosya_adindan_BAGIMSIZ(tmp_path: Path) -> None:
    """SAYISAL SONEK KULLANILMIYOR — kanıt, iddia değil.

    Deponun GERÇEK göçleri, adları TAMAMEN karıştırılmış (ve sıralaması
    tersine dönmüş) hâlde kopyalanır. Sırayı ya da ebeveynliği dosya adından
    türeten bir araç burada başka bir cevap verir; graftan türeten aynı
    cevabı verir.

    Bu depoda soneğe güvenmek ayrıca ÖLÇÜLEBİLİR biçimde yanlıştır: `0009`
    soneği İKİ dosyada var ve o ikisi KARDEŞTİR.
    """
    m = _arac()
    gercek = m.zinciri_coz()

    karisik = tmp_path / "karisik"
    karisik.mkdir()
    dosyalar = m.goc_dosyalari()
    for i, yol in enumerate(reversed(dosyalar)):
        # Ad hiçbir bilgi TAŞIMIYOR: ne tarih, ne sonek, ne konu.
        shutil.copy2(yol, karisik / f"{i:04d}_zzz.py")
    karisik_zincir = m.zinciri_coz(karisik)

    assert karisik_zincir.bas == gercek.bas
    assert karisik_zincir.sira == gercek.sira, (
        "zincir sırası dosya adlarına BAĞLI; sayısal sonek bir yerde okunuyor"
    )
    assert karisik_zincir.kokler == gercek.kokler
    assert karisik_zincir.birlesmeler == gercek.birlesmeler


def test_ARACIN_KAYNAGINDA_sayisal_sonek_ARANIYOR_ve_YOK() -> None:
    """5. maddenin makine kontrolü: sonek deseni kaynakta GEÇMİYOR.

    İnsan beyanı kanıt değildir. Araç kaynağında `_0044` gibi bir sonek
    desenini ya da dosya adından sayı çeken bir çağrıyı arıyoruz.
    """
    import re

    kaynak = ARAC.read_text(encoding="utf-8")
    # Yorum ve docstring'ler soneği ANLATIYOR; ölçülen şey KOD.
    kod_satirlari = [
        s for s in kaynak.splitlines()
        if s.strip() and not s.strip().startswith("#")
    ]
    kod = "\n".join(kod_satirlari)
    supheli = [
        r"_\(\?P<sonek>",
        r"\[-1\]\s*\.\s*split",
        r"split\(['\"]_['\"]\)",
        r"\\d\{4\}",
        r"int\(\s*\w+\.name",
        r"int\(\s*\w+\.stem",
        r"lstrip\(['\"]0",
    ]
    bulunan = [d for d in supheli if re.search(d, kod)]
    assert not bulunan, f"kaynakta dosya adından sayı türeten desen var: {bulunan}"


# ===========================================================================
# 2. DEĞİŞMEZLER ÇAĞIRANA BIRAKILMIYOR
# ===========================================================================

def test_DEGISMEZLER_cozumun_ICINDE_uyari_DEGIL(tmp_path: Path) -> None:
    """`zinciri_coz` ya GEÇERLİ zincir döner ya FIRLATIR — ara durum yok.

    İki aracın da yanlış cevap verebilmesinin sebebi, kontrolün ÇAĞIRANA
    bırakılmasıydı: "başlar şunlar, sen bak" diyen bir dönüş, bakılmadığında
    sessizce yanlış olur.
    """
    m = _arac()
    _goc(tmp_path, "0001_a.py", "A", None)
    _goc(tmp_path, "0002_b.py", "B", None)          # İKİ baş
    with pytest.raises(m.ZincirHatasi) as red:
        m.zinciri_coz(tmp_path)
    assert red.value.ihlal == "TEK_BAS"
    # Ve "uyarı" diye bir kanal YOK.
    assert not hasattr(m, "uyarilar")
    assert not hasattr(m.Zincir, "uyarilar")


# ===========================================================================
# 3. MUTASYON — HER DEĞİŞMEZ İÇİN İKİ YÖN
#
# Her çift: KIR -> hangi değişmezin kırıldığını ADIYLA al; ONAR -> geç.
# `ihlal` alanı üzerinde iddia edildiği için "bir şey patladı" ile "BU
# değişmez patladı" birbirine karışamaz.
# ===========================================================================

def test_MUTASYON_TEK_BAS_kir_ve_onar(tmp_path: Path) -> None:
    """DEĞİŞMEZ 1: tam bir baş."""
    m = _arac()
    kok = tmp_path / "k"
    _saglam_zincir(kok)

    # KIR: ikinci bir baş ekle (hiç kimsenin göstermediği bir yaprak).
    _goc(kok, "0004_d.py", "D", "A")
    with pytest.raises(m.ZincirHatasi) as red:
        m.zinciri_coz(kok)
    assert red.value.ihlal == "TEK_BAS", red.value.ihlal
    assert "2 başı var" in str(red.value), str(red.value)
    assert type(red.value) is m.BasSayisiHatasi
    print(f"MUTASYON_KIRMIZI TEK_BAS: {str(red.value).splitlines()[0]}")

    # ONAR
    (kok / "0004_d.py").unlink()
    zincir = m.zinciri_coz(kok)
    assert zincir.bas == "C"
    print(f"MUTASYON_YESIL   TEK_BAS: baş={zincir.bas}")


def test_MUTASYON_TEK_BAS_SIFIR_bas_da_kirmizi(tmp_path: Path) -> None:
    """DEĞİŞMEZ 1, öteki uç: döngüde SIFIR baş kalır — o da ihlaldir."""
    m = _arac()
    kok = tmp_path / "d"
    _goc(kok, "0001_a.py", "A", "C")
    _goc(kok, "0002_b.py", "B", "A")
    _goc(kok, "0003_c.py", "C", "B")
    with pytest.raises(m.ZincirHatasi) as red:
        m.zinciri_coz(kok)
    assert red.value.ihlal == "TEK_BAS", red.value.ihlal
    assert "0 başı var" in str(red.value), str(red.value)
    print(f"MUTASYON_KIRMIZI TEK_BAS/döngü: {str(red.value).splitlines()[0]}")

    (kok / "0001_a.py").write_text(
        "revision = 'A'\ndown_revision = None\n", encoding="utf-8")
    assert m.zinciri_coz(kok).bas == "C"
    print("MUTASYON_YESIL   TEK_BAS/döngü: baş=C")


def test_MUTASYON_ERISILEBILIRLIK_kir_ve_onar(tmp_path: Path) -> None:
    """DEĞİŞMEZ 2: baştan HER revizyona erişilebilirlik.

    KIRMA BİÇİMİ ÖNEMLİ: ayrık ada TEK BAŞ kapısını GEÇER. Adanın en üst
    düğümü (`Y`) bir ebeveyn olarak gösterildiği için baş sayılmaz; yani
    baş sayısı hâlâ 1'dir ama iki düğüm zincirin dışındadır. Tek-baş
    değişmezi bunu TEK BAŞINA görmez — erişilebilirlik değişmezinin ayrı
    var olma sebebi budur.
    """
    m = _arac()
    kok = tmp_path / "k"
    _saglam_zincir(kok)

    # AYRIK ADA: X ve Y birbirini gösterir. İkisi de bir ebeveyn olarak
    # GÖSTERİLDİĞİ için hiçbiri baş sayılmaz — baş kümesi hâlâ tam olarak
    # {C}. Yani TEK_BAS kapısı YEŞİL kalır ve ada yalnız erişilebilirlikle
    # görülür.
    _goc(kok, "0010_x.py", "X", "Y")
    _goc(kok, "0011_y.py", "Y", "X")

    with pytest.raises(m.ZincirHatasi) as red:
        m.zinciri_coz(kok)
    assert red.value.ihlal == "ERISILEBILIRLIK", red.value.ihlal
    assert type(red.value) is m.ErisilebilirlikHatasi
    assert "X" in str(red.value) and "Y" in str(red.value), str(red.value)
    print(f"MUTASYON_KIRMIZI ERISILEBILIRLIK: {str(red.value).splitlines()[0]}")

    # ONAR: adayı kaldır.
    (kok / "0010_x.py").unlink()
    (kok / "0011_y.py").unlink()
    zincir = m.zinciri_coz(kok)
    assert set(zincir.sira) == {"A", "B", "C"}
    print(f"MUTASYON_YESIL   ERISILEBILIRLIK: {len(zincir.sira)} düğüm erişildi")


def test_MUTASYON_SAYIM_kir_ve_onar(tmp_path: Path) -> None:
    """DEĞİŞMEZ 3: yürünen düğüm == göç DOSYASI sayısı.

    KIRMA BİÇİMİ: `revision` ilan ETMEYEN bir dosya. Böyle bir dosya
    `gocler`e hiç girmez; grafın kendi içinden hesaplanan her sayı onunla
    TUTARLI olur ve tarama sessizce daralır. Ölçüt bu yüzden AYRI zeminden —
    dosya sayısından — gelir.
    """
    m = _arac()
    kok = tmp_path / "k"
    _saglam_zincir(kok)

    _yaz(kok, "0009_ilansiz.py", '"""revision ilan etmiyor."""\nsuruklenen = 1\n')
    with pytest.raises(m.ZincirHatasi) as red:
        m.zinciri_coz(kok)
    assert red.value.ihlal == "SAYIM", red.value.ihlal
    assert type(red.value) is m.SayimHatasi
    assert "3, göç DOSYASI sayısı 4" in str(red.value), str(red.value)
    print(f"MUTASYON_KIRMIZI SAYIM: {str(red.value).splitlines()[0]}")

    (kok / "0009_ilansiz.py").unlink()
    zincir = m.zinciri_coz(kok)
    assert len(zincir.sira) == zincir.dosya_sayisi == 3
    print(f"MUTASYON_YESIL   SAYIM: {len(zincir.sira)}/{zincir.dosya_sayisi}")


def test_MUTASYON_YINELENEN_ID_kir_ve_onar(tmp_path: Path) -> None:
    """ÖN KOŞUL: aynı id'yi iki dosya ilan ederse girdi GRAF DEĞİLDİR."""
    m = _arac()
    kok = tmp_path / "k"
    _saglam_zincir(kok)

    _goc(kok, "0004_c_kopya.py", "C", "A")      # C ikinci kez, ÇELİŞKİLİ ebeveyn
    with pytest.raises(m.ZincirHatasi) as red:
        m.zinciri_coz(kok)
    assert red.value.ihlal == "YINELENEN_ID", red.value.ihlal
    assert "0003_c.py" in str(red.value) and "0004_c_kopya.py" in str(red.value)
    print(f"MUTASYON_KIRMIZI YINELENEN_ID: {str(red.value).splitlines()[0]}")

    (kok / "0004_c_kopya.py").unlink()
    assert m.zinciri_coz(kok).bas == "C"
    print("MUTASYON_YESIL   YINELENEN_ID: baş=C")


def test_MUTASYON_KOPUK_ISARET_kir_ve_onar(tmp_path: Path) -> None:
    """ÖN KOŞUL: `down_revision` var olmayan bir revizyonu gösteremez."""
    m = _arac()
    kok = tmp_path / "k"
    _saglam_zincir(kok)

    _goc(kok, "0002_b.py", "B", "YOK_BOYLE_BIR_REV", tek_satir=True)
    with pytest.raises(m.ZincirHatasi) as red:
        m.zinciri_coz(kok)
    assert red.value.ihlal == "KOPUK_ISARET", red.value.ihlal
    assert "B -> YOK_BOYLE_BIR_REV" in str(red.value), str(red.value)
    print(f"MUTASYON_KIRMIZI KOPUK_ISARET: {str(red.value).splitlines()[0]}")

    _goc(kok, "0002_b.py", "B", "A", tek_satir=True)
    assert m.zinciri_coz(kok).bas == "C"
    print("MUTASYON_YESIL   KOPUK_ISARET: baş=C")


def test_MUTASYON_her_ihlal_KENDI_adini_soyluyor(tmp_path: Path) -> None:
    """KARŞI YÖN: beş kırma biçimi BEŞ FARKLI ad üretmeli.

    Hepsini tek bir "zincir bozuk" ile bildiren bir araç da yukarıdaki beş
    testi geçerdi ve hangi değişmezin kırıldığını söyleyemezdi.
    """
    m = _arac()
    adlar = []

    a = tmp_path / "a"; _saglam_zincir(a); _goc(a, "0004_d.py", "D", "A")
    b = tmp_path / "b"; _saglam_zincir(b)
    _goc(b, "0010_x.py", "X", "Y"); _goc(b, "0011_y.py", "Y", "X")
    c = tmp_path / "c"; _saglam_zincir(c)
    _yaz(c, "0009_ilansiz.py", "x = 1\n")
    d = tmp_path / "d"; _saglam_zincir(d); _goc(d, "0004_c2.py", "C", "A")
    e = tmp_path / "e"; _saglam_zincir(e); _goc(e, "0002_b.py", "B", "HAYALET")

    for kok in (a, b, c, d, e):
        with pytest.raises(m.ZincirHatasi) as red:
            m.zinciri_coz(kok)
        adlar.append(red.value.ihlal)

    assert adlar == ["TEK_BAS", "ERISILEBILIRLIK", "SAYIM",
                     "YINELENEN_ID", "KOPUK_ISARET"], adlar
    assert len(set(adlar)) == 5, adlar
    print(f"MUTASYON_AYIRT_ETME: {adlar}")


def test_CONTROL_saglam_zincir_YESIL(tmp_path: Path) -> None:
    """CONTROL: her şeyi reddeden bir araç yukarıdaki her çifti geçerdi."""
    m = _arac()
    kok = tmp_path / "temiz"
    _saglam_zincir(kok)
    zincir = m.zinciri_coz(kok)
    assert zincir.bas == "C"
    assert zincir.sira == ("C", "B", "A")
    assert zincir.dosya_sayisi == 3


# ===========================================================================
# 4. GERÇEKLİĞE KARŞI — bilinen zincirin YENİDEN ÜRETİLMESİ
# ===========================================================================

def test_GERCEK_zincir_bilinen_hali_YENIDEN_URETIYOR() -> None:
    """Baştan yürüyüş, bilinen zinciri harfi harfine vermeli."""
    m = _arac()
    zincir = m.zinciri_coz()

    # BAŞ, GÖÇ EKLENDİKÇE ELLE GÜNCELLENİR — bilerek. Bu satır her yeni göçte
    # KIRMIZI olur ve yeni başın BİLİNÇLİ bir karar olmasını zorlar; başı
    # zincirden okuyan bir kapı, yanlış yere eklenmiş bir göçü de sessizce
    # onaylardı.
    assert zincir.bas == "20260827_0062", zincir.bas
    assert zincir.kokler == ["20260712_0000"], zincir.kokler
    assert len(zincir.sira) == zincir.dosya_sayisi, (
        f"{len(zincir.sira)} yürüdü / {zincir.dosya_sayisi} dosya"
    )
    assert zincir.dosya_sayisi == len(list(VERSIONS.glob("*.py"))) - (
        1 if (VERSIONS / "__init__.py").exists() else 0
    )
    # Her düğüm baştan erişilebilir (değişmez zaten zorluyor; burada AÇIKÇA).
    assert set(zincir.sira) == set(zincir.gocler)


def test_GERCEK_20260807_0044_20260812_0057_nin_ATASI() -> None:
    """Bilinen ata ilişkisi — ve sayısal sonek bunu SÖYLEYEMEZ.

    `0044 < 0057` karşılaştırması burada tesadüfen doğru cevabı verir; ama
    aynı depoda `0009` soneği iki KARDEŞTE birden geçiyor, yani sonek bir
    ATA ilişkisi değildir. İlişki graftan okunuyor.
    """
    zincir = _arac().zinciri_coz()
    assert zincir.atasi_mi("20260807_0044", "20260812_0057"), (
        "20260807_0044, 20260812_0057'nin atası olarak bulunamadı"
    )
    atalar = zincir.atalar("20260812_0057")
    assert "20260807_0044" in atalar
    assert "20260827_0062" not in atalar, "baş, kendi atasının atası olamaz"


def test_GERCEK_20260719_0013_BIRLESMESI_IKI_EBEVEYNIYLE() -> None:
    """Bilinen birleşme — iki ebeveyni de zincirde ve ikisi de ATA."""
    zincir = _arac().zinciri_coz()
    goc = zincir.gocler["20260719_0013"]
    assert goc.ebeveynler == ("20260716_0009", "20260718_0012"), goc.ebeveynler
    assert len(goc.ebeveynler) == 2
    for ebeveyn in goc.ebeveynler:
        assert ebeveyn in zincir.gocler
        assert zincir.atasi_mi(ebeveyn, "20260719_0013")
    # Birleşme GERÇEKTEN iki KOLU birleştiriyor: kollar birbirinin atası DEĞİL.
    sol, sag = goc.ebeveynler
    assert not zincir.atasi_mi(sol, sag) and not zincir.atasi_mi(sag, sol), (
        "iki ebeveyn aynı doğru üzerinde; bu bir birleşme değil düz zincir"
    )
    # Ortak ata: çatallanma noktası.
    assert "20260716_0008" in zincir.atalar(sol) & zincir.atalar(sag)


def test_GERCEK_SONEK_bu_depoda_YETMEZ_olcum() -> None:
    """5. maddenin GEREKÇESİ, ölçüm olarak: `0009` soneği İKİ dosyada.

    Ve o iki dosya KARDEŞ — biri ötekinin atası değil. Soneği sıra sanan
    bir araç bu depoda YANLIŞ cevap verir; iddia değil, ölçüm.
    """
    m = _arac()
    zincir = m.zinciri_coz()
    sonekli = [g.dosya for g in zincir.gocler.values()
               if g.dosya.split("_")[1] == "0009"]
    assert len(sonekli) == 2, sonekli
    assert sorted(sonekli) == ["20260716_0009_machine_idempotency.py",
                               "20260717_0009_work_orders.py"], sonekli
    a, b = "20260716_0009", "20260717_0009"
    assert not zincir.atasi_mi(a, b) and not zincir.atasi_mi(b, a), (
        "aynı soneği taşıyan iki revizyon KARDEŞ olmalı"
    )
    assert zincir.gocler[a].ebeveynler == zincir.gocler[b].ebeveynler == (
        "20260716_0008",
    )


# ===========================================================================
# 5. CLI — aracın kendi çağrılma yolu, İKİ ÇIKIŞ YÖNÜ
# ===========================================================================

def _cli(*argumanlar: str):
    return subprocess.run(
        [sys.executable, str(ARAC), *argumanlar],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(DEPO_KOKU),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def test_CLI_kontrol_gercek_depoda_CIKIS_0() -> None:
    sonuc = _cli("--kontrol")
    assert sonuc.returncode == 0, sonuc.stderr
    assert "TEK BAŞ VAR" in sonuc.stdout
    assert "HEPSİ ERİŞİLEBİLİR" in sonuc.stdout
    assert "SAYIM TUTUYOR" in sonuc.stdout
    assert "20260827_0062" in sonuc.stdout


def test_CLI_kontrol_BOZUK_zincirde_CIKIS_1_ve_IHLALI_ADLANDIRIR(tmp_path) -> None:
    kok = tmp_path / "iki_bas"
    _goc(kok, "0001_a.py", "A", None)
    _goc(kok, "0002_b.py", "B", None)
    sonuc = _cli("--kontrol", "--versions", str(kok))
    assert sonuc.returncode == 1, sonuc.stdout
    assert "::error::" in sonuc.stderr
    assert "[TEK_BAS]" in sonuc.stderr, sonuc.stderr


def test_CLI_ciplak_cagri_zinciri_BASTAN_KOKE_basar() -> None:
    sonuc = _cli()
    assert sonuc.returncode == 0, sonuc.stderr
    satirlar = [s for s in sonuc.stdout.splitlines() if s.strip()]
    zincir = _arac().zinciri_coz()
    assert len(satirlar) == len(zincir.sira)
    assert satirlar[0].startswith(zincir.bas)
    assert satirlar[-1].endswith("(kök)")

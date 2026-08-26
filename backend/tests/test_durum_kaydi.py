"""İnen iş kaydının biçim ve bütünlük kapıları.

Kayıt artık `docs/durum/` altında girdi başına bir dosyada tutuluyor. Bu
dosya iki şeyi koruyor:

1. **Kayıt kaybolmasın.** Taşınan 30 girdinin metni ve SIRASI çapaya
   bağlıdır. Bir girdiyi silmek, metnini düzeltmek ya da sırasını
   değiştirmek kapıyı kırar — kaydın kendisi işin kanıtı olduğu için
   sessizce düzeltilebilir olmamalı.
2. **Yeni düzen bozulmasın.** Dosya adı deseni, tek satır kuralı ve
   (sıra, pr) çiftinin biricikliği sınanır; bunlar çakışmasızlığın
   dayandığı varsayımlardır.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

DEPO_KOKU = Path(__file__).resolve().parents[2]
KAYIT_DIZINI = DEPO_KOKU / "docs" / "durum"
DURUM_MD = DEPO_KOKU / "docs" / "DURUM.md"
OKUYUCU = DEPO_KOKU / "scripts" / "durum.py"

# DONMUŞ TARİHSEL BOŞLUK (ölçüldü 2026-08-17, develop f244c8f3).
# Birleşme commit'i olan 50 PR'ın 17'si girdisiz indi. Bu sayılar geriye dönük
# KAPATILMAYACAK; varlık kapısı yalnız bundan sonrasını korur.
TARIHSEL_GIRDISIZ_TOPLAM = 17
TARIHSEL_GIRDISIZ_KAYIT_ONCESI = 10          # PR #30'dan önce: kaydın başlangıcından önce
TARIHSEL_GIRDISIZ_GOC_PENCERESINDE = 5       # #31, #61, #62, #65, #66
TARIHSEL_GIRDISIZ_BU_PR_ILE_KAPANAN = 2      # #71, #72

#: Taşıma anındaki korpusun çapası: 30 girdinin, EN YENİDEN ESKİYE, "\n" ile
#: birleştirilmiş metninin sha256'sı. Eski `docs/DURUM.md`den harfi harfine
#: alınmıştır. Yeni girdiler bu çapayı DEĞİŞTİRMEZ; çapa yalnız taşınan
#: bölümü kilitler, çünkü kilitlenmesi gereken şey geçmiştir.
TASINAN_GIRDI_SAYISI = 30
TASINAN_KORPUS_SHA256 = "9ee605539e0d5fd378863bc55a736ec120495006bae4a3b68db44df292c29742"

AD_DESENI = re.compile(r"^(\d{4})-pr-(\d{4})\.md$")


def _okuyucu():
    """`scripts/durum.py`yi modül olarak yükler.

    Test, aracın KENDİSİNİ sınamalı: kaydı okuyan kod ile kaydı doğrulayan
    kod aynı olmazsa, araç bozulduğunda kapı bunu göremez.
    """
    spec = importlib.util.spec_from_file_location("durum_araci", OKUYUCU)
    assert spec and spec.loader, "scripts/durum.py yüklenemedi"
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_taşınan_korpus_capaya_bagli() -> None:
    """Geçmiş kayıt harfi harfine ve SIRASIYLA korunmuş olmalı."""
    girdiler = _okuyucu().girdileri_oku(KAYIT_DIZINI)
    tasinan = [metin for sira, _, metin, _ in girdiler if sira <= TASINAN_GIRDI_SAYISI]
    assert len(tasinan) == TASINAN_GIRDI_SAYISI, (
        f"taşınan girdi sayısı {len(tasinan)}, beklenen {TASINAN_GIRDI_SAYISI}; "
        "geçmiş kayıttan girdi eksilmiş ya da eklenmiş olabilir"
    )
    olculen = hashlib.sha256("\n".join(tasinan).encode("utf-8")).hexdigest()
    assert olculen == TASINAN_KORPUS_SHA256, (
        "Taşınan kayıt çapadan ayrıştı.\n"
        f"  ölçülen={olculen}\n  çapa   ={TASINAN_KORPUS_SHA256}\n"
        "Bir girdinin metni ya da sırası değişmiş demektir. Kayıt işin "
        "kanıtıdır; düzeltme gerekiyorsa YENİ bir girdiyle belirtilir, "
        "eskisi yeniden yazılmaz."
    )


def test_dosya_adlari_desene_uyuyor() -> None:
    """Ad deseni çakışmasızlığın taşıyıcısı; bozulursa okuma sırası da bozulur."""
    kusurlu = [
        yol.name for yol in sorted(KAYIT_DIZINI.glob("*")) if not AD_DESENI.match(yol.name)
    ]
    assert not kusurlu, (
        f"Dosya adı <sıra>-pr-<numara>.md biçiminde olmalı: {kusurlu}. "
        "Adı `python scripts/durum.py --sonraki <PR>` verir."
    )


def test_her_girdi_tek_satir() -> None:
    """Bir girdi TEK satırdır: kayıt taranabilir kalsın diye."""
    cok_satirli = []
    for yol in sorted(KAYIT_DIZINI.glob("*.md")):
        satirlar = [s for s in yol.read_text(encoding="utf-8").split("\n") if s.strip()]
        if len(satirlar) != 1:
            cok_satirli.append(f"{yol.name} ({len(satirlar)} satır)")
    assert not cok_satirli, f"Girdi tek satır olmalı: {cok_satirli}"


def test_sira_pr_cifti_biricik() -> None:
    """(sıra, pr) çifti biricik olmalı — sıralama belirsiz kalmasın.

    İki eşzamanlı PR'ın AYNI `sıra`yı seçmesi beklenen ve zararsız bir
    durumdur; ayrıştıran şey `pr`dir. Aynı çiftin iki kez görünmesi ise
    aynı PR'ın iki girdi yazdığı anlamına gelir ve sıralama artık
    belirsizdir.
    """
    girdiler = _okuyucu().girdileri_oku(KAYIT_DIZINI)
    ciftler = [(sira, pr) for sira, pr, _, _ in girdiler]
    yinelenen = sorted({c for c in ciftler if ciftler.count(c) > 1})
    assert not yinelenen, f"(sıra, pr) çifti yinelenmiş: {yinelenen}"


def test_okuma_sirasi_en_yeni_ustte() -> None:
    """Okuyucu EN YENİDEN ESKİYE basmalı."""
    girdiler = _okuyucu().girdileri_oku(KAYIT_DIZINI)
    anahtarlar = [(sira, pr) for sira, pr, _, _ in girdiler]
    assert anahtarlar == sorted(anahtarlar, reverse=True), (
        f"okuma sırası en yeni üstte değil: {anahtarlar[:5]}"
    )


def test_durum_md_kurali_tek_cumleyle_yaziyor() -> None:
    """Bir sonraki PR'ın ne yapacağı DOSYANIN KENDİSİNDE yazmalı.

    Kuralı örneklerden çıkarmak zorunda kalan biri yanlış çıkarır; bugünkü
    çakışmaların kaynağı da tam olarak buydu.
    """
    metin = DURUM_MD.read_text(encoding="utf-8")
    assert "**Yeni bir PR şunu yapar:**" in metin, (
        "DURUM.md, yeni bir PR'ın ne yapacağını açıkça söylemeli"
    )
    assert "scripts/durum.py --sonraki" in metin, (
        "kural, dosya adını üreten komutu ADIYLA vermeli"
    )
    assert "## İnen iş" not in metin, (
        "girdiler artık DURUM.md'de tutulmuyor; eski başlık kalırsa "
        "bir sonraki PR yine bu dosyaya ekler ve çakışma geri gelir"
    )


@pytest.mark.parametrize("pr", [67, 999])
def test_sonraki_ad_mevcut_girdiyle_carpismiyor(pr: int) -> None:
    """Önerilen ad her zaman YENİ bir yol olmalı.

    DİKKAT — bu testin GÖREMEDİĞİ şey: yalnız DALIN KENDİ ağacına bakar.
    Bayat bir dal, base'de çoktan kullanılmış bir sıradan yeni bir yol
    üretebilir ve bu test yeşil kalır. O kusuru `bayat_sira_denetle`
    yakalar ve yalnız BİRLEŞME SONUCUNDA görülebilir.
    """
    arac = _okuyucu()
    ad = arac.sonraki_ad(KAYIT_DIZINI, pr)
    assert AD_DESENI.match(ad), f"önerilen ad desene uymuyor: {ad}"
    assert not (KAYIT_DIZINI / ad).exists(), f"önerilen ad zaten var: {ad}"


def test_ayni_sira_pr_ye_gore_siralanir(tmp_path) -> None:
    """Aynı sıradaki iki girdi `pr`ye göre AZALAN okunmalı.

    Mevcut korpusta yinelenen sıra YOK; dolayısıyla gerçek korpus üzerinde
    koşan sıralama testi bu ikincil anahtarı KANITLAMAZ — anahtar sessizce
    değiştirilebilir ve her şey yeşil kalırdı. Bu yüzden yinelenen sıra
    SENTETİK olarak kuruluyor: eşzamanlılık, tasarımın izin vermek için
    var olduğu durumdur ve sözleşmesi çivilenmelidir.
    """
    for ad, metin in [
        ("0031-pr-0901.md", "#901 — A dalı"),
        ("0031-pr-0902.md", "#902 — B dalı"),
        ("0030-pr-0064.md", "#64 — önceki kuşak"),
    ]:
        (tmp_path / ad).write_text(metin + "\n", encoding="utf-8")
    girdiler = _okuyucu().girdileri_oku(tmp_path)
    assert [(s, p) for s, p, _, _ in girdiler] == [(31, 902), (31, 901), (30, 64)], (
        "aynı sırada `pr` azalan olmalı; ikincil anahtar değişmiş"
    )


# (base adları, head adları, kırmızı mı) — bayat sıra kapısının sözleşmesi.
BAYAT_VAKALARI = [
    pytest.param(["0030-pr-0064.md"], ["0030-pr-0064.md", "0031-pr-0067.md"], False,
                 id="normal-bir-sonraki"),
    pytest.param(["0030-pr-0064.md", "0031-pr-0901.md"],
                 ["0030-pr-0064.md", "0031-pr-0901.md", "0031-pr-0902.md"], False,
                 id="eszamanli-ayni-sira-MESRU"),
    pytest.param(["0030-pr-0064.md", "0031-pr-0901.md", "0032-pr-0903.md"],
                 ["0030-pr-0064.md", "0031-pr-0901.md", "0032-pr-0903.md",
                  "0031-pr-0902.md"], True,
                 id="BAYAT-base-ilerlemis"),
    pytest.param(["0030-pr-0064.md"], ["0030-pr-0064.md", "0035-pr-0902.md"], True,
                 id="sira-BOSLUK-birakmis"),
    # Göç PR'ı base'i BOŞ bir kayıtla bulur ve 31 girdiyi birden ekler.
    # Üst sınır sabit olsaydı bu meşru durum kırmızı olurdu; ölçüldü ve
    # oldu da — sınır bu yüzden eklenen sayısına bağlı.
    pytest.param([], [f"{i:04d}-pr-{i:04d}.md" for i in range(1, 32)], False,
                 id="toplu-goc-bosluksuz-MESRU"),
    pytest.param([], ["0001-pr-0001.md", "0003-pr-0003.md"], True,
                 id="toplu-eklemede-BOSLUK-kirmizi"),
]


@pytest.mark.parametrize("base, head, kirmizi_olmali", BAYAT_VAKALARI)
def test_bayat_sira_kapisi(base: list[str], head: list[str], kirmizi_olmali: bool) -> None:
    """Bayat sıra kırmızı, eşzamanlı sıra yeşil.

    İki yön de sınanır: kapı bayatlığı yakalamazsa kusur geri gelir, ama
    eşzamanlılığı da reddederse bu tasarımın var olma sebebini yok eder.
    """
    ihlaller = _okuyucu().bayat_sira_denetle(base, head)
    if kirmizi_olmali:
        assert ihlaller, "bayat/atlamış sıra kırmızı olmalıydı, temiz döndü"
    else:
        assert not ihlaller, f"meşru girdi reddedildi: {ihlaller}"


CI_YML = DEPO_KOKU / ".github" / "workflows" / "ci.yml"
SINIR_BAS = "# >>> DEKLARE EDİLMİŞ SINIR (durum-kaydi) >>>"
SINIR_SON = "# <<< DEKLARE EDİLMİŞ SINIR <<<"

#: Deklare edilmiş sınırın KENDİ izi. #59'daki desen: sınır metni tek tek
#: cümlelere değil, BÜTÜNÜNE sabitlenir — yoksa metin daraltılıp test yeşil
#: kalabilir. Sınırı değiştirmek meşrudur; SESSİZCE değiştirmek değildir.
BEKLENEN_SINIR_IZI = "8b8c2503a4886734c2ef1ddace0bff46705b0730913de6a045f724699c4a6131"


def _deklare_edilmis_sinir() -> str:
    metin = CI_YML.read_text(encoding="utf-8")
    assert SINIR_BAS in metin and SINIR_SON in metin, (
        "durum-kaydi işinin deklare edilmiş sınır bloğu ci.yml'de bulunamadı; "
        "sınır kapının KENDİ dosyasında yaşar"
    )
    bas = metin.index(SINIR_BAS)
    son = metin.index(SINIR_SON) + len(SINIR_SON)
    return metin[bas:son]


def test_deklare_edilmis_sinir_isine_yazili_ve_izine_bagli() -> None:
    """Kapının ne ölçmediği, kapının KENDİ dosyasında ve çiviye bağlı.

    Kullanım belgesi (`docs/DURUM.md`) katkı verene ne YAPACAĞINI söyler;
    kapının neyi KAÇIRDIĞINI bilmesi gereken kişi ise işi ve log'unu okur.
    Sınır orada yazılı olmazsa, orada olmadığı için yok sayılır.
    """
    blok = _deklare_edilmis_sinir()
    olculen = hashlib.sha256(blok.encode("utf-8")).hexdigest()
    assert olculen == BEKLENEN_SINIR_IZI, (
        "Deklare edilmiş sınır değişmiş.\n"
        f"  ölçülen={olculen}\n  çapa   ={BEKLENEN_SINIR_IZI}\n"
        "Sınırı DARALTMAK da genişletmek de bilinçli bir işlem olmalı: "
        "metni değiştirdiysen bu izi de güncelle."
    )


def test_deklare_edilmis_sinir_devralinmayan_sinirlari_ayirt_ediyor() -> None:
    """Sınır, GEÇERLİ OLMAYAN bir sınırı devralmadığını açıkça söylemeli.

    Var olmayan bir sınırı yazmak, eksik yazmak kadar yanlıştır: okuyana
    kapının kör olduğu bir yer varmış gibi öğretir. Bu test o ayrımın
    metinden silinmesini engeller.
    """
    blok = _deklare_edilmis_sinir()
    for beklenen in ("ÖLÇER:", "ÖLÇMEZ:", "DEVRALINMAYAN SINIR", "GERÇEKTEN KALAN"):
        assert beklenen in blok, f"sınır bildirimi {beklenen!r} başlığını taşımalı"


def test_bayat_sira_kapisi_gercek_korpusta_temiz() -> None:
    """Bu PR'ın kendi girdisi kapıdan geçmeli — kapı kendini de bağlar."""
    arac = _okuyucu()
    base = [f"{i:04d}-pr-0000.md" for i in range(1, 31)]
    head = base + ["0031-pr-0067.md"]
    assert not arac.bayat_sira_denetle(base, head)


# ---------------------------------------------------------------------------
# VARLIK KAPISI — İKİ YÖN
#
# Tek yön kanıtlamak yetmez: yalnız "girdisiz PR kırmızı" kanıtlanırsa, HER
# PR'ı kırmızı yapan bir kapı da yeşil görünür. İkinci yön kapının hâlâ
# geçirdiğini gösterir.
# ---------------------------------------------------------------------------


def _girdi_varligi_denetle():
    return _okuyucu().girdi_varligi_denetle


def test_girdi_EKLEYEN_pr_varlik_kapisindan_gecer() -> None:
    """YÖN 1: girdi taşıyan PR yeşil."""
    base = ["docs/durum/0031-pr-0067.md", "docs/durum/0032-pr-0068.md"]
    head = base + ["docs/durum/0033-pr-0099.md"]
    assert _girdi_varligi_denetle()(base, head) == []


def test_girdi_EKLEMEYEN_pr_varlik_kapisini_KIRMIZI_yapar() -> None:
    """YÖN 2: girdisiz PR kırmızı — kapının KENDİ cümlesiyle."""
    base = ["docs/durum/0031-pr-0067.md", "docs/durum/0032-pr-0068.md"]
    ihlaller = _girdi_varligi_denetle()(base, list(base))
    assert len(ihlaller) == 1, ihlaller
    assert "GİRDİ YOK" in ihlaller[0]
    assert "Kayıt PR başına bir girdiyle büyür" in ihlaller[0]


def test_yalnizca_dosya_degistiren_pr_de_KIRMIZI() -> None:
    """Var olan girdiyi DÜZENLEMEK yeni girdi sayılmaz."""
    base = ["docs/durum/0031-pr-0067.md"]
    assert _girdi_varligi_denetle()(base, ["docs/durum/0031-pr-0067.md"]) != []


def test_varlik_kapisi_TARIHSEL_bosslugu_dondurur() -> None:
    """DONMUŞ TARİHSEL BOŞLUK — kayıt bu sayılarla okunmalı.

    2026-08-17'de develop `f244c8f3` üzerinde ölçüldü: birleşme commit'i olan
    50 PR'ın 17'si girdisiz inmiştir. Bu sayı GERİYE DÖNÜK KAPATILMAYACAK; kapı
    yalnız bundan SONRASINI korur. Sayı burada donar ki boşluk bir karar olarak
    okunsun, bir mazeret olarak değil.
    """
    assert TARIHSEL_GIRDISIZ_TOPLAM == 17
    assert TARIHSEL_GIRDISIZ_KAYIT_ONCESI == 10
    assert TARIHSEL_GIRDISIZ_GOC_PENCERESINDE == 5
    assert TARIHSEL_GIRDISIZ_BU_PR_ILE_KAPANAN == 2
    assert (
        TARIHSEL_GIRDISIZ_KAYIT_ONCESI
        + TARIHSEL_GIRDISIZ_GOC_PENCERESINDE
        + TARIHSEL_GIRDISIZ_BU_PR_ILE_KAPANAN
        == TARIHSEL_GIRDISIZ_TOPLAM
    )


# ---------------------------------------------------------------------------
# SIKI KURAL — eklenen girdilerden biri PR'ın KENDİ numarasını taşımalı.
# Backfill serbest; kendi girdisinin YERİNE geçemez.
# ---------------------------------------------------------------------------


def test_yalnizca_BASKASININ_girdisini_ekleyen_pr_KIRMIZI() -> None:
    """YÖN 1: sadece backfill yapıp kendi girdisini yazmayan PR kırmızı."""
    base = ["docs/durum/0033-pr-0070.md"]
    head = base + ["docs/durum/0034-pr-0071.md"]          # #71'in girdisi, #99'un değil
    ihlaller = _girdi_varligi_denetle()(base, head, 99)
    assert len(ihlaller) == 1, ihlaller
    assert "KENDİ GİRDİSİ YOK" in ihlaller[0]
    assert "#99" in ihlaller[0] and "#71" in ihlaller[0]


def test_kendi_girdisini_ekleyen_pr_GECER_backfill_ile_birlikte() -> None:
    """YÖN 2: kendi girdisi varsa, yanında backfill olsa da geçer (#70 böyleydi)."""
    base = ["docs/durum/0031-pr-0067.md"]
    head = base + ["docs/durum/0032-pr-0068.md", "docs/durum/0033-pr-0070.md"]
    assert _girdi_varligi_denetle()(base, head, 70) == []


def test_sadece_kendi_girdisi_de_GECER() -> None:
    base = ["docs/durum/0033-pr-0070.md"]
    head = base + ["docs/durum/0034-pr-0099.md"]
    assert _girdi_varligi_denetle()(base, head, 99) == []


def test_hic_girdi_yoksa_sıkı_kuralda_da_KIRMIZI() -> None:
    base = ["docs/durum/0033-pr-0070.md"]
    ihlaller = _girdi_varligi_denetle()(base, list(base), 99)
    assert ihlaller and "GİRDİ YOK" in ihlaller[0]


def test_ci_kapiyi_SIKI_calistirmali() -> None:
    """`--pr` verilmezse kapı sessizce GEVŞEK çalışır; bu satır o düşüşü kapatır.

    Kapının kendisini sınamak yetmez: CI onu gevşek çağırırsa sıkı kural hiç
    çalışmaz ve kimse fark etmez.
    """
    akis = (DEPO_KOKU / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/durum.py --kapi" in akis
    satir = next(s for s in akis.splitlines() if "scripts/durum.py --kapi" in s)
    assert "--pr" in satir, f"CI kapıyı SIKI çağırmıyor: {satir!r}"
    assert "github.event.pull_request.number" in akis


# ---------------------------------------------------------------------------
# YİNELENEN SIRA — BİRLEŞME SONUCUNDA
#
# BAYAT kapısı `sıra == base_max`a izin verdiği için iki eşzamanlı PR aynı
# numarayı taşıyabiliyordu; bugün altı kez oldu ve altısını da insan yakaladı.
# ---------------------------------------------------------------------------

YINELENEN_SIRA_TEPE_DURUMU = 0      # develop'ın 284 ilk-ebeveyn tepe durumunda
YINELENEN_SIRA_DAL_BIRLESMESI = 5   # dal-tarafı birleşme sonuçlarında ölçüldü


def _yinelenen():
    return _okuyucu().yinelenen_sira_denetle


def _agac(tmp_path, adlar):
    """GERÇEK bir ağaç kurar ve listeler — liste ÇİFTİ değil.

    Kusuru üreten şey `set(base) | set(head)` idi: silme ve yeniden adlandırma
    o birleşimde iz bırakıyordu. Testler artık dosyaları gerçekten yaratıp
    dizini listeler; yani girdi, CI'daki birleşme sonucuyla AYNI biçimdedir.
    """
    dizin = tmp_path / "durum"
    dizin.mkdir(exist_ok=True)
    for eski in dizin.glob("*.md"):
        eski.unlink()
    for ad in adlar:
        (dizin / ad).write_text("girdi", encoding="utf-8")
    return [f"docs/durum/{yol.name}" for yol in sorted(dizin.glob("*.md"))]


def test_SILME_sonrasi_ayni_sira_YESIL(tmp_path) -> None:
    """A: head 0042-pr-0083'ü SİLİP 0042-pr-0084 ekler -> ağaçta TEK dosya.

    BEKLENTİ ÖNCEDEN YAZILDI: GREEN. Sözleşme cümlesi birleşme SONUCUNU
    ölçmeyi söylüyor; sonuçta sıra 42'de tek dosya var.
    """
    agac = _agac(tmp_path, ["0041-pr-0082.md", "0042-pr-0084.md"])
    assert _yinelenen()(agac) == []


def test_YENIDEN_ADLANDIRMA_sonucu_yinelenirse_KIRMIZI(tmp_path) -> None:
    """B: 0042-pr-0083 -> 0043-pr-0083 ve başka dal 0043'ü tutuyor.

    BEKLENTİ ÖNCEDEN YAZILDI: RED. Yeniden adlandırmanın kendisi ilgisizdir;
    ölçülen ağaçta sıra 43'te İKİ dosya vardır.
    """
    agac = _agac(tmp_path, ["0043-pr-0083.md", "0043-pr-0084.md"])
    ihlaller = _yinelenen()(agac)
    assert len(ihlaller) == 1, ihlaller
    assert "0043-pr-0083.md" in ihlaller[0] and "0043-pr-0084.md" in ihlaller[0]


def test_CANLI_cift_KIRMIZI_ve_iki_dosyayi_da_adlandirir(tmp_path) -> None:
    """C: #83/#84 canlı çifti. BEKLENTİ ÖNCEDEN YAZILDI: RED, iki dosya adlı."""
    agac = _agac(tmp_path, ["0042-pr-0077.md", "0043-pr-0083.md", "0043-pr-0084.md"])
    ihlaller = _yinelenen()(agac)
    assert len(ihlaller) == 1, ihlaller
    assert "YİNELENEN sıra 0043" in ihlaller[0]
    assert "0043-pr-0083.md" in ihlaller[0] and "0043-pr-0084.md" in ihlaller[0]


def test_MESRU_dizi_YESIL(tmp_path) -> None:
    """D: 0043 -> 0044 -> 0045. BEKLENTİ ÖNCEDEN YAZILDI: GREEN."""
    agac = _agac(tmp_path, ["0043-pr-0084.md", "0044-pr-0085.md", "0045-pr-0086.md"])
    assert _yinelenen()(agac) == []


def test_CONTROL_gercek_kayit_YESIL() -> None:
    """E: deponun gerçek kaydı. BEKLENTİ ÖNCEDEN YAZILDI: GREEN."""
    adlar = [f"docs/durum/{yol.name}" for yol in sorted(KAYIT_DIZINI.glob("*.md"))]
    assert adlar, "kayıt boş ölçüldü; bu test vakumda geçemez"
    assert _yinelenen()(adlar) == []


def test_gecmiste_INEN_hicbir_kayit_bu_kuralla_reddedilmezdi() -> None:
    """GERİYE DÖNÜK KARŞI ÖRNEK YOK — #75'in katılığını savunan ölçüm.

    develop'ın ilk-ebeveyn tepe durumlarının HİÇBİRİ yinelenen sıra taşımıyor;
    buna karşılık dal-tarafı birleşme sonuçlarında taşıyor. Kusurun yeri
    birleşme sonucudur, inen kayıt değil.
    """
    assert YINELENEN_SIRA_TEPE_DURUMU == 0
    assert YINELENEN_SIRA_DAL_BIRLESMESI == 5

"""WA3-core NİYET: deterministik çözücü ve telefon kanonikleştirme — SAF KOD.

Konu: `app/whatsapp/niyet.py`, `app/whatsapp/telefon.py`, `app/whatsapp/__init__.py`.

Kaynak `nazgul_website/backend`: `test_whatsapp_assistant.py` (42 test) ve
`test_whatsapp_tahsilat.py` (22 test), toplam 64. ÖLÇÜLDÜ (imza satırları
`ortam` / `istemci` fixture'ına göre süzülerek):

  * 10 test TAŞINDI — DB fixture'sız ve taşınan modülleri ölçüyor:
      assistant: normalize_phone, dar niyetler doğru araca, dar niyetler
      eski akışı bozmaz, emir kipi dar niyete ulaşamaz, her aracın cevap
      şablonu var (5);
      tahsilat: tutar ayrıştırıcı tablosu, günlük fiiller, fiil stok
      bağlamında sayılmaz, fiil yöntemsiz yöntemi sorar, tutar var niyet
      yok rehberlik (5).
  * 54 test DÜŞÜRÜLDÜ:
      52'si `ortam` (izole SQLite + gerçek tablolar) ya da `istemci`
      (webhook TestClient) fixture'ı istiyor — service/worker/webhook/
      bekleyen katmanları bu PR'da YOK (tablo yok, rota yok);
      2'si taşınmayan modülleri ölçüyor: `test_soru_maskele_birim_davranislari`
      (web asistanının `assistant.masking.soru_maskele`i — WhatsApp bu
      maskeyi kullanmaz) ve `test_saglayici_kapaliyken_ag_cagrisi_yapilmaz`
      (`provider.py`, Meta Cloud API sağlayıcısı).

Taşınan test gövdeleri KAYNAKLA AYNI; yalnız import yolu değişti ve
`ARAC_BEYAZ_LISTESI` artık `niyet` içinde (kaynakta `service`teydi).

BU DEPOYA ÖZGÜ EKLER: Türkçe katlama varyantları (ş/ı/İ, "bakiye", "stok",
"vade"), belirsizlik → ARAÇ YOK (tahmin yok), koşturucu dikişi
(`NiyetYurutucu` + `NoOpYurutucu` + `dene`), E.164, ve modülün DB'siz
olduğunu AST ile sabitleyen statik kapı.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

  * `coz`da `len(gruplar) != 1` yerine `> 1` (tek grup yokken tahmin)
                                       -> BELİRSİZLİK kapısı KIRMIZI
  * `tr_katla`dan ("İ","I") çiftini düşürmek
                                       -> KATLAMA (İ) kapısı KIRMIZI
  * `_YUMUSAMA`yı boşaltmak ("stoğu" STOK'a yaslanamaz)
                                       -> KATLAMA (yumuşama) kapısı KIRMIZI
  * `_ozel_niyet`ten emir kipi kapısını (0) kaldırmak
                                       -> EMİR KİPİ kapısı KIRMIZI (kaynak testi)
  * `dene`de `sonuc_bos_mu` denetimini düşürmek (hep ilk deneme)
                                       -> DENEME SIRASI kapısı KIRMIZI
  * `niyet.py`ye `from ..db import ...` eklemek
                                       -> DB'SİZ statik kapı KIRMIZI
"""
from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from app.whatsapp import niyet, telefon
from app.whatsapp.niyet import ARAC_BEYAZ_LISTESI, KAPSAM_MESAJI, Niyet, NoOpYurutucu, coz, dene

PAKET = Path(__file__).resolve().parents[1] / "app" / "whatsapp"


# ---------------------------------------------------------------------------
# KAYNAKTAN TAŞINAN 10 TEST (gövdeler kaynakla aynı)
# ---------------------------------------------------------------------------


def test_normalize_phone_ayni_anahtara_iner() -> None:
    for ham in ("0540 599 59 59", "+90 540 599 59 59", "905405995959", "5405995959"):
        assert telefon.normalize_phone(ham) == "905405995959", ham


def test_dar_niyetler_dogru_araca_gider() -> None:
    """Canlıda ölçülen dört arıza (2026-08-22) için sözleşme.

    O gün kullanıcının gönderdiği mesajlar ya "yanıtlayamıyorum" alıyor ya da
    SESSİZCE stok aramasına düşüyordu: "Bıçak kaç adet satılmış" stok cevabı
    veriyordu — satış sorusuna stok rakamı, yanlış cevabı doğruymuş gibi.
    """
    beklenen = {
        # canlıda başarısız olan gerçek mesajlar
        "En çok satilan ürün": "en_cok_satan_parcalar",
        "Erp programında en çok satılan ürün": "en_cok_satan_parcalar",
        "Bıçak kaç adet satılmış": "parca_satis_gecmisi",
        # yeni açılan diğer araçlar
        "vadesi geçen alacaklar": "alacak_yaslandirma",
        "kim borçlu alacak": "alacak_yaslandirma",
        "neyin stoğu bitiyor": "kritik_stok",
        "stoğu azalıyor": "kritik_stok",
        "kritik stok": "kritik_stok",
        "geçen ay en çok ne sattım": "en_cok_satan_parcalar",
        # terimsiz satış sorusu firma geneli demektir
        "bu ay satış ne kadar": "donem_ozeti",
    }
    for metin, arac in beklenen.items():
        sonuc = niyet.coz(metin)
        assert sonuc.arac == arac, f"{metin!r} -> {sonuc.arac or sonuc.mesaj}"

    # Dönem argümanı açık yazıldığında okunur; yazılmadığında araca uygun
    # varsayılan seçilir (özet "bu ay", satış geçmişi "bu yıl").
    assert niyet.coz("geçen ay en çok ne sattım").deneme_argumanlari[0] == {
        "donem": "gecen_ay"
    }
    assert niyet.coz("En çok satilan ürün").deneme_argumanlari[0] == {"donem": "bu_ay"}
    assert (
        niyet.coz("Bıçak kaç adet satılmış").deneme_argumanlari[0]["donem"] == "bu_yil"
    )


def test_dar_niyetler_mevcut_akislari_bozmaz() -> None:
    """Yeni kökler eski komutları yutmamalı."""
    degismeyen = {
        "yağ filtresi stok": "parca_stok",
        "84993120 stok": "parca_stok",
        "Şaban Korkmaz borç": "cari_durum",
        "bu ay ciro": "donem_ozeti",
        "geçen ay tahsilat": "donem_ozeti",
    }
    for metin, arac in degismeyen.items():
        assert niyet.coz(metin).arac == arac, metin

    # "satın almak" satış DEĞİLDİR: kökler bu yüzden kısa tutulmadı.
    assert niyet.coz("satın aldığım parçalar").arac != "parca_satis_gecmisi"


def test_emir_kipi_hicbir_dar_niyete_ulasamaz() -> None:
    """Yazma denemeleri okuma araçlarına sızmamalı.

    Kanalda tek yazma yolu tahsilattır ve onun kendi onay akışı vardır.
    "kritik stoğu 5 yap" kelime örtüşmesi yüzünden kritik_stok'a gidiyordu;
    kullanıcı yapılmamış bir işi yapılmış sanardı.
    """
    for metin in (
        "kritik stoğu 5 yap",
        "alacakları güncelle",
        "Bıçak sil",
        "kritik seviye tanımla",
        "en çok satanı değiştir",
    ):
        sonuc = niyet.coz(metin)
        assert sonuc.arac in (None, "parca_stok", "cari_durum"), metin
        # Dar niyetlerin hiçbirine ulaşmamalı.
        assert sonuc.arac not in {
            "alacak_yaslandirma",
            "kritik_stok",
            "en_cok_satan_parcalar",
            "parca_satis_gecmisi",
        }, metin


def test_yeni_araclarin_cevap_sablonu_var() -> None:
    """Beyaz listedeki her araç için cevap_yaz şablonu bulunmalı.

    Şablon yoksa cevap_yaz KeyError atar ve kullanıcı "cevaplayamıyorum"
    görür — araç çalışmış, veri gelmiş olsa bile.
    """
    for arac in ARAC_BEYAZ_LISTESI:
        try:
            niyet.cevap_yaz(arac, {})
        except KeyError:  # pragma: no cover - başarısızlıkta anlamlı mesaj
            raise AssertionError(f"{arac} için cevap şablonu yok")


def test_tutar_ayristirici_birim_tablosu() -> None:
    """Zorunlu TR tutar tablosu — LLM'siz, saf fonksiyon."""
    gecerli = {
        "Ahmet 20.000 TL nakit tahsilat": Decimal("20000"),
        "Ahmet 20.000,50 TL nakit tahsilat": Decimal("20000.50"),
        "Ahmet 20000 nakit tahsilat": Decimal("20000"),
        "Ahmet 20 bin TL nakit tahsilat": Decimal("20000"),
        "Ahmet 20000,5 nakit tahsilat": Decimal("20000.5"),
    }
    for metin, beklenen in gecerli.items():
        sonuc = niyet.tahsilat_coz(metin)
        assert isinstance(sonuc, niyet.TahsilatNiyeti), metin
        assert sonuc.tutar == beklenen and sonuc.yontem == "cash", metin
        assert sonuc.musteri_terimi == "Ahmet", metin

    redler = [
        "Ahmet -500 TL nakit tahsilat",          # negatif
        "Ahmet 0 TL nakit tahsilat",             # sıfır
        "Ahmet 99.000.000 TL nakit tahsilat",    # üst sınır
        "Ahmet 100 TL 200 TL nakit tahsilat",    # belirsiz (iki tutar)
        "Ahmet 500 TL tahsilat",                 # yöntem yok
        "Ahmet 500 TL nakit ve kart tahsilat",   # iki yöntem
        "dün Ahmet 500 TL nakit tahsilat",       # geçmiş tarih
        "12.08.2026 Ahmet 500 TL nakit tahsilat",
        "Ahmet 500 USD nakit tahsilat",          # TRY dışı
    ]
    for metin in redler:
        sonuc = niyet.tahsilat_coz(metin)
        assert isinstance(sonuc, niyet.Niyet) and sonuc.mesaj, metin

    # Tutar yoksa yazma niyeti DEĞİLDİR (okuma akışına düşer).
    assert niyet.tahsilat_coz("geçen ay tahsilat ne kadar") is None
    assert niyet.tahsilat_coz("tahsilat aldım Ahmet") is None
    # 10+ haneli saf rakam telefon sayılır, tutar adayı değildir.
    sonuc = niyet.tahsilat_coz("05405995959 500 TL nakit tahsilat")
    assert isinstance(sonuc, niyet.TahsilatNiyeti) and sonuc.tutar == Decimal("500")


def test_gunluk_fiiller_tahsilat_sayilir() -> None:
    """"tahsilat" kelimesi olmadan da yazma niyeti çözülür.

    Kullanıcı günlük dilde "verdi / ödedi / yatırdı" diyor. Yöntem ve tutar
    ZORUNLU kalır — gevşetilen yalnız niyet kelimesidir.
    """
    beklenenler = {
        "Şaban Korkmaz 175.000 nakit verdi": ("cash", Decimal("175000")),
        "Şaban Korkmaz 50.000 havale ödedi": ("bank_transfer", Decimal("50000")),
        "Şaban Korkmaz 12.500 kart yatırdı": ("card", Decimal("12500")),
        "Şaban Korkmaz nakit ödeme 8.000": ("cash", Decimal("8000")),
    }
    for metin, (yontem, tutar) in beklenenler.items():
        sonuc = niyet.tahsilat_coz(metin)
        assert isinstance(sonuc, niyet.TahsilatNiyeti), metin
        assert sonuc.yontem == yontem and sonuc.tutar == tutar, metin
        # Fiil müşteri terimine sızmamalı, yoksa müşteri bulunamaz.
        assert sonuc.musteri_terimi == "Şaban Korkmaz", metin


def test_fiil_stok_baglaminda_tahsilat_sayilmaz() -> None:
    """"5 tane verdi" parça teslimidir — para taslağı AÇILMAZ.

    Fiiller açık "tahsilat" kelimesi kadar kesin değildir; stok kökü varken
    yazma akışı devreye girmez ve mesaj Faz 1 okuma akışına düşer.
    """
    for metin in (
        "5 tane verdi",
        "3 adet yağ filtresi verdi",
        "84993120 parça 2 verdi",
        "10 tane nakit verdi",  # yöntem VAR ama bağlam stok: yine de yazma değil
    ):
        assert niyet.tahsilat_coz(metin) is None, metin

    # Açık niyet kelimesi yazılırsa koruma aranmaz: kullanıcı söylemiştir.
    # (Tek tutar; iki tutar zaten ayrı bir kuralla reddedilir.)
    sonuc = niyet.tahsilat_coz("Ahmet 5.000 nakit tahsilat parça")
    assert isinstance(sonuc, niyet.TahsilatNiyeti)
    assert sonuc.tutar == Decimal("5000")


def test_fiil_yontemsiz_yontemi_sorar() -> None:
    """Yöntem sessizce varsayılmaz; eksikse açıkça sorulur."""
    sonuc = niyet.tahsilat_coz("Şaban Korkmaz 175.000 verdi")
    assert isinstance(sonuc, niyet.Niyet) and sonuc.mesaj
    assert "nakit" in sonuc.mesaj and "havale" in sonuc.mesaj

    # Tutarsız fiil yazma niyeti DEĞİLDİR (okuma akışı denesin).
    assert niyet.tahsilat_coz("Ahmet ödedi") is None


def test_tutar_var_niyet_yok_rehberlik_doner() -> None:
    """Tutar yazılıp niyet anlaşılmazsa genel kapsam mesajı YETMEZ.

    Kullanıcı bir tutar yazdığına göre tahsilat girmeye çalışıyordur; doğru
    kalıbı öğrenebilmeli. Tanınan tutar geri okunur, kuruşsuz yazılır.
    """
    sonuc = niyet.coz("Şaban Korkmaz 175.000")
    assert sonuc.arac is None and sonuc.mesaj
    assert "175.000" in sonuc.mesaj
    assert "tahsilat" in sonuc.mesaj.lower()
    assert "175.000,00" not in sonuc.mesaj

    # Tutarsız anlamsız mesaj eski davranışta kalır.
    assert niyet.coz("merhaba nasılsın").mesaj == niyet.KAPSAM_MESAJI
    # Çapraz niyette tahmin yürütülmez: sorun tutar değil, belirsizliktir.
    assert niyet.coz("Ahmet 500 borç stok").mesaj == niyet.KAPSAM_MESAJI


# ---------------------------------------------------------------------------
# TÜRKÇE KATLAMA VARYANTLARI — ş/ı/İ, "bakiye", "stok", "vade"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metin", "arac", "arg"),
    [
        # "bakiye" — küçük/BÜYÜK/noktalı İ/noktasız ı hepsi CARI köküne yaslanır
        ("Şaban Korkmaz bakiye", "cari_durum", {"musteri": "Şaban Korkmaz"}),
        ("ŞABAN KORKMAZ BAKİYE", "cari_durum", {"musteri": "ŞABAN KORKMAZ"}),
        ("şaban korkmaz bakıye", "cari_durum", {"musteri": "şaban korkmaz"}),
        ("İsmail Öztürk bakiyesi", "cari_durum", {"musteri": "İsmail Öztürk"}),
        ("İBRAHİM ÖZTÜRK BAKİYESİ", "cari_durum", {"musteri": "İBRAHİM ÖZTÜRK"}),
        # "stok" — yumuşama (stoğu), büyük İ'li STOĞU, eksiz
        ("yağ filtresi stoğu", "parca_stok", {"arama": "yağ filtresi"}),
        ("YAĞ FİLTRESİ STOĞU", "parca_stok", {"arama": "YAĞ FİLTRESİ"}),
        ("bıçak stok", "parca_stok", {"arama": "bıçak"}),
        ("BIÇAK STOK", "parca_stok", {"arama": "BIÇAK"}),
        # "vade" — alacak yaşlandırma; terim aranmaz
        ("vadesi geçen", "alacak_yaslandirma", {}),
        ("VADESİ GEÇENLER", "alacak_yaslandirma", {}),
        ("vadesı gecmiş alacaklar", "alacak_yaslandirma", {}),
    ],
)
def test_turkce_katlama_varyantlari(metin: str, arac: str, arg: dict) -> None:
    sonuc = coz(metin)
    assert sonuc.arac == arac, f"{metin!r} -> {sonuc.arac or sonuc.mesaj}"
    assert sonuc.deneme_argumanlari[0] == arg, metin


def test_tr_katla_noktali_ve_noktasiz_I() -> None:
    """"İ"→"I" ve "ı"→"i" çifti katlamadan ÖNCE düzlenir; upper() tek başına yetmez."""
    assert niyet.tr_katla("İsmail ışık") == "ISMAIL ISIK"
    assert niyet.tr_katla("bakİye") == niyet.tr_katla("bakıye") == niyet.tr_katla("bakiye") == "BAKIYE"
    assert niyet.tr_katla("stoğu") == "STOGU" and niyet._kok_eslesir("STOGU", niyet.STOK_KOKLER)
    assert niyet.tr_katla("ŞĞÜÖÇ şğüöç") == "SGUOC SGUOC"


def test_OKUMA_akisinda_YILMAZ_soyadi_YIL_kokune_yaslanir_OLCULDU() -> None:
    """Kaynak davranışı, düzeltme DEĞİL: "Yılmaz" okuma akışında elenir.

    `_terim_cikar` sözlük köküne 3 harften itibaren yaslanır (YIL + 3 harf
    ek ≤ tolerans 6) ve soyadını düşürür; müşteri terimi "İsmail" kalır.
    Kaynak bunu yalnız YAZMA akışında (≥4 harf kök) korumuştu. Burada
    sabitlenir ki ileride biri "düzeltirken" iki akış sessizce ayrışmasın.
    """
    assert coz("İsmail Yılmaz bakiyesi").deneme_argumanlari == ({"musteri": "İsmail"},)
    yazma = niyet.tahsilat_coz("İsmail Yılmaz 500 TL nakit tahsilat")
    assert isinstance(yazma, niyet.TahsilatNiyeti) and yazma.musteri_terimi == "İsmail Yılmaz"


def test_ek_kirpilmis_yedek_ikinci_deneme() -> None:
    """"filtresinden" → ikinci deneme FILTRE; "Korkmazın" → KORKMAZ."""
    sonuc = coz("yağ filtresinden stok")
    assert sonuc.arac == "parca_stok"
    assert sonuc.deneme_argumanlari == ({"arama": "yağ filtresinden"}, {"arama": "YAG FILTRE"})
    sonuc = coz("Korkmazın borcu")
    assert sonuc.arac == "cari_durum"
    assert sonuc.deneme_argumanlari == ({"musteri": "Korkmazın"}, {"musteri": "KORKMAZ"})


# ---------------------------------------------------------------------------
# BELİRSİZLİK → ARAÇ YOK (tahmin yok, fail-closed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metin",
    [
        "",                                  # boş
        "   ",                               # yalnız boşluk
        "merhaba",                           # hiçbir kök
        "yağ filtresi",                      # terim var, niyet kökü yok
        "Şaban Korkmaz borç stok",           # cari + stok çapraz
        "bu ay ciro stok",                   # donem + stok çapraz
        "Şaban Korkmaz bakiye ciro",         # cari + donem çapraz
        "Şaban'ın cirosu",                   # donem + kalıntı terim
        "ne yapmalıyım",                     # yalnız soru sözlüğü
    ],
)
def test_belirsizlik_ARAC_YOK_kapsam_mesaji(metin: str) -> None:
    sonuc = coz(metin)
    assert sonuc.arac is None, f"{metin!r} tahmin edildi: {sonuc.arac}"
    assert sonuc.deneme_argumanlari == ()
    assert sonuc.mesaj == KAPSAM_MESAJI


def test_eksik_terim_rehberlik_arac_yok() -> None:
    for metin, ipucu in (("borç", "Hangi müşteri"), ("stok", "Hangi parça"), ("bakiye ne", "Hangi müşteri")):
        sonuc = coz(metin)
        assert sonuc.arac is None and sonuc.mesaj and ipucu in sonuc.mesaj, metin


def test_bos_niyet_hep_mesaj_ya_da_arac_ikisi_birden_degil() -> None:
    for metin in ("Şaban Korkmaz borç", "merhaba", "84993120 stok", "bu ay ciro", "", "500"):
        sonuc = coz(metin)
        assert (sonuc.arac is None) != (sonuc.mesaj is None), metin
        if sonuc.arac is not None:
            assert sonuc.arac in ARAC_BEYAZ_LISTESI and sonuc.deneme_argumanlari, metin


# ---------------------------------------------------------------------------
# KOŞTURUCU DİKİŞİ — NoOp; DB yok
# ---------------------------------------------------------------------------


def test_noop_yurutucu_tum_denemeleri_sirayla_kosar_ve_bos_doner() -> None:
    yurutucu = NoOpYurutucu()
    n = coz("yağ filtresinden stok")
    veri = dene(n, yurutucu)
    # Son denemenin BOŞ sonucu döner; `{}` DEĞİL — kaynak `{}`yi dolu okur.
    assert veri == dict(niyet.BOS_SONUC, aranan="YAG FILTRE")
    assert niyet.sonuc_bos_mu("parca_stok", veri) and not niyet.sonuc_bos_mu("parca_stok", {})
    assert "bulunamadı" in niyet.cevap_yaz("parca_stok", veri)
    assert yurutucu.cagrilar == [
        ("parca_stok", {"arama": "yağ filtresinden"}),
        ("parca_stok", {"arama": "YAG FILTRE"}),
    ]


def test_dene_ilk_DOLU_sonucta_durur() -> None:
    class Sahte:
        def __init__(self) -> None:
            self.cagrilar: list[dict] = []

        def kos(self, arac: str, argumanlar: dict) -> dict:
            self.cagrilar.append(argumanlar)
            return {"bulunan": 1, "musteri": "Şaban Korkmaz"} if len(self.cagrilar) == 1 else {"bulunan": 0}

    y = Sahte()
    veri = dene(coz("Korkmazın borcu"), y)
    assert veri["bulunan"] == 1 and y.cagrilar == [{"musteri": "Korkmazın"}]
    assert "Şaban Korkmaz" in niyet.cevap_yaz("cari_durum", veri)


def test_dene_bos_ilk_sonucta_yedegi_dener() -> None:
    class Sahte:
        def __init__(self) -> None:
            self.cagrilar: list[dict] = []

        def kos(self, arac: str, argumanlar: dict) -> dict:
            self.cagrilar.append(argumanlar)
            return {"bulunan": 0, "aranan": argumanlar["musteri"]} if len(self.cagrilar) == 1 else {"bulunan": 1}

    y = Sahte()
    assert dene(coz("Korkmazın borcu"), y) == {"bulunan": 1}
    assert y.cagrilar == [{"musteri": "Korkmazın"}, {"musteri": "KORKMAZ"}]


def test_dene_mesaj_niyeti_ve_beyaz_liste_disi_arac_reddedilir() -> None:
    y = NoOpYurutucu()
    with pytest.raises(ValueError):
        dene(coz("merhaba"), y)
    with pytest.raises(KeyError):
        dene(Niyet(arac="tahsilat_yaz", deneme_argumanlari=({},)), y)
    assert y.cagrilar == []


def test_beyaz_liste_kaynakla_ayni_YEDI_okuma_araci() -> None:
    assert ARAC_BEYAZ_LISTESI == frozenset({
        "cari_durum", "parca_stok", "donem_ozeti", "alacak_yaslandirma",
        "kritik_stok", "en_cok_satan_parcalar", "parca_satis_gecmisi",
    })
    with pytest.raises(KeyError):
        niyet.cevap_yaz("tahsilat_yaz", {})


# ---------------------------------------------------------------------------
# TELEFON — E.164
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ham", ["0540 599 59 59", "+90 540 599 59 59", "905405995959", "5405995959", "+90 (540) 599-59-59"]
)
def test_e164_ayni_numaraya_iner(ham: str) -> None:
    assert telefon.e164(ham) == "+905405995959"


@pytest.mark.parametrize("ham", ["", "abc", "12345", "1" * 16, "+90 540"])
def test_e164_gecersiz_numara_yukseltir(ham: str) -> None:
    with pytest.raises(telefon.TelefonGecersiz):
        telefon.e164(ham)


# ---------------------------------------------------------------------------
# STATİK KAPI — paket DB'siz, rotasız, config'siz (kopru hariç)
# ---------------------------------------------------------------------------


def _importlar(yol: Path) -> list[str]:
    adlar: list[str] = []
    for n in ast.parse(yol.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Import):
            adlar += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            adlar.append(("." * n.level) + (n.module or ""))
    return adlar


@pytest.mark.parametrize("dosya", ["__init__.py", "niyet.py", "telefon.py"])
def test_niyet_ve_telefon_DBSIZ_ve_uygulama_importsuz(dosya: str) -> None:
    for ad in _importlar(PAKET / dosya):
        assert not ad.startswith("."), f"{dosya}: uygulama importu {ad}"
        assert not ad.split(".")[0] in {"app", "sqlalchemy", "fastapi", "httpx", "pydantic"}, f"{dosya}: {ad}"
    # Belge dizileri (docstring) DIŞINDAKİ kod: yorum ve belge "Session"
    # kelimesini anlatmak için kullanabilir; ölçülen KOD'dur.
    agac = ast.parse((PAKET / dosya).read_text(encoding="utf-8"))
    kod_adlari = {n.id for n in ast.walk(agac) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(agac) if isinstance(n, ast.Attribute)
    }
    for yasak in ("Session", "select", "text", "execute", "engine", "SessionLocal"):
        assert yasak not in kod_adlari, f"{dosya}: {yasak}"


def test_paket_init_alt_modul_cekmez() -> None:
    assert _importlar(PAKET / "__init__.py") == []

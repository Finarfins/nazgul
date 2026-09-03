"""FEFO seçicisinin birim testleri (`app/parti.py`). SEÇİCİYİ ÇAĞIRAN YOK.

Bu dosya ile `test_parti_skt_postgresql.py`, seçicinin TEK kapsamıdır — ne
bir yol ne bir ekran onu çağırıyor. Bu yüzden buradaki testlerin AYIRT EDİCİ
olması normalden kritiktir: yanlış bir seçici, başka HİÇBİR yerde kırmızı
üretmez.

--- HER MEKANİZMA KENDİ TESTİYLE ANILIR -----------------------------------

`test_birim_donusumu.py`nin dersi burada uygulanıyor: bir test, ADINI
TAŞIDIĞI mekanizmayı çalıştırmadan da geçebilir. Bu yüzden aşağıdaki dört
mekanizmanın her biri MUTASYONLA kırmızıya çevrilerek gösterildi ve mutasyon
ile testin adı kayıtta EŞLEŞTİRİLDİ:

  1. NULL-SON sıralaması   -> `test_SKT_SIZ_parti_EN_SONA_dusar_...`
  2. Süresi geçmiş reddi   -> `test_SURESI_GECMIS_parti_VARSAYILAN_...`
  3. Sonluluk kapısı       -> `test_SONLU_OLMAYAN_istenen_REDDEDILIR_...`
  4. `CHECK quantity >= 0` -> PG ikizinde (SQLite'ta ölçülemez)

Dördüncüsü BURADA DEĞİL, `test_parti_skt_postgresql.py`dedir ve sebebi
göçte yazılı: SQLite'ın `NUMERIC`i ölçek/tür dayatmaz, yani NaN yarısı orada
başka bir şey ölçerdi.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from app import units
from app.parti import (
    URUN_KUANTUM,
    Parti,
    ParticiYetersiz,
    PartiSecilemedi,
    Secim,
    fefo_sec,
)

# DONMUŞ GÜN. `date.today()` bu dosyada GEÇMEZ ve geçmemesi testin kendi
# sözleşmesidir: takvime bağlı bir SKT testi yazıldığı gün yeşil, altı ay
# sonra kırmızı olurdu ve kırmızılığı kusuru DEĞİL takvimi gösterirdi.
BUGUN = date(2026, 9, 3)
DAMGA = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _p(
    kimlik: int,
    miktar: str,
    skt: date | None = None,
    olusma: datetime | None = None,
) -> Parti:
    return Parti(kimlik, Decimal(miktar), skt, olusma or DAMGA)


# ===========================================================================
# 1. SIRA — FEFO'nun tanımı ve DÖRT anahtarı
# ===========================================================================

def test_EN_ERKEN_SKT_ONCE_cikar_FEFO_tanimi() -> None:
    """Tanımın kendisi: bozulmaya en yakın olan önce çıkar."""
    partiler = [
        _p(1, "10", date(2027, 1, 1)),
        _p(2, "10", date(2026, 10, 1)),
        _p(3, "10", date(2026, 12, 1)),
    ]
    secim = fefo_sec(partiler, Decimal("30"), bugun=BUGUN)
    assert [kimlik for kimlik, _ in secim.dagitim] == [2, 3, 1]


def test_SKT_SIZ_parti_EN_SONA_dusar_NULL_ONE_alinamaz() -> None:
    """MUTASYON 1'İN ADRESİ — NULL-SON kuralı silinirse BURASI kırmızı olur.

    SKT'si OLMAYAN parti, SKT'si EN UZAK olandan bile SONRA gelir. Ölçüm bu
    yüzden UZAK bir tarihle yapılıyor: yakın bir tarihle yapılsaydı NULL'un
    sona düşmesi ile "geç tarih sona düşer" birbirinden AYIRT EDİLEMEZDİ.

    Tersi (NULL'u başa koymak) bozulabilir malı rafta BEKLETİRDİ ve göç
    0067'nin düzeltmek için var olduğu kusuru ÜRETİRDİ.
    """
    partiler = [
        _p(1, "10", None),
        _p(2, "10", date(2099, 12, 31)),
    ]
    secim = fefo_sec(partiler, Decimal("20"), bugun=BUGUN)
    assert [kimlik for kimlik, _ in secim.dagitim] == [2, 1], (
        "SKT'siz parti ÖNE geçti; NULL-son kuralı yok"
    )

    # Ve KISMİ bir istek YALNIZ tarihli olandan karşılanır: sıranın sonucu
    # yalnız listede değil, DAĞITIMDA da görünsün.
    kismi = fefo_sec(partiler, Decimal("10"), bugun=BUGUN)
    assert kismi.dagitim == ((2, Decimal("10")),)


def test_AYNI_SKT_ise_ONCE_GIREN_cikar_created_at_esitlik_bozucusu() -> None:
    """İkinci anahtar: aynı SKT'de FIFO. Rafta uzun duran önce çıkar."""
    erken = DAMGA
    gec = DAMGA + timedelta(days=30)
    partiler = [
        _p(1, "10", date(2026, 12, 1), gec),
        _p(2, "10", date(2026, 12, 1), erken),
    ]
    secim = fefo_sec(partiler, Decimal("20"), bugun=BUGUN)
    assert [kimlik for kimlik, _ in secim.dagitim] == [2, 1]


def test_AYNI_SKT_ve_AYNI_DAMGA_ise_id_BELIRLENIMCI_kilar() -> None:
    """Üçüncü anahtar: toplu içe aktarma aynı damgayı basar; sıra yine de
    KARARLI olmalı — kararsız bir seçici geri çağırma kaydını sorgulanamaz
    yapar. Giriş sırası TERS verilerek ölçülüyor."""
    partiler = [
        _p(9, "10", date(2026, 12, 1)),
        _p(4, "10", date(2026, 12, 1)),
        _p(7, "10", date(2026, 12, 1)),
    ]
    secim = fefo_sec(partiler, Decimal("30"), bugun=BUGUN)
    assert [kimlik for kimlik, _ in secim.dagitim] == [4, 7, 9]


def test_SIRA_giris_sirasindan_BAGIMSIZ_ayni_cevap() -> None:
    """Aynı küme hangi sırayla verilirse verilsin AYNI dağıtım.

    Belirlenimcilik bir iddiadır ve iddia ölçülür: giriş permütasyonu
    değiştiğinde cevap değişiyorsa sıra bir anahtara değil, LİSTE SIRASINA
    dayanıyor demektir.
    """
    partiler = [
        _p(1, "5", date(2026, 10, 1)),
        _p(2, "5", None),
        _p(3, "5", date(2026, 11, 1)),
    ]
    beklenen = fefo_sec(partiler, Decimal("15"), bugun=BUGUN).dagitim
    for permutasyon in ([2, 3, 1], [3, 1, 2], [2, 1, 3]):
        karisik = [partiler[i - 1] for i in permutasyon]
        assert fefo_sec(karisik, Decimal("15"), bugun=BUGUN).dagitim == beklenen


# ===========================================================================
# 2. SÜRESİ GEÇMİŞ — sessizce seçilmez, ama GİZLENMEZ de
# ===========================================================================

def test_SURESI_GECMIS_parti_VARSAYILAN_olarak_SECILMEZ() -> None:
    """MUTASYON 2'NİN ADRESİ — süresi geçmiş reddi silinirse BURASI kırmızı.

    Süresi geçmiş parti dağıtımın DIŞINDADIR, ama `suresi_gecmis` içinde
    RAPOR EDİLİR: "mal yok" ile "mal var ama süresi geçmiş" aynı şey değildir.
    """
    gecmis = _p(1, "10", BUGUN - timedelta(days=1))
    saglam = _p(2, "10", BUGUN + timedelta(days=30))
    secim = fefo_sec([gecmis, saglam], Decimal("10"), bugun=BUGUN)

    assert secim.dagitim == ((2, Decimal("10")),), (
        "süresi geçmiş parti dağıtıma girdi"
    )
    assert secim.suresi_gecmis == (gecmis,), (
        "süresi geçmiş parti raporlanmadı; red bilgiyi YOK ETTİ"
    )


def test_SURESI_GECMIS_parti_SKT_SIZDEN_bile_ONCE_GELECEKKEN_dislaniyor() -> None:
    """Reddin sıradan BAĞIMSIZ olduğunu ölçer.

    Süresi geçmiş parti FEFO sırasında EN BAŞTA olurdu (tarihi en erken).
    Yani "seçilmedi" sonucu, onun sıraya hiç girmemesinden değil, AYRICA
    DIŞLANMASINDAN geliyor. Sıralamaya güvenip dışlamayı silen biri burada
    kırmızı alır.
    """
    gecmis = _p(1, "10", BUGUN - timedelta(days=365))
    sktsiz = _p(2, "10", None)
    secim = fefo_sec([gecmis, sktsiz], Decimal("10"), bugun=BUGUN)
    assert secim.dagitim == ((2, Decimal("10")),)


def test_IZIN_VERILINCE_suresi_gecmis_SECILIR_ve_YINE_raporlanir() -> None:
    """`izin_ver_suresi_gecmis=True`: seçime girer, ama rapor KAYBOLMAZ.

    Bayrak AÇIKÇA yazılmak zorundadır — varsayılan olsaydı süresi geçmiş mal
    kimse fark etmeden çıkardı.
    """
    gecmis = _p(1, "10", BUGUN - timedelta(days=1))
    saglam = _p(2, "10", BUGUN + timedelta(days=30))
    secim = fefo_sec(
        [gecmis, saglam], Decimal("15"), bugun=BUGUN, izin_ver_suresi_gecmis=True
    )
    assert secim.dagitim == ((1, Decimal("10")), (2, Decimal("5")))
    assert secim.suresi_gecmis == (gecmis,), (
        "izin verildiğinde rapor DÜŞTÜ; çağıran ne kullandığını bilemez"
    )


def test_SKT_si_BUGUN_olan_parti_SURESI_GECMIS_SAYILMAZ_sinir_dar() -> None:
    """`< bugun`, `<=` DEĞİL: SKT SON KULLANILABİLİR GÜNDÜR.

    `<=` yazan biri burada kırmızı alır. Sınırın dar olması bir karardır:
    bugünü dışarıda bırakmak, kullanılabilir malı bir gün erken imhaya
    yollardı.
    """
    bugun_biten = _p(1, "10", BUGUN)
    secim = fefo_sec([bugun_biten], Decimal("10"), bugun=BUGUN)
    assert secim.dagitim == ((1, Decimal("10")),)
    assert secim.suresi_gecmis == ()

    # DÜN biten AYNI parti, aynı çağrıda süresi geçmiş SAYILIR — sınırın
    # hangi tarafta olduğu tek bir günle ölçülüyor.
    dun_biten = _p(1, "10", BUGUN - timedelta(days=1))
    assert fefo_sec(
        [dun_biten], Decimal("0"), bugun=BUGUN
    ).suresi_gecmis == (dun_biten,)


def test_SKT_SIZ_parti_ASLA_suresi_gecmis_olmaz() -> None:
    """NULL "tarihi YOKTUR" der; tarihi olmayan bir şey geçemez."""
    sktsiz = _p(1, "10", None)
    secim = fefo_sec([sktsiz], Decimal("10"), bugun=date(2999, 1, 1))
    assert secim.suresi_gecmis == ()
    assert secim.dagitim == ((1, Decimal("10")),)


# ===========================================================================
# 3. BÖLÜŞTÜRME — toplam TAM, yuvarlama YOK
# ===========================================================================

def test_DAGITIM_TOPLAMI_istenene_TAM_ESIT_yuvarlama_farki_YOK() -> None:
    """Seçici yalnız BÖLÜŞTÜRÜR; çarpma yoktur, yeni basamak DOĞMAZ."""
    partiler = [
        _p(1, "3.3333", date(2026, 10, 1)),
        _p(2, "3.3333", date(2026, 11, 1)),
        _p(3, "3.3334", date(2026, 12, 1)),
    ]
    istenen = Decimal("10.0000")
    secim = fefo_sec(partiler, istenen, bugun=BUGUN)
    toplam = sum((pay for _, pay in secim.dagitim), Decimal("0"))
    assert toplam == istenen
    # `==` sayısal eşitliktir; ÖLÇEĞİN de korunduğunu METİN söyler.
    assert str(toplam) == "10.0000", str(toplam)


def test_SON_PARTIDEN_yalniz_KALAN_kadar_dusulur_fazlasi_DEGIL() -> None:
    """Kısmi pay: son parti tamamen tüketilmez."""
    partiler = [
        _p(1, "4", date(2026, 10, 1)),
        _p(2, "10", date(2026, 11, 1)),
    ]
    secim = fefo_sec(partiler, Decimal("7"), bugun=BUGUN)
    assert secim.dagitim == ((1, Decimal("4")), (2, Decimal("3")))


def test_ISTEK_KARSILANINCA_kalan_partilere_DOKUNULMAZ() -> None:
    """Yeterince mal bulununca sıradaki partiler dağıtıma HİÇ girmez."""
    partiler = [
        _p(1, "10", date(2026, 10, 1)),
        _p(2, "10", date(2026, 11, 1)),
        _p(3, "10", date(2026, 12, 1)),
    ]
    secim = fefo_sec(partiler, Decimal("10"), bugun=BUGUN)
    assert secim.dagitim == ((1, Decimal("10")),)


def test_MIKTARI_SIFIR_parti_dagitima_GIRMEZ_bos_satir_uretmez() -> None:
    """Sıfırlık pay, yazılacak hiçbir şeyi olmayan bir hareket satırı olurdu.

    Satırın KENDİSİ silinmez (göç `CHECK quantity >= 0`), yalnız seçimden
    düşer — tükenmiş parti geri çağırmanın kanıtıdır.
    """
    partiler = [
        _p(1, "0", date(2026, 10, 1)),
        _p(2, "10", date(2026, 11, 1)),
    ]
    secim = fefo_sec(partiler, Decimal("10"), bugun=BUGUN)
    assert secim.dagitim == ((2, Decimal("10")),), (
        "sıfır miktarlı parti dağıtımda bir satır üretti"
    )


def test_ISTENEN_SIFIR_bos_dagitim_verir_RED_YOK() -> None:
    """Gerçek bir sıfır gerçek bir istektir (`units.resolve` ile aynı duruş)."""
    secim = fefo_sec([], Decimal("0"), bugun=BUGUN)
    assert secim == Secim((), ())
    # Parti VARKEN de sıfır istek boş dağıtım verir.
    assert fefo_sec(
        [_p(1, "10", date(2026, 10, 1))], Decimal("0"), bugun=BUGUN
    ).dagitim == ()


# ===========================================================================
# 4. YETERSİZLİK — bir İŞ DURUMU, ve kanıt ÜZERİNDE taşınır
# ===========================================================================

def test_YETERSIZ_parti_ParticiYetersiz_atar_ve_KANITI_tasir() -> None:
    partiler = [_p(1, "4", date(2026, 10, 1))]
    with pytest.raises(ParticiYetersiz) as yakalanan:
        fefo_sec(partiler, Decimal("10"), bugun=BUGUN)
    hata = yakalanan.value
    assert hata.sebep == PartiSecilemedi.PARTI_YETERSIZ
    assert hata.istenen == Decimal("10")
    assert hata.mevcut == Decimal("4")
    assert hata.eksik == Decimal("6")


def test_YETERSIZLIK_SURESI_GECMIS_MAL_VARKEN_bunu_SOYLER() -> None:
    """"Mal yok" ile "mal var ama süresi geçmiş" AYNI ŞEY DEĞİLDİR.

    İki cümle operatörü iki farklı işe yollar (satın alma / imha) ve
    ikincisini yalnız bu alan söyleyebilir. `suresi_gecmis` istisnanın
    üzerinden düşerse red, bilgiyi yok etmiş olur.
    """
    gecmis = _p(1, "100", BUGUN - timedelta(days=1))
    with pytest.raises(ParticiYetersiz) as yakalanan:
        fefo_sec([gecmis], Decimal("10"), bugun=BUGUN)
    hata = yakalanan.value
    assert hata.mevcut == Decimal("0")
    assert hata.suresi_gecmis == (gecmis,)
    assert "SÜRESİ GEÇMİŞ" in str(hata), str(hata)


def test_YETERSIZLIK_ParticiYetersiz_AILE_ICINDEDIR() -> None:
    """Tek bir `except PartiSecilemedi:` hepsini yakalamalı."""
    assert issubclass(ParticiYetersiz, PartiSecilemedi)
    with pytest.raises(PartiSecilemedi):
        fefo_sec([], Decimal("1"), bugun=BUGUN)


def test_IZIN_VERILINCE_YETERSIZLIK_KALKAR_ayni_girdi_ayni_mal() -> None:
    """Aynı depo, aynı istek: bayrak reddi karara çevirir.

    İki çağrının FARKI yalnız bayraktır; yani red bir VERİ yokluğundan değil,
    bir KARARDAN geliyor. Bu ayrım testte görünür olmalı.
    """
    gecmis = _p(1, "100", BUGUN - timedelta(days=1))
    with pytest.raises(ParticiYetersiz):
        fefo_sec([gecmis], Decimal("10"), bugun=BUGUN)
    secim = fefo_sec(
        [gecmis], Decimal("10"), bugun=BUGUN, izin_ver_suresi_gecmis=True
    )
    assert secim.dagitim == ((1, Decimal("10")),)


# ===========================================================================
# 5. SONLULUK KAPISI — MUTASYON 3'ÜN ADRESİ
# ===========================================================================

@pytest.mark.parametrize("ham", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_SONLU_OLMAYAN_istenen_REDDEDILIR_ISTENEN_GECERSIZ(ham: str) -> None:
    """MUTASYON 3'ÜN ADRESİ — sonluluk kapısı silinirse BURASI kırmızı.

    NaN ve sonsuzluk ÖLÇÜLMEMİŞ sayılardır; bir stok isteği olamazlar.
    """
    with pytest.raises(PartiSecilemedi) as yakalanan:
        fefo_sec([_p(1, "10", None)], Decimal(ham), bugun=BUGUN)
    assert yakalanan.value.sebep == PartiSecilemedi.ISTENEN_GECERSIZ


@pytest.mark.parametrize("ham", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_SONLU_OLMAYAN_PARTI_MIKTARI_reddedilir_DEFTER_kusurudur(ham: str) -> None:
    """AYRI SEBEP, ve ayrımın gerekçesi ÇARENİN farklı olmasıdır.

    `needed` bozuksa operatör YENİDEN GİRER; bir partinin `quantity`si
    bozuksa yeniden girmek onu DÜZELTMEZ — DEFTERİN düzeltilmesi gerekir.
    Tek bir sebep altında birleşselerdi çağıran ikisini ayıramazdı.
    """
    with pytest.raises(PartiSecilemedi) as yakalanan:
        fefo_sec([_p(1, ham, None)], Decimal("1"), bugun=BUGUN)
    assert yakalanan.value.sebep == PartiSecilemedi.PARTI_MIKTARI_GECERSIZ


@pytest.mark.parametrize("ham", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_SONLU_OLMAYAN_red_AILE_ICINDEDIR_InvalidOperation_SIZMAZ(
    ham: str,
) -> None:
    """Belgelenen sözleşme `except PartiSecilemedi:`tir; ondan KAÇAN bir
    istisna, çağıran için reddin HİÇ OLMAMASIYLA aynı kapıdır.

    #27'nin ölçtüğü kusur tam buydu: `decimal.InvalidOperation` aile DIŞINDA
    kalıyordu. Burada iki rol de ayrı ayrı sınanıyor.
    """
    for cagri in (
        lambda: fefo_sec([_p(1, "10", None)], Decimal(ham), bugun=BUGUN),
        lambda: fefo_sec([_p(1, ham, None)], Decimal("1"), bugun=BUGUN),
    ):
        try:
            cagri()
        except PartiSecilemedi:
            pass
        except InvalidOperation as hata:  # pragma: no cover - kusur hâli
            pytest.fail(
                f"{ham}: `decimal.InvalidOperation` AİLE DIŞINA sızdı ({hata!r})"
            )
        else:  # pragma: no cover - kusur hâli
            pytest.fail(f"{ham}: hiç reddedilmedi")


def test_SONLULUK_denetimi_KARSILASTIRMADAN_ONCE_gelir_olcum() -> None:
    """SIRANIN ZORUNLULUĞU, iddia değil ÖLÇÜM olarak.

    `Decimal("NaN") < 0` KARŞILAŞTIRMANIN KENDİSİ `InvalidOperation` atar —
    aşağıda ölçülüyor. Yani sonluluk denetimi `< 0`dan SONRA konsaydı NaN
    ona HİÇ ULAŞAMAZ ve aile dışı istisna sızmaya DEVAM ederdi.
    """
    with pytest.raises(InvalidOperation):
        _ = Decimal("NaN") < 0
    # Seçici ise AYNI değer için aile içinde reddediyor.
    with pytest.raises(PartiSecilemedi):
        fefo_sec([], Decimal("NaN"), bugun=BUGUN)


@pytest.mark.parametrize("ham", ["-1", "-0.0001"])
def test_NEGATIF_istenen_REDDEDILIR(ham: str) -> None:
    with pytest.raises(PartiSecilemedi) as yakalanan:
        fefo_sec([], Decimal(ham), bugun=BUGUN)
    assert yakalanan.value.sebep == PartiSecilemedi.ISTENEN_GECERSIZ


def test_NEGATIF_PARTI_MIKTARI_REDDEDILIR_eksi_mal_olamaz() -> None:
    with pytest.raises(PartiSecilemedi) as yakalanan:
        fefo_sec([_p(1, "-5", None)], Decimal("1"), bugun=BUGUN)
    assert yakalanan.value.sebep == PartiSecilemedi.PARTI_MIKTARI_GECERSIZ


@pytest.mark.parametrize("deger", [1.5, 1, "10", None])
def test_float_ve_diger_tipler_REDDEDILIR_ikili_kayan_nokta_GIREMEZ(deger) -> None:
    """`int` de reddedilir ve bu bilinçli: `Decimal` olmayan bir sayı, ona
    yapılacak ilk aritmetikte tipi BULAŞTIRIR."""
    with pytest.raises(PartiSecilemedi) as yakalanan:
        fefo_sec([], deger, bugun=BUGUN)
    assert yakalanan.value.sebep == PartiSecilemedi.ISTENEN_GECERSIZ


def test_YINELENEN_parti_kimligi_REDDEDILIR_iki_kez_dusulmesin() -> None:
    """Aynı satırın iki kopyası ondan İKİ KEZ düşülmesine yol açardı ve
    toplam elde olandan FAZLA çıkardı."""
    parti = _p(1, "10", None)
    with pytest.raises(PartiSecilemedi) as yakalanan:
        fefo_sec([parti, parti], Decimal("15"), bugun=BUGUN)
    assert yakalanan.value.sebep == PartiSecilemedi.PARTI_YINELENEN_KIMLIK


def test_GIRDI_DENETIMI_yetersizlikten_ONCE_gelir() -> None:
    """Bozuk bir defter satırı, "mal yetmiyor" diye RAPORLANMAMALI.

    Sıra tersine dönseydi bozuk bir `quantity` sessizce 0 sayılır ve hata
    `ParticiYetersiz` olarak görünürdü — çağıran defteri düzeltmesi
    gerektiğini ASLA öğrenemezdi.
    """
    with pytest.raises(PartiSecilemedi) as yakalanan:
        fefo_sec([_p(1, "NaN", None)], Decimal("999"), bugun=BUGUN)
    assert yakalanan.value.sebep == PartiSecilemedi.PARTI_MIKTARI_GECERSIZ


# ===========================================================================
# 6. SAFLIK — saat OKUNMAZ, ölçek İTHAL EDİLİR
# ===========================================================================

def test_SECICI_SAAT_OKUMAZ_kaynakta_today_GECMEZ() -> None:
    """`bugun` zorunlu argümandır; kaynakta `date.today` GEÇMEMELİ.

    Bu bir statik kapıdır ve bilinçlidir: davranışsal bir test bugünün
    tarihiyle tesadüfen yeşil kalabilir. `today()` ekleyen biri BURADA
    kırmızı alır, altı ay sonra başka bir testte değil.
    """
    kaynak = (
        Path(__file__).resolve().parents[1] / "app" / "parti.py"
    ).read_text(encoding="utf-8")
    govde = kaynak.split('"""', 2)[2]  # başlık düzyazısı hariç
    assert "today(" not in govde, "seçici saati OKUYOR; saflık kırıldı"
    assert "now(" not in govde, "seçici saati OKUYOR; saflık kırıldı"


def test_AYNI_GIRDI_AYNI_CEVAP_bugun_degisince_YALNIZ_SKT_degisir() -> None:
    """Saflığın davranışsal yüzü: cevabı yalnız ARGÜMANLAR belirler."""
    partiler = [_p(1, "10", date(2026, 10, 1)), _p(2, "10", None)]
    birinci = fefo_sec(partiler, Decimal("15"), bugun=BUGUN)
    ikinci = fefo_sec(partiler, Decimal("15"), bugun=BUGUN)
    assert birinci == ikinci

    # `bugun` ileri alınınca AYNI parti süresi geçmişe düşer — tek değişen
    # argüman, tek değişen sonuç.
    sonra = fefo_sec(partiler, Decimal("10"), bugun=date(2027, 1, 1))
    assert sonra.suresi_gecmis == (partiler[0],)
    assert sonra.dagitim == ((2, Decimal("10")),)


def test_URUN_KUANTUM_units_ten_ITHAL_EDILDI_ikinci_kopya_YOK() -> None:
    """#27 ikinci bir kopyanın nasıl ayrıştığını Türkçe katlama üzerinden
    ölçtü. Aynı hatayı sayısal bir sabit için tekrarlamak daha da kötü
    olurdu: iki ölçek sessizce ayrışırsa hangi sayının doğru olduğu
    sorulamaz."""
    assert URUN_KUANTUM is units.URUN_KUANTUM
    assert URUN_KUANTUM == Decimal("0.0001")

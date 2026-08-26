"""Hayvancılık V1 — FAZ 4: yaşa bağlı zorunlu aşı takvimi.

Konu: mobil-erp#17.

Bu modül SAF hesap: veritabanına dokunmuyor, "bugün"ü parametre alıyor. Sebebi
test edilebilirlik değil, DOĞRULUK: takvim yaş üzerinden çalıştığı için "bugün"
her yerde AYNI olmalı; her fonksiyon kendi `date.today()`'ini çağırsaydı gece
yarısını geçen bir istek aynı yanıt içinde iki farklı güne göre hesap yapardı.

--- DÖRT KARAR VE GEREKÇELERİ --------------------------------------------

1. **ARALIK ARALIK OLARAK KALIR, TEK TARİHE İNDİRİLMEZ.**
   Şap için kural "4–6 ayda bir", brusella ikinci doz için "4–12 ay sonra".
   Ortasını alıp tek bir tarih üretmek iki yönde de yanlış olurdu: erken
   seçilirse kullanıcı henüz zamanı gelmemiş aşı için uyarı alır ve uyarılara
   güvenmeyi bırakır; geç seçilirse zorunlu aşı penceresini kaçırır. Bu yüzden
   her hesap `due_from` ve `due_to` döndürüyor.

2. **BRUSELLA YALNIZ DİŞİLERDE.** Kaynak: dişilerde 3–6 aylıkken, ikinci doz
   4–12 ay sonra. Erkeklere de uygulasaydık sürünün yarısı sahte bir eksik
   listesine düşerdi.

3. **DOĞUM TARİHİ YOKSA TAKVİM HESAPLANMAZ — "tamam" DENMEZ.**
   Yaş bilinmeden pencere hesaplanamaz. Böyle bir hayvanı "aşısı tam" saymak
   sessiz bir yanlış güvence olurdu; `UNKNOWN` durumu döndürülüp sebebi
   yazılıyor ki kullanıcı doğum tarihini girmeye yönlendirilsin.

4. **KODSUZ KAYIT HESABA GİRMEZ VE BU GİZLENMEZ.** Eşleşme `vaccine_code`
   üzerinden (bkz. migration 0050). Serbest metinden tahmin, 'FMD' yazılmış bir
   şap aşısını görmezden gelip sahte gecikme üretirdi. Kodsuz kayıt sayısı ucun
   yanıtında AYRICA veriliyor.

--- KODA GİRMEYEN ---------------------------------------------------------

"Aşısız anadan doğduysa ilk şap 14 günlük" kuralı UYGULANMIYOR. Uygulamak,
annenin doğumdan önceki aşı durumunu bilmeyi gerektiriyor; anne kaydı olmayan
(satın alınmış) hayvanlarda bu bilgi YOK ve "bilinmiyor"u "aşısız" saymak
sürünün büyük kısmı için 14 günlük pencere üretirdi — yani sahte aciliyet.
Standart pencere kullanılıyor ve hesabın dayanağı yanıtta `basis` ile
söyleniyor. Ana aşı durumuna bağlı erken pencere, anne aşı geçmişi güvenilir
biçimde toplandığında ayrı bir işte ele alınacak.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

#: Ay ≈ 30 gün. Takvim ayı kullanmıyoruz çünkü kural "4–6 ay" gibi YAKLAŞIK
#: aralıklar veriyor; gün bazlı sabit, ay sonu taşmasından (31 Ocak + 1 ay)
#: doğan kenar durumlarını da ortadan kaldırıyor.
GUN_AY = 30

#: Hesabı olan zorunlu aşıların kodları. Bu iki kod dışındaki aşılar takvime
#: girmez (bkz. modül başlığı: amaç zorunluları takip etmek).
FMD = "FMD"
BRUCELLA = "BRUCELLA"

TAKVIMLI_KODLAR = (FMD, BRUCELLA)

KOD_ADI = {FMD: "Şap", BRUCELLA: "Brusella"}


@dataclass(frozen=True)
class Pencere:
    """Bir dozun beklendiği ARALIK ve hesabın dayanağı."""

    code: str
    #: Kaçıncı doz bekleniyor (1 = hiç yapılmamış).
    dose_no: int
    due_from: date
    due_to: date
    #: Hesabın neye dayandığı — kullanıcı sayıya değil GEREKÇEYE güvenir.
    basis: str


@dataclass(frozen=True)
class Durum:
    """Bir hayvan + bir aşı için takvim durumu."""

    code: str
    #: UNKNOWN | UPCOMING | DUE | OVERDUE | NOT_APPLICABLE
    state: str
    dose_no: int | None
    due_from: date | None
    due_to: date | None
    last_applied_on: date | None
    basis: str
    #: Pencerenin sonu geçtiyse kaç gün geçtiği; değilse None.
    overdue_days: int | None


UNKNOWN = "UNKNOWN"
UPCOMING = "UPCOMING"
DUE = "DUE"
OVERDUE = "OVERDUE"
NOT_APPLICABLE = "NOT_APPLICABLE"


def _ay(gun_sayisi: int) -> timedelta:
    return timedelta(days=gun_sayisi * GUN_AY)


def _fmd_pencere(birth_date: date, dozlar: list[date]) -> Pencere:
    """Şap penceresi.

    Kural: ilk doz ~2 aylıkken, ikinci doz bir ay sonra, sonra ömür boyu
    4–6 ayda bir.

    İlk iki dozun ARALIĞI YOK (kaynak tek bir süre veriyor: "~2 aylıkken",
    "bir ay sonra"). Tek tarihi pencereye çevirmek için uydurma bir tolerans
    eklemek, kaynağın söylemediği bir şeyi söylemek olurdu; onun yerine o
    tarihten itibaren pencere AÇIK bırakılıyor — yani `due_to` = `due_from`
    değil, tekrar aralığının başladığı gün. Aşağıdaki yorumlara bakın.
    """
    if not dozlar:
        # İlk doz: 2 aylıkken. Pencerenin sonu, gecikmenin anlamlı hâle geldiği
        # gün olarak bir ay sonrası alınıyor (ikinci dozun beklendiği gün) —
        # o güne kadar yapılmamışsa takvim gerçekten kaymış olur.
        baslangic = birth_date + _ay(2)
        return Pencere(
            FMD, 1, baslangic, baslangic + _ay(1),
            "İlk şap dozu 2 aylıkken; doğum tarihinden hesaplandı",
        )
    son = max(dozlar)
    if len(dozlar) == 1:
        # İkinci doz: birincinin bir ay sonrası.
        baslangic = son + _ay(1)
        return Pencere(
            FMD, 2, baslangic, baslangic + _ay(1),
            "İkinci şap dozu ilk dozdan bir ay sonra",
        )
    # Ömür boyu tekrar: 4–6 ayda bir. ARALIK BURADA GERÇEK ve korunuyor.
    return Pencere(
        FMD, len(dozlar) + 1, son + _ay(4), son + _ay(6),
        "Şap tekrarı son dozdan 4–6 ay sonra",
    )


def _brucella_pencere(birth_date: date, dozlar: list[date]) -> Pencere:
    """Brusella penceresi — YALNIZ dişiler için çağrılır.

    Kural: ilk doz 3–6 aylıkken, ikinci doz 4–12 ay sonra. İkisi de GERÇEK
    aralık; ikisi de korunuyor.
    """
    if not dozlar:
        return Pencere(
            BRUCELLA, 1, birth_date + _ay(3), birth_date + _ay(6),
            "İlk brusella dozu 3–6 aylıkken; doğum tarihinden hesaplandı",
        )
    son = max(dozlar)
    if len(dozlar) == 1:
        return Pencere(
            BRUCELLA, 2, son + _ay(4), son + _ay(12),
            "İkinci brusella dozu ilk dozdan 4–12 ay sonra",
        )
    # İki doz tamamlandıktan sonra kaynakta ZORUNLU bir tekrar yok. Uydurma bir
    # tekrar aralığı eklemek, olmayan bir zorunluluk üretmek olurdu.
    return Pencere(
        BRUCELLA, len(dozlar), son, son,
        "İki doz tamamlandı; kaynakta zorunlu tekrar tanımlı değil",
    )


def durum_hesapla(
    *,
    code: str,
    sex: str,
    birth_date: date | None,
    dozlar: list[date],
    bugun: date,
) -> Durum:
    """Bir hayvan + bir aşı kodu için takvim durumu.

    `dozlar` = o aşı koduna ait uygulama tarihleri (sırasız olabilir).
    """
    if code == BRUCELLA and sex != "FEMALE":
        return Durum(
            code, NOT_APPLICABLE, None, None, None,
            max(dozlar) if dozlar else None,
            "Brusella zorunluluğu dişilerde", None,
        )

    son_doz = max(dozlar) if dozlar else None

    if birth_date is None:
        # "Tamam" DEMİYORUZ: yaş bilinmeden pencere hesaplanamaz ve bunu
        # gizlemek sessiz bir yanlış güvence olurdu.
        return Durum(
            code, UNKNOWN, None, None, None, son_doz,
            "Doğum tarihi girilmediği için takvim hesaplanamadı", None,
        )

    if code == BRUCELLA and len(dozlar) >= 2:
        pencere = _brucella_pencere(birth_date, dozlar)
        return Durum(
            code, NOT_APPLICABLE, pencere.dose_no, None, None, son_doz,
            pencere.basis, None,
        )

    pencere = _fmd_pencere(birth_date, dozlar) if code == FMD else _brucella_pencere(birth_date, dozlar)

    if bugun < pencere.due_from:
        state, gecikme = UPCOMING, None
    elif bugun <= pencere.due_to:
        state, gecikme = DUE, None
    else:
        state, gecikme = OVERDUE, (bugun - pencere.due_to).days

    return Durum(
        code, state, pencere.dose_no, pencere.due_from, pencere.due_to,
        son_doz, pencere.basis, gecikme,
    )


def hayvan_durumlari(
    *,
    sex: str,
    birth_date: date | None,
    dozlar_koda_gore: dict[str, list[date]],
    bugun: date,
) -> list[Durum]:
    """Bir hayvanın takvimi olan TÜM zorunlu aşıları için durum listesi."""
    return [
        durum_hesapla(
            code=code, sex=sex, birth_date=birth_date,
            dozlar=dozlar_koda_gore.get(code, []), bugun=bugun,
        )
        for code in TAKVIMLI_KODLAR
    ]

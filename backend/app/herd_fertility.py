"""Hayvancılık V1 — FAZ 5: döl verimi göstergeleri.

Konu: mobil-erp#17.

Bu modül de SAF hesap (bkz. `herd_vaccine_schedule`): veritabanına dokunmuyor,
"bugün"ü parametre alıyor.

--- NEDEN "KAÇ DOĞUM" TEK BAŞINA GÖSTERGE DEĞİL --------------------------

Konu #17'nin araştırma bölümündeki tespit: sürü yönetiminde asıl ölçüt doğum
SAYISI değil, doğumlar arası SÜRE. Yılda 40 doğum, 40 inekten geliyorsa iyi;
20 inekten geliyorsa ineklerin yarısı boş geçmiş demektir. Bu yüzden hesaplanan
şey buzağılama aralığı ve servis periyodu.

--- BEŞ KARAR VE GEREKÇELERİ --------------------------------------------

1. **ÖRNEK SAYISI HER GÖSTERGEYLE BİRLİKTE DÖNER.** Tek bir ineğin tek bir
   aralığı "sürünün buzağılama aralığı" değildir. Ortalamayı örnek sayısı
   olmadan göstermek, üç ölçümden çıkan bir sayıyı sürü gerçeği gibi sunmak
   olurdu; kullanıcı ona göre karar verirse zarar eder.

2. **HİÇ DOĞURMAMIŞ HAYVAN ORTALAMAYA 0 OLARAK GİRMEZ.** Girseydi ortalama
   aşağı çekilir ve düzelmesi imkânsız bir "kötü" tablo çıkardı. Onlar
   `never_calved` olarak AYRI sayılıyor — çünkü kendileri de bir bilgi.

3. **GEBELİK BAŞINA TOHUMLAMA YALNIZ BAĞLI KAYITLARDAN.** Hangi tohumlamanın
   hangi doğumu verdiği ancak `breeding_id` bağıyla BİLİNİR. Bağ yoksa
   "doğumdan ~9 ay önceki tohumlama" diye tahmin etmek gerekirdi — ama gebelik
   süresi için güvenilir kaynak BULUNAMADI (konu #17: bu değer sabit
   yazılmayacak). Tahmin, yanlış tohumlamayı doğuma bağlayıp göstergeyi sessizce
   bozardı. Bağsız doğum sayısı yanıtta AYRICA veriliyor.

4. **EŞİKLER KAYNAKTAN, UYDURMA DEĞİL.** Buzağılama aralığı hedefi ~12 ay,
   13 aydan uzunu sorun; servis periyodu 70–90 gün. Kaynakta olmayan bir eşik
   (ör. "üreme etkinliği %75–85"in formülü) HESAPLANMIYOR: yüzdeyi üretmek için
   gereken varsayımlar kaynakta yok ve uydurulmuş bir formül, kullanıcının
   güvendiği tek sayıyı yanlış üretirdi.

5. **AY = 30 GÜN, ve gösterge GÜN cinsinden.** Aralık gün olarak hesaplanıp
   gösterimde aya çevriliyor; takvim ayı kullanmak "13 aydan uzun" eşiğini ay
   sonu taşmalarına duyarlı hâle getirirdi.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

GUN_AY = 30

#: Buzağılama aralığı hedefi ve sorun eşiği (konu #17 araştırması).
HEDEF_ARALIK_GUN = 12 * GUN_AY
SORUN_ARALIK_GUN = 13 * GUN_AY

#: Servis periyodu (doğum → gebe kalan tohumlama) normal aralığı.
SERVIS_ALT_GUN = 70
SERVIS_UST_GUN = 90


@dataclass(frozen=True)
class HayvanDolVerimi:
    animal_id: int
    #: Kaydedilmiş (canlı ya da ölü) doğum olayı sayısı.
    calving_count: int
    #: Ardışık doğumlar arası ortalama gün. Tek doğumda None — "0" DEĞİL.
    avg_calving_interval_days: int | None
    #: En son iki doğum arası. Trend için: ortalama iyi ama son aralık kötü
    #: olabilir ve sorun ancak son aralıkta görünür.
    last_calving_interval_days: int | None
    #: İlkine buzağılama yaşı (gün). Doğum tarihi bilinmiyorsa None.
    age_at_first_calving_days: int | None
    #: Bağlı tohumlama-doğum çiftlerinden servis periyodu (doğum → sonraki
    #: gebe kalan tohumlama).
    service_period_days: int | None
    #: Bu hayvanın gebelik başına tohumlama sayısı; yalnız BAĞLI kayıtlardan.
    #:
    #: `Decimal`, `float` DEĞİL: bu depoda `app/` altında ikili kayan nokta
    #: TAMAMEN yasak (bkz. test_v2_9_decimal_contract.py) ve yasak blanket —
    #: "bu alan para değil, oran" diye istisna açmak bekçiyi zayıflatır, çünkü
    #: bir sonraki kişi aynı gerekçeyle para alanına da float koyabilir.
    services_per_conception: Decimal | None
    #: Aralık sorun eşiğini aşıyor mu (kaynak: >13 ay sorun).
    interval_is_problem: bool


def _ortalama(degerler: list[int]) -> int | None:
    return round(sum(degerler) / len(degerler)) if degerler else None


def hayvan_dol_verimi(
    *,
    animal_id: int,
    birth_date: date | None,
    dogum_tarihleri: list[date],
    #: (tohumlama tarihi, o tohumlamanın bağlandığı doğum tarihi) çiftleri.
    #: YALNIZ `breeding_id` ile açıkça bağlanmış kayıtlar buraya girer.
    bagli_tohumlamalar: list[tuple[date, date]],
    #: Bağlı olmayanlar dâhil, bu hayvanın tüm tohumlama sayısı — gebelik
    #: başına tohumlama için gerekli.
    gebe_sonuclanan: int,
    toplam_tohumlama: int,
) -> HayvanDolVerimi:
    dogumlar = sorted(dogum_tarihleri)

    araliklar = [
        (sonraki - onceki).days
        for onceki, sonraki in zip(dogumlar, dogumlar[1:])
    ]
    ortalama = _ortalama(araliklar)
    son = araliklar[-1] if araliklar else None

    ilkine = None
    if birth_date and dogumlar:
        fark = (dogumlar[0] - birth_date).days
        # Negatif ya da sıfır fark veri hatasıdır; göstergeye SOKMUYORUZ
        # (bir "0 günlük ilkine buzağılama" ortalamayı anlamsız kılar).
        ilkine = fark if fark > 0 else None

    # Servis periyodu: bağlı tohumlamanın tarihinden ÖNCEKİ doğuma uzaklık.
    # Yani "doğurduktan kaç gün sonra yeniden gebe kaldı".
    servisler: list[int] = []
    for tohum_gunu, _bagli_dogum in bagli_tohumlamalar:
        oncekiler = [d for d in dogumlar if d < tohum_gunu]
        if not oncekiler:
            # İlk gebeliğin öncesinde doğum yok; bu bir servis periyodu değil.
            continue
        servisler.append((tohum_gunu - max(oncekiler)).days)

    hizmet = None
    if gebe_sonuclanan > 0 and toplam_tohumlama > 0:
        hizmet = (Decimal(toplam_tohumlama) / Decimal(gebe_sonuclanan)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )

    return HayvanDolVerimi(
        animal_id=animal_id,
        calving_count=len(dogumlar),
        avg_calving_interval_days=ortalama,
        last_calving_interval_days=son,
        age_at_first_calving_days=ilkine,
        service_period_days=_ortalama(servisler),
        services_per_conception=hizmet,
        # Eşik SON aralığa bakıyor: ortalama iyi görünürken son aralık bozulmuş
        # olabilir ve müdahale edilecek yer orasıdır.
        interval_is_problem=son is not None and son > SORUN_ARALIK_GUN,
    )


@dataclass(frozen=True)
class SuruOzeti:
    #: Ortalamaların KAÇ ölçümden çıktığı. Bu alan olmadan ortalama gösterilmez.
    calving_interval_sample: int
    avg_calving_interval_days: int | None
    #: Aralığı 13 aydan uzun olan hayvan sayısı.
    problem_interval_count: int
    age_at_first_calving_sample: int
    avg_age_at_first_calving_days: int | None
    service_period_sample: int
    avg_service_period_days: int | None
    #: Hiç doğurmamış dişi sayısı — ortalamaya 0 olarak GİRMEZ, ayrı durur.
    never_calved: int
    #: Tohumlamaya bağlanmamış doğum sayısı. Gebelik başına tohumlama hesabının
    #: KAPSAMADIĞI kısım; gizlenirse gösterge tam sanılır.
    births_without_breeding_link: int


def suru_ozeti(
    kayitlar: list[HayvanDolVerimi],
    *,
    never_calved: int,
    births_without_breeding_link: int,
) -> SuruOzeti:
    araliklar = [
        k.avg_calving_interval_days for k in kayitlar
        if k.avg_calving_interval_days is not None
    ]
    ilkler = [
        k.age_at_first_calving_days for k in kayitlar
        if k.age_at_first_calving_days is not None
    ]
    servisler = [
        k.service_period_days for k in kayitlar if k.service_period_days is not None
    ]
    return SuruOzeti(
        calving_interval_sample=len(araliklar),
        avg_calving_interval_days=_ortalama(araliklar),
        problem_interval_count=sum(1 for k in kayitlar if k.interval_is_problem),
        age_at_first_calving_sample=len(ilkler),
        avg_age_at_first_calving_days=_ortalama(ilkler),
        service_period_sample=len(servisler),
        avg_service_period_days=_ortalama(servisler),
        never_calved=never_calved,
        births_without_breeding_link=births_without_breeding_link,
    )

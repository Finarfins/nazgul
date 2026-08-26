"""Hayvancılık V1 — FAZ 5: döl verimi hesabının çivilenmesi.

Konu: mobil-erp#17.

Beş iddia; hepsi sessizce bozulabilir ve bozulduğunda ürettiği şey "biraz
yanlış ortalama" değil, kullanıcının yanlış hayvana müdahale etmesi:

1. **Tek doğumda aralık YOKTUR, 0 değildir.** 0 dönerse o hayvan "mükemmel
   aralık" gibi görünür ve ortalamayı sahte biçimde iyileştirir.
2. **Hiç doğurmamış hayvan ortalamaya girmez.** Girerse ortalama düzelmesi
   imkânsız biçimde bozulur; ayrı sayılır çünkü kendisi de bilgidir.
3. **Sorun eşiği SON aralığa bakar.** Ortalama iyi görünürken son aralık
   bozulmuş olabilir; müdahale edilecek yer orasıdır.
4. **Gebelik başına tohumlama yalnız BAĞLI kayıtlardan.** Bağsız doğum sayısı
   ayrı raporlanır; tahmin gebelik süresi varsayımı gerektirir ve o değer için
   güvenilir kaynak YOK.
5. **Örnek sayısı her ortalamayla birlikte döner.** Üç ölçümden çıkan bir sayıyı
   sürü gerçeği gibi sunmak, kullanıcıyı yanlış karara götürür.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.herd_fertility import (  # noqa: E402
    SORUN_ARALIK_GUN,
    hayvan_dol_verimi,
    suru_ozeti,
)

DOGDU = date(2020, 1, 1)


def test_tek_dogumda_aralik_yok_sifir_degil() -> None:
    """Bir doğumlu hayvanın aralığı None — 0 DEĞİL.

    0 dönerse o hayvan "0 günlük buzağılama aralığı" ile mükemmel görünür ve
    sürü ortalamasını sahte biçimde aşağı (iyi) çeker.
    """
    kayit = hayvan_dol_verimi(
        animal_id=1, birth_date=DOGDU, dogum_tarihleri=[date(2022, 6, 1)],
        bagli_tohumlamalar=[], gebe_sonuclanan=0, toplam_tohumlama=0,
    )
    assert kayit.calving_count == 1
    assert kayit.avg_calving_interval_days is None
    assert kayit.last_calving_interval_days is None
    assert kayit.interval_is_problem is False
    # İlkine buzağılama yaşı ise HESAPLANIR: tek doğum bunun için yeterli.
    assert kayit.age_at_first_calving_days == (date(2022, 6, 1) - DOGDU).days


def test_aralik_ortalamasi_ve_son_aralik_ayri() -> None:
    """Ortalama iyi, SON aralık kötü olabilir — ikisi ayrı raporlanır."""
    # 340 gün (iyi) sonra 430 gün (sorunlu) aralık. Ortalama 385 < 390 eşiği,
    # yani ORTALAMAYA bakan bir uygulama bu hayvanı sorunsuz sayardı.
    ilk_dogum = date(2022, 1, 1)
    dogumlar = [
        ilk_dogum,
        ilk_dogum + timedelta(days=340),
        ilk_dogum + timedelta(days=340 + 430),
    ]
    kayit = hayvan_dol_verimi(
        animal_id=1, birth_date=DOGDU, dogum_tarihleri=dogumlar,
        bagli_tohumlamalar=[], gebe_sonuclanan=0, toplam_tohumlama=0,
    )
    ilk = (dogumlar[1] - dogumlar[0]).days
    son = (dogumlar[2] - dogumlar[1]).days
    assert kayit.last_calving_interval_days == son
    assert kayit.avg_calving_interval_days == round((ilk + son) / 2)
    # Ortalama eşiğin ALTINDA ama son aralık üstünde: sorun İŞARETLENMELİ.
    assert kayit.avg_calving_interval_days < SORUN_ARALIK_GUN
    assert son > SORUN_ARALIK_GUN
    assert kayit.interval_is_problem is True, "son aralık sorunu gizlenmiş"


def test_dogum_sirasi_karisik_gelse_de_aralik_dogru() -> None:
    """Doğumlar sırasız gelirse hesap yine sıralı yapar.

    Sorgu `ORDER BY birth_date` ile okuyor ama hesap ona GÜVENMEMELİ: sıralamayı
    kaybeden bir düzenleme negatif aralık üretir ve gösterge sessizce saçmalar.
    """
    kayit = hayvan_dol_verimi(
        animal_id=1, birth_date=DOGDU,
        dogum_tarihleri=[date(2024, 3, 1), date(2022, 1, 1), date(2022, 12, 27)],
        bagli_tohumlamalar=[], gebe_sonuclanan=0, toplam_tohumlama=0,
    )
    assert kayit.avg_calving_interval_days is not None
    assert kayit.avg_calving_interval_days > 0
    assert kayit.age_at_first_calving_days == (date(2022, 1, 1) - DOGDU).days


def test_servis_periyodu_dogumdan_sonraki_tohumlamayi_olcer() -> None:
    """Servis periyodu = doğum → yeniden gebe kalan tohumlama."""
    dogum1, dogum2 = date(2022, 1, 1), date(2022, 12, 27)
    # İlk doğumdan 80 gün sonra tohumlanmış ve o tohumlama ikinci doğumu vermiş.
    tohum = dogum1 + timedelta(days=80)
    kayit = hayvan_dol_verimi(
        animal_id=1, birth_date=DOGDU, dogum_tarihleri=[dogum1, dogum2],
        bagli_tohumlamalar=[(tohum, dogum2)], gebe_sonuclanan=1, toplam_tohumlama=1,
    )
    assert kayit.service_period_days == 80

    # İLK gebeliğin öncesinde doğum yoksa bu bir servis periyodu DEĞİLDİR.
    ilk_gebelik = hayvan_dol_verimi(
        animal_id=2, birth_date=DOGDU, dogum_tarihleri=[dogum1],
        bagli_tohumlamalar=[(dogum1 - timedelta(days=280), dogum1)],
        gebe_sonuclanan=1, toplam_tohumlama=1,
    )
    assert ilk_gebelik.service_period_days is None, "düve ilk gebeliği servis sayılmış"


def test_gebelik_basina_tohumlama() -> None:
    """3 tohumlama / 1 gebelik = 3; gebelik yoksa None (0 DEĞİL).

    Sonuç `Decimal`: `app/` altında ikili kayan nokta yasak
    (test_v2_9_decimal_contract.py) ve yasağa "bu para değil, oran" istisnası
    açmak bekçiyi zayıflatırdı.
    """
    kayit = hayvan_dol_verimi(
        animal_id=1, birth_date=DOGDU, dogum_tarihleri=[date(2022, 1, 1)],
        bagli_tohumlamalar=[], gebe_sonuclanan=1, toplam_tohumlama=3,
    )
    assert kayit.services_per_conception == Decimal("3.00")

    gebelik_yok = hayvan_dol_verimi(
        animal_id=2, birth_date=DOGDU, dogum_tarihleri=[date(2022, 1, 1)],
        bagli_tohumlamalar=[], gebe_sonuclanan=0, toplam_tohumlama=4,
    )
    # 4/0 tanımsız; 0 döndürmek "hiç tohumlama gerekmedi" gibi okunurdu.
    assert gebelik_yok.services_per_conception is None


def test_ozet_ornek_sayisini_ve_kapsam_disini_verir() -> None:
    """Ortalama YANINDA örnek sayısı; hiç doğurmamış AYRI."""
    iki_dogumlu = hayvan_dol_verimi(
        animal_id=1, birth_date=DOGDU,
        dogum_tarihleri=[date(2022, 1, 1), date(2023, 1, 1)],
        bagli_tohumlamalar=[], gebe_sonuclanan=0, toplam_tohumlama=0,
    )
    tek_dogumlu = hayvan_dol_verimi(
        animal_id=2, birth_date=DOGDU, dogum_tarihleri=[date(2022, 5, 1)],
        bagli_tohumlamalar=[], gebe_sonuclanan=0, toplam_tohumlama=0,
    )
    ozet = suru_ozeti(
        [iki_dogumlu, tek_dogumlu], never_calved=5, births_without_breeding_link=3,
    )
    # Aralık ortalaması YALNIZ bir ölçümden çıkıyor ve bu görünür.
    assert ozet.calving_interval_sample == 1
    assert ozet.avg_calving_interval_days == 365
    # İlkine yaş İKİ ölçümden — farklı göstergelerin örnek sayısı farklı olabilir.
    assert ozet.age_at_first_calving_sample == 2
    # Hiç doğurmamışlar ortalamaya KARIŞMADI ama sayı kayboldu da değil.
    assert ozet.never_calved == 5
    assert ozet.births_without_breeding_link == 3


def test_bozuk_veri_ilkine_yasi_kirletmez() -> None:
    """Doğum tarihinden ÖNCE doğurmuş görünen kayıt göstergeye girmez.

    Veri girişi hatası; 0 ya da negatif bir "ilkine buzağılama yaşı" ortalamayı
    anlamsız kılar.
    """
    kayit = hayvan_dol_verimi(
        animal_id=1, birth_date=date(2023, 1, 1),
        dogum_tarihleri=[date(2022, 1, 1)],
        bagli_tohumlamalar=[], gebe_sonuclanan=0, toplam_tohumlama=0,
    )
    assert kayit.age_at_first_calving_days is None
    ozet = suru_ozeti([kayit], never_calved=0, births_without_breeding_link=0)
    assert ozet.age_at_first_calving_sample == 0
    assert ozet.avg_age_at_first_calving_days is None

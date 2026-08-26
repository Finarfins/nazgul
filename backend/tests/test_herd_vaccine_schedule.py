"""Hayvancılık V1 — FAZ 4: aşı takvimi hesabının çivilenmesi.

Konu: mobil-erp#17.

Buradaki testler saf hesabı sınıyor ve hepsi ARAŞTIRMADAN çıkan bir iddiayı
koruyor. Beşi de sessizce bozulabilir ve bozulduğunda ürettiği şey "biraz
yanlış tarih" değil, kullanıcının uyarı listesine güvenmeyi bırakması:

1. **Aralık aralık kalır.** Şap tekrarı 4–6 ay; tek tarihe indirilirse ya
   erken uyarı (güven kaybı) ya kaçırılmış zorunlu aşı olur.
2. **Brusella erkeklerde YOK.** Uygulanırsa sürünün yarısı sahte eksik listesine
   düşer.
3. **Doğum tarihi yoksa "tamam" DENMEZ.** Sessiz yanlış güvence.
4. **Pencere sınırları kapsayıcı.** `due_to` günü hâlâ zamanında sayılır;
   ertesi gün gecikmedir.
5. **İki doz tamamlanan brusellada uydurma tekrar YOK.** Kaynak zorunlu tekrar
   tanımlamıyor; tanımlamak olmayan bir zorunluluk üretmek olurdu.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.herd_vaccine_schedule import (  # noqa: E402
    BRUCELLA,
    DUE,
    FMD,
    GUN_AY,
    NOT_APPLICABLE,
    OVERDUE,
    UNKNOWN,
    UPCOMING,
    durum_hesapla,
    hayvan_durumlari,
)

DOGUM = date(2026, 1, 1)


def ay(n: int) -> timedelta:
    return timedelta(days=n * GUN_AY)


def test_sap_tekrari_aralik_olarak_kalir() -> None:
    """4–6 ay ARALIK; tek bir tarihe indirilmiyor.

    Bu testin asıl işi `due_from != due_to` iddiası. Biri "kullanıcıya tek
    tarih göstermek daha basit" diye ortalamayı alırsa test düşer.
    """
    dozlar = [DOGUM + ay(2), DOGUM + ay(3)]
    son = dozlar[-1]
    durum = durum_hesapla(
        code=FMD, sex="FEMALE", birth_date=DOGUM, dozlar=dozlar, bugun=son + ay(1),
    )
    assert durum.due_from == son + ay(4)
    assert durum.due_to == son + ay(6)
    assert durum.due_from != durum.due_to, "aralık tek tarihe indirilmiş"
    # Pencere henüz açılmadı.
    assert durum.state == UPCOMING
    assert durum.overdue_days is None
    # Gerekçe kullanıcıya gidiyor; boş bırakılamaz.
    assert "4–6" in durum.basis


def test_pencere_sinirlari_kapsayici() -> None:
    """`due_to` günü HÂLÂ zamanında; ertesi gün gecikme.

    Sınırı dışlayıcı yapmak, zorunlu aşıyı son gün yapan bir işletmeyi
    gecikmiş göstermek olurdu.
    """
    dozlar = [DOGUM + ay(2), DOGUM + ay(3)]
    son = dozlar[-1]
    ortak = {"code": FMD, "sex": "FEMALE", "birth_date": DOGUM, "dozlar": dozlar}

    assert durum_hesapla(**ortak, bugun=son + ay(4) - timedelta(days=1)).state == UPCOMING
    # Pencerenin İLK günü: zamanı geldi.
    assert durum_hesapla(**ortak, bugun=son + ay(4)).state == DUE
    # Pencerenin SON günü: hâlâ zamanında.
    assert durum_hesapla(**ortak, bugun=son + ay(6)).state == DUE
    # Bir gün sonrası: gecikme, ve gecikme BİR gün.
    gecikmis = durum_hesapla(**ortak, bugun=son + ay(6) + timedelta(days=1))
    assert gecikmis.state == OVERDUE
    assert gecikmis.overdue_days == 1


def test_brusella_erkekte_uygulanmaz() -> None:
    """Erkek hayvan brusella eksiği listesine DÜŞMEZ."""
    erkek = durum_hesapla(
        code=BRUCELLA, sex="MALE", birth_date=DOGUM, dozlar=[], bugun=DOGUM + ay(12),
    )
    assert erkek.state == NOT_APPLICABLE
    assert "dişi" in erkek.basis.lower()

    # Aynı yaştaki DİŞİ ise gecikmiş olmalı — testin dişleri olduğunu bu
    # karşılaştırma gösteriyor (yoksa "her şey NOT_APPLICABLE" de geçerdi).
    disi = durum_hesapla(
        code=BRUCELLA, sex="FEMALE", birth_date=DOGUM, dozlar=[], bugun=DOGUM + ay(12),
    )
    assert disi.state == OVERDUE
    assert disi.due_from == DOGUM + ay(3)
    assert disi.due_to == DOGUM + ay(6)


def test_dogum_tarihi_yoksa_tamam_denmez() -> None:
    """Yaş bilinmiyorsa durum UNKNOWN — "aşısı tam" DEĞİL."""
    durum = durum_hesapla(
        code=FMD, sex="FEMALE", birth_date=None, dozlar=[], bugun=date(2026, 8, 8),
    )
    assert durum.state == UNKNOWN
    assert durum.due_from is None and durum.due_to is None
    assert "doğum tarihi" in durum.basis.lower()
    # Kritik: UNKNOWN, "yapılacak iş yok" demek DEĞİL.
    assert durum.state != NOT_APPLICABLE


def test_brusella_iki_dozdan_sonra_uydurma_tekrar_yok() -> None:
    """İki doz tamam → zorunlu tekrar ÜRETİLMİYOR.

    Kaynakta iki dozdan sonra zorunlu tekrar tanımlı değil. Şap gibi bir
    tekrar aralığı eklemek, mevzuatta olmayan bir zorunluluk uydurmak olurdu.
    """
    dozlar = [DOGUM + ay(4), DOGUM + ay(10)]
    durum = durum_hesapla(
        code=BRUCELLA, sex="FEMALE", birth_date=DOGUM, dozlar=dozlar,
        bugun=DOGUM + ay(60),
    )
    assert durum.state == NOT_APPLICABLE, "olmayan bir zorunlu tekrar üretilmiş"
    assert durum.last_applied_on == dozlar[-1]

    # Şap ise AYNI koşulda tekrar üretir — fark bilinçli.
    sap = durum_hesapla(
        code=FMD, sex="FEMALE", birth_date=DOGUM, dozlar=dozlar, bugun=DOGUM + ay(60),
    )
    assert sap.state == OVERDUE


def test_ilk_dozlar_dogum_tarihinden_hesaplanir() -> None:
    """Hiç aşı yoksa pencere DOĞUM TARİHİNDEN çıkar."""
    sap = durum_hesapla(
        code=FMD, sex="FEMALE", birth_date=DOGUM, dozlar=[], bugun=DOGUM + ay(1),
    )
    assert sap.dose_no == 1
    assert sap.due_from == DOGUM + ay(2)
    assert sap.state == UPCOMING

    # 2 aylık olduğu gün: zamanı geldi.
    assert durum_hesapla(
        code=FMD, sex="FEMALE", birth_date=DOGUM, dozlar=[], bugun=DOGUM + ay(2),
    ).state == DUE


def test_doz_sirasi_karisik_gelse_de_son_doz_dogru() -> None:
    """Dozlar sırasız gelirse hesap EN SON tarihe göre yapılır.

    Uç `ORDER BY applied_on` ile okuyor ama hesap ona GÜVENMEMELİ: sıralamayı
    kaybeden bir sorgu düzenlemesi, takvimi sessizce ilk doza göre kaydırırdı.
    """
    dozlar = [DOGUM + ay(3), DOGUM + ay(2)]  # ters sırada
    durum = durum_hesapla(
        code=FMD, sex="FEMALE", birth_date=DOGUM, dozlar=dozlar, bugun=DOGUM + ay(8),
    )
    assert durum.last_applied_on == DOGUM + ay(3)
    assert durum.due_from == DOGUM + ay(3) + ay(4)


def test_hayvan_durumlari_iki_asiyi_da_verir() -> None:
    """Bir dişi hayvan için hem şap hem brusella durumu döner."""
    durumlar = hayvan_durumlari(
        sex="FEMALE", birth_date=DOGUM, dozlar_koda_gore={}, bugun=DOGUM + ay(7),
    )
    kodlar = {d.code for d in durumlar}
    assert kodlar == {FMD, BRUCELLA}
    # İkisi de gecikmiş: şap 2-3 ay, brusella 3-6 ay penceresi geçti.
    assert {d.state for d in durumlar} == {OVERDUE}

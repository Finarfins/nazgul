"""Hayvan ilaç bekleme süresi hesabı — süt kilidi (PR-1).

SAF hesap: veritabanına dokunmuyor, ``bugun`` parametresini alıyor. Bu
``herd_vaccine_schedule.py`` ile aynı ev kuralı — her fonksiyon kendi
``date.today()``'ini çağırsaydı gece yarısını geçen bir istek aynı yanıt
içinde iki farklı güne göre hesap yapardı.

Hesap basit: ``safe_from = treated_on + timedelta(days=interval)``. Boş süre
(``None``) ihlal DEĞİL — bilinmeyeni ihlal saymak kullanıcıyı gerekçe yazmaya
alıştırır ve gerçek uyarıyı değersizleştirir. Katalog boş kalma SIKLIĞINI
düşürür; boşun ANLAMINI değiştirmez.

VOIDED durumda olan bir satır bu hesaba GİRMEZ — filtreleme çağıranın
sorumluluğu: bu modül yalnız kendisine verilen satırlara bakar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class BeklemeIhlali:
    """Bir ilaç kaydının hedef günü kapsayan bekleme süresi ihlali."""

    treatment_id: int
    drug_name: str
    treated_on: date
    interval_days: int
    safe_from: date
    #: Hesabın dayanağı: kullanıcı sayıya değil GEREKÇEYE güvenir.
    basis: str


def sut_bekleme_ihlalleri(
    *,
    sutirlar: list[dict],
    hedef_gun: date,
) -> list[BeklemeIhlali]:
    """Süt bekleme süresi ihlallerini döndürür.

    ``sutirlar`` = ``milk_withdrawal_days`` GİRİLMİŞ, ``VOIDED`` OLMAYAN
    ilaç kayıtları (çağıran filtreler). Boş liste → boş ihlal listesi.
    """
    ihlaller: list[BeklemeIhlali] = []
    for satir in sutirlar:
        gun = int(satir["milk_withdrawal_days"])
        ilac_gunu = satir["treated_on"]
        if not isinstance(ilac_gunu, date):
            ilac_gunu = date.fromisoformat(str(ilac_gunu)[:10])
        guvenli = ilac_gunu + timedelta(days=gun)
        if hedef_gun < guvenli:
            ihlaller.append(
                BeklemeIhlali(
                    treatment_id=int(satir["id"]),
                    drug_name=str(satir["drug_name"]),
                    treated_on=ilac_gunu,
                    interval_days=gun,
                    safe_from=guvenli,
                    basis=(
                        f"{ilac_gunu.isoformat()} tarihli {satir['drug_name']} ilacı "
                        f"{gun} gün süt bekleme süresi gerektiriyor"
                    ),
                )
            )
    # En geç biten kısıt en üstte: kullanıcının beklemesi gereken tarih o.
    ihlaller.sort(key=lambda x: x.safe_from, reverse=True)
    return ihlaller


def et_bekleme_ihlalleri(
    *,
    satirlar: list[dict],
    hedef_gun: date,
) -> list[BeklemeIhlali]:
    """Et bekleme süresi ihlalleri — PR-2'de kullanılacak, PR-1'de hazır.

    Süt ile aynı hesap; yalnız sütun adı farklı. Katalog PR-1'de her iki
    süreyi de taşıdığı için PR-2 yeni migration istemez.
    """
    ihlaller: list[BeklemeIhlali] = []
    for satir in satirlar:
        gun = int(satir["meat_withdrawal_days"])
        ilac_gunu = satir["treated_on"]
        if not isinstance(ilac_gunu, date):
            ilac_gunu = date.fromisoformat(str(ilac_gunu)[:10])
        guvenli = ilac_gunu + timedelta(days=gun)
        if hedef_gun < guvenli:
            ihlaller.append(
                BeklemeIhlali(
                    treatment_id=int(satir["id"]),
                    drug_name=str(satir["drug_name"]),
                    treated_on=ilac_gunu,
                    interval_days=gun,
                    safe_from=guvenli,
                    basis=(
                        f"{ilac_gunu.isoformat()} tarihli {satir['drug_name']} ilacı "
                        f"{gun} gün et bekleme süresi gerektiriyor"
                    ),
                )
            )
    ihlaller.sort(key=lambda x: x.safe_from, reverse=True)
    return ihlaller

"""Telefon normalizasyonu — alt seviye ortak modül.

Kaynak ``nazgul_website/backend/app/whatsapp/telefon.py``:
``normalize_phone`` BİREBİR taşındı. ``e164`` ve ``TelefonGecersiz`` bu
depoda EKLENDİ (kaynakta yok): Meta'nın ``from`` biçimi işaretsizdir,
E.164 ise ``+`` ile başlar; ikisi aynı rakam dizisinin iki yazımıdır.

Hiçbir üst katmanı çekmez (çevrimsiz import garantisi).
"""

from __future__ import annotations

#: E.164 en çok 15 rakam taşır (ülke kodu dâhil). Alt sınır olarak 10
#: seçildi: TR mobil numarası ülke kodsuz 10 hanedir; daha kısası kanonik
#: bir numara olamaz.
E164_EN_AZ_RAKAM = 10
E164_EN_COK_RAKAM = 15


class TelefonGecersiz(ValueError):
    """Verilen metin E.164'e indirgenemiyor."""


def normalize_phone(ham: str) -> str:
    """Numarayı Meta'nın ``from`` biçimine (ülke kodlu, işaretsiz) indirger.

    "0540 599 59 59", "+90 540 599 59 59" ve "905405995959" aynı satıra
    eşleşmelidir; eşleşme anahtarı tektir.
    """
    rakamlar = "".join(ch for ch in (ham or "") if ch.isdigit())
    if len(rakamlar) == 11 and rakamlar.startswith("0"):
        return "9" + rakamlar
    if len(rakamlar) == 10 and rakamlar.startswith("5"):
        return "90" + rakamlar
    return rakamlar


def e164(ham: str) -> str:
    """``normalize_phone`` çıktısını ``+`` önekiyle E.164 olarak döner.

    Rakam sayısı [10, 15] dışındaysa :class:`TelefonGecersiz` yükselir;
    sessizce kırpılmış ya da uydurulmuş bir numara üretilmez.
    """
    rakamlar = normalize_phone(ham)
    if not (E164_EN_AZ_RAKAM <= len(rakamlar) <= E164_EN_COK_RAKAM):
        raise TelefonGecersiz(
            f"E.164 için {E164_EN_AZ_RAKAM}-{E164_EN_COK_RAKAM} rakam gerekir; "
            f"{len(rakamlar)} rakam bulundu."
        )
    return "+" + rakamlar


__all__ = ["E164_EN_AZ_RAKAM", "E164_EN_COK_RAKAM", "TelefonGecersiz", "e164", "normalize_phone"]

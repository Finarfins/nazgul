"""F10-4a — PARTİ GERİ ÇAĞIRMA ÖNİZLEMESİ: `GET /api/lots/{lot_id}/recall-preview`.

SALT OKUR: yazma yok, aktivite yok (diğer GET'lerle tutarlı; keşif §5.1),
yani `ACTION_TYPES` SABİT. Eylem ucu (`POST /api/lots/{lot_id}/recall`) ve
kayıt tabloları 10-4b'dir.

İZİN `sales`dir ve middleware'de (`auth.required_permission`) `read`
geri düşüşünün ÜSTÜNDE yazılıdır: cevap cari adı ve telefonu taşır, `read`
herkese açık bir telefon listesi olurdu (K2). Telefon ayrıca
`alan_maskeleme.maskele_cari` ile role göre maskelenir (SEC-3b). Bugün
`sales` taşıyan üç rolün üçü de `MASKESIZ_ROLLER`dedir; maske BAĞLIDIR ama
hiçbir yerleşik rolde TETİKLENMEZ — `cek_senetler`in aynı durumu.

RIZA KURU ÇALIŞIR: `evaluate_consent` yalnız okur. Kanal SMS'tir (K4:
WhatsApp'ta şablon mesajı yok). Telefonu ya da rızası olmayan alıcı
`manual_pending`dir (K3); rıza kapısı DELİNMEZ.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..alan_maskeleme import maskele_cari
from ..db import get_db
from ..lot_izi import parti_izi_oku
from ..lot_izi_ozet import ozetle
from ..lot_izi_schemas import GeriCagirmaOnizleme
from ..notifications.consents import evaluate_consents_bulk, normalize_msisdn
from ..tenancy import company_id, istek_rolu
from .pos import _mapped_retail_customer_id

router = APIRouter(prefix="/lots", tags=["Partiler"])

#: Geri çağırma bildiriminin kanalı (K4). Rıza bu kanal için sorulur.
GERI_CAGIRMA_KANALI = "SMS"


def _iletisim(
    db: Session, cid: int, cariler: dict[int, dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Cari başına telefon + rıza bayrağı. `evaluate_consent`in KURU, TOPLU
    çalışması: cari sayısından bağımsız tek okuma (`evaluate_consents_bulk`)."""
    kararlar = evaluate_consents_bulk(
        db,
        company_id=cid,
        party_type="CUSTOMER",
        channel=GERI_CAGIRMA_KANALI,
        recipients={
            int(cari_id): str(cari.get("phone") or "")
            for cari_id, cari in cariler.items()
        },
    )
    return {
        cari_id: {
            "has_phone": normalize_msisdn(cari.get("phone")) is not None,
            "has_consent": bool(kararlar[int(cari_id)]["allowed"]),
            "consent_reason": kararlar[int(cari_id)]["reason"],
        }
        for cari_id, cari in cariler.items()
    }


@router.get("/{lot_id}/recall-preview", response_model=GeriCagirmaOnizleme)
def recall_preview(lot_id: int, request: Request, db: Session = Depends(get_db)):
    """Bu parti kime, ne kadar, ne zaman çıktı; elinde ne kaldı; nerede iz koptu."""
    cid = company_id(request)
    iz = parti_izi_oku(db, cid, lot_id)
    if iz is None:
        # Başka firmanın kimliği de buraya düşer; VARLIĞI sızdırılmaz.
        raise HTTPException(404, "Parti bulunamadı")
    govde = ozetle(
        iz,
        perakende_cari_id=_mapped_retail_customer_id(db, cid),
        iletisim=_iletisim(db, cid, iz.cariler),
    )
    rol = istek_rolu(request)
    govde["customers"] = [maskele_cari(m, rol) for m in govde["customers"]]
    return govde

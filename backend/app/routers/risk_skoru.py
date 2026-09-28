"""`GET /api/customers/{customer_id}/risk-score` — müşteri risk skoru (F10-9a).

Salt okunur. Formül `app/risk_skoru.py`, sinyaller `app/risk_skoru_okuma.py`.
İzin `sales` (K7): `app/auth.py`de `/statement` kuralının yanındaki tam sonek
kuralı; genel GET → `read` düşüşünün ÜSTÜNDE, yani `depo`/`rapor` 403 alır.

KVKK (K8): cevap cari kişisel alanı TAŞIMAZ (ad/VKN/telefon yok) ve model
`extra="forbid"` ile kapalıdır. Skor saklanmaz (K3), okuma denetlenmez (K9).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..business_time import business_today
from ..db import get_db
from ..risk_skoru_okuma import risk_skoru
from ..tenancy import company_id

router = APIRouter(prefix="/customers", tags=["customers"])


class RiskCezasiCevabi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kod: Literal["gecikme", "vadesi_oran", "gec_kapanis", "karsiliksiz", "vade_farki", "limit"]
    puan: int
    aciklama: str
    kanit: str


class RiskSkoruCevabi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    puan: int
    harf: Literal["A", "B", "C", "D", "E"]
    yetersiz_veri: bool
    cezalar: list[RiskCezasiCevabi]
    hesaplandi: date


@router.get("/{customer_id}/risk-score", response_model=RiskSkoruCevabi)
def customer_risk_score(
    customer_id: int,
    request: Request,
    as_of: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RiskSkoruCevabi:
    skor = risk_skoru(db, company_id(request), customer_id, as_of or business_today())
    if skor is None:
        raise HTTPException(404, "Cari bulunamadı")
    return RiskSkoruCevabi(
        puan=skor.puan,
        harf=skor.harf,
        yetersiz_veri=skor.yetersiz_veri,
        cezalar=[
            RiskCezasiCevabi(kod=c.kod, puan=c.puan, aciklama=c.aciklama, kanit=c.kanit)
            for c in skor.cezalar
        ],
        hesaplandi=skor.hesaplandi,
    )

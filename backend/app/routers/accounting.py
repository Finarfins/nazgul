"""Muhasebe uçları (F9-5a): hesap eşlemesi + fiş önizlemesi + aylık KDV özeti.

İzin `app/auth.py`deki METODA BAKAN `/api/accounting` önek kuralından gelir
(keşif K6): GET → `reports`, PUT → `finance`. Handler izin DENETLEMEZ; kural
genel `GET → read` düşüşünün ÜSTÜNDEDİR (aksi hâlde üç GET `read`e çözülür ve
`depo` fişleri görürdü — `test_f9_5a_muhasebe_fisi.py` izin matrisi).

Bu dilimde dosya ÜRETİLMEZ (9-5b); önizleme kanonik JSON'dur. Cari VKN'si
`maskele_cari`den geçer (SEC-3b): `reports` taşıyan `rapor` rolü maskelidir.
`musavir` rolü (K13, maskesiz) 9-5c'dedir.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..activity_log import log_request_activity
from ..alan_maskeleme import maskele_cari
from ..db import get_db
from ..muhasebe.fis import fis_sozlugu
from ..muhasebe.hesap_plani import (
    HESAP_KODU_DESENI,
    VARSAYILANLAR,
    eslemeleri_oku,
    esleme_yaz,
    oran_normalize,
    plan_oku,
)
from ..muhasebe.kaynak import DonemHatasi, donem_coz, donem_oku
from ..muhasebe.kdv_ozeti import kdv_ozeti
from ..muhasebe.schema import OLAYLAR
from ..tenancy import company_id, istek_rolu

router = APIRouter(prefix="/accounting", tags=["accounting"])

SAYFA_TAVANI = 500
ESLEME_TAVANI = 200

Olay = Literal[
    "SATIS_CARI", "SATIS_GELIR", "SATIS_KDV", "SATIS_IADE", "ALIS_CARI", "ALIS_STOK",
    "ALIS_KDV", "ALIS_IADE", "SERVIS_GELIR", "MUSTAHSIL_STOPAJ", "MUSTAHSIL_BAGKUR",
    "KASA", "BANKA", "POS", "ALINAN_CEK", "VADE_FARKI_GELIR",
]
TarafTipi = Literal["CUSTOMER", "SUPPLIER"]


class VarsayilanHesap(BaseModel):
    olay: str
    hesap_kodu: str


class HesapEslemesi(BaseModel):
    olay: str
    kdv_orani: str | None = None
    taraf_tipi: str | None = None
    hesap_kodu: str
    updated_at: datetime | None = None


class HesapPlaniCevabi(BaseModel):
    varsayilanlar: list[VarsayilanHesap]
    eslemeler: list[HesapEslemesi]


class HesapEslemesiGirdisi(BaseModel):
    olay: Olay
    kdv_orani: Decimal | None = Field(None, ge=0, le=100, max_digits=9, decimal_places=4)
    taraf_tipi: TarafTipi | None = None
    hesap_kodu: str = Field(..., min_length=3, max_length=40, pattern=HESAP_KODU_DESENI)


class HesapPlaniGuncelleme(BaseModel):
    eslemeler: list[HesapEslemesiGirdisi] = Field(..., min_length=1, max_length=ESLEME_TAVANI)


class FisSatiriGorunumu(BaseModel):
    hesap_kodu: str
    borc: str
    alacak: str
    aciklama: str
    kdv_orani: str | None = None
    cari_tipi: str | None = None
    cari_id: int | None = None


class FisCarisi(BaseModel):
    ad: str | None = None
    tax_number: str | None = None


class FisGorunumu(BaseModel):
    fis_no: str
    fis_tarihi: str
    belge_tipi: str
    belge_no: str
    belge_tarihi: str
    odeme_yontemi: str
    kaynak: str
    borc_toplami: str
    alacak_toplami: str
    satirlar: list[FisSatiriGorunumu]
    cari: FisCarisi


class MuhasebeUyarisi(BaseModel):
    kod: str
    kaynak: str
    belge_id: int | None = None
    belge_no: str | None = None
    fark: str | None = None
    mesaj: str


class FisListesi(BaseModel):
    period: str
    total: int
    limit: int
    offset: int
    items: list[FisGorunumu]
    dengesiz_sayisi: int
    uyarilar: list[MuhasebeUyarisi]


class KdvSatiri(BaseModel):
    yon: str
    tur: str
    oran: str
    bilinen_oran: bool
    matrah: str
    kdv: str
    belge_sayisi: int


class MustahsilOzeti(BaseModel):
    belge_sayisi: int
    brut: str
    stopaj: str
    bagkur: str
    net: str


class KdvOzeti(BaseModel):
    period: str
    satirlar: list[KdvSatiri]
    hesaplanan_kdv: str
    indirilecek_kdv: str
    fark: str
    mustahsil: MustahsilOzeti
    uyarilar: list[MuhasebeUyarisi]
    kapsam_notlari: list[str]


def _donem(period: str):
    try:
        return donem_coz(period)
    except DonemHatasi as exc:
        raise HTTPException(422, str(exc)) from exc


def _plan_gorunumu(db: Session, cid: int) -> dict:
    return {
        "varsayilanlar": [{"olay": o, "hesap_kodu": VARSAYILANLAR[o]} for o in OLAYLAR],
        "eslemeler": [
            {
                "olay": s["olay"],
                "kdv_orani": (
                    None if s["kdv_orani"] is None else format(oran_normalize(s["kdv_orani"]), "f")
                ),
                "taraf_tipi": s["taraf_tipi"],
                "hesap_kodu": s["hesap_kodu"],
                "updated_at": s["updated_at"],
            }
            for s in eslemeleri_oku(db, cid)
        ],
    }


@router.get("/account-map", response_model=HesapPlaniCevabi)
def hesap_plani_getir(request: Request, db: Session = Depends(get_db)) -> dict:
    """Tek Düzen varsayılanları + firmanın yazdığı istisnalar (boş = hep varsayılan)."""
    return _plan_gorunumu(db, company_id(request))


@router.put("/account-map", response_model=HesapPlaniCevabi)
def hesap_plani_guncelle(
    payload: HesapPlaniGuncelleme, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Eşlem listesini yazar (varsa günceller); aynı liste İKİ kez = aynı durum.

    Aktivite satırı YALNIZ değişen eşlemleri taşır (önce/sonra, PII yok) ve
    yazmayla AYNI işlemdedir. Hiçbir şey değişmediyse satır YAZILMAZ.
    """
    cid = company_id(request)
    anahtarlar = [
        (e.olay, oran_normalize(e.kdv_orani), e.taraf_tipi) for e in payload.eslemeler
    ]
    if len(set(anahtarlar)) != len(anahtarlar):
        raise HTTPException(422, "Aynı (olay, oran, taraf) listede birden fazla kez geçiyor")
    degisen = []
    try:
        for e in payload.eslemeler:
            once, sonra = esleme_yaz(db, cid, e.olay, e.kdv_orani, e.taraf_tipi, e.hesap_kodu)
            if once != sonra:
                oran = oran_normalize(e.kdv_orani)
                degisen.append({
                    "olay": e.olay,
                    "kdv_orani": None if oran is None else format(oran, "f"),
                    "taraf_tipi": e.taraf_tipi,
                    "once": once,
                    "sonra": sonra,
                })
        if degisen:
            log_request_activity(
                db, request, cid, "accounting.account_map_updated", "account_map", None,
                f"Muhasebe hesap eşlemesini güncelledi — {len(degisen)} eşlem",
                {"degisen": degisen},
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Eşleme aynı anda başka bir istekle yazıldı; tekrar deneyin") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    return _plan_gorunumu(db, cid)


def _muhasebe_fisi_gorunumu(fis, rol: str) -> dict:
    govde = fis_sozlugu(fis)
    govde["cari"] = maskele_cari({"ad": fis.cari_ad, "tax_number": fis.cari_vkn}, rol)
    return govde


@router.get("/vouchers", response_model=FisListesi)
def fis_onizleme(
    request: Request,
    period: str = Query(..., min_length=7, max_length=7, description="YYYY-AA"),
    limit: int = Query(100, ge=1, le=SAYFA_TAVANI),
    offset: int = Query(0, ge=0, le=2_147_483_647),
    db: Session = Depends(get_db),
) -> dict:
    """Dönemin kanonik fişleri (sayfalı). Dengesiz fiş LİSTEYE GİRMEZ; `uyarilar`da."""
    cid = company_id(request)
    donem = _donem(period)
    veri = donem_oku(db, cid, donem, plan_oku(db, cid))
    ozet = kdv_ozeti(veri)
    rol = istek_rolu(request)
    return {
        "period": donem.metin,
        "total": len(veri.fisler),
        "limit": limit,
        "offset": offset,
        "items": [_muhasebe_fisi_gorunumu(f, rol) for f in veri.fisler[offset:offset + limit]],
        "dengesiz_sayisi": len(veri.reddedilen),
        "uyarilar": ozet["uyarilar"],
    }


@router.get("/vat-summary", response_model=KdvOzeti)
def kdv_ozeti_getir(
    request: Request,
    period: str = Query(..., min_length=7, max_length=7, description="YYYY-AA"),
    db: Session = Depends(get_db),
) -> dict:
    """Aylık KDV özeti `(yön, oran, tür)` üçlüsüyle + uyarılar (G2-G9)."""
    cid = company_id(request)
    donem = _donem(period)
    return kdv_ozeti(donem_oku(db, cid, donem, plan_oku(db, cid)))

"""Muhasebe uçları (F9-5a): hesap eşlemesi + fiş önizlemesi + aylık KDV özeti.

İzin `app/auth.py`deki METODA BAKAN `/api/accounting` önek kuralından gelir
(keşif K6): GET → `reports`, PUT → `finance`. Handler izin DENETLEMEZ; kural
genel `GET → read` düşüşünün ÜSTÜNDEDİR (aksi hâlde üç GET `read`e çözülür ve
`depo` fişleri görürdü — `test_f9_5a_muhasebe_fisi.py` izin matrisi).

Önizleme kanonik JSON'dur; dosya `GET /export` ile iner (F9-5b). Cari VKN'si
`maskele_cari`den geçer (SEC-3b): `reports` taşıyan `rapor` rolü maskelidir.
`musavir` rolü (K13, maskesiz) 9-5c'dedir.
"""
from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..activity_log import log_request_activity
from ..alan_maskeleme import maskele_cari
from ..db import engine, get_db
from ..muhasebe.fis import SIFIR, Fis, fis_sozlugu
from ..muhasebe.hedef_kanonik import (
    CSV_ADI,
    JSON_ADI,
    KanonikSerilestirici,
    icerik_sha256,
    kanonik_csv,
    kanonik_json,
)
from ..muhasebe.hedef_luca import LucaSerilestirici
from ..muhasebe.hedef_mikro import MikroSerilestirici
from ..muhasebe.serilestirici import Serilestirici, fis_anahtarlari_tekil
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
from .kiraci_disa_aktarim import _AkanTampon

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
    kdv_dahil: bool | None = None


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


# --------------------------------------------------------------------------
# DIŞA AKTARIM (F9-5b, keşif §3, §6, §7.2)
# --------------------------------------------------------------------------

#: `target` → serileştirici. `kanonik` brifingin yazımıdır; keşif `canonical`
#: der (keşif kazanır, ikisi de kabul). `logo` K2 kapanana kadar YAZILMAZ.
SERILESTIRICILER: dict[str, Serilestirici] = {
    "luca": LucaSerilestirici(),
    "mikro": MikroSerilestirici(),
    "canonical": KanonikSerilestirici(),
    "kanonik": KanonikSerilestirici(),
}
Bicim = Literal["xlsx", "csv"]
ILK_HATA_TAVANI = 5


class DisaAktarimHatasiDetayi(BaseModel):
    code: str
    message: str
    dengesiz_sayisi: int | None = None
    ilk_hatalar: list[str] | None = None


class DisaAktarimHatasi(BaseModel):
    detail: DisaAktarimHatasiDetayi


def _aktarim_hatasi(durum: int, kod: str, mesaj: str, **ek) -> HTTPException:
    return HTTPException(durum, detail={"code": kod, "message": mesaj, **ek})


def _kesit_baslat(conn: Connection) -> None:
    """PG: dönemin TÜM okuması tek `REPEATABLE READ, READ ONLY` kesitte.

    `app/db.py` motoru varsayılan `READ COMMITTED`dir; plan, beş kol ve KDV
    grupları ayrı ifadelerle okunur ve araya giren bir yazma fiş toplamını
    manifest toplamından ayırırdı (keşif §6.1). SQLite'ta tek bağlantının tek
    işlemi aynı garantiyi verir; ifade atlanır.
    """
    if conn.dialect.name == "postgresql":
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")


def _donem_kesiti(cid: int, donem):
    """Dönemi KENDİ bağlantısında okur; istek oturumu yazma için boş kalır."""
    with engine.connect() as conn:
        _kesit_baslat(conn)
        with Session(bind=conn) as oturum:
            return donem_oku(oturum, cid, donem, plan_oku(oturum, cid))


def _toplam(fisler: list[Fis], alan: str) -> str:
    return format(sum((getattr(f, alan) for f in fisler), SIFIR), "f")


def _akit(
    serilestirici: Serilestirici,
    fisler: list[Fis],
    donem: str,
    rol: str,
    json_bayt: bytes,
    manifest: dict,
) -> Iterator[bytes]:
    """Zip konumlanamayan tampona yazılır; her dosyadan sonra akar.

    Durum 200'de kilitlidir (`kiraci_disa_aktarim` HATA SINIRI): buradan
    sonra doğan hata YARIM (açılamayan) zip üretir. Denge kapısı, fiş tekilliği
    ve günlük satırı bu yüzden üreteçten ÖNCE, uçtadır.
    """
    def vkn(fis: Fis) -> str | None:
        return maskele_cari({"tax_number": fis.cari_vkn}, rol)["tax_number"]

    tampon = _AkanTampon()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as zf:
        for ad, bayt in serilestirici.dosyalar(fisler, donem, vkn):
            zf.writestr(ad, bayt)
            manifest["dosyalar"].append(ad)
            yield tampon.bosalt()
        manifest["parca_sayisi"] = len(manifest["dosyalar"])
        zf.writestr(JSON_ADI, json_bayt)
        yield tampon.bosalt()
        zf.writestr(CSV_ADI, kanonik_csv(fisler, vkn))
        yield tampon.bosalt()
        manifest["dosyalar"] += [JSON_ADI, CSV_ADI]
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    yield tampon.bosalt()


@router.get(
    "/export",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"application/zip": {}},
            "description": "Hedef dosyaları + kanonik fisler.json/fisler.csv + manifest.json",
        },
        409: {
            "model": DisaAktarimHatasi,
            "description": "Dönemde dengesiz fiş var veya fiş no tekrarlı",
        },
        422: {"model": DisaAktarimHatasi, "description": "Hedef/biçim/dönem geçersiz"},
    },
)
def disa_aktar(
    request: Request,
    period: str = Query(..., min_length=7, max_length=7, description="YYYY-AA"),
    target: str = Query(
        ...,
        description="Hedef muhasebe programı (luca | mikro | canonical | kanonik)",
        examples=["luca", "mikro", "canonical", "kanonik"],
    ),
    format: Bicim | None = Query(None, description="Hedefin kendi dosya biçimi; verilirse eşleşmeli"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Dönemin fişlerini hedef biçiminde akan bir zip olarak indirir.

    Sıra (keşif §6.1): dönem kesiti → denge kapısı ve fiş tekilliği (409, akış
    YOK) → TEK `accounting.exported` satırı (commit) → akış. İdempotency
    DURUMSUZDUR (§6.3): `icerik_sha256` kanonik `fisler.json`un özetidir;
    DB'ye aktarım durumu YAZILMAZ.
    """
    if target == "logo":
        raise _aktarim_hatasi(
            422, "HEDEF_DESTEKLENMIYOR",
            "Logo hedefi henüz desteklenmiyor: muhasebe fişi XML şablonu doğrulanmadı (K2). "
            "Kanonik (canonical) çıktıyı kullanın.",
        )
    if target not in SERILESTIRICILER:
        raise _aktarim_hatasi(
            422, "HEDEF_DESTEKLENMIYOR",
            f"'{target}' hedefi desteklenmiyor. Desteklenen hedefler: "
            "luca, mikro, canonical (kanonik).",
        )
    serilestirici = SERILESTIRICILER[target]
    if format is not None and format != serilestirici.bicim:
        raise _aktarim_hatasi(
            422, "BICIM_DESTEKLENMIYOR",
            f"{target} hedefi {serilestirici.bicim} üretir; istenen biçim {format}.",
        )
    try:
        donem = donem_coz(period)
    except DonemHatasi as exc:
        raise _aktarim_hatasi(422, "DONEM_GECERSIZ", str(exc)) from exc

    cid = company_id(request)
    veri = _donem_kesiti(cid, donem)
    if veri.reddedilen:
        raise _aktarim_hatasi(
            409, "DONEM_DENGESIZ",
            f"{donem.metin} döneminde {len(veri.reddedilen)} dengesiz fiş var; "
            "önce belgeleri düzeltin (fiş önizlemesindeki uyarılar).",
            dengesiz_sayisi=len(veri.reddedilen),
            ilk_hatalar=[str(h) for h in veri.reddedilen[:ILK_HATA_TAVANI]],
        )

    fisler = veri.fisler
    try:
        fis_anahtarlari_tekil(fisler)
    except ValueError as exc:
        raise _aktarim_hatasi(409, "FIS_NO_TEKRARLI", str(exc)) from exc

    json_bayt = kanonik_json(fisler)
    ozet = icerik_sha256(json_bayt)
    borc, alacak = _toplam(fisler, "borc_toplami"), _toplam(fisler, "alacak_toplami")
    # Günlük akıştan ÖNCE ve commit'li: kopan bir akış denetim satırını
    # KAYBETTİRMEZ; 409'da ise hiç yazılmaz.
    log_request_activity(
        db, request, cid, "accounting.exported", "account_map", None,
        f"Muhasebe dışa aktarımı — {donem.metin} {serilestirici.hedef}, {len(fisler)} fiş",
        {"period": donem.metin, "target": serilestirici.hedef, "fis_sayisi": len(fisler),
         "borc_toplami": borc, "icerik_sha256": ozet},
    )
    db.commit()

    manifest = {
        "period": donem.metin,
        "target": serilestirici.hedef,
        "fis_sayisi": len(fisler),
        "borc_toplami": borc,
        "alacak_toplami": alacak,
        "parca_sayisi": 0,
        "icerik_sha256": ozet,
        "uretim_zamani": datetime.now(timezone.utc).isoformat(),
        "dosyalar": [],
    }
    ad = f"muhasebe-{donem.metin}-{serilestirici.hedef}.zip"
    return StreamingResponse(
        _akit(serilestirici, fisler, donem.metin, istek_rolu(request), json_bayt, manifest),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{ad}"'},
    )

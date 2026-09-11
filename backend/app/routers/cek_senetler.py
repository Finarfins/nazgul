"""ÇEK/SENET PORTFÖYÜ uçları (CS1) — ``/api/cek-senetler``.

Kaynak: ``docs/cek-senet-kesif-2026-09-10.md`` §5 PR 1. Beş operasyon:

    GET  /api/cek-senetler                    liste (süzgeç + sayfalama, vade artan)
    POST /api/cek-senetler                    tek evrak (durum ZORLA ``portfoyde``)
    GET  /api/cek-senetler/{id}               tekil
    POST /api/cek-senetler/{id}/durum-degistir  durum makinesi (``cek_senet_engine``)
    POST /api/cek-senetler/bordro             toplu giriş: en çok 200 satır, HEP-YA-HİÇ

--- YETKİ ------------------------------------------------------------------

``app/auth.py``de AÇIK önek kuralı: ``/api/cek-senetler`` -> ``payments``,
BÜTÜN metotlar (GET dahil). Ölçülen matris: ``payments``ı ``admin``,
``yonetici``, ``muhasebe`` ve ``satis`` taşır; ``depo`` ve ``rapor`` taşımaz
ve GET'te bile 403 alır (karar 4: ``satis`` bugünkü matrisle YAZAR).

--- KİRACI ------------------------------------------------------------------

Her sorgu ``company_id == request.state.company_id`` yüklemini AÇIKÇA taşır.
Başka firmanın kimliği 404'tür, 403 DEĞİL: 403 kaydın VAR olduğunu söylerdi.
Başvurulan müşteri/tedarikçi/kasa da aynı firmada aranır; bulunamazsa 422
(yük geçersiz). Veritabanı ayrıca bileşik FK ile aynı şeyi zorlar (göç 0085).

--- MUHASEBE YOK (karar 1 -> CS2) -------------------------------------------

Bu dosya ``payments``a, ``finance_transactions``a ve borç belgelerine
YAZMAZ. ``payment_id``, ``financial_transaction_id``, ``charge_document_id``
her yolda NULL kalır.

--- MASKELEME (SEC-3b) ------------------------------------------------------

``hesap_no`` keşidecinin banka hesabıdır ve cari hassas alanı sayılır:
``alan_maskeleme.MASKELENEN_ALANLAR``a eklendi, yanıt ``maskele_cari`` ile
role göre maskelenir. Ölçüm: bugün ``payments`` taşıyan dört rolün dördü de
``MASKESIZ_ROLLER`` içinde, yani maskeleme bugün HİÇBİR role uygulanmaz —
bağlıdır, ama tetiklenmez. Maskeli bir role ``payments`` verildiği gün
kendiliğinden devreye girer.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import String, Date, Integer, bindparam, func, insert, or_, select, update
from sqlalchemy.orm import Session

from ..activity_log import log_activity
from ..alan_maskeleme import maskele_cari
from ..auth import utcnow
from ..cek_senet_engine import (
    CIRO_EDILDI,
    DURUMLAR,
    IADE,
    KARSILIKSIZ,
    PORTFOYDE,
    TAHSIL_EDILDI,
    TAHSIL_HESAP_TIPLERI,
    GecisHatasi,
    gecis_dogrula,
)
from ..cek_senet_schema import cek_senetler
from ..core_schema import customers, suppliers
from ..db import get_db
from ..finance_engine import finance_accounts
from ..money import money
from ..tenancy import company_id, istek_rolu

router = APIRouter(prefix="/cek-senetler", tags=["Çek/Senet Portföyü"])

#: Bordronun satır tavanı (brif). Tek istekte daha fazlası bir toplu
#: aktarımdır ve ayrı bir iş olarak ele alınmalıdır.
BORDRO_TAVANI = 200
SAYFA_TAVANI = 200
#: Kimlik sütunları PG'de INTEGER (int4). Üstündeki bir kimlik sorguya
#: ulaşırsa PG ``NumericValueOutOfRange`` (500) verir; sınır uçta 422'dir.
INT4_UST = 2147483647

Tur = Literal["cek", "senet"]
Yon = Literal["alinan", "verilen"]
Durum = Literal["portfoyde", "tahsile_verildi", "tahsil_edildi", "ciro_edildi", "karsiliksiz", "iade"]


# ---------------------------------------------------------------- modeller ---

class CekSenetGirdisi(BaseModel):
    """Yeni evrak. ``portfoy_durumu`` ALINMAZ: her evrak ``portfoyde`` doğar."""

    tur: Tur
    yon: Yon
    customer_id: int | None = Field(default=None, ge=1, le=INT4_UST)
    supplier_id: int | None = Field(default=None, ge=1, le=INT4_UST)
    tutar: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    vade: date
    keside_tarihi: date | None = None
    banka_adi: str | None = Field(default=None, max_length=160)
    sube_adi: str | None = Field(default=None, max_length=120)
    hesap_no: str | None = Field(default=None, max_length=100)
    seri_no: str = Field(min_length=1, max_length=100)
    kesideci: str | None = Field(default=None, max_length=200)
    notlar: str | None = Field(default=None, max_length=2000)

    @field_validator("seri_no")
    @classmethod
    def _seri_bos_olamaz(cls, deger: str) -> str:
        temiz = deger.strip()
        if not temiz:
            raise ValueError("Seri no boş olamaz")
        return temiz

    @model_validator(mode="after")
    def _yon_taraf(self) -> "CekSenetGirdisi":
        # Göçün ``ck_cek_senetler_yon_taraf``ının uçtaki ikizi — ve DAHA DAR:
        # karşı yönün tarafı da REDDEDİLİR. Alınan bir çekte tedarikçi,
        # verilen bir çekte müşteri anlamsızdır; kabul edilseydi portföy
        # listesinin "kimin çeki" sütunu iki cevap verebilirdi.
        if self.yon == "alinan":
            if self.customer_id is None:
                raise ValueError("Alınan evrakta müşteri (customer_id) zorunludur")
            if self.supplier_id is not None:
                raise ValueError("Alınan evrakta tedarikçi (supplier_id) verilemez")
        else:
            if self.supplier_id is None:
                raise ValueError("Verilen evrakta tedarikçi (supplier_id) zorunludur")
            if self.customer_id is not None:
                raise ValueError("Verilen evrakta müşteri (customer_id) verilemez")
        if self.keside_tarihi is not None and self.keside_tarihi > self.vade:
            raise ValueError("Keşide tarihi vadeden sonra olamaz")
        return self


class BordroGirdisi(BaseModel):
    satirlar: list[CekSenetGirdisi] = Field(min_length=1, max_length=BORDRO_TAVANI)


class DurumDegistir(BaseModel):
    """Hedef duruma göre yük: gerekenler zorunlu, ilgisizler REDDEDİLİR."""

    hedef: Durum
    tahsil_hesap_id: int | None = Field(default=None, ge=1, le=INT4_UST)
    tahsil_tarihi: date | None = None
    endorsed_supplier_id: int | None = Field(default=None, ge=1, le=INT4_UST)
    endorsed_date: date | None = None
    not_metni: str | None = Field(default=None, max_length=2000)


class CekSenet(BaseModel):
    id: int
    tur: str
    yon: str
    portfoy_durumu: str
    customer_id: int | None
    supplier_id: int | None
    endorsed_supplier_id: int | None
    endorsed_date: date | None
    tutar: Decimal
    vade: date
    keside_tarihi: date | None
    banka_adi: str | None
    sube_adi: str | None
    hesap_no: str | None
    seri_no: str
    kesideci: str | None
    tahsil_hesap_id: int | None
    tahsil_tarihi: date | None
    payment_id: int | None
    financial_transaction_id: int | None
    charge_document_id: int | None
    notlar: str | None
    created_at: datetime
    created_by: int | None


class CekSenetListesi(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CekSenet]


class BordroSonucu(BaseModel):
    ids: list[int]


# --------------------------------------------------------------- yardımcı ---

def _cek_kullanici_kimligi(request: Request) -> int | None:
    kullanici = getattr(request.state, "user", None) or {}
    kimlik = kullanici.get("id") if isinstance(kullanici, dict) else None
    return int(kimlik) if kimlik is not None else None


def _cek_gorunum(satir: Any, rol: str) -> dict:
    """Yanıt satırı: ``hesap_no`` (keşidecinin banka hesabı) role göre maskelenir.

    Maskeleme ``alan_maskeleme.maskele_cari`` üzerinden — tek kaynak. SEC-3b
    envanteri (``tests/pins/cari_alan_envanteri.txt``) bu iki GET'i
    ``hesap_no`` alanıyla sınıflandırır.
    """
    return maskele_cari(dict(satir), rol)


def _cek_evrak(db: Session, cid: int, evrak_id: int) -> dict:
    satir = db.execute(
        select(cek_senetler).where(
            cek_senetler.c.company_id == cid, cek_senetler.c.id == evrak_id
        )
    ).mappings().first()
    if satir is None:
        # 404 — başka firmanın kaydı da buraya düşer; VARLIĞI sızdırılmaz.
        raise HTTPException(404, "Çek/senet bulunamadı")
    return dict(satir)


def _cek_musteri_var(db: Session, cid: int, musteri_id: int) -> bool:
    return db.execute(
        select(customers.c.id).where(customers.c.company_id == cid, customers.c.id == musteri_id)
    ).first() is not None


def _cek_tedarikci_var(db: Session, cid: int, tedarikci_id: int) -> bool:
    return db.execute(
        select(suppliers.c.id).where(suppliers.c.company_id == cid, suppliers.c.id == tedarikci_id)
    ).first() is not None


def _cek_tahsil_hesabi_uygun(db: Session, cid: int, hesap_id: int) -> bool:
    satir = db.execute(
        select(finance_accounts.c.account_type).where(
            finance_accounts.c.company_id == cid,
            finance_accounts.c.id == hesap_id,
            finance_accounts.c.is_active.is_(True),
        )
    ).first()
    return satir is not None and str(satir[0]) in TAHSIL_HESAP_TIPLERI


def _cek_taraf_dogrula(db: Session, cid: int, girdi: CekSenetGirdisi, *, sira: int | None = None) -> None:
    onek = "" if sira is None else f"Satır {sira + 1}: "
    if girdi.customer_id is not None and not _cek_musteri_var(db, cid, girdi.customer_id):
        raise HTTPException(422, f"{onek}Müşteri bulunamadı")
    if girdi.supplier_id is not None and not _cek_tedarikci_var(db, cid, girdi.supplier_id):
        raise HTTPException(422, f"{onek}Tedarikçi bulunamadı")


def _cek_ekle(db: Session, cid: int, uid: int | None, girdi: CekSenetGirdisi) -> int:
    yeni_id = db.execute(
        insert(cek_senetler)
        .values(
            company_id=cid,
            tur=girdi.tur,
            yon=girdi.yon,
            portfoy_durumu=PORTFOYDE,
            customer_id=girdi.customer_id,
            supplier_id=girdi.supplier_id,
            tutar=money(girdi.tutar),
            vade=girdi.vade,
            keside_tarihi=girdi.keside_tarihi,
            banka_adi=girdi.banka_adi,
            sube_adi=girdi.sube_adi,
            hesap_no=girdi.hesap_no,
            seri_no=girdi.seri_no,
            kesideci=girdi.kesideci,
            notlar=girdi.notlar,
            created_at=utcnow(),
            created_by=uid,
        )
        .returning(cek_senetler.c.id)
    ).scalar_one()
    return int(yeni_id)


def _cek_olusturma_kaydi(
    db: Session, request: Request, cid: int, uid: int | None, evrak_id: int,
    girdi: CekSenetGirdisi, *, bordro: bool,
) -> None:
    tur = "Çek" if girdi.tur == "cek" else "Senet"
    log_activity(
        db, cid, uid, "cek_senet.created", "cek_senet", evrak_id,
        f"{tur} portföye alındı: {girdi.seri_no} ({money(girdi.tutar)})",
        {"tur": girdi.tur, "yon": girdi.yon, "tutar": str(money(girdi.tutar)),
         "vade": girdi.vade.isoformat(), "bordro": bordro},
        correlation_id=getattr(request.state, "request_id", None),
    )


# -------------------------------------------------------------------- uçlar ---

@router.get("", response_model=CekSenetListesi)
def cek_senet_listesi(
    request: Request,
    tur: Tur | None = None,
    yon: Yon | None = None,
    portfoy_durumu: Durum | None = None,
    customer_id: int | None = Query(None, ge=1, le=INT4_UST),
    supplier_id: int | None = Query(None, ge=1, le=INT4_UST),
    vade_from: date | None = None,
    vade_to: date | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=SAYFA_TAVANI),
    offset: int = Query(0, ge=0, le=INT4_UST),
    db: Session = Depends(get_db),
) -> dict:
    """Portföy listesi, vade ARTAN (en yakın vade önce), sonra kimlik.

    İsteğe bağlı süzgeçler SATIR İÇİ ve TİPLİ bağlı parametredir
    (``:x IS NULL OR sütun = :x``): koşul listesi (``where(*liste)``) Core
    sorgu envanterinde "variable-arg" sayılır ve bilerek kullanılmadı.
    ``q`` seri no / keşideci / banka adında büyük-küçük harf duyarsız arar;
    joker karakterler kaçışlıdır.
    """
    cid = company_id(request)
    aranan = (q or "").strip().lower()
    p_tur = bindparam("p_tur", tur, type_=String)
    p_yon = bindparam("p_yon", yon, type_=String)
    p_durum = bindparam("p_durum", portfoy_durumu, type_=String)
    p_musteri = bindparam("p_musteri", customer_id, type_=Integer)
    p_tedarikci = bindparam("p_tedarikci", supplier_id, type_=Integer)
    p_vade_bas = bindparam("p_vade_bas", vade_from, type_=Date)
    p_vade_son = bindparam("p_vade_son", vade_to, type_=Date)
    toplam = db.execute(
        select(func.count(cek_senetler.c.id))
        .select_from(cek_senetler)
        .where(
            cek_senetler.c.company_id == cid,
            or_(p_tur.is_(None), cek_senetler.c.tur == p_tur),
            or_(p_yon.is_(None), cek_senetler.c.yon == p_yon),
            or_(p_durum.is_(None), cek_senetler.c.portfoy_durumu == p_durum),
            or_(p_musteri.is_(None), cek_senetler.c.customer_id == p_musteri),
            or_(p_tedarikci.is_(None), cek_senetler.c.supplier_id == p_tedarikci),
            or_(p_vade_bas.is_(None), cek_senetler.c.vade >= p_vade_bas),
            or_(p_vade_son.is_(None), cek_senetler.c.vade <= p_vade_son),
            or_(
                func.lower(cek_senetler.c.seri_no).contains(aranan, autoescape=True),
                func.lower(func.coalesce(cek_senetler.c.kesideci, "")).contains(aranan, autoescape=True),
                func.lower(func.coalesce(cek_senetler.c.banka_adi, "")).contains(aranan, autoescape=True),
            ),
        )
    ).scalar_one()
    satirlar = db.execute(
        select(cek_senetler)
        .where(
            cek_senetler.c.company_id == cid,
            or_(p_tur.is_(None), cek_senetler.c.tur == p_tur),
            or_(p_yon.is_(None), cek_senetler.c.yon == p_yon),
            or_(p_durum.is_(None), cek_senetler.c.portfoy_durumu == p_durum),
            or_(p_musteri.is_(None), cek_senetler.c.customer_id == p_musteri),
            or_(p_tedarikci.is_(None), cek_senetler.c.supplier_id == p_tedarikci),
            or_(p_vade_bas.is_(None), cek_senetler.c.vade >= p_vade_bas),
            or_(p_vade_son.is_(None), cek_senetler.c.vade <= p_vade_son),
            or_(
                func.lower(cek_senetler.c.seri_no).contains(aranan, autoescape=True),
                func.lower(func.coalesce(cek_senetler.c.kesideci, "")).contains(aranan, autoescape=True),
                func.lower(func.coalesce(cek_senetler.c.banka_adi, "")).contains(aranan, autoescape=True),
            ),
        )
        .order_by(cek_senetler.c.vade.asc(), cek_senetler.c.id.asc())
        .limit(limit)
        .offset(offset)
    ).mappings().all()
    rol = istek_rolu(request)
    return {
        "total": int(toplam),
        "limit": limit,
        "offset": offset,
        "items": [_cek_gorunum(s, rol) for s in satirlar],
    }


@router.post("", status_code=201, response_model=CekSenet)
def cek_senet_olustur(
    payload: CekSenetGirdisi, request: Request, db: Session = Depends(get_db)
) -> dict:
    cid = company_id(request)
    uid = _cek_kullanici_kimligi(request)
    _cek_taraf_dogrula(db, cid, payload)
    evrak_id = _cek_ekle(db, cid, uid, payload)
    _cek_olusturma_kaydi(db, request, cid, uid, evrak_id, payload, bordro=False)
    db.commit()
    return _cek_gorunum(_cek_evrak(db, cid, evrak_id), istek_rolu(request))


@router.post("/bordro", status_code=201, response_model=BordroSonucu)
def cek_senet_bordro(
    payload: BordroGirdisi, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Toplu giriş: HEP-YA-HİÇ.

    Satırların TAMAMI önce doğrulanır (şema: FastAPI 422; taraf: aynı firmada
    yoksa 422 ve satır numarası). Yazım TEK işlemdedir: herhangi bir satır
    veritabanında reddedilirse (CHECK/FK) işlem geri alınır ve HİÇBİR satır
    kalmaz. Yanıt, yazılan kimlikleri GİRİŞ SIRASIYLA döndürür.
    """
    cid = company_id(request)
    uid = _cek_kullanici_kimligi(request)
    for sira, satir in enumerate(payload.satirlar):
        _cek_taraf_dogrula(db, cid, satir, sira=sira)
    kimlikler: list[int] = []
    try:
        for satir in payload.satirlar:
            evrak_id = _cek_ekle(db, cid, uid, satir)
            _cek_olusturma_kaydi(db, request, cid, uid, evrak_id, satir, bordro=True)
            kimlikler.append(evrak_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ids": kimlikler}


@router.get("/{evrak_id}", response_model=CekSenet)
def cek_senet_detay(evrak_id: int = Path(ge=1, le=INT4_UST), *, request: Request, db: Session = Depends(get_db)) -> dict:
    return _cek_gorunum(_cek_evrak(db, company_id(request), evrak_id), istek_rolu(request))


#: Hedef -> yükte BULUNABİLECEK alanlar. Listede olmayan dolu alan 422'dir:
#: sessizce yok saymak, istemciye yazılmayan bir değeri yazılmış sandırırdı.
_IZINLI_ALANLAR: dict[str, frozenset[str]] = {
    "tahsile_verildi": frozenset({"not_metni"}),
    "portfoyde": frozenset({"not_metni"}),
    TAHSIL_EDILDI: frozenset({"tahsil_hesap_id", "tahsil_tarihi", "not_metni"}),
    CIRO_EDILDI: frozenset({"endorsed_supplier_id", "endorsed_date", "not_metni"}),
    KARSILIKSIZ: frozenset({"not_metni"}),
    IADE: frozenset({"not_metni"}),
}
_YUK_ALANLARI = ("tahsil_hesap_id", "tahsil_tarihi", "endorsed_supplier_id", "endorsed_date", "not_metni")


@router.post("/{evrak_id}/durum-degistir", response_model=CekSenet)
def cek_senet_durum_degistir(
    payload: DurumDegistir,
    request: Request,
    evrak_id: int = Path(ge=1, le=INT4_UST),
    db: Session = Depends(get_db),
) -> dict:
    """Durum makinesi. Sıra: 404 -> 409 (geçiş/yön) -> 422 (yük).

    Yazım bir CAS'tır: ``WHERE portfoy_durumu = <okunan>``. Arada başka bir
    istek durumu değiştirdiyse satır güncellenmez ve 409 döner — iki
    eşzamanlı "tahsil edildi" aynı çeki iki kez kapatamaz.
    """
    cid = company_id(request)
    uid = _cek_kullanici_kimligi(request)
    evrak = _cek_evrak(db, cid, evrak_id)
    kaynak = str(evrak["portfoy_durumu"])
    hedef = payload.hedef
    try:
        gecis_dogrula(kaynak, hedef, yon=str(evrak["yon"]))
    except GecisHatasi as exc:
        raise HTTPException(
            409, {"code": exc.kod, "message": exc.mesaj, "from": kaynak, "to": hedef}
        ) from None

    izinli = _IZINLI_ALANLAR[hedef]
    fazla = [a for a in _YUK_ALANLARI if getattr(payload, a) is not None and a not in izinli]
    if fazla:
        raise HTTPException(422, f"'{hedef}' geçişinde bu alanlar verilemez: {', '.join(fazla)}")

    degerler: dict[str, Any] = {"portfoy_durumu": hedef}
    if hedef == TAHSIL_EDILDI:
        if payload.tahsil_hesap_id is None or payload.tahsil_tarihi is None:
            raise HTTPException(422, "Tahsil için tahsil_hesap_id ve tahsil_tarihi zorunludur")
        if not _cek_tahsil_hesabi_uygun(db, cid, payload.tahsil_hesap_id):
            raise HTTPException(422, "Tahsil hesabı bulunamadı ya da kasa/banka hesabı değil")
        degerler["tahsil_hesap_id"] = payload.tahsil_hesap_id
        degerler["tahsil_tarihi"] = payload.tahsil_tarihi
    elif hedef == CIRO_EDILDI:
        if payload.endorsed_supplier_id is None:
            raise HTTPException(422, "Ciro için endorsed_supplier_id zorunludur")
        if not _cek_tedarikci_var(db, cid, payload.endorsed_supplier_id):
            raise HTTPException(422, "Ciro edilen tedarikçi bulunamadı")
        degerler["endorsed_supplier_id"] = payload.endorsed_supplier_id
        degerler["endorsed_date"] = payload.endorsed_date or date.today()
    if payload.not_metni:
        onceki = evrak.get("notlar") or ""
        ek = f"[{hedef}] {payload.not_metni.strip()}"
        degerler["notlar"] = f"{onceki}\n{ek}" if onceki else ek

    sonuc = db.execute(
        update(cek_senetler)
        .where(
            cek_senetler.c.company_id == cid,
            cek_senetler.c.id == evrak_id,
            cek_senetler.c.portfoy_durumu == kaynak,
        )
        # AÇIK SÜTUN KÜMESİ, `**sözlük` DEĞİL: kiracı kapsam kapısı çalışma
        # zamanında kurulan bir sözlüğün sütunlarını çözemez ve haklı olarak
        # "çözülemiyor" der. Değişmeyen sütunlar OKUNAN değerle yazılır; CAS
        # (`portfoy_durumu == kaynak`) arada başka bir yazımı zaten reddeder.
        .values(
            portfoy_durumu=hedef,
            tahsil_hesap_id=degerler.get("tahsil_hesap_id", evrak["tahsil_hesap_id"]),
            tahsil_tarihi=degerler.get("tahsil_tarihi", evrak["tahsil_tarihi"]),
            endorsed_supplier_id=degerler.get("endorsed_supplier_id", evrak["endorsed_supplier_id"]),
            endorsed_date=degerler.get("endorsed_date", evrak["endorsed_date"]),
            notlar=degerler.get("notlar", evrak["notlar"]),
        )
    )
    if int(sonuc.rowcount or 0) != 1:
        db.rollback()
        raise HTTPException(
            409, {"code": "CEK_DURUM_DEGISTI",
                  "message": "Evrakın durumu bu istek sırasında değişti; yeniden okuyun"}
        )
    log_activity(
        db, cid, uid, "cek_senet.durum", "cek_senet", evrak_id,
        f"Çek/senet durumu: {kaynak} -> {hedef} ({evrak['seri_no']})",
        {"from": kaynak, "to": hedef,
         **{k: (v.isoformat() if isinstance(v, date) else v)
            for k, v in degerler.items() if k not in ("portfoy_durumu", "notlar")}},
        correlation_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    return _cek_gorunum(_cek_evrak(db, cid, evrak_id), istek_rolu(request))


# DURUMLAR tüm hedefleri kapsamalı: yeni bir durum eklenip `_IZINLI_ALANLAR`
# unutulursa modül yüklenirken DÜŞER, çalışma anında KeyError değil.
assert set(_IZINLI_ALANLAR) == set(DURUMLAR), sorted(set(DURUMLAR) ^ set(_IZINLI_ALANLAR))

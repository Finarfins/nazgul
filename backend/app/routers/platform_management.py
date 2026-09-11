"""PLATFORM YÖNETİM PANELİ — salt-okunur uçlar (PP1, göç YOK).

Plan: ``docs/platform-paneli-kesif-2026-09-10.md`` §4 PP1. Yedi GET, yedisi de
``require_platform_operator`` arkasında (rol ``admin`` VE
``SUNGUR_PLATFORM_OPERATORS`` listesi). Ara katman bu önekte kiracı ÇÖZMEZ
(``platform_access.platform_yolu``); ``X-Company-Id`` yok sayılır.

--- NE GÖSTERİLİR, NE GÖSTERİLMEZ ------------------------------------------

Operatör SAYILARI ve META VERİYİ görür: firma adı ve durumu, üye sayısı, son
hareket zamanı, platform kullanıcılarının hesap bilgisi, kuyruk ve e-belge
durum dağılımları. Hiçbir kiracının TİCARİ satırı — cari, fatura kalemi,
tutar, stok, hayvan/tarla kaydı — ve hiçbir carinin iletişim/kimlik alanı bu
dosyadan çıkmaz (SEC-3b). Bunu ``tests/test_pp1_platform_paneli.py`` yanıtın
TÜM anahtar kümesini gezerek, ``tests/test_sec3b_cari_alan_envanteri.py``
statik taramayla çiviler: bu uçların hiçbiri o envantere girmez.

Kullanıcı listesi ``username`` döndürür (kendi kaydolan hesaplarda bu, giriş
için kullanılan e-posta adresidir — ``routers/auth.py::register``); ayrı bir
iletişim sütunu döndürülmez. Doğrulama listesi belirteç DEĞERİ ya da özeti
döndürmez, yalnız zamanları.

--- KİRACI KAPSAMI ---------------------------------------------------------

Aşağıdaki dört sorgu KİRACILAR ARASIDIR ve bu, sorunun kendisidir ("kaç firmada
kaç üye var", "kuyruk bir bütün olarak akıyor mu"). Her biri
``tests/test_core_tenant_scoping_guard.py::CEKIRDEK_KIRACI_ISTISNALARI``
içinde fonksiyon + ifade parmak iziyle AYRI AYRI lisanslıdır; fonksiyon
değişirse lisans kendiliğinden düşer. Hiçbiri satır içeriği döndürmez:
yalnız ``company_id``/``user_id`` anahtarları ve toplamlar.

N+1 YOK: sayfa kimlikleri önce çekilir, üye sayısı / son hareket / üyelikler
sayfa başına TEK gruplu sorguyla gelir. Alt sorgu kullanılmaz — depoda
``.subquery()`` sayısı SIFIRDA dondurulmuş (kapı: yeniden takma ad yüzeyi).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, case, func, select
from sqlalchemy.orm import Session

from ..auth import audit_logs, auth_rate_limits, email_verification_tokens, users, utcnow
from ..config import settings
from ..db import get_db
from ..field_stok_zamanlayici import canlilik, yas_saniye
from ..notifications.service import platform_kanal_sayaclari
from ..platform_access import require_platform_operator
from ..tenancy import companies, memberships

router = APIRouter(prefix="/platform", tags=["Platform Yönetimi"])

#: Sayfa tavanı. Bu uçlar operatör ekranıdır; tek istekte binlerce satır
#: istemenin meşru bir kullanımı yok.
SAYFA_TAVANI = 200

# ``activity_logs`` ve ``invoices`` için depoda Core ``Table`` nesnesi YOK
# (yalnız ham SQL ile kullanılıyorlar). Burada YALNIZ okunan sütunlarla, AYRI
# ve hiçbir zaman ``create_all`` edilmeyen bir metadata üzerinde tanımlanırlar.
# Python adı tablo adıyla AYNI tutulur ki kiracı kapsam kapısı ve Core sorgu
# envanteri onları GÖRSÜN (farklı bir ad iki kapıya da görünmez olurdu).
_okuma_metadata = MetaData()
activity_logs = Table(
    "activity_logs",
    _okuma_metadata,
    Column("company_id", Integer),
    Column("created_at", DateTime(timezone=True)),
)
invoices = Table(
    "invoices",
    _okuma_metadata,
    Column("company_id", Integer),
    Column("einvoice_status", String(20)),
)


# ---------------------------------------------------------------------------
# Yanıt modelleri — sözleşme ön yüz tiplerine buradan akar (types.gen.ts).
# ---------------------------------------------------------------------------
class SirketSayilari(BaseModel):
    total: int
    active: int
    inactive: int


class KullaniciSayilari(BaseModel):
    total: int
    verified: int
    unverified: int


class KuyrukKanali(BaseModel):
    channel: str
    pending: int
    failed: int
    sent_last_24h: int
    oldest_pending_age_seconds: int | None


class KuyrukOzeti(BaseModel):
    pending: int
    failed: int
    oldest_pending_age_seconds: int | None


class PlatformOzeti(BaseModel):
    companies: SirketSayilari
    users: KullaniciSayilari
    pending_verifications: int
    outbox: KuyrukOzeti
    field_stock_scheduler: dict[str, Any]
    rate_limit_blocks_last_24h: int
    einvoice_status_histogram: dict[str, int]


class PlatformSirketi(BaseModel):
    id: int
    name: str
    is_active: bool
    created_at: datetime | None
    member_count: int
    last_activity_at: datetime | None


class PlatformSirketListesi(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[PlatformSirketi]


class KullaniciUyeligi(BaseModel):
    company_id: int
    company_name: str
    company_is_active: bool
    is_default: bool


class PlatformKullanicisi(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    email_verified: bool
    must_change_password: bool
    created_at: datetime | None
    last_login_at: datetime | None
    memberships: list[KullaniciUyeligi]


class PlatformKullaniciListesi(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[PlatformKullanicisi]


class BekleyenDogrulama(BaseModel):
    user_id: int
    username: str
    created_at: datetime | None
    expires_at: datetime | None


class BekleyenDogrulamaListesi(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[BekleyenDogrulama]


class KuyrukSagligi(BaseModel):
    channels: list[KuyrukKanali]
    field_stock_scheduler: dict[str, Any]


class HizSiniriSatiri(BaseModel):
    action: str
    ip_address: str
    attempts: int
    last_at: datetime | None


class HizSiniriOzeti(BaseModel):
    window_hours: int
    retention_hours: int
    items: list[HizSiniriSatiri]


class EBelgeSirketi(BaseModel):
    company_id: int
    company_name: str
    total: int
    by_status: dict[str, int]


class EBelgeSagligi(BaseModel):
    izibiz_env: str
    companies: list[EBelgeSirketi]


# ---------------------------------------------------------------------------
# Sorgular. Kiracılar arası olanlar lisanslıdır (modül başlığı).
# ---------------------------------------------------------------------------
def _sirket_sayilari(db: Session) -> dict[str, int]:
    row = db.execute(
        select(
            func.count(companies.c.id).label("total"),
            func.sum(case((companies.c.is_active.is_(True), 1), else_=0)).label("active"),
        ).select_from(companies)
    ).mappings().one()
    total = int(row["total"] or 0)
    active = int(row["active"] or 0)
    return {"total": total, "active": active, "inactive": total - active}


def _kullanici_sayilari(db: Session) -> dict[str, int]:
    # ``email_verified`` NULL kabul eder; giriş kapısı NULL'u DOĞRULANMAMIŞ
    # sayar (``if not row["email_verified"]``, routers/auth.py). Aynı anlam.
    row = db.execute(
        select(
            func.count(users.c.id).label("total"),
            func.sum(case((users.c.email_verified.is_(True), 1), else_=0)).label("verified"),
        ).select_from(users)
    ).mappings().one()
    total = int(row["total"] or 0)
    verified = int(row["verified"] or 0)
    return {"total": total, "verified": verified, "unverified": total - verified}


def _bekleyen_dogrulama_sayisi(db: Session, simdi: datetime) -> int:
    return int(
        db.execute(
            select(func.count(email_verification_tokens.c.id))
            .select_from(email_verification_tokens)
            .where(
                email_verification_tokens.c.used_at.is_(None),
                email_verification_tokens.c.expires_at > simdi,
            )
        ).scalar_one()
        or 0
    )


def _hiz_siniri_bloklari(db: Session, pencere_basi: datetime) -> int:
    """Son pencerede 429 ile kesilen kimlik-öncesi istek sayısı.

    ``auth_rate_limits`` blokajı KAYDETMEZ (yalnız denemeleri tutar ve her
    tüketimde bir saatten eski satırları siler). Blokajın kalıcı izi, 429
    yanıtının firmasız denetim satırıdır: kayıt, parola sıfırlama ve giriş
    POST'ları kimlik öncesi olduğu için ``company_id IS NULL`` yazılır.
    """
    return int(
        db.execute(
            select(func.count(audit_logs.c.id))
            .select_from(audit_logs)
            .where(
                audit_logs.c.company_id.is_(None),
                audit_logs.c.status_code == 429,
                audit_logs.c.created_at >= pencere_basi,
            )
        ).scalar_one()
        or 0
    )


def _uye_sayilari(db: Session, sirket_kimlikleri: list[int]) -> dict[int, int]:
    """Sayfadaki firmaların üye sayısı — TEK gruplu sorgu."""
    if not sirket_kimlikleri:
        return {}
    rows = db.execute(
        select(memberships.c.company_id, func.count(memberships.c.id))
        .where(memberships.c.company_id.in_(sirket_kimlikleri))
        .group_by(memberships.c.company_id)
    ).all()
    return {int(r[0]): int(r[1]) for r in rows}


def _son_hareketler(db: Session, sirket_kimlikleri: list[int]) -> dict[int, Any]:
    """Sayfadaki firmaların EN SON ``activity_logs`` damgası — TEK gruplu sorgu."""
    if not sirket_kimlikleri:
        return {}
    rows = db.execute(
        select(activity_logs.c.company_id, func.max(activity_logs.c.created_at))
        .where(activity_logs.c.company_id.in_(sirket_kimlikleri))
        .group_by(activity_logs.c.company_id)
    ).all()
    return {int(r[0]): r[1] for r in rows}


def _kullanici_uyelikleri(db: Session, kullanici_kimlikleri: list[int]) -> dict[int, list[dict]]:
    """Sayfadaki kullanıcıların üyelikleri — TEK sorgu."""
    if not kullanici_kimlikleri:
        return {}
    rows = db.execute(
        select(
            memberships.c.user_id,
            memberships.c.company_id,
            memberships.c.is_default,
            companies.c.name,
            companies.c.is_active,
        )
        .join(companies, companies.c.id == memberships.c.company_id)
        .where(memberships.c.user_id.in_(kullanici_kimlikleri))
        .order_by(memberships.c.user_id, memberships.c.company_id)
    ).mappings().all()
    sonuc: dict[int, list[dict]] = {}
    for r in rows:
        sonuc.setdefault(int(r["user_id"]), []).append(
            {
                "company_id": int(r["company_id"]),
                "company_name": str(r["name"]),
                "company_is_active": bool(r["is_active"]),
                "is_default": bool(r["is_default"]),
            }
        )
    return sonuc


def _ebelge_dagilimi(db: Session) -> list[dict[str, Any]]:
    """Firma başına ``einvoice_status`` sayıları — TEK gruplu sorgu."""
    rows = db.execute(
        select(
            invoices.c.company_id,
            companies.c.name,
            invoices.c.einvoice_status,
            func.count().label("n"),
        )
        .join(companies, companies.c.id == invoices.c.company_id)
        .group_by(invoices.c.company_id, companies.c.name, invoices.c.einvoice_status)
        .order_by(invoices.c.company_id, invoices.c.einvoice_status)
    ).mappings().all()
    sirketler: dict[int, dict[str, Any]] = {}
    for r in rows:
        kayit = sirketler.setdefault(
            int(r["company_id"]),
            {"company_id": int(r["company_id"]), "company_name": str(r["name"]),
             "total": 0, "by_status": {}},
        )
        durum = str(r["einvoice_status"] or "NONE")
        kayit["by_status"][durum] = kayit["by_status"].get(durum, 0) + int(r["n"])
        kayit["total"] += int(r["n"])
    return list(sirketler.values())


def _tam_saniye(yas: Any) -> int | None:
    return None if yas is None else int(yas)


def _kuyruk_kanallari(db: Session) -> list[dict[str, Any]]:
    kanallar = []
    for k in platform_kanal_sayaclari(db):
        kanallar.append(
            {
                "channel": k["channel"],
                "pending": k["pending"],
                "failed": k["failed"],
                "sent_last_24h": k["sent_last_24h"],
                # #58'in canlılık ölçüsü: lehçeden bağımsız damga çözümü.
                # Tam saniyeye indirilir — depoda ikili kayan nokta tipi
                # yanıt sözleşmesine girmez (test_v2_9_decimal_contract).
                "oldest_pending_age_seconds": _tam_saniye(yas_saniye(k["oldest_pending_created_at"])),
            }
        )
    return kanallar


# ---------------------------------------------------------------------------
# Uçlar
# ---------------------------------------------------------------------------
@router.get("/overview", response_model=PlatformOzeti)
def platform_ozeti(request: Request, db: Session = Depends(get_db)) -> dict:
    """Platformun tek ekranlık özeti: yalnız SAYILAR."""
    require_platform_operator(request)
    simdi = utcnow()
    kanallar = _kuyruk_kanallari(db)
    yaslar = [k["oldest_pending_age_seconds"] for k in kanallar
              if k["oldest_pending_age_seconds"] is not None]
    histogram: dict[str, int] = {}
    for sirket in _ebelge_dagilimi(db):
        for durum, adet in sirket["by_status"].items():
            histogram[durum] = histogram.get(durum, 0) + adet
    return {
        "companies": _sirket_sayilari(db),
        "users": _kullanici_sayilari(db),
        "pending_verifications": _bekleyen_dogrulama_sayisi(db, simdi),
        "outbox": {
            "pending": sum(k["pending"] for k in kanallar),
            "failed": sum(k["failed"] for k in kanallar),
            "oldest_pending_age_seconds": max(yaslar) if yaslar else None,
        },
        "field_stock_scheduler": canlilik(db),
        "rate_limit_blocks_last_24h": _hiz_siniri_bloklari(db, simdi - timedelta(hours=24)),
        "einvoice_status_histogram": dict(sorted(histogram.items())),
    }


@router.get("/companies", response_model=PlatformSirketListesi)
def platform_sirketleri(
    request: Request,
    q: str | None = Query(None, max_length=200),
    active: bool | None = None,
    limit: int = Query(50, ge=1, le=SAYFA_TAVANI),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """Firmalar: durum, üye sayısı, son hareket zamanı."""
    require_platform_operator(request)
    # İSTEĞE BAĞLI SÜZGEÇLER SATIR İÇİ ve BAĞLI parametredir: boş arama
    # ``LIKE '%%'`` olur (her satır), ``active`` yoksa iki durum da kabul
    # edilir. Koşul listesi (``where(*liste)``) Core sorgu envanterinde
    # "variable-arg" sayılır ve bilerek kullanılmadı.
    aranan = (q or "").strip().lower()
    durumlar = [True, False] if active is None else [active]
    total = int(
        db.execute(
            select(func.count(companies.c.id))
            .select_from(companies)
            .where(
                func.lower(companies.c.name).contains(aranan, autoescape=True),
                companies.c.is_active.in_(durumlar),
            )
        ).scalar_one()
        or 0
    )
    sayfa = db.execute(
        select(companies.c.id, companies.c.name, companies.c.is_active, companies.c.created_at)
        .where(
            func.lower(companies.c.name).contains(aranan, autoescape=True),
            companies.c.is_active.in_(durumlar),
        )
        .order_by(companies.c.id)
        .limit(limit)
        .offset(offset)
    ).mappings().all()
    kimlikler = [int(r["id"]) for r in sayfa]
    uyeler = _uye_sayilari(db, kimlikler)
    hareketler = _son_hareketler(db, kimlikler)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": int(r["id"]),
                "name": str(r["name"]),
                "is_active": bool(r["is_active"]),
                "created_at": r["created_at"],
                "member_count": uyeler.get(int(r["id"]), 0),
                "last_activity_at": hareketler.get(int(r["id"])),
            }
            for r in sayfa
        ],
    }


@router.get("/users", response_model=PlatformKullaniciListesi)
def platform_kullanicilari(
    request: Request,
    q: str | None = Query(None, max_length=200),
    verified: bool | None = None,
    limit: int = Query(50, ge=1, le=SAYFA_TAVANI),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """Platform kullanıcıları ve firma üyelikleri.

    Rol hesap düzeyindedir (``app_users.role``); üyelik satırı rol TAŞIMAZ
    (``user_company_memberships``: user_id, company_id, is_default).
    """
    require_platform_operator(request)
    # Süzgeçler satır içi (bkz. ``platform_sirketleri``). NULL doğrulama
    # giriş kapısındaki anlamıyla DOĞRULANMAMIŞ sayılır.
    aranan = (q or "").strip().lower()
    dogrulama = [True, False] if verified is None else [verified]
    total = int(
        db.execute(
            select(func.count(users.c.id))
            .select_from(users)
            .where(
                func.lower(users.c.username).contains(aranan, autoescape=True)
                | func.lower(users.c.display_name).contains(aranan, autoescape=True),
                func.coalesce(users.c.email_verified, False).in_(dogrulama),
            )
        ).scalar_one()
        or 0
    )
    sayfa = db.execute(
        select(
            users.c.id,
            users.c.username,
            users.c.display_name,
            users.c.role,
            users.c.is_active,
            users.c.email_verified,
            users.c.must_change_password,
            users.c.created_at,
            users.c.last_login_at,
        )
        .where(
            func.lower(users.c.username).contains(aranan, autoescape=True)
            | func.lower(users.c.display_name).contains(aranan, autoescape=True),
            func.coalesce(users.c.email_verified, False).in_(dogrulama),
        )
        .order_by(users.c.id)
        .limit(limit)
        .offset(offset)
    ).mappings().all()
    uyelikler = _kullanici_uyelikleri(db, [int(r["id"]) for r in sayfa])
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": int(r["id"]),
                "username": str(r["username"]),
                "display_name": str(r["display_name"]),
                "role": str(r["role"]),
                "is_active": bool(r["is_active"]),
                "email_verified": r["email_verified"] is True or r["email_verified"] == 1,
                "must_change_password": bool(r["must_change_password"]),
                "created_at": r["created_at"],
                "last_login_at": r["last_login_at"],
                "memberships": uyelikler.get(int(r["id"]), []),
            }
            for r in sayfa
        ],
    }


@router.get("/verifications", response_model=BekleyenDogrulamaListesi)
def platform_dogrulamalari(
    request: Request,
    limit: int = Query(50, ge=1, le=SAYFA_TAVANI),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """Kullanılmamış ve süresi dolmamış doğrulama belirteçleri.

    Belirteç DEĞERİ ve ÖZETİ (``token_hash``) seçilmez: özet, bağlantıyı
    doğrulayan anahtarın ta kendisidir.
    """
    require_platform_operator(request)
    simdi = utcnow()
    total = _bekleyen_dogrulama_sayisi(db, simdi)
    rows = db.execute(
        select(
            email_verification_tokens.c.user_id,
            users.c.username,
            email_verification_tokens.c.created_at,
            email_verification_tokens.c.expires_at,
        )
        .join(users, users.c.id == email_verification_tokens.c.user_id)
        .where(
            email_verification_tokens.c.used_at.is_(None),
            email_verification_tokens.c.expires_at > simdi,
        )
        .order_by(email_verification_tokens.c.created_at, email_verification_tokens.c.id)
        .limit(limit)
        .offset(offset)
    ).mappings().all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "user_id": int(r["user_id"]),
                "username": str(r["username"]),
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
            }
            for r in rows
        ],
    }


@router.get("/outbox/health", response_model=KuyrukSagligi)
def platform_kuyruk_sagligi(request: Request, db: Session = Depends(get_db)) -> dict:
    """Bildirim kuyruğu kanal başına + saha stok zamanlayıcısının canlılığı."""
    require_platform_operator(request)
    return {"channels": _kuyruk_kanallari(db), "field_stock_scheduler": canlilik(db)}


#: ``auth_rate_limits`` her tüketimde BİR saatten eski satırları siler
#: (``routers/auth.py::_consume_ip_limit``). Daha geniş bir pencere geçerli
#: bir sorudur ama tablonun cevabı en fazla bu kadar geriye gider; yanıt bunu
#: ``retention_hours`` olarak SÖYLER, gizlemez.
HIZ_SINIRI_SAKLAMA_SAATI = 1


@router.get("/rate-limits", response_model=HizSiniriOzeti)
def platform_hiz_sinirlari(
    request: Request,
    window_hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
) -> dict:
    """``auth_rate_limits`` eylem + IP başına toplamı. Kiracı verisi yok."""
    require_platform_operator(request)
    pencere_basi = utcnow() - timedelta(hours=window_hours)
    rows = db.execute(
        select(
            auth_rate_limits.c.action,
            auth_rate_limits.c.ip_address,
            func.count(auth_rate_limits.c.id).label("attempts"),
            func.max(auth_rate_limits.c.attempted_at).label("last_at"),
        )
        .where(auth_rate_limits.c.attempted_at >= pencere_basi)
        .group_by(auth_rate_limits.c.action, auth_rate_limits.c.ip_address)
        .order_by(func.count(auth_rate_limits.c.id).desc(), auth_rate_limits.c.action)
        .limit(SAYFA_TAVANI)
    ).mappings().all()
    return {
        "window_hours": window_hours,
        "retention_hours": HIZ_SINIRI_SAKLAMA_SAATI,
        "items": [
            {
                "action": str(r["action"]),
                "ip_address": str(r["ip_address"]),
                "attempts": int(r["attempts"]),
                "last_at": r["last_at"],
            }
            for r in rows
        ],
    }


@router.get("/edocuments/health", response_model=EBelgeSagligi)
def platform_ebelge_sagligi(request: Request, db: Session = Depends(get_db)) -> dict:
    """Firma başına e-belge durum sayıları ve entegratör ORTAMI.

    Yalnız ``izibiz_env`` (``test``/``live``) döner; entegratör kimlik
    bilgilerinin hiçbiri bu yanıta girmez.
    """
    require_platform_operator(request)
    return {"izibiz_env": str(settings.izibiz_env), "companies": _ebelge_dagilimi(db)}

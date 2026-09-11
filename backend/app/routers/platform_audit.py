"""Kiracıya bağlanamayan güvenlik denetim olaylarının OKUMA YOLU.

NEDEN AYRI BİR UÇ VAR. ``GET /api/auth/audit`` denetim kaydını
``company_id = <aktif firma>`` ile süzer. Firmasız satırlar — giriş denemeleri,
kayıt, parola sıfırlama, AUTH_REQUIRED 401'leri — hiçbir firmaya ait olmadığı
için o süzgecin DIŞINA düşer ve hiçbir kiracı okumasında görünmez.

Asıl şikâyet buydu: GÖRÜNMEZLİK. CHECK kısıtı firmasız satırın ANLAMINI
daraltır ama görünmezliği çözmez — kısıt eklendikten sonra da o satırları
okuyan kimse olmazsa kusur kapanmamış, yer değiştirmiş olur. Bu uç, o satırların
okunabildiği yerdir.

Satırlar AYRI TABLOYA taşınmadı ve bu bilinçli: bir saldırı kimlik-öncesinden
kimlik-sonrasına GEÇER (başarısız girişler, sonra başarılı giriş, sonra
firma sınırının yoklanması). Bu diziyi tek tabloda okumak mümkündür; iki tabloya
bölmek onu okunamaz yapardı.

YETKİ: ``require_platform_operator`` — firma yöneticisi değil, PLATFORM
operatörü. Bu satırlar tek bir kiracıya ait olmadığı için tek bir kiracının
yöneticisine de ait değildir.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import DateTime, Integer, String, bindparam, or_, select
from sqlalchemy.orm import Session

from ..auth import audit_logs, users
from ..db import get_db
from ..platform_access import require_platform_operator

router = APIRouter(prefix="/platform/audit", tags=["Platform Denetim"])


def _aktor_deseni(db: Session, username: str | None) -> str | None:
    """``username`` süzgecinin platform olaylarındaki karşılığı.

    Firmasız satır CHECK kısıtı gereği ``username``i NULL taşır; platform
    olayının aktörü ``failure_reason``da ``aktor=<id>; olay=...`` olarak durur
    (``platform_denetim.platform_olayi_yaz``). Ad bir hesaba çözülürse o
    önek aranır; ``;`` ayracı ``aktor=1``in ``aktor=12``yi yakalamasını önler.
    """
    if username is None:
        return None
    kimlik = db.execute(
        select(users.c.id).where(users.c.username == username)
    ).scalar_one_or_none()
    return None if kimlik is None else f"aktor={int(kimlik)};%"


@router.get("")
def list_untenanted_audit(
    request: Request,
    limit: int = 250,
    action: str | None = Query(None, max_length=20),
    ip_address: str | None = Query(None, max_length=80),
    username: str | None = Query(None, max_length=80),
    status_code: int | None = Query(None, ge=100, le=599),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: Session = Depends(get_db),
):
    """Hiçbir kiracıya bağlanamayan güvenlik denetim olayları.

    Süzgeçler (PP2; keşif §PP1 madde 4, PP1'de yapılmamıştı) SATIR İÇİ ve
    TİPLİ bağlı parametredir (``:p IS NULL OR sütun = :p``): koşul listesi
    Core sorgu envanterinde "variable-arg" sayılır. ``date_from`` dahil,
    ``date_to`` hariçtir. ``username`` hem sütunu (eski satırlar) hem platform
    olayının ``aktor=<id>`` notunu eşler.
    """
    require_platform_operator(request)
    limit = min(max(limit, 1), 1000)
    p_eylem = bindparam("p_eylem", action, type_=String)
    p_ip = bindparam("p_ip", ip_address, type_=String)
    p_kullanici = bindparam("p_kullanici", username, type_=String)
    p_aktor = bindparam("p_aktor", _aktor_deseni(db, username), type_=String)
    p_durum = bindparam("p_durum", status_code, type_=Integer)
    p_bas = bindparam("p_bas", date_from, type_=DateTime(timezone=True))
    p_son = bindparam("p_son", date_to, type_=DateTime(timezone=True))
    rows = db.execute(
        select(audit_logs)
        .where(
            audit_logs.c.company_id.is_(None),
            or_(p_eylem.is_(None), audit_logs.c.action == p_eylem),
            or_(p_ip.is_(None), audit_logs.c.ip_address == p_ip),
            or_(
                p_kullanici.is_(None),
                audit_logs.c.username == p_kullanici,
                audit_logs.c.failure_reason.like(p_aktor),
            ),
            or_(p_durum.is_(None), audit_logs.c.status_code == p_durum),
            or_(p_bas.is_(None), audit_logs.c.created_at >= p_bas),
            or_(p_son.is_(None), audit_logs.c.created_at < p_son),
        )
        .order_by(audit_logs.c.id.desc())
        .limit(limit)
    ).mappings().all()
    return [dict(row) for row in rows]

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

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import audit_logs
from ..db import get_db
from ..platform_access import require_platform_operator

router = APIRouter(prefix="/platform/audit", tags=["Platform Denetim"])


@router.get("")
def list_untenanted_audit(
    request: Request,
    limit: int = 250,
    db: Session = Depends(get_db),
):
    """Hiçbir kiracıya bağlanamayan güvenlik denetim olayları."""
    require_platform_operator(request)
    limit = min(max(limit, 1), 1000)
    rows = db.execute(
        select(audit_logs)
        .where(audit_logs.c.company_id.is_(None))
        .order_by(audit_logs.c.id.desc())
        .limit(limit)
    ).mappings().all()
    return [dict(row) for row in rows]

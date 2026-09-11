from __future__ import annotations

import hmac
from typing import Mapping

from fastapi import HTTPException, Request

from .config import settings

#: PLATFORM YÜZEYİNİN ÖNEKİ. Sonundaki eğik çizgi ZORUNLU: "/api/platform"
#: öneki "/api/platformx"i de yakalardı. Bu önek altındaki uçlar TEK BİR
#: kiracıya ait değildir — kapıları kiracı üyeliği değil
#: ``require_platform_operator``dır.
PLATFORM_API_ONEKI = "/api/platform/"


def platform_yolu(path: str) -> bool:
    """Bu yol kiracı çözümünden MUAF mı (PP1).

    TEK YÜKLEM, TEK YER. Ara katman (``main.security_and_audit``) kiracıyı bu
    yüklem doğruysa ÇÖZMEZ, ``X-Company-Id``yi OKUMAZ ve
    ``request.state.company_id``yi ``None`` bırakır; denetim yazıcısı da
    firmasız satırın kimliğini aynı yüklemle ayırt eder. İkisi ayrı birer
    ``startswith`` yazsaydı biri değiştiğinde diğeri sessizce eski kalırdı.

    NEDEN MUAF: ölçüldü (PP1 öncesi, develop 6442794) — üyeliği olmayan bir
    operatör ``GET /api/platform/backups``ta 403 COMPANY_ACCESS_DENIED alıyor,
    yani yönlendiricideki asıl kapıya HİÇ ulaşamıyordu; üyeliği olan operatörün
    yedek olayı ise seçtiği KİRACININ ``activity_logs``una yazılıyordu.

    Muafiyet YETKİ VERMEZ: kimlik, CSRF ve zorunlu parola rotasyonu kapıları
    aynen koşar; asıl kapı her uçtaki ``require_platform_operator`` çağrısıdır.
    ``tests/test_pp1_platform_paneli.py`` bağlı HER ``/api/platform`` rotasının
    muaf, başka HİÇBİR rotanın muaf olmadığını çiviler.
    """
    return path.startswith(PLATFORM_API_ONEKI)


def platform_operator_entries(raw: str | None = None) -> frozenset[str]:
    return frozenset(
        item.strip().casefold()
        for item in (settings.sungur_platform_operators if raw is None else raw).split(",")
        if item.strip()
    )


def is_platform_operator(user: Mapping[str, object], raw: str | None = None) -> bool:
    if str(user.get("role", "")) != "admin":
        return False
    entries = platform_operator_entries(raw)
    user_id = str(user.get("id", ""))
    return user_id.isdigit() and any(
        entry.isdigit() and hmac.compare_digest(user_id, entry) for entry in entries
    )


def require_platform_operator(request: Request) -> None:
    user = getattr(request.state, "user", {})
    if not is_platform_operator(user):
        raise HTTPException(status_code=403, detail="Platform operatörü yetkisi gerekli")

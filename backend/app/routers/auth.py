from __future__ import annotations

import logging
import secrets
import json
import time
from urllib import parse, request as urlrequest
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..activity_log import log_request_activity
from ..auth import (
    ROLE_PERMISSIONS,
    auth_rate_limits,
    audit_logs,
    auth_tokens,
    authenticate,
    can_assign_role,
    can_manage_role,
    clear_login_failures,
    csrf_is_valid,
    email_verification_tokens,
    extract_access_token,
    password_reset_tokens,
    hash_password,
    issue_csrf_token,
    issue_refresh_token,
    issue_token,
    login_lock_status,
    permissions_for,
    record_login_failure,
    revoke_refresh_family_for_user,
    revoke_refresh_token,
    revoke_token,
    revoke_user_access_tokens,
    revoke_user_refresh_tokens,
    rotate_refresh_token,
    RefreshPasswordChangeRequired,
    token_digest,
    users,
    utcnow,
    verify_password,
)
from ..config import settings
from ..db import get_db
from ..email_verification import (
    create_verification_token,
    deliver_now,
    queue_existing_account_notice,
    queue_verification_email,
)
from ..firma_profilleri import FirmaProfili, profilleri_birlestir
from ..password_reset import create_reset_token, queue_reset_email
from ..tenancy import branches, companies, memberships, user_companies
from ..platform_access import is_platform_operator

router = APIRouter(tags=["Kimlik Doğrulama"])
logger = logging.getLogger("yerel_hesap.auth")

# Deliberate policy decision: interactive accounts require a minimum length and
# nothing else. Character-class rules (upper/lower/digit/symbol) were dropped
# because they pushed users towards predictable substitutions without adding
# real entropy. Online guessing stays bounded by the existing login rate limit
# (five failed attempts lock the account); the denylist below still blocks the
# handful of secrets an attacker would try first.
#
# Independent of this constant: BOOTSTRAP_ADMIN_PASSWORD keeps its own, stricter
# production floor in app/config.py — that is an operator secret, not a user
# account, and is intentionally not relaxed here.
PASSWORD_MIN_LENGTH = 6
REGISTER_RESPONSE_FLOOR_SECONDS = 0.25

# Rejected regardless of length. These are historical defaults and trivially
# guessable secrets; membership is checked case-insensitively.
COMMON_WEAK_PASSWORDS = frozenset(
    {
        "admin123",
        "administrator",
        "password",
        "password123",
        "password1234",
        "parola123456",
        "sifre12345678",
        "123456789012",
        "qwertyuiop12",
        "sungurtarim1",
        "yerelhesap12",
    }
)


def _reject_weak_password(value: str) -> str:
    if value.strip().lower() in COMMON_WEAK_PASSWORDS:
        raise ValueError("Parola çok yaygın; daha güçlü bir parola seçin")
    return value


#: Mobil kabuğun kendini tanıttığı başlık. Web SPA'sı bu başlığı GÖNDERMEZ —
#: ölçüldü, varsayılmadı: ``frontend/src/api.ts`` istek yorumlayıcısı yalnız
#: ``X-Company-ID`` ve ``X-CSRF-Token`` ekler, ``AuthContext`` login çağrısına
#: başlık vermez. Bu yüzden başlığın YOKLUĞU tarayıcı yolunun tanımıdır ve
#: bugünkü davranış bit bit korunur.
MOBILE_CLIENT_KIND = "mobile"


def _is_mobile_client(client_kind: str | None) -> bool:
    return (client_kind or "").strip().lower() == MOBILE_CLIENT_KIND


def _session_user_agent(user_agent: str | None, *, mobile: bool) -> str | None:
    """Oturum satırına istemci TÜRÜNÜ de yazar — göç GEREKTİRMEDEN.

    ``auth_refresh_tokens.user_agent`` zaten var ve zaten serbest metin. Mobil
    oturumları ayırt etmek için yeni bir sütun açmak bir göç demekti; bunun
    yerine tür, metnin ÖNÜNE ``mobile:`` öneki olarak yazılır. Böylece
    "hangi oturum telefondan" sorusu ileride şema değişmeden yanıtlanabilir.
    Web yolunda metin DEĞİŞMEZ; önek yalnız mobil yolda eklenir.
    """

    if not mobile:
        return user_agent
    return f"{MOBILE_CLIENT_KIND}:{user_agent or ''}"


class RefreshPayload(BaseModel):
    """Çerezsiz istemcinin yenileme gövdesi.

    Alan İSTEĞE BAĞLI, çünkü tarayıcı SPA'sı bu uca boş nesne (``{}``) gönderir
    (``frontend/src/api.ts``); zorunlu alan o çağrıyı 422'ye düşürürdü.
    """

    refresh_token: str | None = Field(default=None, min_length=1, max_length=512)


class LogoutPayload(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=1, max_length=512)


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Kullanıcı adı boş olamaz")
        return normalized


class ChangePasswordPayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=200)

    @field_validator("new_password")
    @classmethod
    def _new_password_strength(cls, value: str) -> str:
        return _reject_weak_password(value)


class UserPayload(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=200)
    role: str = Field(default="rapor")

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        return _reject_weak_password(value)

    @model_validator(mode="after")
    def _password_not_username(self) -> "UserPayload":
        if self.password.strip().lower() == self.username.strip().lower():
            raise ValueError("Parola kullanıcı adıyla aynı olamaz")
        return self


class RegisterPayload(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=200)
    password_confirmation: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=10, max_length=10)
    terms_accepted: bool
    turnstile_token: str | None = Field(default=None, max_length=4096)
    # Faz 5.2 — kayıt anındaki iş kolu seçimi. İSTEĞE BAĞLIDIR: zorunlu
    # yapmak, bu uca bugün istek atan her istemciyi ve kayıt akışını kırardı.
    # Boş liste "seçilmedi" demektir ve depoda `''` olarak durur.
    profiller: list[FirmaProfili] = Field(default_factory=list)

    @field_validator("company_name", "display_name")
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Bu alan boş olamaz")
        return normalized

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if (
            "@" not in normalized
            or normalized.startswith("@")
            or normalized.endswith("@")
            or "." not in normalized.rsplit("@", 1)[1]
        ):
            raise ValueError("Geçerli bir e-posta adresi girin")
        return normalized

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) != 10 or not normalized.isdigit() or normalized.startswith("0"):
            raise ValueError("Cep telefonu başında 0 olmadan 10 rakam olmalıdır")
        return normalized

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        return _reject_weak_password(value)

    @model_validator(mode="after")
    def _password_not_email(self) -> "RegisterPayload":
        if self.password.strip().lower() == self.email:
            raise ValueError("Parola e-posta adresiyle aynı olamaz")
        if self.password != self.password_confirmation:
            raise ValueError("Şifreler eşleşmiyor")
        if not self.terms_accepted:
            raise ValueError("Kullanım Koşullarını kabul etmelisiniz")
        return self


class ResendVerificationPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class ForgotPasswordPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class ResetPasswordPayload(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=200)

    @field_validator("new_password")
    @classmethod
    def _new_password_strength(cls, value: str) -> str:
        return _reject_weak_password(value)


def _tax_number_algorithm_valid(value: str) -> bool:
    digits = [int(item) for item in value]
    if len(digits) == 11:
        if digits[0] == 0:
            return False
        tenth = ((sum(digits[index] for index in (0, 2, 4, 6, 8)) * 7)
                  - sum(digits[index] for index in (1, 3, 5, 7))) % 10
        eleventh = sum(digits[:10]) % 10
        return digits[9] == tenth and digits[10] == eleventh
    total = 0
    for index in range(9):
        value_at_position = (digits[index] + 9 - index) % 10
        total += (value_at_position * (2 ** (9 - index))) % 9 if value_at_position else 0
    return (10 - (total % 10)) % 10 == digits[9]


def validate_tax_number(value: str | None) -> tuple[str | None, str | None]:
    normalized = (value or "").strip()
    if not normalized:
        return None, None
    if len(normalized) not in {10, 11} or not normalized.isdigit():
        raise ValueError("VKN/TCKN yalnızca 10 veya 11 rakam olmalıdır")
    warning = None
    if not _tax_number_algorithm_valid(normalized):
        warning = "VKN doğrulanamadı, ayarlardan düzeltebilirsiniz."
    return normalized, warning


#: Cloudflare'ın BELGELENMİŞ test gizli anahtarları. Bunlar gerçek bir
#: doğrulama yapmaz: `1x...` her isteği GEÇİRİR, `2x...` her isteği reddeder,
#: `3x...` "token zaten kullanıldı" döndürür. Geliştirmede ve CI'da doğru
#: araçtırlar; PRODUCTION'da varlıkları korumanın FİİLEN KAPALI olduğu
#: anlamına gelir.
#:
#: NEDEN AYRI BİR KONTROL GEREKTİ: config.py production'da
#: TURNSTILE_SECRET_KEY'i zorunlu tutuyor, ama yalnızca DOLU olmasına bakıyor.
#: Test anahtarı bu kontrolü geçiyor ve sistem "bot koruması yapılandırılmış"
#: görünüyordu. Ölçüldü (2026-08-08): canlıda kayıt sayfası
#: `1x00000000000000000000AA` site anahtarıyla yayındaydı, yani her bot
#: doğrulamayı geçiyordu. "Var mı" kontrolü, "işe yarıyor mu" sorusunu
#: cevaplamıyor.
_TURNSTILE_TEST_SECRETS = frozenset({
    "1x0000000000000000000000000000000AA",
    "2x0000000000000000000000000000000AA",
    "3x0000000000000000000000000000000AA",
})


def _turnstile_test_key_in_production() -> bool:
    return (
        settings.is_production
        and (settings.turnstile_secret_key or "").strip() in _TURNSTILE_TEST_SECRETS
    )


def _verify_turnstile(token: str | None, ip_address: str) -> None:
    # PRODUCTION'DA TEST ANAHTARI = KORUMA YOK. Bu durumda isteği KABUL ETMEK,
    # korumayı sessizce kapatmak olurdu.
    #
    # NEDEN AÇILIŞTA DEĞİL DE BURADA REDDEDİYORUZ: config.py'nin kalıbı
    # eksik yapılandırmayı açılışta reddetmek (SMTP'de öyle yapılıyor). Ama
    # burada boşluk YALNIZ KAYIT AKIŞINDA; açılışı engellemek tüm siteyi
    # indirirdi ve mevcut kullanıcıların oturumunu keserdi. Orantılı olan,
    # yalnız açık olan kapıyı kapatmak.
    if _turnstile_test_key_in_production():
        logger.error(
            "TURNSTILE_SECRET_KEY bir Cloudflare TEST anahtarı; production'da "
            "kayıt reddediliyor. Gerçek anahtar çifti tanımlanmalı."
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Kayıt geçici olarak kapalı: bot koruması yapılandırılmamış. "
                "Lütfen yönetici ile iletişime geçin."
            ),
        )
    if not settings.turnstile_secret_key:
        logger.warning("TURNSTILE_SECRET_KEY tanımlı değil; bot doğrulaması atlandı")
        return
    if not token:
        raise HTTPException(status_code=400, detail="Bot doğrulaması başarısız")
    body = parse.urlencode(
        {
            "secret": settings.turnstile_secret_key,
            "response": token,
            "remoteip": ip_address,
        }
    ).encode()
    try:
        with urlrequest.urlopen(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=body,
            timeout=5,
        ) as response:
            valid = bool(json.loads(response.read()).get("success"))
    except Exception:
        logger.exception("Turnstile doğrulama servisine ulaşılamadı")
        valid = False
    if not valid:
        raise HTTPException(status_code=400, detail="Bot doğrulaması başarısız")


def _registration_response(started_at) -> dict[str, str]:
    remaining = REGISTER_RESPONSE_FLOOR_SECONDS - (time.monotonic() - started_at)
    if remaining > 0:
        time.sleep(remaining)
    return {"message": "Doğrulama e-postası gönderildi."}


def _consume_ip_limit(
    db: Session, *, action: str, ip_address: str, maximum: int
) -> None:
    cutoff = utcnow() - timedelta(hours=1)
    db.execute(
        delete(auth_rate_limits).where(auth_rate_limits.c.attempted_at < cutoff)
    )
    count = db.execute(
        select(func.count())
        .select_from(auth_rate_limits)
        .where(
            auth_rate_limits.c.action == action,
            auth_rate_limits.c.ip_address == ip_address,
            auth_rate_limits.c.attempted_at >= cutoff,
        )
    ).scalar_one()
    if int(count) >= maximum:
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Çok fazla deneme. Bir saat sonra tekrar deneyin.",
        )
    db.execute(
        insert(auth_rate_limits).values(
            action=action, ip_address=ip_address, attempted_at=utcnow()
        )
    )
    db.commit()


def _cookie_common() -> dict[str, object]:
    return {
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
    }


def _set_session_cookies(
    response: Response,
    *,
    access_token: str,
    access_expires: datetime,
    refresh_token: str,
    refresh_expires: datetime,
    csrf_token: str,
) -> None:
    common = _cookie_common()
    access_max_age = max(int((access_expires - utcnow()).total_seconds()), 1)
    refresh_max_age = max(int((refresh_expires - utcnow()).total_seconds()), 1)
    response.set_cookie(
        settings.access_cookie_name,
        access_token,
        max_age=access_max_age,
        expires=access_expires,
        path="/",
        httponly=True,
        **common,
    )
    response.set_cookie(
        settings.refresh_cookie_name,
        refresh_token,
        max_age=refresh_max_age,
        expires=refresh_expires,
        path="/api/auth",
        httponly=True,
        **common,
    )
    # Double-submit CSRF token: readable by the SPA, never accepted as an auth
    # credential, and compared against X-CSRF-Token on unsafe cookie requests.
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=refresh_max_age,
        expires=refresh_expires,
        path="/",
        httponly=False,
        **common,
    )


def _clear_session_cookies(response: Response) -> None:
    common = _cookie_common()
    response.delete_cookie(
        settings.access_cookie_name,
        path="/",
        httponly=True,
        **common,
    )
    response.delete_cookie(
        settings.refresh_cookie_name,
        path="/api/auth",
        httponly=True,
        **common,
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        path="/",
        httponly=False,
        **common,
    )


def _session_payload(db: Session, user: dict) -> dict:
    return {
        "user": {
            key: user[key]
            for key in (
                "id",
                "username",
                "display_name",
                "role",
                "must_change_password",
            )
        },
        # Resolved through the same helper as the gate itself. Keeping a second
        # copy of the fallback here would let the SPA be told it may show
        # read-only screens while every request behind them is denied 403.
        "permissions": sorted(permissions_for(user["role"])),
        "is_platform_operator": is_platform_operator(user),
        "companies": user_companies(db, int(user["id"])),
    }


@router.post("/auth/login")
def login(
    payload: LoginPayload,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_client_kind: str | None = Header(default=None, alias="X-Client-Kind"),
):
    username = payload.username
    ip_address = request.client.host if request.client else "unknown"
    locked_until = login_lock_status(db, username, ip_address)
    if locked_until:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Çok fazla başarısız deneme. 15 dakika sonra tekrar deneyin.",
        )
    user = authenticate(db, username, payload.password)
    if not user:
        record_login_failure(db, username, ip_address)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kullanıcı adı veya şifre hatalı",
        )
    if not user["email_verified"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "EMAIL_VERIFICATION_REQUIRED",
                "message": "Giriş yapmadan önce e-posta adresinizi doğrulayın",
            },
        )
    clear_login_failures(db, username, ip_address)
    mobile = _is_mobile_client(x_client_kind)
    access_token, access_expires = issue_token(db, int(user["id"]))
    refresh_token, refresh_expires, _ = issue_refresh_token(
        db,
        int(user["id"]),
        ip_address=ip_address,
        user_agent=_session_user_agent(
            request.headers.get("user-agent"), mobile=mobile
        ),
    )
    body = {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_at": access_expires.isoformat(),
        **_session_payload(db, user),
    }
    if mobile:
        # MOBİL YOLDA ÇEREZ YOK — ve bu bir tercih değil, ZORUNLULUK. Capacitor
        # kabuğunun origin'i (`https://localhost`) API alan adından farklıdır;
        # HttpOnly çerez oraya ya hiç yazılmaz ya da üçüncü-taraf çerez
        # engelleriyle sessizce düşer. Çerez yazıp GÖVDEYE de token koymak, iki
        # ayrı oturum kanalı demekti: biri düşerken diğeri yaşar ve "çıkış
        # yaptım" diyen kullanıcının oturumu ayakta kalırdı.
        #
        # HAM refresh token gövdede YALNIZ BURADA ve YALNIZ BİR KEZ görünür;
        # bundan sonrası ``/auth/refresh`` rotasyonudur. CSRF çerezi de
        # yazılmaz: çerez yoksa taklit edilecek bir kimlik bilgisi de yoktur.
        body["refresh_token"] = refresh_token
        body["refresh_expires_at"] = refresh_expires.isoformat()
        return body
    csrf_token = issue_csrf_token()
    _set_session_cookies(
        response,
        access_token=access_token,
        access_expires=access_expires,
        refresh_token=refresh_token,
        refresh_expires=refresh_expires,
        csrf_token=csrf_token,
    )
    # access_token remains in the JSON response for existing non-browser API
    # clients. The SPA deliberately ignores it and authenticates with cookies.
    return body


@router.post("/auth/register", status_code=200)
def register(
    payload: RegisterPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    started_at = time.monotonic()
    ip_address = request.client.host if request.client else "unknown"
    _verify_turnstile(payload.turnstile_token, ip_address)
    _consume_ip_limit(
        db, action="register", ip_address=ip_address, maximum=5
    )
    email = payload.email
    _consume_ip_limit(
        db,
        action="register_email",
        ip_address=token_digest(email),
        maximum=5,
    )
    existing = db.execute(
        select(users.c.id, users.c.email, memberships.c.company_id)
        .join(memberships, memberships.c.user_id == users.c.id)
        .where(func.lower(users.c.email) == email)
        .limit(1)
    ).mappings().first()
    if existing:
        hash_password(payload.password)
        existing_company_id = int(existing["company_id"])
        notification_id = queue_existing_account_notice(
            db,
            company_id=existing_company_id,
            user_id=int(existing["id"]),
            email=str(existing["email"]),
        )
        db.commit()
        # Gönderim commit'ten SONRA denenir: geri alınan bir kayıt işlemi
        # hiçbir koşulda dışarıya mesaj üretmemelidir.
        deliver_now(
            db, company_id=existing_company_id, notification_id=notification_id
        )
        return _registration_response(started_at)
    db.rollback()

    verification_link = ""
    notification_id = 0
    company_id = 0
    try:
        with db.begin():
            company_id = int(
                db.execute(
                    insert(companies)
                    .values(
                        name=payload.company_name,
                        tax_number=None,
                        is_active=True,
                        profiller=profilleri_birlestir(payload.profiller),
                        created_at=utcnow(),
                    )
                    .returning(companies.c.id)
                ).scalar_one()
            )
            db.execute(
                insert(branches).values(
                    company_id=company_id,
                    name="Merkez",
                    is_active=True,
                    created_at=utcnow(),
                )
            )
            user_id = int(
                db.execute(
                    insert(users)
                    .values(
                        username=email,
                        email=email,
                        email_verified=False,
                        phone=payload.phone,
                        terms_accepted_at=utcnow(),
                        display_name=payload.display_name,
                        password_hash=hash_password(payload.password),
                        role="admin",
                        is_active=True,
                        must_change_password=False,
                        created_at=utcnow(),
                    )
                    .returning(users.c.id)
                ).scalar_one()
            )
            db.execute(
                insert(memberships).values(
                    user_id=user_id,
                    company_id=company_id,
                    is_default=True,
                    created_at=utcnow(),
                )
            )
            _, verification_link = create_verification_token(
                db, user_id, company_id=company_id
            )
            # Kuyruk satırı kaynak transaction'ın İÇİNDE yazılır: kayıt geri
            # alınırsa outbox satırı da geri alınır.
            notification_id = queue_verification_email(
                db,
                company_id=company_id,
                user_id=user_id,
                email=email,
                link=verification_link,
            )
    except IntegrityError as exc:
        db.rollback()
        logger.info("Eşzamanlı yinelenen kayıt isteği genel yanıtla kapatıldı")
        return _registration_response(started_at)

    # Commit sonrası tek fırsatçı deneme. Başarısız olursa satır kuyrukta
    # kalır ve drenaj betiği devralır; kullanıcıya dönen yanıt değişmez.
    deliver_now(db, company_id=company_id, notification_id=notification_id)

    return _registration_response(started_at)


@router.get("/auth/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    now = utcnow()
    with db.begin():
        row = db.execute(
            select(email_verification_tokens).where(
                email_verification_tokens.c.token_hash == token_digest(token)
            )
        ).mappings().first()
        expires_at = (
            row["expires_at"].replace(tzinfo=timezone.utc)
            if row and row["expires_at"].tzinfo is None
            else row["expires_at"] if row else None
        )
        if not row or row["used_at"] is not None or expires_at <= now:
            raise HTTPException(
                status_code=400,
                detail="Doğrulama bağlantısı geçersiz veya süresi dolmuş",
            )
        updated = db.execute(
            update(email_verification_tokens)
            .where(
                email_verification_tokens.c.id == row["id"],
                email_verification_tokens.c.used_at.is_(None),
            )
            .values(used_at=now)
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=400, detail="Doğrulama bağlantısı kullanılmış")
        db.execute(
            update(users)
            .where(users.c.id == row["user_id"])
            .values(email_verified=True)
        )
    return {"message": "E-posta adresiniz doğrulandı. Giriş yapabilirsiniz."}


@router.post("/auth/resend-verification")
def resend_verification(
    payload: ResendVerificationPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    ip_address = request.client.host if request.client else "unknown"
    _consume_ip_limit(
        db, action="resend_verification", ip_address=ip_address, maximum=5
    )
    row = db.execute(
        select(users.c.id, users.c.email, users.c.email_verified).where(
            users.c.email == payload.email
        )
    ).mappings().first()
    if not row or row["email_verified"]:
        db.rollback()
        return {"message": "Adres uygunsa doğrulama e-postası gönderildi."}

    company_id = db.execute(
        select(memberships.c.company_id)
        .where(memberships.c.user_id == row["id"])
        .limit(1)
    ).scalar_one()
    _, link = create_verification_token(
        db, int(row["id"]), company_id=int(company_id)
    )
    notification_id = queue_verification_email(
        db,
        company_id=int(company_id),
        user_id=int(row["id"]),
        email=str(row["email"]),
        link=link,
    )
    db.commit()
    deliver_now(db, company_id=int(company_id), notification_id=notification_id)
    return {"message": "Adres uygunsa doğrulama e-postası gönderildi."}


# Yer tutucu adresler 0039 migration'ında üretildi: e-posta sütunu sonradan
# eklendiği için eski hesaplara ``legacy-user-<id>@legacy.invalid`` yazıldı. Bu
# adresler var olmayan bir alan adına gider. Sıfırlama postasını oraya kuyruğa
# almak hem kullanıcıya hiçbir şey ulaştırmaz hem de SES'te sert geri dönüş
# (bounce) üretip gönderim itibarını yakar.
LEGACY_EMAIL_DOMAIN = "@legacy.invalid"

# Cevap her durumda aynıdır: "adres kayıtlıysa gönderildi". Farklı cevap vermek
# bu ucu bir hesap-var-mı sorgulama aracına çevirirdi.
_FORGOT_PASSWORD_RESPONSE = {
    "message": "Adres kayıtlıysa şifre sıfırlama bağlantısı gönderildi."
}


@router.post("/auth/forgot-password")
def forgot_password(
    payload: ForgotPasswordPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    ip_address = request.client.host if request.client else "unknown"
    _consume_ip_limit(
        db, action="forgot_password", ip_address=ip_address, maximum=5
    )
    row = db.execute(
        select(users.c.id, users.c.email, users.c.is_active).where(
            users.c.email == payload.email
        )
    ).mappings().first()
    if (
        not row
        or not row["is_active"]
        or str(row["email"]).endswith(LEGACY_EMAIL_DOMAIN)
    ):
        db.rollback()
        return _FORGOT_PASSWORD_RESPONSE

    company_id = db.execute(
        select(memberships.c.company_id)
        .where(memberships.c.user_id == row["id"])
        .limit(1)
    ).scalar_one_or_none()
    if company_id is None:
        # Firmasız hesap kuyruğa satır yazamaz (``notifications.company_id``
        # zorunlu). Kullanıcıya yine aynı cevap döner.
        db.rollback()
        return _FORGOT_PASSWORD_RESPONSE

    _, link = create_reset_token(db, int(row["id"]), company_id=int(company_id))
    notification_id = queue_reset_email(
        db,
        company_id=int(company_id),
        user_id=int(row["id"]),
        email=str(row["email"]),
        link=link,
    )
    db.commit()
    deliver_now(db, company_id=int(company_id), notification_id=notification_id)
    return _FORGOT_PASSWORD_RESPONSE


@router.post("/auth/reset-password")
def reset_password(
    payload: ResetPasswordPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    ip_address = request.client.host if request.client else "unknown"
    _consume_ip_limit(db, action="reset_password", ip_address=ip_address, maximum=10)
    now = utcnow()
    with db.begin():
        row = db.execute(
            select(password_reset_tokens).where(
                password_reset_tokens.c.token_hash == token_digest(payload.token)
            )
        ).mappings().first()
        expires_at = (
            row["expires_at"].replace(tzinfo=timezone.utc)
            if row and row["expires_at"].tzinfo is None
            else row["expires_at"] if row else None
        )
        if not row or row["used_at"] is not None or expires_at <= now:
            raise HTTPException(
                status_code=400,
                detail="Sıfırlama bağlantısı geçersiz veya süresi dolmuş",
            )
        account = db.execute(
            select(users.c.id, users.c.username, users.c.is_active).where(
                users.c.id == row["user_id"]
            )
        ).mappings().first()
        if not account or not account["is_active"]:
            raise HTTPException(status_code=400, detail="Hesap kullanım dışı")
        if (
            payload.new_password.strip().lower()
            == str(account["username"]).strip().lower()
        ):
            raise HTTPException(
                status_code=400, detail="Parola kullanıcı adıyla aynı olamaz"
            )
        # Tokenı ÖNCE tüket. İki eşzamanlı istek aynı linki kullanırsa
        # ``used_at IS NULL`` koşulu yalnız birinde eşleşir; diğeri rowcount 0
        # görüp düşer ve şifre iki kez yazılmaz.
        consumed = db.execute(
            update(password_reset_tokens)
            .where(
                password_reset_tokens.c.id == row["id"],
                password_reset_tokens.c.used_at.is_(None),
            )
            .values(used_at=now)
        )
        if consumed.rowcount != 1:
            raise HTTPException(
                status_code=400, detail="Sıfırlama bağlantısı kullanılmış"
            )
        db.execute(
            update(users)
            .where(users.c.id == account["id"])
            .values(
                password_hash=hash_password(payload.new_password),
                must_change_password=False,
                # Sıfırlama linkini açabilen kişi posta kutusuna erişebiliyor
                # demektir; bu, e-posta sahipliğinin doğrulama linkiyle aynı
                # gücte kanıtıdır. Doğrulanmamış bir hesap aksi hâlde şifresini
                # sıfırlayıp yine giriş yapamazdı.
                email_verified=True,
            )
        )
        # Şifre sıfırlandıysa hesabın ele geçirilmiş olabileceği varsayılır:
        # tüm cihazlardaki oturumlar kapatılır. ``change-password``un aksine
        # yeni oturum AÇILMAZ — kullanıcı yeni şifresiyle giriş ekranına döner.
        revoke_user_access_tokens(db, int(account["id"]))
        revoke_user_refresh_tokens(db, int(account["id"]))
    return {"message": "Şifreniz güncellendi. Yeni şifrenizle giriş yapabilirsiniz."}


def _refresh_from_body(db: Session, request: Request, refresh_token: str) -> dict:
    """Çerezsiz (mobil) yenileme — AYNI rotasyon, AYNI yeniden kullanım tespiti.

    Kasıtlı olarak ``rotate_refresh_token`` çağrılır ve o fonksiyona
    DOKUNULMAZ: aile rotasyonu ve replay tespiti tek bir yerde durur. İki ayrı
    rotasyon yolu olsaydı, biri diğerinden sessizce ayrışır ve mobil yol
    replay'e açık kalırdı — testler bunu adıyla ölçüyor.

    Yanıt ham refresh tokenı TAŞIR, çünkü eski token bu çağrıyla ÖLDÜ; yenisi
    gövdede dönmezse istemcinin oturumu 15 dakika sonra kurtarılamaz biçimde
    biter. Çerez YAZILMAZ.
    """

    try:
        rotated = rotate_refresh_token(
            db,
            refresh_token,
            ip_address=request.client.host if request.client else None,
            user_agent=_session_user_agent(
                request.headers.get("user-agent"), mobile=True
            ),
        )
    except RefreshPasswordChangeRequired:
        # Çerez yolundaki ile AYNI gövde ve AYNI kod; tek fark çerez
        # temizliğinin olmaması — silinecek çerez yok.
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PASSWORD_CHANGE_REQUIRED",
                "message": "Oturumu yenilemeden önce şifrenizi değiştirin",
            },
        ) from None
    if not rotated:
        raise HTTPException(status_code=401, detail="Refresh oturumu geçersiz")
    new_refresh, refresh_expires, user = rotated
    access_token, access_expires = issue_token(db, int(user["id"]))
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_at": access_expires.isoformat(),
        "refresh_token": new_refresh,
        "refresh_expires_at": refresh_expires.isoformat(),
        **_session_payload(db, user),
    }


@router.post("/auth/refresh")
def refresh_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    payload: RefreshPayload | None = Body(default=None),
):
    cookie_refresh = request.cookies.get(settings.refresh_cookie_name)
    body_refresh = payload.refresh_token if payload else None
    # YOL SEÇİMİ ÇEREZE BAKAR, GÖVDEYE DEĞİL. Çerez VARSA bugünkü yol aynen
    # koşar — CSRF dahil. Tersi olsaydı, tarayıcıda çalışan bir saldırgan
    # gövdeye kendi seçtiği bir token koyup CSRF kapısını ATLATABİLİRDİ:
    # gövde yolu CSRF istemediği için kapı, saldırganın kontrolündeki bir
    # alanla kapatılıp açılır hale gelirdi.
    #
    # GÖVDE YOLUNDA CSRF YOK ve bu bir gevşetme DEĞİL. CSRF, tarayıcının
    # isteği OTOMATİK kimliklendirmesine karşıdır: çerez, saldırganın
    # sitesinden yapılan isteğe de iliştirilir. Gövdedeki token otomatik
    # iliştirilmez — saldırganın onu YAZABİLMESİ zaten tokenı bilmesi
    # demektir ve o noktada CSRF'in koruduğu şey çoktan gitmiştir.
    if cookie_refresh is None and body_refresh:
        return _refresh_from_body(db, request, body_refresh)
    if not csrf_is_valid(request):
        raise HTTPException(status_code=403, detail="CSRF doğrulaması başarısız")
    refresh_token = cookie_refresh
    if not refresh_token:
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh oturumu bulunamadı")
    try:
        rotated = rotate_refresh_token(
            db,
            refresh_token,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except RefreshPasswordChangeRequired:
        blocked = JSONResponse(
            status_code=403,
            content={
                "detail": {
                    "code": "PASSWORD_CHANGE_REQUIRED",
                    "message": "Oturumu yenilemeden önce şifrenizi değiştirin",
                }
            },
        )
        _clear_session_cookies(blocked)
        return blocked
    if not rotated:
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh oturumu geçersiz")
    new_refresh, refresh_expires, user = rotated
    access_token, access_expires = issue_token(db, int(user["id"]))
    csrf_token = issue_csrf_token()
    _set_session_cookies(
        response,
        access_token=access_token,
        access_expires=access_expires,
        refresh_token=new_refresh,
        refresh_expires=refresh_expires,
        csrf_token=csrf_token,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_at": access_expires.isoformat(),
        **_session_payload(db, user),
    }


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    payload: LogoutPayload | None = Body(default=None),
):
    token, source = extract_access_token(request)
    user = getattr(request.state, "user", None)
    if token:
        revoke_token(db, token)
    # ÇEREZSİZ (mobil) ÇIKIŞ. İstemci kendi refresh tokenını gövdede verir,
    # çünkü düşürülecek bir çerezi yoktur. Aile ancak token ÇAĞIRANIN ise
    # düşer: gövdeyi istemci yazar, yani sahiplik yüklemi olmasaydı bearer ile
    # kimliklenen bir çağıran BAŞKASININ refresh ailesini imha edebilirdi —
    # aşağıdaki çerez dalının tam da reddettiği tehlike.
    body_refresh = payload.refresh_token if payload else None
    if body_refresh and user:
        revoke_refresh_family_for_user(db, body_refresh, int(user["id"]))
    # Only a cookie-authenticated logout may revoke the refresh-cookie family.
    # When a bearer token wins authentication, require_logout_csrf intentionally
    # skips CSRF, and the refresh cookie can belong to a different principal
    # (stale or attacker-supplied) — revoking it would let a bearer-authed caller
    # destroy another user's refresh session. In that case revoke only the bearer
    # token and clear the cookies client-side.
    if source == "cookie":
        refresh_token = request.cookies.get(settings.refresh_cookie_name)
        if refresh_token:
            revoke_refresh_token(db, refresh_token, whole_family=True)
    _clear_session_cookies(response)


@router.post("/auth/logout-all", status_code=204)
def logout_all(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Bu kullanıcının TÜM oturumlarını kapatır — cihaz kaybı yolu.

    İKİ tablo da süpürülür ve bu ZORUNLU: yalnız refresh iptal edilseydi,
    dağıtılmış access tokenları 15 dakika daha canlı kalırdı (``config.py``:
    ``access_token_minutes``). Çalınmış bir telefonda 15 dakika, "hemen
    kapat" düğmesinin vaadini bozmaya yeter.

    HIZ SINIRI YOK — ölçüldü, atlanmadı. ``_consume_ip_limit`` IP başına
    saatlik sayaçtır ve KİMLİKSİZ uçlar (kayıt, şifre sıfırlama) içindir;
    burada çağıran ZATEN geçerli bir access token taşıyor, yani sınır bir
    saldırganı değil kendi hesabını kapatmaya çalışan kullanıcıyı
    engellerdi. Ucun kötüye kullanımı da yalnız çağıranın KENDİ oturumlarını
    düşürür; başka bir aktöre dokunmaz.
    """

    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Oturum geçersiz")
    user_id = int(user["id"])
    revoke_user_access_tokens(db, user_id)
    revoke_user_refresh_tokens(db, user_id)
    # İki yardımcı da BİLEREK commit etmiyor (app/auth.py): çağıran ikisini tek
    # işlemde kapatabilsin diye. Tek commit, "access düştü ama refresh yaşıyor"
    # ara durumunu imkansız kılar.
    db.commit()
    # Çağıran tarayıcıysa çerezleri de düşür; mobil istemcide silinecek çerez
    # yoktur ve bu satır zararsızdır.
    _clear_session_cookies(response)


@router.get("/auth/me")
def me(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Oturum geçersiz")
    return _session_payload(db, user)


@router.post("/auth/change-password")
def change_password(
    payload: ChangePasswordPayload,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    current_user = getattr(request.state, "user", None)
    if not current_user:
        raise HTTPException(status_code=401, detail="Oturum geçersiz")
    row = db.execute(
        select(users.c.id, users.c.password_hash).where(
            users.c.id == int(current_user["id"])
        )
    ).mappings().first()
    if not row or not verify_password(payload.current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Mevcut şifre hatalı")
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=400, detail="Yeni şifre mevcut şifreden farklı olmalıdır"
        )
    if payload.new_password.strip().lower() == str(current_user["username"]).strip().lower():
        raise HTTPException(
            status_code=400, detail="Parola kullanıcı adıyla aynı olamaz"
        )
    db.execute(
        update(users)
        .where(users.c.id == row["id"])
        .values(
            password_hash=hash_password(payload.new_password),
            must_change_password=False,
        )
    )
    # A password change invalidates every previously issued credential for this
    # user: all access tokens (including the one used for this request) and the
    # entire refresh-token population. Any other live session, on any device, is
    # terminated. A completely new session is then minted and returned so the
    # caller (browser cookies or a bearer client) can continue seamlessly.
    revoke_user_access_tokens(db, int(row["id"]))
    revoke_user_refresh_tokens(db, int(row["id"]))
    db.commit()
    access_token, access_expires = issue_token(db, int(row["id"]))
    new_refresh, refresh_expires, _ = issue_refresh_token(
        db,
        int(row["id"]),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookies(
        response,
        access_token=access_token,
        access_expires=access_expires,
        refresh_token=new_refresh,
        refresh_expires=refresh_expires,
        csrf_token=issue_csrf_token(),
    )
    return {
        "ok": True,
        "must_change_password": False,
        "access_token": access_token,
        "token_type": "bearer",
        "expires_at": access_expires.isoformat(),
    }


@router.get("/users")
def list_users(request: Request, db: Session = Depends(get_db)):
    cid = int(request.state.company_id)
    rows = db.execute(
        select(
            users.c.id,
            users.c.username,
            users.c.display_name,
            users.c.role,
            users.c.is_active,
            users.c.created_at,
            users.c.last_login_at,
            users.c.must_change_password,
        )
        .join(memberships, memberships.c.user_id == users.c.id)
        .where(memberships.c.company_id == cid)
        .order_by(users.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


@router.post("/users", status_code=201)
def create_user(
    payload: UserPayload, request: Request, db: Session = Depends(get_db)
):
    if payload.role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail="Geçersiz rol")
    current_role = str(request.state.user["role"])
    if not can_assign_role(current_role, payload.role):
        raise HTTPException(status_code=403, detail="Bu rolü atama yetkiniz yok")
    exists = db.execute(
        select(users.c.id).where(users.c.username == payload.username.strip())
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="Kullanıcı adı zaten var")
    result = db.execute(
        insert(users).values(
            username=payload.username.strip(),
            email=f"legacy-{secrets.token_hex(12)}@legacy.invalid",
            email_verified=True,
            display_name=payload.display_name.strip(),
            password_hash=hash_password(payload.password),
            role=payload.role,
            is_active=True,
            # The creating admin knows this initial secret, so the account must
            # rotate it before it can use any protected endpoint. The middleware
            # gate enforces the rotation on first login.
            must_change_password=True,
            created_at=utcnow(),
        )
    )
    new_id = result.inserted_primary_key[0]
    db.execute(
        insert(memberships).values(
            user_id=new_id,
            company_id=request.state.company_id,
            is_default=True,
            created_at=utcnow(),
        )
    )
    log_request_activity(
        db,
        request,
        int(request.state.company_id),
        "user.create",
        "user",
        int(new_id),
        f"{payload.username.strip()} kullanıcısını ekledi — rol: {payload.role}",
        {"username": payload.username.strip(), "role": payload.role,
         "display_name": payload.display_name.strip()},
    )
    db.commit()
    return {"id": new_id}


@router.patch("/users/{user_id}/status")
def change_user_status(
    user_id: int, is_active: bool, request: Request, db: Session = Depends(get_db)
):
    cid = int(request.state.company_id)
    target = db.execute(
        select(memberships.c.id, users.c.role, users.c.is_active)
        .join(users, users.c.id == memberships.c.user_id)
        .where(
            memberships.c.user_id == user_id,
            memberships.c.company_id == cid,
        )
    ).mappings().first()
    if not target:
        raise HTTPException(status_code=404, detail="Kullanıcı bu firmada bulunamadı")
    if int(request.state.user["id"]) == user_id and not is_active:
        raise HTTPException(status_code=400, detail="Kendi hesabınızı pasife alamazsınız")
    actor_role = str(request.state.user["role"])
    target_role = str(target["role"])
    if not can_manage_role(actor_role, target_role):
        raise HTTPException(status_code=403, detail="Bu kullanıcıyı yönetme yetkiniz yok")
    if target_role == "admin" and not is_active:
        active_admin_count = db.execute(
            select(users.c.id)
            .join(memberships, memberships.c.user_id == users.c.id)
            .where(
                memberships.c.company_id == cid,
                users.c.role == "admin",
                users.c.is_active.is_(True),
            )
        ).all()
        if len(active_admin_count) <= 1:
            raise HTTPException(
                status_code=409,
                detail="Firmanın son aktif admin kullanıcısı pasife alınamaz",
            )
    db.execute(update(users).where(users.c.id == user_id).values(is_active=is_active))
    log_request_activity(
        db,
        request,
        cid,
        "user.status_change",
        "user",
        user_id,
        f"#{user_id} kullanıcısını "
        f"{'aktif etti' if is_active else 'pasife aldı'} — rol: {target_role}",
        {"role": target_role,
         "is_active": {"old": bool(target["is_active"]), "new": bool(is_active)}},
    )
    db.commit()
    return {"id": user_id, "is_active": is_active}


@router.get("/audit")
def list_audit(request: Request, limit: int = 250, db: Session = Depends(get_db)):
    limit = min(max(limit, 1), 1000)
    cid = int(request.state.company_id)
    rows = db.execute(
        select(audit_logs)
        .where(audit_logs.c.company_id == cid)
        .order_by(audit_logs.c.id.desc())
        .limit(limit)
    ).mappings().all()
    return [dict(row) for row in rows]

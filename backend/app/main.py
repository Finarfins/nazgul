from __future__ import annotations

import logging
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import insert, text
from sqlalchemy.exc import SQLAlchemyError

from .auth import (
    SAFE_METHODS,
    SELF_SERVICE_API,
    audit_logs,
    csrf_is_valid,
    extract_access_token,
    get_user_by_token,
    has_permission,
    required_permission,
    utcnow,
)
from .config import settings
from .sure_butcesi import KontenjanDolu, sureli_kos
from .db import SessionLocal, engine
from .backup_errors import MaintenanceActiveError
from .bootstrap_data import seed_bootstrap_data
from .disa_aktarim_errors import DisaAktarimError
from .request_limits import RequestBodyLimitMiddleware
from .runtime_migrations import (
    database_bootstrap_lock,
    migration_status as alembic_migration_status,
    run_database_migrations,
)
from .tenancy import resolve_company
from .routers import (
    absorption,
    activity_logs,
    analytics,
    auth,
    companies,
    customers,
    dashboard,
    demo,
    entegrasyon_olaylari,
    finance,
    farm,
    cost_rates,
    herd,
    field,
    harvest_scheduling,
    history,
    imports,
    avans,
    mustahsil,
    invoices,
    late_fees,
    machines,
    machine_hour_readings,
    machine_ownership,
    notifications,
    outputs,
    payment_allocations,
    part_supersessions,
    platform_audit,
    kiraci_disa_aktarim,
    kiraci_imha,
    platform_backups,
    pos,
    products,
    quick_pick,
    reports,
    search,
    seasonal_plan,
    technician_profiles,
    transactions,
    transfer_details,
    supplier_prices,
    supplier_price_import,
    supplier_price_bridge,
    warehouse_counts,
    warehouses,
    work_orders,
    work_order_attachments,
    work_order_labor_lines,
    work_order_parts,
    work_order_billing,
    workflow,
)
from .maintenance import hold_sqlite_runtime_lock, is_maintenance_active, maintenance_status
from .field_stok_zamanlayici import (
    baslat_field_stok_zamanlayici,
    durdur_field_stok_zamanlayici,
)

logger = logging.getLogger("yerel_hesap")
# Give the application logger its own stdout handler so unhandled-exception
# tracebacks always reach container logs, independent of whatever root/uvicorn/
# alembic logging configuration is (or is not) applied later in startup.
if not logger.handlers:
    _stdout_handler = logging.StreamHandler(sys.stdout)
    _stdout_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(_stdout_handler)
logger.setLevel(logging.INFO)
logger.propagate = False
APP_VERSION = "2.9.0"

hold_sqlite_runtime_lock(settings.database_url, settings.sungur_data_dir)

# Alembic is the sole schema owner. The cluster-wide advisory lock prevents
# multiple replicas from racing during upgrade; bootstrap seeding performs DML only.
with database_bootstrap_lock(engine):
    if settings.auto_migrate:
        run_database_migrations(engine, acquire_lock=False)
    else:
        status = alembic_migration_status(engine)
        if not status["up_to_date"]:
            raise RuntimeError(
                "AUTO_MIGRATE=false fakat veritabanı şeması güncel değil: "
                f"current={status['current']!r}, expected={status['expected']!r}"
            )
    seed_bootstrap_data(engine)

maintenance_status(engine)

# A company that already has charge allocations while the V2c write flag is off
# keeps a ledger the write paths can no longer maintain. Derivation stays
# correct either way, so this is a loud warning rather than a startup failure.
if not settings.receivable_charge_allocation_enabled:
    try:
        with engine.connect() as _connection:
            _charge_allocation_companies = [
                int(row[0])
                for row in _connection.execute(
                    text(
                        "SELECT DISTINCT company_id FROM payment_allocations "
                        "WHERE receivable_charge_id IS NOT NULL ORDER BY company_id"
                    )
                )
            ]
    except SQLAlchemyError:
        _charge_allocation_companies = []
    if _charge_allocation_companies:
        logger.warning(
            "RECEIVABLE_CHARGE_ALLOCATION_ENABLED kapalı olmasına rağmen "
            "tahakkuk tahsisi bulunan şirketler var: %s",
            _charge_allocation_companies,
        )


@asynccontextmanager
async def _yasam_dongusu(_app: FastAPI):
    """Start the field stock outbox scheduler with the app and stop it with it.

    ``on_event`` is deprecated in FastAPI and ``pytest.ini`` turns the resulting
    DeprecationWarning into an error for every ``app.*`` module, so the wiring
    lives in a lifespan handler instead.
    """
    # FIELD_STOCK_OUTBOX_ENABLED=false envanteri degistiren tuketiciyi hic
    # baslatmaz. Varsayilan KAPALI: acilis kosullari icin bkz.
    # app/FIELD_STOK_OUTBOX_ACILIS_KOSULLARI.md.
    calisacak = settings.field_stock_outbox_enabled
    if not calisacak:
        logger.warning(
            "FIELD_STOCK_OUTBOX_ENABLED=false; tarla stok outbox tuketicisi "
            "BASLATILMADI, olaylar PENDING birikmeye devam edecek"
        )
    else:
        # Do not catch startup errors: an application that cannot start its only
        # production consumer must fail startup loudly instead of serving traffic.
        baslat_field_stok_zamanlayici(settings.field_stock_outbox_interval_seconds)
    try:
        yield
    finally:
        # Shutdown must run even when the served application raised, otherwise
        # the scheduler thread outlives the process that owns it.
        if calisacak:
            durdur_field_stok_zamanlayici()


# Interactive API docs and the raw OpenAPI schema expose the full route surface,
# so they are disabled in production. Non-production environments keep the
# default /docs, /redoc and /openapi.json for development and QA.
app = FastAPI(
    title=settings.app_name,
    version=APP_VERSION,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=_yasam_dongusu,
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=settings.max_request_body_bytes,
    # Approved import endpoints stream 10 MiB Excel uploads with their own
    # specific limit messages; the higher boundary cap keeps that reachable
    # while every other route stays on the strict global default.
    path_overrides={
        "/api/imports/": settings.max_import_request_body_bytes,
        "/api/supplier-price-lists/import": settings.max_import_request_body_bytes,
        "/api/supplier-prices/imports": settings.max_supplier_price_import_request_body_bytes,
        # Work-order attachment uploads (photo/signature) stream up to the
        # attachment cap and raise their own 413; +1 MiB covers multipart
        # framing. Scoped to the attachment router's own prefix so no other
        # work-order route leaves the strict global limit.
        "/api/work-order-attachments/": settings.max_attachment_upload_bytes + 1024 * 1024,
    },
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_API = {
    "/api/health",
    "/api/live",
    "/api/ready",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/register",
    "/api/auth/verify-email",
    "/api/auth/resend-verification",
    # Şifresini unutan kullanıcının oturumu yoktur; bu iki uç kimlik doğrulama
    # kapısının önünde olmak ZORUNDA. Kötüye kullanım IP başına saatlik limitle
    # (``_consume_ip_limit``) sınırlanır ve /forgot-password adresin kayıtlı
    # olup olmadığını sızdırmaz.
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
}
# Aynı üç uç, İKİ AYRI KAPIDA muaf: zorunlu parola rotasyonu (aşağıda) ve
# yetki kapısı. Liste tek kaynakta (app/auth.py) durur; burada üçüncü bir kopya
# tutmak, bu PR'ın kapattığı sürüklenmenin ta kendisi olurdu.
PASSWORD_CHANGE_ALLOWED_API = SELF_SERVICE_API
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# DENETİM YAZIM ARIZASI MANDALI. Bir kez kalkar ve süreç ömrü boyunca kalkık
# kalır; `clear()` yalnız testler içindir. Kendiliğinden düşmez ÇÜNKÜ sonraki
# başarılı bir yazım, kaybolmuş bir olayı geri getirmez — mandalı başarıya
# bağlamak, kapatmaya çalıştığımız sessiz kaybın ta kendisi olurdu.
_audit_sink_failure = threading.Event()


def audit_sink_healthy() -> bool:
    """Bu süreçte hiç denetim kaydı düşmediyse True."""
    return not _audit_sink_failure.is_set()



@app.middleware("http")
async def security_and_audit(request: Request, call_next):
    started_at = time.perf_counter_ns()
    supplied_request_id = request.headers.get("x-request-id", "")
    request_id = supplied_request_id if REQUEST_ID_RE.fullmatch(supplied_request_id) else uuid4().hex
    request.state.request_id = request_id
    path = request.url.path

    def audited_response(status_code: int, detail: str, *, code: str | None = None) -> JSONResponse:
        payload = {"detail": detail}
        if code:
            payload["code"] = code
        response = JSONResponse(status_code=status_code, content=payload)
        _write_security_audit(request, response, started_at, failure_reason=code or detail)
        _apply_response_headers(request, response, request_id)
        return response

    if (
        engine.dialect.name == "sqlite"
        and path.startswith("/api")
        and request.method not in SAFE_METHODS
        and is_maintenance_active(engine)
    ):
        return audited_response(
            503,
            "Geri yükleme sürüyor; yazma işlemleri geçici olarak durduruldu",
            code="RESTORE_MAINTENANCE",
        )

    if path.startswith("/api") and path not in PUBLIC_API and request.method != "OPTIONS":
        # KİRACI SEÇİCİSİNİN SÖZDİZİMİ KİMLİKTEN ÖNCE DOĞRULANIR. Okunamayan
        # bir seçici hakkında YETKİ KARARI VERİLEMEZ: hangi firmanın sorulduğu
        # bilinmiyorsa, "bu firmaya erişimin var mı" sorusunun konusu da yok.
        #
        # Sıra ayrıca denetim kaydını TUTARLI kılar: bozuk seçici artık kimlik
        # İLİŞTİRİLMEDEN reddedilir, yani satır (kimlik yok + firma yok) olarak
        # düşer ve CHECK kısıtının izin verdiği biçimdedir. Sıra tersken satır
        # (kimlik VAR + firma yok) oluyordu; bu, kısıtın tam da reddetmesi
        # gereken biçim ve yazımı düşürüp mandalı kaldırırdı.
        requested_raw = request.headers.get("x-company-id")
        requested_company: int | None = None
        if requested_raw is not None and requested_raw.strip() != "":
            candidate = requested_raw.strip()
            # A present-but-malformed tenant selector must fail closed. Silently
            # falling back to the user's default company could route writes to the
            # wrong tenant when a client sends a corrupted or tampered header.
            if not candidate.isdigit() or int(candidate) <= 0:
                # BİÇİMİ BOZUK SEÇİCİ İÇİN FİRMA YAZILMAZ. Okunamayan bir
                # seçicinin hangi firmayı kastettiği BİLİNMİYOR; buraya bir
                # sayı yazmak tahmin olurdu. Olay firmasız kalır ve platform
                # okuma yolundan görünür.
                return audited_response(
                    403, "Bu firmaya erişim yetkiniz yok", code="COMPANY_ACCESS_DENIED"
                )
            requested_company = int(candidate)
            # İSTENEN firma, çözüm başarısız olsa BİLE denetime girer: sınırı
            # yoklanan firmanın izinde durması gereken bir olaydır.
            request.state.requested_company_id = requested_company
        token, auth_source = extract_access_token(request)
        with SessionLocal() as db:
            user = get_user_by_token(db, token) if token else None
        if not user:
            return audited_response(401, "Oturum açmanız gerekiyor", code="AUTH_REQUIRED")
        request.state.auth_source = auth_source
        request.state.user = user
        if auth_source == "cookie" and request.method not in SAFE_METHODS and not csrf_is_valid(request):
            return audited_response(403, "CSRF doğrulaması başarısız", code="CSRF_FAILED")
        if user.get("must_change_password") and path not in PASSWORD_CHANGE_ALLOWED_API:
            return audited_response(
                403,
                "Devam etmek için önce şifrenizi değiştirin",
                code="PASSWORD_CHANGE_REQUIRED",
            )
        # KİMLİK/OTURUM SELF-SERVİSİ YETKİ KAPISINA GİRMEZ. Kimlik doğrulaması
        # kendi kimliği ve oturumu üzerindeki işlemler için YETERLİDİR; yetki
        # iş verisini korur. Bu üç uç ``read`` ile ifade ediliyordu ve bu yalnız
        # tablodaki her rolün ``read`` taşıması sayesinde çalışıyordu. Rolü
        # çözülemeyen bir hesap için ``read`` artık yok; muafiyet olmasaydı o
        # hesap ne çıkış yapabilir ne parolasını değiştirebilirdi — arayüz yerel
        # durumu temizler ama sunucudaki oturumu ve HttpOnly çerezleri
        # düşüremez, yani kurtarma yolu kalmazdı.
        #
        # Muafiyet ROLE DEĞİL UCUN DOĞASINA ait: bilinmeyen role izin vermek,
        # bu PR'ın kapattığı fail-open'ı geri açardı. Üyelik TAM EŞLEŞME —
        # önek olsaydı bu yolların altına eklenen her uç sessizce muaf olurdu.
        if path not in SELF_SERVICE_API:
            permission = required_permission(request.method, path)
            if not has_permission(user["role"], permission):
                return audited_response(
                    403, "Bu işlem için yetkiniz yok", code="PERMISSION_DENIED"
                )
        try:
            with SessionLocal() as db:
                request.state.company_id = resolve_company(
                    db,
                    int(user["id"]),
                    requested_company,
                )
        except HTTPException as exc:
            return audited_response(exc.status_code, str(exc.detail), code="COMPANY_ACCESS_DENIED")

    try:
        response = await call_next(request)
    except MaintenanceActiveError as exc:
        return audited_response(503, str(exc), code="RESTORE_MAINTENANCE")
    except Exception:
        if path.startswith("/api"):
            synthetic = JSONResponse(status_code=500, content={"detail": "Sunucu hatası"})
            _write_security_audit(request, synthetic, started_at, failure_reason="UNHANDLED_EXCEPTION")
        logger.exception("İstek işlenirken beklenmeyen hata", extra={"request_id": request_id, "path": path})
        raise

    _write_security_audit(request, response, started_at)
    _apply_response_headers(request, response, request_id)
    return response


def _write_security_audit(
    request: Request,
    response,
    started_at: int,
    *,
    failure_reason: str | None = None,
) -> None:
    path = request.url.path
    if not (
        path.startswith("/api")
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
    ):
        return
    user = getattr(request.state, "user", None)
    status_code = int(response.status_code)
    outcome = "success" if status_code < 400 else ("denied" if status_code < 500 else "error")
    # HANGİ FİRMA YAZILIR: İSTENEN firma, asla varsayılan. `company_id` çözüm
    # başarılı olduğunda `request.state.company_id`'de durur; reddedilen
    # isteklerde çözüm hiç tamamlanmaz ama İSTENEN firma bilinir ve
    # `request.state.requested_company_id`'de taşınır.
    #
    # Reddedilen bir istek UYDURMA DEĞİLDİR: COMPANY_ACCESS_DENIED olayı,
    # sınırı yoklanan firmaya ait bir olaydır ve o firmanın denetim izine
    # girer. Uydurma olan, HİÇBİR firma belirtmeyen isteğe varsayılan bir
    # firma seçmektir; o yasak sürüyor — aşağıda ikinci terim yoksa None kalır.
    # KAYIT ``try`` İÇİNDE KURULUR. Alanların toplanması da (başlık çözümü,
    # istemci adresi, zaman damgası) hata verebilir; dışarıda kurulsaydı o hata
    # isteğe sızar ve denetimin arızası sıradan kullanıcının isteğini düşürürdü.
    # Bu fonksiyonun sözleşmesi tam tersi.
    record: dict | None = None
    try:
        company_id = getattr(request.state, "company_id", None)
        if company_id is None:
            company_id = getattr(request.state, "requested_company_id", None)
        record = {
            "user_id": user.get("id") if user else None,
            "username": user.get("username") if user else None,
            "action": request.method,
            "path": path,
            "status_code": status_code,
            "ip_address": request.client.host if request.client else None,
            "created_at": utcnow(),
            "company_id": company_id,
            "request_id": getattr(request.state, "request_id", None),
            "outcome": outcome,
            "duration_ms": max(0, (time.perf_counter_ns() - started_at) // 1_000_000),
            "auth_source": getattr(request.state, "auth_source", None),
            "user_agent": (request.headers.get("user-agent") or "")[:500] or None,
            "failure_reason": (failure_reason or "")[:300] or None,
        }
        with SessionLocal.begin() as db:
            # ``company_id`` AÇIKÇA verilir, ``**record`` içinde saklı değil.
            # İki sebep, ikisi de kasıtlı:
            #   1) Kiracı sütunu yazım yerinde GÖRÜNÜR olur; kiracı kapsam kapısı
            #      ``.values(**...)`` içindeki sütunları çalışma zamanında
            #      çözemiyor ve haklı olarak "çözülemiyor" diyor.
            #   2) Python yinelenen anahtar sözcüğü reddettiği için açık anahtar
            #      yayılım tarafından EZİLEMEZ — yani bu yalnız kapıyı memnun
            #      eden bir biçim değil, gerçekten daha güçlü bir garanti.
            db.execute(
                insert(audit_logs).values(
                    company_id=company_id,
                    **{k: v for k, v in record.items() if k != "company_id"},
                )
            )
    except Exception:
        # DENETİM KAYDI YUTULMAZ. Ölçüldü: bu blok eskiden yalnız log yazıyordu
        # ve satır sessizce kayboluyordu — kaybolan satırlar tam olarak bir
        # saldırganın ürettiği olaylardı (başarısız giriş, AUTH_REQUIRED 401).
        # Yalnızca loglamak YETERSİZ olduğu ÖLÇÜLDÜ: log zaten vardı, olay yine
        # görünmezdi. Bu yüzden iki şey birden yapılır:
        #
        # 1) OLAY KAYBOLMAZ, SİLOSU DEĞİŞİR. Kaydın tamamı yedek çıkışa
        #    (uygulama logu) CRITICAL seviyesinde yazılır. Satır tabloya
        #    giremediyse bile olayın içeriği okunabilir kalır.
        # 2) ARIZA MAKİNE TARAFINDAN GÖRÜLÜR. Süreç düzeyinde bir mandal
        #    kalkar ve /api/ready 503'e döner. Gerekçe: `_readiness_probe`
        #    zaten "migration güncel değil" ve "bakım aktif" durumlarında
        #    örneği trafikten çekiyor; "güvenlik denetim kaydını yazamıyorum"
        #    aynı sınıftan bir yetersizliktir.
        #
        # İSTEK YOLU DÜŞÜRÜLMEZ: istisna yeniden fırlatılmaz, sıradan
        # kullanıcının yanıtı (401/201/…) değişmeden döner. Denetimin arızası
        # kullanıcının işini bozmaz; ama sessiz de kalmaz.
        _audit_sink_failure.set()
        logger.critical(
            "DENETİM KAYDI YAZILAMADI — olay yedek çıkışa düşürüldü",
            exc_info=True,
            extra={"audit_fallback_record": record},
        )


def _apply_response_headers(request: Request, response, request_id: str) -> None:
    path = request.url.path
    if path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    else:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # challenges.cloudflare.com: Turnstile widget'ı (Register sayfası) api.js'i script
    # olarak yükler, kendi challenge iframe'ini açar ve doğrulama çağrılarını aynı
    # host'a yapar; bu yüzden script-src/frame-src/connect-src üçünde de gerekli.
    # frame-src daha önce yoktu, default-src 'self' ile aynı davranışı korumak için
    # 'self' birlikte yazıldı.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self' https://challenges.cloudflare.com; "
        "frame-src 'self' https://challenges.cloudflare.com; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
    )


app.include_router(auth.router, prefix="/api")
app.include_router(platform_audit.router, prefix="/api")
app.include_router(companies.router, prefix="/api")
app.include_router(warehouse_counts.router, prefix="/api")
app.include_router(warehouses.router, prefix="/api")
app.include_router(transfer_details.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(demo.router, prefix="/api")
app.include_router(customers.router, prefix="/api")
# Registered ahead of transactions so the specific /machines/{id} routes win over
# the generic /{kind}/{transaction_id} order/purchase detail route.
app.include_router(machines.router, prefix="/api")
app.include_router(machine_hour_readings.router, prefix="/api")
app.include_router(machine_ownership.router, prefix="/api")
app.include_router(technician_profiles.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(finance.router, prefix="/api")
# Tarla Yönetimi V1 (mobil-erp#2). Öneksiz: uçlar /api/farms,
# /api/farm-parcels, /api/field-activities … olarak issue'da sabitlendi.
app.include_router(farm.router, prefix="/api")
# Müstahsil makbuzu (D1). Öneksiz include: uçlar /api/producer-receipts
# olarak router'ın kendi yollarında sabit.
app.include_router(mustahsil.router, prefix="/api")
# Avans / makbuz ödemesi / borsa tescili / vergi defteri (D2). Aynı öneksiz
# kalıp: uçlar /api/suppliers/{id}/advances, /api/producer-receipts/{id}/pay,
# /api/producer-receipts/{id}/exchange-registration ve /api/tax-liabilities.
app.include_router(avans.router, prefix="/api")
# Outbox okuma yuzeyi (FIELD_STOK_OUTBOX acilis kosulu 2). AYRI router:
# alan bir PARAMETREDIR (bkz. `OlayYuzeyi`), yani ikinci outbox tablosu
# (`herd_integration_events`) icin ayni modulde bir betimleyici eklemek
# yeterli olacak; farm.py'ye gomulseydi surunun yuzeyi ORAYA yazilirdi.
app.include_router(entegrasyon_olaylari.router, prefix="/api")
# Hayvancılık V1 (mobil-erp#17). Öneksiz: uçlar /api/animals, /api/animal-*,
# /api/milk-yields, /api/herd-dashboard olarak issue'da sabitlendi.
app.include_router(herd.router, prefix="/api")
# Gerçek Maliyet V1 (mobil-erp#24). Saatlik maliyet oranları hem tarlayı hem
# hayvancılığı besliyor; bu yüzden ikisinden de AYRI bir router.
app.include_router(cost_rates.router, prefix="/api")
app.include_router(field.router, prefix="/api")
app.include_router(late_fees.router, prefix="/api")
app.include_router(harvest_scheduling.router, prefix="/api")
app.include_router(payment_allocations.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(activity_logs.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(absorption.router, prefix="/api")
app.include_router(seasonal_plan.router, prefix="/api")
app.include_router(outputs.router, prefix="/api")
app.include_router(pos.router, prefix="/api")
app.include_router(quick_pick.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(invoices.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(workflow.router, prefix="/api")
app.include_router(work_orders.router, prefix="/api")
app.include_router(work_order_parts.router, prefix="/api")
app.include_router(work_order_labor_lines.router, prefix="/api")
app.include_router(work_order_attachments.router, prefix="/api")
app.include_router(work_order_billing.router, prefix="/api")
app.include_router(supplier_prices.router, prefix="/api")
app.include_router(supplier_price_import.router, prefix="/api")
app.include_router(supplier_price_bridge.router, prefix="/api")
app.include_router(part_supersessions.router, prefix="/api")
app.include_router(platform_backups.router, prefix="/api")
app.include_router(kiraci_disa_aktarim.router, prefix="/api")
app.include_router(kiraci_imha.router, prefix="/api")


@app.exception_handler(kiraci_imha.FirmaZatenKapaliError)
async def _firma_zaten_kapali(
    _request: Request, exc: kiraci_imha.FirmaZatenKapaliError
) -> JSONResponse:
    """İkinci imha denemesini KARARLI kodlu 409'a çevirir.

    Dışa aktarım hatalarıyla AYNI biçim: kod gövdededir ve sözleşmedir.
    Bu yolun bugün HTTP'den erişilemez olduğu — ve neden yine de yazılı
    olduğu — ``routers/kiraci_imha.py``daki sınıfın kendisinde açıklanmıştır.
    """
    return JSONResponse(
        status_code=409, content={"detail": str(exc), "code": exc.kod}
    )


@app.exception_handler(DisaAktarimError)
async def _disa_aktarim_hatasi(_request: Request, exc: DisaAktarimError) -> JSONResponse:
    """Adı konmuş dışa aktarım hatasını KARARLI kodlu 500'e çevirir.

    Kod gövdededir ve sözleşmedir; istemci hata METNİNİ ayrıştırmak zorunda
    değildir. Akış BAŞLADIKTAN sonra doğan hata buraya ulaşamaz (durum kodu
    o noktada 200'de kilitlidir) — sınırın gerekçesi
    ``routers/kiraci_disa_aktarim.py`` başlığında yazılı.
    """
    return JSONResponse(
        status_code=500, content={"detail": str(exc), "code": exc.kod}
    )
app.include_router(transactions.router, prefix="/api")


@app.get("/api/live")
def live() -> dict[str, str]:
    """Process liveness probe; it deliberately does not depend on the database."""
    return {"status": "alive"}


# Eşzamanlı prob kontenjanı. Sınıra ulaşıldığında istek BEKLETİLMEZ, hemen
# 503 alır: doymuş durum da "sağlıksız"dır ve sözleşme "prob her zaman yanıt
# verir" olarak kalır.
_PROB_KONTENJANI = threading.BoundedSemaphore(int(settings.readiness_max_inflight))


def _readiness_probe() -> None:
    """Hazırlık kontrolünün veritabanına dokunan kısmı; süre bütçesi çağırana ait."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1")).scalar_one()
    migration = alembic_migration_status(engine)
    if not migration["up_to_date"]:
        raise RuntimeError("Migration sürümü güncel değil")
    status = maintenance_status(engine)
    if status["active"]:
        raise RuntimeError("Platform bakım veya recovery_required durumunda")
    # Güvenlik denetim kaydını yazamayan bir örnek trafiğe UYGUN DEĞİLDİR.
    # Yukarıdaki iki koşulla aynı sınıf: örnek çalışıyor olabilir ama
    # sözleşmesini yerine getiremiyor.
    if not audit_sink_healthy():
        raise RuntimeError("Güvenlik denetim kaydı yazılamıyor")


# HAZIRLIK PROBU HER ZAMAN YANIT VERİR. Veritabanı ulaşılamaz olduğunda
# "sağlıksız" demek, hiç konuşmamaktan iyidir: yanıtsız kalan bir prob yük
# dengeleyiciye hiçbir şey söylemez ve isteği zaman aşımına bırakır.
#
# Süre bütçesi neden burada, sürücüde değil: `connect_timeout` yalnız TCP
# bağlanmayı sınırlar. Soket AÇIK ama sunucu yanıt vermiyorsa (donmuş sunucu;
# çekirdek ağ yığını TCP'yi hâlâ ACK'liyor) hiçbir sürücü ayarı devreye
# girmiyor — TCP keepalive dahil ölçüldü, üçü de 20 saniyeden uzun asılı kaldı.
#
# Bütçe iyileşmeyi MASKELEMEZ: ölü bir havuz bağlantısı `pool_pre_ping` ile
# zaten sessizce yenileniyor ve o yol saniyenin altında bitiyor.
#
# Bu gerekçe YORUMDA, docstring'de DEĞİL: docstring OpenAPI açıklamasına akıyor
# ve üretilen frontend tiplerine iniyor. İç muhakeme genel sözleşmeye ait değil.
@app.get("/api/ready")
def ready() -> dict[str, str]:
    """Readiness probe; traffic is accepted only while the database is reachable and current."""
    sinir = int(settings.readiness_timeout_seconds)
    try:
        sureli_kos(_readiness_probe, sinir, _PROB_KONTENJANI)
    except KontenjanDolu as exc:
        logger.error("Eşzamanlı hazırlık probu sınırına ulaşıldı (%s)",
                     settings.readiness_max_inflight)
        raise HTTPException(
            status_code=503, detail="Uygulama trafiğe hazır değil"
        ) from exc
    except TimeoutError as exc:
        # İş parçacığı İPTAL EDİLMİYOR ve BEKLENMİYOR: sürücünün içinde bloke ve
        # kesilemez. Donmuş sunucu geri döndüğünde ya da `connect_timeout`
        # dolduğunda kendi kendine biter. Önemli olan İSTEĞİN yanıt vermesi.
        logger.error("Readiness kontrolü %s sn içinde tamamlanmadı", sinir)
        raise HTTPException(
            status_code=503, detail="Uygulama trafiğe hazır değil"
        ) from exc
    except Exception as exc:
        logger.error("Readiness kontrolü başarısız", exc_info=exc)
        raise HTTPException(
            status_code=503, detail="Uygulama trafiğe hazır değil"
        ) from exc
    return {"status": "ready", "database": "ok"}


@app.get("/api/health")
def health() -> dict[str, str]:
    """Backward-compatible coarse health probe without deployment fingerprints."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    except Exception as exc:
        logger.error("Health kontrolü başarısız", exc_info=exc)
        raise HTTPException(status_code=503, detail="Uygulama sağlıklı değil") from exc
    return {"status": "ok", "database": "ok"}


frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend.exists():
    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")


@app.get("/{path:path}")
def spa(path: str):
    if frontend.exists():
        frontend_root = frontend.resolve()
        candidate = (frontend_root / path).resolve()
        # Serve a concrete static file only if it remains inside frontend/dist.
        # Otherwise fall back to the SPA shell without exposing arbitrary files.
        if candidate.is_file() and (
            candidate == frontend_root or frontend_root in candidate.parents
        ):
            headers = (
                {"Service-Worker-Allowed": "/saha/", "Cache-Control": "no-cache"}
                if path == "field-pwa/sw-v1.js"
                else None
            )
            return FileResponse(candidate, headers=headers)
        index = frontend_root / "index.html"
        if index.is_file():
            return FileResponse(index)
    # No built frontend (backend-only test/CI environment): return a minimal
    # 200 shell so the SPA route and reverse-proxy health checks still succeed.
    return HTMLResponse(
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        "<title>Harman Zamanı</title></head><body></body></html>"
    )

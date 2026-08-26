from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .client_ip import (
    install_trusted_proxy_client_resolution,
    parse_trusted_proxy_networks,
)

BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_name: str = "Harman Zamanı"
    environment: str = "development"
    database_url: str = f"sqlite:///{BASE_DIR / 'veriler.db'}"
    auto_migrate: bool = True
    demo_mode: bool = False
    sqlite_busy_timeout_ms: int = 5000
    # Ulaşılamayan bir veritabanına yapılan bağlanma denemesinin ÜST SINIRI.
    # Sınırsız bırakıldığında ölçüldü: paket düşüren bir ağda `engine.connect()`
    # 20 saniyeden uzun süre döndü ve /api/ready hiç yanıt vermedi. Yanıt
    # vermeyen bir hazırlık probu, 503 dönen bir probdan kötüdür: yük
    # dengeleyici "sağlıksız" bilgisini hiç alamaz, isteği zaman aşımına bırakır.
    db_connect_timeout_seconds: int = 5
    # Hazırlık probunun TOPLAM süre bütçesi. `connect_timeout` yalnız TCP
    # bağlanmayı sınırlar; soket AÇIK ama sunucu yanıt vermiyorsa (donmuş
    # sunucu, çekirdek ağ yığını hâlâ ACK'liyor) sürücü ayarlarının hiçbiri
    # devreye girmez — ölçüldü, TCP keepalive dahil. Bu yüzden probun kendisi
    # bir süre bütçesiyle koşuyor.
    readiness_timeout_seconds: int = 8
    # Aynı anda koşabilecek hazırlık probu sayısı. Süre bütçesi dolunca iş
    # parçacığı bırakılıyor (kesilemez), yani sınır olmadan sayı istek sayısıyla
    # büyür — donmuş veritabanında ölçüldü: 10 eş zamanlı istek 6 → 35 iş
    # parçacığı. Bugün /api/ready'yi yalnız deploy doğrulaması çağırıyor, ama bu
    # BUGÜNKÜ ÇAĞIRAN hakkında bir savunma; uca bir uptime denetleyicisi ya da
    # yük dengeleyici bağlandığı gün sınırsız yol tükenme vektörüne dönerdi.
    readiness_max_inflight: int = 4
    # Field stock outbox is local DB work. Thirty seconds keeps ordinary field
    # updates near-real-time without continuously polling an idle database.
    field_stock_outbox_interval_seconds: int = 30
    # AC/KAPA anahtari. Tuketici ENVANTERI DEGISTIRIR. Varsayilan `False`:
    # bugun hasattan urune giden yol YOK, yani her hasat olayi terminal
    # `SKIPPED_NO_PRODUCT` kovasina duser, terminal satirlar bir daha
    # SECILMEZ ve uygulamada bu tabloyu okuyan hicbir yuzey yok — acik
    # varsayilan, her hasadi SESSIZCE ve KALICI olarak cope atmak demekti.
    # Acmadan once app/FIELD_STOK_OUTBOX_ACILIS_KOSULLARI.md'deki dort kosul
    # saglanmali. Kapaliyken olaylar PENDING birikir ve anahtar acildiginda
    # islenir — kayip yoktur, erteleme vardir.
    field_stock_outbox_enabled: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    migration_lock_timeout_seconds: int = 120
    max_request_body_bytes: int = 2 * 1024 * 1024
    # Approved Excel import endpoints accept 10 MiB uploads (see
    # routers/imports.MAX_EXCEL_UPLOAD_BYTES); the extra 1 MiB covers multipart
    # framing so the endpoint's own streaming limit stays the effective cap.
    max_import_request_body_bytes: int = 11 * 1024 * 1024
    max_supplier_price_import_request_body_bytes: int = 26 * 1024 * 1024
    trusted_proxy_cidrs: str = ""

    # Persistent data directory for uploaded files (work-order photos and
    # signatures). Production MUST point this OUTSIDE the release directory
    # (e.g. /opt/sungur-data): the tarball-swap deploy replaces the release
    # tree wholesale, so anything written inside it is lost on deploy.
    sungur_data_dir: str = str(BASE_DIR / "data")
    # Full-database operations are intentionally outside tenant RBAC. A caller
    # must be both an admin and explicitly named here (comma-separated user
    # immutable numeric user ids only).
    sungur_platform_operators: str = ""
    backup_retention_count: int = 14
    backup_lock_timeout_seconds: int = 0
    restore_drain_timeout_seconds: int = 60
    # Streaming cap enforced by the attachment endpoint itself (HTTP 413). The
    # transport-level body override adds 1 MiB for multipart framing.
    max_attachment_upload_bytes: int = 10 * 1024 * 1024

    # First-run administrator credentials. Development keeps the historical
    # bootstrap password for local compatibility; production must provide a
    # unique secret through BOOTSTRAP_ADMIN_PASSWORD.
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_display_name: str = "Sistem Yöneticisi"
    bootstrap_admin_password: str | None = None

    # Browser sessions use short-lived HttpOnly access cookies and rotating
    # refresh cookies. COOKIE_SECURE must be true behind production HTTPS.
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    access_cookie_name: str = "yhp_access_token"
    refresh_cookie_name: str = "yhp_refresh_token"
    csrf_cookie_name: str = "yhp_csrf_token"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cookie_domain: str | None = None

    # e-Fatura seam (Increment 3). All optional; provider defaults to NoOp, so the
    # internal invoice flow needs no e-invoice configuration to run unchanged.
    einvoice_provider: str = "noop"
    einvoice_base_url: str | None = None
    einvoice_username: SecretStr | None = None
    einvoice_password: SecretStr | None = None
    # ÖLÜ — gönderici VKN companies.tax_number'dan okunur (invoices.py:106). Kaldırılması ayrı iş.
    einvoice_sender_vkn: str | None = None
    # Nes may require an API key alongside the login credentials ‹doğrulanacak›.
    # Unset by default: the header is only sent when the provider is known to
    # need it (app.einvoice.endpoints.NES_REQUIRES_API_KEY).
    einvoice_api_key: SecretStr | None = None
    # Operator opt-in that the provider's endpoints/operation names/field names
    # have actually been verified against the official document. Default False:
    # setting a base URL alone must not license the ‹doğrulanacak› guesses in
    # app/einvoice/endpoints.py against a live provider.
    einvoice_endpoints_verified: bool = False
    # İzibiz ortam kilidi: "test" | "live". Ayarlanmamışsa "test" — en güvenli
    # taraf. Boş dize ya da başka bir değer HATA verir, sessizce "test"e
    # düşmez: "IZIBIZ_ENV=prod" yazan bir operatör canlıya gittiğini sanırken
    # sandbox'a gitmemeli (ya da tersi). Kontrolün kendisi
    # app/einvoice/endpoints.py: izibiz_endpoint_violation().
    izibiz_env: str = "test"

    # Notification delivery seam. The safe default is inert and never performs
    # a network call; Twilio/WhatsApp are wiring-only stubs for a later adapter.
    notification_provider: str = "noop"
    public_app_url: str = "http://localhost:5173"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str | None = None
    smtp_use_tls: bool = True
    turnstile_site_key: str | None = None
    turnstile_secret_key: str | None = None
    # §2.9: durdurulan bildirimlerin saklama süresi. Süre dolduğunda satır
    # SİLİNMEZ, notifications_archive'a taşınır. Alt sınır (30 gün) hem burada
    # hem app/notifications/archive.py içinde uygulanır: tek bir katmanın
    # atlanması denetim izini kapatamasın.
    notification_disarmed_retention_days: int = 90
    # Persistent AR allocation cutover. Disabled until the dry-run
    # reconciliation/backfill report is explicitly approved.
    payment_allocation_engine_enabled: bool = False
    payment_allocation_closed_through: date | None = None
    # Harman V2c: allow payments to be allocated against posted late-fee charge
    # documents. Write paths only; derivation is always ledger-aware. Disabled
    # until the charge-allocation backfill/cutover checkpoint is approved.
    receivable_charge_allocation_enabled: bool = False

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    @field_validator("notification_disarmed_retention_days")
    @classmethod
    def validate_notification_retention(cls, value: int) -> int:
        # §2.9 alt sınırı yapılandırma katmanında da uygulanır. Servis
        # katmanındaki kontrol (app/notifications/archive.py) bunun kopyası
        # değil, ikinci bağımsız kapısıdır: biri atlanırsa diğeri tutar.
        if value < 30:
            raise ValueError(
                "NOTIFICATION_DISARMED_RETENTION_DAYS 30 günden kısa olamaz"
            )
        return value

    @field_validator("smtp_port")
    @classmethod
    def validate_smtp_port(cls, value: int) -> int:
        if value < 1 or value > 65535:
            raise ValueError("SMTP_PORT 1 ile 65535 arasında olmalıdır")
        return value

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"development", "test", "demo", "production"}:
            raise ValueError("ENVIRONMENT development, test, demo veya production olmalıdır")
        return normalized

    @field_validator("bootstrap_admin_username")
    @classmethod
    def validate_bootstrap_admin_username(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("BOOTSTRAP_ADMIN_USERNAME boş olamaz")
        if len(normalized) > 80:
            raise ValueError("BOOTSTRAP_ADMIN_USERNAME en fazla 80 karakter olabilir")
        return normalized

    @field_validator("bootstrap_admin_display_name")
    @classmethod
    def validate_bootstrap_admin_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("BOOTSTRAP_ADMIN_DISPLAY_NAME boş olamaz")
        if len(normalized) > 160:
            raise ValueError("BOOTSTRAP_ADMIN_DISPLAY_NAME en fazla 160 karakter olabilir")
        return normalized

    @field_validator("access_token_minutes")
    @classmethod
    def validate_access_minutes(cls, value: int) -> int:
        if value < 1 or value > 1440:
            raise ValueError("ACCESS_TOKEN_MINUTES 1 ile 1440 arasında olmalıdır")
        return value

    @field_validator("field_stock_outbox_interval_seconds")
    @classmethod
    def validate_field_stock_outbox_interval(cls, value: int) -> int:
        if value < 1 or value > 3600:
            raise ValueError(
                "FIELD_STOCK_OUTBOX_INTERVAL_SECONDS 1 ile 3600 arasinda olmalidir"
            )
        return value

    @field_validator("refresh_token_days")
    @classmethod
    def validate_refresh_days(cls, value: int) -> int:
        if value < 1 or value > 90:
            raise ValueError("REFRESH_TOKEN_DAYS 1 ile 90 arasında olmalıdır")
        return value

    @field_validator("max_request_body_bytes")
    @classmethod
    def validate_max_request_body_bytes(cls, value: int) -> int:
        if value < 1024 or value > 100 * 1024 * 1024:
            raise ValueError(
                "MAX_REQUEST_BODY_BYTES 1024 ile 104857600 arasında olmalıdır"
            )
        return value

    @field_validator("max_import_request_body_bytes")
    @classmethod
    def validate_max_import_request_body_bytes(cls, value: int) -> int:
        if value < 1024 or value > 100 * 1024 * 1024:
            raise ValueError(
                "MAX_IMPORT_REQUEST_BODY_BYTES 1024 ile 104857600 arasında olmalıdır"
            )
        return value

    @field_validator("max_attachment_upload_bytes")
    @classmethod
    def validate_max_attachment_upload_bytes(cls, value: int) -> int:
        if value < 1024 or value > 100 * 1024 * 1024:
            raise ValueError(
                "MAX_ATTACHMENT_UPLOAD_BYTES 1024 ile 104857600 arasında olmalıdır"
            )
        return value

    @field_validator("backup_retention_count")
    @classmethod
    def validate_backup_retention_count(cls, value: int) -> int:
        if value < 1 or value > 365:
            raise ValueError("BACKUP_RETENTION_COUNT 1 ile 365 arasında olmalıdır")
        return value

    @field_validator("backup_lock_timeout_seconds", "restore_drain_timeout_seconds")
    @classmethod
    def validate_backup_timeouts(cls, value: int) -> int:
        if value < 0 or value > 600:
            raise ValueError("Yedek/restore timeout değeri 0 ile 600 saniye arasında olmalıdır")
        return value

    @field_validator("sungur_data_dir")
    @classmethod
    def validate_sungur_data_dir(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("SUNGUR_DATA_DIR boş olamaz")
        return normalized

    @field_validator("cookie_samesite")
    @classmethod
    def validate_samesite(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("COOKIE_SAMESITE lax, strict veya none olmalıdır")
        return normalized

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        """Reject wildcard CORS at construction, not only when the list is read."""
        origins = [item.strip() for item in value.split(",") if item.strip()]
        if "*" in origins:
            raise ValueError(
                "CORS_ORIGINS wildcard kullanamaz; izinli originleri açıkça yazın"
            )
        return value

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_trusted_proxy_cidrs(cls, value: str) -> str:
        try:
            parse_trusted_proxy_networks(value)
        except ValueError as exc:
            raise ValueError("TRUSTED_PROXY_CIDRS geçerli IP/CIDR değerleri içermelidir") from exc
        return value

    @model_validator(mode="after")
    def validate_allocation_flag_matrix(self) -> "Settings":
        # Charge-document allocation is a layer ON TOP of the allocation
        # engine: with the engine off no write path can maintain the ledger the
        # charge flag promises, so the combination is refused at startup
        # instead of running in a meaningless half-state.
        if (
            self.receivable_charge_allocation_enabled
            and not self.payment_allocation_engine_enabled
        ):
            raise ValueError(
                "RECEIVABLE_CHARGE_ALLOCATION_ENABLED=true için "
                "PAYMENT_ALLOCATION_ENGINE_ENABLED=true zorunludur"
            )
        return self

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none yalnızca COOKIE_SECURE=true ile kullanılabilir")

        # Ortamdan bağımsız: TLS kapalıyken SMTP kimlik bilgisi düz metin
        # gider. Bunu yalnız production'da yasaklamak, aynı parolanın test
        # ortamında ağa açık dolaşmasına izin vermek olurdu.
        if (self.smtp_username or "").strip() and not self.smtp_use_tls:
            raise ValueError(
                "SMTP_USERNAME tanımlıyken SMTP_USE_TLS=false kullanılamaz"
            )

        if self.environment == "production":
            password = (self.bootstrap_admin_password or "").strip()
            if not self.cookie_secure:
                raise ValueError("Production ortamında COOKIE_SECURE=true zorunludur")
            if not password:
                raise ValueError(
                    "Production ilk kurulumu için BOOTSTRAP_ADMIN_PASSWORD zorunludur"
                )
            if password == "admin123" or len(password) < 12:
                raise ValueError(
                    "BOOTSTRAP_ADMIN_PASSWORD en az 12 karakter olmalı ve varsayılan parola olmamalıdır"
                )
            if not self.trusted_proxy_cidrs.strip():
                raise ValueError(
                    "Production ortamında TRUSTED_PROXY_CIDRS zorunludur"
                )
            if not (self.turnstile_secret_key or "").strip():
                raise ValueError(
                    "Production ortamında TURNSTILE_SECRET_KEY zorunludur"
                )
            # F0-email/AK-4: SMTP'siz bir production, kayıt akışı görünürde
            # çalışırken hiç kimsenin hesabını doğrulayamadığı sessiz bir
            # arızadır. Kuyruk satırı yazılır ama teslim edilmez; bu yüzden
            # eksiklik başlangıçta, açıkça reddedilir.
            if not (self.smtp_host or "").strip() or not (
                self.smtp_from_email or ""
            ).strip():
                raise ValueError(
                    "Production ortamında SMTP_HOST ve SMTP_FROM_EMAIL zorunludur"
                )
            # SMTP ayarlarını doldurup sağlayıcıyı "noop" bırakmak, aynı sessiz
            # arızanın ikinci kapısıdır: satır kuyruğa girer, "sağlayıcı
            # yapılandırılmamış" damgasını yer ve kimse mail almaz.
            if self.notification_provider.strip().lower() != "smtp":
                raise ValueError(
                    "Production ortamında NOTIFICATION_PROVIDER=smtp zorunludur"
                )
            # Doğrulama bağlantısı doğrudan bu adresten kurulur. Varsayılan
            # localhost değeriyle production'a çıkmak, teslim EDİLEN ama
            # tıklanamayan bir mail üretir — en sinsi arıza sınıfı.
            public_url = (self.public_app_url or "").strip()
            if not public_url.lower().startswith("https://"):
                raise ValueError(
                    "Production ortamında PUBLIC_APP_URL https:// ile başlamalıdır"
                )
            host = public_url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
            if host.lower() in {"localhost", "127.0.0.1", "::1", ""}:
                raise ValueError(
                    "Production ortamında PUBLIC_APP_URL localhost olamaz"
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def effective_bootstrap_admin_password(self) -> str:
        password = (self.bootstrap_admin_password or "").strip()
        if password:
            return password
        # Backward-compatible local/demo bootstrap only. Production validation
        # above prevents this fallback from ever being used in production.
        return "admin123"

    @property
    def cors_origin_list(self) -> list[str]:
        """Return normalized CORS origins without silently allowing wildcards."""
        values = [item.strip() for item in self.cors_origins.split(",") if item.strip()]
        if "*" in values:
            raise ValueError(
                "CORS_ORIGINS wildcard kullanamaz; izinli originleri açıkça yazın"
            )
        return values


settings = Settings()
install_trusted_proxy_client_resolution(settings.trusted_proxy_cidrs)

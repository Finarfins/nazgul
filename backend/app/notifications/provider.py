"""Provider contract for outbound notifications.

The default provider is deliberately inert: it performs no network call and
returns ``NONE``. Twilio and WhatsApp prove the factory seam but remain wiring
stubs until company-level credentials and a real adapter are implemented.

``SmtpEmailNotificationProvider`` is the first real adapter (F0-email). SMTP
taşıyıcısı **yalnız bu modüldedir**: ``smtplib`` importu başka hiçbir uygulama
modülünde bulunmaz, böylece "kim, nereden mail gönderiyor" sorusunun tek bir
cevabı olur.
"""

from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Literal

from pydantic import BaseModel


NotificationStatus = Literal[
    "NONE", "PENDING", "SENT", "DELIVERED", "SIMULATED", "FAILED"
]
_NOT_CONFIGURED = "Bildirim sağlayıcısı şirket entegrasyonu sonrası yapılandırılacak"
_SMTP_NOT_CONFIGURED = "SMTP yapılandırılmamış"
# Lease 5 dakikadır (service._LEASE_MINUTES); taşıyıcı hiçbir koşulda lease'i
# aşacak kadar beklememelidir, aksi hâlde satır başka bir sürece kapılabilir.
SMTP_TIMEOUT_SECONDS = 15
logger = logging.getLogger("yerel_hesap.notifications.smtp")


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    get_secret_value = getattr(value, "get_secret_value", None)
    return get_secret_value() if callable(get_secret_value) else str(value)


def _redact_smtp_error(error: Exception, config: Any) -> str:
    message = f"{type(error).__name__}: {error}"
    credentials = (
        _secret_value(getattr(config, "smtp_password", None)),
        str(getattr(config, "smtp_username", None) or ""),
    )
    for credential in credentials:
        if credential:
            message = message.replace(credential, "***")
    return message


class NotificationResult(BaseModel):
    status: NotificationStatus
    message: str | None = None
    external_id: str | None = None


class NotificationProvider(ABC):
    supports_idempotency: bool = False

    def __init__(self, settings: Any | None = None) -> None:
        # Fabrika ayarları geçirir; testler argümansız kurabilir.
        self.settings = settings

    @abstractmethod
    def send(self, notification: dict[str, Any]) -> NotificationResult:
        """Send one normalized notification or return an inert/error result."""
        ...


class NoOpNotificationProvider(NotificationProvider):
    """Safe default: records the outbox row without contacting any network."""

    supports_idempotency = True

    def send(self, notification: dict[str, Any]) -> NotificationResult:
        return NotificationResult(
            status="NONE",
            message="Bildirim sağlayıcısı yapılandırılmamış",
        )


class SimulationNotificationProvider(NotificationProvider):
    """Ağa çıkmadan gönderim akışını uçtan uca çalıştıran test sağlayıcısı.

    ``SENT`` **yazmaz**: gerçekten gönderilmemiş bir mesajı "gönderildi" diye
    raporlamak denetim izini yalan söyler hâle getirir. Ayrı bir ``SIMULATED``
    durumu döner; bu durum terminaldir ve raporlarda gerçek gönderimlerden
    ayrı sayılır.

    Yalnız ``notification_provider = "simulation"`` ile açıkça seçildiğinde
    devreye girer; varsayılan hiçbir koşulda bu değildir.
    """

    supports_idempotency = True

    def send(self, notification: dict[str, Any]) -> NotificationResult:
        key = notification.get("provider_idempotency_key") or "simulation"
        return NotificationResult(
            status="SIMULATED",
            message="Simülasyon: mesaj dışarı gönderilmedi",
            external_id=f"sim-{key}",
        )


class TwilioNotificationProvider(NotificationProvider):
    """Wiring-only stub for a later credentialled Twilio adapter."""

    supports_idempotency = False

    def send(self, notification: dict[str, Any]) -> NotificationResult:
        raise NotImplementedError(_NOT_CONFIGURED)


class WhatsAppNotificationProvider(NotificationProvider):
    """Wiring-only stub for a later WhatsApp adapter."""

    supports_idempotency = False

    def send(self, notification: dict[str, Any]) -> NotificationResult:
        raise NotImplementedError(_NOT_CONFIGURED)


class SmtpEmailNotificationProvider(NotificationProvider):
    """SMTP taşıyıcısı — F0-email'in tek gerçek gönderim noktası.

    Üç kural bu sınıfın şeklini belirler:

    * **Idempotency yoktur.** SMTP'de sağlayıcı tarafında tekilleştirme
      anahtarı yoktur; ``supports_idempotency = True`` demek süresi dolmuş
      lease'in otomatik geri alınmasını açar ve aynı maili iki kez gönderir.
    * **Sunucu metni dışarı sızmaz.** SMTP yanıtları alıcı adresi ve iç
      hostname taşıyabilir; istisna yukarı taşınır ve motor tarafından
      sınıflandırılıp maskelenir (``service._ERROR_MESSAGES``). Bu sınıf
      ``NotificationResult.message`` alanına hiçbir sunucu metni yazmaz.
    * **İçerik üretmez.** Konu ve gövde payload'dan gelir; eksikse bu bir
      içerik hatasıdır (``ValueError`` → ``VALIDATION``) ve yeniden denenmez.
    """

    supports_idempotency = False

    def send(self, notification: dict[str, Any]) -> NotificationResult:
        config = self.settings
        host = getattr(config, "smtp_host", None)
        sender = getattr(config, "smtp_from_email", None)
        if not host or not sender:
            # Motor bunu ``NONE`` olarak sonlandırır: denenmedi, başarısız da
            # olmadı. Yapılandırma gelince satır elle retry ile canlanır.
            return NotificationResult(status="NONE", message=_SMTP_NOT_CONFIGURED)

        recipient = str(notification.get("recipient") or "").strip()
        if not recipient:
            raise ValueError("Bildirimde alıcı adresi yok")
        payload = notification.get("payload") or {}
        subject = str(payload.get("subject") or "").strip()
        body = str(payload.get("body") or "").strip()
        if not subject or not body:
            raise ValueError("Bildirim payload'ında konu veya gövde yok")

        message_id = self._message_id(notification, sender)
        message = EmailMessage()
        message["Subject"] = subject
        display_name = str(getattr(config, "smtp_from_name", None) or "").strip()
        message["From"] = formataddr((display_name, sender)) if display_name else sender
        message["To"] = recipient
        message["Message-ID"] = message_id
        message.set_content(body)
        self._deliver(message)
        return NotificationResult(status="SENT", external_id=message_id)

    @staticmethod
    def _message_id(notification: dict[str, Any], sender: str) -> str:
        """Kararlı Message-ID: outbox kimliğinden türer, denemeler arası sabit.

        Böylece posta sunucusu logu ile outbox satırı eşleşir ve belirsiz bir
        yeniden denemenin MTA tarafında kopya üretip üretmediği görülebilir.
        """
        key = str(notification.get("provider_idempotency_key") or "").strip()
        local = key.replace(":", "-") or "notification"
        domain = sender.rsplit("@", 1)[-1] or "localhost"
        return f"<{local}@{domain}>"

    def _deliver(self, message: EmailMessage) -> None:
        """Tek taşıyıcı: bağlan → starttls → login → gönder.

        Eskiden bu blok ``email_verification`` içinde iki kez tekrarlanıyordu
        (doğrulama maili ve mevcut-hesap bildirimi); tek noktaya indirildi.
        """
        config = self.settings
        try:
            with smtplib.SMTP(
                config.smtp_host,
                getattr(config, "smtp_port", 587),
                timeout=SMTP_TIMEOUT_SECONDS,
            ) as client:
                if getattr(config, "smtp_use_tls", True):
                    client.starttls()
                username = getattr(config, "smtp_username", None)
                if username:
                    password = _secret_value(getattr(config, "smtp_password", None))
                    client.login(username, password)
                client.send_message(message)
        except Exception as exc:
            logger.error(
                "SMTP gönderimi başarısız (host=%s port=%s): %s",
                config.smtp_host,
                getattr(config, "smtp_port", 587),
                _redact_smtp_error(exc, config),
            )
            raise


_PROVIDERS: dict[str, type[NotificationProvider]] = {
    "simulation": SimulationNotificationProvider,
    "smtp": SmtpEmailNotificationProvider,
    "twilio": TwilioNotificationProvider,
    "whatsapp": WhatsAppNotificationProvider,
}


def get_notification_provider(settings: Any) -> NotificationProvider:
    """Resolve the configured adapter, failing safely to NoOp."""
    name = (
        getattr(settings, "notification_provider", "noop") or "noop"
    ).strip().lower()
    provider_type = _PROVIDERS.get(name)
    if provider_type is None:
        return NoOpNotificationProvider(settings)
    return provider_type(settings)

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
_WHATSAPP_NOT_CONFIGURED = "WhatsApp Cloud API yapılandırılmamış"
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
    """WHATSAPP kanalının outbox adaptörü — taşıyıcı WA3'ün sağlayıcısıdır.

    Artık bir "wiring-only stub" DEĞİL: yapılandırılmış bir kurulumda
    gerçekten ağa çıkar. Ama TAŞIYICIYI KENDİSİ YAZMAZ —
    ``app.whatsapp.saglayici.saglayici_al()`` çağırır.

    TAŞIYICI NEDEN BURADA DEĞİL (ölçülmüş karar, üslup değil): WA4 ilk
    hâlinde `cloud_api.metin_gonder` adında KENDİ urllib göndericisini
    getiriyordu. WA3-full (#85) develop'a inerken `app/whatsapp/saglayici.py`
    ile AYNI işi yapan ikinci bir gönderici getirdi — rebase'de ÖLÇÜLDÜ:
    aynı depoda Meta'ya çıkan İKİ yol vardı, ikisi de aynı uca POST atıyordu
    ve ikisinin hata sınıflandırması FARKLIYDI. İki taşıyıcı, "kim nereden
    WhatsApp mesajı gönderiyor" sorusunun İKİ cevabı demektir ve o soru bu
    depoda tek cevaplı olmak zorunda (``SmtpEmailNotificationProvider``
    için yazılı olan kuralın aynısı). WA3'ünki KALDI çünkü önce indi ve
    daha zengin: hatayı yeniden-denenebilir / kalıcı / kota olarak
    SINIFLANDIRIYOR. WA4'ünki SİLİNDİ.

    ÜÇ YOL VAR ve üçü de AYRI bir şey söyler:

    * **Yapılandırılmamış → ``NONE``.** Ağa HİÇ çıkılmaz;
      ``saglayici.yapilandirildi_mi()`` yanlışsa taşıyıcı ÇAĞRILMAZ bile.
      Varsayılan kurulumda (``.env``de WhatsApp jetonu yok) BU yol koşar,
      yani bu sınıfın varlığı hiçbir kurulumda dışarı mesaj ÇIKARMAZ.
    * **``notification_provider = "simulation"`` → ``SIMULATED``.** Ağa
      çıkmadan akışı uçtan uca koşturur. ``SENT`` YAZMAZ ve bu kural
      ``PushNotificationProvider``dan devralınmıştır: gerçekten
      gönderilmemiş bir mesajı "gönderildi" diye raporlamak denetim izini
      yalan söyler hâle getirir. ``SIMULATED`` terminaldir.
    * **Yapılandırılmış → gerçek gönderim**, ardından ``SENT``.

    ``external_id`` NEDEN ``None``: WA4'ün kendi göndericisi Meta'nın
    döndürdüğü ``wamid``i okuyup ``external_id``ye yazıyordu. WA3'ün
    sağlayıcısı yanıt gövdesini OKUR ama AYRIŞTIRMAZ (yalnız keep-alive
    için tüketir), yani mesaj kimliği bu sınıra ulaşmıyor. İki seçenek
    vardı ve seçilen İKİNCİSİDİR: (a) `saglayici.metin_gonder`i kimlik
    döndürecek biçimde değiştirmek — WA3'ün sözleşmesini WA4 uğruna
    genişletmek olurdu ve o sözleşmenin tek çağıranı bugün işçidir;
    (b) ``external_id``yi BOŞ bırakıp ``SENT``in anlamını DARALTMAK.
    Burada ``SENT``, "Meta POST'u 2xx ile KABUL ETTİ" demektir — bir
    teslimat kanıtı DEĞİL. Dar ama DOĞRU bir iddia; geniş ve yanlış olana
    yeğlendi. Mesaj kimliğine ihtiyaç doğduğunda taşıyıcının sözleşmesi
    genişletilmelidir, bu sınıf tahmin ETMEMELİDİR.

    ``supports_idempotency = False`` ve bu ÖLÇÜLMÜŞ bir karardır: Meta
    Cloud API'nin gönderim ucunda istemci tarafı tekilleştirme anahtarı
    YOKTUR. ``True`` demek, süresi dolmuş bir lease'in otomatik geri
    alınmasını açar ve AYNI mesajı kullanıcıya İKİ KEZ gönderirdi —
    ``SmtpEmailNotificationProvider`` ile aynı gerekçe.

    KAPSAM SINIRI — ASİSTAN CEVAPLARI BURADAN GEÇMEZ. Bu adaptör
    ``notification_outbox`` satırlarını taşır ve o defterin rıza kaydı
    (``notification_consents``) yalnız ``CUSTOMER``/``SUPPLIER`` taraflarını
    tanır. WhatsApp asistanının kullanıcıya verdiği cevap kullanıcının
    KENDİ başlattığı bir oturumun cevabıdır ve outbox'a girmez — onu WA3'ün
    işçisi doğrudan sağlayıcı üzerinden gönderir. Gerekçenin tamamı
    ``docs/whatsapp/WA4_KANALLAR.md``dedir.
    """

    supports_idempotency = False

    def send(self, notification: dict[str, Any]) -> NotificationResult:
        from ..whatsapp import saglayici

        recipient = str(notification.get("recipient") or "").strip()
        if not recipient:
            # İÇERİK HATASI, AĞ HATASI DEĞİL: hedefi olmayan bir satır
            # yeniden denenerek düzelmez. Yapılandırma denetiminden ÖNCE
            # duruyor ki bozuk bir satır, kanal kapalıyken de görünür olsun.
            raise ValueError("WhatsApp bildiriminde alıcı numarası yok")
        payload = notification.get("payload") or {}
        body = str(payload.get("body") or "").strip()
        if not body:
            raise ValueError("WhatsApp bildirimi payload'ında gövde yok")

        # SİMÜLASYON ÖNCE GELİR ve sıra ÖLÇÜLDÜ: yapılandırma denetimi
        # önce koşsaydı, jetonu olmayan bir kurulumda `simulation` ayarı
        # HİÇ ETKİ ETMEZ ve `NONE` dönerdi — yani akışı ağa çıkmadan uçtan
        # uca koşturmak için tam da jeton koymak gerekirdi. Simülasyon
        # OPERATÖRÜN AÇIK SEÇİMİDİR ve ağa hiçbir koşulda çıkmaz.
        provider_name = (
            getattr(self.settings, "notification_provider", "") or ""
        ).strip().lower()
        if provider_name == "simulation":
            anahtar = str(
                notification.get("provider_idempotency_key") or "whatsapp"
            )
            return NotificationResult(
                status="SIMULATED",
                message="WhatsApp simülasyonu: mesaj dışarı gönderilmedi",
                external_id="wa-" + anahtar.replace(":", "-"),
            )

        # YAPILANDIRMA DENETİMİ, TAŞIYICININ OKUDUĞU KAYNAKTAN OKUNUR.
        # `self.settings` DEĞİL, `saglayici`nin kendi genel ayarları —
        # çünkü gönderimi asıl yapan `saglayici_al()` da oradan okuyor.
        # İki ayrı kaynağa bakmak, "yapılandırılmış" deyip NoOp'a düşen ve
        # hiçbir şey göndermeden `SENT` yazan bir yol açardı.
        if not saglayici.yapilandirildi_mi():
            return NotificationResult(
                status="NONE", message=_WHATSAPP_NOT_CONFIGURED
            )

        # İstisnalar YUKARI TAŞINIR ve motor tarafından sınıflandırılıp
        # maskelenir. Bu sınıf `NotificationResult.message` alanına hiçbir
        # sunucu metni yazmaz — `SmtpEmailNotificationProvider` ile AYNI
        # kural. Sağlayıcının kendi günlüğü de yalnız durum kodu ve maskeli
        # numara taşır.
        saglayici.saglayici_al().metin_gonder(recipient, body)
        return NotificationResult(status="SENT")


class PushNotificationProvider(NotificationProvider):
    """PUSH kanalının taşıyıcı yeri — 5.4c'de AĞA ÇIKMIYOR.

    GERÇEK TESLİMAT ÖLÇÜLMEDİ VE İDDİA EDİLMİYOR. Bu turda depoda FCM ya da
    APNs kimlik bilgisi YOKTUR (ölçüldü: `app/config.py`de `fcm`/`apns`
    literali SIFIR kez geçiyor), yani gönderilecek bir yer yoktur. Bu sınıf
    DENEMEYİ KAYDEDER ve `SIMULATED` döner.

    `SENT` YAZMIYOR ve bu, `SimulationNotificationProvider`ın yıllardır
    yazılı kuralının AYNEN uygulanmasıdır: gerçekten gönderilmemiş bir mesajı
    "gönderildi" diye raporlamak denetim izini yalan söyler hâle getirir.
    `SIMULATED` terminaldir (`schema.TERMINAL_STATUSES`), yani satır yeniden
    denemeye TAKILMAZ; raporlarda gerçek gönderimlerden AYRI sayılır.

    `supports_idempotency = True`: bu sağlayıcının yan etkisi YOKTUR, yani
    süresi dolmuş bir lease'in geri alınması aynı mesajı iki kez GÖNDEREMEZ.
    Gerçek adaptör geldiğinde bu değer YENİDEN ÖLÇÜLMELİDİR — FCM'in kendi
    tekilleştirme anahtarı vardır, APNs'in `apns-collapse-id`si ise farklı
    çalışır.

    CİHAZ JETONUNUN GEÇERSİZLEŞMESİ (FCM `NotRegistered`, APNs `Unregistered`)
    bu turda ELE ALINMIYOR: gerçek bir cevap olmadığı için hangi jetonun ölü
    olduğunu söyleyecek bir kaynak yok. Cihazı pasifleştiren tek yol bugün
    açıktır ve ölçülüdür — `DELETE /api/push/devices/{id}` ve `logout-all`.
    """

    supports_idempotency = True

    def send(self, notification: dict[str, Any]) -> NotificationResult:
        jeton = str(notification.get("recipient") or "").strip()
        if not jeton:
            # İÇERİK HATASI, AĞ HATASI DEĞİL: hedefi olmayan bir satır
            # yeniden denenerek düzelmez.
            raise ValueError("Push bildiriminde cihaz jetonu yok")
        anahtar = str(notification.get("provider_idempotency_key") or "push")
        return NotificationResult(
            status="SIMULATED",
            message="Push simülasyonu: sağlayıcı yapılandırılmadı",
            external_id="push-" + anahtar.replace(":", "-"),
        )


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
    "push": PushNotificationProvider,
    "simulation": SimulationNotificationProvider,
    "smtp": SmtpEmailNotificationProvider,
    "twilio": TwilioNotificationProvider,
    "whatsapp": WhatsAppNotificationProvider,
}

#: KANALA ÇİVİLİ SAĞLAYICILAR — ayarlardan BAĞIMSIZ (5.4c).
#:
#: Bu eşleme olmasaydı ölçülen kusur şu olurdu: `notification_provider="smtp"`
#: ayarlı bir kurulumda PUSH kanalındaki bir outbox satırı SMTP adaptörüne
#: giderdi ve o adaptör alıcı alanındaki CİHAZ JETONUNU bir e-posta adresi
#: sanıp ona mail atmaya çalışırdı. Kanal, taşıyıcıyı BELİRLER; ayar yalnız
#: kanalın BİRDEN ÇOK adaptörü olduğu yerde (SMS: twilio/simulation) seçer.
#:
#: Küme KAPALI ve bugün TEK üyesi var: SMS/WHATSAPP/EMAIL kanalları
#: TARİHSEL olarak ayardan seçiliyor ve o davranış BU TURDA KIMILDAMADI —
#: kımıldatmak, çalışan üç kanalı tek turda değiştirmek olurdu.
KANAL_SAGLAYICILARI: dict[str, type[NotificationProvider]] = {
    "PUSH": PushNotificationProvider,
    # WHATSAPP (WA4). 5.4c'de bu kanal "TARİHSEL olarak ayardan seçiliyor ve
    # o davranış BU TURDA KIMILDAMADI" diye AYRIK bırakılmıştı; kımıldatan
    # şey, kanalın artık GERÇEK bir adaptörünün olmasıdır.
    #
    # Çivilenmeseydi ölçülen kusur şu olurdu: `notification_provider="smtp"`
    # ayarlı bir kurulumda WHATSAPP kanalındaki bir outbox satırı SMTP
    # adaptörüne giderdi ve o adaptör alıcı alanındaki TELEFON NUMARASINI
    # bir e-posta adresi sanıp ona mail atmaya çalışırdı — PUSH için
    # ölçülen kusurun birebir aynısı, yalnız cihaz jetonu yerine numarayla.
    #
    # Çivi, simülasyonu KAPATMAZ: `notification_provider="simulation"`
    # ayarında bu adaptörün KENDİSİ `SIMULATED` döner (sınıfın başlığı),
    # yani ağa çıkmadan uçtan uca koşturma yolu AÇIK kalır. Çivinin
    # kapattığı tek şey, WhatsApp satırının BAŞKA bir kanalın taşıyıcısına
    # düşmesidir.
    "WHATSAPP": WhatsAppNotificationProvider,
}


def get_notification_provider(
    settings: Any, *, channel: str | None = None
) -> NotificationProvider:
    """Resolve the adapter: channel-pinned first, then configured, then NoOp.

    `channel` VERİLMEZSE davranış 5.4c ÖNCESİYLE BİREBİR AYNIDIR — mevcut
    çağıranların hiçbiri kımıldamadı.
    """
    if channel:
        cihaz_tipi = KANAL_SAGLAYICILARI.get(channel.strip().upper())
        if cihaz_tipi is not None:
            return cihaz_tipi(settings)
    name = (
        getattr(settings, "notification_provider", "noop") or "noop"
    ).strip().lower()
    provider_type = _PROVIDERS.get(name)
    if provider_type is None:
        return NoOpNotificationProvider(settings)
    return provider_type(settings)

"""İş günü/saati için tek zaman kaynağı.

Prod sunucusu UTC çalışır, kullanıcılar Europe/Istanbul'dadır. İş anlamında
"bugün" kullanıcının takvim günüdür: İstanbul'da 00:00-03:00 arasında UTC hâlâ
önceki günü gösterir, bu yüzden o aralıkta oluşan kayıtlar UTC tabanlı
raporlarda bir gün geride görünür.

İş tarihini okuyan TEK nokta burasıdır; testler zamanı sabitlemek için
`business_now` fonksiyonunu monkeypatch eder (`business_today` onu modül
global'inden çağırdığı için tek yama ikisini de kapsar).

Teknik zaman damgaları -- audit log, outbox lease, backoff, idempotency, token
süresi, created_at/posted_at -- UTC kalır ve bu modülü KULLANMAZ.

Bu modül bilerek hiçbir `app` modülünü import etmez (döngü riski).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

# tzdata, psycopg üzerinden requirements.lock'ta sabitli; slim imajda da çözülür.
ISTANBUL = ZoneInfo("Europe/Istanbul")


def business_now() -> datetime:
    """İstanbul yerel saati, tz-aware."""
    return datetime.now(ISTANBUL)


def business_today() -> date:
    """İstanbul yerel takvim günü (iş günü)."""
    return business_now().date()

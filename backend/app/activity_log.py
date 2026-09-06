"""Kullanıcı aktivite paneli — değişmez (append-only) denetim kaydı.

Tasarım sözleşmesi
------------------
1. **Değişmezlik.** ``activity_logs`` satırları hiçbir uçtan UPDATE/DELETE
   edilmez. Tek istisna arşiv üçlüsüdür (``archived_at``/``archived_by``/
   ``archive_reason``) ve o da yalnız :func:`archive_log` /
   :func:`unarchive_log` üzerinden, admin rolüne açık iki uçtan yazılır.
   Çekirdek alanlara (özet, detay, kullanıcı, zaman) dokunan bir yol yoktur.
2. **Aynı transaction.** :func:`log_activity` çağrıyı yapan işlemin *kendi*
   Session'ı üzerinde çalışır ve commit etmez. İşlem rollback olursa log satırı
   da yazılmaz; log satırı yazılamazsa istisna yükselir ve işlem de düşer.
   Middleware/sinyal sihri yoktur — kayıt işlemin tam noktasında açıkça alınır.
3. **Ham SQL + zorunlu tenant filtresi.** Bütün sorgular ``text()`` ve bağlı
   parametrelerle yazılır; dinamik liste sorgusu değişmez başlangıç koşulunu ve
   parametresini :func:`tenant_text` yardımcısından birlikte alır.
4. **Para sözleşmesi.** Özet metinlerindeki tutarlar :func:`format_money_tr` ile
   Decimal üzerinden üretilir; ``float``/``Number()`` yoluna hiç girilmez.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, bindparam, text
from sqlalchemy.orm import Session

from .money import decimal_value
from .tenancy import tenant_text

# Ham ``text()`` SQL sonuçlarına açık tip verilir. Aksi hâlde SQLite sürücüsü
# zaman damgalarını düz metin, PostgreSQL ise ``datetime`` döndürür ve panel iki
# veritabanında farklı biçimler görürdü. Bağlı parametreler de aynı nedenle
# tiplenir (dialect'in kendi bind processor'ı devreye girsin diye).
_TS = DateTime(timezone=True)
_RESULT_TYPES = {
    "id": Integer(),
    "company_id": Integer(),
    "user_id": Integer(),
    "action_type": String(),
    "resource_type": String(),
    "resource_id": Integer(),
    "summary": Text(),
    "details": Text(),
    "correlation_id": String(),
    "created_at": _TS,
    "archived_at": _TS,
    "archived_by": Integer(),
    "archive_reason": Text(),
}

# --------------------------------------------------------------------------
# v1 olay kataloğu — kasıtlı olarak kapalı bir küme. Yeni bir olay eklemek
# demek, hem bu katalogda hem de ilgili uçta açık bir ``log_activity`` çağrısı
# eklemek demektir; katalog dışı bir action_type ValueError ile reddedilir.
# --------------------------------------------------------------------------
ACTION_TYPES: dict[str, str] = {
    # Satış
    "sale.create": "Satış oluşturma",
    "sale.update": "Satış güncelleme",
    "sale.cancel": "Satış iptali",
    # POS (dokunmatik hızlı satış) ayrı olay tipleridir: dükkândaki satışın
    # büyük kısmı buradan geçer ve panelde "POS'tan mı satıldı" sorusu tek
    # başına filtrelenebilmelidir. Kaynak tipi yine ``sale``'dir — POS fişi de
    # bir ``orders`` satırıdır, kaynak linki bu sayede çalışmaya devam eder.
    "pos.sale_created": "POS satışı",
    "pos.sale_cancelled": "POS satışı iptali",
    # Tahsilat / ödeme
    "payment.create": "Ödeme oluşturma",
    "payment.update": "Ödeme güncelleme",
    "payment.delete": "Ödeme silme",
    # İade
    "return.create": "İade oluşturma",
    # Fiyatlandırma
    "product.bulk_price_update": "Toplu fiyat güncelleme",
    # Taban birim (sütun göç 20260902_0066, yazma yolu kantar fişi v2):
    # bütün gelecek birim çözümlerinin kaynağı sessizce değişemez; eski ve
    # yeni değer `details`ta durur. Kataloğa GİRMEDEN çağrılamaz — ilk yazım
    # "UPDATE" ile çağırıyordu ve uç 4xx veriyordu (ÖLÇÜLDÜ,
    # `tests/test_kantar_fisi_sozlesme.py` BACAK 1/2 ile).
    "product.base_unit_update": "Ürün taban birimi güncelleme",
    # Stok
    "stock.adjust": "Stok düzeltme",
    "stock.transfer": "Stok transferi",
    # İş emri
    "work_order.create": "İş emri oluşturma",
    "work_order.status_change": "İş emri durum değişikliği",
    "work_order.cancel": "İş emri iptali",
    "work_order.receivable.reverse": "İş emri cari borcu ters kaydı",
    # Fatura
    "invoice.create": "Fatura oluşturma",
    "invoice.cancel": "Fatura iptali",
    # Vade farkı
    "late_fee.draft": "Vade farkı taslağı",
    "late_fee.post": "Vade farkı onayı",
    "late_fee.reversal": "Vade farkı ters kaydı",
    # Tahsis
    "allocation.manual": "Manuel tahsis",
    "allocation.reversal": "Tahsis ters kaydı",
    "allocation.reallocation": "Tahsis yeniden dağıtımı",
    # Kullanıcı yönetimi
    "user.create": "Kullanıcı ekleme",
    # ``user.role_change`` katalogda rezerve tutulur: develop üzerinde rol
    # değiştiren bir uç yoktur (yalnız aktif/pasif değişimi vardır), bu yüzden
    # v1'de emisyonu yoktur. Rol değişikliği ucu geldiğinde katalog hazırdır.
    "user.role_change": "Kullanıcı rol değişikliği",
    "user.status_change": "Kullanıcı durum değişikliği",
    # Panelin kendisi
    "activity_log.archive": "Aktivite kaydı arşivleme",
    "activity_log.unarchive": "Aktivite kaydı arşivden çıkarma",
    # Platform düzeyi tam-veritabanı işlemleri tenant adminliğinden ayrıdır.
    # Kiracı dışa aktarımı YEDEK DEĞİL: platform yedeği kümenin tamamını
    # alır, bu ise TEK firmanın verisini o firmaya teslim eder. Ayrı bir
    # eylem adı olmasaydı ikisi panelde birbirine karışırdı.
    "company.exported": "Kiracı verisi dışa aktarma",
    "company.erased": "Kiracı kapatma",
    "backup.created": "Veritabanı yedeği oluşturma",
    "backup.downloaded": "Veritabanı yedeği indirme",
    "backup.restore_started": "Veritabanı geri yükleme başlatma",
    "backup.restore_completed": "Veritabanı geri yükleme tamamlama",
    "backup.restore_failed": "Veritabanı geri yükleme hatası",
    "backup.maintenance_recovered": "Sahipsiz bakım durumu kurtarıldı",
    "backup.maintenance_legacy_normalized": "Eski bakım durumu normalleştirildi",
    "backup.maintenance_force_cleared": "Bakım durumu zorla temizlendi",
    "backup.restore_rollback_started": "Geri yükleme geri alma başlatma",
    "backup.restore_rollback_completed": "Geri yükleme geri alma tamamlama",
    "backup.restore_rollback_failed": "Geri yükleme geri alma hatası",
    # Bildirimler (tasarım §6). Gönderimin kendisi kadar onay, iptal, izin
    # değişikliği ve kural durdurma da denetime girer: "bu mesaj neden gitti"
    # ve "neden gitmedi" sorularının ikisi de buradan yanıtlanır.
    "notification.queued": "Bildirim kuyruğa alındı",
    "notification.approved": "Bildirim gönderimi onaylandı",
    "notification.cancelled": "Bildirim iptal edildi",
    "notification.sent": "Bildirim gönderildi",
    "notification.failed": "Bildirim gönderilemedi",
    "notification.consent_granted": "Bildirim izni verildi",
    "notification.consent_revoked": "Bildirim izni kaldırıldı",
    "notification.template_created": "Bildirim şablonu oluşturuldu",
    "notification.template_updated": "Bildirim şablonu güncellendi",
    "notification.rule_enabled": "Bildirim kuralı etkinleştirildi",
    "notification.rule_disabled": "Bildirim kuralı kapatıldı",
    "notification.rows_disarmed": "Kuyruktaki bildirimler durduruldu",
    "notification.compliance_unavailable": "Uyum doğrulaması yapılamadı",
    "supplier_price.import_created": "Tedarikçi fiyat importu oluşturma",
    "supplier_price.import_applied": "Tedarikçi fiyat importu uygulama",
    "supplier_price.import_reverted": "Tedarikçi fiyat importu geri alma",
    "supplier_price.block_overridden": "Tedarikçi fiyat blok kabulü",
    "supplier_price.xref_created": "Tedarikçi parça eşlemesi",
    # Outbox yeniden kuyruklama (FIELD_STOK_OUTBOX açılış koşulu 3).
    # `field_integration_events` üzerinde `requeued_by`/`requeued_at` SÜTUNU
    # YOKTUR ve o dilim göç EKLEMEDİ; kimin hangi olayı HANGİ terminal
    # durumdan geri aldığı bu yüzden BURAYA yazılır. Kataloğa girmeden
    # çağrılamaz: `log_activity` katalog dışı bir tipi ValueError ile reddeder.
    "field_event.requeued": "Entegrasyon olayı yeniden kuyruklama",
}

RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "sale",
        "payment",
        "return",
        "product",
        "stock",
        "work_order",
        "invoice",
        "late_fee_charge",
        "payment_allocation",
        "user",
        "activity_log",
        "backup",
        "notification",
        "supplier_price_import",
        "notification_template",
        "notification_consent",
        "notification_rule",
        # Outbox olayı (açılış koşulu 3). Kaynak KİMLİĞİ olay satırının
        # id'sidir; panelin kaynak bağlantısı okuma yüzeyine (`GET
        # /api/field-integration-events`) karşılık gelir.
        "field_integration_event",
    }
)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MIN_ARCHIVE_REASON_LENGTH = 5

_SELECT_COLUMNS = """id, company_id, user_id, action_type, resource_type,
    resource_id, summary, details, correlation_id, created_at,
    archived_at, archived_by, archive_reason"""


# --------------------------------------------------------------------------
# Biçimlendirme yardımcıları
# --------------------------------------------------------------------------
def format_money_tr(value: object, currency: str = "TL") -> str:
    """``4500`` → ``4.500,00 TL``. Tamamen Decimal üzerinden çalışır."""
    amount = decimal_value(value, field_name="Tutar").quantize(Decimal("0.01"))
    sign = "-" if amount < 0 else ""
    whole, _, fraction = format(abs(amount), "f").partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    fraction = (fraction + "00")[:2]
    formatted = f"{sign}{grouped},{fraction}"
    return f"{formatted} {currency}".strip()


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def dump_details(details: Mapping[str, Any] | None) -> str | None:
    if details is None:
        return None
    return json.dumps(details, ensure_ascii=False, sort_keys=True, default=_json_default)


def diff_details(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    """Panelde gösterilecek eski/yeni değer özünü üretir.

    Yalnız ``fields`` içindeki alanlar ve yalnız gerçekten değişenler saklanır;
    denetim kaydı bütün satırın kopyası değil, kararın özüdür.
    """
    before_values = dict(before or {})
    after_values = dict(after or {})
    changed: dict[str, Any] = {}
    for field in fields:
        old = before_values.get(field)
        new = after_values.get(field)
        if old == new:
            continue
        changed[field] = {
            "old": _json_default(old) if old is not None else None,
            "new": _json_default(new) if new is not None else None,
        }
    return changed


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Yakalama
# --------------------------------------------------------------------------
def log_activity(
    conn: Session,
    company_id: int,
    user_id: int | None,
    action_type: str,
    resource_type: str,
    resource_id: int | None,
    summary: str,
    details: Mapping[str, Any] | None = None,
    *,
    correlation_id: str | None = None,
) -> int:
    """İşlemin transaction'ı içinde tek bir aktivite satırı yazar.

    ``conn`` çağıran ucun kendi Session'ıdır ve burada **commit edilmez**:
    denetim garantisi tam olarak buradan gelir — işlem düşerse kayıt da düşer,
    kayıt yazılamazsa işlem de düşer.
    """
    if action_type not in ACTION_TYPES:
        raise ValueError(f"Bilinmeyen aktivite tipi: {action_type}")
    if resource_type not in RESOURCE_TYPES:
        raise ValueError(f"Bilinmeyen kaynak tipi: {resource_type}")
    clean_summary = (summary or "").strip()
    if not clean_summary:
        raise ValueError("Aktivite özeti boş olamaz")

    row = conn.execute(
        text(
            """INSERT INTO activity_logs
               (company_id, user_id, action_type, resource_type, resource_id,
                summary, details, correlation_id, created_at)
               VALUES (:cid, :uid, :action_type, :resource_type, :resource_id,
                :summary, :details, :correlation_id, :created_at)
               RETURNING id"""
        ).bindparams(bindparam("created_at", type_=_TS)),
        {
            "cid": company_id,
            "uid": user_id,
            "action_type": action_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "summary": clean_summary,
            "details": dump_details(details),
            "correlation_id": correlation_id,
            "created_at": _utcnow(),
        },
    ).scalar_one()
    return int(row)


def actor(request: Any) -> tuple[int | None, str | None]:
    """``request.state`` üzerinden (kullanıcı id, correlation id) çifti."""
    user = getattr(getattr(request, "state", None), "user", None)
    user_id = user.get("id") if isinstance(user, dict) else None
    correlation_id = getattr(getattr(request, "state", None), "request_id", None)
    return (int(user_id) if user_id is not None else None, correlation_id)


def log_request_activity(
    conn: Session,
    request: Any,
    company_id: int,
    action_type: str,
    resource_type: str,
    resource_id: int | None,
    summary: str,
    details: Mapping[str, Any] | None = None,
) -> int:
    """Uçlarda kullanılan ince sarmalayıcı: aktörü ``request``'ten çözer.

    Sihir değil, yalnız iki alanın (kullanıcı, correlation) tekrarını önler;
    çağrı yine işlem noktasında ve açıktır.
    """
    user_id, correlation_id = actor(request)
    return log_activity(
        conn,
        company_id,
        user_id,
        action_type,
        resource_type,
        resource_id,
        summary,
        details,
        correlation_id=correlation_id,
    )


# --------------------------------------------------------------------------
# Okuma
# --------------------------------------------------------------------------
def _iso_utc(value: Any) -> str | None:
    """SQLite (naive) ve PostgreSQL (aware) damgalarını tek biçime indirger."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode_row(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    raw_details = item.pop("details", None)
    try:
        item["details"] = json.loads(raw_details) if raw_details else None
    except (TypeError, ValueError):
        item["details"] = None
    item["created_at"] = _iso_utc(item.get("created_at"))
    item["archived_at"] = _iso_utc(item.get("archived_at"))
    item["action_label"] = ACTION_TYPES.get(str(item.get("action_type")), "")
    item["is_archived"] = item.get("archived_at") is not None
    return item


def list_activity_logs(
    conn: Session,
    company_id: int,
    *,
    user_id: int | None = None,
    action_type: str | None = None,
    resource_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_archived: bool = False,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    """Sunucu tarafı sayfalama + filtreleme. Tenant filtresi zorunludur."""
    bounded_limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    bounded_offset = max(0, int(offset))

    conditions: list[str] = []
    params: dict[str, Any] = {}
    if user_id is not None:
        conditions.append("user_id = :uid")
        params["uid"] = int(user_id)
    if action_type:
        conditions.append("action_type = :action_type")
        params["action_type"] = action_type
    if resource_type:
        conditions.append("resource_type = :resource_type")
        params["resource_type"] = resource_type
    if date_from:
        conditions.append("created_at >= :date_from")
        params["date_from"] = _parse_boundary(date_from, end=False)
    if date_to:
        conditions.append("created_at <= :date_to")
        params["date_to"] = _parse_boundary(date_to, end=True)
    if not include_archived:
        conditions.append("archived_at IS NULL")

    # Bu parça yalnız modül içindeki sabit, isteğe bağlı koşullardan oluşur;
    # kullanıcıdan gelen her değer bağlı parametredir. Zorunlu tenant koşulu
    # SQL içinde literal kalır ve ``tenant_text`` tarafından da doğrulanır.
    optional_where = "".join(f" AND {condition}" for condition in conditions)
    date_binds = [
        bindparam(name, type_=_TS) for name in ("date_from", "date_to") if name in params
    ]
    count_sql, tenant_params = tenant_text(
        f"SELECT COUNT(*) FROM activity_logs WHERE company_id = :cid{optional_where}",
        company_id,
    )
    page_text, page_tenant_params = tenant_text(
        f"""SELECT {_SELECT_COLUMNS} FROM activity_logs
            WHERE company_id = :cid{optional_where}
            ORDER BY created_at DESC, id DESC
            LIMIT :limit OFFSET :offset""",
        company_id,
    )
    params = {**tenant_params, **page_tenant_params, **params}
    if date_binds:
        count_sql = count_sql.bindparams(*date_binds)
        page_text = page_text.bindparams(*date_binds)
    page_sql = page_text.columns(**_RESULT_TYPES)
    total = int(conn.execute(count_sql, params).scalar_one())
    rows = conn.execute(
        page_sql,
        {**params, "limit": bounded_limit, "offset": bounded_offset},
    ).mappings().all()
    return {
        "items": [_decode_row(row) for row in rows],
        "total": total,
        "limit": bounded_limit,
        "offset": bounded_offset,
    }


def _parse_boundary(value: str, *, end: bool) -> datetime:
    """``2026-07-27`` → gün başı/sonu; tam ISO damgası aynen kullanılır."""
    text_value = str(value).strip()
    try:
        if len(text_value) == 10:
            day = date.fromisoformat(text_value)
            time_part = (
                datetime.max.time().replace(microsecond=999999)
                if end
                else datetime.min.time()
            )
            return datetime.combine(day, time_part, tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Geçersiz tarih değeri") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def get_activity_log(
    conn: Session, company_id: int, log_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            f"""SELECT {_SELECT_COLUMNS} FROM activity_logs
                WHERE company_id = :cid AND id = :id"""
        ).columns(**_RESULT_TYPES),
        {"cid": company_id, "id": log_id},
    ).mappings().first()
    return _decode_row(row) if row else None


# --------------------------------------------------------------------------
# Arşivleme — çekirdek alanlara dokunmayan TEK yazma yolu
# --------------------------------------------------------------------------
def archive_log(
    conn: Session,
    company_id: int,
    log_id: int,
    *,
    archived_by: int,
    reason: str,
) -> dict[str, Any]:
    """Yalnız ``archived_*`` kolonlarını doldurur; commit etmez."""
    clean_reason = (reason or "").strip()
    if len(clean_reason) < MIN_ARCHIVE_REASON_LENGTH:
        raise ValueError(
            f"Arşivleme gerekçesi en az {MIN_ARCHIVE_REASON_LENGTH} karakter olmalıdır"
        )
    updated = conn.execute(
        text(
            """UPDATE activity_logs
               SET archived_at = :now, archived_by = :actor, archive_reason = :reason
               WHERE company_id = :cid AND id = :id AND archived_at IS NULL"""
        ).bindparams(bindparam("now", type_=_TS)),
        {
            "now": _utcnow(),
            "actor": archived_by,
            "reason": clean_reason,
            "cid": company_id,
            "id": log_id,
        },
    ).rowcount
    if not updated:
        raise LookupError("Arşivlenecek aktivite kaydı bulunamadı")
    record = get_activity_log(conn, company_id, log_id)
    assert record is not None
    return record


def unarchive_log(conn: Session, company_id: int, log_id: int) -> dict[str, Any]:
    """Arşiv üçlüsünü temizler; çekirdek alanlara dokunmaz, commit etmez."""
    updated = conn.execute(
        text(
            """UPDATE activity_logs
               SET archived_at = NULL, archived_by = NULL, archive_reason = NULL
               WHERE company_id = :cid AND id = :id AND archived_at IS NOT NULL"""
        ),
        {"cid": company_id, "id": log_id},
    ).rowcount
    if not updated:
        raise LookupError("Arşivden çıkarılacak aktivite kaydı bulunamadı")
    record = get_activity_log(conn, company_id, log_id)
    assert record is not None
    return record

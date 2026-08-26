from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from time import time_ns

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..db import get_db
from ..tenancy import company_id

router = APIRouter(prefix="/field", tags=["field"])

# Sahadan yapılabilecek durum geçişleri. work_orders.ALLOWED_TRANSITIONS'ın
# KASITLI olarak dar bir alt kümesi.
#
# Dışarıda bırakılanlar ve nedenleri:
#   CANCELLED  — iptal ticari bir karardır; rezerve parça stoğunu geri verir ve
#                kaydedilmiş işçiliği iptal eder. Teknisyenin telefonundan,
#                üstelik çevrimdışı kuyruktan tetiklenmemeli.
#   DELIVERED  — teslim müşteriyle yüz yüze kapanıştır ve hizmet alacağını
#                kesinleştirir; ofisin işi.
#   COMPLETED → IN_PROGRESS — tamamlanmış işi yeniden açmak parça/işçilik
#                donmasını çözer ve faturalanmışsa reddedilmesi gerekir.
#                Bu kontrolü sahaya taşımanın karşılığı yok.
#
# Yani saha yüzeyi yalnızca işin İLERLEYİŞİNİ bildirir; ticari sonuç doğuran
# geçişler panelde kalır. Genişletmek isteyen bu listeyi büyütür, ama yukarıdaki
# gerekçeleri karşılamak zorundadır.
FIELD_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "SCHEDULED": {"OPEN"},
    "OPEN": {"IN_PROGRESS"},
    "IN_PROGRESS": {"WAITING_PARTS", "WAITING_CUSTOMER", "COMPLETED"},
    "WAITING_PARTS": {"IN_PROGRESS", "WAITING_CUSTOMER"},
    "WAITING_CUSTOMER": {"IN_PROGRESS", "WAITING_PARTS"},
    "COMPLETED": set(),
    "DELIVERED": set(),
    "CANCELLED": set(),
}

_OPERATION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class FieldMachine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    brand: str | None
    model: str | None
    serial_number: str | None
    chassis_number: str | None
    working_hours: str | None


class FieldCustomer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    service_address: str | None


class FieldPart(BaseModel):
    """İş emrine kayıtlı bir parça — teknisyenin çevrimdışı görebildiği hâli.

    FİYAT YOK, bilerek. Teknisyenin sahada ihtiyacı olan bilgi "hangi parça,
    ne kadar, hangi durumda"; satır tutarı ticari bilgidir ve bu DTO'nun
    tamamı teknisyenin cihazında ŞİFRESİZ, saatlerce duruyor. Snapshot'ın
    geri kalanı da aynı asgari çizgide; hangi alanların dışarıda kaldığı
    test_field_snapshot_contract'ta sayılı.

    (Not: bu docstring JSON şemasına da giriyor ve o test şema metnini yasaklı
    kelimelere karşı tarıyor — dışarıda kalan alanları burada ADIYLA saymak
    testi düşürür. Bir kez düşürdü.)
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    product_name: str
    quantity: str
    unit: str | None
    # RESERVED | ISSUED | RETURNED — teknisyen "ayrıldı mı, çıktı mı, iade mi"
    # ayrımını görmeden hangi parçayı taktığını bilemez.
    line_status: str


class FieldAttachment(BaseModel):
    """İş emrine yüklenmiş bir ek — teknisyenin çevrimdışı gördüğü LİSTE hâli.

    Dosyanın KENDİSİ taşınmıyor, yalnız kaydı. Fotoğraflar megabaytlarca yer
    tutar; hepsini snapshot'a koymak zayıf şebekede senkronu dakikalara çıkarır
    ve cihaz depolamasını doldurur. Teknisyenin sahada ihtiyacı olan bilgi
    "bu iş emrine kaç fotoğraf, ne zaman yüklendi" — dosyayı görmek isterse
    çevrimiçiyken indirir.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: str
    file_name: str
    created_at: datetime


class FieldWorkOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    work_order_no: str
    status: str
    version: str
    updated_at: datetime
    priority: str
    scheduled_date: datetime | None
    complaint: str | None
    diagnosis: str | None
    repair_summary: str | None
    technician_notes: str | None
    machine: FieldMachine
    customer: FieldCustomer
    parts: list[FieldPart]
    attachments: list[FieldAttachment]


class FieldSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_version: int
    generated_at: datetime
    user_id: int
    company_id: int
    work_orders: list[FieldWorkOrder]


_FIELD_WORK_ORDER_SELECT = """SELECT w.id,w.work_order_no,w.status,w.updated_at,w.priority,
    w.scheduled_date,w.complaint,w.diagnosis,w.repair_summary,
    w.technician_notes,m.id machine_id,m.brand machine_brand,
    m.model machine_model,m.serial_number,m.chassis_number,
    m.working_hours,c.name customer_name,c.address service_address
    FROM work_orders w
    JOIN machines m ON m.id=w.machine_id AND m.company_id=w.company_id
    JOIN customers c ON c.id=w.customer_id AND c.company_id=w.company_id
    WHERE w.company_id=:cid AND w.technician_id=:user_id"""


def _version_of(updated_at: object) -> str:
    """Eşzamanlılık anahtarı.

    İş emrinde ayrı bir sürüm sütunu YOK; istemciye gönderilen `version`
    `updated_at`ten türetiliyor. Snapshot bunu böyle ürettiği için yazma ucu da
    aynı şekilde üretmek ZORUNDA — iki yerde farklı biçimlenirse istemcinin
    gönderdiği sürüm hiçbir zaman tutmaz ve her yazma çakışma sanılır.
    """

    return updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at)


def _field_parts(db: Session, cid: int, user_id: int) -> dict[int, list[FieldPart]]:
    """Teknisyenin TÜM iş emirlerinin parçalarını tek sorguda getirir.

    İş emri başına ayrı sorgu (N+1) yerine tek sorgu: saha snapshot'ı zayıf
    şebekede çekiliyor, sorgu sayısı doğrudan bekleme süresi demek.

    Tek bir iş emri için de bu kullanılıp sonuç Python'da süzülüyor. Sorguya
    `AND wp.work_order_id=:wid` eklemek daha dar olurdu ama SQL'i çalışma
    anında kurmayı gerektirirdi; teknisyenin açık iş emri sayısı zaten küçük.
    """

    rows = db.execute(
        text(
            """SELECT wp.id,wp.work_order_id,wp.quantity,wp.line_status,
            p.name product_name,p.unit
            FROM work_order_parts wp
            JOIN work_orders w ON w.id=wp.work_order_id AND w.company_id=wp.company_id
            JOIN products p ON p.id=wp.product_id AND p.company_id=wp.company_id
            WHERE wp.company_id=:cid AND w.technician_id=:user_id
            ORDER BY wp.id"""
        ),
        {"cid": cid, "user_id": user_id},
    ).mappings().all()
    grouped: dict[int, list[FieldPart]] = {}
    for row in rows:
        grouped.setdefault(int(row["work_order_id"]), []).append(
            FieldPart(
                id=int(row["id"]),
                product_name=str(row["product_name"]),
                # Miktar METİN olarak taşınıyor: JavaScript'in float'ı 0.1+0.2
                # gibi değerleri bozar ve saha kaydı doğrudan faturaya giden
                # bir sayıyı temsil ediyor.
                quantity=str(row["quantity"]),
                unit=row["unit"],
                line_status=str(row["line_status"]),
            )
        )
    return grouped


def _field_attachments(db: Session, cid: int, user_id: int) -> dict[int, list[FieldAttachment]]:
    """Teknisyenin tüm iş emirlerinin ek KAYITLARINI tek sorguda getirir.

    Dosya içeriği yok, yalnız kayıt (bkz. FieldAttachment gerekçesi). Parça
    sorgusuyla aynı desen: iş emri başına ayrı sorgu, zayıf şebekede doğrudan
    bekleme süresi demek.
    """

    rows = db.execute(
        text(
            """SELECT a.id,a.work_order_id,a.kind,a.file_name,a.created_at
            FROM work_order_attachments a
            JOIN work_orders w ON w.id=a.work_order_id AND w.company_id=a.company_id
            WHERE a.company_id=:cid AND w.technician_id=:user_id
            ORDER BY a.id"""
        ),
        {"cid": cid, "user_id": user_id},
    ).mappings().all()
    grouped: dict[int, list[FieldAttachment]] = {}
    for row in rows:
        grouped.setdefault(int(row["work_order_id"]), []).append(
            FieldAttachment(
                id=int(row["id"]),
                kind=str(row["kind"]),
                file_name=str(row["file_name"]),
                created_at=row["created_at"],
            )
        )
    return grouped


def _to_field_work_order(row, parts: list[FieldPart] | None = None,
                         attachments: list[FieldAttachment] | None = None) -> FieldWorkOrder:
    return FieldWorkOrder(
        id=int(row["id"]),
        work_order_no=str(row["work_order_no"]),
        status=str(row["status"]),
        version=_version_of(row["updated_at"]),
        updated_at=row["updated_at"],
        priority=str(row["priority"]),
        scheduled_date=row["scheduled_date"],
        complaint=row["complaint"],
        diagnosis=row["diagnosis"],
        repair_summary=row["repair_summary"],
        technician_notes=row["technician_notes"],
        machine=FieldMachine(
            id=int(row["machine_id"]),
            brand=row["machine_brand"],
            model=row["machine_model"],
            serial_number=row["serial_number"],
            chassis_number=row["chassis_number"],
            working_hours=(
                str(row["working_hours"]) if row["working_hours"] is not None else None
            ),
        ),
        customer=FieldCustomer(
            display_name=str(row["customer_name"]),
            service_address=row["service_address"],
        ),
        parts=parts or [],
        attachments=attachments or [],
    )


def _field_user_id(request: Request) -> int:
    user = getattr(request.state, "user", {}) or {}
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:  # middleware invariant
        raise HTTPException(404, "Saha kaydı bulunamadı") from exc


@router.get("/snapshot", response_model=FieldSnapshot)
def field_snapshot(request: Request, db: Session = Depends(get_db)) -> FieldSnapshot:
    """Return only this authenticated user's non-terminal assigned work."""

    cid = company_id(request)
    user_id = _field_user_id(request)

    rows = db.execute(
        text(
            _FIELD_WORK_ORDER_SELECT
            + """ AND w.status NOT IN ('DELIVERED','CANCELLED')
            ORDER BY w.scheduled_date,w.id"""
        ),
        {"cid": cid, "user_id": user_id},
    ).mappings().all()

    parts = _field_parts(db, cid, user_id)
    ekler = _field_attachments(db, cid, user_id)
    work_orders = [_to_field_work_order(row, parts.get(int(row["id"])), ekler.get(int(row["id"]))) for row in rows]
    return FieldSnapshot(
        # Microseconds remain below JavaScript's MAX_SAFE_INTEGER while still
        # giving concurrent snapshot requests a monotonic ordering key.
        snapshot_version=time_ns() // 1_000,
        generated_at=datetime.now(timezone.utc),
        user_id=user_id,
        company_id=cid,
        work_orders=work_orders,
    )


class FieldStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    # İstemcinin gördüğü sürüm. Snapshot'taki `version` alanı birebir buraya
    # geri gönderilir; sunucudaki değerle tutmazsa yazma reddedilir. Çevrimdışı
    # çalışan bir istemci saatlerce eski veriyle gezebilir, bu yüzden "son yazan
    # kazanır" kabul edilemez.
    expected_version: str
    status: str

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not _OPERATION_ID.match(value):
            raise ValueError("Geçersiz işlem kimliği")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return value.strip().upper()


class FieldStatusConflict(BaseModel):
    """409 gövdesi.

    Çakışmada istemciye yalnız "olmadı" demek yetmez: teknisyen sahada, veri
    saatler öncesine ait olabilir. Sunucunun güncel hâli birlikte gönderiliyor
    ki istemci çakışmayı kullanıcıya gösterebilsin ve önbelleğini tazeleyebilsin.
    """

    model_config = ConfigDict(extra="forbid")

    detail: str
    current: FieldWorkOrder


def _field_work_order(db: Session, cid: int, user_id: int, work_order_id: int, *, lock: bool = False):
    """İş emrini SAHA kapsamıyla oku.

    Kapsam çift: firma VE isteği yapan teknisyen. Başkasının iş emri için 404
    dönüyoruz, 403 değil — 403 "var ama sana kapalı" bilgisini sızdırır ve
    teknisyenin kendi listesi dışındaki kayıtların varlığını öğrenmesine yarar.
    """

    lock_clause = (
        " FOR UPDATE OF w" if lock and db.get_bind().dialect.name == "postgresql" else ""
    )
    row = db.execute(
        text(_FIELD_WORK_ORDER_SELECT + " AND w.id=:wid" + lock_clause),
        {"cid": cid, "user_id": user_id, "wid": work_order_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "Saha kaydı bulunamadı")
    return row


@router.post(
    "/work-orders/{work_order_id}/status",
    response_model=FieldWorkOrder,
    responses={409: {"model": FieldStatusConflict}},
)
def field_change_status(
    work_order_id: int,
    payload: FieldStatusChange,
    request: Request,
    db: Session = Depends(get_db),
) -> FieldWorkOrder:
    """Sahadan iş emri durumu ilerlet.

    Panelin `PATCH /work-orders/{id}/status` ucundan üç noktada ayrılır:

    1. Kapsam teknisyenin kendi işleriyle sınırlı (yukarıdaki 404 kararı).
    2. Geçiş kümesi dar (FIELD_ALLOWED_TRANSITIONS'daki gerekçeler).
    3. İyimser kilit + tekrar koruması var; çevrimdışı kuyruk ikisini de
       zorunlu kılıyor.

    Asıl işi panelin ucu yapıyor: durum zaman damgaları, COMPLETED'da hizmet
    alacağının oluşturulması, denetim kaydı ve aktivite günlüğü orada. Burada
    kopyalamak, iki yerde ayrışacak iki gerçek üretirdi.
    """

    cid = company_id(request)
    user_id = _field_user_id(request)
    before = _field_work_order(db, cid, user_id, work_order_id, lock=True)
    current_status = str(before["status"])

    # TEKRAR GÖNDERİMİ DURDURAN ASIL MEKANİZMA BURASI — ve sırası önemli.
    #
    # Sürüm kontrolünden ÖNCE olmak ZORUNDA: tekrar gönderilen bir işlem tanımı
    # gereği bayat sürüm taşır (ilk gönderim uygulanmış, updated_at ilerlemiştir).
    # Sıra ters olsaydı her tekrar gönderim 409 alırdı ve teknisyen kuyruktaki
    # işin başarısız olduğunu sanardı; oysa iş yapılmıştır.
    #
    # ÖLÇÜLDÜ (PG16, eşzamanlı iki özdeş istek): bu blok devre dışı bırakılınca
    # ikinci istek 200 değil 409 alıyor. Yani aşağıdaki benzersizlik kısıtı bu
    # görevi DEVRALMIYOR — `FOR UPDATE` ikinci isteği bekletiyor, o da kilidi
    # alınca güncel sürümü görüp sürüm kontrolüne takılıyor ve INSERT'e hiç
    # varmıyor. Kısıt, satır kilidi olmayan diyalektler (SQLite) ve
    # derinlemesine savunma için duruyor; PostgreSQL'de asıl koruyucu bu SELECT.
    already = db.execute(
        text(
            """SELECT 1 FROM field_operations
            WHERE company_id=:cid AND operation_id=:oid"""
        ),
        {"cid": cid, "oid": payload.operation_id},
    ).first()
    if already:
        return _to_field_work_order(before, _field_parts(db, cid, user_id).get(work_order_id),
                                     _field_attachments(db, cid, user_id).get(work_order_id))

    if payload.status not in FIELD_ALLOWED_TRANSITIONS.get(current_status, set()):
        raise HTTPException(
            409,
            f"Sahadan {current_status} durumundan {payload.status} durumuna geçilemez",
        )

    # İyimser kilit. Kilit satırı FOR UPDATE ile tutulduğu için bu kontrolle
    # aşağıdaki güncelleme arasında araya girilemez.
    if payload.expected_version != _version_of(before["updated_at"]):
        # HTTPException KULLANILMIYOR: onun `detail`i gövdeyi bir kat daha
        # sarar ({"detail": {...}}) ve yukarıda belgelenen FieldStatusConflict
        # şeklini bozardı. İstemci `current` alanını doğrudan okuyabilmeli.
        return JSONResponse(
            status_code=409,
            content=FieldStatusConflict(
                detail="İş emri siz çevrimdışıyken değişti",
                current=_to_field_work_order(before, _field_parts(db, cid, user_id).get(work_order_id),
                                     _field_attachments(db, cid, user_id).get(work_order_id)),
            ).model_dump(mode="json"),
        )

    try:
        db.execute(
            text(
                """INSERT INTO field_operations
                (company_id,user_id,work_order_id,operation_id,kind,created_at)
                VALUES (:cid,:uid,:wid,:oid,'status',:now)"""
            ),
            {
                "cid": cid,
                "uid": user_id,
                "wid": work_order_id,
                "oid": payload.operation_id,
                "now": utcnow(),
            },
        )
        db.flush()
    except IntegrityError:
        # Emniyet kemeri. Buraya ancak yukarıdaki SELECT'in iki istekte de "yok"
        # görebildiği bir ortamda düşülür — yani satır kilidi olmayan bir
        # diyalektte (SQLite). PostgreSQL'de `FOR UPDATE` istekleri sıraya
        # soktuğu için pratikte ulaşılmaz; yine de duruyor, çünkü doğruluğun
        # dialect davranışına bağlı kalması istenmiyor.
        db.rollback()
        current = _field_work_order(db, cid, user_id, work_order_id)
        return _to_field_work_order(current, _field_parts(db, cid, user_id).get(work_order_id),
                                      _field_attachments(db, cid, user_id).get(work_order_id))

    from .work_orders import WorkOrderStatusUpdate, update_work_order_status

    # Panelin ucu kendi içinde commit ediyor; yukarıdaki field_operations
    # satırı da aynı işlemin parçası olduğu için onunla birlikte kalıcı olur.
    # Durum değişikliği reddedilirse (ör. eş zamanlı değişiklik) istisna
    # yükselir ve satır da yazılmamış olur — tekrar gönderim yeniden denenebilir.
    update_work_order_status(
        work_order_id, WorkOrderStatusUpdate(status=payload.status), request, db
    )

    after = _field_work_order(db, cid, user_id, work_order_id)
    return _to_field_work_order(after, _field_parts(db, cid, user_id).get(work_order_id),
                                _field_attachments(db, cid, user_id).get(work_order_id))


class FieldPartUse(BaseModel):
    """Sahada kullanılan parça.

    İSTEMCİ FİYAT GÖNDERMEZ ve DEPO SEÇMEZ — ikisi de bilerek sunucuda
    belirleniyor:

    * Fiyat ticari bir karardır; teknisyenin telefonundan gelen bir tutarın
      faturaya dönüşmesi kabul edilemez. Ürünün satış fiyatı ve KDV oranı
      uygulama anında okunur.
    * Depo, teknisyenin KENDİ servis aracı deposudur. İstemciye bıraksaydık
      çevrimdışı bir cihaz yanlış depodan stok düşürebilirdi; üstelik saha
      kullanıcısının depo listesini görmesi de gerekmiyor.

    `expected_version` YOK: parça eklemek bir durum geçişi değil, ekleme.
    Teknisyenin okuduğu bir değerin üstüne yazmıyoruz, bu yüzden iyimser kilit
    yanlış araç olurdu. Tekrar gönderime karşı koruma `operation_id` ile.
    """

    model_config = ConfigDict(extra="forbid")

    operation_id: str
    product_id: int
    quantity: Decimal

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not _OPERATION_ID.match(value):
            raise ValueError("Geçersiz işlem kimliği")
        return value

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("Miktar sıfırdan büyük olmalı")
        return value


def _service_vehicle_warehouse(db: Session, cid: int, user_id: int) -> int:
    """Teknisyenin kendi servis aracı deposu.

    Yoksa TAHMİN EDİLMEZ. Merkez depoya düşmek kolay olurdu ama yanlış: parça
    fiziken araçtan çıkıyor, merkezden düşürmek iki deponun da sayımını bozar
    ve kimse fark etmez. Bunun yerine anlaşılır bir hata dönüyoruz.
    """

    row = db.execute(
        text(
            """SELECT id FROM warehouses
            WHERE company_id=:cid AND technician_user_id=:uid
              AND warehouse_type='SERVICE_VEHICLE' AND is_active=TRUE
            ORDER BY id"""
        ),
        {"cid": cid, "uid": user_id},
    ).first()
    if not row:
        raise HTTPException(
            409,
            "Size tanımlı bir servis aracı deposu yok; parça girişi için "
            "önce yöneticinizden depo tanımlanmasını isteyin",
        )
    return int(row[0])


@router.post("/work-orders/{work_order_id}/parts", status_code=201)
def field_add_part(
    work_order_id: int,
    payload: FieldPartUse,
    request: Request,
    db: Session = Depends(get_db),
):
    """Sahada kullanılan parçayı iş emrine ekle.

    Asıl işi panelin `create_part_in_transaction`'ı yapıyor: rezervasyon mu
    doğrudan çıkış mı (firma ayarı), stok düşümü, satır toplamı, denetim
    kaydı. Burada kopyalamak stok mantığının iki yerde ayrışması demekti.

    Çevrimdışı kuyruktan gelen bir istek stok yetmediği için reddedilebilir;
    bu İSTENEN davranış. Cihaz saatler önce "bu parça var" varsayımıyla
    kuyruğa aldı, o varsayım artık doğru olmayabilir ve stoğu eksiye düşürmek
    yerine teknisyene çakışma göstermek doğrusu.
    """

    cid = company_id(request)
    user_id = _field_user_id(request)
    # Kapsam kontrolü: iş emri bu teknisyenin mi (değilse 404).
    _field_work_order(db, cid, user_id, work_order_id, lock=True)

    already = db.execute(
        text(
            """SELECT 1 FROM field_operations
            WHERE company_id=:cid AND operation_id=:oid"""
        ),
        {"cid": cid, "oid": payload.operation_id},
    ).first()
    if already:
        # Tekrar gönderim: parça zaten eklendi, ikinci kez eklemek stoğu iki
        # kez düşürürdü. Aynı gerekçe durum ucundaki ön kontrolle birebir.
        return {"status": "already_applied", "operation_id": payload.operation_id}

    product = db.execute(
        text(
            """SELECT sale_price,vat_rate FROM products
            WHERE id=:pid AND company_id=:cid AND active=TRUE"""
        ),
        {"pid": payload.product_id, "cid": cid},
    ).mappings().first()
    if not product:
        raise HTTPException(400, "Aktif ürün bulunamadı veya firmaya ait değil")

    warehouse_id = _service_vehicle_warehouse(db, cid, user_id)

    try:
        db.execute(
            text(
                """INSERT INTO field_operations
                (company_id,user_id,work_order_id,operation_id,kind,created_at)
                VALUES (:cid,:uid,:wid,:oid,'part',:now)"""
            ),
            {
                "cid": cid, "uid": user_id, "wid": work_order_id,
                "oid": payload.operation_id, "now": utcnow(),
            },
        )
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "already_applied", "operation_id": payload.operation_id}

    from .work_order_parts import create_part_in_transaction
    from ..work_order_part_schemas import WorkOrderPartWrite

    try:
        after = create_part_in_transaction(
            db, request, cid, work_order_id,
            WorkOrderPartWrite(
                product_id=payload.product_id,
                warehouse_id=warehouse_id,
                quantity=payload.quantity,
                unit_price=product["sale_price"] or Decimal("0"),
                discount=Decimal("0"),
                tax_rate=product["vat_rate"] or Decimal("0"),
            ),
        )
        db.commit()
    except HTTPException:
        # field_operations satırı da geri alınır: istek uygulanmadıysa
        # "uygulandı" izi bırakmak, teknisyenin yeniden denemesini sessizce
        # engellerdi.
        db.rollback()
        raise
    return after


class FieldLaborEntry(BaseModel):
    """Sahada harcanan işçilik.

    İSTEMCİ SAAT ÜCRETİ GÖNDERMEZ ve KİMİN adına yazıldığını SEÇEMEZ:

    * Ücret ticari bir karardır. Panelin ucu zaten "ücret verilmezse iş emri
      başlığındaki labor_rate donar" davranışını uyguluyor; saha da ücreti hiç
      göndermeyerek o yolu kullanıyor. Teknisyenin telefonundan gelen bir saat
      ücretinin faturaya dönüşmesi kabul edilemez.
    * Satır isteği yapan teknisyenin adına açılır. Başkasının adına işçilik
      yazmak bir yönetim işlemi; sahada karşılığı yok.

    Satır DRAFT olarak açılıyor (panelin ucunun varsayılanı) — yani ofis
    onaylamadan faturaya girmiyor. Sahadan gelen bir kaydın doğrudan
    faturalanabilir OLMAMASI bilinçli bir güvenlik payı.
    """

    model_config = ConfigDict(extra="forbid")

    operation_id: str
    hours: Decimal
    note: str | None = None

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not _OPERATION_ID.match(value):
            raise ValueError("Geçersiz işlem kimliği")
        return value

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("Süre sıfırdan büyük olmalı")
        return value


@router.post("/work-orders/{work_order_id}/labor", status_code=201)
def field_add_labor(
    work_order_id: int,
    payload: FieldLaborEntry,
    request: Request,
    db: Session = Depends(get_db),
):
    """Sahada harcanan işçiliği iş emrine yaz."""

    cid = company_id(request)
    user_id = _field_user_id(request)
    _field_work_order(db, cid, user_id, work_order_id, lock=True)

    already = db.execute(
        text(
            """SELECT 1 FROM field_operations
            WHERE company_id=:cid AND operation_id=:oid"""
        ),
        {"cid": cid, "oid": payload.operation_id},
    ).first()
    if already:
        # Tekrar gönderim: satır zaten açıldı. İkinci kez yazmak aynı işçiliği
        # iki kez faturalamak demekti.
        return {"status": "already_applied", "operation_id": payload.operation_id}

    try:
        db.execute(
            text(
                """INSERT INTO field_operations
                (company_id,user_id,work_order_id,operation_id,kind,created_at)
                VALUES (:cid,:uid,:wid,:oid,'labor',:now)"""
            ),
            {
                "cid": cid, "uid": user_id, "wid": work_order_id,
                "oid": payload.operation_id, "now": utcnow(),
            },
        )
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "already_applied", "operation_id": payload.operation_id}

    from .work_order_labor_lines import create_labor_line
    from ..work_order_labor_schemas import LaborLineWrite

    try:
        return create_labor_line(
            work_order_id,
            LaborLineWrite(
                technician_user_id=user_id,
                hours=payload.hours,
                # hourly_rate BİLEREK verilmiyor: panelin ucu iş emri
                # başlığındaki ücreti donduruyor (rate freeze).
                note=payload.note,
            ),
            request,
            db,
        )
    except HTTPException:
        db.rollback()
        raise


# Sahadan yüklenebilecek ek türleri. Panelin `other` seçeneği burada YOK:
# saha uygulaması yalnız kamera ve imza üretiyor, serbest bir "diğer" kanalı
# açmak cihazdan sunucuya rastgele dosya taşımanın önünü açardı.
_FIELD_ATTACHMENT_KINDS = frozenset({"photo", "signature"})


@router.post("/work-orders/{work_order_id}/attachments", status_code=201)
def field_add_attachment(
    work_order_id: int,
    request: Request,
    operation_id: str = Form(...),
    kind: str = Form("photo"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Sahadan fotoğraf veya imza yükle.

    Tekrar gönderim burada özellikle önemli: fotoğraf yükleme uzun sürer ve
    zayıf şebekede cevabın kaybolması olağandır. Koruma olmasaydı her yeniden
    deneme AYNI fotoğrafı bir kez daha diske yazar, iş emrinde beş tane aynı
    resim birikirdi.

    Asıl işi panelin `upload_attachment`'ı yapıyor: akış hâlinde boyut sınırı,
    yol kaçışına karşı koruma, hata hâlinde yetim dosya bırakmama. Burada
    kopyalamak o korumaların ikinci bir kopyasını üretirdi.
    """

    cid = company_id(request)
    user_id = _field_user_id(request)
    if not _OPERATION_ID.match(operation_id):
        raise HTTPException(422, "Geçersiz işlem kimliği")
    normalized_kind = kind.strip().lower()
    if normalized_kind not in _FIELD_ATTACHMENT_KINDS:
        raise HTTPException(422, "Sahadan yalnızca fotoğraf veya imza yüklenebilir")

    # Kapsam: iş emri bu teknisyenin mi (değilse 404).
    _field_work_order(db, cid, user_id, work_order_id)

    already = db.execute(
        text(
            """SELECT 1 FROM field_operations
            WHERE company_id=:cid AND operation_id=:oid"""
        ),
        {"cid": cid, "oid": operation_id},
    ).first()
    if already:
        return {"status": "already_applied", "operation_id": operation_id}

    try:
        db.execute(
            text(
                """INSERT INTO field_operations
                (company_id,user_id,work_order_id,operation_id,kind,created_at)
                VALUES (:cid,:uid,:wid,:oid,'attachment',:now)"""
            ),
            {
                "cid": cid, "uid": user_id, "wid": work_order_id,
                "oid": operation_id, "now": utcnow(),
            },
        )
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "already_applied", "operation_id": operation_id}

    from .work_order_attachments import upload_attachment

    # upload_attachment kendi içinde commit ediyor; yukarıdaki field_operations
    # satırı aynı işlemin parçası olduğu için onunla birlikte kalıcı olur.
    # Yükleme reddedilirse (boyut, boş dosya, kapalı iş emri) o fonksiyon
    # rollback edip dosyayı da siliyor — iz de kalmıyor, dosya da.
    return upload_attachment(work_order_id, request, file, normalized_kind, db)

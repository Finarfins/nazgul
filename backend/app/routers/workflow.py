from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from ..activity_log import format_money_tr, log_request_activity
from ..company_policies import (
    PolicyOverrideContext,
    get_policy_settings,
    negative_stock_allowed,
    override_context,
    record_policy_overrides,
    stock_policy_message,
)
from ..business_time import business_today
from ..db import get_db
from ..document_engine import next_document_no
from ..inventory import adjust_warehouse_stock, default_warehouse, warehouses
from ..money import (
    HUNDRED,
    ZERO_MONEY,
    allocate_vat_inclusive_document_discount,
    apply_global_discount,
    money,
    persisted_percentage,
    quantity,
)
from ..movement_references import validate_return_reference
from ..parti_defteri import _hareket_notu, _parti_geri_al, _parti_tuket
from ..schemas import TransactionCreate, WorkflowDocumentCreate
from ..tenancy import company_id
from .transactions import _save

router = APIRouter(prefix="/workflow", tags=["workflow"])
logger = logging.getLogger("yerel_hesap")
WORKFLOW_FAILED_MESSAGE = "İşlem tamamlanamadı. Lütfen tekrar deneyin."
STOCK_STATUSES = {"completed", "approved"}

#: Satır tabloları sözleşmesi TAMAMLANDI (1. dilim, 20260812_0058).
#:
#: CONFIG'teki bütün satır tabloları artık kendi ``company_id``sini taşıyor,
#: bu yüzden kiracı yüklemi KOŞULSUZDUR. Eskiden burada bir ``tenant_line``
#: bayrağı vardı: taşımayan tabloya ``company_id=:cid`` yazmak "no such
#: column" ile patladığı için yüklem koşullu olmak zorundaydı. Son dilim
#: inince koşul her zaman doğru hâle geldi, yani bayrak ölü ağırlığa dönüştü
#: ve sökülmüştür — üstüne yanlışlıkla iş inşa edilmesin diye.
CONFIG = {
    "quote": dict(
        head="quotes",
        items="quote_items",
        fk="quote_id",
        entity="customer_id",
        date="quote_date",
        prefix="TEK",
        stock=0,
    ),
    "sales_order": dict(
        head="sales_orders",
        items="sales_order_items",
        fk="sales_order_id",
        entity="customer_id",
        date="order_date",
        prefix="SIP",
        stock=0,
    ),
    "delivery": dict(
        head="delivery_notes",
        items="delivery_note_items",
        fk="delivery_note_id",
        entity="customer_id",
        date="delivery_date",
        prefix="IRS",
        stock=-1,
    ),
    "sale_return": dict(
        head="returns",
        items="return_items",
        fk="return_id",
        entity="entity_id",
        date="return_date",
        prefix="SIA",
        stock=1,
        return_type="sale_return",
    ),
    "purchase_return": dict(
        head="returns",
        items="return_items",
        fk="return_id",
        entity="entity_id",
        date="return_date",
        prefix="AIA",
        stock=-1,
        return_type="purchase_return",
    ),
}


RETURN_LABELS = {"sale_return": "satış iadesi", "purchase_return": "alış iadesi"}


def _cfg(kind: str) -> dict:
    if kind not in CONFIG:
        raise HTTPException(404, "Belge türü bulunamadı")
    return CONFIG[kind]


def _workflow_entity_name(db: Session, cid: int, kind: str, entity_id: int | None) -> str:
    """Aktivite özeti için cari adı; tablo adı sabit, tenant filtresi literal."""
    if entity_id is None:
        return "bilinmiyor"
    if kind == "purchase_return":
        row = db.execute(
            text("SELECT name FROM suppliers WHERE id=:id AND company_id=:cid"),
            {"id": int(entity_id), "cid": cid},
        ).first()
    else:
        row = db.execute(
            text("SELECT name FROM customers WHERE id=:id AND company_id=:cid"),
            {"id": int(entity_id), "cid": cid},
        ).first()
    return str(row[0]) if row and row[0] else f"#{entity_id}"


def _warehouse(db: Session, cid: int, warehouse_id: int | None) -> int:
    warehouse_id = warehouse_id or default_warehouse(db, cid)
    exists = db.execute(
        select(warehouses.c.id).where(
            warehouses.c.id == warehouse_id,
            warehouses.c.company_id == cid,
            warehouses.c.is_active.is_(True),
        )
    ).first()
    if not exists:
        raise HTTPException(400, "Depo bulunamadı")
    return int(warehouse_id)


def _totals(db: Session, payload: WorkflowDocumentCreate, cid: int):
    product_ids = list(dict.fromkeys(int(item.product_id) for item in payload.items))
    products = {}
    if product_ids:
        statement = text(
            "SELECT id,name FROM products WHERE company_id=:cid AND id IN :product_ids"
        ).bindparams(bindparam("product_ids", expanding=True))
        products = {
            int(row["id"]): row
            for row in db.execute(
                statement,
                {"cid": cid, "product_ids": product_ids},
            ).mappings().all()
        }

    rows = []
    subtotal = vat_total = gross = ZERO_MONEY
    for item in payload.items:
        product = products.get(int(item.product_id))
        if not product:
            raise HTTPException(400, f"Ürün bulunamadı: {item.product_id}")
        raw = money(quantity(item.quantity) * money(item.unit_price))
        line_discount = money(
            raw * persisted_percentage(item.discount_percent) / HUNDRED
        )
        total = money(raw - line_discount)
        line_subtotal = (
            money(total / (Decimal("1") + Decimal(item.vat_rate) / HUNDRED))
            if item.vat_rate
            else total
        )
        line_vat = money(total - line_subtotal)
        rows.append((product, item, line_subtotal, line_vat, total, line_discount))
        subtotal += line_subtotal
        vat_total += line_vat
        gross += total
    subtotal_after, vat_after, global_discount, final_total = apply_global_discount(
        subtotal, vat_total, gross, persisted_percentage(payload.discount_percent)
    )
    allocated_rows = allocate_vat_inclusive_document_discount(
        [row[4] for row in rows],
        [row[1].vat_rate for row in rows],
        global_discount,
    )
    discounted_rows = [
        (
            product,
            item,
            allocated.subtotal,
            allocated.tax,
            allocated.total,
            money(line_discount + allocated.document_discount),
        )
        for (product, item, _subtotal, _vat, _total, line_discount), allocated
        in zip(rows, allocated_rows, strict=True)
    ]
    subtotal_after = money(sum((row[2] for row in discounted_rows), ZERO_MONEY))
    vat_after = money(sum((row[3] for row in discounted_rows), ZERO_MONEY))
    if money(subtotal_after + vat_after) != final_total:
        raise ValueError("Workflow satırları belge başlığıyla mutabık değil")
    return discounted_rows, subtotal_after, vat_after, final_total, global_discount


def _reverse_stock(
    db: Session,
    cid: int,
    kind: str,
    doc_id: int,
    *,
    policy_mode: str,
    override: PolicyOverrideContext,
    override_policies: set[str],
) -> None:
    config = _cfg(kind)
    if not config["stock"]:
        return
    head = db.execute(
        text(
            f"SELECT COALESCE(warehouse_id,:wid) warehouse_id,"
            f"COALESCE(status,'completed') status FROM {config['head']} "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"id": doc_id, "cid": cid, "wid": default_warehouse(db, cid)},
    ).mappings().first()
    if not head or head["status"] not in STOCK_STATUSES:
        return
    for item in db.execute(
        text(
            f"SELECT product_id,quantity FROM {config['items']} "
            f"WHERE {config['fk']}=:id AND company_id=:cid"
        ),
        {"id": doc_id, "cid": cid},
    ).mappings():
        delta = -config["stock"] * quantity(item["quantity"])
        allow_negative = negative_stock_allowed(policy_mode, override) if delta < 0 else False
        new_stock = adjust_warehouse_stock(
            db,
            cid,
            int(head["warehouse_id"]),
            int(item["product_id"]),
            delta,
            allow_negative=allow_negative,
        )
        if new_stock < 0 and policy_mode == "manager_override":
            override_policies.add("negative_stock")


def _save_doc(
    kind: str,
    payload: WorkflowDocumentCreate,
    db: Session,
    cid: int,
    override: PolicyOverrideContext,
    doc_id: int | None = None,
    *,
    commit: bool = True,
    request: Request | None = None,
):
    config = _cfg(kind)
    old = None
    if doc_id:
        old = db.execute(
            text(
                f"SELECT document_no FROM {config['head']} "
                "WHERE id=:id AND company_id=:cid"
            ),
            {"id": doc_id, "cid": cid},
        ).first()
        if not old:
            raise HTTPException(404, "Belge bulunamadı")

    rows, subtotal, vat_total, total, discount = _totals(db, payload, cid)
    warehouse_id = _warehouse(db, cid, payload.warehouse_id)
    entity_table = "suppliers" if kind == "purchase_return" else "customers"
    if not db.execute(
        text(f"SELECT id FROM {entity_table} WHERE id=:id AND company_id=:cid"),
        {"id": payload.entity_id, "cid": cid},
    ).first():
        raise HTTPException(400, "Cari bulunamadı")
    if kind in {"sale_return", "purchase_return"}:
        validate_return_reference(
            db,
            cid,
            kind,
            payload.entity_id,
            payload.source_type,
            payload.source_id,
        )

    if payload.document_no:
        extra = " AND return_type=:return_type" if kind in {"sale_return", "purchase_return"} else ""
        duplicate = db.execute(
            text(
                f"SELECT id FROM {config['head']} WHERE company_id=:cid "
                "AND LOWER(document_no)=LOWER(:document_no) "
                "AND (CAST(:current_id AS INTEGER) IS NULL OR id<>CAST(:current_id AS INTEGER))"
                + extra
                + " LIMIT 1"
            ),
            {
                "cid": cid,
                "document_no": payload.document_no,
                "current_id": doc_id,
                "return_type": config.get("return_type"),
            },
        ).first()
        if duplicate:
            raise HTTPException(409, "Bu belge numarası daha önce kullanılmış")

    policies = get_policy_settings(db, cid)
    override_policies: set[str] = set()
    try:
        document_no = payload.document_no
        if doc_id:
            _reverse_stock(
                db,
                cid,
                kind,
                doc_id,
                policy_mode=policies.negative_stock_policy,
                override=override,
                override_policies=override_policies,
            )
            db.execute(
                text(
                    f"DELETE FROM {config['items']} "
                    f"WHERE {config['fk']}=:id AND company_id=:cid"
                ),
                {"id": doc_id, "cid": cid},
            )
            # PARTİ BORCU HAREKETLERDEN ÖNCE DÜŞÜLÜR (1B-B) — `transactions.py`
            # güncelleme/silme yollarındaki ikizin AYNI gerekçesi: aşağıdaki
            # DELETE satırları yok ettikten sonra hangi partiden ne kadar
            # düşüldüğü OKUNAMAZ. Sıra zorunludur, üslup değil.
            _parti_geri_al(
                db, cid, reference_type=config["head"], reference_id=doc_id
            )
            db.execute(
                text(
                    "DELETE FROM stock_movements WHERE company_id=:cid "
                    "AND reference_type=:rt AND reference_id=:rid"
                ),
                {"cid": cid, "rt": config["head"], "rid": doc_id},
            )
            document_no = document_no or old[0]
        else:
            document_no = document_no or next_document_no(
                db, config["head"], cid, config["prefix"]
            )

        if kind == "quote":
            values = dict(
                customer_id=payload.entity_id,
                quote_date=payload.document_date,
                valid_until=payload.valid_until,
                status=payload.status,
                note=payload.note,
                total=total,
                subtotal=subtotal,
                vat_total=vat_total,
                grand_total=total,
                discount_percent=persisted_percentage(payload.discount_percent),
                discount_amount=discount,
                company_id=cid,
                document_no=document_no,
                warehouse_id=warehouse_id,
            )
        elif kind == "sales_order":
            values = dict(
                customer_id=payload.entity_id,
                order_date=payload.document_date,
                delivery_date=payload.delivery_date,
                status=payload.status,
                note=payload.note,
                subtotal=subtotal,
                vat_total=vat_total,
                grand_total=total,
                discount_percent=persisted_percentage(payload.discount_percent),
                discount_amount=discount,
                company_id=cid,
                document_no=document_no,
                warehouse_id=warehouse_id,
            )
        elif kind == "delivery":
            values = dict(
                customer_id=payload.entity_id,
                delivery_date=payload.document_date,
                status=payload.status,
                note=payload.note,
                total=total,
                company_id=cid,
                document_no=document_no,
                warehouse_id=warehouse_id,
                source_type=payload.source_type,
                source_id=payload.source_id,
            )
        else:
            values = dict(
                return_type=config["return_type"],
                entity_id=payload.entity_id,
                return_date=payload.document_date,
                document_no=document_no,
                note=payload.note,
                total=total,
                subtotal=subtotal,
                vat_total=vat_total,
                company_id=cid,
                warehouse_id=warehouse_id,
                status=payload.status,
                source_type=payload.source_type,
                source_id=payload.source_id,
            )

        if doc_id:
            sets = ",".join(f"{key}=:{key}" for key in values)
            db.execute(
                text(
                    f"UPDATE {config['head']} SET {sets} "
                    "WHERE id=:id AND company_id=:cid"
                ),
                {**values, "id": doc_id, "cid": cid},
            )
        else:
            columns = ",".join(values)
            parameters = ",".join(":" + key for key in values)
            doc_id = int(
                db.execute(
                    text(
                        f"INSERT INTO {config['head']}({columns}) "
                        f"VALUES({parameters}) RETURNING id"
                    ),
                    values,
                ).scalar_one()
            )

        for product, item, line_subtotal, line_vat, line_total, line_discount in rows:
            if kind in {"quote", "sales_order"}:
                item_values = dict(
                    **{config["fk"]: doc_id},
                    product_id=product["id"],
                    product_name=product["name"],
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    vat_rate=item.vat_rate,
                    discount_percent=persisted_percentage(item.discount_percent),
                    discount_amount=line_discount,
                    line_subtotal=line_subtotal,
                    line_vat=line_vat,
                    line_total=line_total,
                )
            elif kind == "delivery":
                item_values = dict(
                    delivery_note_id=doc_id,
                    product_id=product["id"],
                    product_name=product["name"],
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    line_total=line_total,
                )
            else:
                item_values = dict(
                    return_id=doc_id,
                    product_id=product["id"],
                    product_name=product["name"],
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    vat_rate=item.vat_rate,
                    line_subtotal=line_subtotal,
                    line_vat=line_vat,
                    line_total=line_total,
                )
            item_values["company_id"] = cid
            columns = ",".join(item_values)
            parameters = ",".join(":" + key for key in item_values)
            db.execute(
                text(
                    f"INSERT INTO {config['items']}({columns}) VALUES({parameters})"
                ),
                item_values,
            )

            if config["stock"] and payload.status in STOCK_STATUSES:
                delta = config["stock"] * item.quantity
                allow_negative = (
                    negative_stock_allowed(policies.negative_stock_policy, override)
                    if delta < 0
                    else False
                )
                new_stock = adjust_warehouse_stock(
                    db,
                    cid,
                    warehouse_id,
                    int(product["id"]),
                    delta,
                    allow_negative=allow_negative,
                )
                if new_stock < 0 and policies.negative_stock_policy == "manager_override":
                    override_policies.add("negative_stock")
                # PARTİ TÜKETİMİ (1B-B) — `transactions.py`nin satış yolunun
                # İKİZİ ve aynı `parti_defteri` yazıcısını çağırıyor.
                # STOKTAN DÜŞEN HER TÜR: `config["stock"] < 0` olan kinds
                # `delivery` (irsaliye) ve `purchase_return` (alış iadesi).
                # Koşul `kind` ADIYLA yazılmadı; işaret ÖLÇÜLÜYOR, çünkü
                # CONFIG'e üçüncü bir çıkış türü eklendiği gün ad listesi onu
                # SESSİZCE dışarıda bırakırdı. `sale_return` (`stock=+1`)
                # parti AÇMAZ: iade edilen malın hangi partiden çıktığı bu
                # dilimde ÖLÇÜLMEDİ ve uydurmak defteri yalan söyletirdi.
                #
                # SIRA: `adjust_warehouse_stock`un ARDINDA (gerekçe ve
                # eşzamanlılık ölçümü `parti_defteri._parti_tuket` belgesinde).
                paylar: list[tuple[int | None, Decimal]] = [(None, delta)]
                suresi_gecmisler: frozenset[int] = frozenset()
                defter_bosaldi = False
                if config["stock"] < 0:
                    tuketim = _parti_tuket(
                        db,
                        cid,
                        product_id=int(product["id"]),
                        warehouse_id=warehouse_id,
                        miktar=item.quantity,
                        bugun=business_today(),
                        suresi_gecmise_izin=payload.allow_expired_lots,
                    )
                    defter_bosaldi = tuketim.defter_bosaldi
                    if tuketim.dagitim:
                        paylar = [(kimlik, -pay) for kimlik, pay in tuketim.dagitim]
                        suresi_gecmisler = tuketim.suresi_gecmis_kimlikler
                # BİR PAY = BİR HAREKET SATIRI (gerekçe satış yolunda).
                # `lot_id` bu INSERT'e 1B-B'de EKLENDİ: 1B-A sütunu yalnız
                # `transactions.py`de yazıyordu ve buradaki hareketler
                # `lot_id`si NULL kalıyordu. Kalmaya devam etseydi geri alma
                # (`_parti_geri_al`) bu belgelerin parti borcunu HİÇ göremez,
                # stok geri alınır defter alınmazdı.
                for hareket_lot, hareket_miktar in paylar:
                    db.execute(
                        text(
                            "INSERT INTO stock_movements(product_id,movement_type,quantity,"
                            "movement_date,reference_type,reference_id,note,company_id,"
                            "warehouse_id,lot_id) "
                            "VALUES(:pid,:mt,:q,:d,:rt,:rid,:n,:cid,:wid,:lot)"
                        ),
                        dict(
                            pid=product["id"],
                            mt=kind,
                            q=hareket_miktar,
                            d=payload.document_date,
                            rt=config["head"],
                            rid=doc_id,
                            n=_hareket_notu(
                                kind,
                                int(doc_id),
                                hareket_lot in suresi_gecmisler,
                                defter_bosaldi,
                            ),
                            cid=cid,
                            wid=warehouse_id,
                            lot=hareket_lot,
                        ),
                    )

        record_policy_overrides(
            db,
            company_id=cid,
            context=override,
            policy_names=override_policies,
            resource_type=config["head"],
            resource_id=doc_id,
        )
        # v1 kataloğu yalnız İADE belgesini kapsar (teklif/sipariş/irsaliye
        # sonraki sürümde). Kayıt belgeyle aynı transaction içinde alınır.
        if request is not None and config.get("return_type"):
            log_request_activity(
                db, request, cid, "return.create", "return", int(doc_id),
                (
                    f"{document_no} {RETURN_LABELS[str(config['return_type'])]} "
                    f"belgesini {'güncelledi' if old is not None else 'oluşturdu'}"
                    f" — {format_money_tr(total)}, cari: "
                    f"{_workflow_entity_name(db, cid, kind, payload.entity_id)}"
                ),
                {
                    "document_no": document_no,
                    "return_type": config["return_type"],
                    "total": total,
                    "status": payload.status,
                    "entity_id": payload.entity_id,
                },
            )
        if commit:
            db.commit()
        else:
            db.flush()
        return {
            "id": doc_id,
            "document_no": document_no,
            "total": total,
            "status": payload.status,
            "policy_overrides": sorted(override_policies),
        }
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        if "yetersiz stok" in str(exc).lower():
            raise HTTPException(409, stock_policy_message(policies.negative_stock_policy)) from exc
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("İş akışı belgesi beklenmeyen hata")
        raise HTTPException(400, WORKFLOW_FAILED_MESSAGE) from exc


@router.get("/{kind}")
def list_docs(kind: str, request: Request, db: Session = Depends(get_db)):
    config = _cfg(kind)
    cid = company_id(request)
    entity = "suppliers" if kind == "purchase_return" else "customers"
    name = "supplier_name" if kind == "purchase_return" else "customer_name"
    return_filter = (
        "AND h.return_type=:return_type" if "return_type" in config else ""
    )
    rows = db.execute(
        text(
            f"SELECT h.*,e.name {name} FROM {config['head']} h "
            f"JOIN {entity} e ON e.id=h.{config['entity']} AND e.company_id=h.company_id "
            f"WHERE h.company_id=:cid {return_filter} ORDER BY h.id DESC"
        ),
        {"cid": cid, "return_type": config.get("return_type")},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.get("/{kind}/{doc_id}")
def get_doc(kind: str, doc_id: int, request: Request, db: Session = Depends(get_db)):
    config = _cfg(kind)
    cid = company_id(request)
    entity = "suppliers" if kind == "purchase_return" else "customers"
    name_alias = "supplier_name" if kind == "purchase_return" else "customer_name"
    return_filter = (
        "AND h.return_type=:return_type" if "return_type" in config else ""
    )
    head = db.execute(
        text(
            f"SELECT h.*,e.name {name_alias} FROM {config['head']} h "
            f"JOIN {entity} e ON e.id=h.{config['entity']} AND e.company_id=h.company_id "
            f"WHERE h.id=:id AND h.company_id=:cid {return_filter}"
        ),
        {"id": doc_id, "cid": cid, "return_type": config.get("return_type")},
    ).mappings().first()
    if not head:
        raise HTTPException(404, "Belge bulunamadı")
    items = [
        dict(row)
        for row in db.execute(
            text(
                f"SELECT * FROM {config['items']} "
                f"WHERE {config['fk']}=:id AND company_id=:cid"
            ),
            {"id": doc_id, "cid": cid},
        ).mappings()
    ]
    return {"document": dict(head), "items": items}


@router.post("/{kind}")
def create(
    kind: str,
    payload: WorkflowDocumentCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    return _save_doc(
        kind,
        payload,
        db,
        company_id(request),
        override_context(request),
        request=request,
    )


@router.put("/{kind}/{doc_id}")
def update(
    kind: str,
    doc_id: int,
    payload: WorkflowDocumentCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    return _save_doc(
        kind,
        payload,
        db,
        company_id(request),
        override_context(request),
        doc_id,
        request=request,
    )


@router.delete("/{kind}/{doc_id}")
def delete_doc(kind: str, doc_id: int, request: Request, db: Session = Depends(get_db)):
    config = _cfg(kind)
    cid = company_id(request)
    owned = db.execute(
        text(f"SELECT id FROM {config['head']} WHERE id=:id AND company_id=:cid"),
        {"id": doc_id, "cid": cid},
    ).first()
    if not owned:
        raise HTTPException(404, "Belge bulunamadı")
    policies = get_policy_settings(db, cid)
    override = override_context(request)
    override_policies: set[str] = set()
    try:
        _reverse_stock(
            db,
            cid,
            kind,
            doc_id,
            policy_mode=policies.negative_stock_policy,
            override=override,
            override_policies=override_policies,
        )
        db.execute(
            text(
                f"DELETE FROM {config['items']} "
                f"WHERE {config['fk']}=:id AND company_id=:cid"
            ),
            {"id": doc_id, "cid": cid},
        )
        # PARTİ BORCU HAREKETLERDEN ÖNCE (1B-B) — güncelleme yolundaki
        # ikizin aynı gerekçesi: DELETE'ten sonra okunacak satır kalmaz.
        _parti_geri_al(
            db, cid, reference_type=config["head"], reference_id=doc_id
        )
        db.execute(
            text(
                "DELETE FROM stock_movements WHERE company_id=:cid "
                "AND reference_type=:rt AND reference_id=:rid"
            ),
            {"cid": cid, "rt": config["head"], "rid": doc_id},
        )
        result = db.execute(
            text(f"DELETE FROM {config['head']} WHERE id=:id AND company_id=:cid"),
            {"id": doc_id, "cid": cid},
        )
        record_policy_overrides(
            db,
            company_id=cid,
            context=override,
            policy_names=override_policies,
            resource_type=f"{config['head']}:delete",
            resource_id=doc_id,
        )
        db.commit()
        return {"deleted": result.rowcount > 0}
    except ValueError as exc:
        db.rollback()
        if "yetersiz stok" in str(exc).lower():
            raise HTTPException(409, stock_policy_message(policies.negative_stock_policy)) from exc
        raise HTTPException(400, str(exc)) from exc


def _conversion_source(db: Session, table: str, doc_id: int, cid: int):
    query = f"SELECT * FROM {table} WHERE id=:id AND company_id=:cid"
    if db.get_bind().dialect.name == "postgresql":
        query += " FOR UPDATE"
    return db.execute(text(query), {"id": doc_id, "cid": cid}).mappings().first()


@router.post("/quote/{doc_id}/to-order")
def quote_to_order(doc_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    quote = _conversion_source(db, "quotes", doc_id, cid)
    if not quote:
        raise HTTPException(404, "Teklif bulunamadı")
    if quote.get("converted_type") == "sales_order" and quote.get("converted_id"):
        return {"id": quote["converted_id"], "already_converted": True}
    items = db.execute(
        text(
            "SELECT * FROM quote_items WHERE company_id=:cid AND quote_id=:id "
            "ORDER BY id"
        ),
        {"id": doc_id, "cid": cid},
    ).mappings().all()
    payload = WorkflowDocumentCreate(
        entity_id=quote["customer_id"],
        document_date=quote["quote_date"],
        delivery_date=quote["valid_until"],
        warehouse_id=quote["warehouse_id"],
        status="approved",
        note=f"Teklif {quote['document_no'] or quote['id']} üzerinden",
        discount_percent=quote["discount_percent"] or 0,
        items=[
            dict(
                product_id=item["product_id"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                vat_rate=item["vat_rate"],
                discount_percent=item["discount_percent"] or 0,
            )
            for item in items
        ],
    )
    try:
        out = _save_doc(
            "sales_order", payload, db, cid, override_context(request), commit=False
        )
        db.execute(
            text(
                "UPDATE quotes SET status='converted',converted_type='sales_order',"
                "converted_id=:oid WHERE id=:id AND company_id=:cid"
            ),
            {"oid": out["id"], "id": doc_id, "cid": cid},
        )
        db.commit()
        return out
    except Exception:
        db.rollback()
        raise


@router.post("/sales_order/{doc_id}/to-sale")
def order_to_sale(doc_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    order = _conversion_source(db, "sales_orders", doc_id, cid)
    if not order:
        raise HTTPException(404, "Sipariş bulunamadı")
    if order.get("converted_type") and order.get("converted_id"):
        if order["converted_type"] == "sale":
            return {"id": order["converted_id"], "already_converted": True}
        raise HTTPException(
            409,
            f"Sipariş daha önce {order['converted_type']} belgesine dönüştürülmüş",
        )
    items = db.execute(
        text(
            "SELECT * FROM sales_order_items "
            "WHERE sales_order_id=:id AND company_id=:cid ORDER BY id"
        ),
        {"id": doc_id, "cid": cid},
    ).mappings().all()
    payload = TransactionCreate(
        entity_id=order["customer_id"],
        transaction_date=order["order_date"],
        due_date=order["delivery_date"],
        warehouse_id=order["warehouse_id"],
        status="completed",
        payment_method="credit",
        paid_amount=0,
        discount_percent=order["discount_percent"] or 0,
        note=f"Sipariş {order['document_no'] or order['id']} üzerinden",
        items=[
            dict(
                product_id=item["product_id"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                vat_rate=item["vat_rate"],
                discount_percent=item["discount_percent"] or 0,
            )
            for item in items
        ],
    )
    try:
        out = _save("sale", payload, db, cid, override_context(request), commit=False)
        db.execute(
            text(
                "UPDATE sales_orders SET status='converted',converted_type='sale',"
                "converted_id=:sid WHERE id=:id AND company_id=:cid"
            ),
            {"sid": out["id"], "id": doc_id, "cid": cid},
        )
        db.commit()
        return out
    except Exception:
        db.rollback()
        raise


@router.post("/sales_order/{doc_id}/to-delivery")
def order_to_delivery(doc_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    order = _conversion_source(db, "sales_orders", doc_id, cid)
    if not order:
        raise HTTPException(404, "Sipariş bulunamadı")
    if order.get("converted_type") and order.get("converted_id"):
        if order["converted_type"] == "delivery":
            return {"id": order["converted_id"], "already_converted": True}
        raise HTTPException(
            409,
            f"Sipariş daha önce {order['converted_type']} belgesine dönüştürülmüş",
        )
    items = db.execute(
        text(
            "SELECT * FROM sales_order_items "
            "WHERE sales_order_id=:id AND company_id=:cid ORDER BY id"
        ),
        {"id": doc_id, "cid": cid},
    ).mappings().all()
    payload = WorkflowDocumentCreate(
        entity_id=order["customer_id"],
        document_date=order["delivery_date"] or order["order_date"],
        warehouse_id=order["warehouse_id"],
        status="completed",
        note=f"Sipariş {order['document_no'] or order['id']} üzerinden",
        source_type="sales_order",
        source_id=doc_id,
        discount_percent=order["discount_percent"] or 0,
        items=[
            dict(
                product_id=item["product_id"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                vat_rate=item["vat_rate"],
                discount_percent=item["discount_percent"] or 0,
            )
            for item in items
        ],
    )
    try:
        out = _save_doc(
            "delivery", payload, db, cid, override_context(request), commit=False
        )
        db.execute(
            text(
                "UPDATE sales_orders SET status='delivered',converted_type='delivery',"
                "converted_id=:did WHERE id=:id AND company_id=:cid"
            ),
            {"did": out["id"], "id": doc_id, "cid": cid},
        )
        db.commit()
        return out
    except Exception:
        db.rollback()
        raise

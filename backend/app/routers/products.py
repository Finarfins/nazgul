from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import insert, select, text, update
from sqlalchemy.orm import Session

from ..activity_log import format_money_tr, log_request_activity
from ..units import turkce_katla
from ..business_time import business_today
from ..change_history import record_change
from ..company_policies import (
    POLICY_MANAGER_OVERRIDE,
    enforce_known_violation,
    get_policy_settings,
    negative_stock_allowed,
    override_context,
    record_policy_overrides,
    stock_policy_message,
)
from ..db import get_db
from ..inventory import (
    adjust_warehouse_stock,
    default_warehouse,
    sync_product_stock,
    warehouse_stocks,
    warehouses,
)
from ..money import HUNDRED, ZERO_MONEY, money, percentage, quantity
from ..schemas import (
    BulkPriceUpdate,
    BulkStockUpdate,
    ProductCreate,
    ProductUpdate,
    StockAdjust,
)
from ..tenancy import company_id

router = APIRouter(prefix="/products", tags=["products"])
logger = logging.getLogger("yerel_hesap")


def _product_name(db: Session, cid: int, product_id: int) -> str:
    """Aktivite özeti için ürün adı (tenant filtresi literal)."""
    row = db.execute(
        text("SELECT name FROM products WHERE id=:id AND company_id=:cid"),
        {"id": int(product_id), "cid": cid},
    ).first()
    return str(row[0]) if row and row[0] else f"#{product_id}"


BULK_PRICE_LABELS = {
    "sale_price": "satış fiyatı",
    "purchase_price": "alış fiyatı",
}
BULK_PRICE_DETAIL_LIMIT = 50
PRODUCT_FAILED_MESSAGE = "İşlem tamamlanamadı. Lütfen tekrar deneyin."
SORTS = {
    "name_asc": "LOWER(name) ASC",
    "stock_asc": "stock ASC",
    "stock_desc": "stock DESC",
    "price_asc": "sale_price ASC",
    "price_desc": "sale_price DESC",
    "purchase_asc": "purchase_price ASC",
    "purchase_desc": "purchase_price DESC",
}


def _record_negative_override(
    *,
    mode: str,
    new_stock,
    overrides: set[str],
) -> None:
    if new_stock < 0 and mode == POLICY_MANAGER_OVERRIDE:
        overrides.add("negative_stock")


def _raise_stock_error(exc: ValueError, mode: str) -> None:
    if "yetersiz stok" in str(exc).lower():
        raise HTTPException(409, stock_policy_message(mode)) from exc
    raise HTTPException(400, str(exc)) from exc


@router.get("")
def list_products(
    request: Request,
    q: str = "",
    sort: str = "name_asc",
    warehouse_id: int | None = None,
    limit: int = Query(300, le=2000),
    include_meta: bool = False,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    order = SORTS.get(sort, SORTS["name_asc"])
    fetch_limit = limit + 1 if include_meta else limit
    if warehouse_id:
        if not db.execute(
            select(warehouses.c.id).where(
                warehouses.c.id == warehouse_id,
                warehouses.c.company_id == cid,
                warehouses.c.is_active == True,
            )
        ).first():
            raise HTTPException(404, "Depo bulunamadı")
        rows = db.execute(
            text(
                f"""SELECT p.id,p.name,p.product_code,p.barcode,p.purchase_price,
                p.sale_price,p.stock,p.unit,p.vat_rate,p.category,p.location,p.oem_number,p.alternative_oem,p.brand,p.manufacturer,p.compatible_models,
                COALESCE(p.critical_stock,0) critical_stock,
                COALESCE(ws.quantity,0) warehouse_stock,
                COALESCE(ws.reserved_quantity,0) reserved_quantity,
                COALESCE(ws.quantity,0)-COALESCE(ws.reserved_quantity,0) available_stock,
                COALESCE(ws.critical_stock,p.critical_stock,0) warehouse_critical_stock
                FROM products p
                LEFT JOIN warehouse_stocks ws ON ws.product_id=p.id
                    AND ws.warehouse_id=:wid AND ws.company_id=p.company_id
                WHERE p.company_id=:cid AND (
                    LOWER(p.name) LIKE LOWER(:q)
                    OR COALESCE(p.product_code,'') LIKE :q
                    OR COALESCE(p.barcode,'') LIKE :q
                    OR COALESCE(p.oem_number,'') LIKE :q
                    OR COALESCE(p.alternative_oem,'') LIKE :q
                    OR LOWER(COALESCE(p.brand,'')) LIKE LOWER(:q)
                    OR LOWER(COALESCE(p.compatible_models,'')) LIKE LOWER(:q)
                ) ORDER BY {order} LIMIT :limit"""
            ),
            {"cid": cid, "wid": warehouse_id, "q": f"%{q}%", "limit": fetch_limit},
        ).mappings().all()
    else:
        rows = db.execute(
            text(
                f"""SELECT id,name,product_code,barcode,purchase_price,sale_price,
                stock,unit,vat_rate,category,location,oem_number,alternative_oem,brand,manufacturer,compatible_models,COALESCE(critical_stock,0) critical_stock,
                stock warehouse_stock,COALESCE(critical_stock,0) warehouse_critical_stock
                FROM products WHERE company_id=:cid AND (
                    LOWER(name) LIKE LOWER(:q)
                    OR COALESCE(product_code,'') LIKE :q
                    OR COALESCE(barcode,'') LIKE :q
                    OR COALESCE(oem_number,'') LIKE :q
                    OR COALESCE(alternative_oem,'') LIKE :q
                    OR LOWER(COALESCE(brand,'')) LIKE LOWER(:q)
                    OR LOWER(COALESCE(compatible_models,'')) LIKE LOWER(:q)
                ) ORDER BY {order} LIMIT :limit"""
            ),
            {"cid": cid, "q": f"%{q}%", "limit": fetch_limit},
        ).mappings().all()
    items = [dict(row) for row in rows[:limit]]
    if include_meta:
        return {"items": items, "has_more": len(rows) > limit}
    return items


@router.get("/stock/movements/all")
def movements(
    request: Request,
    q: str = "",
    warehouse_id: int | None = None,
    limit: int = Query(500, le=3000),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    warehouse_filter = " AND sm.warehouse_id=:wid" if warehouse_id else ""
    rows = db.execute(
        text(
            f"""SELECT sm.id,sm.product_id,p.name product_name,sm.movement_type,
            sm.quantity,sm.movement_date,sm.reference_type,sm.reference_id,sm.note,
            sm.warehouse_id,w.name warehouse_name
            FROM stock_movements sm
            JOIN products p ON p.id=sm.product_id AND p.company_id=sm.company_id
            LEFT JOIN warehouses w ON w.id=sm.warehouse_id AND w.company_id=sm.company_id
            WHERE sm.company_id=:cid AND LOWER(p.name) LIKE LOWER(:q)
            {warehouse_filter} ORDER BY sm.id DESC LIMIT :limit"""
        ),
        {"cid": cid, "q": f"%{q}%", "limit": limit, "wid": warehouse_id},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.get("/{product_id}")
def detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    row = db.execute(
        text("SELECT * FROM products WHERE id=:id AND company_id=:cid"),
        {"id": product_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Ürün bulunamadı")
    moves = db.execute(
        text(
            """SELECT sm.*,w.name warehouse_name FROM stock_movements sm
            LEFT JOIN warehouses w ON w.id=sm.warehouse_id AND w.company_id=sm.company_id
            WHERE sm.product_id=:id AND sm.company_id=:cid
            ORDER BY sm.id DESC LIMIT 200"""
        ),
        {"id": product_id, "cid": cid},
    ).mappings().all()
    stocks = db.execute(
        text(
            """SELECT ws.warehouse_id,w.name,ws.quantity,ws.critical_stock
            FROM warehouse_stocks ws
            JOIN warehouses w ON w.id=ws.warehouse_id AND w.company_id=ws.company_id
            WHERE ws.product_id=:id AND ws.company_id=:cid
            ORDER BY w.is_default DESC,w.name"""
        ),
        {"id": product_id, "cid": cid},
    ).mappings().all()
    sales_history = db.execute(
        text(
            """SELECT o.id,o.document_no,o.order_date,c.id AS customer_id,c.name customer_name,
            oi.quantity,oi.unit_price,oi.discount_percent,oi.line_total
            FROM order_items oi
            JOIN orders o ON o.id=oi.order_id AND o.company_id=:cid
            JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id
            WHERE oi.company_id=:cid AND oi.product_id=:id
            ORDER BY o.order_date DESC,o.id DESC,oi.id DESC LIMIT 50"""
        ),
        {"id": product_id, "cid": cid},
    ).mappings().all()
    purchase_history = db.execute(
        text(
            """SELECT p.id,p.document_no,p.purchase_date,s.id AS supplier_id,s.name supplier_name,
            pi.quantity,pi.unit_price,pi.discount_percent,pi.line_total
            FROM purchase_items pi
            JOIN purchases p ON p.id=pi.purchase_id AND p.company_id=:cid
            JOIN suppliers s ON s.id=p.supplier_id AND s.company_id=p.company_id
            WHERE pi.company_id=:cid AND pi.product_id=:id
            ORDER BY p.purchase_date DESC,p.id DESC,pi.id DESC LIMIT 50"""
        ),
        {"id": product_id, "cid": cid},
    ).mappings().all()
    commercial_summary = db.execute(
        text(
            """SELECT
            COALESCE((SELECT SUM(oi.quantity) FROM order_items oi JOIN orders o ON o.id=oi.order_id AND o.company_id=:cid WHERE oi.product_id=:id),0) total_sold_quantity,
            COALESCE((SELECT SUM(pi.quantity) FROM purchase_items pi JOIN purchases p ON p.id=pi.purchase_id AND p.company_id=:cid WHERE pi.product_id=:id),0) total_purchased_quantity,
            (SELECT oi.unit_price FROM order_items oi JOIN orders o ON o.id=oi.order_id AND o.company_id=:cid WHERE oi.product_id=:id ORDER BY o.order_date DESC,o.id DESC,oi.id DESC LIMIT 1) last_sale_price,
            (SELECT pi.unit_price FROM purchase_items pi JOIN purchases p ON p.id=pi.purchase_id AND p.company_id=:cid WHERE pi.product_id=:id ORDER BY p.purchase_date DESC,p.id DESC,pi.id DESC LIMIT 1) last_purchase_price"""
        ),
        {"id": product_id, "cid": cid},
    ).mappings().one()
    return {
        "product": dict(row),
        "movements": [dict(item) for item in moves],
        "warehouse_stocks": [dict(item) for item in stocks],
        "sales_history": [dict(item) for item in sales_history],
        "purchase_history": [dict(item) for item in purchase_history],
        "commercial_summary": dict(commercial_summary),
    }


@router.get("/{product_id}/warehouse-stock")
def product_warehouse_stock(
    product_id: int, request: Request, db: Session = Depends(get_db)
):
    """Where is this part in stock? One row per ACTIVE warehouse (branch).

    Powers the "Şubeler Arası Transfer" screen: the counter picks a part and
    immediately sees which branch can supply it. Unlike the ``warehouse_stocks``
    block on ``GET /products/{id}``, every active warehouse is listed even when
    it holds no stock row yet (LEFT JOIN -> quantity 0), because an empty
    warehouse is still a valid transfer *destination*. Read-only and
    tenant-scoped; the movement itself stays on ``POST /warehouses/transfers``.
    """
    cid = company_id(request)
    product = db.execute(
        text(
            "SELECT id,name,product_code,unit FROM products "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"id": product_id, "cid": cid},
    ).mappings().first()
    if not product:
        raise HTTPException(404, "Ürün bulunamadı")
    rows = db.execute(
        text(
            """SELECT w.id warehouse_id,w.name warehouse_name,w.code warehouse_code,
            w.is_default,w.branch_id,b.name branch_name,
            COALESCE(ws.quantity,0) quantity,
            COALESCE(ws.critical_stock,0) critical_stock
            FROM warehouses w
            LEFT JOIN branches b ON b.id=w.branch_id AND b.company_id=w.company_id
            LEFT JOIN warehouse_stocks ws ON ws.warehouse_id=w.id
                AND ws.company_id=w.company_id AND ws.product_id=:pid
            WHERE w.company_id=:cid AND COALESCE(w.is_active,TRUE)=TRUE
            ORDER BY COALESCE(ws.quantity,0) DESC,w.is_default DESC,LOWER(w.name)"""
        ),
        {"pid": product_id, "cid": cid},
    ).mappings().all()
    stocks = [dict(row) for row in rows]
    return {
        "product": dict(product),
        "warehouses": stocks,
        "total_quantity": sum((quantity(row["quantity"]) for row in stocks), quantity(0)),
        # Convenience for the UI: how many branches can actually supply a transfer.
        "available_warehouse_count": sum(
            1 for row in stocks if quantity(row["quantity"]) > 0
        ),
    }


@router.post("", status_code=201)
def create(payload: ProductCreate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    values = payload.model_dump()
    values["company_id"] = cid
    initial = quantity(values.pop("stock", 0))
    # --- TABAN BİRİM: OLUŞTURMADA DA YAZILIR (C2) -------------------------
    # ÖLÇÜLEN KUSUR: `ProductCreate` `ProductUpdate`ten türediği için
    # `base_unit` gövdede KABUL EDİLİYORDU, ama INSERT'in sütun listesinde
    # YOKTU — değer sessizce DÜŞÜYORDU. İstemci 201 alıyor, kart taban
    # birimsiz doğuyor ve o ürünün İLK kantar fişi `TABAN_BILDIRILMEMIS` ile
    # reddediliyordu. Hiçbir yerde kırmızı yok; yalnız bir red, sebebi bir
    # önceki istekte.
    #
    # KURALLAR PUT İLE AYNI, VE BİLEREK AYNI:
    #   * `turkce_katla` ile kanonikleşir ("kg" -> "KG"); çözücü kapalı
    #     kümeyi BÜYÜK harfle arar, katlanmamış bir "kg" BIRIM_TANIMSIZ alır.
    #   * Katlanınca BOŞA düşen dizgi ("   ") ile açık `null` AYNI şeydir ve
    #     ikisi de 422 `TABAN_BIRIM_SILINEMEZ`. "Oluştururken silinecek bir
    #     şey yok" diye bunu GEÇİRMEK, sütuna boş dizgi yazan bir yol açardı
    #     ve o ürün sonsuza kadar `BIRIM_TANIMSIZ` alırdı.
    #   * ALAN HİÇ GÖNDERİLMEZSE sütun NULL doğar. `unit`ten KOPYALANMAZ:
    #     `unit` satış/görüntü birimi, `base_unit` stoğun TUTULDUĞU birimdir
    #     ve 0066 ikisini bilerek ayırdı. Kopyalamak, hiç bildirilmemiş bir
    #     olguyu bildirilmiş göstermek olurdu (`test_kantar_fisi_sozlesme`
    #     bunu ölçüyor: taban birimsiz ürünün fişi 422 almalı).
    #
    # ETKİNLİK KAYDI YOK, VE BU PUT'TAN FARKI: PUT bir DEĞİŞİKLİKTİR ("kim,
    # ne zaman değiştirdi" sorulabilir olmalı); oluşturmada önceki bir değer
    # YOKTUR ve kaydın taşıyacağı bir fark da yoktur.
    taban_birim_yazildi = "base_unit" in payload.model_fields_set
    ham_taban = values.pop("base_unit", None)
    yeni_taban = (turkce_katla(ham_taban) or None) if ham_taban is not None else None
    if taban_birim_yazildi and yeni_taban is None:
        raise HTTPException(
            422,
            {
                "code": "TABAN_BIRIM_SILINEMEZ",
                "message": (
                    "Ürünün taban birimi boş olamaz; alanı göndermemek "
                    "sütunu NULL bırakır."
                ),
            },
        )
    values["base_unit"] = yeni_taban
    policies = get_policy_settings(db, cid)
    override = override_context(request)
    override_policies: set[str] = set()
    if initial < 0:
        overridden = enforce_known_violation(
            mode=policies.negative_stock_policy,
            context=override,
            blocked_message=stock_policy_message(policies.negative_stock_policy),
            override_message=stock_policy_message(POLICY_MANAGER_OVERRIDE),
        )
        if overridden:
            override_policies.add("negative_stock")
    try:
        result = db.execute(
            text(
                """INSERT INTO products(
                name,product_code,barcode,purchase_price,sale_price,vat_rate,
                stock,unit,category,location,oem_number,alternative_oem,brand,manufacturer,compatible_models,technical_notes,company_id,base_unit
                ) VALUES(
                :name,:product_code,:barcode,:purchase_price,:sale_price,:vat_rate,
                0,:unit,:category,:location,:oem_number,:alternative_oem,:brand,:manufacturer,:compatible_models,:technical_notes,:company_id,:base_unit) RETURNING id"""
            ),
            values,
        )
        product_id = int(result.scalar_one())
        active_warehouses = db.execute(
            select(warehouses.c.id, warehouses.c.is_default).where(
                warehouses.c.company_id == cid,
                warehouses.c.is_active == True,
            )
        ).all()
        default_id = default_warehouse(db, cid)
        for warehouse_id, _ in active_warehouses:
            db.execute(
                insert(warehouse_stocks).values(
                    company_id=cid,
                    warehouse_id=warehouse_id,
                    product_id=product_id,
                    quantity=initial if warehouse_id == default_id else 0,
                    critical_stock=0,
                )
            )
        if initial:
            db.execute(
                text(
                    """INSERT INTO stock_movements(
                    product_id,movement_type,quantity,movement_date,reference_type,
                    reference_id,note,company_id,warehouse_id
                    ) VALUES(
                    :id,'opening',:q,:movement_date,'product',:id,'Açılış stoku',:cid,:wid)"""
                ),
                {
                    "id": product_id,
                    "q": initial,
                    "cid": cid,
                    "wid": default_id,
                    "movement_date": business_today().isoformat(),
                },
            )
        sync_product_stock(db, cid, product_id)
        record_policy_overrides(
            db,
            company_id=cid,
            context=override,
            policy_names=override_policies,
            resource_type="products",
            resource_id=product_id,
        )
        db.commit()
        return {"id": product_id, "policy_overrides": sorted(override_policies)}
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Ürün işlemi beklenmeyen hata")
        raise HTTPException(400, PRODUCT_FAILED_MESSAGE) from exc


@router.put("/{product_id}")
def update_product(
    product_id: int,
    payload: ProductUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    old = db.execute(
        text("SELECT * FROM products WHERE id=:id AND company_id=:cid"),
        {"id": product_id, "cid": cid},
    ).mappings().first()
    if not old:
        raise HTTPException(404, "Ürün bulunamadı")
    policies = get_policy_settings(db, cid)
    override = override_context(request)
    override_policies: set[str] = set()
    values = payload.model_dump()
    target = quantity(values.pop("stock", old["stock"] or 0))
    # --- TABAN BİRİM: AYRI YAZILIR, HİÇBİR ŞEY YENİDEN HESAPLANMAZ ---------
    # `base_unit` genel UPDATE'in DIŞINDA çünkü alanın gönderilmemesi ile
    # `null` gönderilmesi AYRI şeylerdir: gönderilmediyse sütuna DOKUNULMAZ.
    # Döngüye katılsaydı, alanı bilmeyen eski bir istemci her kaydetmede
    # taban birimi SESSİZCE siler ve 0066'nın "geriye doldurma yok" kararını
    # tersine çevirirdi.
    #
    # DEĞER `turkce_katla` İLE KANONİKLEŞİR: birim kodu KAPALI kümede aranan
    # bir anahtardır ve `units.resolve` onu büyük harfli biçimde bekler.
    # Katlama burada yapılmazsa "kg" yazan operatörün ürünü, "KG" bekleyen
    # çözücüde BİRİM_TANIMSIZ alırdı.
    #
    # HİÇBİR ŞEY YENİDEN HESAPLANMIYOR: mevcut hareketlerin `entered_factor`ı
    # ve `base_quantity`si O GÜN neye inanıldığının kanıtıdır; taban birim
    # değişince onları yeniden çarpmak, geçmişi bugünün inancına göre
    # yazmak olurdu (`app/units.py`, sahip kararı 1).
    taban_birim_yazildi = "base_unit" in payload.model_fields_set
    ham_taban = values.pop("base_unit", None)
    # Katlanınca BOŞA düşen dizgi ("   ") None ile AYNI şeydir: aşağıdaki
    # açık-null kapısına düşer, sütuna '' YAZILMAZ (ölçüldü: `or None`
    # olmadan "   " 200 dönüyor ve sütuna boş dizgi yazıyordu).
    yeni_taban = (turkce_katla(ham_taban) or None) if ham_taban is not None else None
    # AÇIK NULL REDDEDİLİR, SESSİZCE YAZILMAZ (sahip kararı, #40'taki açık-null
    # kapısıyla AYNI ŞEKİL): alanı GÖNDERMEMEK "dokunma" demektir ve sütun
    # olduğu gibi kalır; `null` (ya da katlanınca boşa düşen bir dizgi)
    # GÖNDERMEK ise etkileşimli bir SİLME isteğidir ve bu yol taban birimi
    # silmez — silinen taban, ondan sonraki her fişi TABAN_BILDIRILMEMIS ile
    # düşürür ve bunun kaydı "kim, ne zaman" diye sorulabilir olmalı. Red
    # HER SQL'DEN ÖNCE: hiçbir sütun (stok dahil) yazılmadan. AİLE İÇİ 4xx,
    # `code` gövdede.
    if taban_birim_yazildi and yeni_taban is None:
        raise HTTPException(
            422,
            {
                "code": "TABAN_BIRIM_SILINEMEZ",
                "message": (
                    "Ürünün taban birimi bu uçtan silinemez; alanı göndermemek "
                    "mevcut değeri korur."
                ),
            },
        )
    values.update({"id": product_id, "cid": cid})
    try:
        db.execute(
            text(
                """UPDATE products SET name=:name,product_code=:product_code,
                barcode=:barcode,purchase_price=:purchase_price,sale_price=:sale_price,
                vat_rate=:vat_rate,unit=:unit,category=:category,location=:location,
                oem_number=:oem_number,alternative_oem=:alternative_oem,brand=:brand,manufacturer=:manufacturer,
                compatible_models=:compatible_models,technical_notes=:technical_notes,
                active=:active
                WHERE id=:id AND company_id=:cid"""
            ),
            values,
        )
        if taban_birim_yazildi and yeni_taban != (old["base_unit"] or None):
            db.execute(
                text(
                    "UPDATE products SET base_unit=:bu "
                    "WHERE id=:id AND company_id=:cid"
                ),
                {"bu": yeni_taban, "id": product_id, "cid": cid},
            )
            # KAYIT, DEĞİŞİKLİĞİN KENDİSİ KADAR ÖNEMLİ: taban birim bütün
            # gelecek birim çözümlerinin kaynağıdır ve sessizce değişirse
            # ondan sonraki her `entered_factor` başka bir sayı olur. Eski
            # değer de yazılıyor ki "neydi" sorusu kayıttan cevaplanabilsin.
            log_request_activity(
                db,
                request,
                cid,
                # KATALOĞA KAYITLI tip: `activity_log.ACTION_TYPES` kapalı bir
                # kümedir ve katalog dışı bir tip ValueError ile reddedilir.
                # İlk yazım burada "UPDATE" diyordu ve uç 4xx veriyordu —
                # ÖLÇÜLDÜ (`tests/test_kantar_fisi_sozlesme.py`).
                "product.base_unit_update",
                "product",
                product_id,
                f"Ürün taban birimi: {old['base_unit'] or '—'} -> "
                f"{yeni_taban or '—'}",
                {"base_unit_before": old["base_unit"], "base_unit_after": yeni_taban},
            )
        diff = target - quantity(old["stock"])
        if diff:
            warehouse_id = default_warehouse(db, cid)
            allow_negative = (
                negative_stock_allowed(policies.negative_stock_policy, override)
                if diff < 0
                else False
            )
            new_stock = adjust_warehouse_stock(
                db,
                cid,
                warehouse_id,
                product_id,
                diff,
                allow_negative=allow_negative,
            )
            _record_negative_override(
                mode=policies.negative_stock_policy,
                new_stock=new_stock,
                overrides=override_policies,
            )
            db.execute(
                text(
                    """INSERT INTO stock_movements(
                    product_id,movement_type,quantity,movement_date,reference_type,
                    reference_id,note,company_id,warehouse_id
                    ) VALUES(
                    :id,'manual',:q,:movement_date,'product',:id,'Ürün düzenleme',:cid,:wid)"""
                ),
                {
                    "id": product_id,
                    "q": diff,
                    "cid": cid,
                    "wid": warehouse_id,
                    "movement_date": business_today().isoformat(),
                },
            )
        record_policy_overrides(
            db,
            company_id=cid,
            context=override,
            policy_names=override_policies,
            resource_type="products",
            resource_id=product_id,
        )
        after = db.execute(text("SELECT * FROM products WHERE id=:id AND company_id=:cid"), {"id": product_id, "cid": cid}).mappings().first()
        record_change(db, request, company_id=cid, entity_type="product", entity_id=product_id, action="update", before=dict(old), after=dict(after))
        db.commit()
        return {"id": product_id, "policy_overrides": sorted(override_policies)}
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        _raise_stock_error(exc, policies.negative_stock_policy)
    except Exception as exc:
        db.rollback()
        logger.exception("Ürün işlemi beklenmeyen hata")
        raise HTTPException(400, PRODUCT_FAILED_MESSAGE) from exc


@router.post("/{product_id}/stock")
def adjust(
    product_id: int,
    payload: StockAdjust,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    warehouse_id = payload.warehouse_id or default_warehouse(db, cid)
    if not db.execute(
        text("SELECT id FROM products WHERE id=:id AND company_id=:cid"),
        {"id": product_id, "cid": cid},
    ).first():
        raise HTTPException(404, "Ürün bulunamadı")
    if not db.execute(
        select(warehouses.c.id).where(
            warehouses.c.id == warehouse_id,
            warehouses.c.company_id == cid,
            warehouses.c.is_active == True,
        )
    ).first():
        raise HTTPException(404, "Depo bulunamadı")
    row = db.execute(
        select(warehouse_stocks.c.quantity).where(
            warehouse_stocks.c.company_id == cid,
            warehouse_stocks.c.warehouse_id == warehouse_id,
            warehouse_stocks.c.product_id == product_id,
        )
    ).first()
    old_stock = quantity(row[0] if row else 0)
    diff = (
        payload.quantity
        if payload.mode == "add"
        else -payload.quantity
        if payload.mode == "remove"
        else quantity(payload.quantity) - old_stock
    )
    policies = get_policy_settings(db, cid)
    override = override_context(request)
    override_policies: set[str] = set()
    try:
        allow_negative = (
            negative_stock_allowed(policies.negative_stock_policy, override)
            if diff < 0
            else False
        )
        new_stock = adjust_warehouse_stock(
            db,
            cid,
            warehouse_id,
            product_id,
            diff,
            allow_negative=allow_negative,
        )
        _record_negative_override(
            mode=policies.negative_stock_policy,
            new_stock=new_stock,
            overrides=override_policies,
        )
        db.execute(
            text(
                """INSERT INTO stock_movements(
                product_id,movement_type,quantity,movement_date,reference_type,
                note,company_id,warehouse_id
                ) VALUES(:id,:m,:q,:d,'manual',:n,:cid,:wid)"""
            ),
            {
                "id": product_id,
                "m": payload.mode,
                "q": diff,
                "d": payload.movement_date,
                "n": payload.note,
                "cid": cid,
                "wid": warehouse_id,
            },
        )
        record_policy_overrides(
            db,
            company_id=cid,
            context=override,
            policy_names=override_policies,
            resource_type="products:stock",
            resource_id=product_id,
        )
        log_request_activity(
            db, request, cid, "stock.adjust", "stock", product_id,
            (
                f"{_product_name(db, cid, product_id)} ürününde stok düzeltmesi "
                f"yaptı — {old_stock} → {new_stock} ({diff:+}), depo #{warehouse_id}"
            ),
            {
                "product_id": product_id,
                "warehouse_id": warehouse_id,
                "mode": payload.mode,
                "quantity": {"old": old_stock, "new": new_stock, "delta": diff},
                "note": payload.note,
            },
        )
        db.commit()
        return {"stock": new_stock, "policy_overrides": sorted(override_policies)}
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        _raise_stock_error(exc, policies.negative_stock_policy)
    except Exception as exc:
        db.rollback()
        logger.exception("Ürün işlemi beklenmeyen hata")
        raise HTTPException(400, PRODUCT_FAILED_MESSAGE) from exc


@router.post("/bulk-price")
def bulk_price(
    payload: BulkPriceUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    if payload.field not in ("sale_price", "purchase_price") or payload.method not in (
        "percent",
        "fixed",
        "set",
    ):
        raise HTTPException(400, "Geçersiz işlem")
    # An omitted product_ids deliberately means "every product in this company".
    # An explicitly EMPTY list used to mean the same thing, because the falsy list
    # silently dropped the id filter and repriced the whole catalogue.
    if payload.product_ids is not None and not payload.product_ids:
        raise HTTPException(400, "Ürün seçilmedi")
    if payload.method == "percent" and percentage(payload.value) < -HUNDRED:
        raise HTTPException(400, "Yüzde değeri -100'ün altında olamaz")

    column = payload.field
    ids = ""
    requested: set[int] = set()
    if payload.product_ids:
        requested = {int(item) for item in payload.product_ids}
        ids = " AND id IN (" + ",".join(str(item) for item in sorted(requested)) + ")"
    rows = db.execute(
        text(f"SELECT id,{column} AS current_value FROM products WHERE company_id=:cid" + ids),
        {"cid": cid},
    ).mappings().all()
    # A foreign or unknown id previously produced 200 {"updated": 0}, which is
    # indistinguishable from a successful no-op. Fail closed on the whole batch so
    # a partially applied selection can never be mistaken for a complete one.
    if requested and len(rows) != len(requested):
        raise HTTPException(404, "Ürün bulunamadı")

    # The money math lives here, not in SQL: a bound integer made SQLite evaluate
    # ":v/100" as integer division (a +10% raise changed nothing while the response
    # still reported success) whereas PostgreSQL bound NUMERIC and divided
    # correctly. Routing every branch through app.money keeps both dialects on the
    # same Decimal result and the same ROUND_HALF_UP quantum.
    updates: list[dict] = []
    for row in rows:
        current = money(row["current_value"])
        if payload.method == "percent":
            new_value = money(current * (HUNDRED + percentage(payload.value)) / HUNDRED)
        elif payload.method == "fixed":
            new_value = money(current + money(payload.value))
        else:
            new_value = money(payload.value)
        if new_value <= ZERO_MONEY:
            raise HTTPException(400, "Fiyat sıfır veya negatif olamaz")
        updates.append({"id": int(row["id"]), "value": new_value, "cid": cid})

    if updates:
        db.execute(
            text(f"UPDATE products SET {column}=:value WHERE id=:id AND company_id=:cid"),
            updates,
        )
        # Toplu işlem tek bir aktivite satırı üretir: kaynak kimliği yoktur,
        # etkilenen ürünler ve eski/yeni değerler ``details`` içinde durur.
        method_text = {
            "percent": f"%{percentage(payload.value)} oranında",
            "fixed": f"{format_money_tr(payload.value)} tutarında",
            "set": f"{format_money_tr(payload.value)} olarak",
        }[payload.method]
        log_request_activity(
            db, request, cid, "product.bulk_price_update", "product", None,
            (
                f"{len(updates)} ürünün {BULK_PRICE_LABELS[column]} alanını toplu "
                f"güncelledi — {method_text}"
            ),
            {
                "field": column,
                "method": payload.method,
                "value": payload.value,
                "updated_count": len(updates),
                # Bütün katalog fiyatlandığında detay sınırsız büyümesin: ilk 50
                # kalem saklanır ve kırpıldığı açıkça işaretlenir.
                "truncated": len(updates) > BULK_PRICE_DETAIL_LIMIT,
                "products": [
                    {
                        "id": item["id"],
                        "old": row["current_value"],
                        "new": item["value"],
                    }
                    for item, row in list(zip(updates, rows))[:BULK_PRICE_DETAIL_LIMIT]
                ],
            },
        )
        db.commit()
    return {"updated": len(updates)}


@router.post("/bulk-stock")
def bulk_stock(
    payload: BulkStockUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    warehouse_id = payload.warehouse_id or default_warehouse(db, cid)
    if not db.execute(
        select(warehouses.c.id).where(
            warehouses.c.id == warehouse_id,
            warehouses.c.company_id == cid,
            warehouses.c.is_active == True,
        )
    ).first():
        raise HTTPException(404, "Depo bulunamadı")
    if payload.method not in ("add", "set"):
        raise HTTPException(400, "Geçersiz işlem")
    # Same two guards bulk-price grew in #157, for the same two reasons.
    # An omitted product_ids deliberately means "every product in this company".
    # An explicitly EMPTY list used to mean the same thing, because the falsy list
    # silently dropped the id filter and re-stocked the whole catalogue.
    if payload.product_ids is not None and not payload.product_ids:
        raise HTTPException(400, "Ürün seçilmedi")
    ids = ""
    requested: set[int] = set()
    if payload.product_ids:
        requested = {int(item) for item in payload.product_ids}
        ids = " AND id IN (" + ",".join(str(item) for item in sorted(requested)) + ")"
    rows = db.execute(
        text("SELECT id FROM products WHERE company_id=:cid" + ids),
        {"cid": cid},
    ).all()
    # A foreign or unknown id previously produced 200 {"updated": 0}, which is
    # indistinguishable from a successful no-op. Fail closed on the whole batch,
    # before the first movement row is written, so a partially applied selection
    # can never be mistaken for a complete one.
    if requested and len(rows) != len(requested):
        raise HTTPException(404, "Ürün bulunamadı")
    policies = get_policy_settings(db, cid)
    override = override_context(request)
    override_policies: set[str] = set()
    count = 0
    try:
        for (product_id,) in rows:
            current = db.execute(
                select(warehouse_stocks.c.quantity).where(
                    warehouse_stocks.c.company_id == cid,
                    warehouse_stocks.c.warehouse_id == warehouse_id,
                    warehouse_stocks.c.product_id == product_id,
                )
            ).scalar() or 0
            diff = (
                payload.value
                if payload.method == "add"
                else quantity(payload.value) - quantity(current)
            )
            if not diff:
                continue
            allow_negative = (
                negative_stock_allowed(policies.negative_stock_policy, override)
                if diff < 0
                else False
            )
            new_stock = adjust_warehouse_stock(
                db,
                cid,
                warehouse_id,
                product_id,
                diff,
                allow_negative=allow_negative,
            )
            _record_negative_override(
                mode=policies.negative_stock_policy,
                new_stock=new_stock,
                overrides=override_policies,
            )
            db.execute(
                text(
                    """INSERT INTO stock_movements(
                    product_id,movement_type,quantity,movement_date,reference_type,
                    note,company_id,warehouse_id
                    ) VALUES(:id,'bulk',:q,:d,'bulk',:n,:cid,:wid)"""
                ),
                {
                    "id": product_id,
                    "q": diff,
                    "d": payload.movement_date,
                    "n": payload.note,
                    "cid": cid,
                    "wid": warehouse_id,
                },
            )
            count += 1
        record_policy_overrides(
            db,
            company_id=cid,
            context=override,
            policy_names=override_policies,
            resource_type="products:bulk-stock",
            resource_id=None,
        )
        db.commit()
        return {"updated": count, "policy_overrides": sorted(override_policies)}
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        _raise_stock_error(exc, policies.negative_stock_policy)
    except Exception as exc:
        db.rollback()
        logger.exception("Ürün işlemi beklenmeyen hata")
        raise HTTPException(400, PRODUCT_FAILED_MESSAGE) from exc

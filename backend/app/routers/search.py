from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..money import money
from ..part_search import normalize_part_identifier, parse_part_search_query
from ..tenancy import company_id
from .products import list_products

router = APIRouter(prefix='/search', tags=['search'])


def _like(value: str) -> str:
    """Build a literal contains-pattern for SQL LIKE expressions.

    User-provided percent and underscore characters must not become wildcard
    operators. Backslash is escaped first because the SQL expressions below
    explicitly use it as the LIKE escape character.
    """

    escaped = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _normalized_identifier_sql(column: str, dialect: str = "sqlite") -> str:
    if dialect == "postgresql":
        return (
            f"regexp_replace(UPPER(COALESCE({column},'')),"
            "'[^A-Z0-9]+','','g')"
        )
    expression = f"UPPER(COALESCE({column},''))"
    for separator in ("-", " ", ".", "_", "/", ":", "\\"):
        expression = f"REPLACE({expression},'{separator}','')"
    return expression


def _identifier_match(row: dict, candidates: tuple[str, ...]) -> tuple[int, str] | None:
    candidate_rank = {candidate: index for index, candidate in enumerate(candidates)}
    best: tuple[int, str] | None = None
    for field, label, field_score in (
        ("oem_number", "oem", 40),
        ("product_code", "product_code", 30),
        ("barcode", "barcode", 20),
        ("alternative_oem", "alternative_oem", 10),
    ):
        normalized = normalize_part_identifier(row.get(field))
        rank = candidate_rank.get(normalized)
        if rank is None:
            continue
        score = (2000 if rank == 0 else 1000) + field_score - rank
        match_type = f"{label}_exact" if rank == 0 else f"{label}_ocr"
        if best is None or score > best[0]:
            best = (score, match_type)
    return best


@router.get('')
def global_search(
    request: Request,
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=8, ge=1, le=25),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    params = {'cid': cid, 'q': _like(q), 'limit': limit}
    results: list[dict] = []

    customers = db.execute(text("""
        SELECT id, name, phone, email
        FROM customers
        WHERE company_id=:cid AND (
          name LIKE :q ESCAPE '\\' OR COALESCE(phone,'') LIKE :q ESCAPE '\\' OR COALESCE(email,'') LIKE :q ESCAPE '\\'
        )
        ORDER BY name LIMIT :limit
    """), params).mappings().all()
    results.extend({
        'type': 'customer', 'id': row['id'], 'title': row['name'],
        'subtitle': row['phone'] or row['email'] or 'Müşteri',
        'path': f"/musteriler/{row['id']}",
    } for row in customers)

    products = db.execute(text("""
        SELECT id, name, product_code, barcode, stock, unit
        FROM products
        WHERE company_id=:cid AND COALESCE(active, TRUE)=TRUE AND (
          name LIKE :q ESCAPE '\\' OR COALESCE(product_code,'') LIKE :q ESCAPE '\\' OR COALESCE(barcode,'') LIKE :q ESCAPE '\\'
        )
        ORDER BY name LIMIT :limit
    """), params).mappings().all()
    results.extend({
        'type': 'product', 'id': row['id'], 'title': row['name'],
        'subtitle': f"{row['product_code'] or row['barcode'] or 'Ürün'} · {row['stock'] or 0} {row['unit'] or 'Adet'}",
        'path': f"/urunler?q={row['name']}",
    } for row in products)

    orders = db.execute(text("""
        SELECT o.id, o.document_no, o.order_date, o.final_total, c.name customer_name
        FROM orders o JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id
        WHERE o.company_id=:cid AND (
          COALESCE(o.document_no,'') LIKE :q ESCAPE '\\' OR c.name LIKE :q ESCAPE '\\' OR CAST(o.id AS TEXT) LIKE :q ESCAPE '\\'
        )
        ORDER BY o.id DESC LIMIT :limit
    """), params).mappings().all()
    results.extend({
        'type': 'sale', 'id': row['id'],
        'title': row['document_no'] or f"Satış #{row['id']}",
        'subtitle': f"{row['customer_name']} · {row['order_date']} · {money(row['final_total']):,.2f} ₺",
        'path': f"/satislar?q={row['document_no'] or row['id']}",
    } for row in orders)

    purchases = db.execute(text("""
        SELECT p.id, p.document_no, p.purchase_date, p.final_total, s.name supplier_name
        FROM purchases p JOIN suppliers s ON s.id=p.supplier_id AND s.company_id=p.company_id
        WHERE p.company_id=:cid AND (
          COALESCE(p.document_no,'') LIKE :q ESCAPE '\\' OR s.name LIKE :q ESCAPE '\\' OR CAST(p.id AS TEXT) LIKE :q ESCAPE '\\'
        )
        ORDER BY p.id DESC LIMIT :limit
    """), params).mappings().all()
    results.extend({
        'type': 'purchase', 'id': row['id'],
        'title': row['document_no'] or f"Alış #{row['id']}",
        'subtitle': f"{row['supplier_name']} · {row['purchase_date']} · {money(row['final_total']):,.2f} ₺",
        'path': f"/alislar?q={row['document_no'] or row['id']}",
    } for row in purchases)

    type_order = {'customer': 0, 'product': 1, 'sale': 2, 'purchase': 3}
    results.sort(key=lambda x: (type_order.get(x['type'], 99), x['title'].casefold()))
    return {'query': q, 'items': results[:limit * 3]}


@router.get("/parts")
def smart_part_search(
    request: Request,
    q: str = Query(min_length=1, max_length=120),
    sort: str = "name_asc",
    limit: int = Query(default=300, ge=1, le=2000),
    warehouse_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    parsed = parse_part_search_query(q)
    legacy_rows = list_products(
        request=request,
        q=q,
        sort=sort,
        warehouse_id=warehouse_id,
        limit=limit,
        db=db,
    )
    merged = {int(item["id"]): {**item, "score": 0, "match_type": "text"} for item in legacy_rows}

    candidates = parsed.identifier_candidates
    if candidates:
        if warehouse_id is None:
            columns = """
                id,name,product_code,barcode,purchase_price,sale_price,stock,unit,
                vat_rate,category,location,oem_number,alternative_oem,brand,
                manufacturer,compatible_models,COALESCE(critical_stock,0) critical_stock,
                stock warehouse_stock,COALESCE(critical_stock,0) warehouse_critical_stock
            """
            source = "products"
        else:
            columns = """
                p.id,p.name,p.product_code,p.barcode,p.purchase_price,p.sale_price,
                p.stock,p.unit,p.vat_rate,p.category,p.location,p.oem_number,
                p.alternative_oem,p.brand,p.manufacturer,p.compatible_models,
                COALESCE(p.critical_stock,0) critical_stock,
                COALESCE(ws.quantity,0) warehouse_stock,
                COALESCE(ws.critical_stock,p.critical_stock,0) warehouse_critical_stock
            """
            source = """products p LEFT JOIN warehouse_stocks ws
                ON ws.product_id=p.id AND ws.company_id=p.company_id
               AND ws.warehouse_id=:warehouse_id"""
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            placeholders = ",".join(
                f":candidate_{index}" for index in range(len(candidates))
            )
            params: dict[str, object] = {
                "cid": cid,
                "warehouse_id": warehouse_id,
            }
            params.update(
                {
                    f"candidate_{index}": candidate
                    for index, candidate in enumerate(candidates)
                }
            )
            selects = [
                f"""SELECT {columns} FROM {source}
                    WHERE {"p." if warehouse_id is not None else ""}company_id=:cid
                    AND COALESCE({"p." if warehouse_id is not None else ""}active,TRUE)=TRUE
                    AND {_normalized_identifier_sql(field, dialect)}
                        IN ({placeholders})"""
                for field in (
                    "oem_number",
                    "product_code",
                    "barcode",
                    "alternative_oem",
                )
            ]
            identifier_rows = db.execute(
                text(" UNION ALL ".join(selects)), params
            ).mappings().all()
        else:
            identifier_rows = db.execute(
                text(
                    f"""SELECT {columns} FROM {source}
                    WHERE {"p." if warehouse_id is not None else ""}company_id=:cid
                    AND COALESCE({"p." if warehouse_id is not None else ""}active,TRUE)=TRUE"""
                ),
                {"cid": cid, "warehouse_id": warehouse_id},
            ).mappings().all()

        for mapping in identifier_rows:
            item = dict(mapping)
            match = _identifier_match(item, candidates)
            if match is None:
                continue
            item["score"], item["match_type"] = match
            current = merged.get(int(item["id"]))
            if current is None or int(item["score"]) > int(current["score"]):
                merged[int(item["id"])] = item

    legacy_order = {int(item["id"]): index for index, item in enumerate(legacy_rows)}
    ranked = sorted(
        merged.values(),
        key=lambda item: (
            -int(item["score"]),
            legacy_order.get(int(item["id"]), len(legacy_order)),
            str(item.get("name") or "").casefold(),
            int(item["id"]),
        ),
    )
    return {
        "query": q,
        "kind": parsed.kind,
        "normalized_identifier": parsed.normalized_identifier,
        "candidate_count": len(candidates),
        "candidates_truncated": parsed.candidates_truncated,
        "items": ranked[:limit],
    }

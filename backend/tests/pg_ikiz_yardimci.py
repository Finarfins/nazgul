"""PostgreSQL ikizleri için paylaşılan yardımcılar.

İki ana sorumluluk:
1. acilisa_cek: Admin şifresini açılış durumuna (admin123 + must_change_password=True)
   getirir. PostgreSQL ikizleri paylaşılan veritabanında çalışırken şifre kirlenmesini
   önlemek için hem kurulumda hem teardown'da çağrılır.
2. parti_temizle: İkizlerin açtığı product_lots satırlarını ve onlara yabancı anahtarla
   bağlı stock_movements satırlarını güvenli sırada siler. Böylece 0073 göç koruması
   (_parti_bos_olmali) ardışık koşularda tetiklenmez.
"""
from __future__ import annotations
import uuid
from typing import Sequence

try:
    from sqlalchemy import text
except ImportError:
    text = None  # type: ignore


def kosu_eki() -> str:
    """Ardışık koşularda tekillik kısıtlarına takılmamak için benzersiz koşu son eki üretir."""
    return uuid.uuid4().hex[:8]


def _sync_sequences(eng) -> None:
    try:
        from sqlalchemy import text as _text
    except ImportError:
        return
    with eng.begin() as conn:
        for table in (
            "products",
            "customers",
            "machines",
            "invoices",
            "work_orders",
            "warehouses",
            "suppliers",
        ):
            if conn.execute(_text("SELECT to_regclass(:tbl)"), {"tbl": f"public.{table}"}).scalar() is None:
                continue
            seq = conn.execute(_text("SELECT pg_get_serial_sequence(:tbl, 'id')"), {"tbl": table}).scalar()
            if seq:
                conn.execute(
                    _text(
                        f"SELECT setval(:seq, COALESCE((SELECT max(id) FROM {table}), 1))"
                    ),
                    {"seq": seq},
                )


def acilisa_cek(engine=None, url: str | None = None) -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (admin123 + must_change_password=True) yaz.

    D2/1B-A/1B-B ikizlerinden devralındı. CI dosya başına `reset_schema` çalıştırdığı
    için (`ci.yml:609`) CI ortamında DB durumu paylaşılmaz; bu dikiş yerel/pglens
    paylaşılan veritabanı koşularını korur ve gelecekte reset_schema adımının
    kaldırılmasına karşı savunma sağlar. Tek yönlü bir çare (yalnız teardown)
    dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz, çünkü şifreyi bozan
    ÖNCEKİ dosya olabilir. Bu yüzden İKİ UÇTAN (setup + teardown) çağrılır.
    """
    import os
    try:
        from sqlalchemy import create_engine, text as _text
    except ImportError:
        return
    from app.auth import hash_password
    from app.db import engine as default_engine

    should_dispose = False
    if engine is not None:
        eng = engine
    elif url or any(k.endswith("_TEST_DATABASE_URL") or k == "DATABASE_URL" for k in os.environ):
        target_url = url or os.environ.get("DATABASE_URL")
        if not target_url:
            for k, v in os.environ.items():
                if k.endswith("_TEST_DATABASE_URL") and v:
                    target_url = v
                    break
        if target_url and target_url.startswith(("postgresql://", "postgresql+psycopg://")):
            eng = create_engine(target_url)
            should_dispose = True
        else:
            eng = default_engine
    else:
        eng = default_engine

    if eng.dialect.name != "postgresql":
        return

    try:
        with eng.begin() as conn:
            if conn.execute(_text("SELECT to_regclass('public.app_users')")).scalar() is None:
                return
            conn.execute(
                _text(
                    "UPDATE app_users SET password_hash=:h, "
                    "must_change_password=true WHERE username='admin'"
                ),
                {"h": hash_password("admin123")},
            )
        _sync_sequences(eng)
    finally:
        if should_dispose:
            eng.dispose()


_acilisa_cek = acilisa_cek


def parti_temizle(
    engine=None,
    *,
    lot_codes: Sequence[str] | None = None,
    lot_code_prefix: str | None = None,
    lot_code_prefixes: Sequence[str] | None = None,
    product_ids: Sequence[int] | None = None,
    min_product_id: int | None = None,
    company_names: Sequence[str] | None = None,
    company_ids: Sequence[int] | None = None,
    hepsini_temizle: bool = False,
) -> None:
    """PostgreSQL ikizlerinin açtığı product_lots satırlarını ve bağlı hareketlerini temizler.

    Yabancı anahtar sırası zorunludur:
    stock_movements -> fk_stock_movements_lot_same_company ile product_lots'a bağlıdır.
    Bu yüzden önce ilgili hareketler silinmeli, ardından parti silinmelidir.
    """
    from app.db import SessionLocal, engine as default_engine

    eng = engine or default_engine
    if eng.dialect.name != "postgresql":
        return

    def _temizle_islemi(execute_fn):
        if not execute_fn(text("SELECT to_regclass('public.product_lots')")).scalar():
            return

        if hepsini_temizle:
            execute_fn(text("DELETE FROM stock_movements WHERE lot_id IS NOT NULL"))
            execute_fn(text("DELETE FROM product_lots"))
            return

        filtreler = []
        params = {}

        if lot_codes is not None:
            filtreler.append("lot_code = ANY(:lot_codes)")
            params["lot_codes"] = list(lot_codes)
        if lot_code_prefix is not None:
            filtreler.append("lot_code LIKE :lot_pref")
            params["lot_pref"] = f"{lot_code_prefix}%"
        if lot_code_prefixes is not None:
            or_parts = []
            for idx, pfx in enumerate(lot_code_prefixes):
                key = f"lot_pref_{idx}"
                or_parts.append(f"lot_code LIKE :{key}")
                params[key] = f"{pfx}%"
            if or_parts:
                filtreler.append(f"({' OR '.join(or_parts)})")
        if product_ids is not None:
            filtreler.append("product_id = ANY(:pids)")
            params["pids"] = list(product_ids)
        if min_product_id is not None:
            filtreler.append("product_id >= :min_pid")
            params["min_pid"] = min_product_id
        if company_names is not None:
            filtreler.append("company_id IN (SELECT id FROM companies WHERE name = ANY(:cnames))")
            params["cnames"] = list(company_names)
        if company_ids is not None:
            filtreler.append("company_id = ANY(:cids)")
            params["cids"] = list(company_ids)

        if not filtreler:
            execute_fn(text("DELETE FROM stock_movements WHERE lot_id IS NOT NULL"))
            execute_fn(text("DELETE FROM product_lots"))
            return

        where_clause = " AND ".join(filtreler)
        hareket_sql = f"DELETE FROM stock_movements WHERE lot_id IN (SELECT id FROM product_lots WHERE {where_clause})"
        lot_sql = f"DELETE FROM product_lots WHERE {where_clause}"

        execute_fn(text(hareket_sql), params)
        execute_fn(text(lot_sql), params)

    if engine is not None:
        with engine.begin() as conn:
            _temizle_islemi(conn.execute)
    else:
        with SessionLocal() as db:
            _temizle_islemi(db.execute)
            db.commit()


def saha_islem_temizle(
    engine=None,
    *,
    operation_ids: Sequence[str] | None = None,
    prefix: str | None = "op-",
) -> None:
    """field_operations tablosundaki test kalıntılarını temizler."""
    from app.db import SessionLocal, engine as default_engine

    eng = engine or default_engine
    if eng.dialect.name != "postgresql":
        return

    def _temizle(execute_fn):
        if not execute_fn(text("SELECT to_regclass('public.field_operations')")).scalar():
            return
        if operation_ids:
            execute_fn(
                text("DELETE FROM field_operations WHERE operation_id = ANY(:oids)"),
                {"oids": list(operation_ids)},
            )
        elif prefix:
            execute_fn(
                text("DELETE FROM field_operations WHERE operation_id LIKE :pref"),
                {"pref": f"{prefix}%"},
            )

    if engine is not None:
        with engine.begin() as conn:
            _temizle(conn.execute)
    else:
        with SessionLocal() as db:
            _temizle(db.execute)
            db.commit()


def idempotency_temizle(
    engine=None,
    *,
    keys: Sequence[str] | None = None,
    prefix: str | None = "pg-",
) -> None:
    """idempotency_keys tablosundaki test kalıntılarını temizler."""
    from app.db import SessionLocal, engine as default_engine

    eng = engine or default_engine
    if eng.dialect.name != "postgresql":
        return

    def _temizle(execute_fn):
        if not execute_fn(text("SELECT to_regclass('public.idempotency_keys')")).scalar():
            return
        if keys:
            execute_fn(
                text("DELETE FROM idempotency_keys WHERE key = ANY(:keys)"),
                {"keys": list(keys)},
            )
        elif prefix:
            execute_fn(
                text("DELETE FROM idempotency_keys WHERE key LIKE :pref"),
                {"pref": f"{prefix}%"},
            )

    if engine is not None:
        with engine.begin() as conn:
            _temizle(conn.execute)
    else:
        with SessionLocal() as db:
            _temizle(db.execute)
            db.commit()


def supplier_import_temizle(
    engine=None,
    *,
    filename: str = "race.xlsx",
) -> None:
    """supplier_price_imports tablosundaki test kalıntılarını temizler."""
    from app.db import SessionLocal, engine as default_engine

    eng = engine or default_engine
    if eng.dialect.name != "postgresql":
        return

    def _temizle(execute_fn):
        if not execute_fn(text("SELECT to_regclass('public.supplier_price_imports')")).scalar():
            return
        execute_fn(
            text("DELETE FROM supplier_price_imports WHERE source_filename = :fn"),
            {"fn": filename},
        )

    if engine is not None:
        with engine.begin() as conn:
            _temizle(conn.execute)
    else:
        with SessionLocal() as db:
            _temizle(db.execute)
            db.commit()


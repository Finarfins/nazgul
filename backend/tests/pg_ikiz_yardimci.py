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
from typing import Sequence

try:
    from sqlalchemy import text
except ImportError:
    text = None  # type: ignore


def acilisa_cek(engine=None) -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (admin123 + must_change_password=True) yaz.

    D2/1B-A/1B-B ikizlerinden devralındı:
    PostgreSQL ikizleri CI'da veya yerel pglens ortamında AYNI veritabanını
    paylaşır ve her biri girişten sonra admin şifresini KENDİ sabitine çevirir.
    Tek yönlü bir çare (yalnız teardown) dosyayı iyi bir komşu yapar ama
    KENDİSİNİ korumaz, çünkü şifreyi bozan ÖNCEKİ dosya olabilir. Bu yüzden
    İKİ UÇTAN (setup + teardown) çağrılır.
    """
    from app.auth import hash_password
    from app.db import SessionLocal, engine as default_engine

    eng = engine or default_engine
    if eng.dialect.name != "postgresql":
        return

    with SessionLocal() as db:
        if db.execute(text("SELECT to_regclass('public.app_users')")).scalar() is None:
            return
        db.execute(
            text(
                "UPDATE app_users SET password_hash=:h, "
                "must_change_password=true WHERE username='admin'"
            ),
            {"h": hash_password("admin123")},
        )
        db.commit()


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

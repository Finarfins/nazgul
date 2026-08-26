from __future__ import annotations

import argparse
import os
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from sqlalchemy import create_engine, insert, select, text, update

from app.bootstrap_data import seed_bootstrap_data
from app.core_schema import (
    customers,
    document_sequences,
    income_expenses,
    order_items,
    orders,
    payments,
    products,
    purchase_items,
    purchases,
    settings_table,
    stock_movements,
    suppliers,
)
from app.finance_engine import finance_accounts, finance_transactions
from app.inventory import warehouse_stocks, warehouses
from app.config import settings
from app.runtime_migrations import run_database_migrations
from app.tenancy import branches, companies

MONEY = Decimal("0.01")
QTY = Decimal("0.0001")
SEED_KEY = "demo_seed_v1"


def money(value: Decimal | int | str | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def qty(value: Decimal | int | str | float) -> Decimal:
    return Decimal(str(value)).quantize(QTY, rounding=ROUND_HALF_UP)


def iso(day: date) -> str:
    return day.isoformat()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def inserted_id(result) -> int:
    return int(result.inserted_primary_key[0])


def build_demo(database_url: str, *, force: bool = False) -> dict[str, int]:
    settings.database_url = database_url
    engine = create_engine(database_url, pool_pre_ping=True)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)

    rng = random.Random(20260715)
    today = date.today()

    with engine.begin() as conn:
        already = conn.execute(select(settings_table.c.value).where(settings_table.c.key == SEED_KEY)).first()
        if already and not force:
            raise RuntimeError("Demo verileri daha önce kurulmuş. Tekrar eklenmedi.")
        if already and force:
            raise RuntimeError("--force mevcut ticari veriyi silmez. Temiz bir demo DB kullanın.")

        company_id = int(conn.execute(select(companies.c.id).order_by(companies.c.id).limit(1)).scalar_one())
        branch_id = int(conn.execute(select(branches.c.id).where(branches.c.company_id == company_id).limit(1)).scalar_one())
        warehouse_id = int(conn.execute(select(warehouses.c.id).where(warehouses.c.company_id == company_id).limit(1)).scalar_one())
        account_rows = conn.execute(select(finance_accounts.c.id, finance_accounts.c.account_type).where(finance_accounts.c.company_id == company_id)).all()
        account_by_type = {str(row.account_type): int(row.id) for row in account_rows}

        conn.execute(update(companies).where(companies.c.id == company_id).values(name="Sungur Tarım Demo"))

        customer_names = [
            "Akıncı Tarım", "Bereket Çiftliği", "Çorlu Ziraat", "Demirbaş Tarım", "Ekin Kardeşler",
            "Ergene Tarım", "Güneş Agro", "Hasat Trakya", "Işıklar Çiftliği", "Kardeşler Biçerdöver",
            "Marmara Tarım", "Özbaşak Ziraat", "Pınar Tarım", "Sarılar Çiftliği", "Şahin Tarım",
            "Tekirdağ Hasat", "Trakya Biçerdöver", "Umut Tarım", "Yıldız Ziraat", "Yeni Nesil Çiftlik",
            "Kaya Tarım Makine", "Başak Agro", "Çınar Çiftliği", "Meriç Tarım", "Vatan Ziraat",
        ]
        customer_ids: list[int] = []
        for idx, name in enumerate(customer_names, start=1):
            customer_ids.append(inserted_id(conn.execute(insert(customers).values(
                name=name,
                owner_name=f"Yetkili {idx}",
                phone=f"+90 5{30 + idx % 20:02d} 55{idx:02d} {1000 + idx:04d}",
                email=f"demo.musteri{idx}@example.com",
                address=f"Tekirdağ / {['Çorlu','Muratlı','Hayrabolu','Saray','Malkara'][idx % 5]}",
                tax_number=f"100000{idx:04d}",
                opening_balance=money(rng.choice([0, 2500, 7500, 12500, -1500])),
                risk_limit=money(rng.choice([50000, 100000, 150000, 250000, 400000])),
                payment_term_days=rng.choice([0, 15, 30, 45, 60]),
                notes="Demo müşteri kaydı",
                is_active=True,
                company_id=company_id,
            ))))

        supplier_names = [
            "Trakya Rulman", "Marmara Kayış", "CNH Parça Tedarik", "Anadolu Filtre",
            "Bereket Zincir", "Agro Hidrolik", "Teknik Hırdavat", "Avrupa Tarım Parça",
            "Rubena Bölge Bayi", "Sanayi Keçe Rulman",
        ]
        supplier_ids: list[int] = []
        for idx, name in enumerate(supplier_names, start=1):
            supplier_ids.append(inserted_id(conn.execute(insert(suppliers).values(
                name=name,
                owner_name=f"Tedarik Yetkilisi {idx}",
                phone=f"+90 212 44{idx:02d} {2000 + idx:04d}",
                email=f"demo.tedarikci{idx}@example.com",
                address="İstanbul / Türkiye",
                tax_number=f"200000{idx:04d}",
                opening_balance=money(rng.choice([0, 10000, 25000, 50000])),
                risk_limit=money(0),
                payment_term_days=rng.choice([15, 30, 45, 60]),
                notes="Demo tedarikçi kaydı",
                is_active=True,
                company_id=company_id,
            ))))

        product_templates = [
            ("Biçerdöver V Kayışı 17x1425", "KYS-171425", "869100000001", "Kayış", "A-01"),
            ("Batör Rulmanı 6208 2RS", "RLM-6208", "869100000002", "Rulman", "B-01"),
            ("Elevatör Zinciri 083", "ZNC-083", "869100000003", "Zincir", "C-01"),
            ("Hidrolik Yağ Filtresi", "FLT-HYD-01", "869100000004", "Filtre", "D-01"),
            ("Motor Yağ Filtresi", "FLT-MTR-01", "869100000005", "Filtre", "D-02"),
            ("Mazot Filtresi", "FLT-MZT-01", "869100000006", "Filtre", "D-03"),
            ("Hava Filtresi İç", "FLT-HVI-01", "869100000007", "Filtre", "D-04"),
            ("Hava Filtresi Dış", "FLT-HVD-01", "869100000008", "Filtre", "D-05"),
            ("Keçe 45x65x10", "KCE-456510", "869100000009", "Keçe", "E-01"),
            ("Gergi Rulmanı", "RLM-GRG-01", "869100000010", "Rulman", "B-02"),
            ("Varyatör Kayışı", "KYS-VRY-01", "869100000011", "Kayış", "A-02"),
            ("Sap Elevatör Paleti", "PLT-ELV-01", "869100000012", "Palet", "C-02"),
            ("Biçak Lama Takımı", "BCK-LMA-01", "869100000013", "Biçak", "F-01"),
            ("Helezon Yatağı", "YTK-HLZ-01", "869100000014", "Yatak", "F-02"),
            ("Konik Rulman 30208", "RLM-30208", "869100000015", "Rulman", "B-03"),
        ]
        brands = ["CNH", "Rubena", "SKF", "FAG", "Optibelt", "Gates"]
        product_ids: list[int] = []
        product_meta: dict[int, dict[str, Decimal | str]] = {}
        for idx in range(1, 76):
            base = product_templates[(idx - 1) % len(product_templates)]
            suffix = (idx - 1) // len(product_templates) + 1
            name = base[0] if suffix == 1 else f"{base[0]} Varyant {suffix}"
            purchase_price = money(180 + idx * 37 + rng.randint(0, 400))
            sale_price = money(purchase_price * Decimal(str(rng.choice([1.28, 1.35, 1.42, 1.55]))))
            initial_stock = qty(rng.randint(8, 75))
            product_id = inserted_id(conn.execute(insert(products).values(
                name=name,
                purchase_price=purchase_price,
                sale_price=sale_price,
                category=base[3],
                country=rng.choice(["Türkiye", "Almanya", "İtalya", "Çekya"]),
                vat_rate=money(20),
                product_code=f"{base[1]}-{suffix:02d}",
                barcode=f"{int(base[2]) + idx:013d}",
                stock=initial_stock,
                location=f"{base[4]}-{suffix:02d}",
                oem_number=f"CNH-{84000000 + idx}",
                alternative_oem=f"ALT-{84000000 + idx}, {base[1]}-MUADIL-{suffix:02d}",
                brand=brands[(idx - 1) % len(brands)],
                manufacturer=brands[(idx + 1) % len(brands)],
                compatible_models=rng.choice(["CS540, CS640", "TC56, TC5070", "CX8080, CX6090", "CR9080, CR9090"]),
                technical_notes=f"Demo teknik notu: {base[0]} için montaj öncesi ölçü ve bağlantı kontrolü yapın.",
                supplier_id=rng.choice(supplier_ids),
                unit="Adet",
                price_per="Adet",
                active=True,
                critical_stock=qty(rng.choice([3, 5, 8, 10])),
                minimum_stock=qty(rng.choice([5, 10, 15])),
                company_id=company_id,
            )))
            product_ids.append(product_id)
            product_meta[product_id] = {"name": name, "purchase_price": purchase_price, "sale_price": sale_price, "stock": initial_stock}
            conn.execute(insert(warehouse_stocks).values(
                company_id=company_id,
                warehouse_id=warehouse_id,
                product_id=product_id,
                quantity=initial_stock,
                critical_stock=qty(rng.choice([3, 5, 8, 10])),
            ))

        # Alışlar: stokları artırır ve tedarikçi ödeme geçmişi oluşturur.
        purchase_count = 0
        for n in range(1, 26):
            supplier_id = rng.choice(supplier_ids)
            pdate = today - timedelta(days=rng.randint(35, 210))
            chosen = rng.sample(product_ids, rng.randint(2, 5))
            lines = []
            subtotal = Decimal("0")
            for pid in chosen:
                q = qty(rng.randint(3, 18))
                unit_price = money(product_meta[pid]["purchase_price"])
                line_subtotal = money(q * unit_price)
                line_vat = money(line_subtotal * Decimal("0.20"))
                line_total = money(line_subtotal + line_vat)
                lines.append((pid, q, unit_price, line_subtotal, line_vat, line_total))
                subtotal += line_subtotal
            subtotal = money(subtotal)
            vat_total = money(subtotal * Decimal("0.20"))
            total = money(subtotal + vat_total)
            paid = money(total * Decimal(str(rng.choice([0, 0.25, 0.5, 1]))))
            purchase_id = inserted_id(conn.execute(insert(purchases).values(
                supplier_id=supplier_id,
                purchase_date=iso(pdate), subtotal=subtotal, vat_total=vat_total,
                grand_total=total, discount_percent=money(0), discount_amount=money(0),
                final_total=total, document_no=f"ALS-DEMO-{n:04d}",
                due_date=iso(pdate + timedelta(days=30)), note="Demo alış belgesi",
                company_id=company_id, branch_id=branch_id, warehouse_id=warehouse_id,
                status="completed", payment_method="bank_transfer" if paid else "credit", paid_amount=paid,
            )))
            purchase_count += 1
            for pid, q, unit_price, line_subtotal, line_vat, line_total in lines:
                conn.execute(insert(purchase_items).values(
                    # Satır KENDİ kiracısını taşır (göç 20260812_0058).
                    company_id=company_id,
                    purchase_id=purchase_id, product_id=pid, product_name=product_meta[pid]["name"],
                    quantity=q, unit_price=unit_price, vat_rate=money(20), line_subtotal=line_subtotal,
                    line_vat=line_vat, line_total=line_total, discount_percent=money(0), discount_amount=money(0),
                ))
                conn.execute(update(products).where(products.c.id == pid).values(stock=products.c.stock + q))
                conn.execute(update(warehouse_stocks).where(
                    warehouse_stocks.c.company_id == company_id,
                    warehouse_stocks.c.warehouse_id == warehouse_id,
                    warehouse_stocks.c.product_id == pid,
                ).values(quantity=warehouse_stocks.c.quantity + q))
                conn.execute(insert(stock_movements).values(
                    product_id=pid, movement_type="purchase", quantity=q, movement_date=iso(pdate),
                    reference_type="purchase", reference_id=purchase_id, note="Demo alış stok girişi",
                    company_id=company_id, warehouse_id=warehouse_id,
                ))
            if paid > 0:
                payment_id = inserted_id(conn.execute(insert(payments).values(
                    entity_type="supplier", entity_id=supplier_id, amount=paid, payment_date=iso(pdate),
                    note=f"{n}. demo alış ödemesi", company_id=company_id, branch_id=branch_id,
                    payment_method="bank_transfer", reference_type="purchase", reference_id=purchase_id,
                    account_id=account_by_type.get("bank"),
                )))
                txid = inserted_id(conn.execute(insert(finance_transactions).values(
                    company_id=company_id, account_id=account_by_type["bank"], txn_date=iso(pdate),
                    direction="out", amount=paid, category="payment", payment_method="bank_transfer",
                    description="Demo tedarikçi ödemesi", reference_type="payment", reference_id=payment_id,
                    transfer_group=None, created_at=now_utc(),
                )))
                conn.execute(update(payments).where(payments.c.id == payment_id).values(financial_transaction_id=txid))

        # Satışlar: farklı ödeme türleri, kısmi ödeme ve veresiye örnekleri.
        order_count = 0
        for n in range(1, 61):
            customer_id = rng.choice(customer_ids)
            odate = today - timedelta(days=rng.randint(0, 180))
            available = [pid for pid in product_ids if Decimal(str(product_meta[pid]["stock"])) > 0]
            chosen = rng.sample(available, rng.randint(1, 4))
            lines = []
            subtotal = Decimal("0")
            for pid in chosen:
                current_db_stock = Decimal(str(conn.execute(select(products.c.stock).where(products.c.id == pid)).scalar_one()))
                max_qty = max(1, min(5, int(current_db_stock)))
                q = qty(rng.randint(1, max_qty))
                unit_price = money(product_meta[pid]["sale_price"])
                discount_pct = money(rng.choice([0, 0, 0, 5, 10]))
                raw = money(q * unit_price)
                discount_amount = money(raw * discount_pct / Decimal("100"))
                line_subtotal = money(raw - discount_amount)
                line_vat = money(line_subtotal * Decimal("0.20"))
                line_total = money(line_subtotal + line_vat)
                lines.append((pid, q, unit_price, discount_pct, discount_amount, line_subtotal, line_vat, line_total))
                subtotal += line_subtotal
            subtotal = money(subtotal)
            vat_total = money(subtotal * Decimal("0.20"))
            total = money(subtotal + vat_total)
            payment_pattern = rng.choice(["cash", "card", "bank_transfer", "credit", "split"])
            if payment_pattern == "credit":
                paid = money(0)
            elif payment_pattern == "split":
                paid = money(total * Decimal("0.70"))
            else:
                paid = total
            order_id = inserted_id(conn.execute(insert(orders).values(
                customer_id=customer_id, order_date=iso(odate), subtotal=subtotal, vat_total=vat_total,
                grand_total=total, discount_percent=money(0), discount_amount=money(0), final_total=total,
                document_no=f"SAT-DEMO-{n:04d}", due_date=iso(odate + timedelta(days=30)),
                note="Demo satış belgesi", company_id=company_id, branch_id=branch_id, warehouse_id=warehouse_id,
                status="completed", payment_method=payment_pattern if payment_pattern != "split" else "cash",
                paid_amount=paid,
            )))
            order_count += 1
            for pid, q, unit_price, discount_pct, discount_amount, line_subtotal, line_vat, line_total in lines:
                conn.execute(insert(order_items).values(
                    # Satır KENDİ kiracısını taşır (göç 20260812_0058).
                    company_id=company_id,
                    order_id=order_id, product_id=pid, product_name=product_meta[pid]["name"], quantity=q,
                    unit_price=unit_price, vat_rate=money(20), line_subtotal=line_subtotal, line_vat=line_vat,
                    line_total=line_total, discount_percent=discount_pct, discount_amount=discount_amount,
                ))
                conn.execute(update(products).where(products.c.id == pid).values(stock=products.c.stock - q))
                conn.execute(update(warehouse_stocks).where(
                    warehouse_stocks.c.company_id == company_id,
                    warehouse_stocks.c.warehouse_id == warehouse_id,
                    warehouse_stocks.c.product_id == pid,
                ).values(quantity=warehouse_stocks.c.quantity - q))
                conn.execute(insert(stock_movements).values(
                    product_id=pid, movement_type="sale", quantity=-q, movement_date=iso(odate),
                    reference_type="order", reference_id=order_id, note="Demo satış stok çıkışı",
                    company_id=company_id, warehouse_id=warehouse_id,
                ))

            if paid > 0:
                allocations: list[tuple[str, Decimal]]
                if payment_pattern == "split":
                    first = money(paid * Decimal("0.55"))
                    allocations = [("cash", first), ("card", money(paid - first))]
                else:
                    allocations = [(payment_pattern, paid)]
                for method, amount in allocations:
                    account_type = {"cash": "cash", "card": "pos", "bank_transfer": "bank"}[method]
                    payment_id = inserted_id(conn.execute(insert(payments).values(
                        entity_type="customer", entity_id=customer_id, amount=amount, payment_date=iso(odate),
                        note=f"SAT-DEMO-{n:04d} tahsilatı", company_id=company_id, branch_id=branch_id,
                        payment_method=method, reference_type="order", reference_id=order_id,
                        account_id=account_by_type[account_type],
                    )))
                    txid = inserted_id(conn.execute(insert(finance_transactions).values(
                        company_id=company_id, account_id=account_by_type[account_type], txn_date=iso(odate),
                        direction="in", amount=amount, category="collection", payment_method=method,
                        description="Demo müşteri tahsilatı", reference_type="payment", reference_id=payment_id,
                        transfer_group=None, created_at=now_utc(),
                    )))
                    conn.execute(update(payments).where(payments.c.id == payment_id).values(financial_transaction_id=txid))

        # Bağımsız tahsilat ve tedarikçi ödemeleri.
        for n in range(1, 16):
            pdate = today - timedelta(days=rng.randint(0, 90))
            customer_id = rng.choice(customer_ids)
            amount = money(rng.randint(2500, 35000))
            method = rng.choice(["cash", "bank_transfer"])
            account_type = "cash" if method == "cash" else "bank"
            payment_id = inserted_id(conn.execute(insert(payments).values(
                entity_type="customer", entity_id=customer_id, amount=amount, payment_date=iso(pdate),
                note="Demo bağımsız tahsilat", company_id=company_id, branch_id=branch_id,
                payment_method=method, account_id=account_by_type[account_type],
            )))
            txid = inserted_id(conn.execute(insert(finance_transactions).values(
                company_id=company_id, account_id=account_by_type[account_type], txn_date=iso(pdate),
                direction="in", amount=amount, category="collection", payment_method=method,
                description="Demo bağımsız tahsilat", reference_type="payment", reference_id=payment_id,
                created_at=now_utc(),
            )))
            conn.execute(update(payments).where(payments.c.id == payment_id).values(financial_transaction_id=txid))

        for n in range(1, 11):
            pdate = today - timedelta(days=rng.randint(0, 90))
            supplier_id = rng.choice(supplier_ids)
            amount = money(rng.randint(4000, 50000))
            payment_id = inserted_id(conn.execute(insert(payments).values(
                entity_type="supplier", entity_id=supplier_id, amount=amount, payment_date=iso(pdate),
                note="Demo bağımsız tedarikçi ödemesi", company_id=company_id, branch_id=branch_id,
                payment_method="bank_transfer", account_id=account_by_type["bank"],
            )))
            txid = inserted_id(conn.execute(insert(finance_transactions).values(
                company_id=company_id, account_id=account_by_type["bank"], txn_date=iso(pdate),
                direction="out", amount=amount, category="payment", payment_method="bank_transfer",
                description="Demo bağımsız tedarikçi ödemesi", reference_type="payment", reference_id=payment_id,
                created_at=now_utc(),
            )))
            conn.execute(update(payments).where(payments.c.id == payment_id).values(financial_transaction_id=txid))

        categories_in = ["Servis İşçiliği", "Kargo Geliri", "Diğer Gelir"]
        categories_out = ["Elektrik", "Akaryakıt", "Kira", "Kargo", "Ofis Gideri", "Yemek"]
        income_count = 0
        expense_count = 0
        for n in range(1, 31):
            txn_type = "income" if n <= 10 else "expense"
            category = rng.choice(categories_in if txn_type == "income" else categories_out)
            amount = money(rng.randint(750, 18000))
            txn_date = iso(today - timedelta(days=rng.randint(0, 120)))
            conn.execute(insert(income_expenses).values(
                txn_date=txn_date, txn_type=txn_type, category=category, amount=amount,
                account_id=account_by_type["cash"], note=f"Demo {category.lower()} kaydı", company_id=company_id,
            ))
            conn.execute(insert(finance_transactions).values(
                company_id=company_id, account_id=account_by_type["cash"], txn_date=txn_date,
                direction="in" if txn_type == "income" else "out", amount=amount,
                category=category, payment_method="cash", description=f"Demo {category}",
                reference_type="income_expense", reference_id=None, created_at=now_utc(),
            ))
            if txn_type == "income": income_count += 1
            else: expense_count += 1

        for key, current in (("sale", order_count), ("purchase", purchase_count)):
            conn.execute(insert(document_sequences).values(
                company_id=company_id, sequence_key=key, current_value=current
            ).prefix_with("OR REPLACE" if engine.dialect.name == "sqlite" else ""))

        conn.execute(insert(settings_table).values(key=SEED_KEY, value=now_utc().isoformat()))

        counts = {
            "customers": len(customer_ids),
            "suppliers": len(supplier_ids),
            "products": len(product_ids),
            "sales": order_count,
            "purchases": purchase_count,
            "income": income_count,
            "expenses": expense_count,
        }
    engine.dispose()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Yerel Hesap Pro X deneme verilerini kurar.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--database", type=Path, help="SQLite dosya yolu")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.database_url:
        database_url = args.database_url
    else:
        target = (args.database or Path(__file__).resolve().parent / "demo_veriler.db").resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{target.as_posix()}"

    counts = build_demo(database_url, force=args.force)
    print("Demo verileri hazır:")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print(f"Veritabanı: {database_url}")
    print("Giriş: admin / admin123 (ilk girişte parola değişikliği gerekir)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

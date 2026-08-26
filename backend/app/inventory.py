from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import utcnow
from .core_schema import products
from .tenancy import branches, companies

metadata = MetaData()
QUANTITY = Numeric(18, 4)
ZERO = Decimal("0")

warehouses = Table(
    "warehouses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(160), nullable=False),
    Column("location", String(300)),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("company_id", Integer, nullable=False, default=1, index=True),
    Column("branch_id", Integer, nullable=True, index=True),
    Column("code", String(40)),
    Column("is_default", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=True),
    extend_existing=True,
)
warehouse_stocks = Table(
    "warehouse_stocks",
    metadata,
    Column("warehouse_id", Integer, primary_key=True),
    Column("product_id", Integer, primary_key=True),
    Column("quantity", QUANTITY, nullable=False, default=0),
    Column("company_id", Integer, nullable=False, default=1, index=True),
    Column("critical_stock", QUANTITY, nullable=False, default=0),
    # Reserved-but-not-yet-issued stock (Servis v2 FAZ-3). ``quantity`` keeps its
    # v1 meaning — what is physically on hand — and availability is derived:
    # available = quantity - reserved_quantity.
    #
    # server_default matches what the migration adds to existing databases, so a
    # freshly created schema and an upgraded one are identical — otherwise the
    # baseline would produce a NOT NULL column with no SQL-level default and any
    # statement that omits it (a raw INSERT, a restored dump) would fail.
    Column("reserved_quantity", QUANTITY, nullable=False, default=0, server_default="0"),
    extend_existing=True,
)
stock_transfers = Table(
    "stock_transfers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False, index=True),
    Column("transfer_date", String(30), nullable=False),
    Column("source_warehouse_id", Integer, nullable=False),
    Column("target_warehouse_id", Integer, nullable=False),
    Column("note", String(500)),
    Column("status", String(30), nullable=False, default="completed"),
    Column("created_by", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # The composite target the child's foreign key points at. ``id`` is already
    # unique on its own; this pair exists so the child can name company_id in
    # the same reference and make a cross-tenant link unrepresentable.
    UniqueConstraint("company_id", "id", name="uq_stock_transfers_company_id"),
)
stock_transfer_items = Table(
    "stock_transfer_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # The line's own tenant. Denormalized from the parent on purpose: without it
    # the row's isolation depends on every query remembering to join and filter
    # stock_transfers, which is a convention, not a guarantee.
    Column("company_id", Integer, nullable=False),
    Column("transfer_id", Integer, nullable=False, index=True),
    Column("product_id", Integer, nullable=False),
    Column("quantity", QUANTITY, nullable=False),
    # The guarantee itself: the pair must exist in the parent, so a line can
    # never reference a transfer belonging to another company. Enforced by the
    # database on both dialects (PRAGMA foreign_keys=ON is set in db.py).
    ForeignKeyConstraint(
        ["company_id", "transfer_id"],
        ["stock_transfers.company_id", "stock_transfers.id"],
        name="fk_stock_transfer_items_transfer_same_company",
    ),
    Index("ix_stock_transfer_items_company_transfer", "company_id", "transfer_id"),
)


def _decimal(value: object, *, scale: str = "0.0001") -> Decimal:
    """Convert external numeric values without passing through binary float math."""
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Geçersiz miktar") from exc
    return result.quantize(Decimal(scale))


def _add(conn, table: str, column: str, sql: str) -> None:
    columns = {item["name"] for item in inspect(conn).get_columns(table)}
    if column not in columns:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql}"))


def ensure_company_default_warehouse(
    db, company_id: int, branch_id: int | None = None
) -> int:
    """Ensure a newly created or legacy company is immediately operational."""
    current = db.execute(
        select(warehouses.c.id)
        .where(
            warehouses.c.company_id == company_id,
            warehouses.c.is_active.is_(True),
        )
        .order_by(warehouses.c.id)
    ).first()
    default = db.execute(
        select(warehouses.c.id).where(
            warehouses.c.company_id == company_id,
            warehouses.c.is_active.is_(True),
            warehouses.c.is_default.is_(True),
        )
    ).first()

    if not current:
        if branch_id is None:
            branch = db.execute(
                select(branches.c.id)
                .where(branches.c.company_id == company_id)
                .order_by(branches.c.id)
            ).first()
            branch_id = int(branch[0]) if branch else None

        # Ad çakışması ŞİRKET İÇİNDE aranır. Buradaki eski yorum "name üzerinde
        # legacy bir KÜRESEL UNIQUE var" diyordu ve sorgu bu yüzden kapsamsız
        # kalmıştı; göç etmiş şemada ÖLÇÜLDÜ (2026-08-12) ve öyle bir kısıt YOK:
        # PostgreSQL 16'da warehouses üzerinde yalnız `warehouses_pkey` (id) ile
        # `ck_warehouses_type` var, `name` indeksli bile değil; migrate edilmiş
        # SQLite'ta da aynı. Kapsamsız hâli başka kiracının depo adını görüp
        # "Merkez Depo (2)" üretiyordu — kiracı sınırını gereksiz yere geçen bir
        # okumaydı.
        base_name = "Merkez Depo"
        warehouse_name = base_name
        suffix = company_id
        while db.execute(
            select(warehouses.c.id).where(
                warehouses.c.company_id == company_id,
                warehouses.c.name == warehouse_name,
            )
        ).first():
            warehouse_name = f"{base_name} ({suffix})"
            suffix += 1

        warehouse_id = int(
            db.execute(
                insert(warehouses)
                .values(
                    name=warehouse_name,
                    location=None,
                    is_active=True,
                    company_id=company_id,
                    branch_id=branch_id,
                    code="MRK",
                    is_default=True,
                    created_at=utcnow(),
                )
                .returning(warehouses.c.id)
            ).scalar_one()
        )
    else:
        warehouse_id = int(default[0] if default else current[0])
        if not default:
            db.execute(
                update(warehouses)
                .where(warehouses.c.company_id == company_id)
                .values(is_default=False)
            )
            db.execute(
                update(warehouses)
                .where(
                    warehouses.c.id == warehouse_id,
                    warehouses.c.company_id == company_id,
                )
                .values(is_default=True)
            )

    # A company created while the application is running must receive stock rows
    # immediately; waiting for the next process restart leaves its UI unusable.
    product_rows = db.execute(
        select(
            products.c.id,
            products.c.stock,
            products.c.critical_stock,
        ).where(products.c.company_id == company_id)
    ).all()
    for product_id, quantity, critical_stock in product_rows:
        exists = db.execute(
            select(warehouse_stocks.c.product_id).where(
                warehouse_stocks.c.company_id == company_id,
                warehouse_stocks.c.warehouse_id == warehouse_id,
                warehouse_stocks.c.product_id == product_id,
            )
        ).first()
        if not exists:
            db.execute(
                insert(warehouse_stocks).values(
                    company_id=company_id,
                    warehouse_id=warehouse_id,
                    product_id=product_id,
                    quantity=_decimal(quantity),
                    critical_stock=_decimal(critical_stock),
                )
            )
    return warehouse_id


def initialize_inventory(engine: Engine) -> None:
    metadata.create_all(engine)
    with engine.begin() as conn:
        inspector = inspect(conn)
        if inspector.has_table("warehouses"):
            for column, sql in (
                ("company_id", "INTEGER DEFAULT 1"),
                ("branch_id", "INTEGER"),
                ("code", "TEXT"),
                ("is_default", "INTEGER DEFAULT 0"),
                ("created_at", "TEXT"),
            ):
                _add(conn, "warehouses", column, sql)
            conn.execute(text("UPDATE warehouses SET company_id=1 WHERE company_id IS NULL"))
        if inspector.has_table("warehouse_stocks"):
            for column, sql in (
                ("company_id", "INTEGER DEFAULT 1"),
                ("critical_stock", "NUMERIC(18,4) DEFAULT 0"),
            ):
                _add(conn, "warehouse_stocks", column, sql)
            conn.execute(text("UPDATE warehouse_stocks SET company_id=1 WHERE company_id IS NULL"))
        for table in ("orders", "purchases"):
            if inspector.has_table(table):
                _add(conn, table, "warehouse_id", "INTEGER")
                _add(conn, table, "branch_id", "INTEGER")
        if inspector.has_table("stock_movements"):
            _add(conn, "stock_movements", "warehouse_id", "INTEGER")

        for (company_id,) in conn.execute(select(companies.c.id)).all():
            ensure_company_default_warehouse(conn, int(company_id))


def default_warehouse(db: Session, company_id: int) -> int:
    row = db.execute(
        select(warehouses.c.id)
        .where(
            warehouses.c.company_id == company_id,
            warehouses.c.is_active.is_(True),
        )
        .order_by(warehouses.c.is_default.desc(), warehouses.c.id)
    ).first()
    if not row:
        raise RuntimeError("Aktif depo bulunamadı")
    return int(row[0])


def sync_product_stock(db: Session, company_id: int, product_id: int) -> Decimal:
    # Keep the denormalized products.stock summary in sync in one SQL
    # statement. A separate SELECT followed by UPDATE can drift under READ
    # COMMITTED when two warehouses for the same product are changed at once.
    stock_total = (
        select(func.coalesce(func.sum(warehouse_stocks.c.quantity), ZERO))
        .where(
            warehouse_stocks.c.company_id == company_id,
            warehouse_stocks.c.product_id == product_id,
        )
        .scalar_subquery()
    )
    updated = db.execute(
        update(products)
        .where(products.c.id == product_id, products.c.company_id == company_id)
        .values(stock=stock_total)
        .returning(products.c.stock)
    ).scalar_one_or_none()
    if updated is None:
        raise ValueError("Ürün bulunamadı veya firmaya ait değil")
    return _decimal(updated)


def _update_existing_stock(
    db: Session,
    company_id: int,
    warehouse_id: int,
    product_id: int,
    difference: Decimal,
    allow_negative: bool,
) -> Decimal | None:
    quantity_expression = warehouse_stocks.c.quantity + difference
    statement = (
        update(warehouse_stocks)
        .where(
            warehouse_stocks.c.company_id == company_id,
            warehouse_stocks.c.warehouse_id == warehouse_id,
            warehouse_stocks.c.product_id == product_id,
        )
        .values(quantity=quantity_expression)
    )
    if not allow_negative:
        statement = statement.where(quantity_expression >= ZERO)
    return db.execute(statement.returning(warehouse_stocks.c.quantity)).scalar_one_or_none()


# --------------------------------------------------------------------------
# Reservation primitives (Servis v2 FAZ-3)
#
# available = quantity - reserved_quantity, and every transition below applies
# its guard INSIDE the same UPDATE statement. A SELECT-then-UPDATE would let two
# concurrent reservations both read the same availability and both succeed —
# the classic oversell. Each helper returns False when its guard rejected the
# move, so callers turn that into a 409 instead of silently overselling.
# --------------------------------------------------------------------------


def _stock_row_exists(db: Session, company_id: int, warehouse_id: int, product_id: int) -> bool:
    return db.execute(
        select(warehouse_stocks.c.quantity).where(
            warehouse_stocks.c.company_id == company_id,
            warehouse_stocks.c.warehouse_id == warehouse_id,
            warehouse_stocks.c.product_id == product_id,
        )
    ).scalar_one_or_none() is not None


def reserve_warehouse_stock(
    db: Session, company_id: int, warehouse_id: int, product_id: int, amount: Decimal
) -> bool:
    """Reserve ``amount`` if availability allows it — atomically.

    The guard ``quantity - (reserved_quantity + amount) >= 0`` is part of the
    UPDATE, so concurrent reservations serialize on the row and the second one
    fails instead of overselling. Physical ``quantity`` is untouched: reserving
    is a claim on stock, not a movement of it.
    """
    delta = _decimal(amount)
    if delta <= ZERO:
        raise ValueError("Rezerve edilecek miktar sıfırdan büyük olmalıdır")
    reserved_expression = warehouse_stocks.c.reserved_quantity + delta
    updated = db.execute(
        update(warehouse_stocks)
        .where(
            warehouse_stocks.c.company_id == company_id,
            warehouse_stocks.c.warehouse_id == warehouse_id,
            warehouse_stocks.c.product_id == product_id,
            warehouse_stocks.c.quantity - reserved_expression >= ZERO,
        )
        .values(reserved_quantity=reserved_expression)
        .returning(warehouse_stocks.c.reserved_quantity)
    ).scalar_one_or_none()
    return updated is not None


def release_warehouse_reservation(
    db: Session, company_id: int, warehouse_id: int, product_id: int, amount: Decimal
) -> bool:
    """Drop a reservation without moving physical stock (cancel / return path)."""
    delta = _decimal(amount)
    if delta <= ZERO:
        return True
    reserved_expression = warehouse_stocks.c.reserved_quantity - delta
    updated = db.execute(
        update(warehouse_stocks)
        .where(
            warehouse_stocks.c.company_id == company_id,
            warehouse_stocks.c.warehouse_id == warehouse_id,
            warehouse_stocks.c.product_id == product_id,
            reserved_expression >= ZERO,
        )
        .values(reserved_quantity=reserved_expression)
        .returning(warehouse_stocks.c.reserved_quantity)
    ).scalar_one_or_none()
    return updated is not None


def issue_reserved_stock(
    db: Session, company_id: int, warehouse_id: int, product_id: int, amount: Decimal
) -> bool:
    """Convert a reservation into a physical issue in ONE statement.

    Decrementing ``quantity`` and ``reserved_quantity`` together is what keeps
    availability constant across the transition: the stock was already unavailable
    while reserved, so issuing it must not free it up for someone else in the gap
    between two separate statements.
    """
    delta = _decimal(amount)
    if delta <= ZERO:
        raise ValueError("Çıkış miktarı sıfırdan büyük olmalıdır")
    quantity_expression = warehouse_stocks.c.quantity - delta
    reserved_expression = warehouse_stocks.c.reserved_quantity - delta
    updated = db.execute(
        update(warehouse_stocks)
        .where(
            warehouse_stocks.c.company_id == company_id,
            warehouse_stocks.c.warehouse_id == warehouse_id,
            warehouse_stocks.c.product_id == product_id,
            quantity_expression >= ZERO,
            reserved_expression >= ZERO,
        )
        .values(quantity=quantity_expression, reserved_quantity=reserved_expression)
        .returning(warehouse_stocks.c.quantity)
    ).scalar_one_or_none()
    if updated is None:
        return False
    sync_product_stock(db, company_id, product_id)
    return True


def available_warehouse_stock(
    db: Session, company_id: int, warehouse_id: int, product_id: int
) -> Decimal:
    """quantity - reserved_quantity for one (warehouse, product) pair."""
    row = db.execute(
        select(
            warehouse_stocks.c.quantity,
            warehouse_stocks.c.reserved_quantity,
        ).where(
            warehouse_stocks.c.company_id == company_id,
            warehouse_stocks.c.warehouse_id == warehouse_id,
            warehouse_stocks.c.product_id == product_id,
        )
    ).mappings().first()
    if row is None:
        return ZERO
    return _decimal(row["quantity"]) - _decimal(row["reserved_quantity"])


def adjust_warehouse_stock(
    db: Session,
    company_id: int,
    warehouse_id: int,
    product_id: int,
    difference: Decimal | int | str,
    allow_negative: bool = False,
) -> Decimal:
    """Apply a stock delta atomically and preserve NUMERIC(18,4) precision.

    The update and the non-negative guard are part of the same SQL statement. This
    prevents two concurrent sales from both reading the same old quantity and then
    overwriting each other (the classic lost-update race).
    """
    delta = _decimal(difference)
    new_quantity = _update_existing_stock(
        db, company_id, warehouse_id, product_id, delta, allow_negative
    )

    if new_quantity is None:
        current = db.execute(
            select(warehouse_stocks.c.quantity).where(
                warehouse_stocks.c.company_id == company_id,
                warehouse_stocks.c.warehouse_id == warehouse_id,
                warehouse_stocks.c.product_id == product_id,
            )
        ).scalar_one_or_none()
        if current is not None:
            current_decimal = _decimal(current)
            if not allow_negative and current_decimal + delta < ZERO:
                raise ValueError(f"Depoda yetersiz stok. Mevcut: {current_decimal:g}")
            # A concurrent writer may have changed the row between the UPDATE and
            # this SELECT. Retry the guarded atomic UPDATE against the latest value.
            new_quantity = _update_existing_stock(
                db, company_id, warehouse_id, product_id, delta, allow_negative
            )
            if new_quantity is None:
                latest = db.execute(
                    select(warehouse_stocks.c.quantity).where(
                        warehouse_stocks.c.company_id == company_id,
                        warehouse_stocks.c.warehouse_id == warehouse_id,
                        warehouse_stocks.c.product_id == product_id,
                    )
                ).scalar_one_or_none()
                raise ValueError(
                    f"Depoda yetersiz stok. Mevcut: {_decimal(latest):g}"
                )
        else:
            if delta < ZERO and not allow_negative:
                raise ValueError("Depoda yetersiz stok. Mevcut: 0")
            try:
                # The savepoint keeps an insert race from rolling back the caller's
                # complete sale/purchase transaction.
                with db.begin_nested():
                    new_quantity = db.execute(
                        insert(warehouse_stocks)
                        .values(
                            company_id=company_id,
                            warehouse_id=warehouse_id,
                            product_id=product_id,
                            quantity=delta,
                            critical_stock=ZERO,
                        )
                        .returning(warehouse_stocks.c.quantity)
                    ).scalar_one()
            except IntegrityError:
                new_quantity = _update_existing_stock(
                    db, company_id, warehouse_id, product_id, delta, allow_negative
                )
                if new_quantity is None:
                    current = db.execute(
                        select(warehouse_stocks.c.quantity).where(
                            warehouse_stocks.c.company_id == company_id,
                            warehouse_stocks.c.warehouse_id == warehouse_id,
                            warehouse_stocks.c.product_id == product_id,
                        )
                    ).scalar_one_or_none()
                    raise ValueError(
                        f"Depoda yetersiz stok. Mevcut: {_decimal(current):g}"
                    )

    result = _decimal(new_quantity)
    sync_product_stock(db, company_id, product_id)
    return result

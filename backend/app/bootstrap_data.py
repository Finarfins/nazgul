from __future__ import annotations

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine

from .auth import hash_password, users, utcnow, verify_password
from .config import settings
from .core_schema import products
from .finance_engine import finance_accounts
from .inventory import warehouse_stocks, warehouses
from .tenancy import branches, companies, memberships


def _ensure_bootstrap_admin(
    conn: Connection,
    *,
    username: str,
    display_name: str,
    password: str,
) -> int:
    """Resolve the configured bootstrap admin without creating surprise admins.

    Bootstrap account creation is valid only while the user table is empty. Once
    an installation has users, changing BOOTSTRAP_ADMIN_USERNAME must not create
    another global administrator or attach an arbitrary existing account to the
    first tenant.
    """
    admin = conn.execute(
        select(users).where(users.c.username == username)
    ).mappings().first()
    existing_user = conn.execute(select(users.c.id).limit(1)).first()

    if admin is None:
        if existing_user is not None:
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_USERNAME mevcut bir yöneticiyle eşleşmiyor; "
                "başlatma sırasında yeni ayrıcalıklı hesap oluşturulmadı"
            )
        return int(
            conn.execute(
                insert(users)
                .values(
                    username=username,
                    email=f"legacy-bootstrap-{username.lower()}@legacy.invalid",
                    email_verified=True,
                    display_name=display_name,
                    password_hash=hash_password(password),
                    role="admin",
                    is_active=True,
                    must_change_password=True,
                    created_at=utcnow(),
                )
                .returning(users.c.id)
            ).scalar_one()
        )

    if str(admin["role"]) != "admin":
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_USERNAME admin rolüne sahip olmayan bir hesapla eşleşiyor"
        )
    if not bool(admin["is_active"]):
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_USERNAME pasif bir yönetici hesabıyla eşleşiyor"
        )

    admin_id = int(admin["id"])
    # Existing installations that still use the historical development
    # password must change it at the next login. Production can never reach
    # this fallback because Settings rejects admin123 there.
    if verify_password("admin123", admin["password_hash"]) and not admin[
        "must_change_password"
    ]:
        conn.execute(
            update(users)
            .where(users.c.id == admin_id)
            .values(must_change_password=True)
        )
    return admin_id


def _ensure_bootstrap_company(conn: Connection) -> int:
    """Resolve an active bootstrap tenant without reviving disabled companies."""
    active_company = conn.execute(
        select(companies.c.id)
        .where(companies.c.is_active.is_(True))
        .order_by(companies.c.id)
        .limit(1)
    ).first()
    if active_company is not None:
        return int(active_company[0])

    existing_company = conn.execute(select(companies.c.id).limit(1)).first()
    if existing_company is not None:
        raise RuntimeError(
            "Bootstrap için aktif firma bulunamadı; pasif firma otomatik olarak "
            "yeniden etkinleştirilmedi veya yöneticiye bağlanmadı"
        )

    return int(
        conn.execute(
            insert(companies)
            .values(name="Ana Firma", is_active=True, created_at=utcnow())
            .returning(companies.c.id)
        ).scalar_one()
    )


def seed_bootstrap_data(engine: Engine) -> None:
    """Create required first-run data without performing any schema DDL."""
    bootstrap_username = settings.bootstrap_admin_username.strip()
    bootstrap_password = settings.effective_bootstrap_admin_password
    bootstrap_display_name = settings.bootstrap_admin_display_name.strip()

    with engine.begin() as conn:
        admin_id = _ensure_bootstrap_admin(
            conn,
            username=bootstrap_username,
            display_name=bootstrap_display_name,
            password=bootstrap_password,
        )

        company_id = _ensure_bootstrap_company(conn)

        branch = conn.execute(
            select(branches.c.id)
            .where(branches.c.company_id == company_id)
            .order_by(branches.c.id)
            .limit(1)
        ).first()
        if branch is None:
            branch_id = int(
                conn.execute(
                    insert(branches)
                    .values(
                        company_id=company_id,
                        name="Merkez",
                        is_active=True,
                        created_at=utcnow(),
                    )
                    .returning(branches.c.id)
                ).scalar_one()
            )
        else:
            branch_id = int(branch[0])

        membership = conn.execute(
            select(memberships.c.id).where(
                memberships.c.user_id == admin_id,
                memberships.c.company_id == company_id,
            )
        ).first()
        if membership is None:
            conn.execute(
                insert(memberships).values(
                    user_id=admin_id,
                    company_id=company_id,
                    is_default=True,
                    created_at=utcnow(),
                )
            )

        warehouse = conn.execute(
            select(warehouses.c.id)
            .where(
                warehouses.c.company_id == company_id,
                warehouses.c.is_active.is_(True),
            )
            .order_by(warehouses.c.id)
            .limit(1)
        ).first()
        if warehouse is None:
            warehouse_id = int(
                conn.execute(
                    insert(warehouses)
                    .values(
                        name="Merkez Depo",
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
            warehouse_id = int(warehouse[0])

        for product_id, stock, critical_stock in conn.execute(
            select(products.c.id, products.c.stock, products.c.critical_stock).where(
                products.c.company_id == company_id
            )
        ).all():
            exists = conn.execute(
                select(warehouse_stocks.c.product_id).where(
                    warehouse_stocks.c.company_id == company_id,
                    warehouse_stocks.c.warehouse_id == warehouse_id,
                    warehouse_stocks.c.product_id == product_id,
                )
            ).first()
            if exists is None:
                conn.execute(
                    insert(warehouse_stocks).values(
                        company_id=company_id,
                        warehouse_id=warehouse_id,
                        product_id=product_id,
                        quantity=stock or 0,
                        critical_stock=critical_stock or 0,
                    )
                )

        for name, account_type in (
            ("Merkez Kasa", "cash"),
            ("Ana Banka", "bank"),
            ("Ana POS", "pos"),
        ):
            exists = conn.execute(
                select(finance_accounts.c.id)
                .where(
                    finance_accounts.c.company_id == company_id,
                    finance_accounts.c.account_type == account_type,
                )
                .limit(1)
            ).first()
            if exists is None:
                conn.execute(
                    insert(finance_accounts).values(
                        company_id=company_id,
                        name=name,
                        account_type=account_type,
                        currency="TRY",
                        opening_balance=0,
                        is_active=True,
                        created_at=utcnow(),
                    )
                )

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest
from fastapi import FastAPI

from app.route_security_contracts import (
    build_route_security_contracts,
    fingerprint_route_contracts,
    registered_api_operations,
)


ROUTE_REASON_GROUPS = (
    (
        "Public authentication or health endpoint; no tenant context is available.",
        {
            ("POST", "/api/auth/login"),
            ("POST", "/api/auth/refresh"),
            ("POST", "/api/auth/register"),
            ("POST", "/api/auth/resend-verification"),
            ("GET", "/api/auth/verify-email"),
            # Şifresini unutan kullanıcının oturumu yoktur; bu iki uç kimlik
            # doğrulama kapısının önünde olmak zorunda. /forgot-password adresin
            # kayıtlı olup olmadığını sızdırmaz, ikisi de IP başına saatlik
            # limite tabidir ve /reset-password tek kullanımlık token ister.
            ("POST", "/api/auth/forgot-password"),
            ("POST", "/api/auth/reset-password"),
            ("GET", "/api/health"),
            ("GET", "/api/live"),
            ("GET", "/api/ready"),
        },
    ),
    (
        "Authenticated session self-service; no feature permission is required.",
        {
            ("POST", "/api/auth/logout"),
            ("GET", "/api/auth/me"),
            ("POST", "/api/auth/change-password"),
        },
    ),
    (
        "Tenant directory or configuration read available to authenticated company members.",
        {
            ("GET", "/api/companies"),
            ("GET", "/api/company-settings"),
            ("GET", "/api/branches"),
        },
    ),
    (
        "Tenant-scoped operational read; the handler must filter by request.state.company_id.",
        {
            ("GET", "/api/notifications"),
            ("GET", "/api/warehouses/counts"),
            ("GET", "/api/warehouses/counts/{count_id}"),
            ("GET", "/api/warehouses"),
            ("GET", "/api/warehouses/stock"),
            ("GET", "/api/warehouses/replenishment"),
            ("GET", "/api/warehouses/transfers"),
            ("GET", "/api/warehouses/{warehouse_id}"),
            ("GET", "/api/warehouse-transfers/{transfer_id}"),
            ("GET", "/api/dashboard"),
            ("GET", "/api/demo/summary"),
            ("GET", "/api/customers"),
            ("GET", "/api/customers/{customer_id}"),
            ("GET", "/api/customers/{customer_id}/statement"),
            # Kart listesi bir önizlemedir; TÜM satış/alış geçmişi bu iki uçtan
            # sayfalanır. Kök sorgu company_id ile bağlıdır ve cari başka
            # firmaya aitse 404 döner (belge sayısı bile sızmaz).
            ("GET", "/api/customers/{customer_id}/documents"),
            ("GET", "/api/suppliers/{supplier_id}/documents"),
            ("GET", "/api/machines"),
            ("GET", "/api/machines/{machine_id}"),
            ("GET", "/api/machines/{machine_id}/hour-readings"),
            ("GET", "/api/machines/{machine_id}/ownership-history"),
            ("GET", "/api/technician-profiles"),
            ("GET", "/api/products"),
            ("GET", "/api/products/stock/movements/all"),
            ("GET", "/api/products/{product_id}"),
            ("GET", "/api/products/{product_id}/warehouse-stock"),
            ("GET", "/api/suppliers"),
            ("GET", "/api/suppliers/{supplier_id}"),
            ("GET", "/api/suppliers/{supplier_id}/statement"),
            ("GET", "/api/payment-allocations/engine-state"),
            ("GET", "/api/payment-allocations/payments/{payment_id}"),
            ("GET", "/api/payment-allocations/orders/{order_id}"),
            ("GET", "/api/payment-allocations/charges/{receivable_charge_id}"),
            ("GET", "/api/search"),
            ("GET", "/api/search/parts"),
            ("GET", "/api/analytics/seasonal-plan"),
            ("GET", "/api/pos/lookup"),
            ("GET", "/api/quick-pick"),
            ("GET", "/api/imports/customers/template.xlsx"),
            ("GET", "/api/imports/suppliers/template.xlsx"),
            ("GET", "/api/imports/products/template.xlsx"),
            ("GET", "/api/invoices"),
            ("GET", "/api/invoices/{invoice_id}"),
            ("GET", "/api/invoices/{invoice_id}/history"),
            ("GET", "/api/invoices/{invoice_id}/pdf"),
            ("GET", "/api/invoices/{invoice_id}/einvoice/status"),
            ("GET", "/api/exchange-rates"),
            ("GET", "/api/part-supersessions"),
            ("GET", "/api/products/{product_id}/current"),
            ("GET", "/api/orders"),
            ("GET", "/api/purchases"),
            ("GET", "/api/orders/last-sale-price"),
            ("GET", "/api/purchases/last-purchase-price"),
            ("GET", "/api/{kind}/{transaction_id}"),
        },
    ),
    (
        "Tenant-scoped document or export read; source records remain company-filtered.",
        {
            ("GET", "/api/documents/{kind}/{document_id}/pdf"),
            ("GET", "/api/documents/{kind}/{document_id}/xlsx"),
            ("GET", "/api/products/{product_id}/qr.png"),
            ("GET", "/api/products/{product_id}/label.pdf"),
            ("GET", "/api/products/{product_id}/barcode-label.pdf"),
            ("GET", "/api/exports/products.xlsx"),
            ("GET", "/api/customers/{customer_id}/statement.pdf"),
            ("GET", "/api/suppliers/{supplier_id}/statement.pdf"),
            ("GET", "/api/exports/warehouse-count-variance.xlsx"),
        },
    ),
    (
        "Tenant-scoped notification read; recipient and payload redaction rules still apply.",
        {
            ("GET", "/api/notifications/outbox"),
            ("GET", "/api/notifications/counters"),
            ("GET", "/api/notifications/templates"),
            ("GET", "/api/notifications/consents"),
            ("GET", "/api/notifications/consents/{consent_id}/events"),
            ("GET", "/api/notifications/rules"),
            ("GET", "/api/notifications/{notification_id}/preview"),
        },
    ),
    (
        "Tenant-scoped workflow or service read available to baseline operational roles.",
        {
            ("GET", "/api/workflow/{kind}"),
            ("GET", "/api/workflow/{kind}/{doc_id}"),
            ("GET", "/api/work-orders"),
            ("GET", "/api/work-orders/technicians"),
            ("GET", "/api/work-orders/{work_order_id}"),
            ("GET", "/api/work-orders/{work_order_id}/parts"),
            ("GET", "/api/work-orders/{work_order_id}/labor-lines"),
            ("GET", "/api/work-order-attachments/{work_order_id}"),
            ("GET", "/api/work-order-attachments/{work_order_id}/{attachment_id}/download"),
            ("GET", "/api/work-orders/{work_order_id}/invoice"),
        },
    ),
    (
        "Platform backup operation; router-level admin and operator allow-list checks apply.",
        {
            ("GET", "/api/platform/backups"),
            ("POST", "/api/platform/backups"),
            ("GET", "/api/platform/backups/{name}/download"),
            ("POST", "/api/platform/backups/{name}/verify"),
            ("POST", "/api/platform/backups/{name}/restore"),
        },
    ),
    (
        "Untenanted security audit read; rows belong to no company, so the router "
        "applies the platform-operator allow-list instead of tenant scoping.",
        {
            ("GET", "/api/platform/audit"),
        },
    ),
)
ROUTE_REASONS = {
    route: reason
    for reason, routes in ROUTE_REASON_GROUPS
    for route in routes
}
DYNAMIC_PERMISSION_CASES = {
    ("GET", "/api/documents/{kind}/{document_id}/pdf"): {
        "/api/documents/orders/1/pdf": "sales",
        "/api/documents/purchases/1/pdf": "purchases",
    },
    ("GET", "/api/documents/{kind}/{document_id}/xlsx"): {
        "/api/documents/orders/1/xlsx": "sales",
        "/api/documents/purchases/1/xlsx": "purchases",
    },
    ("GET", "/api/workflow/{kind}"): {
        "/api/workflow/quote": "read",
        "/api/workflow/sales_order": "read",
        "/api/workflow/delivery": "read",
        "/api/workflow/sale_return": "read",
        "/api/workflow/purchase_return": "read",
    },
    ("POST", "/api/workflow/{kind}"): {
        "/api/workflow/quote": "sales",
        "/api/workflow/sales_order": "sales",
        "/api/workflow/delivery": "sales",
        "/api/workflow/sale_return": "sales",
        "/api/workflow/purchase_return": "purchases",
    },
    ("DELETE", "/api/workflow/{kind}/{doc_id}"): {
        "/api/workflow/quote/1": "sales",
        "/api/workflow/sales_order/1": "sales",
        "/api/workflow/delivery/1": "sales",
        "/api/workflow/sale_return/1": "sales",
        "/api/workflow/purchase_return/1": "purchases",
    },
    ("GET", "/api/workflow/{kind}/{doc_id}"): {
        "/api/workflow/quote/1": "read",
        "/api/workflow/sales_order/1": "read",
        "/api/workflow/delivery/1": "read",
        "/api/workflow/sale_return/1": "read",
        "/api/workflow/purchase_return/1": "read",
    },
    ("PUT", "/api/workflow/{kind}/{doc_id}"): {
        "/api/workflow/quote/1": "sales",
        "/api/workflow/sales_order/1": "sales",
        "/api/workflow/delivery/1": "sales",
        "/api/workflow/sale_return/1": "sales",
        "/api/workflow/purchase_return/1": "purchases",
    },
    ("DELETE", "/api/{kind}/{transaction_id}"): {
        "/api/orders/1": "sales",
        "/api/purchases/1": "purchases",
    },
    ("GET", "/api/{kind}/{transaction_id}"): {
        "/api/orders/1": "read",
        "/api/purchases/1": "read",
    },
}

EXPECTED_OPERATION_COUNT = 340
EXPECTED_PATH_COUNT = 263
EXPECTED_SECURITY_FINGERPRINT = (
    # 20260807: saha yazma yüzeyi eklendi —
    #   POST /api/field/work-orders/{work_order_id}/status  (durum ilerletme)
    #   POST /api/field/work-orders/{work_order_id}/parts   (kullanılan parça)
    #   POST /api/field/work-orders/{work_order_id}/attachments (fotoğraf/imza)
    #   POST /api/field/work-orders/{work_order_id}/labor   (işçilik, DRAFT açılır)
    # İzin `/api/field` önekinden gelir (field_service); kapsam firma VE isteği
    # yapan teknisyen, başkasının iş emrine 404 döner. Parça ucunda fiyat ve
    # depo istemciden ALINMAZ: fiyat üründen, depo teknisyenin servis aracı
    # deposundan çözülür.
    # 20260807 Tarla Yönetimi V1 FAZ 2 (mobil-erp#2): 13 uç eklendi —
    # /api/farms, /api/farm-parcels, /api/crop-seasons, /api/field-activities
    # (+/inputs), /api/field-harvests, /api/field-tasks, /api/field-dashboard.
    # İzinler farm.view / farm.manage / farm.inputs. DİKKAT: bu yollar
    # /api/field ile başlıyor; izin çözümleyicide tarla kuralı saha
    # kuralından ÖNCE olmak zorunda (testi var).
    # 20260807 (FAZ 5): GET /api/field-safety — yürürlükteki ilaç güvenlik
    # kısıtları. İzni `farm.view`; `/api/field-safety` de `/api/field` ile
    # BAŞLADIĞI için `_FARM_PATH_PREFIXES`'e eklenmeseydi sessizce
    # `field_service`'e düşerdi (bu tuzak iki kez yaşandı, artık türetilmiş bir
    # test tüm tarla uçlarını tarıyor).
    #
    # NOT: bu parmak izi ÖNCE yanlış hesaplandı — uç `field_service`'e
    # düşmüşken alınmıştı. Yani sözleşme yanlış izni sadakatle çiviliyordu;
    # doğru sıra "izni düzelt, SONRA parmak izini al".
    # 20260807 (FAZ 7): GET /api/farm-parcels/{parcel_id}/timeline — parselin
    # sezonları + faaliyet/hasat çizelgesi TEK istekte. İzni `farm.view`;
    # `/api/farm-parcels` öneki zaten `_FARM_PATH_PREFIXES`'te olduğu için
    # `/api/field` tuzağına düşmüyor (yine de türetilmiş test tarıyor).
    # 20260808 (Hayvancılık V1 FAZ 2): 12 uç — /api/animals, /api/animal-*,
    # /api/milk-yields, /api/herd-dashboard. İzinler `herd.view/manage/health`;
    # aşı ucu AYRI izinde (veteriner hayvan satamamalı). Parmak izi
    # dondurulmadan ÖNCE tüm uçların herd.* iznine düştüğü türetilmiş kontrolle
    # doğrulandı — FAZ 5'te yanlış izin sadakatle çivilenmişti.
    # 20260808: Rota envanteri, onaylı tenant/SQL incelemesi sonrası yenilendi.
    # 20260808 Gerçek Maliyet V1 FAZ 1 (mobil-erp#24): 4 işlem / 2 yol eklendi —
    # GET+POST /api/cost-rates ve GET+PUT /api/cost-rates/{rate_id}. Dördü de
    # `finance` iznine bağlı, GET'ler dahil: oran bir PARA TANIMI ve okunması da
    # değiştirilmesi de kâr rakamını ilgilendirir. Genel `read`e düşselerdi depo
    # rolü firmanın maliyet yapısını görebilirdi.
    # 20260812 (denetim görünürlüğü): 1 işlem / 1 yol eklendi —
    # GET /api/platform/audit. Hiçbir kiracıya bağlanamayan güvenlik denetim
    # satırları (giriş denemeleri, kayıt, AUTH_REQUIRED 401'leri) `company_id`
    # süzen HİÇBİR okumada görünmüyordu; bu uç onların okunabildiği yerdir.
    # İzin çözümleyicide `read`e düşer — /api/platform/backups ile AYNI — çünkü
    # gerçek kapı yönlendiricideki `require_platform_operator`'dır: satırlar tek
    # bir kiracıya ait olmadığı için tek bir kiracının yöneticisine de değildir.
    # Parmak izi DONDURULMADAN ÖNCE iznin `read` olduğu ve operatör olmayan bir
    # admin'in 403 aldığı ayrı ayrı ölçüldü (bu dosyanın yukarıdaki notu, yanlış
    # izni sadakatle çivilemenin iki kez yaşandığını söylüyor).
    # 20260827 (FIELD_STOK_OUTBOX açılış koşulu 2 — outbox OKUMA yüzeyi):
    # 2 işlem / 2 yol eklendi — GET /api/field-integration-events ve
    # GET /api/field-integration-events/summary. İkisi de `farm.view`.
    # AYNI TUZAK YİNE KURULDU VE YİNE ELLE AÇILDI: yollar `/api/field` ile
    # BAŞLIYOR, `_FARM_PATH_PREFIXES`'e eklenmeseydi sessizce
    # `field_service`'e düşerlerdi. Bu dosyanın yukarıdaki notu, parmak
    # izini YANLIŞ izinle dondurmanın iki kez yaşandığını söylüyor; bu
    # yüzden sıra korundu: önce önek listesine eklendi, `required_permission`
    # ile `farm.view` ÖLÇÜLDÜ, SONRA parmak izi alındı.
    # Uçlar SALT OKUR; tüketici davranışı ve `FIELD_STOCK_OUTBOX_ENABLED`
    # varsayılanı DEĞİŞMEDİ.
    "c7a3c174419f7f1d6bd62ac36f7ffc476a84377b010d526925096b54aaf380a6"
)
TEST_PERMISSIONS = {"__admin_only__", "read", "sales"}


@pytest.fixture(scope="module")
def application_contract(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("route-security-contract")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(workspace / 'test.db').as_posix()}")
    monkeypatch.setenv("SUNGUR_DATA_DIR", str(workspace))
    monkeypatch.setenv("AUTO_MIGRATE", "true")

    from app.auth import ROLE_PERMISSIONS, required_permission
    from app.main import PUBLIC_API, app, security_and_audit
    from app.routers import outputs

    known_permissions = {
        permission
        for permissions in ROLE_PERMISSIONS.values()
        for permission in permissions
        if permission != "*"
    } | {"__admin_only__"}

    def effective_permission(method: str, path: str) -> str:
        if method == "GET" and path.startswith("/api/documents/"):
            kind = path.split("/")[3]
            return str(outputs._kind(kind)["permission"])
        return required_permission(method, path)

    yield (
        app,
        PUBLIC_API,
        security_and_audit,
        effective_permission,
        known_permissions,
        outputs,
    )
    monkeypatch.undo()


def _schema(*operations: tuple[str, str]) -> dict:
    paths: dict[str, dict[str, dict]] = {}
    for method, path in operations:
        paths.setdefault(path, {})[method.lower()] = {}
    return {"paths": paths}


def _accepted_kind_literals(function) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    accepted: set[str] = set()
    for comparison in ast.walk(tree):
        if not isinstance(comparison, ast.Compare):
            continue
        if not isinstance(comparison.left, ast.Name) or comparison.left.id != "kind":
            continue
        for comparator in comparison.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                accepted.add(comparator.value)
            elif isinstance(comparator, (ast.List, ast.Set, ast.Tuple)):
                accepted.update(
                    element.value
                    for element in comparator.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                )
    return accepted


def test_read_route_requires_an_explicit_review_reason() -> None:
    with pytest.raises(ValueError, match="explicit review reason"):
        build_route_security_contracts(
            _schema(("GET", "/api/example")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
        )


def test_public_and_platform_exceptions_require_an_explicit_reason() -> None:
    with pytest.raises(ValueError, match="explicit review reason"):
        build_route_security_contracts(
            _schema(("GET", "/api/live")),
            public_paths={"/api/live"},
            route_reasons={},
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
        )

    with pytest.raises(ValueError, match="explicit review reason"):
        build_route_security_contracts(
            _schema(("POST", "/api/platform/backups")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
        )


def test_unknown_permission_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown permission"):
        build_route_security_contracts(
            _schema(("POST", "/api/example")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "typo_permission",
            known_permissions=TEST_PERMISSIONS,
        )

    with pytest.raises(ValueError, match="unknown permission"):
        build_route_security_contracts(
            _schema(("POST", "/api/example")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "*",
            known_permissions=TEST_PERMISSIONS,
        )


def test_stale_review_reason_fails_closed() -> None:
    with pytest.raises(ValueError, match="review reason is not registered"):
        build_route_security_contracts(
            _schema(("POST", "/api/example")),
            public_paths=set(),
            route_reasons={("GET", "/api/removed"): "Stale reason."},
            permission_resolver=lambda method, path: "sales",
            known_permissions=TEST_PERMISSIONS,
        )


def test_new_route_changes_the_contract_fingerprint() -> None:
    reasons = {("GET", "/api/example"): "Tenant-scoped operational read."}
    before = build_route_security_contracts(
        _schema(("GET", "/api/example")),
        public_paths=set(),
        route_reasons=reasons,
        permission_resolver=lambda method, path: "read",
        known_permissions=TEST_PERMISSIONS,
    )
    after = build_route_security_contracts(
        _schema(("GET", "/api/example"), ("POST", "/api/example")),
        public_paths=set(),
        route_reasons=reasons,
        permission_resolver=lambda method, path: "read" if method == "GET" else "sales",
        known_permissions=TEST_PERMISSIONS,
    )

    assert fingerprint_route_contracts(before) != fingerprint_route_contracts(after)


def test_kind_route_requires_concrete_permission_cases() -> None:
    with pytest.raises(ValueError, match="concrete permission cases"):
        build_route_security_contracts(
            _schema(("POST", "/api/workflow/{kind}")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "sales",
            known_permissions=TEST_PERMISSIONS,
            permission_cases={},
        )

    with pytest.raises(ValueError, match="permission case mismatch"):
        build_route_security_contracts(
            _schema(("POST", "/api/workflow/{kind}")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: (
                "purchases" if "purchase_return" in path else "sales"
            ),
            known_permissions={*TEST_PERMISSIONS, "purchases"},
            permission_cases={
                ("POST", "/api/workflow/{kind}"): {
                    "/api/workflow/order": "sales",
                    "/api/workflow/purchase_return": "sales",
                }
            },
        )


def test_hidden_api_route_cannot_bypass_the_contract() -> None:
    local_app = FastAPI()

    @local_app.get("/api/visible")
    def visible() -> None:
        return None

    @local_app.get("/api/hidden", include_in_schema=False)
    def hidden() -> None:
        return None

    with pytest.raises(ValueError, match="registered API routes differ from OpenAPI"):
        build_route_security_contracts(
            local_app.openapi(),
            public_paths=set(),
            route_reasons={
                ("GET", "/api/visible"): "Reviewed tenant-scoped read."
            },
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
            registered_operations=registered_api_operations(local_app),
        )


def test_mounted_api_cannot_bypass_the_contract() -> None:
    local_app = FastAPI()

    @local_app.get("/api/visible")
    def visible() -> None:
        return None

    local_app.mount("/api/mounted", FastAPI())

    with pytest.raises(ValueError, match="registered API routes differ from OpenAPI"):
        build_route_security_contracts(
            local_app.openapi(),
            public_paths=set(),
            route_reasons={
                ("GET", "/api/visible"): "Reviewed tenant-scoped read."
            },
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
            registered_operations=registered_api_operations(local_app),
        )


def test_registered_route_security_contract_is_exact(application_contract) -> None:
    app, public_api, _, permission_resolver, known_permissions, _ = application_contract
    assert sum(len(routes) for _, routes in ROUTE_REASON_GROUPS) == len(ROUTE_REASONS)
    contracts = build_route_security_contracts(
        app.openapi(),
        public_paths=public_api,
        route_reasons=ROUTE_REASONS,
        permission_resolver=permission_resolver,
        known_permissions=known_permissions,
        permission_cases=DYNAMIC_PERMISSION_CASES,
        registered_operations=registered_api_operations(app),
    )

    assert len(contracts) == EXPECTED_OPERATION_COUNT
    assert len({contract.path for contract in contracts}) == EXPECTED_PATH_COUNT
    assert fingerprint_route_contracts(contracts) == EXPECTED_SECURITY_FINGERPRINT


def test_security_middleware_orders_rbac_and_tenant_before_handler(
    application_contract,
) -> None:
    app, _, security_and_audit, _, _, _ = application_contract
    installed_dispatches = [
        middleware.kwargs.get("dispatch")
        for middleware in app.user_middleware
    ]
    assert installed_dispatches.count(security_and_audit) == 1

    source = inspect.getsource(security_and_audit)

    permission_check = source.index("has_permission(")
    tenant_resolution = source.index("resolve_company(")
    handler_call = source.index("await call_next(request)")
    assert permission_check < tenant_resolution < handler_call


def test_document_output_cases_include_handler_permission(application_contract) -> None:
    _, _, _, permission_resolver, _, outputs = application_contract

    assert permission_resolver("GET", "/api/documents/orders/1/pdf") == "sales"
    assert (
        permission_resolver("GET", "/api/documents/purchases/1/xlsx")
        == "purchases"
    )
    for handler in (outputs.document_pdf, outputs.document_xlsx):
        source = inspect.getsource(handler)
        assert '_require_permission(request, config["permission"])' in source


def test_dynamic_kind_cases_match_handler_inventories(application_contract) -> None:
    _, _, _, _, _, outputs = application_contract
    from app.routers import transactions, workflow

    workflow_kind_sets = {
        frozenset(path.split("/")[3] for path in cases)
        for (method, template), cases in DYNAMIC_PERMISSION_CASES.items()
        if template.startswith("/api/workflow/")
    }
    assert workflow_kind_sets == {frozenset(workflow.CONFIG)}

    output_kind_sets = {
        frozenset(path.split("/")[3] for path in cases)
        for (method, template), cases in DYNAMIC_PERMISSION_CASES.items()
        if template.startswith("/api/documents/")
    }
    accepted_output_kinds = _accepted_kind_literals(outputs._kind)
    assert output_kind_sets == {frozenset(accepted_output_kinds)}

    transaction_kind_sets = {
        frozenset(path.split("/")[2] for path in cases)
        for (_, template), cases in DYNAMIC_PERMISSION_CASES.items()
        if template.startswith("/api/{kind}/")
    }
    handler_transaction_kinds = {
        frozenset(_accepted_kind_literals(handler))
        for handler in (transactions.detail, transactions.delete_transaction)
    }
    assert handler_transaction_kinds == transaction_kind_sets

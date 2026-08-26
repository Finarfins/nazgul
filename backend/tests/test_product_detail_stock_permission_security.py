"""Ürün detayının stok yetkisine bağlı olduğunun yapısal kanıtı.

Bu dosya daha önce ``detail`` fonksiyonunun **gövdesinde** ``has_permission``
çağrısı arıyor, bulamadığı için ``strict xfail`` ile "açık güvenlik gap'i"
olarak işaretliyordu. İddia yanlış yere bakıyordu: koruma repoda uygulanmış
durumda, ancak bu kod tabanındaki diğer hassas uçlarla **aynı desende** —
rotaya bağlanan bir ``Depends`` guard'ı olarak
(``app/product_detail_permissions.py``). Aynı desen
``install_audit_list_guard``, ``install_user_list_guard``,
``install_user_status_tenant_guard``, ``install_user_create_identity_guard`` ve
``install_logout_csrf_guard`` için de kullanılıyor; korumayı fonksiyon
gövdesine taşımak konvansiyonu tek bir uç için bozmak olurdu.

Bu yüzden iddia, korumanın *nerede yazıldığına* değil **gerçekten bağlı olup
olmadığına** çevrildi. Çalışma zamanı davranışının kanıtı (yetkisiz rol → 403,
yetkili rol → 200) gerçek HTTP ile
``test_product_detail_stock_permission_http.py`` içinde tutuluyor.
"""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi.routing import APIRoute

from app.product_detail_permissions import require_product_detail_stock
from app.routers import products


PRODUCTS_ROUTER = Path(__file__).resolve().parents[1] / "app" / "routers" / "products.py"


def _detail_source() -> str:
    source = PRODUCTS_ROUTER.read_text(encoding="utf-8")
    module = ast.parse(source)
    detail = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "detail"
    )
    return ast.get_source_segment(source, detail) or ""


def _detail_route() -> APIRoute:
    matches = [
        route
        for route in products.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/products/{product_id}"
        and "GET" in route.methods
    ]
    assert len(matches) == 1, "Ürün detay rotası tekil olmalı"
    return matches[0]


def test_product_detail_requires_explicit_stock_permission() -> None:
    """Detay rotası stok yetkisi guard'ını çözümlenmiş bağımlılıkları arasında taşır."""
    route = _detail_route()

    assert any(
        dependency.call is require_product_detail_stock
        for dependency in route.dependant.dependencies
    ), "Ürün detay rotasında stok yetkisi guard'ı bağlı değil"


def test_product_detail_body_defers_authorization_to_the_guard() -> None:
    """Gövde, guard'ı gölgeleyecek ikinci bir yetki kararı vermez.

    Yetki kararı tek noktada — rota guard'ında — kalmalı. Gövdeye ikinci bir
    karar noktası eklenirse iki kaynak birbirinden sessizce ayrışabilir.
    """
    source = _detail_source()

    assert "has_permission" not in source, (
        "Yetki kararı rota guard'ında; gövdede ikinci bir karar noktası olmamalı"
    )


def test_product_detail_remains_tenant_scoped() -> None:
    source = _detail_source()

    assert "company_id(request)" in source
    assert "company_id=:cid" in source

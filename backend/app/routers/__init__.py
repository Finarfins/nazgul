from __future__ import annotations

# Eagerly load routers whose sensitive endpoints receive focused dependencies
# before app.main includes them in the FastAPI application.
from . import auth as auth
from . import customers as customers
from . import finance as finance
from . import late_fees as late_fees
from . import payment_allocations as payment_allocations
from . import products as products
from . import warehouse_count_reports as warehouse_count_reports
from ..audit_permissions import install_audit_list_guard
from ..logout_csrf_guard import install_logout_csrf_guard
from ..product_detail_permissions import install_product_detail_guard
from ..user_create_identity_guard import install_user_create_identity_guard
from ..user_list_permissions import install_user_list_guard
from ..user_status_tenant_guard import install_user_status_tenant_guard

install_product_detail_guard(products.router)
install_audit_list_guard(auth.router)
install_user_list_guard(auth.router)
install_user_status_tenant_guard(auth.router)
install_user_create_identity_guard(auth.router)
install_logout_csrf_guard(auth.router)

del install_product_detail_guard
del install_audit_list_guard
del install_user_list_guard
del install_user_status_tenant_guard
del install_user_create_identity_guard
del install_logout_csrf_guard

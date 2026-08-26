import os
import shutil
import tempfile
from pathlib import Path

source = Path(__file__).with_name("veriler.db")
temp = Path(tempfile.mkdtemp()) / "test.db"
shutil.copy2(source, temp)
os.environ["DATABASE_URL"] = f"sqlite:///{temp}"

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
assert login.status_code == 200, login.text
headers = {"Authorization": "Bearer " + login.json()["access_token"]}
products = client.get("/api/products", headers=headers).json()
customers = client.get("/api/customers", headers=headers).json()
warehouses = client.get("/api/warehouses", headers=headers).json()
assert products and customers and warehouses
product = products[0]
customer_id = customers[0]["id"]
warehouse_id = warehouses[0]["id"]
client.post(
    f"/api/products/{product['id']}/stock",
    headers=headers,
    json={"mode": "add", "warehouse_id": warehouse_id, "quantity": 100, "movement_date": "2026-07-11", "note": "test"},
)
payload = {
    "entity_id": customer_id,
    "transaction_date": "2026-07-11",
    "warehouse_id": warehouse_id,
    "status": "draft",
    "payment_method": "cash",
    "paid_amount": 0,
    "discount_percent": 10,
    "items": [{"product_id": product["id"], "quantity": 2, "unit_price": 100, "vat_rate": 20, "discount_percent": 5}],
}
created = client.post("/api/orders", headers=headers, json=payload)
assert created.status_code == 201, created.text
order_id = created.json()["id"]
assert created.json()["final_total"] == 171.0
before = client.get("/api/products", headers=headers, params={"warehouse_id": warehouse_id}).json()
stock_before = next(x for x in before if x["id"] == product["id"])["warehouse_stock"]
payload.update({"status": "completed", "paid_amount": 100})
updated = client.put(f"/api/orders/{order_id}", headers=headers, json=payload)
assert updated.status_code == 200, updated.text
after = client.get("/api/products", headers=headers, params={"warehouse_id": warehouse_id}).json()
stock_after = next(x for x in after if x["id"] == product["id"])["warehouse_stock"]
assert stock_after == stock_before - 2
payments = client.get("/api/payments", headers=headers).json()
assert any(x.get("reference_id") == order_id and x.get("amount") == 100 for x in payments)
detail = client.get(f"/api/orders/{order_id}", headers=headers).json()
assert detail["document"]["status"] == "completed"
assert detail["items"][0]["discount_percent"] == 5
print("ALL_NEXT_0_8_DOCUMENT_ENGINE_TESTS_PASSED")

from pathlib import Path
import os, shutil, sqlite3, tempfile

TMP=Path(tempfile.mkdtemp(prefix="yhp_v26_real_"))
DB=TMP/"test.db"
shutil.copy2(Path(__file__).with_name("veriler.db"),DB)
os.environ["DATABASE_URL"]=f"sqlite:///{DB.as_posix()}"

from fastapi.testclient import TestClient
from app.main import app

client=TestClient(app)
login=client.post("/api/auth/login",json={"username":"admin","password":"admin123"})
assert login.status_code==200,login.text
payload=login.json()
headers={"Authorization":"Bearer "+payload["access_token"],"X-Company-ID":str(payload["companies"][0]["id"])}
if payload["user"].get("must_change_password"):
    changed=client.post("/api/auth/change-password",headers=headers,json={"current_password":"admin123","new_password":"V27-Guvenli-Sifre!2026"})
    assert changed.status_code==200,changed.text

def ok(response):
    assert response.status_code<300,(response.status_code,response.text)
    return response.json() if response.content else None

customer=ok(client.post("/api/customers",headers=headers,json={
    "name":"V26 Gerçek Müşteri","opening_balance":100,"risk_limit":200,"payment_term_days":30,"is_active":True
}))
supplier=ok(client.post("/api/suppliers",headers=headers,json={
    "name":"V26 Gerçek Tedarikçi","opening_balance":50,"risk_limit":100,"payment_term_days":30,"is_active":True
}))
products=ok(client.get("/api/products?limit=1",headers=headers))
assert products
product=products[0]

sale=ok(client.post("/api/orders",headers=headers,json={
    "entity_id":customer["id"],"transaction_date":"2026-07-11","document_no":"V26-SALE",
    "due_date":"2026-07-12","status":"completed","payment_method":"credit","paid_amount":0,
    "items":[{"product_id":product["id"],"quantity":1,"unit_price":120,"vat_rate":20,"discount_percent":0}]
}))
purchase=ok(client.post("/api/purchases",headers=headers,json={
    "entity_id":supplier["id"],"transaction_date":"2026-07-11","document_no":"V26-PURCHASE",
    "due_date":"2026-07-12","status":"completed","payment_method":"credit","paid_amount":0,
    "items":[{"product_id":product["id"],"quantity":1,"unit_price":60,"vat_rate":20,"discount_percent":0}]
}))

c_before=ok(client.get(f"/api/customers/{customer['id']}",headers=headers))
s_before=ok(client.get(f"/api/suppliers/{supplier['id']}",headers=headers))
for body in (c_before,s_before):
    assert "summary" in body
    for key in ("current_balance","overdue_amount","risk_limit","risk_exceeded","risk_usage_percent","last_activity"):
        assert key in body["summary"],(key,body)

assert round(float(c_before["summary"]["current_balance"]),2)==220.00,c_before["summary"]
assert round(float(c_before["summary"]["risk_usage_percent"]),1)==110.0,c_before["summary"]
assert c_before["summary"]["risk_exceeded"] is True
assert round(float(s_before["summary"]["current_balance"]),2)==110.00,s_before["summary"]
assert round(float(s_before["summary"]["risk_usage_percent"]),1)==110.0,s_before["summary"]
assert s_before["summary"]["risk_exceeded"] is True

ok(client.post("/api/payments",headers=headers,json={
    "entity_type":"customer","entity_id":customer["id"],"amount":70,"payment_date":"2026-07-13","payment_method":"credit","note":"V26 tahsilat"
}))
ok(client.post("/api/payments",headers=headers,json={
    "entity_type":"supplier","entity_id":supplier["id"],"amount":20,"payment_date":"2026-07-13","payment_method":"credit","note":"V26 ödeme"
}))

c_after=ok(client.get(f"/api/customers/{customer['id']}",headers=headers))
s_after=ok(client.get(f"/api/suppliers/{supplier['id']}",headers=headers))
assert round(float(c_after["summary"]["current_balance"]),2)==150.00,c_after["summary"]
assert round(float(c_after["summary"]["risk_usage_percent"]),1)==75.0,c_after["summary"]
assert c_after["summary"]["risk_exceeded"] is False
assert round(float(s_after["summary"]["current_balance"]),2)==90.00,s_after["summary"]
assert round(float(s_after["summary"]["risk_usage_percent"]),1)==90.0,s_after["summary"]
assert s_after["summary"]["risk_exceeded"] is False
assert c_after["summary"]["last_activity"]=="2026-07-11"
assert s_after["summary"]["last_activity"]=="2026-07-11"

con=sqlite3.connect(DB)
assert con.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
con.close()
print("ALL_V2_6_QUICK_ACTIONS_TESTS_PASSED")

from pathlib import Path
import os, shutil, sqlite3, tempfile

TMP=Path(tempfile.mkdtemp(prefix="yhp_v27_tenant_"))
DB=TMP/"test.db"
shutil.copy2(Path(__file__).with_name("veriler.db"),DB)
os.environ["DATABASE_URL"]=f"sqlite:///{DB.as_posix()}"

from fastapi.testclient import TestClient
from app.main import app

c=TestClient(app)
login=c.post("/api/auth/login",json={"username":"admin","password":"admin123"})
assert login.status_code==200,login.text
body=login.json(); token=body["access_token"]; company_a=body["companies"][0]["id"]
h_a={"Authorization":"Bearer "+token,"X-Company-ID":str(company_a)}
if body["user"].get("must_change_password"):
    r=c.post("/api/auth/change-password",headers=h_a,json={"current_password":"admin123","new_password":"V27-Tenant-Admin!2026"})
    assert r.status_code==200,r.text

# Create a second company and a user that belongs only to it.
company_b=c.post("/api/companies",headers=h_a,json={"name":"Firma B Güvenlik"}).json()["id"]
h_b={**h_a,"X-Company-ID":str(company_b)}
user_b=c.post("/api/users",headers=h_b,json={"username":"firma_b_user","display_name":"Firma B User","password":"V27-user-pass!","role":"rapor"})
assert user_b.status_code==201,user_b.text
uid_b=user_b.json()["id"]

users_a=c.get("/api/users",headers=h_a)
assert users_a.status_code==200,users_a.text
assert not any(x["id"]==uid_b for x in users_a.json())
users_b=c.get("/api/users",headers=h_b)
assert users_b.status_code==200 and any(x["id"]==uid_b for x in users_b.json())

forbidden=c.patch(f"/api/users/{uid_b}/status?is_active=false",headers=h_a)
assert forbidden.status_code==404,forbidden.text
allowed=c.patch(f"/api/users/{uid_b}/status?is_active=false",headers=h_b)
assert allowed.status_code==200,allowed.text

# Generate audit events in both companies and verify isolation.
c.post("/api/customers",headers=h_a,json={"name":"Firma A Audit Müşteri"})
c.post("/api/customers",headers=h_b,json={"name":"Firma B Audit Müşteri"})
audit_a=c.get("/api/audit",headers=h_a)
audit_b=c.get("/api/audit",headers=h_b)
assert audit_a.status_code==200 and audit_b.status_code==200
assert audit_a.json() and audit_b.json()
assert all(x.get("company_id")==company_a for x in audit_a.json())
assert all(x.get("company_id")==company_b for x in audit_b.json())

# Brute-force: fifth bad attempt locks the username+IP combination.
for _ in range(5):
    bad=c.post("/api/auth/login",json={"username":"firma_b_user","password":"yanlis"})
    assert bad.status_code==401,bad.text
locked=c.post("/api/auth/login",json={"username":"firma_b_user","password":"V27-user-pass!"})
assert locked.status_code==429,locked.text

con=sqlite3.connect(DB)
assert con.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
assert "company_id" in {r[1] for r in con.execute("PRAGMA table_info(security_audit_logs)")}
con.close()
print("ALL_V2_7_TENANT_SECURITY_TESTS_PASSED")

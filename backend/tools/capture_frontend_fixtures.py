#!/usr/bin/env python3
"""Gerçek API yanıtlarını frontend test fixture'ları olarak yakala.

Bugünkü prod POS çökmesi, frontend testlerinin ELLE yazılmış mock'larla
(string para değerleri) çalışması yüzünden CI'dan kaçtı: gerçek API sayı
gönderiyordu. Bu script mock yerine GERÇEĞİ üretir — taze bir veritabanına
karşı uygulamayı ayağa kaldırır, POS yüzeyini gerçekten çağırır ve yanıt
gövdelerini olduğu gibi ``frontend/src/test/fixtures/pos-api.json`` içine
yazar. Vitest bu dosyayı mock verisi olarak tüketir; backend'in JSON şekli
değişirse fixture'lar da değişir ve CI drift kontrolü (git diff) kırmızı yanar.

Deterministiktir: sabit tohum verisi, taze SQLite, sıralı anahtarlar.

Kullanım (backend/ dizininden):
    python tools/capture_frontend_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND.parent / "frontend" / "src" / "test" / "fixtures" / "pos-api.json"


def capture() -> dict:
    sys.path.insert(0, str(BACKEND))
    tmpdir = tempfile.mkdtemp(prefix="fixture-capture-")
    db_path = Path(tmpdir) / "fixtures.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert login.status_code == 200, login.text
        body = login.json()
        headers = {
            "Authorization": "Bearer " + body["access_token"],
            "X-Company-ID": str(body["companies"][0]["id"]),
        }
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": "admin123", "new_password": "Fixture123!Gate"},
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]

        product = client.post(
            "/api/products",
            headers=headers,
            json={
                "name": "Yağ Filtresi",
                "product_code": "YF-8",
                "barcode": "8690000000086",
                "purchase_price": "9.00",
                "sale_price": "18.00",
                "vat_rate": 20,
                "stock": "8",
                "unit": "Adet",
            },
        )
        assert product.status_code == 201, product.text
        product_id = product.json()["id"]

        lookup = client.get(
            "/api/pos/lookup", headers=headers, params={"barcode": "8690000000086"}
        )
        assert lookup.status_code == 200, lookup.text

        sale = client.post(
            "/api/pos/sale",
            headers={**headers, "Idempotency-Key": "fixture-pos-sale"},
            json={
                "items": [
                    {"product_id": product_id, "quantity": "2", "unit_price": "18.00"}
                ],
                "payment_type": "cash",
            },
        )
        assert sale.status_code == 201, sale.text

        quick_pick = client.get("/api/quick-pick", headers=headers)
        assert quick_pick.status_code == 200, quick_pick.text
        assert quick_pick.json(), "quick-pick boş — fixture anlamsız olur"

        return {
            "_comment": (
                "OTOMATİK ÜRETİLİR — elle düzenlemeyin. Kaynak: "
                "backend/tools/capture_frontend_fixtures.py (gerçek API yanıtları)."
            ),
            "pos_lookup": lookup.json(),
            "pos_sale": sale.json(),
            "quick_pick": quick_pick.json(),
        }


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    payload = capture()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Fixture yazıldı: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

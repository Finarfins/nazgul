#!/usr/bin/env python3
"""E2E backend sunucusu — taze SQLite ile uvicorn'u ayağa kaldırır.

Playwright'ın webServer kancası tarafından çalıştırılır. Her koşuda geçici bir
veritabanı oluşturur (bootstrap admin + migrations import sırasında uygulanır),
sonra uygulamayı 127.0.0.1:5599 üzerinde süreç-içi çalıştırır ki Playwright
süreci sonlandırdığında sunucu da ölsün. Frontend build (frontend/dist) yoksa
SPA rotaları servis edilemez; erken ve açık bir hatayla çıkar.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
BACKEND = FRONTEND.parent / "backend"

if not (FRONTEND / "dist" / "index.html").is_file():
    sys.stderr.write(
        "HATA: frontend/dist yok. E2E'den önce `npm run build` çalıştırın.\n"
    )
    raise SystemExit(2)

# Test-only mutation worker is copied only into the ephemeral E2E build tree.
# It is never part of public/ or a production image.
mutation_worker = FRONTEND / "e2e" / "fixtures" / "sw-broken-test.js"
mutation_target = FRONTEND / "dist" / "saha" / "sw-broken-test.js"
mutation_target.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(mutation_worker, mutation_target)

tmpdir = tempfile.mkdtemp(prefix="e2e-db-")
db_path = Path(tmpdir) / "e2e.db"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
os.environ.setdefault("COOKIE_SECURE", "false")
# Tahsis motoru BAYRAĞI BURADA ZORLANMAZ. Üretim varsayılanı kapalıdır ve kapı
# kullanıcıların sahip OLMADIĞI bir yapılandırmayı sertifikalamamalıdır. Bayrak
# yalnız dış ortamdan gelirse açılır; böylece iki durum da ölçülebilir:
#   (varsayılan)                          -> motor KAPALI
#   PAYMENT_ALLOCATION_ENGINE_ENABLED=true -> motor AÇIK

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

import uvicorn  # noqa: E402  (env hazırlandıktan sonra)

uvicorn.run("app.main:app", host="127.0.0.1", port=5599, log_level="warning")

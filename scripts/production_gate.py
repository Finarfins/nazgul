from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env.production", "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml"]


def run(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [*COMPOSE, *args]
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, capture_output=capture)


def validate_env() -> None:
    env_file = ROOT / ".env.production"
    if not env_file.exists():
        raise SystemExit(".env.production bulunamadi; .env.production.example dosyasini kopyalayip doldurun.")
    values: dict[str, str] = {}
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    required = ("APP_DOMAIN", "MARKETING_DOMAIN", "ACME_EMAIL", "POSTGRES_PASSWORD", "DATABASE_URL")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise SystemExit(f"Eksik production ayarlari: {', '.join(missing)}")
    password = values["POSTGRES_PASSWORD"]
    if "CHANGE" in password.upper() or len(password) < 24:
        raise SystemExit("POSTGRES_PASSWORD en az 24 karakterlik rastgele bir deger olmali.")
    if password not in values["DATABASE_URL"]:
        print("UYARI: DATABASE_URL farkli/URL-encoded parola kullaniyor; tutarliligi elle dogrulayin.")


def wait_for_ready(timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run("exec", "-T", "app", "python", "-c", "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5050/api/ready', timeout=3).read().decode())", check=False, capture=True)
        if result.returncode == 0:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            if payload.get("database") == "ok":
                print("READY:", json.dumps(payload, ensure_ascii=False))
                return
        time.sleep(3)
    run("logs", "--tail", "200", "app", check=False)
    raise SystemExit("Uygulama readiness zaman asimina ugradi.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Yerel Hesap Pro X production gate")
    parser.add_argument("--keep", action="store_true", help="Test sonunda stack'i calisir birak")
    args = parser.parse_args()
    validate_env()
    try:
        run("config", "--quiet")
        run("build", "--pull", "app")
        run("up", "-d", "db", "app")
        wait_for_ready()
        run("--profile", "gate", "run", "--rm", "gate")
        print("Ilk baslatma gecti; uygulama yeniden baslatiliyor...")
        run("restart", "app")
        wait_for_ready()
        run("up", "-d", "proxy")
        run("ps")
        print("PRODUCTION GATE PASSED")
        return 0
    finally:
        if not args.keep:
            run("down", "--remove-orphans", check=False)


if __name__ == "__main__":
    raise SystemExit(main())

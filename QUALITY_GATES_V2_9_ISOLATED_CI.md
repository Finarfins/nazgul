# V2.9 Isolated CI Quality Gates

## Backend local gate

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m compileall -q app alembic run_isolated_tests.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python run_isolated_tests.py --timeout 180
```

Beklenen: tüm aktif test dosyaları PASS; PostgreSQL ortam değişkeni olmayan entegrasyon testleri SKIP.

## PostgreSQL 16 gate

GitHub Actions `backend-postgresql` işi aşağıdaki dosyaları gerçek PostgreSQL 16 üzerinde çalıştırır:

- `test_workflow_postgresql.py`
- `test_transactions_postgresql.py`
- `test_postgresql_app_smoke.py`
- `test_numeric_migration_postgresql.py`

## Frontend gate

```bash
cd frontend
npm ci
npm run build
npm test -- --run
```

## Container gate

Backend, PostgreSQL ve frontend kapıları geçmeden Docker imajı oluşturulmaz.

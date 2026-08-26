# V2.9 Bootstrap + Container Security Checkpoint

## Uygulanan değişiklikler

- PostgreSQL advisory lock artık yalnızca Alembic'i değil, tüm `initialize_*` + Alembic başlangıç fazını kapsıyor.
- `run_database_migrations(..., acquire_lock=False)` yalnızca dış bootstrap kilidi tutulurken kullanılabiliyor.
- CSP, COOP ve CORP güvenlik başlıkları eklendi.
- Docker Compose varsayılan/sabit PostgreSQL parolasını kaldırdı; `DATABASE_URL` ve `POSTGRES_PASSWORD` dış ortamdan zorunlu.
- Uygulama container'ı read-only root filesystem, `cap_drop: ALL` ve `no-new-privileges` ile sertleştirildi.
- `.env` ve türevlerinin Docker build context'ine girmesi engellendi.
- Gerçek PostgreSQL 16 için legacy floating-point → NUMERIC mutabakat testi eklendi.
- PostgreSQL final doğrulama prosedürü `POSTGRESQL_FINAL_GATE.md` içinde belgelendi.

## Yerelde gerçekten çalıştırılan kontroller

- Yeni bootstrap/container testleri: 4/4 geçti.
- Operational hardening + runtime migration testleri: 9/9 geçti.
- Diğer kritik backend testleri üç izole grupta: 37/37 geçti.
- PostgreSQL gerektiren testler: 5 adet atlandı (sunucu yok).
- Frontend Vitest: 6/6 geçti.
- TypeScript + Vite production build: geçti.
- Python compile: geçti.

## Açık kapı

Gerçek PostgreSQL 16 sunucusunda migration, uygulama smoke, numeric mutabakat, backup/restore ve eşzamanlılık kapısı henüz çalıştırılmadı. Bu nedenle production-ready iddiası yoktur.

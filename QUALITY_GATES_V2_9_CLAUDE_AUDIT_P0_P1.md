# Quality Gates — V2.9 Claude Audit P0/P1

## Gerçekten çalıştırılan kontroller

- `python -m compileall -q app` — geçti
- `pytest -q test_v2_9_claude_audit_fixes.py test_v2_9_role_security.py test_v2_9_decimal_contract.py test_v2_9_tenant_stock_security.py test_workflow_documents.py` — 12 geçti
- `pytest -q test_v2_9_clean_install.py` — 2 geçti
- `pytest -q test_v2_9_finance_decimal.py` — 1 geçti
- `pytest -q test_v2_9_claude_audit_fixes.py test_v2_9_decimal_contract.py` — 9 geçti (son Excel parser değişikliğinden sonra)
- Son birleşik hedefli backend kapısı — 15 geçti
- `npm run build` — TypeScript + Vite production build geçti
- `npm test -- --run` — 3 test dosyası, 6 test geçti

## Dinamik olarak doğrulanan yeni senaryolar

- Şifre değişikliği yapılmadan korumalı API erişimi 403
- Şifre değişikliğinden sonra normal API erişimi
- Global indirimli satışta KDV matrahı ve veri-at-rest mutabakatı
- Global indirimli workflow teklifinde mutabakat
- Yönetici → yönetici rol çoğaltma engeli
- Yönetici → admin pasifleştirme engeli
- Rapor rolünün kullanıcı, audit ve finans API'lerine doğrudan erişememesi
- TR ve US sayı biçimleri; geçersiz ve belirsiz sayı reddi
- Depo toplamından atomik ürün stok özeti güncellemesi

## Çalıştırılmayan / tamamlanmayan kapılar

- Gerçek PostgreSQL 16 entegrasyon paketi bu çalışma turunda yeniden çalıştırılmadı.
- Karantinadaki 25 legacy test topluca yeniden etkinleştirilmedi.
- Tüm aktif test dosyalarını tek pytest process'inde çalıştırma denemesinde bilinen
  kapanış beklemesi tekrar görüldü; dosya bazlı CI matrix ve timeout korunuyor.

Bu nedenle ürün hâlâ `production-ready` olarak işaretlenmemelidir.

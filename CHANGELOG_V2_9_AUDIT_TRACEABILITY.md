# V2.9 Audit Traceability Checkpoint

## Tamamlananlar

- `security_audit_logs` kayıtlarına request correlation alanları eklendi.
- Başarılı mutasyonların yanında middleware seviyesinde reddedilen oturum, CSRF, yetki ve firma erişim girişimleri de kaydediliyor.
- Her kayıt `request_id`, `outcome`, `duration_ms`, `auth_source`, `user_agent` ve sınırlı `failure_reason` içeriyor.
- Beklenmeyen API hataları request ID ile uygulama loguna ve audit tablosuna error sonucu olarak yazılıyor.
- API yanıtlarındaki `X-Request-ID` ile audit kaydı birebir eşleştirilebiliyor.
- Yeni Alembic migration: `20260714_0005_audit_traceability`.
- Migration hem eski 0004 veritabanında hem de güncel metadata kullanan temiz baseline kurulumunda güvenli/idempotent çalışıyor.

## Güvenlik sınırları

- Audit sistemine parola, token, istek gövdesi veya hassas finansal veri yazılmıyor.
- Audit yazma hatası ticari işlemi geri almıyor; hata request ID ile yüksek görünürlüklü uygulama loguna düşüyor.
- Before/after entity diff henüz genel bir altyapı olarak eklenmedi; bu sonraki domain audit sprintinin konusudur.

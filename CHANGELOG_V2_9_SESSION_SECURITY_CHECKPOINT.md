# V2.9 Session Security Checkpoint

Tarih: 14 Temmuz 2026

Bu checkpoint, tarayıcı oturum token'ının `localStorage` içinde saklanması riskini
kapatır ve kısa ömürlü erişim cookie'si, döndürülen refresh token ve CSRF koruması
ekler.

## HttpOnly cookie oturumu

- Erişim token'ı tarayıcıda `yhp_access_token` adlı HttpOnly cookie ile taşınır.
- Refresh token `yhp_refresh_token` adlı HttpOnly cookie içinde ve yalnızca
  `/api/auth` yolu için gönderilir.
- CSRF double-submit token'ı `yhp_csrf_token` cookie'si ile taşınır ve güvenli
  olmayan isteklerde `X-CSRF-Token` başlığıyla eşleşmek zorundadır.
- Frontend artık erişim token'ını veya kullanıcı kaydını Web Storage'a yazmaz.
- Eski sürümlerden kalan `yhp_token` ve `yhp_user` kayıtları açılışta temizlenir.

## Refresh token rotasyonu

- Yeni `auth_refresh_tokens` tablosu ve Alembic migration'ı eklendi.
- Her refresh çağrısı mevcut token'ı tüketir ve aynı oturum ailesinde yeni token üretir.
- Daha önce tüketilmiş bir refresh token tekrar kullanılırsa replay kabul edilir ve
  aynı ailedeki tüm aktif refresh token'lar iptal edilir.
- Token'ların ham değeri veritabanında tutulmaz; yalnızca SHA-256 özeti saklanır.
- Kullanıcı pasife alınırsa access ve refresh doğrulaması başarısız olur.
- Şifre değişikliğinde uzun ömürlü refresh oturumları iptal edilip yeni aile oluşturulur.

## Geriye uyumluluk

- Mevcut entegrasyonların kırılmaması için login ve refresh yanıtları kısa ömürlü
  `access_token` alanını döndürmeye devam eder.
- `Authorization: Bearer ...` kullanan API istemcileri çalışmaya devam eder.
- Bearer isteklerinde tarayıcının otomatik eklediği ambient cookie kullanılmadığı için
  CSRF zorunluluğu uygulanmaz.

## Frontend yenileme davranışı

- Axios `withCredentials` ile cookie oturumunu kullanır.
- Bir istek 401 döndürürse aynı anda yalnızca tek refresh isteği çalışır.
- Refresh başarılı olursa bekleyen istek yeni access cookie ile bir kez tekrar edilir.
- Refresh başarısızsa tarayıcı oturumu temizlenir ve giriş ekranına yönlendirilir.

## Güvenli dağıtım ayarları

Yeni ortam değişkenleri:

- `ACCESS_TOKEN_MINUTES` — varsayılan 15
- `REFRESH_TOKEN_DAYS` — varsayılan 14
- `COOKIE_SECURE` — yerel HTTP için false; HTTPS yayında true olmalıdır
- `COOKIE_SAMESITE` — varsayılan `lax`
- `COOKIE_DOMAIN` — varsayılan boş/host-only

`COOKIE_SAMESITE=none`, `COOKIE_SECURE=true` olmadan kabul edilmez. Secure cookie
modunda HSTS başlığı da eklenir.

## Migration

Yeni Alembic head: `20260714_0004_rotating_sessions`

## Açık üretim kapıları

- Gerçek PostgreSQL 16 temiz kurulum, upgrade, eşzamanlılık ve mutabakat provası
- Karantinadaki legacy testlerin modern fixture yapısına taşınması
- Runtime `initialize_*` DDL ile Alembic şema yönetiminin tek kaynağa indirilmesi
- HTTPS reverse proxy üzerinde Secure cookie/HSTS canlı provası

Bu nedenle ürün hâlâ `production-ready` olarak işaretlenmemiştir.

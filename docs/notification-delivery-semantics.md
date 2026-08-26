# Bildirim teslim semantiği

Bildirim outbox'ı **at-least-once** teslim semantiği sağlar; exactly-once
garantisi vermez. Bir worker dış sağlayıcıya başarılı gönderim yaptıktan sonra
sonucu kaydetmeden durabilir. Dış sağlayıcı idempotency desteklemediğinde bu
durumu exactly-once yapmak mümkün değildir. Bu, seam'in bilinçli tasarım
sınırıdır.

Gerçek bir notification adapter'ı aşağıdaki sözleşmelere uymalıdır:

- `provider_idempotency_key`, dış API'ye `Idempotency-Key` HTTP başlığı olarak
  aynen iletilmelidir. Adapter ancak bu anahtarı gerçekten kullandığında
  `supports_idempotency = True` beyan edebilir.
- Provider çağrısının timeout'u outbox lease süresinden kısa olmalıdır. Mevcut
  lease süresi `_LEASE_MINUTES = 5` olduğundan adapter timeout'u beş dakikadan
  kısa seçilmelidir.
- Ham provider exception metni kullanıcıya veya outbox `last_error` alanına
  taşınmamalıdır. Ayrıntılar yalnız sunucu loglarında tutulur.

Native/on-prem kurulumlar için minimum veritabanı sürümleri:

- SQLite 3.35 veya üzeri (`ON CONFLICT ... RETURNING` desteği)
- PostgreSQL 16 veya üzeri

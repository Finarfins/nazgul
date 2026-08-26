// Tek kaynak: parola politikası. Backend karşılığı routers/auth.py
// PASSWORD_MIN_LENGTH sabitidir; ikisi birlikte değiştirilmelidir.
//
// Bilinçli politika kararı: yalnız minimum uzunluk aranır. Büyük/küçük harf,
// rakam ve özel karakter şartları kaldırılmıştır. Online kaba kuvvete karşı
// koruma mevcut giriş hız sınırıdır (5 hatalı deneme → kilit).
export const PASSWORD_MIN_LENGTH = 6;

export const PASSWORD_RULE_LABEL = `En az ${PASSWORD_MIN_LENGTH} karakter`;

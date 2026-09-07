"""PUSH CİHAZ DEFTERİ: bir kullanıcının bildirim alabilen cihazları (5.4c).

Konu: bu göç ŞEMAYI kurar; davranış `app/push_devices.py`de, uçlar
`app/routers/push.py`de, gönderim kanalı `app/notifications/`dedir.

--- ÖLÇÜLEN EKSİK: MOBİL OTURUM VAR, MOBİL CİHAZ YOK ---------------------

5.4a mobil oturumu getirdi: `X-Client-Kind: mobile` başlığı ve CİHAZ BAŞINA
bir refresh ailesi (`app/routers/auth.py`, `app/auth.py`). Yani sunucu bir
kullanıcının KAÇ oturumu olduğunu biliyor.

Bilmediği şey ölçüldü: `notifications` tablosunun `channel` sütununa depoda
YALNIZ üç değer yazılıyor (`SMS`, `WHATSAPP`, `EMAIL` — `app/notifications/
rules.py`, `templates.py`, `routers/notifications.py`), yani bir bildirimin
gidebileceği TEK yer bir telefon numarası ya da e-posta adresidir. Kullanıcının
CİHAZINA giden bir yol YOKTU ve olamazdı: bir cihaz jetonunu (FCM/APNs token)
saklayan hiçbir tablo yoktu — ölçüldü, `alembic/versions/` altında `token`
sütunu taşıyan tek tablo `auth_tokens`/`auth_refresh_tokens`tır ve o ikisi
OTURUM jetonudur, cihaz jetonu değil.

Bu göç o tabloyu açıyor.

--- NEDEN `session_family_id` YABANCI ANAHTAR DEĞİL — ÖLÇÜLDÜ ------------

Sütun VAR ama FK TAŞIMIYOR ve bu bir ihmal değil, ölçülmüş bir olgudur:
5.4a'nın "refresh ailesi" bir TABLO DEĞİLDİR. `app/auth.py`de aile
`auth_refresh_tokens.family_id` (String(64)) SÜTUNUDUR ve o sütun TEKİL
DEĞİLDİR — bir aile, aynı `family_id`yi taşıyan BİRDEN ÇOK satırdır (rotasyon
her yenilemede yeni bir satır yazar). Yabancı anahtarın hedefi tekil bir
sütun olmak ZORUNDA olduğu için `REFERENCES auth_refresh_tokens(family_id)`
KURULAMAZ; kurulabilseydi de yanlış olurdu: aile satırları rotasyonla
DEĞİŞİR, cihaz kaydı ise kalıcıdır.

Sütun yine de saklanıyor çünkü TEŞHİS DEĞERİ VAR: "bu cihaz hangi mobil
oturumla kaydoldu" sorusunun cevabı, bir jeton çalındığında hangi cihazın
düşürüleceğini gösterir. NULL kabul ediyor çünkü web istemcisi (tarayıcı
push'u) bir refresh ailesi taşımayabilir.

--- TEKİLLİK `(company_id, token)` — NEDEN KULLANICI DEĞİL --------------

Bir cihaz jetonu FİZİKSEL BİR CİHAZI adlandırır ve o cihazda AYNI ANDA TEK
kullanıcı oturur. Tekili `(company_id, user_id, token)` yapsaydık, bir
telefonu ikinci bir kullanıcıya devreden firma İKİ satır elde ederdi ve
gönderim o jetona İKİ KEZ — eski sahibinin bildirimi dahil — giderdi. Kapsam
`(company_id, token)`tır: aynı jeton ikinci kez kaydedildiğinde satır
GÜNCELLENİR (`user_id` dahil), yeni satır DOĞMAZ.

FİRMA TEKİLİN İÇİNDE ve bu ZORUNLU: aynı jeton İKİ FARKLI firmada AYRI
satırlardır. Kiracı sınırı burada da geçerli — bir firmanın cihaz defteri
diğerinin varlığını görmemelidir; jetonu kürese tekil yapmak, aynı telefonu
iki firmada kullanan bir kişinin ikinci kaydını BİRİNCİYİ EZEREK yapardı.

--- `platform` KAPALI KÜME, ÜÇ DEĞER ------------------------------------

`('android','ios','web')`. Küme KAPALI ve CHECK ile veritabanı seviyesinde
ısırıyor: platform gönderim adaptörünü SEÇER (FCM mi APNs mi WebPush mu) ve
tanınmayan bir değer, o satırı HİÇBİR adaptörün almadığı sessiz bir ölü
satır yapardı.

--- BU GÖÇÜN YAPMADIĞI: GERÇEK TESLİMAT — ÖLÇÜLMEDİ ---------------------

Bu turda FCM/APNs kimlik bilgisi YOKTUR ve gerçek bir push GÖNDERİLMEMİŞTİR.
`app/notifications/provider.py`ye eklenen `PushNotificationProvider` ağa
ÇIKMAZ; denemeyi kaydeder ve `SIMULATED` döner. Gerçek teslimatın
davranışı (jeton geçersizleşmesi, `NotRegistered` cevabı, cihazın
otomatik pasifleştirilmesi) ÖLÇÜLMEDİ ve bu göçün iddiası DEĞİLDİR.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260909_0077"
down_revision = "20260909_0076"
branch_labels = None
depends_on = None

DEFTER = "push_devices"

#: Kapalı küme. Gerekçe başlıkta.
PLATFORMLAR = ("android", "ios", "web")

PLATFORM_CHECK = "ck_push_devices_platform"
TEKIL_JETON = "uq_push_devices_company_token"
FIRMA_KIMLIK = "uq_push_devices_company_id"
TARAMA_INDEKS = "ix_push_devices_company_user"


def _platform_check() -> str:
    degerler = ",".join("'" + p + "'" for p in PLATFORMLAR)
    return "platform IN (%s)" % degerler


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # TABLO BU GÖÇLE DOĞUYOR ve açılış DDL'inde BİLDİRİLMİYOR (kapı:
    # `tests/test_54c_push_devices.py::test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR`).
    # 0072'de ölçülen kusur — açılış tabloyu göçten önce kurar, göç onu VAR
    # bulup atlar — bu yüzden burada ÜRETİLEMİYOR.
    if DEFTER in set(inspector.get_table_names()):
        return

    op.create_table(
        DEFTER,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        # ÇIPLAK yabancı anahtar ve bu bir istisna DEĞİL: `app_users`ta
        # `company_id` sütunu YOKTUR, yani bileşik anahtar KURULAMAZ.
        # (0076'nın `user_id` sütunuyla BİREBİR aynı gerekçe.)
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        # 512: FCM kayıt jetonu bugün ~163 karakter, APNs cihaz jetonu 64
        # onaltılık karakter; ikisi de sağlayıcı tarafından UZATILABİLİR ve
        # jetonu KIRPMAK, cihazı sessizce erişilemez yapardı. 512 sınırı
        # `uq_push_devices_company_token` indeksine giriyor ve PostgreSQL'in
        # B-tree satır sınırının (~2704 bayt) çok altında.
        sa.Column("token", sa.String(length=512), nullable=False),
        # YABANCI ANAHTAR YOK — gerekçe başlıkta (aile bir TABLO değil,
        # `auth_refresh_tokens.family_id` TEKİL OLMAYAN bir sütundur).
        # Genişlik o sütunla AYNI: String(64).
        sa.Column("session_family_id", sa.String(length=64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_platform_check(), name=PLATFORM_CHECK),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
        # CİHAZIN KİMLİĞİ — gerekçe başlıkta.
        sa.UniqueConstraint("company_id", "token", name=TEKIL_JETON),
        # 0062'nin kuralı: bileşik yabancı anahtar HEDEFİ olabilmesi için.
        sa.UniqueConstraint("company_id", "id", name=FIRMA_KIMLIK),
    )
    # GÖNDERİM YOLUNUN TARAMASI: "bu kullanıcının etkin cihazları" sorgusu
    # `(company_id, user_id)` ile daralıyor ve `is_active` süzgeci satır
    # üzerinde uygulanıyor. `is_active`i indekse KOYMADIK: iki değerli bir
    # sütun B-tree'de seçicilik katmaz, yalnız her yazımın maliyetini artırır.
    op.create_index(TARAMA_INDEKS, DEFTER, ["company_id", "user_id"])


def downgrade() -> None:
    """Simetrik ve KOŞULLU. Tabloyu düşürmek VERİ KAYBIDIR ve bilinçlidir.

    `push_devices` bu göçle DOĞDU; düşürmek göçten sonra kaydedilmiş her cihaz
    jetonunu siler. Kaybın anlamı DAR: silinen şey iş verisi DEĞİL, "bu
    kullanıcıya hangi cihazdan ulaşılabilir" bilgisidir. Geri alma sonrası
    istemciler jetonlarını YENİDEN kaydeder (kayıt zaten her açılışta
    tekrarlanan bir upsert'tür) — ve o ana kadar push kanalı hedefsiz kalır,
    yani hiçbir satır kuyruğa GİRMEZ. Bu, göçten ÖNCEKİ davranışın ta kendisi.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if DEFTER not in set(inspector.get_table_names()):
        return
    mevcut = {i["name"] for i in inspector.get_indexes(DEFTER)}
    if TARAMA_INDEKS in mevcut:
        op.drop_index(TARAMA_INDEKS, table_name=DEFTER)
    op.drop_table(DEFTER)

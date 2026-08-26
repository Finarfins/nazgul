"""Denetim kaydı: firmasız satır YALNIZ gerçek kimlik-öncesi olaya kalsın.

ÖLÇÜLEN KUSUR (taze şema, her iki lehçe):

``security_audit_logs.company_id`` 93 kiracı tablosu içinde tek NULL kabul eden
kiracı sütunuydu. NULL hiçbir firmaya ait değildir ve ``company_id = ?`` süzen
her okumanın dışına düşer — hem de tek işi "hiçbir şey görünmez kalmasın" olan
tabloda.

Taze şemada üç sıradan istek ölçüldü, ÜÇÜ DE firmasız yazıldı::

    cid=None uid=None  401 /api/auth/login
    cid=None uid=None  200 /api/auth/forgot-password
    cid=None uid=None  401 /api/customers          AUTH_REQUIRED

NEDEN ``NOT NULL`` DEĞİL. İlk refleks sütunu NOT NULL yapmaktı; ÖLÇÜLDÜ ve
yanlış olduğu görüldü. Yazıcı (``main.py::_write_security_audit``) istisnayı
yutuyordu; kısıt insert'i reddettiğinde istek normal dönüyor ve satır sessizce
kayboluyordu::

    failed login  -> HTTP 401   (istemci normal ret görüyor)
    unauth write  -> HTTP 401   (istemci normal ret görüyor)
    security_audit_logs'a gerçekten yazılan satır: 0

Yani çıplak NOT NULL, denetim izini tam da bir saldırganın ürettiği olaylar
için YOK EDİYORDU. Kimlik-öncesi POST'ların denetlenmesi kasıtlıdır ve
``test_v2_9_public_auth_audit_contract`` tarafından zaten çivilenmiştir.

YAPILAN: sütun NULL kabul etmeye devam eder, ama NULL'un ANLAMI kısıtlanır.
Firmasız bir satır ancak GERÇEKTEN kimlik-öncesi bir olaysa meşrudur:

    company_id IS NOT NULL OR (user_id IS NULL AND username IS NULL)

Kimliği olan ama firması olmayan satır artık REDDEDİLİR. Kuralın "hiçbir firma
İSTENMEMİŞ olmalı" yarısı şemada değil YAZICIDA durur: istenen firma artık
çözüm başarısız olsa bile kaydedilir (COMPANY_ACCESS_DENIED olayı, sınırı
yoklanan firmanın izine girer). Şema yalnız saklanan sütunlar arasındaki
ilişkiyi zorlayabilir; istenen-ama-çözülmemiş firma saklanan bir sütun değildir.

ESKİ SATIRLAR SİLİNMEZ. Üretimde kimliği olup firması olmayan satırlar
bulunabilir; onlar bu göçten ÖNCEKİ yazıcının ürünüdür. Bir denetim satırını
silmek, kapatmaya çalıştığımız kusurun daha kötüsüdür; üyelikten firma
"tamamlamak" ise #57'nin kaldırdığı sessiz KİRACI UYDURMASI'nın ta kendisi
olurdu (hiçbir firma belirtmemiş bir isteğe varsayılan seçmek).

Bu yüzden lehçeler AYRI davranır ve fark BİLEREK vardır:

* PostgreSQL (üretim): ``NOT VALID``. Kısıt YENİ yazımlarda tam olarak
  zorlanır, geçmiş satırlar taranmaz ve OLDUĞU GİBİ KALIR. İleride
  ``VALIDATE CONSTRAINT`` çalıştırmak ayrı ve bilinçli bir karardır.
* SQLite (geliştirme/test): CHECK ile tablo yeniden kurulur. SQLite'ta
  ``NOT VALID`` yoktur ve yeniden kurulumdaki kopyalama CHECK'i satır satır
  değerlendirir. İhlal eden eski satır varsa göç SESSİZCE SİLMEK YERİNE
  ANLAŞILIR BİÇİMDE DURUR — ne yapılacağına operatör karar verir.

GERİ DÖNÜŞ SÖZLEŞMESİ: ``downgrade`` kısıtı kaldırır, satırlara dokunmaz.
Kısıt yalnız yazımı sınırlar; kaldırılması hiçbir veriyi geri getirmez ya da
götürmez, bu yüzden geri dönüş tam ve kayıpsızdır.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260812_0059"
# 0058 başka bir dilim tarafından tutuluyor ve henüz inmedi; zincir şimdilik
# 0057'nin üzerine kurulur. O dilim önce inerse bu satır rebase'te 0058'e
# çevrilir — revision id'si YERİNDE DEĞİŞTİRİLMEZ.
down_revision = "20260812_0057"
branch_labels = None
depends_on = None

TABLE = "security_audit_logs"
CONSTRAINT = "ck_security_audit_logs_untenanted_only_preauth"

# Kısıt ifadesi modül düzeyinde: test onu MUTASYONLA bozup kapının kırmızıya
# döndüğünü gösterebilsin diye. Sabit, göçün KENDİ SQL'i olduğu için mutasyon
# taklit değil gerçek yolu bozar.
CHECK_SQL = "company_id IS NOT NULL OR (user_id IS NULL AND username IS NULL)"

_VIOLATION_COUNT = (
    f"SELECT COUNT(*) FROM {TABLE} "  # noqa: S608 - sabit tablo adı
    f"WHERE company_id IS NULL AND (user_id IS NOT NULL OR username IS NOT NULL)"
)

_SQLITE_RECREATE = """
CREATE TABLE {new} (
    id INTEGER NOT NULL,
    user_id INTEGER,
    username VARCHAR(80),
    action VARCHAR(20) NOT NULL,
    path VARCHAR(500) NOT NULL,
    status_code INTEGER NOT NULL,
    ip_address VARCHAR(80),
    created_at DATETIME NOT NULL,
    company_id INTEGER,
    request_id VARCHAR(64),
    outcome VARCHAR(20) NOT NULL,
    duration_ms INTEGER,
    auth_source VARCHAR(20),
    user_agent VARCHAR(500),
    failure_reason VARCHAR(300),
    CONSTRAINT pk_security_audit_logs PRIMARY KEY (id),
    CONSTRAINT {constraint} CHECK ({check})
)
"""

_COLUMNS = (
    "id, user_id, username, action, path, status_code, ip_address, created_at, "
    "company_id, request_id, outcome, duration_ms, auth_source, user_agent, "
    "failure_reason"
)

# Yeniden kurulumda kaybolmaması gereken indeksler. Adlar sözleşmenin parçası:
# ``20260714_0005`` request_id indeksini bu adla yaratıp downgrade'de bu adla
# düşürüyor.
_INDEXES = (
    ("ix_security_audit_logs_user_id", "user_id"),
    ("ix_security_audit_logs_company_id", "company_id"),
    ("ix_security_audit_logs_request_id", "request_id"),
)


def _sqlite_rebuild(*, with_check: bool) -> None:
    bind = op.get_bind()
    new = f"{TABLE}_new"
    if with_check:
        op.execute(
            _SQLITE_RECREATE.format(new=new, constraint=CONSTRAINT, check=CHECK_SQL)
        )
    else:
        without = _SQLITE_RECREATE.format(new=new, constraint=CONSTRAINT, check=CHECK_SQL)
        # Geri dönüşte CHECK satırı tamamen çıkar; ondan önceki virgül de.
        without = without.replace(
            f",\n    CONSTRAINT {CONSTRAINT} CHECK ({CHECK_SQL})", ""
        )
        op.execute(without)
    op.execute(f"INSERT INTO {new} ({_COLUMNS}) SELECT {_COLUMNS} FROM {TABLE}")
    op.execute(f"DROP TABLE {TABLE}")
    op.execute(f"ALTER TABLE {new} RENAME TO {TABLE}")
    existing = {index["name"] for index in sa.inspect(bind).get_indexes(TABLE)}
    for name, column in _INDEXES:
        if name not in existing:
            op.create_index(name, TABLE, [column])


def _constraint_exists(bind) -> bool:
    """Kısıt ZATEN var mı?

    TEMİZ KURULUMDA VARDIR. Baseline (``20260712_0000``) şemayı modelden kurar
    (``auth.metadata.create_all``) ve kısıt artık modelin parçası — yani taze bir
    zincirde tablo 0000'da kısıtla birlikte doğar, 0059'a geldiğinde eklenecek
    bir şey kalmaz. Koruma olmadan PostgreSQL ``DuplicateObject`` ile patlıyordu;
    SQLite'ta ise yeniden kurulum kendi DDL'ini yazdığı için hata VERMİYORDU —
    tam olarak iki lehçenin ayrıştığı ve tek lehçede test etmenin kaçıracağı yer.
    """
    if bind.dialect.name == "postgresql":
        return bool(
            bind.execute(
                sa.text(
                    "SELECT 1 FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE t.relname = :table AND c.conname = :name AND c.contype = 'c'"
                ),
                {"table": TABLE, "name": CONSTRAINT},
            ).first()
        )
    ddl = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": TABLE},
    ).scalar()
    return bool(ddl) and CONSTRAINT in ddl


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        return
    if _constraint_exists(bind):
        return

    if bind.dialect.name == "postgresql":
        # NOT VALID: yeni yazımlarda TAM zorlanır, geçmiş satırlar taranmaz ve
        # silinmez. Denetim satırı silmek bu göçün amacının tersidir.
        op.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {CONSTRAINT} "
            f"CHECK ({CHECK_SQL}) NOT VALID"
        )
        return

    if bind.dialect.name == "sqlite":
        violations = bind.execute(sa.text(_VIOLATION_COUNT)).scalar_one()
        if violations:
            raise RuntimeError(
                f"{TABLE}: kimliği olup firması olmayan {violations} eski satır var. "
                "Göç bu satırları SİLMEZ ve üyelikten firma UYDURMAZ; ne yapılacağı "
                "operatörün kararıdır. Karar verilene kadar bu göç uygulanamaz."
            )
        _sqlite_rebuild(with_check=True)


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        return

    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    elif bind.dialect.name == "sqlite":
        _sqlite_rebuild(with_check=False)

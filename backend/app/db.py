from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

is_sqlite = settings.database_url.startswith("sqlite")
if is_sqlite:
    # sqlite3 does not bind Decimal values by default. Adapting them as plain
    # decimal strings avoids binary-float conversion before SQLite applies the
    # target column affinity.
    sqlite3.register_adapter(Decimal, lambda value: format(value, "f"))
if is_sqlite:
    connect_args: dict[str, object] = {"check_same_thread": False}
else:
    # ULAŞILAMAYAN VERİTABANI SÜRESİZ BEKLETMEZ. Varsayılan libpq davranışı
    # sınırsızdır; paket düşüren bir ağda `engine.connect()` 20 saniyeden uzun
    # süre dönmedi (ölçüldü) ve /api/ready hiç yanıt vermedi. Yanıt vermeyen bir
    # prob, "sağlıksız" diyen bir probdan kötüdür.
    #
    # Bu YALNIZ bağlanma yolunu sınırlar. Soket açıkken sunucu yanıt vermezse
    # devreye girmez; o hâli app/main.py'deki hazırlık süre bütçesi kapatıyor.
    connect_args = {"connect_timeout": int(settings.db_connect_timeout_seconds)}

# `pool_pre_ping` YÜK TAŞIYAN BİR AYAR, süs değil. Havuzdaki bir bağlantı
# sunucu tarafından kapatıldığında (RDS haftalık bakım yükseltmesi bunu her
# seferinde yapıyor) bir sonraki istek onu ölü olarak devralırdı. Pre-ping
# teslimden önce ucuz bir yoklama yapıp ölü bağlantıyı atar ve yenisini açar.
# Kaldırılırsa öldürülen bağlantıdan sonraki ilk istek 503 döner — ölçüldü,
# `tests/test_db_baglanti_dayanikliligi.py` bunu kapı olarak tutuyor.
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            # Local mode must enforce relational integrity and tolerate short
            # concurrent write bursts without immediately returning "database locked".
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={int(settings.sqlite_busy_timeout_ms)}")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    @event.listens_for(engine, "checkout")
    def _reassert_foreign_keys(dbapi_connection, _connection_record, _proxy) -> None:
        """Yabancı anahtar denetimini HER TESLİMDE yeniden dayat.

        NEDEN BAĞLANTI AÇILIŞI YETMİYOR — ÖLÇÜLDÜ, VARSAYILMADI.
        `PRAGMA foreign_keys` BAĞLANTI ÖMÜRLÜDÜR ve bir kez kapatıldığında o
        bağlantı havuza KAPALI olarak geri döner; sonraki her istek onu ölü bir
        güvenceyle devralır. Kapatan taraf niyetli de olabilir: SQLite'ta tablo
        yeniden kurmanın (bileşik kısıt eklemenin) BELGELENMİŞ yordamı
        pragma'yı kapatmaktır — bkz. göç `20260827_0062`.

        VE KAPATAN TARAF KENDİ TEMİZLİĞİNİ YAPAMAZ: `PRAGMA foreign_keys`
        AÇIK BİR İŞLEM İÇİNDE NO-OP'TUR ve yeniden kurulumun kopyalama adımı
        işlemi çoktan açmıştır. Ölçüldü (2026-08-27): göç sonunda pragma'yı
        geri açan bir `finally` HİÇBİR ŞEY YAPMIYOR; havuzdan gelen bağlantı
        `foreign_keys=0` ile geliyor ve `field_activity_inputs -> products`
        gibi bir kısıtın 409 döndürmesi gereken yerde 200 dönüyordu
        (`test_supplier_price_profile_delete_net.py` bunu KIRMIZI yakaladı).

        Teslim anında hiçbir işlem açık DEĞİLDİR; pragma burada GERÇEKTEN
        etki eder. Böylece güvence "kimse kapatmadıysa geçerli"den
        "teslim edilen her bağlantıda geçerli"ye çıkar.

        Bedeli SQLite'ta teslim başına tek bir pragma ifadesidir.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

def _is_write_statement(statement) -> bool:
    if isinstance(statement, (Insert, Update, Delete)):
        return True
    if isinstance(statement, TextClause):
        first = statement.text.lstrip().split(None, 1)[0].upper()
        return first in {"INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE"}
    return False


class SungurSession(Session):
    _maintenance_lock_acquired = False

    def _acquire_write_lock(self) -> None:
        if self._maintenance_lock_acquired:
            return
        from .maintenance import acquire_write_transaction_lock

        acquire_write_transaction_lock(self.connection())
        self._maintenance_lock_acquired = True

    def execute(self, statement, *args, **kwargs):
        if _is_write_statement(statement):
            self._acquire_write_lock()
        return super().execute(statement, *args, **kwargs)

    def commit(self) -> None:
        try:
            super().commit()
        finally:
            self._maintenance_lock_acquired = False

    def rollback(self) -> None:
        try:
            super().rollback()
        finally:
            self._maintenance_lock_acquired = False


@event.listens_for(SungurSession, "before_flush")
def _lock_before_flush(session, _flush_context, _instances) -> None:
    if session.new or session.dirty or session.deleted:
        session._acquire_write_lock()


SessionLocal = sessionmaker(
    bind=engine, class_=SungurSession, autoflush=False, autocommit=False
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

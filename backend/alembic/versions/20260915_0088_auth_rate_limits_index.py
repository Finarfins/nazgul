"""H17: `auth_rate_limits.attempted_at` tek sütunlu indeksi.

Revision ID: 20260915_0088
Revises: 20260915_0087

--- HANGİ SORGU, HANGİ İNDEKS (ÖLÇÜLDÜ) ------------------------------------

Tabloya dokunan üç sorgu var; WHERE yüklemleri okundu:

1. ``routers/auth.py::_consume_ip_limit`` SÜPÜRMESİ:
   ``DELETE ... WHERE attempted_at < :cutoff`` — HER kayıt/giriş/şifre
   denemesinde koşar. Yüklem YALNIZ ``attempted_at``.
2. Aynı fonksiyonun SAYIMI: ``action = :a AND ip_address = :ip AND
   attempted_at >= :cutoff``. 0039'un ``ix_auth_rate_limits_action_ip_time``
   (action, ip_address, attempted_at) indeksi bunu ZATEN tam karşılıyor
   (EXPLAIN: Bitmap Index Scan, 3 tampon).
3. ``routers/platform_management.py`` platform paneli:
   ``WHERE attempted_at >= :pencere_basi GROUP BY action, ip_address``.

(1) ve (3)ün öncü sütunu ``attempted_at``tır ve bileşik indeks onu KULLANAMAZ
(öncü sütunu ``action``). Seçenek ``(ip_address, attempted_at)`` REDDEDİLDİ:
hiçbir sorgu yalnız IP ile süzmüyor ve IP'li sayım zaten bileşik indekste.
Doğru indeks tek sütunlu ``(attempted_at)``.

PostgreSQL 16.14, 100 000 sentetik satır (5 eylem x 2000 IP, son 65 dakikaya
zaman sırasıyla yazılmış — tablo yalnız ekleme alır, fiziksel sıra zamanla
örtüşür):

  süpürme, sürekli durum (süresi dolmuş satır YOK — her çağrı öncekinin
  artığını sildiği için olağan hal budur):
    ÖNCE : Seq Scan, Rows Removed by Filter: 100000, Buffers: shared hit=736
    SONRA: Index Scan using ix_auth_rate_limits_attempted_at, Buffers: shared hit=2
  süpürme, ~7 700 süresi dolmuş satır:
    ÖNCE : Seq Scan, 5.88 ms       SONRA: Index Scan, 2.69 ms
  sayım (2): iki durumda da ix_auth_rate_limits_action_ip_time, 3 tampon.

Yani sıcak yolun maliyeti tablo boyuyla DOĞRUSAL olmaktan çıkıp süresi dolan
satır sayısıyla orantılı oluyor. Tablo bir saatlik pencereyle sınırlı ama o
pencere kaba kuvvet altında büyüdüğünde her deneme tüm tabloyu tarıyordu.

--- İKİ DİYALEKT, VARLIK DENETİMİ -------------------------------------------

Tablo ``app/auth.py``nin ``metadata``sındadır ve açılışta ``create_all`` onu
(ve artık bu indeksi) göçten ÖNCE kurabilir; bu yüzden 0081 gibi önce
bakılır, varsa atlanır. ``downgrade`` yalnız varsa düşürür. Tablo yoksa
(0039 öncesi bir şema bu göçe zaten ulaşamaz) iki yön de dokunmaz.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260915_0088"
down_revision = "20260915_0087"
branch_labels = None
depends_on = None

TABLO = "auth_rate_limits"
INDEKS = "ix_auth_rate_limits_attempted_at"


def _indeksler(bind) -> set[str] | None:
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLO):
        return None
    return {i["name"] for i in inspector.get_indexes(TABLO)}


def upgrade() -> None:
    mevcut = _indeksler(op.get_bind())
    if mevcut is not None and INDEKS not in mevcut:
        op.create_index(INDEKS, TABLO, ["attempted_at"])


def downgrade() -> None:
    mevcut = _indeksler(op.get_bind())
    if mevcut is not None and INDEKS in mevcut:
        op.drop_index(INDEKS, table_name=TABLO)

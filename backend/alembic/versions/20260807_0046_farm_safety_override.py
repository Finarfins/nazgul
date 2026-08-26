"""Hasat bekleme süresi ihlallerinin gerekçesini saklayan sütun.

Revision ID: 20260807_0046
Revises: 20260807_0045

İlaçlama kayıtlarında ``preharvest_interval_days`` (hasat bekleme süresi) var
ama V1'de HİÇBİR YERDE KULLANILMIYORDU: kullanıcı süreyi giriyor, sistem
görmezden geliyordu. Süre dolmadan yapılan hasat gerçek bir kalıntı riski ve
yasal sorun; verinin toplanıp kullanılmaması, toplanmamasından daha kötü —
çünkü "sistem kontrol ediyor" izlenimi veriyor.

Bu sütun, kontrolün AŞILDIĞI durumları kayıt altına alıyor.

NEDEN SERT RET DEĞİL. İlk düşünce hasadı tümüyle reddetmekti. Ama sahada iki
gerçek durum var ve ikisi de meşru:
  * İlaçlama kaydının tarihi ya da süresi YANLIŞ girilmiş olabilir; doğru olan
    hasadı, yanlış bir veri yüzünden bloke etmek işi durdurur.
  * Hasat zaten YAPILMIŞ ve sisteme sonradan giriliyordur; olmuş bir şeyi
    kaydettirmemek veriyi eksik bırakır, riski azaltmaz.
Bu yüzden kontrol AÇIK GEREKÇE istiyor ve gerekçe burada, hasat satırının
kendisinde duruyor — denetimde "bu ürün neden erken hasat edildi" sorusunun
cevabı kayıtla birlikte gelsin diye. Sessiz geçiş YOK: gerekçe girilmeden
istek 422 alıyor.

Sütun ``field_harvests`` üzerinde ve nullable: normal hasatların ezici
çoğunluğunda boştur ve boş olması "ihlal yok" demektir.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260807_0046"
down_revision = "20260807_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    sutunlar = {c["name"] for c in sa.inspect(bind).get_columns("field_harvests")}
    if "safety_override_reason" in sutunlar:
        return
    op.add_column(
        "field_harvests",
        sa.Column("safety_override_reason", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("field_harvests", "safety_override_reason")

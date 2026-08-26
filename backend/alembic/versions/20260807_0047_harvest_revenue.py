"""Hasat kaydına satılan miktar ve gelir tutarı.

Revision ID: 20260807_0047
Revises: 20260807_0046

Konu #2 hasat için "toplam maliyet ve gelir özeti" istiyor. Maliyet tarafı
Faz 2'den beri var (girdilerden toplanıyor); GELİR TARAFI YOKTU, dolayısıyla
sezonun kârı hiç hesaplanamıyordu — sistem "bu tarla para kazandırdı mı"
sorusunu cevaplayamıyordu.

NEDEN SATIŞ MODÜLÜNE BAĞLANMADI. V1'in ürün sınırı açık: "Stok ve muhasebeye
doğrudan/örtük fiş yazmayacak." Hasadı bir satış belgesine bağlamak o sınırı
geçerdi. Ayrıca gerçekte hasat ile satış AYNI AN DEĞİL: ürün depoda bekliyor,
aylar sonra ve parça parça satılıyor, fiyat o gün belli oluyor. Hasat anında
bir satış kaydı zorlamak, olmayan bir işlemi uydurmak olurdu.

Bu yüzden gelir ELLE giriliyor ve iki ayrı sütun tutuluyor:

* ``sold_quantity`` — satılan miktar. Hasat miktarından AYRI, çünkü hasadın
  tamamı satılmaz: bir kısmı tohumluk ayrılır, bir kısmı yemlik kalır, bir
  kısmı fire olur. Tek sütun tutup "hepsi satıldı" varsaymak dekar başına
  geliri şişirirdi.
* ``revenue_amount`` — elde edilen tutar (TRY). Birim fiyat SAKLANMIYOR:
  parça parça satışta tek bir birim fiyat yoktur; tutarı saklayıp gerekirse
  ortalamayı türetmek dürüst olan.

İKİSİ DE NULLABLE ve bu bilinçli: hasat kaydedildiğinde satış henüz
yapılmamış olur. Boş olması "gelir sıfır" değil "HENÜZ BİLİNMİYOR" demektir;
raporlar bu ikisini ayırmak zorunda.

Para birimi sütunu YOK: uygulamanın satış tarafı TRY sabit (çoklu para yalnız
alış tarafında). Buraya para birimi eklemek, olmayan bir yeteneği varmış gibi
gösterirdi.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260807_0047"
down_revision = "20260807_0046"
branch_labels = None
depends_on = None

MIKTAR = sa.Numeric(18, 4)
TUTAR = sa.Numeric(18, 2)


def upgrade() -> None:
    bind = op.get_bind()
    sutunlar = {c["name"] for c in sa.inspect(bind).get_columns("field_harvests")}
    if "sold_quantity" not in sutunlar:
        op.add_column("field_harvests", sa.Column("sold_quantity", MIKTAR, nullable=True))
    if "revenue_amount" not in sutunlar:
        op.add_column("field_harvests", sa.Column("revenue_amount", TUTAR, nullable=True))


def downgrade() -> None:
    op.drop_column("field_harvests", "revenue_amount")
    op.drop_column("field_harvests", "sold_quantity")

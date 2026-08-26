"""Gerçek Maliyet V1 — FAZ 2: oranın faaliyet kaydına KOPYALANMASI.

Revision ID: 20260808_0053
Revises: 20260808_0052

Konu: mobil-erp#24.

--- NEYİ ÇÖZÜYOR ------------------------------------------------------------

FAZ 1 `cost_rates` tablosunu kurdu ve orada tek bir şey duruyor: BUGÜNKÜ
VARSAYILAN. Tablo geçmiş tutmuyor (gerekçesi migration 0052 başlığında). O hâlde
geçmişi bir yerin tutması gerekiyor, yoksa oranı değiştirmek GEÇMİŞ MALİYETİ de
değiştirirdi ve geçen sezonun kârı bu yıl başka çıkardı.

Geçmişi tutan yer İŞLEM SATIRININ KENDİSİ: faaliyet kaydedilirken o anki oran
satıra kopyalanıyor. Bu desen bu depoda yeni değil, servis tarafında
`work_order_labor_lines`'ta yıllardır çalışıyor:

    "Rate freeze. ``hourly_rate`` is written when the line is created ... the
     stored rate is the only rate billing ever reads."

Aynı cümle buradaki dört sütunun tamamı için geçerli.

--- NEDEN TUTAR SÜTUNU YOK --------------------------------------------------

`labor_cost` / `machine_cost` SAKLANMIYOR; okurken `saat × oran` ile türetiliyor
(`work_order_labor_lines._line_total` ile birebir aynı yaklaşım). Saklansaydı
aynı bilgi iki yerde durur ve yuvarlama ya da kısmi güncelleme yüzünden ikisi
ayrışabilirdi. Türetilen değerin ayrışması mümkün değil.

Bu, `field_activity_inputs.total_cost`'tan bilerek AYRILIYOR: orada tutar
saklanıyor çünkü `quantity × unit_cost` istemciden gelen iki değerin çarpımı ve
sezon maliyeti toplamı doğrudan SQL'de (`SUM(total_cost)`) alınıyor. Burada ise
çarpanların ikisi de zaten satırda duruyor ve FAZ 2 hiçbir toplama sorgusu
eklemiyor (raporlama sonraki faz).

--- NEDEN CHECK KISITI EKLENMEDİ --------------------------------------------

`field_activities` ÜRETİMDE DURAN bir tablo ve SQLite'ta mevcut bir tabloya
CHECK eklemek tablonun YENİDEN İNŞASI demek. Deponun bu durumdaki deseni
(bkz. migration 0047, `field_harvests.sold_quantity`/`revenue_amount`) düz
`add_column` ve doğrulamanın şema katmanında yapılması. Aynı yol izleniyor:
`farm_schemas.ActivityWrite` saatleri `gt=0`, oranları `ge=0` ile sınırlıyor ve
"oran var ama saat yok" hâlini reddediyor.

--- DÖRT SÜTUN DA NULLABLE VE BU BİLİNÇLİ ------------------------------------

* Saatler boş olabilir: her faaliyet için işçilik/makine saati girilmez
  (sulama açıp kapamak gibi).
* Oranlar boş olabilir: saat girildiği hâlde firmanın o hedef için tanımlı bir
  oranı olmayabilir. O zaman oran BOŞ kalır ve maliyet "SIFIR" değil
  "BİLİNMİYOR" olarak döner — `field_harvests.revenue_amount`'taki ayrımın
  aynısı. Sıfır yazmak, ölçülmemiş bir maliyeti ölçülmüş gibi gösterirdi.

Boş kalan oranın SONRADAN doldurulması BİLEREK YAPILMIYOR: geriye dönük
doldurma, bugünkü oranı geçmiş bir kayda yazmak demektir ve bu migration'ın
kapattığı deliğin ta kendisidir.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260808_0053"
down_revision = "20260808_0052"
branch_labels = None
depends_on = None

# Ölçekler tarla modülünün geri kalanıyla aynı (bkz. migration 0044/0047):
# miktar 18,4 — para 18,2.
MIKTAR = sa.Numeric(18, 4)
TUTAR = sa.Numeric(18, 2)

_YENI_SUTUNLAR = (
    ("labor_hours", MIKTAR),
    ("labor_hourly_rate", TUTAR),
    ("machine_hours", MIKTAR),
    ("machine_hourly_rate", TUTAR),
)


def upgrade() -> None:
    bind = op.get_bind()
    mevcut = {c["name"] for c in sa.inspect(bind).get_columns("field_activities")}
    for ad, tip in _YENI_SUTUNLAR:
        if ad not in mevcut:
            op.add_column("field_activities", sa.Column(ad, tip, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    mevcut = {c["name"] for c in sa.inspect(bind).get_columns("field_activities")}
    for ad, _ in reversed(_YENI_SUTUNLAR):
        if ad in mevcut:
            op.drop_column("field_activities", ad)

"""Hayvancılık V1 — FAZ 4: aşıya MAKİNE OKUNUR kod.

Revision ID: 20260808_0050
Revises: 20260808_0049

Konu: mobil-erp#17, FAZ 4 (yaşa bağlı zorunlu aşı takvimi).

--- NEDEN YENİ BİR SÜTUN, NEDEN METİNDEN TAHMİN DEĞİL ---------------------

`vaccine` BİLEREK serbest metin (FAZ 1 kararı): zorunlu olmayan aşılar
(septisemi, IBR, BVD…) işletmeye göre değişiyor ve kapalı bir liste
kullanıcıyı yaptığı aşıyı kaydedemez hâle getirirdi. Bu karar DEĞİŞMİYOR.

Ama takvim hesabı bir EŞLEŞME gerektiriyor: "bu hayvanın şap aşısı en son ne
zaman yapıldı" sorusu cevaplanmadan "aşısı geldi mi" hesaplanamaz. Serbest
metinden tahmin etmek — 'Şap', 'şap', 'FMD', 'Şap aşısı', 'şap-tırnak' —
SESSİZCE YANLIŞ cevap üretir: aşısı 'FMD' diye kaydedilmiş bir hayvan hiç
aşılanmamış görünür ve sahte bir gecikme listesine düşer. Sahte gecikme,
gerçek gecikmeyi değersizleştirir; kullanıcı listeye güvenmeyi bırakır.

Bu yüzden AYRI bir sütun: `vaccine_code`. Serbest metin ADI korur, kod
HESABI taşır.

--- NEDEN NULLABLE VE NEDEN GERİYE DÖNÜK TAHMİN YOK ----------------------

Sütun nullable ve MEVCUT SATIRLAR DOLDURULMUYOR. Doldurmak, yukarıda
reddedilen metinden-tahmin işini bir kez migration içinde yapmak olurdu —
aynı yanlışın kalıcı hâli, üstelik geri alınması zor.

Kodsuz satırlar takvim hesabına GİRMEZ. Bunun görünmez kalmaması için uç,
kaç kodsuz kayıt olduğunu AYRICA döndürüyor (bkz. `vaccination_calendar`):
"aşısı eksik 0 hayvan" cevabı, "12 kaydın kodu yok" bilgisiyle birlikte
verilmezse yanlış bir güvence olur.

--- KAPALI LİSTE DEĞİL, ZORUNLULARIN KODU -------------------------------

Kod alanı da kapalı bir liste değil: yalnız takvimi olan aşılar (şap =
``FMD``, brusella = ``BRUCELLA``) hesaba giriyor. Diğer aşılar için kod boş
kalabilir ya da işletme kendi kodunu yazabilir — hesaba girmez, kaydı
etkilemez. Amaç zorunlu aşıları TAKİP ETMEK, tüm aşıları sınıflandırmak
değil.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260808_0050"
down_revision = "20260808_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    baglanti = op.get_bind()
    denetci = sa.inspect(baglanti)
    if "animal_vaccinations" not in denetci.get_table_names():
        # FAZ 1 migration'ı koşmadıysa yapacak iş yok; 0049 tabloyu kuruyor.
        return
    sutunlar = {c["name"] for c in denetci.get_columns("animal_vaccinations")}
    if "vaccine_code" not in sutunlar:
        op.add_column(
            "animal_vaccinations",
            # Uzunluk 40: 'BRUCELLA' 8 karakter; işletmenin kendi kodu için pay.
            sa.Column("vaccine_code", sa.String(length=40), nullable=True),
        )
    indeksler = {i["name"] for i in denetci.get_indexes("animal_vaccinations")}
    if "ix_animal_vacc_company_code" not in indeksler:
        # Takvim hesabı "bu firmadaki şap kayıtları" diye tarıyor; kod olmadan
        # her hesap tüm aşı tablosunu okur.
        op.create_index(
            "ix_animal_vacc_company_code",
            "animal_vaccinations",
            ["company_id", "vaccine_code", "animal_id"],
        )


def downgrade() -> None:
    baglanti = op.get_bind()
    denetci = sa.inspect(baglanti)
    if "animal_vaccinations" not in denetci.get_table_names():
        return
    indeksler = {i["name"] for i in denetci.get_indexes("animal_vaccinations")}
    if "ix_animal_vacc_company_code" in indeksler:
        op.drop_index("ix_animal_vacc_company_code", table_name="animal_vaccinations")
    sutunlar = {c["name"] for c in denetci.get_columns("animal_vaccinations")}
    if "vaccine_code" in sutunlar:
        op.drop_column("animal_vaccinations", "vaccine_code")

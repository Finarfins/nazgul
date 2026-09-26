"""H75: KALICI katlanmış arama sütunlarının TEK yazıcısı.

NEDEN VAR — ÖLÇÜLDÜ, VARSAYILMADI. H57 (#148) arama kolonlarını istek
anında ``translate(kolon, KAYNAK, HEDEF)`` ile katlıyordu. ``LIKE '%x%'``
zaten sıralı taramadır; ama katlama HER SATIRDA, HER KOLONDA yeniden
hesaplanıyordu ve bu, seçicilikten BAĞIMSIZ sabit bir taban maliyet
getiriyordu (20k cari, ``q=ltd``: PG 15.9 → 214 ms, SQLite 26 → 97 ms;
#148 tur-3 merceği ve bu PR'ın kendi tezgâhı). Katlanmış değer artık
satırla birlikte SAKLANIR; istek SQL'i yalnız ``<kolon>_katli LIKE :q``
yazar ve ``translate`` istek yolundan TAMAMEN çıkar.

TEK KURAL: ``<kolon>_katli == arama_katla(COALESCE(<kolon>, ''))``.
Değer VERİTABANINDA, ``app.arama.katli_sql`` ile — yani göçün geri
doldurmasıyla AYNI SQL metniyle — hesaplanır; Python tarafında ikinci bir
katlama YOKTUR. PG'de ``translate`` yerleşiktir, SQLite'ta ``app/db.py``
her bağlantıya aynı anlamda kaydeder.

KİM YAZAR: kaynak kolonu yazan HER yol, yazdığı satırlar için
:func:`katli_esitle`yi AYNI işlemde çağırır. Tetikleyici YOK (SQLite/PG
eşdeğerliği ve geri yükleme anlamı), PG üretilmiş sütunu YOK (SQLite'ta
kullanıcı işlevine dayanan üretilmiş sütun kurulamaz). Kaçan bir yazıcıyı
``tests/test_h75_katli_sutun_esitligi.py`` yakalar: kaynak kolonu yazan
her uç o testte gerçek istekle koşulur ve sonra tablonun TAMAMI
``arama_katla`` ile karşılaştırılır.

GÖRÜNMEZLİK: ``_katli`` sütunları iç ayrıntıdır. ``SELECT *`` okuyan uçlar
onları taşıyabilir; bu yüzden (1) ``alan_maskeleme.MASKELENEN_ALANLAR``
``email_katli``yi ``email`` ile aynı kuralla maskeler (SEC-3b), (2)
``change_history._snapshot`` onları anlık görüntüden atar, (3) kiracı dışa
aktarımı onları YAZMAZ ve geri yükleme :func:`kiraci_katli_esitle` ile
yeniden hesaplar, (4) H96: ``SELECT *`` satırını JSON'a çeviren uçlar
:func:`katli_gizle`den geçirir. (1)'deki maske böylece yanıtta işe yaramaz
kalır ama KALIR: yeni bir ``SELECT *`` ucu (4)'ü unutursa ikinci savunmadır.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import bindparam, text

from .arama import katli_sql

__all__ = ["KATLI_EK", "KATLI_SUTUNLAR", "katli_ad", "katli_esitle",
           "kiraci_katli_esitle", "katli_sutun_mu", "katli_gizle"]

#: Katlanmış sütun adının soneki.
KATLI_EK = "_katli"

#: Tablo -> katlanan kaynak kolonlar. KAPALI liste; tablo ve kolon adları
#: SQL'e bu sözlükten girer, istekten ASLA. Göç 20260925_0092 bu sözlüğün
#: göç anındaki kopyasını taşır; ikisinin ayrışmadığını
#: ``tests/test_h75_katli_sutun_esitligi.py`` ölçer.
KATLI_SUTUNLAR: dict[str, tuple[str, ...]] = {
    "customers": ("name", "owner_name", "email"),
    "suppliers": ("name", "owner_name", "email"),
    "products": ("name", "product_code", "barcode"),
    "orders": ("document_no",),
    "purchases": ("document_no",),
    "finance_transactions": ("description",),
}


def katli_ad(kolon: str) -> str:
    """``name`` -> ``name_katli``."""

    return f"{kolon}{KATLI_EK}"


def katli_sutun_mu(ad: str) -> bool:
    """Bir sütun adı H75 katlanmış sütunu mu? (dışa aktarım/anlık görüntü süzgeci)"""

    return ad.endswith(KATLI_EK)


def katli_gizle(satir: Mapping[str, Any]) -> dict[str, Any]:
    """H96 — satırın ``<kolon>_katli`` anahtarları ATILMIŞ YENİ ``dict``i.

    ``SELECT *`` okuyan bir uç satırı yanıta koymadan önce bundan geçirir.
    Ölçüldü: yönetici için dört uç sızdırıyordu (müşteri/tedarikçi kartı,
    ürün detayı, finans hareketleri listesi). Girdi yerinde değişmez.
    """

    return {ad: deger for ad, deger in satir.items() if not katli_sutun_mu(ad)}


def _katli_ifade(kolon: str) -> str:
    return katli_sql(f"COALESCE({kolon},'')")


def _set_parcasi(tablo: str) -> str:
    return ",".join(f"{katli_ad(k)}={_katli_ifade(k)}" for k in KATLI_SUTUNLAR[tablo])


def _bayat_parcasi(tablo: str) -> str:
    # Yalnız değeri DEĞİŞECEK satırlar yazılır: kiracı çapındaki çağrı PG'de
    # her satıra yeni bir sürüm (MVCC) yazmasın.
    return " OR ".join(
        f"{katli_ad(k)} IS NULL OR {katli_ad(k)}<>{_katli_ifade(k)}"
        for k in KATLI_SUTUNLAR[tablo]
    )


# Metinler modül yüklenirken BİR KEZ, KAPALI sözlükten kurulur.
_TEKIL_SQL = {
    t: text(f"UPDATE {t} SET {_set_parcasi(t)} WHERE company_id=:cid AND id IN :ids")
    .bindparams(bindparam("ids", expanding=True))
    for t in KATLI_SUTUNLAR
}
_KIRACI_SQL = {
    t: text(f"UPDATE {t} SET {_set_parcasi(t)} WHERE company_id=:cid AND ({_bayat_parcasi(t)})")
    for t in KATLI_SUTUNLAR
}


def katli_esitle(db, tablo: str, *, cid: int, ids: Iterable[int] | None = None) -> None:
    """``tablo``nun katlanmış sütunlarını kaynak kolonlardan yeniden hesaplar.

    ``ids`` verilirse yalnız o satırlar (tek kayıt yazan uçlar); verilmezse
    kiracının değeri BAYAT olan bütün satırları (içe aktarım, geri yükleme).
    Çağıranın işlemi içinde koşar; ``commit`` ÇAĞIRANINDIR.
    """

    if ids is None:
        db.execute(_KIRACI_SQL[tablo], {"cid": cid})
        return
    kimlikler = sorted({int(i) for i in ids})
    if kimlikler:
        db.execute(_TEKIL_SQL[tablo], {"cid": cid, "ids": kimlikler})


def kiraci_katli_esitle(db, cid: int) -> None:
    """Kiracının BÜTÜN katlanmış sütunlarını eşitler (geri yükleme sonu)."""

    for tablo in KATLI_SUTUNLAR:
        katli_esitle(db, tablo, cid=cid)

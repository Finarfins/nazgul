"""Tedarikçi avans satırları — TEK okuma yolu (F10-1b düzeltme 1).

`routers/avans.py::list_supplier_advances` (personel ucu) ve
`whatsapp/ciftci_yurutucu.py::ciftci_avans` (çiftçi aracı) AYNI satırları
buradan okur. Önceden çiftçi aracı ucun SELECT'inin bir KOPYASINI
taşıyordu; iki kopya, biri değiştiğinde ötekini SESSİZCE eski hâlinde
bırakırdı ve aynı çiftçi iki yüzeyde farklı rakam görürdü. Eşitlik bir
kapıyla ölçülüyor: `test_AVANS_TOPLAMLARI_UCUN_KENDI_SAYILARIYLA_AYNI`.

--- BÜTÜN SQL SABİT METİNDİR -----------------------------------------------

`avans_engine.py`nin kuralı AYNEN: hiçbir `text()` f-string ya da
birleştirme ALMAZ, `open_only` süzgeci İKİ SABİT metin arasından SEÇİLİR
ve kiracı yüklemi (`a.company_id=:cid`) her metinde LİTERAL durur.

--- `payments` JOIN'İ ---------------------------------------------------------

`supplier_advances`in kendi tarih sütunu YOKTUR (`applied_at` avansın
ALINDIĞI değil MAHSUP EDİLDİĞİ andır); avansın tarihi ödemesinin
`payment_date`idir. JOIN satır kümesini DEĞİŞTİRMEZ: `payment_id` NOT
NULL'dır ve `fk_supplier_advances_payment_same_company` onu AYNI firmanın
bir ödemesine bağlar. Yüklem yine de BİLEŞİKTİR (`company_id` + `id`) ki
kiracı sınırı sorgunun KENDİSİNDE yazılı olsun.

`supplier_advances`in bir Core `Table` nesnesi YOKTUR (`core_schema.py`de
tanımlı değil); bu yüzden `text()`. Bir Core nesnesi uydurmak şemanın
ikinci bir tanımını açmak olurdu.
"""
from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

_ACIK_SATIRLAR_SQL = text(
    "SELECT a.id,a.supplier_id,a.payment_id,a.amount,a.remaining_amount,"
    "a.receipt_id,a.applied_at,a.note,p.payment_date"
    " FROM supplier_advances a"
    " JOIN payments p ON p.company_id=a.company_id AND p.id=a.payment_id"
    " WHERE a.company_id=:cid AND a.supplier_id=:sid AND a.remaining_amount>0"
    " ORDER BY a.id LIMIT :limit OFFSET :offset"
)
_TUM_SATIRLAR_SQL = text(
    "SELECT a.id,a.supplier_id,a.payment_id,a.amount,a.remaining_amount,"
    "a.receipt_id,a.applied_at,a.note,p.payment_date"
    " FROM supplier_advances a"
    " JOIN payments p ON p.company_id=a.company_id AND p.id=a.payment_id"
    " WHERE a.company_id=:cid AND a.supplier_id=:sid"
    " ORDER BY a.id LIMIT :limit OFFSET :offset"
)

#: Bir sayfanın üst sınırı — ucun `_SAYFA` sorgu parametresinin `le`
#: değeriyle AYNI. `tedarikci_tum_avanslari` sayfaları bu boyutta çeker.
SAYFA_UST_SINIRI = 200


def tedarikci_avans_satirlari(
    db: Session,
    company_id: int,
    supplier_id: int,
    *,
    open_only: bool = False,
    limit: int = SAYFA_UST_SINIRI,
    offset: int = 0,
) -> Sequence[Any]:
    """Bir tedarikçinin avans satırları, `id` sırasıyla, TEK sayfa.

    Tedarikçinin varlığı burada DENETLENMEZ: uç 404'ünü kendi
    `_tedarikci_var`ıyla verir, çiftçi aracının cari kimliği ise zaten
    etkin bir bağlantıdan gelir.
    """
    sorgu = _ACIK_SATIRLAR_SQL if open_only else _TUM_SATIRLAR_SQL
    return db.execute(
        sorgu,
        {"cid": company_id, "sid": supplier_id, "limit": limit, "offset": offset},
    ).mappings().all()


def tedarikci_tum_avanslari(
    db: Session, company_id: int, supplier_id: int
) -> list[Any]:
    """Bir tedarikçinin BÜTÜN avans satırları — ucun sayfalarının birleşimi.

    Toplamlar (çiftçi aracı) bir sayfanın değil BÜTÜN kümenin toplamıdır;
    tek bir `LIMIT`le kesmek, 200'den fazla avansı olan bir çiftçiye eksik
    rakam söylerdi. Her sorgu yine de SINIRLIDIR: tek tedarikçi, sayfa
    başına en çok `SAYFA_UST_SINIRI` satır.
    """
    satirlar: list[Any] = []
    offset = 0
    while True:
        sayfa = tedarikci_avans_satirlari(
            db, company_id, supplier_id, limit=SAYFA_UST_SINIRI, offset=offset
        )
        satirlar.extend(sayfa)
        if len(sayfa) < SAYFA_UST_SINIRI:
            return satirlar
        offset += SAYFA_UST_SINIRI

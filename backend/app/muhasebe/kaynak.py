"""Muhasebe fişinin TEK okuma yeri: dönem → `Fis` (F9-5a, keşif §1, §2.4).

Beş kol (keşif §7.1 / K1):

    SATIS          orders/order_items            {approved, completed}
    ALIS           purchases/purchase_items      {approved, completed}
    SATIS_IADE     returns(sale_return)          {approved, completed}
    ALIS_IADE      returns(purchase_return)      {approved, completed}
    SERVIS_FATURA  invoices/invoice_items        ISSUED
    MUSTAHSIL      producer_receipts/_items      issued

`pending`/`draft`/`cancelled` GİRMEZ (K10, G9); girmeyen `pending`/`draft`
belge sayısı `uyarilar`da ADIYLA durur.

--- TOPLAMLAR SATIRDAN (K4, G3) ---------------------------------------------

Fişin ve KDV özetinin her tutarı KALEM satırlarının toplamıdır; başlık
(`vat_total`, `final_total`, `total`) YALNIZ karşılaştırma için okunur ve
ayrışma G3 uyarısıdır. Özet ve fiş AYNI `OranGrubu` listesinden kurulur —
ikisi ayrışamaz.

--- TARİH: ARALIK, SUBSTR DEĞİL (G5) -----------------------------------------

Metin tarihli belgeler (`order_date` … `String(30)`, ISO `YYYY-AA-GG`)
`>= 'YYYY-AA-01' AND < '<sonraki ay>-01'` ile süzülür. `timestamptz`
sütunlar (`invoices.created_at`, `producer_receipts.issued_at`) İSTANBUL ay
sınırının UTC karşılığıyla süzülür: PG'de `substr(timestamptz, …)` ÇALIŞMAZ ve
UTC ay sınırı İstanbul iş gününden üç saat kayar — 31 Temmuz 23:30 İstanbul
(20:30 UTC) Temmuz'dur. SQLite bu sütunları METİN tutar
(`'2026-07-31 20:30:00+00:00'`); sınır aynı biçimde bağlanır (`_UtcAn`) ki
metin karşılaştırması zaman sırasıyla örtüşsün.

--- KİRACI YÜKLEMİ HER PARÇADA İLK -------------------------------------------

Her `where(...)`in İLK koşulu `<tablo>.c.company_id == cid`; birleşimler
`company_id` eşitliğiyle kiracı İÇİNDE kalır (Core kiracı kapısı).
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, String, and_, bindparam, func, select
from sqlalchemy.orm import Session
from sqlalchemy.types import TypeDecorator

from ..business_time import ISTANBUL
from ..core_schema import (
    customers,
    order_items,
    orders,
    purchase_items,
    purchases,
    return_items,
    returns,
    suppliers,
)
from ..document_engine import STOCK_STATUSES
from ..money import money
from .fis import Fis, FisHatasi, SatirToplayici, ayikla, fis_no
from .hesap_plani import HesapPlani, oran_normalize
from .schema import invoice_items, invoices, producer_receipt_items, producer_receipts

SIFIR = Decimal("0.00")
BEKLEYEN_DURUMLAR = ("pending", "draft")
_DEFTER_DURUMLARI = sorted(STOCK_STATUSES)


class DonemHatasi(ValueError):
    pass


@dataclass(frozen=True)
class Donem:
    """Tek takvim ayı (K12). Sınırlar YARI AÇIK: `[bas, son)`."""

    yil: int
    ay: int

    @property
    def metin(self) -> str:
        return f"{self.yil:04d}-{self.ay:02d}"

    @property
    def bas_tarih(self) -> date:
        return date(self.yil, self.ay, 1)

    @property
    def son_tarih(self) -> date:
        return date(self.yil + (self.ay == 12), self.ay % 12 + 1, 1)

    @property
    def bas_an(self) -> datetime:
        """İstanbul ay başının UTC anı."""
        b = self.bas_tarih
        return datetime(b.year, b.month, 1, tzinfo=ISTANBUL).astimezone(timezone.utc)

    @property
    def son_an(self) -> datetime:
        s = self.son_tarih
        return datetime(s.year, s.month, 1, tzinfo=ISTANBUL).astimezone(timezone.utc)


def donem_coz(metin: str) -> Donem:
    """`YYYY-AA` → `Donem`; başka her biçim `DonemHatasi` (uç 422 döner)."""
    parca = (metin or "").strip()
    yil_m, ay_m = parca[:4], parca[5:]
    if (
        len(parca) != 7
        or parca[4] != "-"
        or not (yil_m.isascii() and yil_m.isdigit() and ay_m.isascii() and ay_m.isdigit())
    ):
        raise DonemHatasi("Dönem YYYY-AA biçiminde olmalı (ör. 2026-07)")
    yil, ay = int(yil_m), int(ay_m)
    if not (1 <= ay <= 12) or not (2000 <= yil <= 2100):
        raise DonemHatasi("Dönem geçersiz: ay 01-12, yıl 2000-2100 olmalı")
    return Donem(yil, ay)


class _UtcAn(TypeDecorator):
    """Ay sınırının BAĞLI parametresi: PG'de `timestamptz`, SQLite'ta uygulamanın
    yazdığı metin biçimi (`datetime.isoformat(" ")`, UTC, `+00:00` ekli).

    SQLAlchemy'nin SQLite `DateTime`ı sınırı `'… 21:00:00.000000'` diye
    bağlardı; saklanan `'… 21:00:00+00:00'` ile metin karşılaştırmasında `'+'
    < '.'` olduğu için TAM sınırdaki satır YANLIŞ tarafa düşerdi (ölçüldü).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String())
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        utc = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return utc.isoformat(" ")
        return utc


def _utc(an: object) -> datetime | None:
    if an is None:
        return None
    if isinstance(an, str):
        an = datetime.fromisoformat(an)
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    return an.astimezone(timezone.utc)


def _istanbul_gunu(an: object) -> date | None:
    utc = _utc(an)
    return None if utc is None else utc.astimezone(ISTANBUL).date()


def _metin_gunu(deger: object) -> date | None:
    try:
        return date.fromisoformat(str(deger or "")[:10])
    except ValueError:
        return None


def _d(deger: object) -> Decimal:
    return money(deger if deger is not None else 0)


@dataclass(frozen=True)
class OranGrubu:
    """Bir belgenin tek bir KDV oranındaki kalem toplamı (TRY)."""

    kaynak: str
    belge_id: int
    oran: Decimal
    matrah: Decimal
    kdv: Decimal


@dataclass(frozen=True)
class Uyari:
    kod: str
    kaynak: str
    mesaj: str
    belge_id: int | None = None
    belge_no: str | None = None
    fark: Decimal | None = None
    kdv_dahil: bool | None = None

    def sozluk(self) -> dict:
        d = {
            "kod": self.kod,
            "kaynak": self.kaynak,
            "belge_id": self.belge_id,
            "belge_no": self.belge_no,
            "fark": None if self.fark is None else format(self.fark, "f"),
            "mesaj": self.mesaj,
        }
        if self.kdv_dahil is not None:
            d["kdv_dahil"] = self.kdv_dahil
        return d


@dataclass
class MustahsilToplami:
    belge_sayisi: int = 0
    brut: Decimal = SIFIR
    stopaj: Decimal = SIFIR
    bagkur: Decimal = SIFIR
    net: Decimal = SIFIR


@dataclass
class DonemVerisi:
    donem: Donem
    fisler: list[Fis] = field(default_factory=list)
    reddedilen: list[FisHatasi] = field(default_factory=list)
    oran_gruplari: list[OranGrubu] = field(default_factory=list)
    uyarilar: list[Uyari] = field(default_factory=list)
    mustahsil: MustahsilToplami = field(default_factory=MustahsilToplami)


@dataclass(frozen=True)
class _Kol:
    """Satış/alış/iade kolunun hesapları ve yönü.

    `cari_borclu=True`: cari BORÇ, matrah+KDV ALACAK (satış, alış iadesi).
    `cari_borclu=False`: tersi (alış, satış iadesi).
    """

    kaynak: str
    etiket: str
    cari_tipi: str
    cari_olay: str
    matrah_olay: str
    kdv_olay: str
    cari_borclu: bool
    tarih_alani: str
    toplam_alani: str
    cari_alani: str


_SATIS = _Kol("SATIS", "Satış", "CUSTOMER", "SATIS_CARI", "SATIS_GELIR", "SATIS_KDV",
              True, "order_date", "final_total", "customer_id")
_ALIS = _Kol("ALIS", "Alış", "SUPPLIER", "ALIS_CARI", "ALIS_STOK", "ALIS_KDV",
             False, "purchase_date", "final_total", "supplier_id")
# Satıştan iade: 610 B, indirilecek KDV (191) B, 120 A (keşif §2.2).
_SATIS_IADE = _Kol("SATIS_IADE", "Satış iadesi", "CUSTOMER", "SATIS_CARI", "SATIS_IADE",
                   "ALIS_KDV", False, "return_date", "total", "entity_id")
# Alıştan iade: 320 B, 153 A, hesaplanan KDV (391) A.
_ALIS_IADE = _Kol("ALIS_IADE", "Alış iadesi", "SUPPLIER", "ALIS_CARI", "ALIS_IADE",
                  "SATIS_KDV", True, "return_date", "total", "entity_id")


# --------------------------------------------------------------------------
# SATIŞ
# --------------------------------------------------------------------------

def _satis(db: Session, cid: int, d: Donem, plan: HesapPlani, v: DonemVerisi) -> list[Fis]:
    bas, son = d.bas_tarih.isoformat(), d.son_tarih.isoformat()
    basliklar = db.execute(
        select(
            orders.c.id, orders.c.order_date, orders.c.document_no, orders.c.status,
            orders.c.payment_method, orders.c.vat_total, orders.c.final_total,
            orders.c.customer_id, customers.c.name, customers.c.tax_number,
        )
        .select_from(
            orders.outerjoin(
                customers,
                and_(
                    customers.c.company_id == orders.c.company_id,
                    customers.c.id == orders.c.customer_id,
                ),
            )
        )
        .where(orders.c.company_id == cid, orders.c.order_date >= bas, orders.c.order_date < son)
        .order_by(orders.c.id)
    ).mappings().all()
    gruplar = db.execute(
        select(
            order_items.c.order_id, order_items.c.vat_rate,
            func.sum(order_items.c.line_subtotal), func.sum(order_items.c.line_vat),
            func.sum(order_items.c.line_total),
        )
        .select_from(
            order_items.join(
                orders,
                and_(
                    orders.c.company_id == order_items.c.company_id,
                    orders.c.id == order_items.c.order_id,
                ),
            )
        )
        .where(
            order_items.c.company_id == cid,
            orders.c.order_date >= bas,
            orders.c.order_date < son,
            orders.c.status.in_(_DEFTER_DURUMLARI),
        )
        .group_by(order_items.c.order_id, order_items.c.vat_rate)
    ).all()
    return _belge_kolu(v, plan, _SATIS, basliklar, gruplar)


# --------------------------------------------------------------------------
# ALIŞ
# --------------------------------------------------------------------------

def _alis(db: Session, cid: int, d: Donem, plan: HesapPlani, v: DonemVerisi) -> list[Fis]:
    bas, son = d.bas_tarih.isoformat(), d.son_tarih.isoformat()
    basliklar = db.execute(
        select(
            purchases.c.id, purchases.c.purchase_date, purchases.c.document_no,
            purchases.c.status, purchases.c.payment_method, purchases.c.vat_total,
            purchases.c.final_total, purchases.c.supplier_id, suppliers.c.name,
            suppliers.c.tax_number,
        )
        .select_from(
            purchases.outerjoin(
                suppliers,
                and_(
                    suppliers.c.company_id == purchases.c.company_id,
                    suppliers.c.id == purchases.c.supplier_id,
                ),
            )
        )
        .where(
            purchases.c.company_id == cid,
            purchases.c.purchase_date >= bas,
            purchases.c.purchase_date < son,
        )
        .order_by(purchases.c.id)
    ).mappings().all()
    gruplar = db.execute(
        select(
            purchase_items.c.purchase_id, purchase_items.c.vat_rate,
            func.sum(purchase_items.c.line_subtotal), func.sum(purchase_items.c.line_vat),
            func.sum(purchase_items.c.line_total),
        )
        .select_from(
            purchase_items.join(
                purchases,
                and_(
                    purchases.c.company_id == purchase_items.c.company_id,
                    purchases.c.id == purchase_items.c.purchase_id,
                ),
            )
        )
        .where(
            purchase_items.c.company_id == cid,
            purchases.c.purchase_date >= bas,
            purchases.c.purchase_date < son,
            purchases.c.status.in_(_DEFTER_DURUMLARI),
        )
        .group_by(purchase_items.c.purchase_id, purchase_items.c.vat_rate)
    ).all()
    return _belge_kolu(v, plan, _ALIS, basliklar, gruplar)


# --------------------------------------------------------------------------
# İADELER — `returns.entity_id` POLİMORFİKTİR (return_type'a göre cari tablosu)
# --------------------------------------------------------------------------

def _iadeler(db: Session, cid: int, d: Donem, plan: HesapPlani, v: DonemVerisi) -> list[Fis]:
    bas, son = d.bas_tarih.isoformat(), d.son_tarih.isoformat()
    musteri_basliklari = db.execute(
        select(
            returns.c.id, returns.c.return_type, returns.c.return_date, returns.c.document_no,
            returns.c.status, returns.c.vat_total, returns.c.total, returns.c.entity_id,
            customers.c.name, customers.c.tax_number,
        )
        .select_from(
            returns.outerjoin(
                customers,
                and_(
                    customers.c.company_id == returns.c.company_id,
                    customers.c.id == returns.c.entity_id,
                ),
            )
        )
        .where(
            returns.c.company_id == cid,
            returns.c.return_type == "sale_return",
            returns.c.return_date >= bas,
            returns.c.return_date < son,
        )
        .order_by(returns.c.id)
    ).mappings().all()
    tedarikci_basliklari = db.execute(
        select(
            returns.c.id, returns.c.return_type, returns.c.return_date, returns.c.document_no,
            returns.c.status, returns.c.vat_total, returns.c.total, returns.c.entity_id,
            suppliers.c.name, suppliers.c.tax_number,
        )
        .select_from(
            returns.outerjoin(
                suppliers,
                and_(
                    suppliers.c.company_id == returns.c.company_id,
                    suppliers.c.id == returns.c.entity_id,
                ),
            )
        )
        .where(
            returns.c.company_id == cid,
            returns.c.return_type == "purchase_return",
            returns.c.return_date >= bas,
            returns.c.return_date < son,
        )
        .order_by(returns.c.id)
    ).mappings().all()
    gruplar = db.execute(
        select(
            return_items.c.return_id, return_items.c.vat_rate,
            func.sum(return_items.c.line_subtotal), func.sum(return_items.c.line_vat),
            func.sum(return_items.c.line_total),
        )
        .select_from(
            return_items.join(
                returns,
                and_(
                    returns.c.company_id == return_items.c.company_id,
                    returns.c.id == return_items.c.return_id,
                ),
            )
        )
        .where(
            return_items.c.company_id == cid,
            returns.c.return_date >= bas,
            returns.c.return_date < son,
            returns.c.status.in_(_DEFTER_DURUMLARI),
        )
        .group_by(return_items.c.return_id, return_items.c.vat_rate)
    ).all()
    satis_id = {int(b["id"]) for b in musteri_basliklari}
    return _belge_kolu(
        v, plan, _SATIS_IADE, musteri_basliklari, [g for g in gruplar if int(g[0]) in satis_id]
    ) + _belge_kolu(
        v, plan, _ALIS_IADE, tedarikci_basliklari, [g for g in gruplar if int(g[0]) not in satis_id]
    )


def _belge_kolu(v: DonemVerisi, plan: HesapPlani, kol: _Kol, basliklar, gruplar) -> list[Fis]:
    """Satış/alış/iade: aynı biçim, farklı hesaplar ve yön (`_Kol`)."""
    belge_gruplari: dict[int, list[tuple[Decimal, Decimal, Decimal, Decimal]]] = defaultdict(list)
    for belge_id, oran, matrah, kdv, toplam in gruplar:
        belge_gruplari[int(belge_id)].append(
            (oran_normalize(oran if oran is not None else 0), _d(matrah), _d(kdv), _d(toplam))
        )
    bekleyen = 0
    fisler: list[Fis] = []
    cari_taraf, karsi_taraf = ("B", "A") if kol.cari_borclu else ("A", "B")
    for b in basliklar:
        durum = b["status"]
        if durum in BEKLEYEN_DURUMLAR:
            bekleyen += 1
        if durum not in STOCK_STATUSES:
            continue
        belge_id = int(b["id"])
        belge_no = (b["document_no"] or "").strip() or f"#{belge_id}"
        gun = _metin_gunu(b[kol.tarih_alani])
        if gun is None:
            v.uyarilar.append(Uyari(
                "TARIH", kol.kaynak, f"{kol.etiket} tarihi okunamadı: {b[kol.tarih_alani]!r}",
                belge_id, belge_no,
            ))
            continue
        # Oran ARTAN: `GROUP BY` sıra vaat etmez; aynı veri aynı fişi üretmeli (§6.3).
        kalemler = sorted(belge_gruplari.get(belge_id, []))
        cari_ad = b["name"]
        aciklama = f"{kol.etiket} {belge_no}" + (f" — {cari_ad}" if cari_ad else "")
        t = SatirToplayici()
        cari_id = b[kol.cari_alani]
        toplam_satir = sum((k[3] for k in kalemler), SIFIR)
        kdv_satir = sum((k[2] for k in kalemler), SIFIR)
        t.ekle(
            plan.hesap(kol.cari_olay, taraf=kol.cari_tipi), cari_taraf, toplam_satir, aciklama,
            cari_tipi=kol.cari_tipi, cari_id=None if cari_id is None else int(cari_id),
        )
        for oran, matrah, kdv, _toplam in kalemler:
            t.ekle(plan.hesap(kol.matrah_olay, oran), karsi_taraf, matrah, aciklama, kdv_orani=oran)
            t.ekle(plan.hesap(kol.kdv_olay, oran), karsi_taraf, kdv, f"{aciklama} KDV %{oran}",
                   kdv_orani=oran)
            v.oran_gruplari.append(OranGrubu(kol.kaynak, belge_id, oran, matrah, kdv))
        # G3: başlık yalnız KIYAS için; tutar SATIRDAN.
        for ad, baslik, satir in (("KDV", b["vat_total"], kdv_satir),
                                  ("toplam", b[kol.toplam_alani], toplam_satir)):
            if baslik is not None and _d(baslik) != satir:
                v.uyarilar.append(Uyari(
                    "G3", kol.kaynak,
                    f"{kol.etiket} {belge_no}: başlık {ad} {_d(baslik)} ≠ satırlar {satir}; "
                    "fiş SATIRDAN kuruldu",
                    belge_id, belge_no, _d(baslik) - satir,
                ))
        fisler.append(Fis(
            fis_no=fis_no(kol.kaynak, belge_id),
            fis_tarihi=gun,
            belge_tipi="invoice",
            belge_no=belge_no,
            belge_tarihi=gun,
            # İadenin ödeme yöntemi YOK: cari mahsubudur (K11 alanı boş kalamaz).
            odeme_yontemi=(b.get("payment_method") or "credit"),
            kaynak=kol.kaynak,
            satirlar=t.satirlar(),
            cari_ad=cari_ad,
            cari_vkn=b["tax_number"],
        ))
    if bekleyen:
        v.uyarilar.append(Uyari(
            "G9", kol.kaynak,
            f"{bekleyen} {kol.etiket.lower()} belgesi pending/draft durumunda; fişe ve özete GİRMEDİ",
        ))
    return fisler


# --------------------------------------------------------------------------
# SERVİS FATURASI — değişmez belge; döviz TRY'ye çevrilir (G4)
# --------------------------------------------------------------------------

def _servis(db: Session, cid: int, d: Donem, plan: HesapPlani, v: DonemVerisi) -> list[Fis]:
    bas = bindparam("svf_bas", d.bas_an, type_=_UtcAn())
    son = bindparam("svf_son", d.son_an, type_=_UtcAn())
    basliklar = db.execute(
        select(
            invoices.c.id, invoices.c.invoice_number, invoices.c.status, invoices.c.currency,
            invoices.c.exchange_rate, invoices.c.customer_snapshot, invoices.c.created_at,
        )
        .where(invoices.c.company_id == cid, invoices.c.created_at >= bas, invoices.c.created_at < son)
        .order_by(invoices.c.id)
    ).mappings().all()
    gruplar = db.execute(
        select(
            invoice_items.c.invoice_id, invoice_items.c.tax_rate,
            func.sum(invoice_items.c.tax_amount), func.sum(invoice_items.c.total),
            func.sum(invoice_items.c.customer_payable), func.sum(invoice_items.c.company_payable),
        )
        .select_from(
            invoice_items.join(
                invoices,
                and_(
                    invoices.c.company_id == invoice_items.c.company_id,
                    invoices.c.id == invoice_items.c.invoice_id,
                ),
            )
        )
        .where(
            invoice_items.c.company_id == cid,
            invoices.c.created_at >= bas,
            invoices.c.created_at < son,
            invoices.c.status == "ISSUED",
        )
        .group_by(invoice_items.c.invoice_id, invoice_items.c.tax_rate)
    ).all()
    belge_gruplari: dict[int, list] = defaultdict(list)
    for belge_id, oran, kdv, toplam, musteri, garanti in gruplar:
        belge_gruplari[int(belge_id)].append(
            (oran_normalize(oran), _d(kdv), _d(toplam), _d(musteri), _d(garanti))
        )
    fisler: list[Fis] = []
    for b in basliklar:
        if b["status"] != "ISSUED":
            continue
        belge_id = int(b["id"])
        numara = str(b["invoice_number"])
        an = _utc(b["created_at"])
        gun = an.astimezone(ISTANBUL).date()
        try:
            musteri = json.loads(b["customer_snapshot"] or "{}")
        except ValueError:
            musteri = {}
        if not isinstance(musteri, dict):
            musteri = {}
        cari_ad = musteri.get("name")
        cari_id = musteri.get("id")
        kur = Decimal(str(b["exchange_rate"] or 1))
        doviz = (b["currency"] or "TRY").upper()
        if doviz != "TRY":
            v.uyarilar.append(Uyari(
                "G4", "SERVIS_FATURA",
                f"Servis faturası {numara} {doviz} — kur {kur} ile TRY'ye çevrildi",
                belge_id, numara,
            ))
        utc_ay = an.strftime("%Y-%m")
        if utc_ay != d.metin:
            v.uyarilar.append(Uyari(
                "G5", "SERVIS_FATURA",
                f"Servis faturası {numara} UTC'ye göre {utc_ay}, İstanbul saatine göre "
                f"{d.metin} — İSTANBUL ayına yazıldı",
                belge_id, numara,
            ))
        aciklama = f"Servis faturası {numara}" + (f" — {cari_ad}" if cari_ad else "")
        t = SatirToplayici()
        gelir: list[tuple] = []
        for oran, kdv, toplam, musteri_payi, garanti_payi in sorted(belge_gruplari.get(belge_id, [])):
            if musteri_payi + garanti_payi != toplam:
                v.uyarilar.append(Uyari(
                    "G3", "SERVIS_FATURA",
                    f"Servis faturası {numara} %{oran}: müşteri+garanti payı "
                    f"{musteri_payi + garanti_payi} ≠ satır toplamı {toplam}",
                    belge_id, numara, musteri_payi + garanti_payi - toplam,
                ))
            # TRY'ye çevrim SATIR GRUBUNDA, fark çıkarmayla: matrah = toplam − KDV
            # ve garanti payı = toplam − müşteri payı. Üç bağımsız yuvarlama
            # fişi bir kuruşla dengesiz bırakabilirdi.
            toplam_try = money(toplam * kur)
            kdv_try = money(kdv * kur)
            matrah_try = toplam_try - kdv_try
            musteri_try = money(musteri_payi * kur)
            garanti_try = toplam_try - musteri_try
            t.ekle(plan.hesap("SATIS_CARI", taraf="CUSTOMER"), "B", musteri_try, aciklama,
                   cari_tipi="CUSTOMER", cari_id=None if cari_id is None else int(cari_id))
            t.ekle(plan.hesap("SATIS_CARI", taraf="CUSTOMER"), "B", garanti_try,
                   f"{aciklama} (garanti payı)")
            gelir.append((oran, matrah_try, kdv_try))
            v.oran_gruplari.append(OranGrubu("SERVIS_FATURA", belge_id, oran, matrah_try, kdv_try))
        for oran, matrah_try, kdv_try in gelir:
            t.ekle(plan.hesap("SERVIS_GELIR", oran), "A", matrah_try, aciklama, kdv_orani=oran)
            t.ekle(plan.hesap("SATIS_KDV", oran), "A", kdv_try, f"{aciklama} KDV %{oran}",
                   kdv_orani=oran)
        fisler.append(Fis(
            fis_no=fis_no("SERVIS_FATURA", numara),
            fis_tarihi=gun,
            belge_tipi="invoice",
            belge_no=numara,
            belge_tarihi=gun,
            odeme_yontemi="credit",
            kaynak="SERVIS_FATURA",
            satirlar=t.satirlar(),
            cari_ad=cari_ad,
            cari_vkn=musteri.get("tax_number"),
        ))
    return fisler


# --------------------------------------------------------------------------
# MÜSTAHSİL MAKBUZU — KDV YOK (`app/mustahsil.py`), stopaj + Bağ-Kur
# --------------------------------------------------------------------------

def _mustahsil(db: Session, cid: int, d: Donem, plan: HesapPlani, v: DonemVerisi) -> list[Fis]:
    bas = bindparam("mm_bas", d.bas_an, type_=_UtcAn())
    son = bindparam("mm_son", d.son_an, type_=_UtcAn())
    basliklar = db.execute(
        select(
            producer_receipts.c.id, producer_receipts.c.receipt_no, producer_receipts.c.status,
            producer_receipts.c.issued_at, producer_receipts.c.supplier_id, suppliers.c.name,
            suppliers.c.tax_number,
        )
        .select_from(
            producer_receipts.outerjoin(
                suppliers,
                and_(
                    suppliers.c.company_id == producer_receipts.c.company_id,
                    suppliers.c.id == producer_receipts.c.supplier_id,
                ),
            )
        )
        .where(
            producer_receipts.c.company_id == cid,
            producer_receipts.c.issued_at >= bas,
            producer_receipts.c.issued_at < son,
        )
        .order_by(producer_receipts.c.id)
    ).mappings().all()
    gruplar = db.execute(
        select(
            producer_receipt_items.c.receipt_id,
            func.sum(producer_receipt_items.c.line_gross),
            func.sum(producer_receipt_items.c.withholding_amount),
            func.sum(producer_receipt_items.c.social_security_amount),
            func.sum(producer_receipt_items.c.line_net),
        )
        .select_from(
            producer_receipt_items.join(
                producer_receipts,
                and_(
                    producer_receipts.c.company_id == producer_receipt_items.c.company_id,
                    producer_receipts.c.id == producer_receipt_items.c.receipt_id,
                ),
            )
        )
        .where(
            producer_receipt_items.c.company_id == cid,
            producer_receipts.c.issued_at >= bas,
            producer_receipts.c.issued_at < son,
            producer_receipts.c.status == "issued",
        )
        .group_by(producer_receipt_items.c.receipt_id)
    ).all()
    toplamlar = {int(g[0]): tuple(_d(x) for x in g[1:]) for g in gruplar}
    # G8: makbuza bağlı alışın KDV'li kalemi — olmayan indirilecek KDV riski.
    bagli = db.execute(
        select(purchase_items.c.purchase_id, func.sum(purchase_items.c.line_vat))
        .select_from(
            purchase_items.join(
                producer_receipts,
                and_(
                    producer_receipts.c.company_id == purchase_items.c.company_id,
                    producer_receipts.c.purchase_id == purchase_items.c.purchase_id,
                ),
            )
        )
        .where(
            purchase_items.c.company_id == cid,
            producer_receipts.c.issued_at >= bas,
            producer_receipts.c.issued_at < son,
            producer_receipts.c.status == "issued",
            purchase_items.c.vat_rate != 0,
        )
        .group_by(purchase_items.c.purchase_id)
    ).all()
    for alis_id, kdv in bagli:
        v.uyarilar.append(Uyari(
            "G8", "MUSTAHSIL",
            f"Alış #{alis_id} bir müstahsil makbuzuna bağlı ama KDV'li kalem taşıyor "
            f"({_d(kdv)} TL) — çiftçi KDV mükellefi değildir; indirilecek KDV'yi denetleyin",
            int(alis_id), None, _d(kdv),
        ))
    fisler: list[Fis] = []
    for b in basliklar:
        if b["status"] != "issued":
            continue
        belge_id = int(b["id"])
        numara = str(b["receipt_no"] or belge_id)
        an = _utc(b["issued_at"])
        gun = _istanbul_gunu(b["issued_at"])
        if an is not None:
            utc_ay = an.strftime("%Y-%m")
            if utc_ay != d.metin:
                v.uyarilar.append(Uyari(
                    "G5", "MUSTAHSIL",
                    f"Müstahsil makbuzu {numara} UTC'ye göre {utc_ay}, İstanbul saatine göre "
                    f"{d.metin} — İSTANBUL ayına yazıldı",
                    belge_id, numara,
                ))
        brut, stopaj, bagkur, net = toplamlar.get(belge_id, (SIFIR,) * 4)
        mm = v.mustahsil
        mm.belge_sayisi += 1
        mm.brut += brut
        mm.stopaj += stopaj
        mm.bagkur += bagkur
        mm.net += net
        cari_ad = b["name"]
        aciklama = f"Müstahsil makbuzu {numara}" + (f" — {cari_ad}" if cari_ad else "")
        t = SatirToplayici()
        t.ekle(plan.hesap("ALIS_STOK"), "B", brut, aciklama)
        t.ekle(plan.hesap("ALIS_CARI", taraf="SUPPLIER"), "A", net, aciklama,
               cari_tipi="SUPPLIER", cari_id=int(b["supplier_id"]))
        t.ekle(plan.hesap("MUSTAHSIL_STOPAJ"), "A", stopaj, f"{aciklama} stopaj")
        t.ekle(plan.hesap("MUSTAHSIL_BAGKUR"), "A", bagkur, f"{aciklama} Bağ-Kur")
        fisler.append(Fis(
            fis_no=fis_no("MUSTAHSIL", numara),
            fis_tarihi=gun,
            # e-Defter `documentType` değeri DOĞRULANMADI (keşif §3.4).
            belge_tipi="receipt",
            belge_no=numara,
            belge_tarihi=gun,
            odeme_yontemi="credit",
            kaynak="MUSTAHSIL",
            satirlar=t.satirlar(),
            cari_ad=cari_ad,
            cari_vkn=b["tax_number"],
        ))
    return fisler


_KOLLAR = (_satis, _alis, _iadeler, _servis, _mustahsil)


def donem_oku(db: Session, cid: int, donem: Donem, plan: HesapPlani) -> DonemVerisi:
    """Dönemin bütün kolları; dengesiz fiş `reddedilen`e ayrılır (YAZILMAZ)."""
    v = DonemVerisi(donem)
    tum: list[Fis] = []
    for kol in _KOLLAR:
        tum.extend(kol(db, cid, donem, plan, v))
    tum.sort(key=lambda f: (f.fis_tarihi, f.fis_no))
    v.fisler, v.reddedilen = ayikla(tum)
    return v


def fisleri_uret(db: Session, cid: int, donem: Donem, plan: HesapPlani) -> Iterator[Fis]:
    """Dönem → `Iterator[Fis]` (yalnız kapıdan geçenler)."""
    yield from donem_oku(db, cid, donem, plan).fisler

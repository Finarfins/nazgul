"""Niyet yürütücüsü: araç adı → gerçek okuma → `niyet.cevap_yaz`ın beklediği sözlük.

WA3-core `NiyetYurutucu` protokolünü ve tek gerçekleştirimi `NoOpYurutucu`yu
açmıştı ("veritabanına bakan gerçekleştirim sonraki dilimin işi"). BU O
DİLİM.

--- YEDİ ARACIN HEPSİ OKUMA ---------------------------------------------

`niyet.ARAC_BEYAZ_LISTESI` bu turda DEĞİŞMEDİ ve yazma niyeti buraya HİÇ
gelmez: `service.py` tahsilat niyetini yürütücüye vermeden önce
`WA4_MESAJI` ile cevaplar. Yani bu modülde `INSERT`/`UPDATE`/`DELETE`
YOKTUR — yalnız `SELECT`.

--- KİRACI SINIRI: `company_id` ARGÜMANDIR, İSTEKTEN GELMEZ -------------

`kos` bir `Kimlik` taşır ve her sorgu o kimliğin `company_id`siyle koşar.
Kimlik `baglam.kimlik_secimi`den gelir: aktif bağlantı + aktif kullanıcı +
aktif firma + üyelik. Mesajdan gelen HİÇBİR firma iddiası kabul edilmez —
zaten mesajda öyle bir alan yoktur.

MUTASYON ADIYLA: `company_id` yüklemini bir araçtan düşürmek, komşu
firmanın AYNI ADI taşıyan müşterisini döndürür. Kapı
`tests/test_wa3_worker.py::test_KIRACI_YALITIMI_ayni_ad_komsu_firmada`
tam bu kurguyu (iki firma, aynı müşteri adı, farklı bakiye) kuruyor.

--- SORGULAR KOPYALANMADI, UÇLARDAN ÇAĞRILIYOR --------------------------

Beş araç uçların KENDİ fonksiyonlarını çağırır ve bu fonksiyonlar bu tur
için `Request` bağımlılığından ARINDIRILDI (gövde taşındı, tek harfi
değişmedi):

* `cari_durum`              → `routers/customers.py::musteri_satirlari`
* `parca_stok`              → `routers/products.py::urun_satirlari`
* `donem_ozeti`             → `routers/reports.py::ozet_verisi`
* `en_cok_satan_parcalar`   → `routers/reports.py::ozet_verisi` (top_products)
* `alacak_yaslandirma`      → `routers/reports.py::yaslandirma_verisi`
* `kritik_stok`             → `routers/dashboard.py::kritik_urunler`

Kopyalanmış bir bakiye formülü, iki yüzeyin aynı müşteri için farklı sayı
söylediği güne kadar sessiz kalırdı ve o gün hangisinin doğru olduğu
BİLİNEMEZDİ.

TEK İSTİSNA `parca_satis_gecmisi`: depoda "şu parça bu dönemde kaç adet
satıldı" sorusunu soran bir uç YOK (arandı; `reports/summary`nin
`top_products`ı SIRALAMA döndürür, tek parça sorgusu değil). Sorgusu
burada yazılı ve kiracı yüklemini AÇIKÇA taşıyor.

--- İÇE AKTARMALAR GÖVDEDE ----------------------------------------------

Uç modülleri FastAPI çeker. `app/whatsapp/__init__.py` "bu paketi içe
aktaran bir test ya da işçi FastAPI uygulamasını yüklemek ZORUNDA
OLMAMALI" diyor; bu modülü içe aktarmak da o yükü getirmemeli. İçe
aktarma çağrı anındadır.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..business_time import business_today
from .eslestirme import Kimlik
from .niyet import ARAC_BEYAZ_LISTESI

log = logging.getLogger("nazgul.whatsapp.yurutucu")

#: WhatsApp listesi kısadır; uçların 500/300'lük sınırları burada anlamsız
#: ve pahalıdır. Beş satırdan fazlası zaten `niyet._WA_LISTE_SINIRI` ile
#: kırpılıyor — sınırı SORGUYA taşımak, kırpılacak satırı hiç okumamaktır.
KANAL_SINIRI = 5

#: `cari_durum` çok eşleşmede aday adlarını listeliyor; uçtan biraz fazlası
#: okunuyor ki "birden fazla kayıt var" cümlesi doğru olsun.
CARI_ADAY_SINIRI = 6


@dataclass(frozen=True, slots=True)
class Aralik:
    """Bir dönem etiketinin çözülmüş hâli."""

    baslangic: date
    bitis: date
    etiket: str


#: `niyet._DONEM_IKILI` / `_DONEM_TEKLI`nin ÜRETTİĞİ ONBİR etiketin TAMAMI.
#: Eksik bırakılan bir etiket sessizce "bu ay"a düşerdi ve kullanıcı
#: "geçen yıl" diye sorup BU ayın rakamını alırdı. Kapı:
#: `tests/test_wa3_worker.py::test_NIYETIN_URETTIGI_HER_DONEM_ETIKETI_COZULUYOR`
DONEM_ADLARI: dict[str, str] = {
    "bugun": "Bugün",
    "dun": "Dün",
    "bu_hafta": "Bu hafta",
    "bu_ay": "Bu ay",
    "gecen_ay": "Geçen ay",
    "bu_ceyrek": "Bu çeyrek",
    "bu_yil": "Bu yıl",
    "gecen_yil": "Geçen yıl",
    "son_7_gun": "Son 7 gün",
    "son_30_gun": "Son 30 gün",
    "son_90_gun": "Son 90 gün",
}


def donem_araligi(etiket: str, bugun: date | None = None) -> Aralik:
    """Dönem etiketini KAPALI tarih aralığına çevirir (iki uç dâhil).

    Bilinmeyen etiket "bu ay"a düşer; bu bir varsayılan DEĞİL, fail-safe:
    `niyet` yalnız :data:`DONEM_ADLARI`daki etiketleri üretir ve bunu bir
    test çiviliyor, yani buraya düşmek bir kusurun kendisidir.
    """
    gun = bugun or business_today()
    ad = DONEM_ADLARI.get(etiket, DONEM_ADLARI["bu_ay"])

    if etiket == "bugun":
        return Aralik(gun, gun, ad)
    if etiket == "dun":
        dun = gun - timedelta(days=1)
        return Aralik(dun, dun, ad)
    if etiket == "bu_hafta":
        return Aralik(gun - timedelta(days=gun.weekday()), gun, ad)
    if etiket == "gecen_ay":
        ayin_ilki = gun.replace(day=1)
        gecen_son = ayin_ilki - timedelta(days=1)
        return Aralik(gecen_son.replace(day=1), gecen_son, ad)
    if etiket == "bu_ceyrek":
        ceyrek_ilk_ay = 3 * ((gun.month - 1) // 3) + 1
        return Aralik(gun.replace(month=ceyrek_ilk_ay, day=1), gun, ad)
    if etiket == "bu_yil":
        return Aralik(gun.replace(month=1, day=1), gun, ad)
    if etiket == "gecen_yil":
        return Aralik(
            date(gun.year - 1, 1, 1), date(gun.year - 1, 12, 31), ad
        )
    if etiket in {"son_7_gun", "son_30_gun", "son_90_gun"}:
        # "SON 7 GÜN" BUGÜNÜ İÇERİR: yedi günlük pencere bugün dâhil yedi
        # gündür, altı değil.
        gun_sayisi = int(etiket.split("_")[1])
        return Aralik(gun - timedelta(days=gun_sayisi - 1), gun, ad)
    return Aralik(gun.replace(day=1), gun, ad)


def _ondalik(deger: Any) -> Decimal:
    """Sürücüden ne gelirse gelsin `Decimal`. Çözülemeyen değer SIFIRDIR.

    Ondalık sözleşmesi (`test_v2_9_decimal_contract`) gereği `float` ADI
    bile geçmiyor; `str` üzerinden dolaşmak psycopg'nin `Decimal`i ile
    sqlite3'ün `int`ini AYNI yoldan geçirir.
    """
    if deger is None:
        return Decimal("0")
    try:
        return Decimal(str(deger))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _metin(deger: Any) -> str:
    return "" if deger is None else str(deger)


# ---------------------------------------------------------------------------
# Yedi araç
# ---------------------------------------------------------------------------


def cari_durum(db: Session, kimlik: Kimlik, argumanlar: dict[str, Any]) -> dict[str, Any]:
    from ..routers.customers import musteri_satirlari

    aranan = _metin(argumanlar.get("musteri")).strip()
    satirlar = musteri_satirlari(
        db, kimlik.company_id, q=aranan, limit=CARI_ADAY_SINIRI
    )
    if not satirlar:
        return {"bulunan": 0, "aranan": aranan}
    if len(satirlar) > 1:
        # Çok eşleşme: `niyet._cari_yaz` tek müşteri yazar, bu yüzden
        # adayları AYRI alanda döndürüyoruz ve cevabı `service` kuruyor.
        return {
            "bulunan": len(satirlar),
            "aranan": aranan,
            "adaylar": [_metin(s["name"]) for s in satirlar],
        }
    satir = satirlar[0]
    return {
        "bulunan": 1,
        "aranan": aranan,
        "musteri": _metin(satir["name"]),
        "acik_bakiye": _ondalik(satir["current_balance"]),
        "vadesi_gecen": _ondalik(satir["overdue_amount"]),
        "son_alisveris_tarihi": satir["last_activity"] or None,
    }


def parca_stok(db: Session, kimlik: Kimlik, argumanlar: dict[str, Any]) -> dict[str, Any]:
    from ..routers.products import urun_satirlari

    aranan = _metin(argumanlar.get("arama")).strip()
    satirlar = urun_satirlari(db, kimlik.company_id, q=aranan, limit=KANAL_SINIRI)
    parcalar = [
        {
            "ad": _metin(s["name"]),
            "parca_no": _metin(s["product_code"]) or None,
            "stok": _ondalik(s["stock"]),
            "birim": _metin(s["unit"]),
            "raf": _metin(s["location"]) or None,
            "kritik_seviye": _ondalik(s["critical_stock"]),
        }
        for s in satirlar
    ]
    return {
        "bulunan_kayit_sayisi": len(parcalar),
        "aranan": aranan,
        "parcalar": parcalar,
    }


def donem_ozeti(db: Session, kimlik: Kimlik, argumanlar: dict[str, Any]) -> dict[str, Any]:
    from ..routers.reports import ozet_verisi

    aralik = donem_araligi(_metin(argumanlar.get("donem")) or "bu_ay")
    ozet = ozet_verisi(
        db,
        kimlik.company_id,
        aralik.baslangic.isoformat(),
        aralik.bitis.isoformat(),
    )
    ciro = _ondalik(ozet.get("sales_total"))
    alis = _ondalik(ozet.get("purchases_total"))
    return {
        "donem": aralik.etiket,
        "tarih_araligi": {
            "baslangic": aralik.baslangic.isoformat(),
            "bitis": aralik.bitis.isoformat(),
        },
        "ciro": ciro,
        "satis_adedi": int(ozet.get("sales_count") or 0),
        "alis_toplami": alis,
        "alis_adedi": int(ozet.get("purchases_count") or 0),
        # Uçtaki adı `gross_difference`; KABA kâr olduğu adında yazılı,
        # gider ve maliyet DÜŞÜLMEZ. Şablon da "Kaba kâr" yazıyor.
        "kaba_kar": ciro - alis,
        "tahsilat": _ondalik(ozet.get("collections")),
        "odeme": _ondalik(ozet.get("payments")),
    }


def alacak_yaslandirma(
    db: Session, kimlik: Kimlik, argumanlar: dict[str, Any]
) -> dict[str, Any]:
    from ..routers.reports import AGING_BUCKETS, yaslandirma_verisi

    veri = yaslandirma_verisi(db, kimlik.company_id)
    musteriler = list(veri.get("customers") or [])
    toplamlar = veri.get("totals") or {}
    # Uç METİN döndürüyor (`_money_string`); şablon `Decimal`e çeviriyor,
    # ama sıralama METİN üzerinde yapılamaz — "9" > "10" olurdu.
    musteriler.sort(key=lambda m: _ondalik(m.get("total")), reverse=True)
    return {
        "toplam_alacak": _ondalik(toplamlar.get("total")),
        "musteri_sayisi": len(musteriler),
        "yas_gruplari": {
            kova: _ondalik(toplamlar.get(kova)) for kova in AGING_BUCKETS
        },
        "en_yuksek_bakiyeli_musteriler": [
            {
                "customer_name": _metin(m.get("customer_name")),
                "total": _ondalik(m.get("total")),
            }
            for m in musteriler[:KANAL_SINIRI]
        ],
    }


def kritik_stok(db: Session, kimlik: Kimlik, argumanlar: dict[str, Any]) -> dict[str, Any]:
    from ..routers.dashboard import kritik_urunler

    satirlar = kritik_urunler(db, kimlik.company_id, KANAL_SINIRI)
    urunler = [
        {
            "ad": _metin(s.get("name")),
            "kod": _metin(s.get("oem_number")) or None,
            "birim": _metin(s.get("unit")),
            "mevcut_stok": _ondalik(s.get("stock")),
            "kritik_seviye": max(
                _ondalik(s.get("critical_stock")), _ondalik(s.get("minimum_stock"))
            ),
        }
        for s in satirlar
    ]
    # ÖLÇÜT ADIYLA DÖNÜYOR. Eşiği HİÇ girilmemiş bir katalogda pano
    # ölçütü `stock <= 0`a indirger ve o listeye "kritik" demek yanıltıcı
    # olurdu (`niyet._kritik_yaz` bu ayrımı YAZIYOR).
    esik_var = any(u["kritik_seviye"] > 0 for u in urunler)
    if esik_var:
        return {
            "liste_olcutu": "kritik_seviye_alti",
            "kritik_seviyedeki_urun_sayisi": len(urunler),
            "urunler": urunler,
        }
    return {
        "liste_olcutu": "stok_sifir_ve_alti",
        "stogu_tukenmis_urun_sayisi": len(urunler),
        "urunler": urunler,
    }


def en_cok_satan_parcalar(
    db: Session, kimlik: Kimlik, argumanlar: dict[str, Any]
) -> dict[str, Any]:
    from ..routers.reports import ozet_verisi

    aralik = donem_araligi(_metin(argumanlar.get("donem")) or "bu_ay")
    ozet = ozet_verisi(
        db,
        kimlik.company_id,
        aralik.baslangic.isoformat(),
        aralik.bitis.isoformat(),
    )
    urunler = list(ozet.get("top_products") or [])[:KANAL_SINIRI]
    return {
        "donem": aralik.etiket,
        "urunler": [
            {
                "product_name": _metin(u.get("product_name")),
                "quantity": _ondalik(u.get("quantity")),
                "total": _ondalik(u.get("total")),
            }
            for u in urunler
        ],
    }


def parca_satis_gecmisi(
    db: Session, kimlik: Kimlik, argumanlar: dict[str, Any]
) -> dict[str, Any]:
    """Tek parçanın dönem içi satış toplamı. Depoda karşılığı olan uç YOK.

    `orders`ın muhasebe durumu yüklemi uçlarla AYNI merkezî yardımcıdan
    (`document_engine.accounting_document_status_sql`) geliyor: iptal
    edilmiş ve taslak belge sayılmaz, içe aktarılmış taslak sayılır.
    Kopya bir yüklem yazmak, bu kanalın iptal edilmiş satışı sayması
    demekti.
    """
    from ..document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
    from ..routers.reports import _normalized_date_sql

    aranan = _metin(argumanlar.get("arama")).strip()
    aralik = donem_araligi(_metin(argumanlar.get("donem")) or "bu_yil")
    lehce = db.bind.dialect.name if db.bind is not None else "sqlite"
    tarih = _normalized_date_sql("o.order_date", lehce)
    satir = db.execute(
        text(
            f"""SELECT COALESCE(SUM(oi.quantity),0) adet,
            COALESCE(SUM(oi.line_total),0) tutar,
            COUNT(DISTINCT o.id) belge,
            MAX({tarih}) son
            FROM order_items oi JOIN orders o ON o.id=oi.order_id
            WHERE o.company_id=:cid
              AND {accounting_document_status_sql(status_column='o.status', note_column='o.note')}
              AND {tarih}>=:df AND {tarih}<=:dt
              AND LOWER(COALESCE(oi.product_name,'')) LIKE LOWER(:q)"""
        ),
        {
            "cid": kimlik.company_id,
            "q": f"%{aranan}%",
            "df": aralik.baslangic.isoformat(),
            "dt": aralik.bitis.isoformat(),
            "sales_import_note": SALES_IMPORT_NOTE,
        },
    ).mappings().one()
    return {
        "aranan": aranan,
        "donem": aralik.etiket,
        "satilan_adet": _ondalik(satir["adet"]),
        "toplam_tutar": _ondalik(satir["tutar"]),
        "belge_sayisi": int(satir["belge"] or 0),
        "son_satis_tarihi": satir["son"] or None,
    }


#: Araç adı → gövde. Anahtar kümesi `niyet.ARAC_BEYAZ_LISTESI` ile BİREBİR
#: aynı olmak ZORUNDA; ayrışma bir kapıyla ölçülüyor
#: (`test_YEDI_ARACIN_TAMAMI_YURUTULEBILIYOR`). Eksik bir gövde, beyaz
#: listeden geçmiş bir aracın `KeyError` ile DEAD üretmesi demekti.
ARAC_GOVDELERI = {
    "cari_durum": cari_durum,
    "parca_stok": parca_stok,
    "donem_ozeti": donem_ozeti,
    "alacak_yaslandirma": alacak_yaslandirma,
    "kritik_stok": kritik_stok,
    "en_cok_satan_parcalar": en_cok_satan_parcalar,
    "parca_satis_gecmisi": parca_satis_gecmisi,
}


class VeritabaniYurutucu:
    """`niyet.NiyetYurutucu` protokolünün GERÇEK gerçekleştirimi.

    Tek örnek TEK kimliğe bağlıdır: `kos` çağrısı firma seçmez, kurulurken
    seçilmiştir. Bu bilerek: bir yürütücüye argümanla firma geçirilebilseydi,
    argümanı mesajdan dolduran bir kod yolu bir gün yazılabilirdi.
    """

    def __init__(self, db: Session, kimlik: Kimlik) -> None:
        self._db = db
        self._kimlik = kimlik

    def kos(self, arac: str, argumanlar: dict[str, Any]) -> dict[str, Any]:
        if arac not in ARAC_BEYAZ_LISTESI:
            raise KeyError(arac)
        return ARAC_GOVDELERI[arac](self._db, self._kimlik, dict(argumanlar))


__all__ = [
    "ARAC_GOVDELERI",
    "Aralik",
    "CARI_ADAY_SINIRI",
    "DONEM_ADLARI",
    "KANAL_SINIRI",
    "VeritabaniYurutucu",
    "alacak_yaslandirma",
    "cari_durum",
    "donem_araligi",
    "donem_ozeti",
    "en_cok_satan_parcalar",
    "kritik_stok",
    "parca_satis_gecmisi",
    "parca_stok",
]

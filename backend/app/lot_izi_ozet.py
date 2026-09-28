"""F10-4a — PARTİ İZİNİN SAF BİRLEŞTİRMESİ. SQL YOK, I/O YOK.

`app/lot_izi.py` üç hopun HAM satırlarını okur; bu modül onları geri çağırma
önizlemesine çevirir. Ayrı durması bilinçlidir: iade düşme, perakende ayrımı
ve müşteri toplamı veritabanısız, tek bir girdiyle birim testte ölçülebilir
ve bu dosyaya bir sorgu girerse `lot_izi` ile ikiye bölünmüş bir SQL yüzeyi
doğar.

--- KURALLAR (keşif §2.2, §3, K3, K8) -----------------------------------------

* Satış (`orders`) ve irsaliye (`delivery_notes`) çıkışı ALICININ satırına
  yazılır. Hareket miktarı işaretlidir (çıkış eksi); alıcıya `-q` yazılır.
* Satışa bağlı iade (`returns.source_type='order'`) KAYNAK siparişin
  alıcısından düşülür. Kaynaksız iade partiye DÖNMEZ (`lot_id` NULL) ve
  `gaps`te `kaynaksiz_iade` olarak görünür (G2).
* POS perakende (`pos_system_customers` eşlemesindeki sistem carisi) ALICI
  DEĞİLDİR: miktar `pos_retail` kovasına gider, `customers[]`e ASLA girmez
  (K8). Kimliksiz bir alıcıya mesaj adayı gibi davranmak geri çağırmanın
  "herkesi bulduk" demesi olurdu.
* Alıcı `has_phone` ve `has_consent` taşır; ikisinden biri yoksa durumu
  `manual_pending`dir (K3: rıza kapısı DELİNMEZ, alıcı elle aranır).
* Transfer iç harekettir (kardeşler arası, net sıfır) ve alıcı üretmez.
* `balance` her önizlemede hesaplanır: giren + diğer = alıcılar + perakende
  + tedarikçiye iade + eldeki; `difference` sıfır değilse rapor
  izleyemediği miktarı ADIYLA söyler (§8 risk 1).
* H117: her boşluk satırı arkasındaki hareketleri `ornekler`de KİMLİĞİYLE
  taşır (belge türü/kimliği/no, cari KİMLİĞİ, tarih, miktar), en çok
  `ORNEK_SINIRI`, SQL'in (tarih, id) sırasıyla — burada YENİDEN SIRALANMAZ.
  Daha fazlası varsa `ornek_kesildi`. Cari ADI/telefonu EKLENMEZ.
* H116: `uyarilar` — kardeş depoda mutabakat kovası `SAPMA` (depo başına,
  fark ile), kaynaksız satış iadesi (boşluk satırı başına) ve `defter_bosaldi`
  damgalı partisiz çıkış (depo başına). Temiz partide boş liste.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

#: Alıcı durumları. `notifiable`: telefon + rıza var; `manual_pending`:
#: biri eksik, elle aranacak (K3).
DURUM_BILDIRILEBILIR = "notifiable"
DURUM_ELLE = "manual_pending"

#: Partisiz hareketlerin boşluk adları (keşif §3). Eşleşmeyen her tür
#: `partisiz_hareket` olur — sessizce DÜŞMEZ.
BOSLUK_KAYNAKSIZ_IADE = "kaynaksiz_iade"
BOSLUK_TARLA = "tarla_hareketi"
BOSLUK_TRANSFER = "partisiz_transfer"
BOSLUK_DIGER = "partisiz_hareket"

#: H117 — boşluk satırı başına en çok bu kadar örnek hareket.
ORNEK_SINIRI = 20

#: H116 — uyarı kodları (şemadaki `Literal` ile AYNI küme).
UYARI_SAPMA = "mutabakat_sapma"
UYARI_KAYNAKSIZ_IADE = "kaynaksiz_iade"
UYARI_DEFTER_BOSALDI = "defter_bosaldi"

#: `parti_mutabakat.SAPMA`nın değeri. O modül SQL taşır ve buraya ithal
#: edilmez; eşitlik `tests/test_h116_h117_geri_cagirma.py`de çivili.
KOVA_SAPMA = "SAPMA"

_SIFIR = Decimal("0")


def tarih_metni(deger: Any) -> str | None:
    """Tarih/damga -> `YYYY-MM-DD`. SQLite `str`, PostgreSQL `date` verir."""
    if deger is None:
        return None
    if isinstance(deger, datetime):
        return deger.date().isoformat()
    if isinstance(deger, date):
        return deger.isoformat()
    metin = str(deger).strip()
    return metin[:10] if metin else None


def _ondalik(deger: Any) -> Decimal:
    if deger is None:
        return _SIFIR
    if isinstance(deger, Decimal):
        return deger
    return Decimal(str(deger))


def bosluk_adi(reference_type: Any, movement_type: Any) -> str:
    """Partisiz bir hareket türünün boşluk adı (keşif §3 G2/G4/G5-G6)."""
    if reference_type == "returns" and movement_type == "sale_return":
        return BOSLUK_KAYNAKSIZ_IADE
    if reference_type == "field_integration_event":
        return BOSLUK_TARLA
    if reference_type == "transfer":
        return BOSLUK_TRANSFER
    return BOSLUK_DIGER


def _ornek(iz: Any, reference_type: Any, reference_id: Any, tarih: Any, miktar: Any) -> dict[str, Any]:
    """Bir boşluk hareketinin KİMLİĞİ (H117). Başlık HOP 3'te okunduysa belge
    no ve cari kimliği ondan gelir; tarla/transfer/iş emri gibi başlığı cari
    taşımayan türlerde ikisi de `None`dır. Satış iadesinde cari `entity_id`dir;
    alış iadesinde `entity_id` tedarikçidir ve cari SAYILMAZ."""
    belge_no = None
    cari_id = None
    if reference_id is not None:
        kimlik = int(reference_id)
        if reference_type in ("orders", "delivery_notes"):
            basliklar = iz.siparisler if reference_type == "orders" else iz.irsaliyeler
            baslik = basliklar.get(kimlik)
            if baslik is not None:
                belge_no = baslik["document_no"]
                cari_id = int(baslik["customer_id"])
        elif reference_type == "returns":
            baslik = iz.iadeler.get(kimlik)
            if baslik is not None:
                belge_no = baslik["document_no"]
                if baslik["return_type"] == "sale_return":
                    cari_id = int(baslik["entity_id"])
    return {
        "reference_type": reference_type,
        "reference_id": None if reference_id is None else int(reference_id),
        "document_no": belge_no,
        "customer_id": cari_id,
        "date": tarih_metni(tarih),
        "quantity": _ondalik(miktar),
    }


def _sayi(deger: Decimal) -> str:
    """Uyarı metni için sade sayı: `83.0000` -> `83`, `-2.5000` -> `-2.5`."""
    metin = format(deger.normalize(), "f")
    return "0" if metin in ("-0", "0") else metin


def ozetle(
    iz: Any,
    *,
    perakende_cari_id: int | None,
    iletisim: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Ham izi önizleme gövdesine çevirir.

    `iz` `lot_izi.PartiIzi` biçimindedir (öznitelikleri okunur). `iletisim`
    cari kimliği -> `{"has_phone", "has_consent", "consent_reason"}`; çağıran
    onu `evaluate_consent`in KURU çalıştırmasından kurar (yazmaz).
    """
    alicilar: dict[int, dict[str, Any]] = {}
    perakende_cikan = _SIFIR
    perakende_iade = _SIFIR
    perakende_belgeler: set[tuple[str, int]] = set()
    giren = _SIFIR
    tedarikciye_iade = _SIFIR
    diger = _SIFIR
    diger_hareketler: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    def alici(cari_id: int) -> dict[str, Any]:
        if cari_id not in alicilar:
            alicilar[cari_id] = {
                "quantity_out": _SIFIR,
                "quantity_returned": _SIFIR,
                "tarihler": [],
                "kanallar": set(),
                "belgeler": {},
            }
        return alicilar[cari_id]

    def belge(kayit: dict[str, Any], kaynak: str, kimlik: int, no: Any, tarih: Any):
        anahtar = (kaynak, kimlik)
        if anahtar not in kayit["belgeler"]:
            kayit["belgeler"][anahtar] = {
                "source": kaynak,
                "document_id": kimlik,
                "document_no": no,
                "date": tarih_metni(tarih),
                "quantity_out": _SIFIR,
                "quantity_returned": _SIFIR,
            }
        return kayit["belgeler"][anahtar]

    for h in iz.hareketler:
        tur = h["reference_type"]
        ref = h["reference_id"]
        q = _ondalik(h["quantity"])
        if tur in ("orders", "delivery_notes"):
            basliklar = iz.siparisler if tur == "orders" else iz.irsaliyeler
            baslik = basliklar.get(int(ref)) if ref is not None else None
            if baslik is None:
                # Başlığı bulunamayan çıkış: alıcısı bilinemez, sessizce düşmez.
                gaps.append({"code": BOSLUK_DIGER, "reference_type": tur,
                             "movement_type": h["movement_type"],
                             "movement_count": 1, "quantity": q,
                             "ornekler": [_ornek(iz, tur, ref, h["movement_date"], q)],
                             "ornek_kesildi": False})
                diger += q
                continue
            cari_id = int(baslik["customer_id"])
            tarih = baslik["order_date"] if tur == "orders" else baslik["delivery_date"]
            if perakende_cari_id is not None and cari_id == perakende_cari_id:
                perakende_cikan += -q
                perakende_belgeler.add((tur, int(ref)))
                continue
            kayit = alici(cari_id)
            kayit["quantity_out"] += -q
            kayit["kanallar"].add(tur)
            if tarih_metni(tarih):
                kayit["tarihler"].append(tarih_metni(tarih))
            belge(kayit, tur, int(ref), baslik["document_no"], tarih)["quantity_out"] += -q
        elif tur == "returns":
            iade = iz.iadeler.get(int(ref)) if ref is not None else None
            if h["movement_type"] == "purchase_return" or (
                iade is not None and iade["return_type"] == "purchase_return"
            ):
                tedarikciye_iade += -q
                continue
            kaynak = None
            if (
                iade is not None
                and iade["source_type"] == "order"
                and iade["source_id"] is not None
            ):
                kaynak = iz.siparisler.get(int(iade["source_id"]))
            if kaynak is None:
                gaps.append({"code": BOSLUK_KAYNAKSIZ_IADE, "reference_type": tur,
                             "movement_type": h["movement_type"],
                             "movement_count": 1, "quantity": q,
                             "ornekler": [_ornek(iz, tur, ref, h["movement_date"], q)],
                             "ornek_kesildi": False})
                diger += q
                continue
            cari_id = int(kaynak["customer_id"])
            if perakende_cari_id is not None and cari_id == perakende_cari_id:
                perakende_iade += q
                continue
            kayit = alici(cari_id)
            kayit["quantity_returned"] += q
            belge(kayit, "orders", int(kaynak["id"]), kaynak["document_no"],
                  kaynak["order_date"])["quantity_returned"] += q
        elif tur == "purchases":
            giren += q
        elif tur == "transfer":
            continue
        else:
            diger += q
            diger_hareketler.append({
                "reference_type": tur,
                "reference_id": ref,
                "movement_type": h["movement_type"],
                "quantity": q,
                "date": tarih_metni(h["movement_date"]),
            })

    # H117 — örnekler SQL sırasıyla (tarih, id) gruplara dağıtılır; grup
    # içi sıra korunur, burada sıralama YAPILMAZ.
    ornek_gruplari: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for o in iz.ornekler:
        grup = ornek_gruplari.setdefault((o["reference_type"], o["movement_type"]), [])
        if len(grup) < ORNEK_SINIRI:
            grup.append(_ornek(iz, o["reference_type"], o["reference_id"],
                               o["movement_date"], o["quantity"]))

    for p in iz.partisiz:
        sayi = int(p["movement_count"])
        ornekler = ornek_gruplari.get((p["reference_type"], p["movement_type"]), [])
        gaps.append({
            "code": bosluk_adi(p["reference_type"], p["movement_type"]),
            "reference_type": p["reference_type"],
            "movement_type": p["movement_type"],
            "movement_count": sayi,
            "quantity": _ondalik(p["quantity"]),
            "ornekler": ornekler,
            "ornek_kesildi": sayi > len(ornekler),
        })

    musteriler: list[dict[str, Any]] = []
    for cari_id, kayit in alicilar.items():
        cari = iz.cariler.get(cari_id, {})
        bilgi = iletisim.get(cari_id, {})
        telefonlu = bool(bilgi.get("has_phone"))
        rizali = bool(bilgi.get("has_consent"))
        tarihler = sorted(kayit["tarihler"])
        musteriler.append({
            "customer_id": cari_id,
            "name": cari.get("name"),
            "phone": cari.get("phone"),
            "quantity_out": kayit["quantity_out"],
            "quantity_returned": kayit["quantity_returned"],
            "quantity_net": kayit["quantity_out"] - kayit["quantity_returned"],
            "first_date": tarihler[0] if tarihler else None,
            "last_date": tarihler[-1] if tarihler else None,
            "channels": sorted(kayit["kanallar"]),
            "documents": sorted(
                kayit["belgeler"].values(),
                key=lambda b: (b["date"] or "", b["source"], b["document_id"]),
            ),
            "has_phone": telefonlu,
            "has_consent": rizali,
            "consent_reason": bilgi.get("consent_reason"),
            "status": DURUM_BILDIRILEBILIR if telefonlu and rizali else DURUM_ELLE,
        })
    musteriler.sort(key=lambda m: (m["first_date"] or "", m["customer_id"]))

    pos_retail = {
        "quantity_out": perakende_cikan,
        "quantity_returned": perakende_iade,
        "quantity_net": perakende_cikan - perakende_iade,
        "document_count": len(perakende_belgeler),
    }

    kardesler = [
        {
            "lot_id": int(k["id"]),
            "warehouse_id": int(k["warehouse_id"]),
            "warehouse_name": iz.depolar.get(int(k["warehouse_id"])),
            "quantity": _ondalik(k["quantity"]),
        }
        for k in iz.kardesler
    ]
    eldeki = sum((k["quantity"] for k in kardesler), _SIFIR)
    musteri_net = sum((m["quantity_net"] for m in musteriler), _SIFIR)
    fark = (
        giren + diger - musteri_net - pos_retail["quantity_net"]
        - tedarikciye_iade - eldeki
    )

    # H116 — UYARILAR. Sıra sabittir: önce mutabakat (depo sırasıyla), sonra
    # kaynaksız iade (boşluk sırasıyla), sonra `defter_bosaldi` (depo sırasıyla).
    uyarilar: list[dict[str, Any]] = []
    for m in iz.mutabakat:
        if m["kova"] != KOVA_SAPMA:
            continue
        depo_id = int(m["warehouse_id"])
        depo_farki = _ondalik(m["stok"]) - _ondalik(m["parti_toplami"])
        uyarilar.append({
            "kod": UYARI_SAPMA,
            "mesaj": (
                f"{iz.depolar.get(depo_id) or f'Depo #{depo_id}'}: stok "
                f"{_sayi(_ondalik(m['stok']))}, parti defteri "
                f"{_sayi(_ondalik(m['parti_toplami']))} (fark {_sayi(depo_farki)}). "
                "Partisiz bir hareket defteri ayrıştırdı; bu depodaki mal geri "
                "çağırmada tam izlenemeyebilir."
            ),
            "warehouse_id": depo_id,
            "miktar": depo_farki,
        })
    for g in gaps:
        if g["code"] != BOSLUK_KAYNAKSIZ_IADE:
            continue
        uyarilar.append({
            "kod": UYARI_KAYNAKSIZ_IADE,
            "mesaj": (
                f"{g['movement_count']} kaynaksız satış iadesi "
                f"({_sayi(g['quantity'])} birim) partiye dönmedi; alıcının "
                "kalanı olduğundan büyük görünebilir."
            ),
            "warehouse_id": None,
            "miktar": g["quantity"],
        })
    for b in iz.bosaldi:
        depo_id = int(b["warehouse_id"])
        miktar = _ondalik(b["quantity"])
        uyarilar.append({
            "kod": UYARI_DEFTER_BOSALDI,
            "mesaj": (
                f"{iz.depolar.get(depo_id) or f'Depo #{depo_id}'}: "
                f"{int(b['movement_count'])} hareket parti defteri tükenmişken "
                f"partisiz yazıldı ({_sayi(miktar)} birim); bu mal geri "
                "çağırmada izlenemez."
            ),
            "warehouse_id": depo_id,
            "miktar": miktar,
        })

    return {
        "lot": {
            "lot_id": int(iz.kok["id"]),
            "product_id": int(iz.urun["id"]),
            "product_name": iz.urun.get("name"),
            "unit": iz.urun.get("unit"),
            "lot_code": iz.kok["lot_code"],
            "expiry_date": tarih_metni(iz.kok["expiry_date"]),
        },
        "siblings": kardesler,
        "customers": musteriler,
        "pos_retail": pos_retail,
        "gaps": gaps,
        "other_movements": diger_hareketler,
        "balance": {
            "received": giren,
            "customers_net": musteri_net,
            "pos_retail_net": pos_retail["quantity_net"],
            "returned_to_supplier": tedarikciye_iade,
            "other": diger,
            "on_hand": eldeki,
            "difference": fark,
        },
        "uyarilar": uyarilar,
    }

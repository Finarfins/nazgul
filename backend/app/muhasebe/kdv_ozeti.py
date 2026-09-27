"""Aylık KDV özeti — SAF, SQL'siz (F9-5a, keşif §2.2, §2.4).

Girdi `kaynak.donem_oku`nun `OranGrubu` listesidir, yani fişi kuran AYNI
satır grupları: özet ile fiş ayrışamaz. Çıktı KDV1 KODU DEĞİL `(yön, oran,
tür)` üçlüsüdür (§2.2: KDV1 satır numaraları DOĞRULANMADI; kod eşlemesi
rapor katmanının — 9-5d — işidir).

    yön  HESAPLANAN   : SATIS, SERVIS_FATURA, ALIS_IADE
         INDIRILECEK  : ALIS, SATIS_IADE

--- BİLİNMEYEN ORAN YUTULMAZ (G2) --------------------------------------------

`vat_rate` kapalı bir küme DEĞİL (`schemas.py`: 0..100). Bugünkü yasal
oranlar dışındaki her oran (eski %8/%18, %0 dahil — istisna KODU saklanmıyor)
KENDİ satırında `bilinen_oran=False` ile görünür ve ayrıca bir G2 uyarısı
doğurur. Başka bir orana "yakın" diye katlanmaz.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .kaynak import DonemVerisi, Uyari

SIFIR = Decimal("0.00")
BILINEN_ORANLAR = frozenset({Decimal("1.00"), Decimal("10.00"), Decimal("20.00")})

YON_TUR: dict[str, tuple[str, str]] = {
    "SATIS": ("HESAPLANAN", "SATIS"),
    "SERVIS_FATURA": ("HESAPLANAN", "SERVIS_FATURA"),
    "ALIS_IADE": ("HESAPLANAN", "ALIS_IADE"),
    "ALIS": ("INDIRILECEK", "ALIS"),
    "SATIS_IADE": ("INDIRILECEK", "SATIS_IADE"),
}

#: Özetin ADIYLA söylediği kapsam sınırları (K9, G1, G7) — dipnot değil.
KAPSAM_NOTLARI: tuple[str, ...] = (
    "Tevkifat desteklenmiyor: kısmi/tam tevkifat hiçbir belgede saklanmıyor (K9, G1).",
    "İstisna kodu saklanmıyor: %0 satırlar kodsuz raporlanır.",
    "Vade farkı KDV'siz kesiliyor; özete girmez (G7).",
    "Müstahsil makbuzunda KDV yoktur; stopaj ve Bağ-Kur ayrı bölümdedir.",
    "Önceki dönemden devreden KDV beyanname dışı veridir; müşavir doldurur.",
)


def _t(deger: Decimal) -> str:
    return format(deger, "f")


def kdv_ozeti(v: DonemVerisi) -> dict:
    """`DonemVerisi` → cevap gövdesi (tutarlar `str`)."""
    matrah: dict[tuple, Decimal] = defaultdict(lambda: SIFIR)
    kdv: dict[tuple, Decimal] = defaultdict(lambda: SIFIR)
    belgeler: dict[tuple, set[int]] = defaultdict(set)
    for g in v.oran_gruplari:
        yon, tur = YON_TUR[g.kaynak]
        anahtar = (yon, tur, g.oran)
        matrah[anahtar] += g.matrah
        kdv[anahtar] += g.kdv
        belgeler[anahtar].add(g.belge_id)

    uyarilar = [u.sozluk() for u in v.uyarilar]
    satirlar = []
    toplam = {"HESAPLANAN": SIFIR, "INDIRILECEK": SIFIR}
    for anahtar in sorted(matrah):
        yon, tur, oran = anahtar
        bilinen = oran in BILINEN_ORANLAR
        toplam[yon] += kdv[anahtar]
        satirlar.append({
            "yon": yon,
            "tur": tur,
            "oran": _t(oran),
            "bilinen_oran": bilinen,
            "matrah": _t(matrah[anahtar]),
            "kdv": _t(kdv[anahtar]),
            "belge_sayisi": len(belgeler[anahtar]),
        })
        if not bilinen:
            uyarilar.append(Uyari(
                "G2", tur,
                f"%{oran} oranı bugünkü yasal oranlardan (1/10/20) değil; ayrı satırda "
                f"gösterildi ({len(belgeler[anahtar])} belge)",
            ).sozluk())
    for hata in v.reddedilen:
        uyarilar.append({
            "kod": "DENGESIZ",
            "kaynak": hata.fis.kaynak,
            "belge_id": None,
            "belge_no": hata.fis.belge_no,
            "fark": _t(hata.fis.borc_toplami - hata.fis.alacak_toplami),
            "mesaj": f"{hata.fis.fis_no} fişi yazılmadı; KDV özetine DAHİL: " + "; ".join(hata.nedenler),
            "kdv_dahil": True,
        })
    mm = v.mustahsil
    return {
        "period": v.donem.metin,
        "satirlar": satirlar,
        "hesaplanan_kdv": _t(toplam["HESAPLANAN"]),
        "indirilecek_kdv": _t(toplam["INDIRILECEK"]),
        "fark": _t(toplam["HESAPLANAN"] - toplam["INDIRILECEK"]),
        "mustahsil": {
            "belge_sayisi": mm.belge_sayisi,
            "brut": _t(mm.brut),
            "stopaj": _t(mm.stopaj),
            "bagkur": _t(mm.bagkur),
            "net": _t(mm.net),
        },
        "uyarilar": uyarilar,
        "kapsam_notlari": list(KAPSAM_NOTLARI),
    }

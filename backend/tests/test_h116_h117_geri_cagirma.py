"""H116 + H117 — geri çağırma önizlemesi: `uyarilar[]` ve boşluk satırı kimliği.

Konu: `app/lot_izi.py` (+4 sabit sorgu), `app/lot_izi_ozet.py` (saf
birleştirme), `app/lot_izi_schemas.py` (`Uyari`, `BoslukOrnegi`). Uçtan uca
davranış (G4 eski veri, kaynaksız iade, `defter_bosaldi`, >20 örnek, temiz
parti, sorgu bütçesi) `tests/test_f10_4a_lot_izi.py`nin `_DAVRANIS`
betiğindedir; PostgreSQL ikizi (`test_f10_4a_lot_izi_postgresql.py`) aynı
betiği gerçek diyalektte koşar. Bu dosya SAF birim kapılarıdır.

--- MUTASYON TABLOSU ------------------------------------------------------

  * `ozetle`de SAPMA uyarısını atlamak -> burada `SAPMA_uyarisi` + davranış
    (G4 bölümü) KIRMIZI
  * `lot_izi`nin örnek sorgusundan `order_by`ı düşürmek -> davranış
    (>20 örnek bölümü, ters tarihli yazım) KIRMIZI
  * `ozetle`de örnekleri yeniden sıralamak -> `SQL_SIRASI_korunur` KIRMIZI
"""
from __future__ import annotations

import sys
import typing
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _iz(**fazla):
    temel = dict(
        kok={"id": 1, "lot_code": "L-1", "expiry_date": "2098-01-31"},
        urun={"id": 7, "name": "NPK", "unit": "Kg"},
        kardesler=[{"id": 1, "warehouse_id": 1, "quantity": Decimal("100")}],
        depolar={1: "Merkez", 2: "Şube"},
        hareketler=[
            {"reference_type": "purchases", "reference_id": 1, "movement_type": "purchase",
             "quantity": Decimal("100"), "movement_date": "2026-09-01"},
        ],
        partisiz=[],
        siparisler={},
        irsaliyeler={},
        iadeler={},
        cariler={},
        ornekler=[],
        mutabakat=[{"warehouse_id": 1, "stok": Decimal("100"),
                     "parti_toplami": Decimal("100"), "kova": "ESIT"}],
        bosaldi=[],
    )
    temel.update(fazla)
    return SimpleNamespace(**temel)


def _ozetle(iz):
    from app.lot_izi_ozet import ozetle

    return ozetle(iz, perakende_cari_id=None, iletisim={})


def test_KOVA_DAMGA_ve_KODLAR_tek_kaynakla_AYNI() -> None:
    from app import lot_izi, lot_izi_ozet, parti_defteri, parti_mutabakat
    from app.lot_izi_schemas import Uyari

    assert lot_izi_ozet.KOVA_SAPMA == parti_mutabakat.SAPMA
    assert lot_izi.DEFTER_BOSALDI_DAMGASI == parti_defteri.DEFTER_BOSALDI_DAMGASI
    # Damga notun SONUNDA durur; `like('%' + damga)` bunu varsayar.
    assert parti_defteri._hareket_notu("sale", 7, False, True).endswith(
        parti_defteri.DEFTER_BOSALDI_DAMGASI)
    kodlar = set(typing.get_args(Uyari.model_fields["kod"].annotation))
    assert kodlar == {
        lot_izi_ozet.UYARI_SAPMA,
        lot_izi_ozet.UYARI_KAYNAKSIZ_IADE,
        lot_izi_ozet.UYARI_DEFTER_BOSALDI,
    }


def test_TEMIZ_parti_uyarisiz() -> None:
    govde = _ozetle(_iz())
    assert govde["uyarilar"] == []
    assert govde["gaps"] == []


def test_SAPMA_uyarisi_depo_basina_FARK_ile() -> None:
    govde = _ozetle(_iz(mutabakat=[
        {"warehouse_id": 1, "stok": Decimal("83"), "parti_toplami": Decimal("87"),
         "kova": "SAPMA"},
        {"warehouse_id": 2, "stok": Decimal("4"), "parti_toplami": Decimal("0"),
         "kova": "SAPMA"},
        {"warehouse_id": 3, "stok": Decimal("9"), "parti_toplami": Decimal("0"),
         "kova": "LOTSUZ_TASARIM"},
    ]))
    assert [(u["kod"], u["warehouse_id"], u["miktar"]) for u in govde["uyarilar"]] == [
        ("mutabakat_sapma", 1, Decimal("-4")),
        ("mutabakat_sapma", 2, Decimal("4")),
    ]
    assert govde["uyarilar"][0]["mesaj"].startswith("Merkez: stok 83, parti defteri 87 (fark -4)")


def test_DEFTER_BOSALDI_uyarisi() -> None:
    govde = _ozetle(_iz(bosaldi=[
        {"warehouse_id": 2, "movement_count": 3, "quantity": Decimal("-7.5000")},
    ]))
    [u] = govde["uyarilar"]
    assert (u["kod"], u["warehouse_id"], u["miktar"]) == (
        "defter_bosaldi", 2, Decimal("-7.5000"))
    assert "3 hareket" in u["mesaj"] and "-7.5 birim" in u["mesaj"], u["mesaj"]


def _iade_satirlari(n):
    """`n` kaynaksız iade hareketi; kimlik ARTARKEN tarih AZALIR."""
    return [
        {"id": 1000 + i, "reference_type": "returns", "reference_id": 500 + i,
         "movement_type": "sale_return", "movement_date": f"2026-09-{30 - i:02d}",
         "quantity": Decimal("1")}
        for i in range(n)
    ]


def test_SQL_SIRASI_korunur_ve_20de_KESILIR() -> None:
    """`ozetle` örnekleri SQL'in verdiği sırayla keser, YENİDEN SIRALAMAZ."""
    verilen = _iade_satirlari(25)
    iadeler = {s["reference_id"]: {"id": s["reference_id"], "return_type": "sale_return",
                                   "entity_id": 42, "document_no": f"IA-{s['reference_id']}",
                                   "source_type": None, "source_id": None}
               for s in verilen}
    govde = _ozetle(_iz(
        partisiz=[{"reference_type": "returns", "movement_type": "sale_return",
                   "movement_count": 25, "quantity": Decimal("25")}],
        ornekler=verilen, iadeler=iadeler,
    ))
    [gap] = govde["gaps"]
    assert gap["ornek_kesildi"] is True
    assert [o["reference_id"] for o in gap["ornekler"]] == [
        s["reference_id"] for s in verilen[:20]]
    assert {o["customer_id"] for o in gap["ornekler"]} == {42}
    assert gap["ornekler"][0]["document_no"] == "IA-500"
    assert [u["kod"] for u in govde["uyarilar"]] == ["kaynaksiz_iade"]


def test_TAM_20de_KESILMEZ_ve_alis_iadesi_CARI_degil() -> None:
    satirlar = [{**s, "movement_type": "purchase_return"} for s in _iade_satirlari(20)]
    iadeler = {s["reference_id"]: {"id": s["reference_id"], "return_type": "purchase_return",
                                   "entity_id": 9, "document_no": None,
                                   "source_type": None, "source_id": None}
               for s in satirlar}
    govde = _ozetle(_iz(
        partisiz=[{"reference_type": "returns", "movement_type": "purchase_return",
                   "movement_count": 20, "quantity": Decimal("-20")}],
        ornekler=satirlar,
        iadeler=iadeler,
    ))
    [gap] = govde["gaps"]
    assert len(gap["ornekler"]) == 20 and gap["ornek_kesildi"] is False
    assert {o["customer_id"] for o in gap["ornekler"]} == {None}


def test_SEMA_ozetle_ciktisini_KABUL_eder() -> None:
    """`extra="forbid"`: yeni anahtarlar şemada yoksa doğrulama patlar."""
    from app.lot_izi_schemas import GeriCagirmaOnizleme

    govde = _ozetle(_iz(
        partisiz=[{"reference_type": "returns", "movement_type": "sale_return",
                   "movement_count": 3, "quantity": Decimal("3")}],
        ornekler=_iade_satirlari(3),
        mutabakat=[{"warehouse_id": 1, "stok": Decimal("103"),
                    "parti_toplami": Decimal("100"), "kova": "SAPMA"}],
    ))
    model = GeriCagirmaOnizleme.model_validate(govde)
    assert len(model.uyarilar) == 2 and len(model.gaps[0].ornekler) == 3

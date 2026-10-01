"""Müşteri risk skorunun ARİTMETİĞİ (F10-9a) — saf, SQL'siz, oturumsuz.

Bu modül veritabanına DOKUNMAZ ve bir `Session` görmez (`mustahsil.py`
deseni). Sinyalleri `risk_skoru_okuma.py` toplar ve `RiskSinyalleri` olarak
buraya verir; burası yalnız formülü uygular. Gerekçe: skor bir sözleşmedir
ve sözleşme, sinyali üreten motorlardan BAĞIMSIZ sınanabilmelidir
(`test_f10_9a_risk_skoru.py` tablo testi).

--- FORMÜL (keşif §3.1, Şef K1) ----------------------------------------------

    puan = max(0, 100 − Σ ceza)
    harf = A (≥85) · B (70–84) · C (50–69) · D (30–49) · E (<30)

| kod          | sinyal                                         | ceza                                   | tavan |
|--------------|------------------------------------------------|----------------------------------------|------:|
| gecikme      | S1: en eski açık belgenin gecikme günü         | 1–30 → 5 · 31–60 → 15 · 61–90 → 25 · 90+ → 35 | 35 |
| vadesi_oran  | S1: vadesi geçmiş / açık toplam                | round(oran × 20)                       |    20 |
| gec_kapanis  | S2: son 12 ayda >7 gün geç kapanan belge oranı | round(oran × 15)                       |    15 |
| karsiliksiz  | S3: son 24 ay `bounced_check` (ters kaydı yok) | her biri 15                            |    30 |
| vade_farki   | S4: son 12 ay `late_fee` (ters kaydı yok)      | her biri 3                             |    10 |
| limit        | S5: bakiye / risk limiti (limit > 0 ise)       | ≥ %80 → 5 · > %100 → 15                |    15 |

* `gecikme` sınırları yaşlandırma kovalarıyla (`routers/reports.py::_aging_bucket`)
  BİREBİR aynıdır: ≤0 vadesiz, ≤30, ≤60, ≤90, sonrası 90+. "Neden D?" sorusunun
  cevabı rapordaki kovadır.
* Oranlar `Decimal` ile hesaplanır ve `ROUND_HALF_UP` ile tamsayı puana iner;
  sonuç kuruş değil puandır. Oran [0, 1] aralığına kırpılır (eksi kalanlı
  belge oranı eksiye itemez).
* `yetersiz_veri` = son 12 ayda kapanan belge < 3 (K2). Harf YİNE hesaplanır;
  az geçmişli müşteriye varsayılan "A" rozeti basmak arayüzün işi DEĞİLDİR.
* Bütün eşik ve ağırlıklar TEK sözlükte, `SABITLER`de durur (K1); firma başına
  ayar YOKTUR. Değiştirmek tablo testini kırar — bu kasıtlıdır.

--- KVKK (K8) ----------------------------------------------------------------

Skor İÇ bir değerlendirmedir: ekstreye, ekstre PDF'ine, WhatsApp'a ve
`yaslandirma_verisi`ne GİRMEZ. Tek yüzeyi `GET /api/customers/{id}/risk-score`
ucudur. Skor saklanmaz (K3) ve okuması denetlenmez (K9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

Harf = Literal["A", "B", "C", "D", "E"]

# K1: TEK sözlük. Tablo testi (`test_SABITLER_tablosu_CIVILI`) bu sözlüğü
# birebir çiviler; bir eşiği oynatmak o testi adıyla kırar.
SABITLER: dict[str, object] = {
    "taban_puan": 100,
    # (üst sınır gün dahil, ceza) — son satır "90+"dır, üst sınırı yoktur.
    "gecikme_kovalari": ((30, 5), (60, 15), (90, 25), (None, 35)),
    "vadesi_oran_carpan": 20,
    "vadesi_oran_tavan": 20,
    "gec_kapanis_esik_gun": 7,
    "gec_kapanis_carpan": 15,
    "gec_kapanis_tavan": 15,
    "karsiliksiz_birim": 15,
    "karsiliksiz_tavan": 30,
    "vade_farki_birim": 3,
    "vade_farki_tavan": 10,
    "limit_yaklasma_oran": Decimal("0.80"),
    "limit_yaklasma_ceza": 5,
    "limit_asim_ceza": 15,
    # (alt sınır dahil, harf), yüksekten düşüğe.
    "harf_esikleri": ((85, "A"), (70, "B"), (50, "C"), (30, "D"), (0, "E")),
    "yetersiz_veri_min_kapanan": 3,
    "pencere_12_ay_gun": 365,
    "pencere_24_ay_gun": 730,
}


@dataclass(frozen=True)
class RiskSinyalleri:
    """Tek müşterinin ham sinyalleri; `risk_skoru_okuma.sinyalleri_topla` üretir."""

    as_of: date
    # S1 — yaşlandırma motoru (müşteri süzgeçli `calculate_net_receivables`).
    acik_toplam: Decimal
    vadesi_gecmis_toplam: Decimal
    en_eski_gecikme_gun: int
    en_eski_belge_no: str | None
    en_eski_vade: date | None
    # S2 — `build_principal_timelines`.
    kapanan_belge_12ay: int
    gec_kapanan_belge_12ay: int
    # S3 / S4 — `receivable_charge_documents`.
    karsiliksiz_24ay: int
    vade_farki_12ay: int
    # S5 — `routers/transactions.py::_credit_exposure`.
    risk_limit: Decimal
    bakiye: Decimal


@dataclass(frozen=True)
class RiskCezasi:
    kod: str
    puan: int
    aciklama: str
    kanit: str


@dataclass(frozen=True)
class RiskSkoru:
    puan: int
    harf: Harf
    yetersiz_veri: bool
    cezalar: tuple[RiskCezasi, ...]
    hesaplandi: date


def tam_sabit(ad: str) -> int:
    return int(SABITLER[ad])  # type: ignore[call-overload]


def _yuvarla(deger: Decimal) -> int:
    return int(deger.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _oran(pay: Decimal | int, payda: Decimal | int) -> Decimal:
    """[0, 1] aralığına kırpılmış oran; payda ≤ 0 ise 0."""
    payda_d = Decimal(payda)
    if payda_d <= 0:
        return Decimal("0")
    return min(Decimal("1"), max(Decimal("0"), Decimal(pay) / payda_d))


def gecikme_cezasi(gun: int) -> int:
    """Yaşlandırma kovası sınırlarıyla aynı basamaklar; ≤0 gün → 0."""
    if gun <= 0:
        return 0
    for ust, ceza in SABITLER["gecikme_kovalari"]:
        if ust is None or gun <= ust:
            return int(ceza)
    raise AssertionError("gecikme_kovalari son satırı üst sınırsız olmalı")


def harf_bul(puan: int) -> Harf:
    for alt, harf in SABITLER["harf_esikleri"]:
        if puan >= alt:
            return harf
    return "E"


def _para(deger: Decimal) -> str:
    return format(deger.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def skor_hesapla(s: RiskSinyalleri) -> RiskSkoru:
    """`RiskSinyalleri` → `RiskSkoru`; formül modül docstring'indedir."""
    cezalar: list[RiskCezasi] = []

    def ekle(kod: str, puan: int, aciklama: str, kanit: str) -> None:
        if puan > 0:
            cezalar.append(RiskCezasi(kod, puan, aciklama, kanit))

    ekle(
        "gecikme",
        gecikme_cezasi(s.en_eski_gecikme_gun),
        "En eski vadesi geçmiş belgenin gecikmesi (yaşlandırma kovası)",
        f"{s.en_eski_belge_no or '-'} · vade "
        f"{s.en_eski_vade.isoformat() if s.en_eski_vade else '-'} · "
        f"{s.en_eski_gecikme_gun} gün",
    )
    vadesi_oran = _oran(s.vadesi_gecmis_toplam, s.acik_toplam)
    ekle(
        "vadesi_oran",
        min(
            tam_sabit("vadesi_oran_tavan"),
            _yuvarla(vadesi_oran * tam_sabit("vadesi_oran_carpan")),
        ),
        "Vadesi geçmiş alacağın açık alacağa oranı",
        f"vadesi geçmiş {_para(s.vadesi_gecmis_toplam)} / açık {_para(s.acik_toplam)}",
    )
    gec_oran = _oran(s.gec_kapanan_belge_12ay, s.kapanan_belge_12ay)
    ekle(
        "gec_kapanis",
        min(
            tam_sabit("gec_kapanis_tavan"),
            _yuvarla(gec_oran * tam_sabit("gec_kapanis_carpan")),
        ),
        f"Son 12 ayda vadesinden {SABITLER['gec_kapanis_esik_gun']} günden geç kapanan belge oranı",
        f"{s.gec_kapanan_belge_12ay} / {s.kapanan_belge_12ay} belge",
    )
    ekle(
        "karsiliksiz",
        min(
            tam_sabit("karsiliksiz_tavan"),
            tam_sabit("karsiliksiz_birim") * s.karsiliksiz_24ay,
        ),
        "Son 24 ayda karşılıksız / iade edilen evrak",
        f"{s.karsiliksiz_24ay} evrak",
    )
    ekle(
        "vade_farki",
        min(
            tam_sabit("vade_farki_tavan"),
            tam_sabit("vade_farki_birim") * s.vade_farki_12ay,
        ),
        "Son 12 ayda kesilen vade farkı",
        f"{s.vade_farki_12ay} belge",
    )
    limit_cezasi = 0
    if s.risk_limit > 0:
        kullanim = s.bakiye / s.risk_limit
        if kullanim > 1:
            limit_cezasi = tam_sabit("limit_asim_ceza")
        elif kullanim >= Decimal(str(SABITLER["limit_yaklasma_oran"])):
            limit_cezasi = tam_sabit("limit_yaklasma_ceza")
    ekle(
        "limit",
        limit_cezasi,
        "Cari bakiyenin risk limitine oranı",
        f"bakiye {_para(s.bakiye)} / limit {_para(s.risk_limit)}",
    )

    toplam = sum(c.puan for c in cezalar)
    puan = max(0, tam_sabit("taban_puan") - toplam)
    return RiskSkoru(
        puan=puan,
        harf=harf_bul(puan),
        yetersiz_veri=s.kapanan_belge_12ay
        < tam_sabit("yetersiz_veri_min_kapanan"),
        cezalar=tuple(cezalar),
        hesaplandi=s.as_of,
    )

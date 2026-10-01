"""Müşteri risk skorunun SİNYAL TOPLAMASI (F10-9a) — `mustahsil_okuma.py` deseni.

Formül `risk_skoru.py`dedir ve SQL görmez; bu modül beş sinyali mevcut
motorlardan okur ve `RiskSinyalleri` olarak ona verir. HİÇBİR sinyal için
ikinci bir hesap YAZILMAZ:

* S1 — `receivables_engine.calculate_net_receivables(..., customer_id=...)`.
  Yaşlandırma raporunun (`routers/reports.py::yaslandirma_verisi`) kullandığı
  motorun AYNISI, müşteri süzgeçli (K5=a). Açık belge kümesi raporla aynıdır
  (`remaining != 0`), yani skorun "vadesi geçmiş" toplamı raporun o müşteri
  satırındaki gecikmiş kovaların toplamına kuruşu kuruşuna eşittir.
* S2 — `receivables_engine.build_principal_timelines` (bayraktan bağımsız).
  Belge kapanışı = anapara bakiyesinin ≤ 0 olduğu ilk değişim tarihi.
  Tahsis defteri kapalıyken bağsız ödeme FIFO ile dağıldığı için kapanış tarihi
  belge bazında bir MODEL çıktısıdır (keşif §1.3).
* S3 / S4 — `receivable_charge_documents` tek tablo sayımı; ters kaydı
  olmayan (`status='posted' AND reversal_of_document_id IS NULL`) belgeler.
  S3 `bounced_check` BELGESİNİ sayar (K4): etiket "karşılıksız / iade edilen
  evrak"tır, çünkü belge `portfoyde → iade`de de açılır. Aktivite günlüğü
  OKUNMAZ.
* S5 — `routers/transactions.py::_credit_exposure`, limit kapısının KENDİ
  bakiyesi. Yeniden yazılmaz, ÇAĞRILIR: kapı değişirse (H104) skor onunla
  birlikte değişir. Kapının bakiyesi bugünündür, `as_of`a bağlı değildir.

Bulunamayan (ya da başka firmanın) müşteri `None` döner; 404'e çevirmek
router'ın işidir.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from .money import ZERO_MONEY, money
from .receivables_engine import (
    ReceivableDocument,
    build_principal_timelines,
    calculate_net_receivables,
)
from .risk_skoru import RiskSinyalleri, RiskSkoru, tam_sabit, skor_hesapla


def _musteri_var(db: Session, cid: int, customer_id: int) -> bool:
    return (
        db.execute(
            text("SELECT id FROM customers WHERE id=:customer_id AND company_id=:cid"),
            {"customer_id": customer_id, "cid": cid},
        ).first()
        is not None
    )


def _acik_belgeler(
    belgeler: list[ReceivableDocument], as_of: date
) -> tuple[Decimal, Decimal, ReceivableDocument | None]:
    """(açık toplam, vadesi geçmiş toplam, en eski vadesi geçmiş belge).

    Toplamlar yaşlandırmanın kümesinden: `remaining != 0`, eksiler DAHİL.
    En eski belge ise netlenmiş kümeden seçilir: terslenen ücret belgesi (+)
    ile ters kaydı (−, `reversal_of_id`) tek belgede toplanır ve net > 0
    olan belge sayılır — iptal edilmiş bir vade farkı "en eski gecikme"
    olamaz (`routers/dashboard.py::_gecikmis_alacaklar` ile aynı kural).
    """
    acik = [b for b in belgeler if b.remaining != ZERO_MONEY]
    acik_toplam = money(sum((b.remaining for b in acik), ZERO_MONEY))
    vadesi_gecmis = money(
        sum((b.remaining for b in acik if b.due_date < as_of), ZERO_MONEY)
    )
    gruplar: dict[tuple[bool, int], list[ReceivableDocument]] = {}
    for belge in acik:
        kok = belge.reversal_of_id if belge.reversal_of_id is not None else belge.id
        gruplar.setdefault((belge.document_type != "sale", kok), []).append(belge)
    en_eski: ReceivableDocument | None = None
    for uyeler in gruplar.values():
        bas = next((u for u in uyeler if u.reversal_of_id is None), None)
        net = sum((u.remaining for u in uyeler), ZERO_MONEY)
        if bas is None or net <= ZERO_MONEY or bas.due_date >= as_of:
            continue
        if en_eski is None or (bas.due_date, bas.id) < (en_eski.due_date, en_eski.id):
            en_eski = bas
    return acik_toplam, vadesi_gecmis, en_eski


def _kapanislar(
    db: Session, cid: int, customer_id: int, as_of: date
) -> tuple[int, int]:
    """(son 12 ayda kapanan belge, bunlardan eşikten geç kapanan)."""
    pencere_basi = as_of - timedelta(days=tam_sabit("pencere_12_ay_gun"))
    esik = tam_sabit("gec_kapanis_esik_gun")
    kapanan = gec = 0
    for cizelge in build_principal_timelines(db, cid, customer_id, as_of):
        bakiye = cizelge.original_principal
        if bakiye <= ZERO_MONEY:
            continue
        for degisim in cizelge.changes:
            bakiye = money(bakiye + degisim.amount)
            if bakiye <= ZERO_MONEY:
                if pencere_basi < degisim.effective_date <= as_of:
                    kapanan += 1
                    if (degisim.effective_date - cizelge.due_date).days > esik:
                        gec += 1
                break
    return kapanan, gec


def _ucret_belgesi_sayisi(
    db: Session, cid: int, customer_id: int, charge_type: str, since: date, as_of: date
) -> int:
    return int(
        db.execute(
            text(
                """SELECT COUNT(*) FROM receivable_charge_documents
                WHERE company_id=:cid AND customer_id=:customer_id
                  AND charge_type=:charge_type AND status='posted'
                  AND reversal_of_document_id IS NULL
                  AND period_end>:since AND period_end<=:as_of"""
            ),
            {
                "cid": cid,
                "customer_id": customer_id,
                "charge_type": charge_type,
                "since": since,
                "as_of": as_of,
            },
        ).scalar_one()
    )


def sinyalleri_topla(
    db: Session, cid: int, customer_id: int, as_of: date
) -> RiskSinyalleri | None:
    if not _musteri_var(db, cid, customer_id):
        return None
    # Döngüsel içe aktarma: `routers.transactions` uygulama modüllerini
    # modül düzeyinde çeker; kapı fonksiyonu İŞLEV GÖVDESİNDE alınır.
    from .routers.transactions import _credit_exposure

    acik_toplam, vadesi_gecmis, en_eski = _acik_belgeler(
        calculate_net_receivables(db, cid, as_of, customer_id=customer_id), as_of
    )
    kapanan, gec = _kapanislar(db, cid, customer_id, as_of)
    risk_limit, bakiye, _ = _credit_exposure(
        db,
        cid=cid,
        customer_id=customer_id,
        final_total=ZERO_MONEY,
        paid_amount=ZERO_MONEY,
        status="draft",
        transaction_id=None,
    )
    return RiskSinyalleri(
        as_of=as_of,
        acik_toplam=acik_toplam,
        vadesi_gecmis_toplam=vadesi_gecmis,
        en_eski_gecikme_gun=(as_of - en_eski.due_date).days if en_eski else 0,
        en_eski_belge_no=(
            (en_eski.document_no or f"S-{en_eski.id}") if en_eski else None
        ),
        en_eski_vade=en_eski.due_date if en_eski else None,
        kapanan_belge_12ay=kapanan,
        gec_kapanan_belge_12ay=gec,
        karsiliksiz_24ay=_ucret_belgesi_sayisi(
            db, cid, customer_id, "bounced_check",
            as_of - timedelta(days=tam_sabit("pencere_24_ay_gun")), as_of,
        ),
        vade_farki_12ay=_ucret_belgesi_sayisi(
            db, cid, customer_id, "late_fee",
            as_of - timedelta(days=tam_sabit("pencere_12_ay_gun")), as_of,
        ),
        risk_limit=money(risk_limit),
        bakiye=money(bakiye),
    )


def risk_skoru(db: Session, cid: int, customer_id: int, as_of: date) -> RiskSkoru | None:
    sinyaller = sinyalleri_topla(db, cid, customer_id, as_of)
    return skor_hesapla(sinyaller) if sinyaller is not None else None

"""Gerçek Maliyet V1 — saatlik maliyet oranları (mobil-erp#24, FAZ 1).

--- BU TABLONUN TUTTUĞU ŞEY GEÇMİŞ DEĞİL, BUGÜNKÜ VARSAYILAN --------------

Oran, bir faaliyet/kayıt oluşturulurken İŞLEM SATIRINA KOPYALANACAK (FAZ 2).
Buradaki satırı sonradan değiştirmek GEÇMİŞ MALİYETİ DEĞİŞTİRMEZ ve bu bilinçli:
değiştirseydi geçen sezonun kârı bu yıl başka çıkardı ve hiçbir rapora
güvenilemezdi. Aynı desen servis tarafında `work_order_labor_lines`'ta yıllardır
çalışıyor ("the stored rate is the only rate billing ever reads").

--- ÇAPRAZ KİRACI BAĞ UYGULAMA KATMANINDA ENGELLENİYOR --------------------

`machines` ve `app_users`'a bileşik yabancı anahtar atılmadı (migration 0052'de
gerekçesi yazılı: mevcut üretim tablosunu yeniden inşa etmek riskli). Bu yüzden
"seçilen makine bu firmaya mı ait" kontrolü BURADA yapılıyor ve testle
çivileniyor — atlanırsa başka firmanın makinesine oran bağlanabilirdi.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..cost_rate_schemas import CostRateUpdate, CostRateWrite
from ..db import get_db
from ..tenancy import company_id

router = APIRouter(tags=["cost-rates"])

_SAYFA = Query(default=50, ge=1, le=200)
_ATLA = Query(default=0, ge=0, le=100_000)

_SUTUNLAR = (
    "id,kind,machine_id,user_id,label,hourly_rate,notes,status,updated_at"
)


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _disari(row: Any) -> dict[str, Any]:
    """Satırı dış temsile çevirir; PARA STRING olarak çıkar.

    Ham ``text()`` sonucu diyalekte göre değişiyor: PostgreSQL ``NUMERIC``i
    float'a çeviren bir JSON kodlamasına düşüyor (``0.0``), SQLite ise
    ``Decimal`` benzeri bir değer veriyor. Aynı ucun veritabanına göre farklı
    TİPTE para döndürmesi kabul edilemez; üstelik float, para için kayıplı.
    Frontend tipi (``CostRates.tsx``) zaten ``hourly_rate: string`` varsayıyor.

    Bu yüzden para, deponun başka yerinde de yapıldığı gibi (bkz.
    ``routers/farm.py``) ``Decimal`` üzerinden iki haneye sabitlenip STRING
    olarak veriliyor.
    """
    kayit = dict(row)
    ham = kayit.get("hourly_rate")
    if ham is not None:
        kayit["hourly_rate"] = str(
            Decimal(str(ham)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
    return kayit


def _satir(db: Session, cid: int, kayit_id: int) -> dict[str, Any]:
    row = db.execute(
        text(f"SELECT {_SUTUNLAR} FROM cost_rates WHERE id=:id AND company_id=:cid"),
        {"id": kayit_id, "cid": cid},
    ).mappings().first()
    if row is None:
        # 404, 403 DEĞİL: 403 "var ama sana kapalı" bilgisini sızdırır.
        raise HTTPException(404, "Maliyet oranı bulunamadı")
    return _disari(row)


def _utc(deger: Any) -> datetime | None:
    """Sürüm damgasını UTC'ye çevirir. Duyarlılık KIRPILMAZ."""
    if deger is None:
        return None
    if isinstance(deger, str):
        deger = datetime.fromisoformat(deger)
    if deger.tzinfo is None:
        deger = deger.replace(tzinfo=timezone.utc)
    return deger.astimezone(timezone.utc)


def _surum_dogrula(mevcut: dict[str, Any], beklenen: datetime) -> None:
    """İstemcinin elindeki sürüm hâlâ güncel mi — TAM DUYARLIKLA.

    Eski sürüm saniyeye yuvarlıyordu (``int(...timestamp())``); aynı saniye
    içindeki iki güncelleme eşit sayılıyor ve kilit tam da en sık çakışılan
    anda sessizce açılıyordu. Bu kontrol kullanıcıya erken ve anlaşılır bir
    409 verir; YARIŞA KARŞI ASIL KORUMA ise UPDATE'in ``updated_at`` yüklemidir
    (bkz. ``update_cost_rate``). İkisi birbirinin yerine geçmez.
    """
    var = _utc(mevcut.get("updated_at"))
    if var is None:
        return
    gelen = _utc(beklenen)
    if var != gelen:
        raise HTTPException(409, "Kayıt siz düzenlerken değişti; yenileyip tekrar deneyin")


def _hedef_dogrula(db: Session, cid: int, payload: CostRateWrite) -> None:
    """Makine/kullanıcı AYNI FİRMAYA ait olmalı.

    Veritabanı bunu bilemiyor (bileşik FK yok, bkz. modül başlığı). Kontrol
    atlanırsa başka firmanın makinesine oran bağlanır ve o makinenin adı bu
    firmanın ekranında görünür — kiracı sızıntısı.
    """
    if payload.machine_id is not None:
        var = db.execute(
            text("SELECT 1 FROM machines WHERE id=:id AND company_id=:cid"),
            {"id": payload.machine_id, "cid": cid},
        ).scalar()
        if not var:
            raise HTTPException(404, "Makine bulunamadı")
    if payload.user_id is not None:
        # Kullanıcı firmaya ÜYELİK üzerinden bağlı: `app_users`ta `company_id`
        # YOK, bağ `user_company_memberships` tablosunda (bkz. tenancy.py).
        var = db.execute(
            text(
                """SELECT 1 FROM user_company_memberships
                WHERE user_id=:uid AND company_id=:cid"""
            ),
            {"uid": payload.user_id, "cid": cid},
        ).scalar()
        if not var:
            raise HTTPException(404, "Kullanıcı bu firmada bulunamadı")


@router.get("/cost-rates")
def list_cost_rates(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    kind: str | None = None, status: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if kind:
        kosul += " AND kind=:kind"
        params["kind"] = kind.strip().upper()
    if status:
        kosul += " AND status=:status"
        params["status"] = status.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM cost_rates WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT {_SUTUNLAR} FROM cost_rates WHERE company_id=:cid{kosul}
            ORDER BY kind,label,id LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return {"items": [_disari(r) for r in rows], "total": int(toplam or 0),
            "limit": limit, "offset": offset}


@router.post("/cost-rates", status_code=201)
def create_cost_rate(payload: CostRateWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _hedef_dogrula(db, cid, payload)
    now = _simdi()
    try:
        yeni = db.execute(
            text(
                """INSERT INTO cost_rates(company_id,kind,machine_id,user_id,label,
                hourly_rate,notes,status,created_at,updated_at)
                VALUES(:cid,:kind,:machine_id,:user_id,:label,:hourly_rate,:notes,
                'ACTIVE',:now,:now) RETURNING id"""
            ),
            {"cid": cid, "now": now, **payload.model_dump()},
        ).scalar_one()
    except IntegrityError:
        db.rollback()
        # Koşullu tekil indeks: aynı makineye/kişiye ya da aynı türün firma
        # geneline İKİ AKTİF oran olamaz. İkisi olsaydı hangisinin uygulanacağı
        # satır sırasına kalırdı — yani sessizce değişebilirdi.
        raise HTTPException(
            409,
            "Bu hedef için zaten aktif bir oran var. İkinci bir oran, hangisinin "
            "uygulanacağını belirsiz kılar; mevcut oranı güncelleyin ya da "
            "arşivleyin.",
        )
    db.commit()
    return _satir(db, cid, int(yeni))


@router.get("/cost-rates/{rate_id}")
def get_cost_rate(rate_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), rate_id)


@router.put("/cost-rates/{rate_id}")
def update_cost_rate(
    rate_id: int, payload: CostRateUpdate, request: Request, db: Session = Depends(get_db),
):
    cid = company_id(request)
    # Kaydın VARLIĞINI burada doğruluyoruz: UPDATE 0 satır etkilerse "kayıt yok"
    # ile "sürüm eskimiş" ayırt edilemez ve ikisi farklı cevap gerektirir.
    mevcut = _satir(db, cid, rate_id)
    _surum_dogrula(mevcut, payload.expected_updated_at)
    _hedef_dogrula(db, cid, payload)
    try:
        # SÜRÜM YÜKLEMİ UPDATE'İN İÇİNDE. Yukarıdaki kontrol tek başına TOCTOU'dur:
        # iki eşzamanlı PUT aynı `updated_at`i okur, ikisi de kontrolü geçer,
        # ikisi de yazar ve ilk yazarın değişikliği sessizce kaybolurdu. Oran
        # geçmiş maliyetin dayanağı; "son yazan kazanır" kabul edilemez.
        #
        # Karşılaştırma, İSTEMCİDEN gelen damgayla değil VERİTABANININ AZ ÖNCE
        # VERDİĞİ HAM DEĞERLE yapılıyor. Sebep dialekt: `updated_at` ham SQL ile
        # okunduğu için SQLite metin, PostgreSQL `datetime` döndürür; istemci
        # damgasını yeniden biçimlendirip bağlamak, iki diyalektten birinde
        # sessizce hiçbir satırı eşleştirmezdi. Ham değeri geri bağlamak her iki
        # diyalektte de tam eşleşir ve araya giren bir yazma varsa 0 satır eder.
        sonuc = db.execute(
            text(
                """UPDATE cost_rates SET kind=:kind,machine_id=:machine_id,
                user_id=:user_id,label=:label,hourly_rate=:hourly_rate,notes=:notes,
                status=:status,updated_at=:now
                WHERE id=:id AND company_id=:cid AND updated_at=:onceki"""
            ),
            {"id": rate_id, "cid": cid, "now": _simdi(),
             "onceki": mevcut["updated_at"],
             **payload.model_dump(exclude={"expected_updated_at"})},
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Bu hedef için zaten aktif bir oran var")
    if sonuc.rowcount != 1:
        # Kaydın var olduğunu yukarıda gördük; 0 satır = araya başka bir yazma
        # girdi. Yarışı kaybeden taraf 409 alır, sessizce üzerine yazamaz.
        db.rollback()
        raise HTTPException(409, "Kayıt siz düzenlerken değişti; yenileyip tekrar deneyin")
    db.commit()
    return _satir(db, cid, rate_id)

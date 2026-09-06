from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, insert, text, update
from sqlalchemy.orm import Session

from ..business_time import business_today
from ..db import get_db
from ..tenancy import companies, branches, memberships, user_companies, company_id
from ..auth import utcnow
from ..inventory import ensure_company_default_warehouse
from ..company_policies import VALID_POLICY_MODES, policy_override_logs
from ..firma_profilleri import FirmaProfili, profilleri_birlestir, profilleri_coz
from .auth import validate_tax_number

router = APIRouter(tags=["Firmalar"])


class CompanyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    tax_number: str | None = None
    # İSTEĞE BAĞLI ve varsayılanı BOŞ: bu uca bugün istek atan her istemci
    # `profiller` göndermiyor ve zorunlu yapmak hepsini kırardı. Boş liste
    # "seçilmedi" demektir, "hiçbiri" demez.
    profiller: list[FirmaProfili] = Field(default_factory=list)


class CompanyPolicyUpdate(BaseModel):
    negative_stock_policy: Literal["block", "manager_override", "allow"]
    credit_limit_policy: Literal["block", "manager_override", "allow"]
    tax_number: str | None = Field(default=None, max_length=40)

    # --- Tarla kuralları (mobil-erp#2, FAZ 9) ---------------------------------
    # İSTEĞE BAĞLI ve `model_fields_set` ile uygulanıyor (tax_number'daki kalıp):
    # zorunlu yapmak, bu uca bugün istek atan her istemciyi kırardı.
    #
    # `farm_early_harvest_policy` içinde "allow" YOK ve bu bilinçli — en gevşek
    # seviye "warn". Erken hasat kalıntı riski demek; kontrolü tamamen
    # kapatabilen bir ayar sessiz bir güvenlik kapatma düğmesi olurdu. "warn"
    # modunda kayıt oluşuyor ama sistemin bulduğu ihlal hasat satırına
    # yazılıyor.
    farm_area_override_policy: Literal["allow", "require_reason", "block"] | None = None
    farm_early_harvest_policy: Literal["warn", "require_reason", "block"] | None = None
    farm_spraying_dose_required: bool | None = None
    # ÇKS tek ürün ve tarlaya giriş yasağı: `allow` YOK. Kalıntı/uyum
    # kontrolünü tamamen kapatabilen bir ayar sessiz bir güvenlik kapatma
    # düğmesi olurdu. En gevşek seviye `warn` — kayıt oluşur, ihlal satıra
    # yazılır.
    farm_monoculture_policy: Literal["warn", "require_reason", "block"] | None = None
    farm_reentry_policy: Literal["warn", "require_reason", "block"] | None = None
    # Ekim-arası bekleme (göç 0072). "allow" YOK — kardeşleriyle aynı sınır:
    # kontrolü tamamen kapatabilen bir ayar sessiz bir güvenlik düğmesi olurdu.
    farm_plantback_policy: Literal["warn", "require_reason", "block"] | None = None
    # Hayvancılıkta ilaç ARINMA (bekleme) kilidi (göç 0074). "allow" YOK ve
    # gerekçe kardeşlerininkinden GÜÇLÜ: kapatılan şey İNSAN GIDASIDIR — süt
    # tanka, et de kasaba gider.
    herd_withdrawal_policy: Literal["warn", "require_reason", "block"] | None = None
    # Karantina kilidi (göç 0075). "allow" YOK — kardeşleriyle aynı sınır.
    # VARSAYILANI `block`tur ve kardeşlerinden FARKLI olmasının gerekçesi
    # göçün başlığındadır: karantinayı bir insan ELLE açmış ve AÇIK
    # bırakmıştır; onun etrafından gerekçeyle dolaşmak VARSAYILAN davranış
    # olamaz, doğru yol karantinayı KAPATMAKTIR.
    herd_quarantine_policy: Literal["warn", "require_reason", "block"] | None = None
    # Firma profilleri (Faz 5.2). `None` ile `[]` AYRI şeylerdir ve ayrım
    # `model_fields_set` ile korunur: alan hiç gönderilmezse mevcut değer
    # KORUNUR, boş liste gönderilirse seçim BİLİNÇLİ olarak temizlenir.
    profiller: list[FirmaProfili] | None = None

    @field_validator("tax_number")
    @classmethod
    def validate_vkn(cls, value: str | None) -> str | None:
        try:
            return validate_tax_number(value)[0]
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


@router.get("/companies")
def list_companies(request: Request, db: Session = Depends(get_db)):
    return user_companies(db, int(request.state.user["id"]))


@router.post("/companies", status_code=201)
def create_company(payload: CompanyCreate, request: Request, db: Session = Depends(get_db)):
    if request.state.user["role"] != "admin":
        raise HTTPException(403, "Yalnızca yönetici firma oluşturabilir")
    cid = int(
        db.execute(
            insert(companies)
            .values(
                name=payload.name.strip(),
                tax_number=payload.tax_number,
                is_active=True,
                negative_stock_policy="block",
                credit_limit_policy="block",
                profiller=profilleri_birlestir(payload.profiller),
                created_at=utcnow(),
            )
            .returning(companies.c.id)
        ).scalar_one()
    )
    branch_id = int(
        db.execute(
            insert(branches)
            .values(company_id=cid, name="Merkez", is_active=True, created_at=utcnow())
            .returning(branches.c.id)
        ).scalar_one()
    )
    db.execute(
        insert(memberships).values(
            user_id=request.state.user["id"],
            company_id=cid,
            is_default=False,
            created_at=utcnow(),
        )
    )
    ensure_company_default_warehouse(db, cid, branch_id)
    db.commit()
    return {"id": cid}


@router.get("/company-settings")
def get_company_settings(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    row = db.execute(
        select(
            companies.c.id,
            companies.c.name,
            companies.c.tax_number,
            companies.c.negative_stock_policy,
            companies.c.credit_limit_policy,
            companies.c.farm_area_override_policy,
            companies.c.farm_early_harvest_policy,
            companies.c.farm_spraying_dose_required,
            companies.c.farm_monoculture_policy,
            companies.c.farm_reentry_policy,
            companies.c.farm_plantback_policy,
            companies.c.herd_withdrawal_policy,
            companies.c.herd_quarantine_policy,
            companies.c.profiller,
        ).where(companies.c.id == cid)
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Firma bulunamadı")
    govde = dict(row)
    # Depoda virgülle birleşik TEK DİZGİ duruyor; dışarıya LİSTE veriliyor,
    # çünkü uca giren şekil de listedir. İki uçta iki farklı şekil, istemciyi
    # depo biçimini ayrıştırmaya zorlardı.
    govde["profiller"] = profilleri_coz(govde["profiller"])
    return govde


@router.put("/company-settings")
def update_company_settings(
    payload: CompanyPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    if request.state.user["role"] != "admin":
        raise HTTPException(403, "Firma politikalarını yalnızca admin değiştirebilir")
    if payload.negative_stock_policy not in VALID_POLICY_MODES or payload.credit_limit_policy not in VALID_POLICY_MODES:
        raise HTTPException(400, "Geçersiz firma politikası")
    cid = company_id(request)
    values = {
        "negative_stock_policy": payload.negative_stock_policy,
        "credit_limit_policy": payload.credit_limit_policy,
    }
    for alan in (
        "farm_area_override_policy",
        "farm_early_harvest_policy",
        "farm_spraying_dose_required",
        "farm_monoculture_policy",
        "farm_reentry_policy",
        "farm_plantback_policy",
        "herd_withdrawal_policy",
        "herd_quarantine_policy",
    ):
        if alan in payload.model_fields_set:
            values[alan] = getattr(payload, alan)
    if "profiller" in payload.model_fields_set:
        values["profiller"] = profilleri_birlestir(payload.profiller or [])
    warning = None
    if "tax_number" in payload.model_fields_set:
        values["tax_number"], warning = validate_tax_number(payload.tax_number)
    result = db.execute(
        update(companies)
        .where(companies.c.id == cid)
        .values(**values)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(404, "Firma bulunamadı")
    db.commit()
    return {
        "negative_stock_policy": payload.negative_stock_policy,
        "credit_limit_policy": payload.credit_limit_policy,
        "farm_area_override_policy": values.get("farm_area_override_policy"),
        "farm_early_harvest_policy": values.get("farm_early_harvest_policy"),
        "farm_spraying_dose_required": values.get("farm_spraying_dose_required"),
        "farm_monoculture_policy": values.get("farm_monoculture_policy"),
        "farm_reentry_policy": values.get("farm_reentry_policy"),
        "farm_plantback_policy": values.get("farm_plantback_policy"),
        "herd_withdrawal_policy": values.get("herd_withdrawal_policy"),
        "herd_quarantine_policy": values.get("herd_quarantine_policy"),
        "tax_number": values.get("tax_number"),
        "profiller": profilleri_coz(values.get("profiller")),
        "warning": warning,
    }


@router.get("/policy-overrides")
def list_policy_overrides(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    if request.state.user["role"] not in {"admin", "yonetici"}:
        raise HTTPException(403, "Politika istisnalarını görüntüleme yetkiniz yok")
    cid = company_id(request)
    rows = db.execute(
        select(policy_override_logs)
        .where(policy_override_logs.c.company_id == cid)
        .order_by(policy_override_logs.c.id.desc())
        .limit(limit)
    ).mappings().all()
    return [dict(row) for row in rows]


@router.get("/branches")
def list_branches(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    rows = db.execute(
        select(branches)
        .where(branches.c.company_id == cid, branches.c.is_active == True)
        .order_by(branches.c.name)
    ).mappings().all()
    return [dict(x) for x in rows]


@router.get("/notifications")
def notifications(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    # `orders.due_date` and `payments.payment_date` are stored as ISO date
    # strings, so they are compared against a bound ISO string rather than
    # CURRENT_DATE: PostgreSQL has no varchar/date comparison operator and
    # would raise UndefinedFunction. Lexicographic ordering of ISO dates
    # matches chronological ordering, and this keeps SQLite parity.
    today = business_today().isoformat()
    items = []
    critical = db.execute(
        text("SELECT COUNT(*) FROM products WHERE company_id=:cid AND COALESCE(stock,0)<=0"),
        {"cid": cid},
    ).scalar() or 0
    if critical:
        items.append(
            {
                "type": "stock",
                "severity": "warning",
                "title": f"{critical} ürün kritik stokta",
                "link": "/urunler",
            }
        )
    overdue = db.execute(
        text(
            "SELECT COUNT(*) FROM orders WHERE company_id=:cid AND due_date IS NOT NULL "
            "AND due_date<>'' AND due_date<:today"
        ),
        {"cid": cid, "today": today},
    ).scalar() or 0
    if overdue:
        items.append(
            {
                "type": "due",
                "severity": "error",
                "title": f"{overdue} vadesi geçmiş satış belgesi",
                "link": "/satislar",
            }
        )
    pending = db.execute(
        text("SELECT COUNT(*) FROM payments WHERE company_id=:cid AND payment_date=:today"),
        {"cid": cid, "today": today},
    ).scalar() or 0
    if pending:
        items.append(
            {
                "type": "finance",
                "severity": "info",
                "title": f"Bugün {pending} tahsilat/ödeme hareketi var",
                "link": "/odemeler",
            }
        )
    return {"count": len(items), "items": items}

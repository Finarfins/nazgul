from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from app.tenancy import companies, memberships, metadata, resolve_company


def _seed_company(db: Session, *, name: str, active: bool) -> int:
    result = db.execute(
        insert(companies).values(
            name=name,
            is_active=active,
            negative_stock_policy="block",
            credit_limit_policy="block",
            created_at=datetime.now(timezone.utc),
        )
    )
    return int(result.inserted_primary_key[0])


def test_explicit_inactive_company_selection_is_rejected() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with Session(engine) as db:
        inactive_company_id = _seed_company(db, name="Kapalı Firma", active=False)
        db.execute(
            insert(memberships).values(
                user_id=7,
                company_id=inactive_company_id,
                is_default=True,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            resolve_company(db, 7, inactive_company_id)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Bu firmaya erişim yetkiniz yok"


def test_default_resolution_skips_inactive_membership() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with Session(engine) as db:
        inactive_company_id = _seed_company(db, name="Eski Firma", active=False)
        active_company_id = _seed_company(db, name="Aktif Firma", active=True)
        created_at = datetime.now(timezone.utc)
        db.execute(
            insert(memberships),
            [
                {
                    "user_id": 11,
                    "company_id": inactive_company_id,
                    "is_default": True,
                    "created_at": created_at,
                },
                {
                    "user_id": 11,
                    "company_id": active_company_id,
                    "is_default": False,
                    "created_at": created_at,
                },
            ],
        )
        db.commit()

        resolved = resolve_company(db, 11, None)

    assert resolved == active_company_id


def test_active_member_company_still_resolves() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with Session(engine) as db:
        company_id = _seed_company(db, name="Sungur Tarım", active=True)
        db.execute(
            insert(memberships).values(
                user_id=13,
                company_id=company_id,
                is_default=True,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        assert resolve_company(db, 13, company_id) == company_id


def test_non_member_cannot_tell_active_from_inactive_from_absent() -> None:
    """ÜYE OLMAYAN için ÜÇ DURUM DA AYNI 403'tür — kiracı yaşam döngüsü sızmaz.

    NEDEN AYRI BİR KAPI VAR (5.1b'de eklendi): kiracı yumuşak imhası
    ``companies.is_active``i bir KİLİT hâline getirdi, ve kilidi görünür
    kılmak için ayrı bir hata kodu (``COMPANY_INACTIVE``) eklemek CAZİPTİR.
    Eklenmedi ve eklenmemeli: o kod, kimliği doğrulanmış herhangi bir
    kullanıcıya, AÇIKÇA ``X-Company-ID`` yazarak bir kimliğin "var olan ama
    kapatılmış bir firma" olduğunu öğrenme yolu açardı. Yukarıdaki
    ``test_explicit_inactive_company_selection_is_rejected`` yalnız ÜYENİN
    gördüğü gövdeyi ölçer; bu test ÜYE OLMAYANIN üç ayrı hedefi birbirinden
    AYIRT EDEMEDİĞİNİ ölçer ve ikisi aynı şey DEĞİLDİR.

    MUTASYON: ``resolve_company``ye "istenen firma kapalıysa farklı bir
    istisna yükselt" dalını geri koymak bu testi KIRMIZI yapar.

    BU KAPI GEREKSİZ DEĞİL — ÖLÇÜLDÜ, VARSAYILMADI. Ayrımı YALNIZ üye
    OLMAYAN için yapan bir dal denendi (üyeye genel gövde, üye olmayana
    ``COMPANY_INACTIVE``): yukarıdaki üç test YEŞİL kaldı, kırmızı yanan tek
    şey bu oldu. Yani sızıntının en zararlı biçimi — dışarıdan gelene
    söylemek — ancak burada görünüyor.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with Session(engine) as db:
        yabanci_aktif = _seed_company(db, name="Yabancı Aktif", active=True)
        yabanci_kapali = _seed_company(db, name="Yabancı Kapalı", active=False)
        db.commit()

        hatalar = []
        for hedef in (yabanci_aktif, yabanci_kapali, 987654):
            with pytest.raises(HTTPException) as tutulan:
                resolve_company(db, 99, hedef)
            hatalar.append(tutulan.value)

    # TİP, DURUM KODU ve METİN — üçü de birebir aynı olmalı. Yalnız durum
    # kodunu karşılaştırmak yetmez: ayrı bir alt sınıf ya da farklı bir metin
    # de istemciye aynı ayrımı yapardı.
    assert {type(h) for h in hatalar} == {HTTPException}, [type(h) for h in hatalar]
    assert {h.status_code for h in hatalar} == {403}, [h.status_code for h in hatalar]
    assert {h.detail for h in hatalar} == {"Bu firmaya erişim yetkiniz yok"}, [
        h.detail for h in hatalar
    ]

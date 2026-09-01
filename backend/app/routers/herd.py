"""Hayvancılık V1 — CRUD API ve sürü panosu (mobil-erp#17, FAZ 2).

BU MODÜLDE TEKRARLANAN DÖRT KURAL:

1. **Her okuma ve yazma kiracı filtreli.** Başka firmanın kimliği verilirse
   **404** — 403 "var ama sana kapalı" bilgisini sızdırır.

2. **Küpe numarası ENGELLENMEZ, UYARILIR.** Biçim beklenenden farklıysa kayıt
   yapılır ve yanıtta `warnings` döner. Küpe standardı 2026'da değişti ve
   kontrol basamağı algoritması açık kaynaklarda yok; katı kural GEÇERLİ bir
   küpeyi reddederdi (bkz. konu #17).

3. **Hareket, hayvanın durumunu DEĞİŞTİRİR.** Satış/ölüm/kesim kaydedilip
   hayvan `ACTIVE` kalsaydı "kaç hayvanım var" sorusu satılmışları da sayardı.
   İkisi AYNI işlemde yazılıyor.

4. **Türetilen değer istemciden alınmaz.** Yaş, gecikmiş aşı sayısı, doğum
   sayıları sunucuda hesaplanır.

V1 STOK/MUHASEBEYE FİŞ YAZMAZ: satış tutarı elle giriliyor, hiçbir yere
aktarılmıyor (ürün sınırı, konu #17).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..business_time import business_today
from ..db import get_db
from ..herd_schemas import (
    MOVEMENT_TO_STATUS,
    AnimalDrugCatalogueUpdate,
    AnimalDrugCatalogueWrite,
    AnimalDrugTreatmentUpdate,
    AnimalDrugTreatmentWrite,
    AnimalUpdate,
    AnimalWrite,
    BirthWrite,
    BreedingUpdate,
    BreedingWrite,
    GroupUpdate,
    GroupWrite,
    HerdFertilityResponse,
    MilkYieldWrite,
    MovementWrite,
    VaccinationWrite,
    WeightWrite,
    kupe_uyarisi,
)
from ..herd_fertility import (
    HEDEF_ARALIK_GUN,
    SERVIS_ALT_GUN,
    SERVIS_UST_GUN,
    SORUN_ARALIK_GUN,
    HayvanDolVerimi,
    hayvan_dol_verimi,
    suru_ozeti,
)
from ..herd_vaccine_schedule import (
    KOD_ADI,
    NOT_APPLICABLE,
    OVERDUE,
    TAKVIMLI_KODLAR,
    UNKNOWN,
    UPCOMING,
    DUE,
    hayvan_durumlari,
)
from ..herd_withdrawal import (
    BeklemeIhlali,
    sut_bekleme_ihlalleri,
)
from ..tenancy import company_id

router = APIRouter(tags=["herd"])

_SAYFA = Query(default=50, ge=1, le=200)
_ATLA = Query(default=0, ge=0, le=100_000)

_TABLOLAR = frozenset({
    "animals", "animal_groups", "animal_vaccinations", "animal_breedings",
    "animal_births", "animal_weights", "milk_yields", "animal_movements",
    "animal_drug_treatments", "animal_drug_catalogue",
})


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _satir(db: Session, cid: int, tablo: str, kayit_id: int) -> dict[str, Any]:
    """Kiracı kapsamlı tekil okuma; tablo adı YALNIZ bu modülün sabitinden."""
    if tablo not in _TABLOLAR:
        raise HTTPException(500, "Geçersiz tablo")
    row = db.execute(
        text(f"SELECT * FROM {tablo} WHERE id=:id AND company_id=:cid"),
        {"id": kayit_id, "cid": cid},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "Kayıt bulunamadı")
    return dict(row)


def _surum_param(beklenen: datetime) -> datetime:
    """CAS karşılaştırması için zaman dilimini tamamla, hassasiyeti koru."""
    return beklenen if beklenen.tzinfo else beklenen.replace(tzinfo=timezone.utc)


def _surum_cakismasi(db: Session) -> None:
    db.rollback()
    raise HTTPException(409, "Kayıt siz düzenlerken değişti; yenileyip tekrar deneyin")


def _gun(deger: Any) -> date | None:
    if deger is None:
        return None
    if isinstance(deger, datetime):
        return deger.date()
    if isinstance(deger, date):
        return deger
    return date.fromisoformat(str(deger)[:10])


def _sayfa(rows, toplam, limit, offset) -> dict[str, Any]:
    return {"items": [dict(r) for r in rows], "total": int(toplam or 0),
            "limit": limit, "offset": offset}


# ---------------------------------------------------------------------- sürü ---

@router.get("/animal-groups")
def list_groups(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    status: str | None = None, db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = " AND status=:status" if status else ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if status:
        params["status"] = status.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animal_groups WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,code,name,species,location,head_count,notes,status,updated_at
            FROM animal_groups WHERE company_id=:cid{kosul}
            ORDER BY code LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


@router.post("/animal-groups", status_code=201)
def create_group(payload: GroupWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    now = _simdi()
    try:
        yeni = db.execute(
            text(
                """INSERT INTO animal_groups(company_id,code,name,species,location,
                head_count,notes,status,created_at,updated_at)
                VALUES(:cid,:code,:name,:species,:location,:head_count,:notes,
                'ACTIVE',:now,:now) RETURNING id"""
            ),
            {"cid": cid, "now": now, **payload.model_dump()},
        ).scalar_one()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Bu sürü kodu zaten kullanılıyor")
    db.commit()
    return _satir(db, cid, "animal_groups", int(yeni))


@router.get("/animal-groups/{group_id}")
def get_group(group_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), "animal_groups", group_id)


@router.put("/animal-groups/{group_id}")
def update_group(group_id: int, payload: GroupUpdate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _kilitli_grup(db, cid, group_id)
    _grup_sayim_dogrula(db, cid, group_id, payload.head_count)
    sonuc = db.execute(
        text(
            """UPDATE animal_groups SET code=:code,name=:name,species=:species,
            location=:location,head_count=:head_count,notes=:notes,status=:status,
            updated_at=:now WHERE id=:id AND company_id=:cid
            AND updated_at=:expected_updated_at"""
        ),
        {"id": group_id, "cid": cid, "now": _simdi(),
         "expected_updated_at": _surum_param(payload.expected_updated_at),
         **payload.model_dump(exclude={"expected_updated_at"})},
    )
    if sonuc.rowcount != 1:
        _surum_cakismasi(db)
    db.commit()
    return _satir(db, cid, "animal_groups", group_id)


def _kilitli_grup(db: Session, cid: int, group_id: int) -> dict[str, Any]:
    """Baş sayısı ve bireysel atamalar için ortak sürü satırı kilidi."""
    kilit = " FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(
        text(
            "SELECT * FROM animal_groups "
            f"WHERE id=:id AND company_id=:cid{kilit}"
        ),
        {"id": group_id, "cid": cid},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "Kayıt bulunamadı")
    return dict(row)


def _grup_sayim_dogrula(db: Session, cid: int, group_id: int, head_count: int | None) -> None:
    """Bireysel kayıt VARSA sürünün elle baş sayısı olamaz.

    İkisi birden dolu olursa aynı hayvanlar iki kez sayılır ve "kaç hayvanım
    var" sorusunun iki farklı cevabı olur. Hangisinin doğru olduğunu sistem
    bilemez; bu yüzden baştan engelleniyor.
    """
    if head_count is None:
        return
    var = db.execute(
        text(
            """SELECT COUNT(*) FROM animals
            WHERE company_id=:cid AND group_id=:gid AND status='ACTIVE'"""
        ),
        {"cid": cid, "gid": group_id},
    ).scalar()
    if var:
        raise HTTPException(
            422,
            f"Bu sürüde {var} bireysel hayvan kaydı var; ayrıca elle baş sayısı "
            "girilemez (aynı hayvanlar iki kez sayılır). Baş sayısını boş bırakın.",
        )


# -------------------------------------------------------------------- hayvan ---

@router.get("/animals")
def list_animals(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    status: str | None = None, species: str | None = None,
    group_id: int | None = None, sex: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    for alan, deger in (("status", status), ("species", species), ("sex", sex)):
        if deger:
            kosul += f" AND {alan}=:{alan}"
            params[alan] = deger.strip().upper()
    if group_id:
        kosul += " AND group_id=:group_id"
        params["group_id"] = group_id
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animals WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,ear_tag,name,species,breed,sex,birth_date,acquisition,
            acquired_on,group_id,mother_id,father_id,status,notes,updated_at
            FROM animals WHERE company_id=:cid{kosul}
            ORDER BY ear_tag IS NULL,ear_tag,id LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


def _kupe_cakismasi(db: Session, cid: int, ear_tag: str | None, haric_id: int | None = None) -> None:
    """Aynı küpe İKİ AKTİF hayvanda olamaz.

    Veritabanındaki koşullu indeks son savunma; buradaki kontrol kullanıcıya
    ham bir kısıt hatası yerine ne olduğunu söyleyen bir mesaj verir.
    """
    if not ear_tag:
        return
    params: dict[str, Any] = {"cid": cid, "tag": ear_tag}
    haric = ""
    if haric_id is not None:
        haric = " AND id<>:haric"
        params["haric"] = haric_id
    var = db.execute(
        text(
            f"""SELECT id FROM animals
            WHERE company_id=:cid AND ear_tag=:tag AND status='ACTIVE'{haric}"""
        ),
        params,
    ).scalar()
    if var:
        raise HTTPException(
            409,
            f"{ear_tag} küpe numarası zaten aktif bir hayvanda kullanılıyor "
            f"(#{var}). Aynı küpe iki canlı hayvana verilemez; hayvan satıldıysa "
            "önce durumunu güncelleyin.",
        )


def _hayvan_dogrula(db: Session, cid: int, payload: AnimalWrite | AnimalUpdate) -> None:
    if payload.group_id is not None:
        grup = _kilitli_grup(db, cid, payload.group_id)
        aktif = not isinstance(payload, AnimalUpdate) or payload.status == "ACTIVE"
        if aktif and grup.get("head_count") is not None:
            raise HTTPException(
                422,
                "Bu sürüde elle baş sayısı var; ayrıca bireysel hayvan atanamaz "
                "(aynı hayvanlar iki kez sayılır). Baş sayısını boş bırakın.",
            )
    for alan, kimlik in (("mother_id", payload.mother_id), ("father_id", payload.father_id)):
        if kimlik is None:
            continue
        ebeveyn = _satir(db, cid, "animals", kimlik)
        beklenen = "FEMALE" if alan == "mother_id" else "MALE"
        if ebeveyn["sex"] != beklenen:
            raise HTTPException(
                422,
                f"{'Anne' if beklenen == 'FEMALE' else 'Baba'} olarak seçilen hayvanın "
                f"cinsiyeti uyuşmuyor",
            )
        # DOĞUM TARİHİ ANNEDEN SONRA OLMALI. Veritabanı bunu bilemez (aynı
        # tablonun iki satırı arasındaki ilişki).
        ebeveyn_dogum = _gun(ebeveyn.get("birth_date"))
        if payload.birth_date and ebeveyn_dogum and payload.birth_date <= ebeveyn_dogum:
            raise HTTPException(
                422,
                f"Doğum tarihi, ebeveynin doğum tarihinden ({ebeveyn_dogum}) sonra olmalı",
            )


@router.post("/animals", status_code=201)
def create_animal(payload: AnimalWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _hayvan_dogrula(db, cid, payload)
    _kupe_cakismasi(db, cid, payload.ear_tag)
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO animals(company_id,ear_tag,name,species,breed,sex,birth_date,
            acquisition,acquired_on,group_id,mother_id,father_id,notes,status,
            created_at,updated_at)
            VALUES(:cid,:ear_tag,:name,:species,:breed,:sex,:birth_date,:acquisition,
            :acquired_on,:group_id,:mother_id,:father_id,:notes,'ACTIVE',:now,:now)
            RETURNING id"""
        ),
        {"cid": cid, "now": now, **payload.model_dump()},
    ).scalar_one()
    db.commit()
    kayit = _satir(db, cid, "animals", int(yeni))
    # KÜPE UYARISI: kayıt YAPILDI, ama biçim alışılmışın dışındaysa söylüyoruz.
    uyari = kupe_uyarisi(payload.ear_tag)
    kayit["warnings"] = [uyari] if uyari else []
    return kayit


@router.get("/animals/{animal_id}")
def get_animal(animal_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    hayvan = _satir(db, cid, "animals", animal_id)
    bugun = business_today()
    dogum = _gun(hayvan.get("birth_date"))
    # YAŞ SUNUCUDA TÜRETİLİR. İstemcide hesaplansaydı cihaz saati yanlış olan
    # bir telefon yanlış yaş gösterirdi ve aşı takvimi ona göre kayardı.
    hayvan["age_days"] = (bugun - dogum).days if dogum else None
    return hayvan


@router.put("/animals/{animal_id}")
def update_animal(animal_id: int, payload: AnimalUpdate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _satir(db, cid, "animals", animal_id)
    _hayvan_dogrula(db, cid, payload)
    if payload.status == "ACTIVE":
        _kupe_cakismasi(db, cid, payload.ear_tag, haric_id=animal_id)
    sonuc = db.execute(
        text(
            """UPDATE animals SET ear_tag=:ear_tag,name=:name,species=:species,
            breed=:breed,sex=:sex,birth_date=:birth_date,acquisition=:acquisition,
            acquired_on=:acquired_on,group_id=:group_id,mother_id=:mother_id,
            father_id=:father_id,notes=:notes,status=:status,updated_at=:now
            WHERE id=:id AND company_id=:cid
            AND updated_at=:expected_updated_at"""
        ),
        {"id": animal_id, "cid": cid, "now": _simdi(),
         "expected_updated_at": _surum_param(payload.expected_updated_at),
         **payload.model_dump(exclude={"expected_updated_at"})},
    )
    if sonuc.rowcount != 1:
        _surum_cakismasi(db)
    db.commit()
    kayit = _satir(db, cid, "animals", animal_id)
    uyari = kupe_uyarisi(payload.ear_tag)
    kayit["warnings"] = [uyari] if uyari else []
    return kayit


# ----------------------------------------------------------------------- aşı ---

@router.get("/animal-vaccinations")
def list_vaccinations(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    animal_id: int | None = None, db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = " AND animal_id=:animal_id" if animal_id else ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if animal_id:
        params["animal_id"] = animal_id
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animal_vaccinations WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,animal_id,vaccine,vaccine_code,applied_on,dose_no,next_due_on,
            veterinarian,batch_no,notes,status,updated_at
            FROM animal_vaccinations WHERE company_id=:cid{kosul}
            ORDER BY applied_on DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


@router.post("/animal-vaccinations", status_code=201)
def create_vaccination(payload: VaccinationWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    hayvan = _satir(db, cid, "animals", payload.animal_id)
    # AŞI, HAYVANIN DOĞUMUNDAN ÖNCE OLAMAZ. Veritabanı iki tablo arasındaki
    # bu ilişkiyi bilemez.
    dogum = _gun(hayvan.get("birth_date"))
    if dogum and payload.applied_on < dogum:
        raise HTTPException(
            422, f"Aşı tarihi hayvanın doğum tarihinden ({dogum}) önce olamaz"
        )
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO animal_vaccinations(company_id,animal_id,vaccine,vaccine_code,
            applied_on,dose_no,next_due_on,veterinarian,batch_no,notes,status,
            created_at,updated_at)
            VALUES(:cid,:animal_id,:vaccine,:vaccine_code,:applied_on,:dose_no,
            :next_due_on,:veterinarian,:batch_no,:notes,'RECORDED',:now,:now)
            RETURNING id"""
        ),
        {"cid": cid, "now": now, **payload.model_dump()},
    ).scalar_one()
    db.commit()
    return _satir(db, cid, "animal_vaccinations", int(yeni))


@router.get("/vaccination-calendar")
def vaccination_calendar(
    request: Request,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    """Yaşa bağlı ZORUNLU aşı takvimi: kimin şapı/brusellası ne zaman.

    FAZ 2'deki `vaccination_overdue` sayısıyla AYNI ŞEY DEĞİL ve olmaması
    bilinçli. O sayı yalnız kullanıcının ELLE girdiği `next_due_on` tarihine
    bakıyor — yani "planladım, yapmadım". Buradaki hesap doğum tarihinden ve
    kaydedilmiş dozlardan TÜRETİLİYOR — yani "mevzuat gereği yapılmalıydı".
    İkisini tek sayıda birleştirmek, hiç plan girmemiş bir işletmeye "aşınız
    tamam" demek olurdu.

    ÜÇ ŞEY YANITTA AÇIKÇA DURUYOR, çünkü üçü de gizlendiğinde sayı yanlış
    okunur:

    * `uncoded_vaccinations` — kodu olmayan aşı kaydı sayısı. Bu kayıtlar
      hesaba GİRMİYOR (bkz. migration 0050: metinden tahmin sahte gecikme
      üretir). Kodsuz kayıt varken "eksiği yok" cevabı eksik bir cevaptır.
    * `unknown_birth_date` — doğum tarihi girilmemiş hayvan sayısı. Onların
      takvimi hesaplanamaz ve "tamam" sayılmaz.
    * `due_from`/`due_to` — pencere ARALIK olarak; tek tarihe indirilmiyor
      (şap tekrarı 4–6 ay, brusella ikinci doz 4–12 ay).
    """
    cid = company_id(request)
    bugun = business_today()

    hayvanlar = db.execute(
        text(
            """SELECT id,ear_tag,name,species,sex,birth_date FROM animals
            WHERE company_id=:cid AND status='ACTIVE'
            ORDER BY ear_tag IS NULL,ear_tag,id"""
        ),
        {"cid": cid},
    ).mappings().all()

    # Takvimi olan kodların dozları. Kodsuz satırlar BİLEREK dışarıda.
    dozlar: dict[int, dict[str, list[date]]] = {}
    kod_satirlari = db.execute(
        text(
            """SELECT animal_id,vaccine_code,applied_on FROM animal_vaccinations
            WHERE company_id=:cid AND vaccine_code IN :kodlar
            ORDER BY applied_on"""
        ).bindparams(bindparam("kodlar", expanding=True)),
        {"cid": cid, "kodlar": list(TAKVIMLI_KODLAR)},
    ).mappings().all()
    for satir in kod_satirlari:
        gun = _gun(satir["applied_on"])
        if gun is None:
            continue
        dozlar.setdefault(int(satir["animal_id"]), {}).setdefault(
            satir["vaccine_code"], []
        ).append(gun)

    kodsuz = db.execute(
        text(
            """SELECT COUNT(*) FROM animal_vaccinations
            WHERE company_id=:cid AND (vaccine_code IS NULL OR vaccine_code='')"""
        ),
        {"cid": cid},
    ).scalar()

    istenen = state.strip().upper() if state else None
    satirlar: list[dict[str, Any]] = []
    sayac = {"UPCOMING": 0, "DUE": 0, "OVERDUE": 0, "UNKNOWN": 0}
    dogumsuz = 0

    for hayvan in hayvanlar:
        dogum = _gun(hayvan.get("birth_date"))
        if dogum is None:
            dogumsuz += 1
        for durum in hayvan_durumlari(
            sex=hayvan["sex"], birth_date=dogum,
            dozlar_koda_gore=dozlar.get(int(hayvan["id"]), {}), bugun=bugun,
        ):
            if durum.state == NOT_APPLICABLE:
                continue
            sayac[durum.state] += 1
            if istenen and durum.state != istenen:
                continue
            satirlar.append({
                "animal_id": int(hayvan["id"]),
                "ear_tag": hayvan.get("ear_tag"),
                "name": hayvan.get("name"),
                "species": hayvan["species"],
                "sex": hayvan["sex"],
                "vaccine_code": durum.code,
                "vaccine_name": KOD_ADI.get(durum.code, durum.code),
                "state": durum.state,
                "dose_no": durum.dose_no,
                "due_from": durum.due_from.isoformat() if durum.due_from else None,
                "due_to": durum.due_to.isoformat() if durum.due_to else None,
                "last_applied_on": (
                    durum.last_applied_on.isoformat() if durum.last_applied_on else None
                ),
                "overdue_days": durum.overdue_days,
                # Kullanıcı sayıya değil GEREKÇEYE güvenir; hesabın dayanağı
                # satırla birlikte geliyor.
                "basis": durum.basis,
            })

    # Gecikmişler önce, en çok gecikmiş en üstte; sonra penceresi yakın olanlar.
    sira = {OVERDUE: 0, DUE: 1, UPCOMING: 2, UNKNOWN: 3}
    satirlar.sort(key=lambda r: (
        sira.get(r["state"], 9),
        -(r["overdue_days"] or 0),
        r["due_from"] or "9999-12-31",
    ))

    return {
        "as_of": bugun.isoformat(),
        "summary": {
            "overdue": sayac[OVERDUE],
            "due": sayac[DUE],
            "upcoming": sayac[UPCOMING],
            "unknown": sayac[UNKNOWN],
            # Bu iki sayı, yukarıdaki dördünün NEYİ KAPSAMADIĞINI söylüyor.
            "uncoded_vaccinations": int(kodsuz or 0),
            "unknown_birth_date": dogumsuz,
        },
        "items": satirlar,
    }


# ----------------------------------------------------------------- tohumlama ---

@router.get("/animal-breedings")
def list_breedings(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    animal_id: int | None = None, result: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if animal_id:
        kosul += " AND animal_id=:animal_id"
        params["animal_id"] = animal_id
    if result:
        kosul += " AND result=:result"
        params["result"] = result.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animal_breedings WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,animal_id,bred_on,method,sire_code,sire_animal_id,technician,
            result,checked_on,expected_birth_on,notes,status,updated_at
            FROM animal_breedings WHERE company_id=:cid{kosul}
            ORDER BY bred_on DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


def _disi_dogrula(db: Session, cid: int, animal_id: int) -> dict[str, Any]:
    hayvan = _satir(db, cid, "animals", animal_id)
    if hayvan["sex"] != "FEMALE":
        raise HTTPException(422, "Tohumlama yalnız dişi hayvana kaydedilebilir")
    return hayvan


@router.post("/animal-breedings", status_code=201)
def create_breeding(payload: BreedingWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _disi_dogrula(db, cid, payload.animal_id)
    if payload.sire_animal_id is not None:
        boga = _satir(db, cid, "animals", payload.sire_animal_id)
        if boga["sex"] != "MALE":
            raise HTTPException(422, "Baba olarak seçilen hayvan erkek olmalı")
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO animal_breedings(company_id,animal_id,bred_on,method,
            sire_code,sire_animal_id,technician,notes,result,status,created_at,updated_at)
            VALUES(:cid,:animal_id,:bred_on,:method,:sire_code,:sire_animal_id,
            :technician,:notes,'UNKNOWN','RECORDED',:now,:now) RETURNING id"""
        ),
        {"cid": cid, "now": now, **payload.model_dump()},
    ).scalar_one()
    db.commit()
    return _satir(db, cid, "animal_breedings", int(yeni))


@router.get("/animal-breedings/{breeding_id}")
def get_breeding(breeding_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), "animal_breedings", breeding_id)


@router.put("/animal-breedings/{breeding_id}")
def update_breeding(breeding_id: int, payload: BreedingUpdate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _satir(db, cid, "animal_breedings", breeding_id)
    _disi_dogrula(db, cid, payload.animal_id)
    sonuc = db.execute(
        text(
            """UPDATE animal_breedings SET animal_id=:animal_id,bred_on=:bred_on,
            method=:method,sire_code=:sire_code,sire_animal_id=:sire_animal_id,
            technician=:technician,notes=:notes,result=:result,checked_on=:checked_on,
            expected_birth_on=:expected_birth_on,updated_at=:now
            WHERE id=:id AND company_id=:cid
            AND updated_at=:expected_updated_at"""
        ),
        {"id": breeding_id, "cid": cid, "now": _simdi(),
         "expected_updated_at": _surum_param(payload.expected_updated_at),
         **payload.model_dump(exclude={"expected_updated_at"})},
    )
    if sonuc.rowcount != 1:
        _surum_cakismasi(db)
    db.commit()
    return _satir(db, cid, "animal_breedings", breeding_id)


# --------------------------------------------------------------------- doğum ---

@router.get("/animal-births")
def list_births(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    mother_id: int | None = None, db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = " AND mother_id=:mother_id" if mother_id else ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if mother_id:
        params["mother_id"] = mother_id
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animal_births WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,mother_id,breeding_id,birth_date,outcome,difficulty,
            offspring_count,notes,status,updated_at
            FROM animal_births WHERE company_id=:cid{kosul}
            ORDER BY birth_date DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


@router.post("/animal-births", status_code=201)
def create_birth(payload: BirthWrite, request: Request, db: Session = Depends(get_db)):
    """Doğum kaydı + yavrular AYNI İŞLEMDE.

    Doğum İKİ ŞEYDİR: annenin geçmişinde bir olay ve yeni hayvan kayıtları.
    İki ayrı istekte yapılsaydı arada kesilme yavrusuz bir doğum kaydı
    bırakırdı — sürü sayısı eksik, doğum geçmişi yanıltıcı olurdu.
    """
    cid = company_id(request)
    anne = _disi_dogrula(db, cid, payload.mother_id)

    anne_dogum = _gun(anne.get("birth_date"))
    if anne_dogum and payload.birth_date <= anne_dogum:
        raise HTTPException(
            422, f"Doğum tarihi, annenin doğum tarihinden ({anne_dogum}) sonra olmalı"
        )
    if payload.breeding_id is not None:
        tohumlama = _satir(db, cid, "animal_breedings", payload.breeding_id)
        if int(tohumlama["animal_id"]) != payload.mother_id:
            raise HTTPException(422, "Seçilen tohumlama kaydı bu anneye ait değil")
        tohum_gun = _gun(tohumlama.get("bred_on"))
        if tohum_gun and payload.birth_date <= tohum_gun:
            raise HTTPException(
                422, f"Doğum tarihi tohumlama tarihinden ({tohum_gun}) sonra olmalı"
            )

    yavrular = payload.offspring or []
    # ÖLÜ DOĞUMDA YAVRU KAYDI AÇILMAZ. Açılsaydı sürüde olmayan bir hayvan
    # görünür ve sayım şişerdi.
    if payload.outcome != "LIVE" and yavrular:
        raise HTTPException(
            422, "Ölü doğum/atık kaydında yavru hayvan kaydı oluşturulamaz"
        )
    # Sayı: yavru satırı verildiyse ondan, verilmediyse alandan (küçükbaş
    # sürülerinde bireysel kayıt tutulmaz).
    sayi = len(yavrular) if yavrular else (payload.offspring_count if payload.offspring_count is not None else (1 if payload.outcome == "LIVE" else 0))

    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO animal_births(company_id,mother_id,breeding_id,birth_date,
            outcome,difficulty,offspring_count,notes,status,created_at,updated_at)
            VALUES(:cid,:mother_id,:breeding_id,:birth_date,:outcome,:difficulty,
            :sayi,:notes,'RECORDED',:now,:now) RETURNING id"""
        ),
        {"cid": cid, "now": now, "sayi": sayi,
         **payload.model_dump(exclude={"offspring", "offspring_count"})},
    ).scalar_one()

    olusan = []
    uyarilar = []
    for yavru in yavrular:
        _kupe_cakismasi(db, cid, yavru.ear_tag)
        uyari = kupe_uyarisi(yavru.ear_tag)
        if uyari:
            uyarilar.append(uyari)
        yavru_id = db.execute(
            text(
                """INSERT INTO animals(company_id,ear_tag,name,species,sex,birth_date,
                acquisition,acquired_on,group_id,mother_id,notes,status,
                created_at,updated_at)
                VALUES(:cid,:ear_tag,:name,:species,:sex,:birth_date,'BORN',
                :birth_date,:group_id,:mother_id,:notes,'ACTIVE',:now,:now)
                RETURNING id"""
            ),
            {"cid": cid, "now": now, "species": anne["species"],
             "birth_date": payload.birth_date, "group_id": anne.get("group_id"),
             "mother_id": payload.mother_id, **yavru.model_dump()},
        ).scalar_one()
        olusan.append(int(yavru_id))

    db.commit()
    kayit = _satir(db, cid, "animal_births", int(yeni))
    kayit["offspring_ids"] = olusan
    kayit["warnings"] = uyarilar
    return kayit


# ------------------------------------------------------------------- tartım ---

@router.get("/animal-weights")
def list_weights(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    animal_id: int | None = None, db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = " AND animal_id=:animal_id" if animal_id else ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if animal_id:
        params["animal_id"] = animal_id
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animal_weights WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,animal_id,weighed_on,weight_kg,notes,updated_at
            FROM animal_weights WHERE company_id=:cid{kosul}
            ORDER BY weighed_on DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


@router.post("/animal-weights", status_code=201)
def create_weight(payload: WeightWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _satir(db, cid, "animals", payload.animal_id)
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO animal_weights(company_id,animal_id,weighed_on,weight_kg,
            notes,created_at,updated_at)
            VALUES(:cid,:animal_id,:weighed_on,:weight_kg,:notes,:now,:now) RETURNING id"""
        ),
        {"cid": cid, "now": now, **payload.model_dump()},
    ).scalar_one()
    db.commit()
    return _satir(db, cid, "animal_weights", int(yeni))


# ---------------------------------------------------------------------- süt ---

_HERD_VARSAYILAN_KURALLAR = {
    "herd_withdrawal_milk_policy": "require_reason",
}

def _firma_herd_kurallari(db: Session, cid: int) -> dict[str, Any]:
    """Firmanın hayvancılık kural ayarları; satır okunamazsa VARSAYILAN (sıkı).

    ``_firma_kurallari`` (farm.py) ile aynı desen; fail-closed. Okunamayan bir
    ayarda gevşek tarafa düşmek, bir veritabanı sorununu sessizce kural
    gevşetmesine çevirirdi.
    """
    row = db.execute(
        text(
            """SELECT herd_withdrawal_milk_policy FROM companies WHERE id=:cid"""
        ),
        {"cid": cid},
    ).mappings().first()
    if not row:
        return dict(_HERD_VARSAYILAN_KURALLAR)
    return {
        "herd_withdrawal_milk_policy": (
            row["herd_withdrawal_milk_policy"] or "require_reason"
        ),
    }

def _ilac_katalog_coz(
    db: Session, cid: int, drug_name: str, species: str | None,
) -> dict[str, Any] | None:
    """İlaç + tür için katalog satırını çöz: tür özeli önce, joker yedek.

    ``species=''`` joker ("bütün türler"); `MIXED` gerçek bir değerdir ve
    joker ile karışmaz. Eşleşme Python'da yapılıyor: SQL ``LOWER()`` Türkçe
    İ/ı diyalekte göre değişir (PR #18'in aynı gerekçesi).
    """
    satirlar = db.execute(
        text(
            """SELECT id,drug_name,species,milk_withdrawal_days,meat_withdrawal_days
            FROM animal_drug_catalogue
            WHERE company_id=:cid AND status='ACTIVE'"""
        ),
        {"cid": cid},
    ).mappings().all()
    aranan = drug_name.casefold().strip()
    ozel: dict[str, Any] | None = None
    joker: dict[str, Any] | None = None
    for r in satirlar:
        if str(r["drug_name"]).casefold().strip() != aranan:
            continue
        katalog_tur = str(r["species"] or "").strip()
        if katalog_tur and species and katalog_tur == species.strip().upper():
            ozel = dict(r)
            break
        if not katalog_tur:
            joker = dict(r)
    return ozel or joker

def _ilac_sureleri_coz(
    db: Session, cid: int, payload: AnimalDrugTreatmentWrite,
    hayvan: dict[str, Any],
) -> tuple[int | None, int | None, str | None, str | None,
           int | None, int | None]:
    """``(milk_gun, meat_gun, milk_koken, meat_koken, katalog_milk, katalog_meat)``.

    Katalog varsa her süre için ayrı karar: operatör girdiyse kazanır
    (uyuşuyorsa `OPERATOR`, uyuşmuyorsa `OPERATOR_OVERRIDE`); girmediyse
    katalog değerini alır (`CATALOGUE`); katalog da yoksa NULL kalır.
    """
    katalog = (
        _ilac_katalog_coz(
            db, cid, payload.drug_name, hayvan.get("species"),
        )
    )
    katalog_milk = int(katalog["milk_withdrawal_days"]) if katalog else None
    katalog_meat = int(katalog["meat_withdrawal_days"]) if katalog else None

    milk_koken: str | None = None
    meat_koken: str | None = None
    milk_gun = payload.milk_withdrawal_days
    meat_gun = payload.meat_withdrawal_days

    if milk_gun is None:
        if katalog_milk is not None:
            milk_gun, milk_koken = katalog_milk, "CATALOGUE"
    elif katalog_milk is None:
        milk_koken = "OPERATOR"
    elif milk_gun == katalog_milk:
        milk_koken = "OPERATOR"
    else:
        milk_koken = "OPERATOR_OVERRIDE"

    if meat_gun is None:
        if katalog_meat is not None:
            meat_gun, meat_koken = katalog_meat, "CATALOGUE"
    elif katalog_meat is None:
        meat_koken = "OPERATOR"
    elif meat_gun == katalog_meat:
        meat_koken = "OPERATOR"
    else:
        meat_koken = "OPERATOR_OVERRIDE"

    return milk_gun, meat_gun, milk_koken, meat_koken, katalog_milk, katalog_meat

def _sut_bekleme_dogrula(
    db: Session, cid: int, payload: MilkYieldWrite,
    politika: str,
) -> str | None:
    """Süt bekleme süresi kontrolü — KİLİT NOKTASI (rapor değil).

    Farm tarafındaki `_hasat_guvenlik_dogrula` ile aynı sözleşme:
    `block` → 422; `warn` → kabul, metin `safety_warning`e yazılır;
    `require_reason` → gerekçesiz 422, gerekçeyle kabul.

    Grup sağımında sürüdeki bütün aktif hayvanlara bakılır; tek hayvanın
    ihlali sürüyü kilitler (toplu sağım tek kapta karışır). 422 mesajında
    hangi hayvanların bekleme süresinde olduğu KÜPE NUMARASIYLA söylenir,
    10 satırda kesilir ve `+N daha` eklenir.
    """
    hedef_gun = payload.milked_on

    if payload.animal_id is not None:
        hedef_hayvanlar = [{"id": payload.animal_id, "ear_tag": None}]
    else:
        hedef_hayvanlar = db.execute(
            text(
                """SELECT id,ear_tag FROM animals
                WHERE company_id=:cid AND group_id=:gid AND status='ACTIVE'"""
            ),
            {"cid": cid, "gid": payload.group_id},
        ).mappings().all()

    if not hedef_hayvanlar:
        return None

    hayvan_idleri = [int(h["id"]) for h in hedef_hayvanlar]
    kupeler = {int(h["id"]): h.get("ear_tag") for h in hedef_hayvanlar}

    ilac_satirlari = db.execute(
        text(
            """SELECT id,animal_id,drug_name,treated_on,milk_withdrawal_days
            FROM animal_drug_treatments
            WHERE company_id=:cid
              AND animal_id IN :animal_ids
              AND status='RECORDED'
              AND milk_withdrawal_days IS NOT NULL
              AND treated_on <= :hedef_gun"""
        ).bindparams(bindparam("animal_ids", expanding=True)),
        {"cid": cid, "animal_ids": hayvan_idleri, "hedef_gun": hedef_gun},
    ).mappings().all()

    ilaclar_hayvana_gore: dict[int, list[dict]] = {}
    for satir in ilac_satirlari:
        ilaclar_hayvana_gore.setdefault(int(satir["animal_id"]), []).append(dict(satir))

    tum_ihlaller: list[tuple[int, BeklemeIhlali]] = []
    for aid in hayvan_idleri:
        ihlaller = sut_bekleme_ihlalleri(
            sutirlar=ilaclar_hayvana_gore.get(aid, []),
            hedef_gun=hedef_gun,
        )
        for ihlal in ihlaller:
            tum_ihlaller.append((aid, ihlal))

    if not tum_ihlaller:
        return None

    metinler = [
        f"{kupeler.get(aid) or '#' + str(aid)}: {ihlal.basis}, güvenli tarih "
        f"{ihlal.safe_from.isoformat()}"
        for aid, ihlal in tum_ihlaller
    ]
    if len(metinler) > 10:
        metin = "; ".join(metinler[:10]) + f"; +{len(metinler) - 10} daha"
    else:
        metin = "; ".join(metinler)

    if politika == "block":
        raise HTTPException(
            422,
            f"Süt bekleme süresi dolmadı: {metin}. "
            "Firma ayarı bekleme süresi dolmadan sağıma izin vermiyor.",
        )
    if politika == "warn":
        return metin
    if payload.safety_override_reason:
        return metin
    raise HTTPException(
        422,
        f"Süt bekleme süresi dolmadı: {metin}. "
        "Yine de kaydetmek için gerekçe girin.",
    )

@router.get("/milk-yields")
def list_milk(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    animal_id: int | None = None, group_id: int | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if animal_id:
        kosul += " AND animal_id=:animal_id"
        params["animal_id"] = animal_id
    if group_id:
        kosul += " AND group_id=:group_id"
        params["group_id"] = group_id
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM milk_yields WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,animal_id,group_id,milked_on,session,quantity_liters,notes,updated_at
            FROM milk_yields WHERE company_id=:cid{kosul}
            ORDER BY milked_on DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


@router.post("/milk-yields", status_code=201)
def create_milk(payload: MilkYieldWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    # HAYVAN YA DA GRUP — İKİSİ BİRDEN DEĞİL. Veritabanındaki CHECK son
    # savunma; buradaki kontrol kullanıcıya anlaşılır mesaj verir.
    if (payload.animal_id is None) == (payload.group_id is None):
        raise HTTPException(
            422,
            "Süt kaydı ya bir hayvana ya bir sürüye bağlanmalı; ikisi birden "
            "verilirse aynı süt iki kez sayılır.",
        )
    if payload.animal_id is not None:
        _satir(db, cid, "animals", payload.animal_id)
    else:
        _satir(db, cid, "animal_groups", payload.group_id)
    uyari = _sut_bekleme_dogrula(
        db, cid, payload,
        _firma_herd_kurallari(db, cid)["herd_withdrawal_milk_policy"],
    )
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO milk_yields(company_id,animal_id,group_id,milked_on,session,
            quantity_liters,notes,safety_override_reason,safety_warning,
            created_at,updated_at)
            VALUES(:cid,:animal_id,:group_id,:milked_on,:session,:quantity_liters,
            :notes,:safety_override_reason,:safety_warning,:now,:now) RETURNING id"""
        ),
        {"cid": cid, "now": now, "safety_warning": uyari, **payload.model_dump()},
    ).scalar_one()
    db.commit()
    return _satir(db, cid, "milk_yields", int(yeni))


# ------------------------------------------------------------------ hareket ---

@router.get("/animal-movements")
def list_movements(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    animal_id: int | None = None, kind: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if animal_id:
        kosul += " AND animal_id=:animal_id"
        params["animal_id"] = animal_id
    if kind:
        kosul += " AND kind=:kind"
        params["kind"] = kind.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animal_movements WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,animal_id,kind,moved_on,amount,counterparty,reason,notes,updated_at
            FROM animal_movements WHERE company_id=:cid{kosul}
            ORDER BY moved_on DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


@router.post("/animal-movements", status_code=201)
def create_movement(payload: MovementWrite, request: Request, db: Session = Depends(get_db)):
    """Hareket kaydı + hayvanın durumu AYNI İŞLEMDE.

    Satış/ölüm/kesim kaydedilip hayvan `ACTIVE` kalsaydı "kaç hayvanım var"
    sorusu satılmış hayvanları da sayardı — ve kimse farkı görmezdi.
    """
    cid = company_id(request)
    hayvan = _satir(db, cid, "animals", payload.animal_id)
    now = _simdi()
    db.execute(
        text(
            """INSERT INTO animal_movements(company_id,animal_id,kind,moved_on,amount,
            counterparty,reason,notes,created_at,updated_at)
            VALUES(:cid,:animal_id,:kind,:moved_on,:amount,:counterparty,:reason,
            :notes,:now,:now)"""
        ),
        {"cid": cid, "now": now, **payload.model_dump()},
    )
    yeni_durum = MOVEMENT_TO_STATUS.get(payload.kind)
    if yeni_durum and hayvan["status"] != yeni_durum:
        db.execute(
            text("UPDATE animals SET status=:s,updated_at=:now WHERE id=:id AND company_id=:cid"),
            {"s": yeni_durum, "now": now, "id": payload.animal_id, "cid": cid},
        )
    db.commit()
    return {
        "animal": _satir(db, cid, "animals", payload.animal_id),
        "movement_kind": payload.kind,
    }



# --------------------------------------------------- ilaç bekleme süresi ---

@router.get("/animal-drug-treatments")
def list_treatments(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    animal_id: int | None = None, status: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if animal_id:
        kosul += " AND animal_id=:animal_id"
        params["animal_id"] = animal_id
    if status:
        kosul += " AND status=:status"
        params["status"] = status.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animal_drug_treatments WHERE company_id=:cid{kosul}"),
        params,
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,animal_id,catalogue_id,drug_name,treated_on,
            milk_withdrawal_days,meat_withdrawal_days,milk_withdrawal_source,
            meat_withdrawal_source,catalogue_milk_days,catalogue_meat_days,
            batch_no,dose,dose_unit,veterinarian,notes,status,updated_at
            FROM animal_drug_treatments WHERE company_id=:cid{kosul}
            ORDER BY treated_on DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)

@router.post("/animal-drug-treatments", status_code=201)
def create_treatment(
    payload: AnimalDrugTreatmentWrite, request: Request,
    db: Session = Depends(get_db),
):
    """İlaç kaydı — TEK hayvan ya da LİSTE. LİSTE all-or-nothing.

    Katalog varsa süre katalogdan çözülür; operatör değeri kazanır ve
    köken kayıt altına alınır. Grup kaydı grup üyeliğini TEK SEFERDE
    çözüp her hayvana ayrı satır yazar: üyelik sonradan değişirse
    kilitleme etkisi doğru kalır.
    """
    cid = company_id(request)
    hedefler: list[int]
    if payload.animal_ids is not None:
        hedefler = list(dict.fromkeys(payload.animal_ids))
    else:
        hedefler = [payload.animal_id]

    hayvanlar: dict[int, dict[str, Any]] = {}
    for aid in hedefler:
        hayvanlar[aid] = _satir(db, cid, "animals", aid)

    ilk_hayvan = hayvanlar[hedefler[0]]
    (
        milk_gun, meat_gun, milk_koken, meat_koken,
        katalog_milk, katalog_meat,
    ) = _ilac_sureleri_coz(db, cid, payload, ilk_hayvan)

    now = _simdi()
    olusan: list[int] = []
    for aid in hedefler:
        yeni = db.execute(
            text(
                """INSERT INTO animal_drug_treatments(
                company_id,animal_id,catalogue_id,drug_name,treated_on,
                milk_withdrawal_days,meat_withdrawal_days,milk_withdrawal_source,
                meat_withdrawal_source,catalogue_milk_days,catalogue_meat_days,
                batch_no,dose,dose_unit,veterinarian,notes,status,
                created_at,updated_at)
                VALUES(:cid,:animal_id,:catalogue_id,:drug_name,:treated_on,
                :milk_days,:meat_days,:milk_source,:meat_source,
                :catalogue_milk,:catalogue_meat,:batch_no,:dose,:dose_unit,
                :veterinarian,:notes,'RECORDED',:now,:now) RETURNING id"""
            ),
            {
                "cid": cid, "animal_id": aid, "now": now,
                "catalogue_id": payload.catalogue_id,
                "milk_days": milk_gun, "meat_days": meat_gun,
                "milk_source": milk_koken, "meat_source": meat_koken,
                "catalogue_milk": katalog_milk, "catalogue_meat": katalog_meat,
                "batch_no": payload.batch_no, "dose": payload.dose,
                "dose_unit": payload.dose_unit,
                "veterinarian": payload.veterinarian,
                "notes": payload.notes,
                "drug_name": payload.drug_name,
                "treated_on": payload.treated_on,
            },
        ).scalar_one()
        olusan.append(int(yeni))

    db.commit()
    return [_satir(db, cid, "animal_drug_treatments", tid) for tid in olusan]

@router.get("/animal-drug-treatments/{treatment_id}")
def get_treatment(treatment_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), "animal_drug_treatments", treatment_id)

@router.put("/animal-drug-treatments/{treatment_id}")
def update_treatment(
    treatment_id: int, payload: AnimalDrugTreatmentUpdate, request: Request,
    db: Session = Depends(get_db),
):
    """İlaç kaydı güncelleme — CAS + VOIDED durumu.

    VOIDED sadece kilit hesabını değiştirir; satır kalır. Farm tarafındaki
    `safety_override_reason` gibi, kilidi geçen meşru kayıt silinmez.
    """
    cid = company_id(request)
    mevcut = _satir(db, cid, "animal_drug_treatments", treatment_id)
    hayvan = _satir(db, cid, "animals", payload.animal_id or mevcut["animal_id"])
    (
        milk_gun, meat_gun, milk_koken, meat_koken,
        katalog_milk, katalog_meat,
    ) = _ilac_sureleri_coz(db, cid, payload, hayvan)
    sonuc = db.execute(
        text(
            """UPDATE animal_drug_treatments SET animal_id=:animal_id,
            catalogue_id=:catalogue_id,drug_name=:drug_name,treated_on=:treated_on,
            milk_withdrawal_days=:milk_days,meat_withdrawal_days=:meat_days,
            milk_withdrawal_source=:milk_source,meat_withdrawal_source=:meat_source,
            catalogue_milk_days=:catalogue_milk,catalogue_meat_days=:catalogue_meat,
            batch_no=:batch_no,dose=:dose,dose_unit=:dose_unit,
            veterinarian=:veterinarian,notes=:notes,status=:status,updated_at=:now
            WHERE id=:id AND company_id=:cid AND updated_at=:expected_updated_at"""
        ),
        {
            "id": treatment_id, "cid": cid, "now": _simdi(),
            "animal_id": payload.animal_id or mevcut["animal_id"],
            "expected_updated_at": _surum_param(payload.expected_updated_at),
            "catalogue_id": payload.catalogue_id,
            "milk_days": milk_gun, "meat_days": meat_gun,
            "milk_source": milk_koken, "meat_source": meat_koken,
            "catalogue_milk": katalog_milk, "catalogue_meat": katalog_meat,
            "batch_no": payload.batch_no, "dose": payload.dose,
            "dose_unit": payload.dose_unit,
            "veterinarian": payload.veterinarian,
            "notes": payload.notes, "status": payload.status,
            "drug_name": payload.drug_name,
            "treated_on": payload.treated_on,
        },
    )
    if sonuc.rowcount != 1:
        _surum_cakismasi(db)
    db.commit()
    return _satir(db, cid, "animal_drug_treatments", treatment_id)

@router.get("/animal-drug-catalogue")
def list_drug_catalogue(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    species: str | None = None, status: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if species is not None:
        kosul += " AND species=:species"
        params["species"] = species.strip().upper()
    if status:
        kosul += " AND status=:status"
        params["status"] = status.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animal_drug_catalogue WHERE company_id=:cid{kosul}"),
        params,
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,drug_name,species,registration_no,milk_withdrawal_days,
            meat_withdrawal_days,notes,status,updated_at
            FROM animal_drug_catalogue WHERE company_id=:cid{kosul}
            ORDER BY drug_name,species LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)

@router.post("/animal-drug-catalogue", status_code=201)
def create_drug_catalogue(
    payload: AnimalDrugCatalogueWrite, request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    now = _simdi()
    try:
        yeni = db.execute(
            text(
                """INSERT INTO animal_drug_catalogue(
                company_id,drug_name,species,registration_no,milk_withdrawal_days,
                meat_withdrawal_days,notes,status,created_at,updated_at)
                VALUES(:cid,:drug_name,:species,:registration_no,:milk_days,
                :meat_days,:notes,'ACTIVE',:now,:now) RETURNING id"""
            ),
            {
                "cid": cid, "now": now,
                "milk_days": payload.milk_withdrawal_days,
                "meat_days": payload.meat_withdrawal_days,
                "drug_name": payload.drug_name,
                "species": payload.species,
                "registration_no": payload.registration_no,
                "notes": payload.notes,
            },
        ).scalar_one()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Bu ilaç ve tür için katalog kaydı zaten var"
        )
    db.commit()
    return _satir(db, cid, "animal_drug_catalogue", int(yeni))

@router.get("/animal-drug-catalogue/{catalogue_id}")
def get_drug_catalogue(catalogue_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), "animal_drug_catalogue", catalogue_id)

@router.put("/animal-drug-catalogue/{catalogue_id}")
def update_drug_catalogue(
    catalogue_id: int, payload: AnimalDrugCatalogueUpdate, request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    _satir(db, cid, "animal_drug_catalogue", catalogue_id)
    try:
        sonuc = db.execute(
            text(
                """UPDATE animal_drug_catalogue SET drug_name=:drug_name,
                species=:species,registration_no=:registration_no,
                milk_withdrawal_days=:milk_days,meat_withdrawal_days=:meat_days,
                notes=:notes,status=:status,updated_at=:now
                WHERE id=:id AND company_id=:cid AND updated_at=:expected_updated_at"""
            ),
            {
                "id": catalogue_id, "cid": cid, "now": _simdi(),
                "milk_days": payload.milk_withdrawal_days,
                "meat_days": payload.meat_withdrawal_days,
                "expected_updated_at": _surum_param(payload.expected_updated_at),
                "drug_name": payload.drug_name,
                "species": payload.species,
                "registration_no": payload.registration_no,
                "notes": payload.notes, "status": payload.status,
            },
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Bu ilaç ve tür için katalog kaydı zaten var"
        )
    if sonuc.rowcount != 1:
        _surum_cakismasi(db)
    db.commit()
    return _satir(db, cid, "animal_drug_catalogue", catalogue_id)

@router.get("/herd-fertility", response_model=HerdFertilityResponse)
def herd_fertility(request: Request, db: Session = Depends(get_db)):
    """Döl verimi göstergeleri: buzağılama aralığı, servis periyodu, ilkine yaş.

    "KAÇ DOĞUM" TEK BAŞINA GÖSTERGE DEĞİL (konu #17 araştırması): yılda 40 doğum
    40 inekten geliyorsa iyi, 20 inekten geliyorsa ineklerin yarısı boş geçmiş
    demektir. Ölçülen şey bu yüzden doğum sayısı değil, doğumlar arası SÜRE.

    ÜÇ ŞEY YANITTA AÇIKÇA DURUYOR, çünkü üçü de gizlenince ortalama yanlış
    okunur:

    * `*_sample` — her ortalamanın KAÇ ölçümden çıktığı. Tek ineğin tek aralığı
      "sürünün buzağılama aralığı" değildir; örnek sayısı olmadan ortalama
      göstermek üç ölçümü sürü gerçeği gibi sunmak olurdu.
    * `never_calved` — hiç doğurmamış dişi. Ortalamaya 0 olarak GİRMİYOR
      (girseydi ortalama düzelmesi imkânsız biçimde aşağı çekilirdi) ama kendisi
      de bir bilgi olduğu için ayrı sayılıyor.
    * `births_without_breeding_link` — tohumlamaya bağlanmamış doğum. Gebelik
      başına tohumlama YALNIZ bağlı kayıtlardan hesaplanıyor; bağ yoksa
      "doğumdan ~9 ay önceki tohumlama" diye tahmin etmek gerekirdi, ama gebelik
      süresi için güvenilir kaynak bulunamadı (konu #17: sabit yazılmayacak).
    """
    cid = company_id(request)
    bugun = business_today()

    disiler = db.execute(
        text(
            """SELECT id,ear_tag,name,species,birth_date FROM animals
            WHERE company_id=:cid AND sex='FEMALE' AND status='ACTIVE'
            ORDER BY ear_tag IS NULL,ear_tag,id"""
        ),
        {"cid": cid},
    ).mappings().all()

    # Doğumlar: ölü doğum da bir doğum OLAYIDIR ve aralığı etkiler — dışlamak
    # boş geçen bir dönemi görünmez kılardı.
    dogum_satirlari = db.execute(
        text(
            """SELECT mother_id,birth_date,breeding_id FROM animal_births
            WHERE company_id=:cid AND status='RECORDED'
            ORDER BY birth_date"""
        ),
        {"cid": cid},
    ).mappings().all()

    tohumlama_satirlari = db.execute(
        text(
            # Durum 'RECORDED' — 'ACTIVE' DEĞİL (bkz. migration 0049). Yanlış
            # sabit yazıldığında sorgu sessizce hiçbir satır döndürmüyor ve
            # göstergeler "veri yok" gibi çıkıyordu; testler bunu yakaladı.
            """SELECT id,animal_id,bred_on,result FROM animal_breedings
            WHERE company_id=:cid AND status='RECORDED'
            ORDER BY bred_on"""
        ),
        {"cid": cid},
    ).mappings().all()

    tohum_gunu = {
        int(t["id"]): _gun(t["bred_on"]) for t in tohumlama_satirlari
    }
    toplam_tohumlama: dict[int, int] = {}
    gebe_sonuclanan: dict[int, int] = {}
    for t in tohumlama_satirlari:
        aid = int(t["animal_id"])
        toplam_tohumlama[aid] = toplam_tohumlama.get(aid, 0) + 1
        if t["result"] == "PREGNANT":
            gebe_sonuclanan[aid] = gebe_sonuclanan.get(aid, 0) + 1

    dogumlar: dict[int, list[date]] = {}
    bagli: dict[int, list[tuple[date, date]]] = {}
    bagsiz = 0
    for d in dogum_satirlari:
        aid = int(d["mother_id"])
        gun = _gun(d["birth_date"])
        if gun is None:
            continue
        dogumlar.setdefault(aid, []).append(gun)
        kimlik = d.get("breeding_id")
        bred = tohum_gunu.get(int(kimlik)) if kimlik else None
        if bred is None:
            bagsiz += 1
        else:
            bagli.setdefault(aid, []).append((bred, gun))

    satirlar: list[dict[str, Any]] = []
    kayitlar: list[HayvanDolVerimi] = []
    hic_dogurmamis = 0

    for hayvan in disiler:
        aid = int(hayvan["id"])
        kendi_dogumlari = dogumlar.get(aid, [])
        if not kendi_dogumlari:
            hic_dogurmamis += 1
            continue
        kayit = hayvan_dol_verimi(
            animal_id=aid,
            birth_date=_gun(hayvan.get("birth_date")),
            dogum_tarihleri=kendi_dogumlari,
            bagli_tohumlamalar=bagli.get(aid, []),
            gebe_sonuclanan=gebe_sonuclanan.get(aid, 0),
            toplam_tohumlama=toplam_tohumlama.get(aid, 0),
        )
        kayitlar.append(kayit)
        satirlar.append({
            "animal_id": aid,
            "ear_tag": hayvan.get("ear_tag"),
            "name": hayvan.get("name"),
            "species": hayvan["species"],
            "calving_count": kayit.calving_count,
            "avg_calving_interval_days": kayit.avg_calving_interval_days,
            "last_calving_interval_days": kayit.last_calving_interval_days,
            "age_at_first_calving_days": kayit.age_at_first_calving_days,
            "service_period_days": kayit.service_period_days,
            "services_per_conception": kayit.services_per_conception,
            "interval_is_problem": kayit.interval_is_problem,
            "last_calving_on": max(kendi_dogumlari).isoformat(),
        })

    ozet = suru_ozeti(
        kayitlar, never_calved=hic_dogurmamis, births_without_breeding_link=bagsiz,
    )

    # Sorunlu aralık önce, sonra en uzun aralık: müdahale edilecek hayvan üstte.
    satirlar.sort(key=lambda r: (
        0 if r["interval_is_problem"] else 1,
        -(r["last_calving_interval_days"] or 0),
    ))

    return {
        "as_of": bugun.isoformat(),
        "thresholds": {
            # Eşikler KAYNAKTAN geliyor ve yanıtta duruyor: kullanıcı "neye göre
            # sorun" sorusunu ekranda görebilmeli.
            "target_calving_interval_days": HEDEF_ARALIK_GUN,
            "problem_calving_interval_days": SORUN_ARALIK_GUN,
            "service_period_low_days": SERVIS_ALT_GUN,
            "service_period_high_days": SERVIS_UST_GUN,
        },
        "summary": {
            "calving_interval_sample": ozet.calving_interval_sample,
            "avg_calving_interval_days": ozet.avg_calving_interval_days,
            "problem_interval_count": ozet.problem_interval_count,
            "age_at_first_calving_sample": ozet.age_at_first_calving_sample,
            "avg_age_at_first_calving_days": ozet.avg_age_at_first_calving_days,
            "service_period_sample": ozet.service_period_sample,
            "avg_service_period_days": ozet.avg_service_period_days,
            "never_calved": ozet.never_calved,
            "births_without_breeding_link": ozet.births_without_breeding_link,
        },
        "items": satirlar,
    }


# -------------------------------------------------------------------- pano ---

@router.get("/herd-dashboard")
def herd_dashboard(request: Request, db: Session = Depends(get_db)):
    """Sürü panosu: kaç hayvan, kaç doğum, aşısı geciken kaç hayvan.

    FAZ 2'DE NE HESAPLANMIYOR: buzağılama aralığı ve döl verimi göstergeleri
    (FAZ 5). "Aşısı gecikenler" burada YALNIZ kaydedilmiş `next_due_on`
    tarihine bakıyor — yaşa bağlı zorunlu aşı takvimi FAZ 4'te gelecek.
    Bu ayrım önemli: bugünkü sayı "planlanmış ama yapılmamış" demek, "zorunlu
    aşısı eksik" DEMEK DEĞİL.
    """
    cid = company_id(request)
    bugun = business_today()

    tur_sayilari = db.execute(
        text(
            """SELECT species,sex,COUNT(*) adet FROM animals
            WHERE company_id=:cid AND status='ACTIVE'
            GROUP BY species,sex"""
        ),
        {"cid": cid},
    ).mappings().all()

    # Bireysel kayıt tutulmayan sürülerin baş sayısı AYRI toplanıyor: aynı
    # hayvanı iki kez saymamak için sürüde bireysel kayıt varsa head_count
    # zaten girilemiyor (bkz. _grup_sayim_dogrula).
    grup_toplam = db.execute(
        text(
            """SELECT COALESCE(SUM(head_count),0) FROM animal_groups
            WHERE company_id=:cid AND status='ACTIVE' AND head_count IS NOT NULL"""
        ),
        {"cid": cid},
    ).scalar()

    geciken = db.execute(
        text(
            """SELECT COUNT(DISTINCT v.animal_id) FROM animal_vaccinations v
            JOIN animals a ON a.id=v.animal_id AND a.company_id=v.company_id
            WHERE v.company_id=:cid AND a.status='ACTIVE'
              AND v.next_due_on IS NOT NULL AND v.next_due_on < :bugun"""
        ),
        {"cid": cid, "bugun": bugun},
    ).scalar()

    # Doğumlar: son 12 ay. Sayı ve canlı yavru AYRI — ölü doğum da bir doğum
    # olayıdır ve gizlenmemeli.
    bir_yil_once = date(bugun.year - 1, bugun.month, min(bugun.day, 28))
    dogumlar = db.execute(
        text(
            """SELECT COUNT(*) olay,
            COALESCE(SUM(CASE WHEN outcome='LIVE' THEN offspring_count ELSE 0 END),0) canli,
            COALESCE(SUM(CASE WHEN outcome<>'LIVE' THEN 1 ELSE 0 END),0) olu
            FROM animal_births
            WHERE company_id=:cid AND status='RECORDED' AND birth_date >= :baslangic"""
        ),
        {"cid": cid, "baslangic": bir_yil_once},
    ).mappings().first()

    gebe = db.execute(
        text(
            """SELECT COUNT(DISTINCT b.animal_id) FROM animal_breedings b
            JOIN animals a ON a.id=b.animal_id AND a.company_id=b.company_id
            WHERE b.company_id=:cid AND a.status='ACTIVE' AND b.result='PREGNANT'"""
        ),
        {"cid": cid},
    ).scalar()

    tur_ozet: dict[str, dict[str, int]] = {}
    toplam = 0
    for r in tur_sayilari:
        adet = int(r["adet"] or 0)
        toplam += adet
        kayit = tur_ozet.setdefault(r["species"], {"total": 0, "female": 0, "male": 0})
        kayit["total"] += adet
        kayit["female" if r["sex"] == "FEMALE" else "male"] += adet

    return {
        "as_of": bugun.isoformat(),
        "summary": {
            "individual_active": toplam,
            "group_head_count": int(grup_toplam or 0),
            "pregnant": int(gebe or 0),
            "vaccination_overdue": int(geciken or 0),
        },
        "by_species": tur_ozet,
        "births_last_12_months": {
            "events": int(dogumlar["olay"] or 0),
            "live_offspring": int(dogumlar["canli"] or 0),
            "non_live_events": int(dogumlar["olu"] or 0),
        },
    }

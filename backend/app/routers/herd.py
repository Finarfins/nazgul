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

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..activity_log import log_request_activity
from ..business_time import business_today
from ..db import get_db
from ..herd_schemas import (
    MOVEMENT_TO_STATUS,
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
    QuarantineClose,
    QuarantineWrite,
    TreatmentWrite,
    VaccinationWrite,
    VetDrugUpdate,
    VetDrugWrite,
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
from ..tenancy import company_id

router = APIRouter(tags=["herd"])

_SAYFA = Query(default=50, ge=1, le=200)
_ATLA = Query(default=0, ge=0, le=100_000)

_TABLOLAR = frozenset({
    "animals", "animal_groups", "animal_vaccinations", "animal_breedings",
    "animal_births", "animal_weights", "milk_yields", "animal_movements",
    # Arınma (bekleme) süreleri — göç 20260908_0074. Küme İSTEKTEN gelen bir
    # değeri asla kabul etmez; `_satir` yalnız bu sabitten okur.
    "vet_drugs", "animal_treatments", "animal_treatment_items",
    # Karantina defteri — göç 20260909_0075. Aynı gerekçe: küme İSTEKTEN
    # gelen bir değeri asla kabul etmez.
    "animal_quarantines",
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
    # ARINMA KİLİDİ (göç 0074). HER SQL'DEN ÖNCE: `block` politikasında satır
    # HİÇ yazılmamalı, `require_reason`da gerekçesiz istek yazılmadan düşmeli.
    uyari = _sut_guvenlik_dogrula(db, cid, payload)
    # KARANTİNA KİLİDİ (göç 0075). ARINMADAN SONRA ve YİNE HER SQL'DEN ÖNCE.
    # İKİSİ AYRI ÇAĞRIDIR ve tek bir "hayvan uygun mu" fonksiyonuna
    # birleştirilmedi: birleştirilseydi ilk `block` ikincisini HİÇ
    # çalıştırmazdı ve kullanıcı iki engeli TEK TEK öğrenmek zorunda kalırdı.
    # Bu sırada da öyle olur, ama sıra AÇIKÇA yazılıdır: arınma daha sık ve
    # daha dar bir kısıttır, önce o konuşur.
    karantina_uyari = _sut_karantina_dogrula(db, cid, payload)
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO milk_yields(company_id,animal_id,group_id,milked_on,session,
            quantity_liters,notes,withdrawal_warning,withdrawal_override_reason,
            quarantine_warning,quarantine_override_reason,
            created_at,updated_at)
            VALUES(:cid,:animal_id,:group_id,:milked_on,:session,:quantity_liters,
            :notes,:withdrawal_warning,:withdrawal_override_reason,
            :quarantine_warning,:quarantine_override_reason,:now,:now)
            RETURNING id"""
        ),
        {"cid": cid, "now": now, "withdrawal_warning": uyari,
         "quarantine_warning": karantina_uyari,
         **payload.model_dump()},
    ).scalar_one()
    # Kayıt aynı işlemde: `log_request_activity` commit ETMEZ, yani uyarı
    # yazılıp aktivite yazılmadan biten bir yol YOK.
    _arinma_gecisini_kaydet(
        db, request, cid, "milk_yield", int(yeni), uyari,
        payload.withdrawal_override_reason,
    )
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
    # ET ARINMA KİLİDİ (göç 0074). HER SQL'DEN ÖNCE, süt yolundaki gerekçenin
    # aynısı.
    uyari = _et_guvenlik_dogrula(db, cid, payload)
    # KARANTİNA KİLİDİ (göç 0075). Küme arınmanınkinden GENİŞTİR: TRANSFER_OUT
    # burada da kesiyor — gerekçe `_KARANTINA_KILITLI_HAREKETLER`de.
    karantina_uyari = _hareket_karantina_dogrula(db, cid, payload)
    now = _simdi()
    hareket_id = db.execute(
        text(
            """INSERT INTO animal_movements(company_id,animal_id,kind,moved_on,amount,
            counterparty,reason,notes,withdrawal_warning,withdrawal_override_reason,
            quarantine_warning,quarantine_override_reason,
            created_at,updated_at)
            VALUES(:cid,:animal_id,:kind,:moved_on,:amount,:counterparty,:reason,
            :notes,:withdrawal_warning,:withdrawal_override_reason,
            :quarantine_warning,:quarantine_override_reason,:now,:now)
            RETURNING id"""
        ),
        {"cid": cid, "now": now, "withdrawal_warning": uyari,
         "quarantine_warning": karantina_uyari,
         **payload.model_dump()},
    ).scalar_one()
    _arinma_gecisini_kaydet(
        db, request, cid, "animal_movement", int(hareket_id), uyari,
        payload.withdrawal_override_reason,
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
        # Uyarı YANITTA da dönüyor: `warn` politikasında istek 201 alır ve
        # kullanıcı hiçbir şey görmezse uyarı kayda girer ama KİMSEYE
        # ULAŞMAZ. Hareket ucu satırı dönmüyor (durum + tür dönüyor), bu
        # yüzden alan AÇIKÇA ekleniyor.
        "withdrawal_warning": uyari,
        # AYNI GEREKÇE, AYRI ALAN: iki kilit iki farklı olgudur ve bir hareket
        # ikisini birden ihlal edebilir. Tek alanda birleştirmek, ikincisinin
        # birinciyi EZMESİ demekti.
        "quarantine_warning": karantina_uyari,
    }


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


# ---------------------------------------------------------------------------
# VETERİNER İLAÇ KATALOĞU VE ARINMA (BEKLEME) KİLİTLERİ — göç 20260908_0074
# ---------------------------------------------------------------------------
#
# ÖLÇÜLEN KUSUR: bu modülde arınma süresi kavramı HİÇ YOKTU. `animal_
# vaccinations` bir AŞI defteridir ve aşının kalıntı süresi yoktur; ilaç
# tedavisinin tutulacağı bir yer, dolayısıyla süt/et için bir kilit de yoktu.
# Antibiyotik uygulanmış bir hayvanın sütü sistem HİÇBİR ŞEY BİLMEDEN tanka
# yazılabiliyordu.
#
# ŞEKİL TARLA MODÜLÜNDEN DEVRALINDI ve BİLEREK aynı: katalog önerir, operatör
# karar verir, köken kayıt altındadır (0063), sistemin bulduğu ile kullanıcının
# söylediği AYRI sütunda durur (0048), politika ÜÇ seviyelidir ve "allow"
# YOKTUR (0064/0072). İkinci bir şekil uydurmak, aynı olguyu iki dilde okutmak
# olurdu.

#: 0063/0072 ile BİREBİR AYNI sözlük. Hayvancılığa ÖZEL bir köken kümesi
#: AÇILMADI: `preharvest_source`/`reentry_source` ile aynı üç değer.
_ARINMA_KOKEN_KATALOG = "CATALOGUE"
_ARINMA_KOKEN_OPERATOR = "OPERATOR"
_ARINMA_KOKEN_USTUNE_YAZMA = "OPERATOR_OVERRIDE"

_ARINMA_SEBEP = "ARINMA_SURESI_DOLMADI"

#: Okunamayan ayarda SIKI tarafa düş (`farm.py` `_firma_kurallari`nın gerekçesi:
#: bir veritabanı sorununu sessiz kural gevşetmesine çevirmemek).
_VARSAYILAN_ARINMA_POLITIKASI = "require_reason"

#: ET arınması YALNIZ bu iki harekette ısırıyor ve dışarıda kalanların
#: gerekçesi ÖLÇÜLMÜŞ bir ayrımdır, unutkanlık değil:
#:
#:   * SALE / SLAUGHTER — hayvan İNSAN GIDA ZİNCİRİNE giriyor. Kilit tam
#:     olarak burayı korur.
#:   * DEATH — hayvan ölmüştür. Kilidi buraya koymak ölümü BİLDİRMEYİ
#:     zorlaştırırdı; oysa ölüm kaydı denetimin en çok ihtiyaç duyduğu
#:     kayıttır ve onu caydırmak arınma süresini korumaz, defteri bozar.
#:   * TRANSFER_OUT — hayvan başka bir işletmeye gidiyor, kesime değil.
#:     Arınma süresi hayvanla BİRLİKTE taşınır ve kesim kararını alacak olan
#:     karşı taraftır. Buraya kilit koymak aynı süreyi iki kez sorardı ve
#:     ikincisinde (gerçek kesimde) hiçbir şey bilmezdik.
#:   * PURCHASE / TRANSFER_IN — hayvan GELİYOR; zaten kesilmiyor.
_ET_KILITLI_HAREKETLER = frozenset({"SALE", "SLAUGHTER"})

#: Tedavi satırındaki iki ETKİN değer sütunu. İki alan BAĞIMSIZ hesaplanıyor;
#: gerekçe `_arinma_coz`da.
_SUT_ALANI = "milk_withdrawal_days"
_ET_ALANI = "meat_withdrawal_days"


def _arinma_politikasi(db: Session, cid: int) -> str:
    """Firmanın arınma kilidi ayarı; satır okunamazsa VARSAYILAN (sıkı).

    `farm.py`nin `_firma_kurallari`sıyla aynı fail-closed duruşu: okunamayan
    bir ayarda gevşek tarafa düşmek, bir veritabanı sorununu sessizce kural
    gevşetmesine çevirirdi ve burada gevşeyen şey İNSAN GIDASIDIR.
    """
    row = db.execute(
        text("SELECT herd_withdrawal_policy FROM companies WHERE id=:cid"),
        {"cid": cid},
    ).mappings().first()
    if not row:
        return _VARSAYILAN_ARINMA_POLITIKASI
    return row["herd_withdrawal_policy"] or _VARSAYILAN_ARINMA_POLITIKASI


def _urun_dogrula(db: Session, cid: int, product_id: int) -> None:
    """Ürün AYNI firmaya ait olmalı.

    Veritabanındaki bileşik yabancı anahtar da bunu zorluyor; buradaki kontrol
    kullanıcıya 404 veriyor, 500 değil (0063'ün `create_ppp`indeki gerekçe).
    """
    var = db.execute(
        text("SELECT 1 FROM products WHERE id=:id AND company_id=:cid"),
        {"id": int(product_id), "cid": cid},
    ).scalar()
    if not var:
        raise HTTPException(404, "Ürün bulunamadı")


def _katalog_arinma(
    db: Session, cid: int, product_id: int, tur: str,
) -> tuple[int | None, int | None]:
    """Ürün için katalogdaki (süt, et) arınma günleri; türe ÖZEL satır önce.

    Türden bağımsız satır (``species=''``) yedek: firma tek satırla başlayıp
    gerektiğinde türe özelleştirebilsin diye (0063'ün `crop` deseni).

    KARŞILAŞTIRMA TAM EŞİTLİKTİR ve 0063'ün Türkçe katlaması BURADA YOK.
    Gerekçe ölçülmüş bir ŞEMA farkıdır: `crop_seasons.crop` SERBEST METİNDİR,
    `animals.species` ise KAPALI bir kümedir (`ck_animals_species`) ve katalog
    sütunu da aynı kümeye kısıtlıdır (`ck_vet_drugs_species`, göç 0074). İki
    taraf da aynı kapalı kümeden geldiği için katlanacak bir şey yok; katlama
    eklemek, olmayan bir sorunu çözerken ``I``/``ı`` gibi gerçek bir kayıp
    riski getirirdi.
    """
    satirlar = db.execute(
        text(
            """SELECT species,milk_withdrawal_days,meat_withdrawal_days
            FROM vet_drugs
            WHERE company_id=:cid AND product_id=:pid AND status='ACTIVE'"""
        ),
        {"cid": cid, "pid": int(product_id)},
    ).mappings().all()

    genel: tuple[int | None, int | None] = (None, None)
    for r in satirlar:
        katalog_tur = (r["species"] or "").strip()
        cift = (int(r["milk_withdrawal_days"]), int(r["meat_withdrawal_days"]))
        if katalog_tur and katalog_tur == tur:
            # Türe ÖZEL satır bulundu; yedeğe bakmaya gerek yok.
            return cift
        if not katalog_tur:
            genel = cift
    return genel


def _tedavi_turu(db: Session, cid: int, payload: TreatmentWrite) -> str:
    """Tedavinin TÜRÜ: hayvanınki, ya da sürününki.

    Sürüde tür BOŞ ya da ``MIXED`` olabilir (0049 karışık sürüye izin veriyor).
    İkisi de katalogdaki hiçbir tür koduna eşleşmez ve o durumda çözüm türden
    BAĞIMSIZ satıra (``species=''``) düşer — bu bir kayıp değil, kataloğun
    zaten tarif ettiği yedek yol.

    Bu çağrı AYNI ZAMANDA kiracı kapısıdır: `_satir` başka firmanın hayvanını
    ya da sürüsünü 404 ile reddeder.
    """
    if payload.animal_id is not None:
        hayvan = _satir(db, cid, "animals", payload.animal_id)
        return str(hayvan.get("species") or "")
    grup = _satir(db, cid, "animal_groups", payload.group_id)
    return str(grup.get("species") or "")


def _arinma_coz(
    db: Session, cid: int, payload: TreatmentWrite, tur: str,
) -> tuple[int | None, int | None, int | None, int | None, str | None]:
    """``(katalog_süt, katalog_et, etkin_süt, etkin_et, köken)``.

    İKİ KURAL, İKİSİ DE 0063'ten DEVRALINDI:

    * **EN UZUN KAZANIR.** Birden çok ilaç uygulandıysa her alan için en uzun
      süre alınır. İKİ ALAN BAĞIMSIZ hesaplanıyor ve bu bilinçli: bir ilacın
      süt arınması, başka bir ilacın et arınması uzun olabilir ve "en uzun
      süreli ilacın çiftini al" demek, öteki ilacın uzun olan alanını
      SESSİZCE kısaltırdı.
    * **KATALOG ÖNERİR, OPERATÖR KARAR VERİR.** Operatörün girdiği değer
      kazanır, ama üstüne yazma SESSİZ OLAMAZ: köken ``OPERATOR_OVERRIDE``
      olur ve katalogun dediği ayrı sütunda durur.

    KÖKEN TEK SÜTUNDUR ama alan İKİ TANEDİR; çift için TEK bir köken şöyle
    türetiliyor ve `_phi_coz`un tek alanlı hâlinin BİREBİR genellemesidir:
    operatör hiç konuşmadıysa ``CATALOGUE``; konuştuğu her alanda katalogla
    aynı şeyi söylüyorsa (ya da katalog o alanda susuyorsa) ``OPERATOR``; en az
    bir alanda katalogla ÇELİŞİYORSA ``OPERATOR_OVERRIDE``. Her uyuşmazlığı
    üstüne yazma saymak, denetimde GERÇEK üstüne yazmaları görünmez kılardı.
    """
    kat_sut: int | None = None
    kat_et: int | None = None
    for kalem in payload.items or []:
        if kalem.product_id is None:
            # Serbest metin ilaç — prospektüsü depoda yok, çözülmez.
            continue
        sut, et = _katalog_arinma(db, cid, kalem.product_id, tur)
        if sut is not None and (kat_sut is None or sut > kat_sut):
            kat_sut = sut
        if et is not None and (kat_et is None or et > kat_et):
            kat_et = et

    op_sut = payload.milk_withdrawal_days
    op_et = payload.meat_withdrawal_days

    etkin_sut = op_sut if op_sut is not None else kat_sut
    etkin_et = op_et if op_et is not None else kat_et

    if op_sut is None and op_et is None:
        if kat_sut is None and kat_et is None:
            # Ne operatör ne katalog: süre BOŞ kalır ve boş ihlal DEĞİLDİR.
            return None, None, None, None, None
        return kat_sut, kat_et, etkin_sut, etkin_et, _ARINMA_KOKEN_KATALOG

    celiski = (op_sut is not None and kat_sut is not None and op_sut != kat_sut) or (
        op_et is not None and kat_et is not None and op_et != kat_et
    )
    koken = _ARINMA_KOKEN_USTUNE_YAZMA if celiski else _ARINMA_KOKEN_OPERATOR
    return kat_sut, kat_et, etkin_sut, etkin_et, koken


# HAYVAN YOLU. Hayvanın KENDİ tedavileri ve İÇİNDE BULUNDUĞU SÜRÜYE yazılmış
# tedaviler BİRLİKTE ısırıyor: sürünün tamamı ilaçlandığında o sürüdeki her
# hayvanın sütü de etkilenir ve yalnız bireysel kayda bakmak, küçükbaş
# işletmesinde kilidi tamamen işlevsiz bırakırdı.
#
# ÜYELİK GÜNCELDİR, GEÇMİŞ DEĞİL — ve bu ÖLÇÜLMÜŞ bir sınırdır, tercih değil:
# `animals.group_id` TEK bir sütundur (0049) ve depoda üyelik GEÇMİŞİ tutan
# hiçbir tablo YOKTUR. Yani tedaviden sonra sürü değiştiren bir hayvan YENİ
# sürüsünün kurallarıyla değerlendirilir. Bunu düzeltmek bir üyelik defteri
# açmak demektir ve o KENDİ göçünü ister; burada iddia EDİLMİYOR.
#
# Hayvanın sürüsü YOKSA (`group_id` NULL) alt sorgu NULL döner ve
# `t.group_id=NULL` karşılaştırması NULL'dur, yani hiçbir satırı seçmez —
# istenen davranış budur ve iki diyalektte de aynıdır.
_ARINMA_HAYVAN_SORGU = text(
    """SELECT t.id,t.treated_on,t.milk_withdrawal_days,t.meat_withdrawal_days,
    t.animal_id,t.group_id
    FROM animal_treatments t
    WHERE t.company_id=:cid
      AND (t.animal_id=:aid
           OR (t.group_id IS NOT NULL AND t.group_id=(
                 SELECT h.group_id FROM animals h
                 WHERE h.company_id=:cid AND h.id=:aid)))"""
)

# SÜRÜ YOLU. Sürüye yazılmış tedaviler VE sürüdeki herhangi bir hayvanın
# bireysel tedavisi BİRLİKTE ısırıyor: grup sağımı o hayvanın sütünü de
# içerir, yani tek bir tedavi edilmiş inek tankın tamamını kirletir.
_ARINMA_GRUP_SORGU = text(
    """SELECT t.id,t.treated_on,t.milk_withdrawal_days,t.meat_withdrawal_days,
    t.animal_id,t.group_id
    FROM animal_treatments t
    WHERE t.company_id=:cid
      AND (t.group_id=:gid
           OR (t.animal_id IS NOT NULL AND t.animal_id IN (
                 SELECT h.id FROM animals h
                 WHERE h.company_id=:cid AND h.group_id=:gid)))"""
)


def _arinma_ihlalleri(
    db: Session, cid: int, animal_id: int | None, group_id: int | None,
    hedef_gun: date, alan: str,
) -> list[dict[str, Any]]:
    """``hedef_gun``de bu hayvan/sürü için ihlal edilecek arınma süreleri.

    Süresi GİRİLMEMİŞ tedavi ihlal SAYILMIYOR — `_bekleme_ihlalleri`nin
    0044'ten beri geçerli kuralı: bilinmeyeni ihlal saymak kullanıcıyı gerekçe
    yazmaya alıştırır ve o da GERÇEK uyarıyı değersizleştirir.

    SINIR GÜNÜ SERBEST: karşılaştırma ``<`` iledir, yani arınmanın dolduğu
    günün KENDİSİ ihlal değildir. ``treated_on`` bir DATE'tir ve saat taşımaz;
    tarla tarafındaki `_yerel_gun` çevrimi (``performed_at`` bir ZAMAN
    DAMGASIDIR ve UTC gününe göre hesaplamak süreyi bir gün kaydırırdı) burada
    GEREKMİYOR — `_gun` yalnız sürücü farkını (SQLite metin, PostgreSQL
    ``date``) düzeltiyor.
    """
    if animal_id is not None:
        satirlar = db.execute(
            _ARINMA_HAYVAN_SORGU, {"cid": cid, "aid": int(animal_id)}
        ).mappings().all()
    else:
        satirlar = db.execute(
            _ARINMA_GRUP_SORGU, {"cid": cid, "gid": int(group_id)}
        ).mappings().all()

    ihlaller: list[dict[str, Any]] = []
    for r in satirlar:
        gun = r[alan]
        if gun is None:
            continue
        uygulama = _gun(r["treated_on"])
        guvenli = uygulama + timedelta(days=int(gun))
        if hedef_gun < guvenli:
            ihlaller.append({
                "treatment_id": int(r["id"]),
                "treated_on": uygulama.isoformat(),
                "withdrawal_days": int(gun),
                "earliest_allowed": guvenli.isoformat(),
                # Kaydı HANGİ yolun kestiği: bireysel tedavi mi, sürü tedavisi
                # mi. Kullanıcı "bu hayvana ne verdim" diye bakıp hiçbir şey
                # bulamasın diye.
                "scope": "ANIMAL" if r["animal_id"] is not None else "GROUP",
            })
    # En geç biten kısıt en üstte: kullanıcının beklemesi gereken tarih odur.
    ihlaller.sort(key=lambda x: x["earliest_allowed"], reverse=True)
    return ihlaller


def _arinma_ihlal_metni(ihlaller: list[dict[str, Any]]) -> str:
    ilk = ihlaller[0]
    return (
        f"{ilk['treated_on']} tarihli tedavi {ilk['withdrawal_days']} gün arınma "
        f"gerektiriyor, en erken {ilk['earliest_allowed']}"
    )


def _kilit_karari(
    ihlaller: list[dict[str, Any]], politika: str, gerekce: str | None,
    uyari: str, basli: str, sebep: str, engel_kuyrugu: str,
) -> str | None:
    """ÜÇ POLİTİKANIN TEK GÖVDESİ: block RED, warn KABUL+uyarı,
    require_reason gerekçe İSTER.

    BU FONKSİYON İKİ KİLİDİN (arınma — göç 0074, karantina — göç 0075) ORTAK
    KARARIDIR ve TEK OLMASI BİLİNÇLİDİR. İkinci bir kopya çıkarılsaydı,
    `block` dalının bir gün birinde düzeltilip ötekinde unutulması SESSİZ bir
    güvenlik farkı üretirdi — ve burada gevşeyen şey İNSAN GIDASIDIR.

    Şekil `_plantback_dogrula` ile birebir ve ret gövdesi SÖZLÜKTÜR: çağıranın
    yapacağı şey HANGİ kaydın ve HANGİ tarihin kestiğine göre değişir, düz bir
    metin bunu ayrıştırılabilir biçimde SÖYLEMEZ.

    ``uyari`` KAYDA yazılan çıplak metindir; ``basli`` kullanıcıya dönen
    mesajın ön ekidir (hangi olgu, hangi ürün). İkisinin AYRI olmasının
    gerekçesi 0048'in ayrımıdır: sütunda duran şey olgunun kendisi,
    kullanıcıya dönen şey o olgunun cümlesidir.
    """
    if not ihlaller:
        return None

    if politika == "block":
        raise HTTPException(
            422,
            {"sebep": sebep, "message": f"{basli}. {engel_kuyrugu}",
             "blocking": ihlaller},
        )

    # `warn`: istek KABUL EDİLİYOR ama sistemin bulduğu kayda yazılıyor.
    if politika == "warn":
        return uyari

    # `require_reason`: gerekçesiz geçmez.
    if gerekce:
        return uyari
    raise HTTPException(
        422,
        {"sebep": sebep,
         "message": f"{basli}. Yine de kaydetmek için gerekçe girin.",
         "blocking": ihlaller},
    )


def _arinma_dogrula(
    ihlaller: list[dict[str, Any]], politika: str, gerekce: str | None, ne: str,
) -> str | None:
    """Arınma kilidinin kararı; politika dalları `_kilit_karari`ndadır."""
    if not ihlaller:
        return None
    metin = _arinma_ihlal_metni(ihlaller)
    return _kilit_karari(
        ihlaller, politika, gerekce, metin,
        f"{ne} arınma süresi dolmadı: {metin}", _ARINMA_SEBEP,
        "Firma ayarı arınma dolmadan kaydetmeye izin vermiyor.",
    )


def _sut_guvenlik_dogrula(
    db: Session, cid: int, payload: MilkYieldWrite,
) -> str | None:
    ihlaller = _arinma_ihlalleri(
        db, cid, payload.animal_id, payload.group_id, payload.milked_on, _SUT_ALANI,
    )
    return _arinma_dogrula(
        ihlaller, _arinma_politikasi(db, cid),
        payload.withdrawal_override_reason, "Süt",
    )


def _et_guvenlik_dogrula(
    db: Session, cid: int, payload: MovementWrite,
) -> str | None:
    """ET kilidi YALNIZ satış ve kesimde; gerekçesi `_ET_KILITLI_HAREKETLER`de."""
    if payload.kind not in _ET_KILITLI_HAREKETLER:
        return None
    ihlaller = _arinma_ihlalleri(
        db, cid, payload.animal_id, None, payload.moved_on, _ET_ALANI,
    )
    return _arinma_dogrula(
        ihlaller, _arinma_politikasi(db, cid),
        payload.withdrawal_override_reason, "Et",
    )


def _arinma_gecisini_kaydet(
    db: Session, request: Request, cid: int, kaynak: str, kayit_id: int,
    uyari: str | None, gerekce: str | None,
) -> None:
    """Kilidin GEREKÇEYLE geçilmesi aktivite kaydına yazılır.

    YALNIZ ikisi de doluyken: uyarı yoksa geçilecek bir şey yoktu, gerekçe
    yoksa (``warn`` politikası) karar kullanıcının DEĞİL firmanın ayarınındır
    ve o ayar zaten `company-settings` üzerinden denetlenebilir.

    Kaydın var olma sebebi `activity_log.ACTION_TYPES` başlığında ÖLÇÜLDÜ:
    `milk_yields` ve `animal_movements` KULLANICI SÜTUNU TAŞIMIYOR (0049),
    yani gerekçeyi kimin yazdığı başka hiçbir yerden çıkarılamaz.
    """
    if not uyari or not gerekce:
        return
    log_request_activity(
        db, request, cid, "herd_withdrawal.overridden", "herd_withdrawal",
        kayit_id, f"Arınma uyarısı gerekçeyle geçildi: {uyari}",
        {"kaynak": kaynak, "uyari": uyari, "gerekce": gerekce},
    )


# ---------------------------------------------------------------------------
# KARANTİNA KİLİTLERİ — göç 20260909_0075
# ---------------------------------------------------------------------------
#
# ARINMADAN (0074) FARKI TEK CÜMLEDE: arınma HESAPLANIR, karantina KARARDIR.
# Arınma bitişi `treated_on + milk_withdrawal_days` aritmetiğidir; karantina
# bitişi `ended_on` sütununda DURUR ve NULL "hâlâ açık" demektir.
#
# Politika dalları AYRI YAZILMADI: iki kilit de `_kilit_karari`ye gidiyor
# (gerekçe orada).

_KARANTINA_SEBEP = "KARANTINA_ACIK"

#: Okunamayan ayarda SIKI tarafa düş — `_arinma_politikasi`nın gerekçesiyle
#: aynı. VARSAYILAN `block`tur ve arınmanınkinden (`require_reason`) FARKLI
#: olmasının gerekçesi göç 0075'in başlığındadır: karantinayı bir insan
#: ELLE açmıştır ve hâlâ AÇIK bırakmıştır; doğru yol onu KAPATMAKTIR.
_VARSAYILAN_KARANTINA_POLITIKASI = "block"

#: KARANTİNA KİLİDİ, ARINMANINKİNDEN DAHA GENİŞTİR ve fark TRANSFER_OUT'tur.
#: Ayrım ÖLÇÜLDÜ, unutulmadı:
#:
#:   * SALE / SLAUGHTER — arınmadaki gerekçenin aynısı: hayvan insan gıda
#:     zincirine giriyor.
#:   * TRANSFER_OUT — ARINMADA SERBESTTİ, BURADA KİLİTLİ. Arınma süresi
#:     hayvanla BİRLİKTE taşınır ve kesim kararını karşı taraf alır; karantina
#:     ise taşınmaz — karantinanın VAR OLMA SEBEBİ hayvanın işletmeden
#:     ÇIKMAMASIDIR. Nakli serbest bırakmak, kilidi tam da engellemek için
#:     kurulduğu yoldan boşaltırdı.
#:   * DEATH — hayvan ölmüştür. 0074'ün gerekçesi burada DAHA GÜÇLÜ: karantina
#:     çoğu zaman bir HASTALIK şüphesidir ve o hayvanın ölümü denetimin en çok
#:     ihtiyaç duyduğu kayıttır. Ölümü bildirmeyi zorlaştırmak salgını
#:     durdurmaz, defteri bozar.
#:   * PURCHASE / TRANSFER_IN — hayvan GELİYOR; karantina zaten girişte konur.
_KARANTINA_KILITLI_HAREKETLER = frozenset({"SALE", "SLAUGHTER", "TRANSFER_OUT"})


def _karantina_politikasi(db: Session, cid: int) -> str:
    """Firmanın karantina kilidi ayarı; satır okunamazsa VARSAYILAN (sıkı)."""
    row = db.execute(
        text("SELECT herd_quarantine_policy FROM companies WHERE id=:cid"),
        {"cid": cid},
    ).mappings().first()
    if not row:
        return _VARSAYILAN_KARANTINA_POLITIKASI
    return row["herd_quarantine_policy"] or _VARSAYILAN_KARANTINA_POLITIKASI


# HAYVAN YOLU. Hayvanın KENDİ karantinası VE İÇİNDE BULUNDUĞU SÜRÜNÜN
# karantinası birlikte ısırıyor: sürü karantinaya alındığında içindeki her
# hayvan karantinadadır ve yalnız bireysel kayda bakmak, sürü karantinasını
# tamamen işlevsiz bırakırdı.
#
# ÜYELİK GÜNCELDİR, GEÇMİŞ DEĞİL — 0074'ün `_ARINMA_HAYVAN_SORGU`sunda ölçülen
# sınırın AYNISI: `animals.group_id` TEK bir sütundur (0049) ve depoda üyelik
# GEÇMİŞİ tutan hiçbir tablo YOKTUR.
#
# TARİH SÜZGECİ SQL'DE DEĞİL PYTHON'DA: `started_on`/`ended_on` sürücüye göre
# metin (SQLite) ya da `date` (PostgreSQL) döner ve karşılaştırmayı SQL'e
# indirmek iki diyalekte iki farklı sonuç verirdi. `_gun` farkı düzeltiyor —
# `_arinma_ihlalleri`nin aynı tercihi.
_KARANTINA_HAYVAN_SORGU = text(
    """SELECT k.id,k.started_on,k.ended_on,k.reason,k.animal_id,k.group_id
    FROM animal_quarantines k
    WHERE k.company_id=:cid
      AND (k.animal_id=:aid
           OR (k.group_id IS NOT NULL AND k.group_id=(
                 SELECT h.group_id FROM animals h
                 WHERE h.company_id=:cid AND h.id=:aid)))"""
)

# SÜRÜ YOLU. Sürüye yazılmış karantinalar VE sürüdeki herhangi bir hayvanın
# bireysel karantinası BİRLİKTE ısırıyor: grup sağımı o hayvanın sütünü de
# içerir, yani karantinadaki tek bir inek tankın tamamını kirletir.
_KARANTINA_GRUP_SORGU = text(
    """SELECT k.id,k.started_on,k.ended_on,k.reason,k.animal_id,k.group_id
    FROM animal_quarantines k
    WHERE k.company_id=:cid
      AND (k.group_id=:gid
           OR (k.animal_id IS NOT NULL AND k.animal_id IN (
                 SELECT h.id FROM animals h
                 WHERE h.company_id=:cid AND h.group_id=:gid)))"""
)


def _karantina_ihlalleri(
    db: Session, cid: int, animal_id: int | None, group_id: int | None,
    hedef_gun: date,
) -> list[dict[str, Any]]:
    """``hedef_gun``de bu hayvanı/sürüyü KAPSAYAN karantinalar.

    ARALIK YARI AÇIKTIR: ``started_on <= hedef_gun < ended_on``. İki ucun
    farklı davranmasının gerekçesi ayrıdır ve ikisi de ölçülmüştür:

    * BAŞLANGIÇ GÜNÜ KAPSANIR — karantina o gün başlar, yani o günün sağımı
      zaten karantinalı hayvanındır.
    * BİTİŞ GÜNÜ KAPSANMAZ — karantina o gün KALKAR. ``<=`` yazmak, hayvanı
      çıkardığınız günün kendisinde onu hâlâ tutardı ve kullanıcı bir gün
      daha beklemek zorunda kalırdı; oysa kapatma tarihini yazan da odur. Bu,
      `_arinma_ihlalleri`nin sınır günü kuralının AYNISIDIR.
    * ``ended_on`` NULL ise ÜST SINIR YOKTUR: karantina açıktır ve
      ``started_on``dan sonraki HER gün kapsanır.

    KAPANMIŞ KARANTİNA GEÇMİŞİ HÂLÂ KESER: aralığın içine düşen GERİYE DÖNÜK
    bir sağım/hareket ihlaldir. Kilidi "yalnız açık karantina" diye yazmak,
    karantinayı kapatıp geçmişe kayıt girerek onu tamamen atlatmayı mümkün
    kılardı.
    """
    if animal_id is not None:
        satirlar = db.execute(
            _KARANTINA_HAYVAN_SORGU, {"cid": cid, "aid": int(animal_id)}
        ).mappings().all()
    else:
        satirlar = db.execute(
            _KARANTINA_GRUP_SORGU, {"cid": cid, "gid": int(group_id)}
        ).mappings().all()

    ihlaller: list[dict[str, Any]] = []
    for r in satirlar:
        baslangic = _gun(r["started_on"])
        bitis = _gun(r["ended_on"])
        if hedef_gun < baslangic:
            continue
        if bitis is not None and hedef_gun >= bitis:
            continue
        ihlaller.append({
            "quarantine_id": int(r["id"]),
            "started_on": baslangic.isoformat(),
            "ended_on": bitis.isoformat() if bitis is not None else None,
            "reason": r["reason"],
            # Kaydı HANGİ yolun kestiği: bireysel karantina mı, sürü
            # karantinası mı. Kullanıcı "bu hayvanı ben karantinaya almadım"
            # deyip hiçbir şey bulamasın diye.
            "scope": "ANIMAL" if r["animal_id"] is not None else "GROUP",
        })
    # AÇIK OLANLAR EN ÜSTTE, sonra en geç başlayan: kullanıcının ilgilendiği
    # şey hâlâ süren kısıttır, kapanmış bir aralık değil.
    ihlaller.sort(
        key=lambda x: (x["ended_on"] is None, x["started_on"]), reverse=True
    )
    return ihlaller


def _karantina_ihlal_metni(ihlaller: list[dict[str, Any]]) -> str:
    ilk = ihlaller[0]
    if ilk["ended_on"] is None:
        return (
            f"{ilk['started_on']} tarihinde açılan karantina HÂLÂ AÇIK: "
            f"{ilk['reason']}"
        )
    return (
        f"{ilk['started_on']} - {ilk['ended_on']} karantinası bu tarihi "
        f"kapsıyor: {ilk['reason']}"
    )


def _karantina_dogrula(
    ihlaller: list[dict[str, Any]], politika: str, gerekce: str | None, ne: str,
) -> str | None:
    """Karantina kilidinin kararı; politika dalları `_kilit_karari`ndadır."""
    if not ihlaller:
        return None
    metin = _karantina_ihlal_metni(ihlaller)
    return _kilit_karari(
        ihlaller, politika, gerekce, metin,
        f"{ne} karantina altında: {metin}", _KARANTINA_SEBEP,
        "Firma ayarı karantina açıkken kaydetmeye izin vermiyor; "
        "önce karantinayı kapatın.",
    )


def _sut_karantina_dogrula(
    db: Session, cid: int, payload: MilkYieldWrite,
) -> str | None:
    ihlaller = _karantina_ihlalleri(
        db, cid, payload.animal_id, payload.group_id, payload.milked_on,
    )
    return _karantina_dogrula(
        ihlaller, _karantina_politikasi(db, cid),
        payload.quarantine_override_reason, "Süt",
    )


def _hareket_karantina_dogrula(
    db: Session, cid: int, payload: MovementWrite,
) -> str | None:
    """Karantina kilidi satış, kesim VE NAKİLDE; gerekçesi kümenin başında."""
    if payload.kind not in _KARANTINA_KILITLI_HAREKETLER:
        return None
    ihlaller = _karantina_ihlalleri(
        db, cid, payload.animal_id, None, payload.moved_on,
    )
    return _karantina_dogrula(
        ihlaller, _karantina_politikasi(db, cid),
        payload.quarantine_override_reason, "Hareket",
    )


# ------------------------------------------------------------ katalog uçları ---

@router.get("/vet-drugs")
def list_vet_drugs(
    request: Request,
    limit: int = _SAYFA,
    offset: int = _ATLA,
    product_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if product_id:
        kosul += " AND k.product_id=:product_id"
        params["product_id"] = product_id
    if status:
        kosul += " AND k.status=:status"
        params["status"] = status.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM vet_drugs k WHERE k.company_id=:cid{kosul}"),
        params,
    ).scalar()
    # ÜRÜN ADI BİRLEŞTİRİLEREK GELİYOR (0063'ün `list_ppp` gerekçesi) ve
    # birleştirme KİRACI İÇİNDE: `u.company_id=k.company_id` olmadan başka
    # firmanın ürün adı bu listeye düşebilirdi.
    rows = db.execute(
        text(
            f"""SELECT k.id,k.product_id,u.name AS product_name,k.species,
            k.milk_withdrawal_days,k.meat_withdrawal_days,k.route,k.dose_unit,
            k.registration_no,k.notes,k.status,k.origin,k.origin_reference,
            k.updated_at
            FROM vet_drugs k
            JOIN products u ON u.id=k.product_id AND u.company_id=k.company_id
            WHERE k.company_id=:cid{kosul}
            ORDER BY u.name,k.species LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


@router.post("/vet-drugs", status_code=201)
def create_vet_drug(
    payload: VetDrugWrite, request: Request, db: Session = Depends(get_db),
):
    cid = company_id(request)
    _urun_dogrula(db, cid, payload.product_id)
    now = _simdi()
    try:
        yeni = db.execute(
            text(
                """INSERT INTO vet_drugs(company_id,product_id,species,
                milk_withdrawal_days,meat_withdrawal_days,route,dose_unit,
                registration_no,notes,status,origin,origin_reference,
                created_at,updated_at)
                VALUES(:cid,:product_id,:species,:milk_withdrawal_days,
                :meat_withdrawal_days,:route,:dose_unit,:registration_no,:notes,
                'ACTIVE','MANUAL',NULL,:now,:now) RETURNING id"""
            ),
            {"cid": cid, "now": now, **payload.model_dump()},
        ).scalar_one()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "Bu ürün ve tür için katalog kaydı zaten var"
        ) from exc
    log_request_activity(
        db, request, cid, "vet_drug.create", "vet_drug", int(yeni),
        "Veteriner ilaç kataloğu satırı eklendi: süt "
        f"{payload.milk_withdrawal_days} gün, et {payload.meat_withdrawal_days} gün",
        {"product_id": payload.product_id, "species": payload.species,
         "milk_withdrawal_days": payload.milk_withdrawal_days,
         "meat_withdrawal_days": payload.meat_withdrawal_days},
    )
    db.commit()
    return _satir(db, cid, "vet_drugs", int(yeni))


@router.get("/vet-drugs/{drug_id}")
def get_vet_drug(drug_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), "vet_drugs", drug_id)


@router.put("/vet-drugs/{drug_id}")
def update_vet_drug(
    drug_id: int, payload: VetDrugUpdate, request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    mevcut = _satir(db, cid, "vet_drugs", drug_id)
    _urun_dogrula(db, cid, payload.product_id)
    veri = payload.model_dump(exclude={"expected_updated_at"})
    try:
        sonuc = db.execute(
            text(
                """UPDATE vet_drugs SET product_id=:product_id,species=:species,
                milk_withdrawal_days=:milk_withdrawal_days,
                meat_withdrawal_days=:meat_withdrawal_days,route=:route,
                dose_unit=:dose_unit,registration_no=:registration_no,
                notes=:notes,status=:status,updated_at=:now
                WHERE id=:id AND company_id=:cid AND updated_at=:expected_updated_at"""
            ),
            {"id": drug_id, "cid": cid, "now": _simdi(),
             "expected_updated_at": _surum_param(payload.expected_updated_at),
             **veri},
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "Bu ürün ve tür için katalog kaydı zaten var"
        ) from exc
    if sonuc.rowcount != 1:
        _surum_cakismasi(db)
    # ESKİ VE YENİ DEĞER BİRLİKTE: 28 günlük et arınmasını 2 güne çeken bir
    # düzenleme, satırın kendi `updated_at`inde yalnız "değişti" der.
    log_request_activity(
        db, request, cid, "vet_drug.update", "vet_drug", drug_id,
        "Veteriner ilaç kataloğu satırı güncellendi: süt "
        f"{mevcut['milk_withdrawal_days']} -> {payload.milk_withdrawal_days} gün, "
        f"et {mevcut['meat_withdrawal_days']} -> {payload.meat_withdrawal_days} gün",
        {"milk_before": mevcut["milk_withdrawal_days"],
         "milk_after": payload.milk_withdrawal_days,
         "meat_before": mevcut["meat_withdrawal_days"],
         "meat_after": payload.meat_withdrawal_days,
         "status_before": mevcut["status"], "status_after": payload.status},
    )
    db.commit()
    return _satir(db, cid, "vet_drugs", drug_id)


# ------------------------------------------------------------- tedavi uçları ---

def _tedavi_kalemleri(db: Session, cid: int, tedavi_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(
        text(
            """SELECT id,product_id,drug_name,dose,dose_unit
            FROM animal_treatment_items
            WHERE company_id=:cid AND treatment_id=:tid ORDER BY id"""
        ),
        {"cid": cid, "tid": tedavi_id},
    ).mappings().all()]


@router.get("/animal-treatments")
def list_treatments(
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
        text(f"SELECT COUNT(*) FROM animal_treatments WHERE company_id=:cid{kosul}"),
        params,
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,animal_id,group_id,treated_on,veterinarian,diagnosis,
            notes,milk_withdrawal_days,meat_withdrawal_days,withdrawal_source,
            catalogue_milk_days,catalogue_meat_days,updated_at
            FROM animal_treatments WHERE company_id=:cid{kosul}
            ORDER BY treated_on DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


@router.post("/animal-treatments", status_code=201)
def create_treatment(
    payload: TreatmentWrite, request: Request, db: Session = Depends(get_db),
):
    """Tedavi + kalemleri + ÇÖZÜLMÜŞ arınma süreleri AYNI İŞLEMDE.

    Süreler YAZMA anında çözülüp SATIRA yazılıyor, okuma anında katalogdan
    hesaplanmıyor. Gerekçe 0063'ün tercihiyle aynı: katalog yarın değişebilir
    ve o değişiklik DÜN kaydedilmiş bir tedavinin arınmasını geriye dönük
    olarak kısaltmamalı. Kilit, tedavinin YAZILDIĞI GÜN geçerli olan
    prospektüse dayanır ve `catalogue_*` sütunları o günün kaydıdır.

    STOK HAREKETİ YAZILMIYOR ve bu, modülün 0049'dan beri geçerli sınırının
    (V1 stok/muhasebeye fiş yazmaz — modül başlığı) AYNEN korunmasıdır:
    kalemin `product_id`si burada YALNIZ katalog çözümü içindir, depodan
    ilaç düşmez.
    """
    cid = company_id(request)
    # HAYVAN YA DA GRUP — İKİSİ BİRDEN DEĞİL. Veritabanındaki CHECK
    # (`ck_animal_treatments_hedef`, göç 0074) son savunma; buradaki kontrol
    # kullanıcıya anlaşılır mesaj verir. `create_milk`in kalıbı.
    if (payload.animal_id is None) == (payload.group_id is None):
        raise HTTPException(
            422,
            "Tedavi ya bir hayvana ya bir sürüye bağlanmalı; ikisi birden "
            "verilirse hangi hayvanların arındığı belirsiz kalır.",
        )
    tur = _tedavi_turu(db, cid, payload)
    for kalem in payload.items:
        if kalem.product_id is not None:
            _urun_dogrula(db, cid, kalem.product_id)

    kat_sut, kat_et, sut, et, koken = _arinma_coz(db, cid, payload, tur)
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO animal_treatments(company_id,animal_id,group_id,
            treated_on,veterinarian,diagnosis,notes,milk_withdrawal_days,
            meat_withdrawal_days,withdrawal_source,catalogue_milk_days,
            catalogue_meat_days,created_at,updated_at)
            VALUES(:cid,:animal_id,:group_id,:treated_on,:veterinarian,:diagnosis,
            :notes,:milk,:meat,:koken,:kat_sut,:kat_et,:now,:now) RETURNING id"""
        ),
        {"cid": cid, "now": now, "animal_id": payload.animal_id,
         "group_id": payload.group_id, "treated_on": payload.treated_on,
         "veterinarian": payload.veterinarian, "diagnosis": payload.diagnosis,
         "notes": payload.notes, "milk": sut, "meat": et, "koken": koken,
         "kat_sut": kat_sut, "kat_et": kat_et},
    ).scalar_one()
    for kalem in payload.items:
        db.execute(
            text(
                """INSERT INTO animal_treatment_items(company_id,treatment_id,
                product_id,drug_name,dose,dose_unit,created_at,updated_at)
                VALUES(:cid,:tid,:product_id,:drug_name,:dose,:dose_unit,:now,:now)"""
            ),
            {"cid": cid, "tid": int(yeni), "now": now, **kalem.model_dump()},
        )
    db.commit()
    kayit = _satir(db, cid, "animal_treatments", int(yeni))
    kayit["items"] = _tedavi_kalemleri(db, cid, int(yeni))
    return kayit


@router.get("/animal-treatments/{treatment_id}")
def get_treatment(
    treatment_id: int, request: Request, db: Session = Depends(get_db),
):
    cid = company_id(request)
    kayit = _satir(db, cid, "animal_treatments", treatment_id)
    kayit["items"] = _tedavi_kalemleri(db, cid, treatment_id)
    return kayit


# --------------------------------------------------------- karantina uçları ---

@router.get("/animal-quarantines")
def list_quarantines(
    request: Request, limit: int = _SAYFA, offset: int = _ATLA,
    animal_id: int | None = None, group_id: int | None = None,
    open_only: bool = False,
    db: Session = Depends(get_db),
):
    """Karantina defteri. ``open_only`` YALNIZ AÇIK olanları süzer.

    Süzgeç parçaları KAPALI bir kümeden kuruluyor ve istekten gelen DEĞERLER
    her durumda BAĞLI PARAMETREDİR; ``open_only`` bir bool'dur ve metne
    DEĞERİ değil, hangi SABİT parçanın ekleneceği girer.
    """
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if animal_id:
        kosul += " AND animal_id=:animal_id"
        params["animal_id"] = animal_id
    if group_id:
        kosul += " AND group_id=:group_id"
        params["group_id"] = group_id
    if open_only:
        kosul += " AND ended_on IS NULL"
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM animal_quarantines WHERE company_id=:cid{kosul}"),
        params,
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,animal_id,group_id,started_on,ended_on,reason,notes,
            updated_at
            FROM animal_quarantines WHERE company_id=:cid{kosul}
            ORDER BY started_on DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return _sayfa(rows, toplam, limit, offset)


@router.post("/animal-quarantines", status_code=201)
def create_quarantine(
    payload: QuarantineWrite, request: Request, db: Session = Depends(get_db),
):
    """Karantinayı AÇAR. Kapanış tarihi BURADA YAZILMAZ.

    AÇIK KARANTİNA HEDEF BAŞINA TEKTİR ve bunu ŞEMA zorluyor (göç 0075'in iki
    kısmi tekil indeksi). Uygulama katmanında bir "önce bak, sonra yaz"
    kontrolü İKİ EŞZAMANLI isteği ayırt EDEMEZDİ; ayıran yalnız indekstir ve
    ihlali burada 409'a çevriliyor.
    """
    cid = company_id(request)
    # HAYVAN YA DA GRUP — İKİSİ BİRDEN DEĞİL. `ck_animal_quarantines_hedef`
    # son savunma; buradaki kontrol kullanıcıya anlaşılır mesaj verir
    # (`create_milk`/`create_treatment` kalıbı).
    if (payload.animal_id is None) == (payload.group_id is None):
        raise HTTPException(
            422,
            "Karantina ya bir hayvana ya bir sürüye bağlanmalı; ikisi birden "
            "verilirse hangi hayvanların karantinada olduğu belirsiz kalır.",
        )
    if payload.animal_id is not None:
        _satir(db, cid, "animals", payload.animal_id)
    else:
        _satir(db, cid, "animal_groups", payload.group_id)
    now = _simdi()
    try:
        yeni = db.execute(
            text(
                """INSERT INTO animal_quarantines(company_id,animal_id,group_id,
                started_on,ended_on,reason,notes,created_at,updated_at)
                VALUES(:cid,:animal_id,:group_id,:started_on,NULL,:reason,:notes,
                :now,:now) RETURNING id"""
            ),
            {"cid": cid, "now": now, **payload.model_dump()},
        ).scalar_one()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409,
            "Bu hayvan/sürü için zaten AÇIK bir karantina var; önce onu "
            "kapatın.",
        ) from exc
    log_request_activity(
        db, request, cid, "animal_quarantine.opened", "animal_quarantine",
        int(yeni),
        f"Karantina açıldı ({payload.started_on}): {payload.reason}",
        {"animal_id": payload.animal_id, "group_id": payload.group_id,
         "started_on": payload.started_on.isoformat(),
         "reason": payload.reason},
    )
    db.commit()
    return _satir(db, cid, "animal_quarantines", int(yeni))


@router.get("/animal-quarantines/{quarantine_id}")
def get_quarantine(
    quarantine_id: int, request: Request, db: Session = Depends(get_db),
):
    return _satir(db, company_id(request), "animal_quarantines", quarantine_id)


@router.post("/animal-quarantines/{quarantine_id}/close")
def close_quarantine(
    quarantine_id: int, payload: QuarantineClose, request: Request,
    db: Session = Depends(get_db),
):
    """Karantinayı KAPATIR. Yazma ``ended_on IS NULL`` üzerinde KOŞULLUDUR.

    KAPATMA AYRI BİR UÇTUR, `PUT /animal-quarantines/{id}` DEĞİL — ve bu
    ayrım BİLİNÇLİDİR. Genel bir güncelleme ucu `started_on`u ve `reason`u da
    yazdırırdı; oysa karantinanın AÇILIŞI bir OLGUDUR ve geçmişe dönük
    değiştirilmesi, o karantinanın kestiği bütün sağım ve hareketleri
    GERİYE DÖNÜK olarak haklı ya da haksız çıkarırdı.

    CAS (compare-and-set): ``WHERE ... AND ended_on IS NULL``. İki eşzamanlı
    kapatmadan biri kazanır, öteki ``rowcount == 0`` görür ve 409 alır. Bir
    "önce oku, kapalı mı bak, sonra yaz" dizisi bu yarışı KAYBEDERDİ.
    """
    cid = company_id(request)
    mevcut = _satir(db, cid, "animal_quarantines", quarantine_id)
    baslangic = _gun(mevcut["started_on"])
    # ARALIK GERİYE AKMAZ. `ck_animal_quarantines_aralik` son savunma;
    # buradaki kontrol 500 yerine anlaşılır bir 422 veriyor.
    if payload.ended_on < baslangic:
        raise HTTPException(
            422,
            f"Karantina {baslangic.isoformat()} tarihinde başladı; kapanış "
            "tarihi ondan önce olamaz.",
        )
    sonuc = db.execute(
        text(
            """UPDATE animal_quarantines
            SET ended_on=:ended_on,notes=COALESCE(:notes,notes),updated_at=:now
            WHERE id=:id AND company_id=:cid AND ended_on IS NULL"""
        ),
        {"id": quarantine_id, "cid": cid, "now": _simdi(),
         "ended_on": payload.ended_on, "notes": payload.notes},
    )
    if sonuc.rowcount != 1:
        # ZATEN KAPALI. 404 DEĞİL 409: kayıt VAR (yukarıdaki `_satir` onu
        # okudu) ve istek YENİDEN GÖNDERİLİRSE de aynı sonucu verir — bu bir
        # durum çakışmasıdır, bulunamama değil.
        db.rollback()
        raise HTTPException(409, "Karantina zaten kapatılmış")
    log_request_activity(
        db, request, cid, "animal_quarantine.closed", "animal_quarantine",
        quarantine_id,
        f"Karantina kapatıldı ({payload.ended_on}): {mevcut['reason']}",
        {"started_on": baslangic.isoformat(),
         "ended_on": payload.ended_on.isoformat(),
         "reason": mevcut["reason"]},
    )
    db.commit()
    return _satir(db, cid, "animal_quarantines", quarantine_id)

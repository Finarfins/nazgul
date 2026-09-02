"""Uygulama Kayıt Çizelgesi — tarla kayıtlarının tek çıktıda toplanması.

--- BU ÇİZELGE NE DEĞİLDİR --------------------------------------------------

Bu dosya RESMÎ BİR FORM ÜRETMEZ. Depoda "Üretici Kayıt Defteri"nin bakanlıkça
yayımlanmış sütun düzenine dair HİÇBİR kaynak yok: yükümlülüğün varlığına
atıflar var, formun kendisine dair sıfır. Elle yeniden kurulmuş bir bakanlık
formu, deponun kaynağı olmayan bir DÜZENİ iddia etmesi olurdu — göç 0063'te
reddedilen "depoya gömülen rakam" kusurunun bir üst seviyesi.

Bu yüzden çıktı KENDİ tasarımımızdır ve bunu sayfanın kendisinde SÖYLER
(bkz. ``CIZELGE_NOTU``). Arma yok, form numarası yok, "onaylıdır" ifadesi yok.
Resmî düzen bir gün eline geçtiğinde değişecek olan yalnız başlık listeleridir;
veri katmanı ve sorgular aynen kalır.

--- SÜTUNLAR ÖLÇÜLDÜ, UYDURULMADI ------------------------------------------

Aşağıdaki iki başlık listesi göçlerden ÖLÇÜLEREK çıkarıldı (0044 tarla modeli,
0046 güvenlik gerekçesi, 0048 sistem uyarısı, 0062 sezon ürünü). Deponun
TUTMADIĞI hiçbir alan için sütun AÇILMADI: boş bir "Hedef zararlı" sütunu,
sistemin o veriyi izlediğini sanmaya davet ederdi. Eksik olan alan görünmez;
yanlış olarak görünmez değil.

BİLEREK DIŞARIDA BIRAKILANLAR — para sütunları. ``unit_cost``/``total_cost``,
``labor_hourly_rate``/``machine_hourly_rate`` (göç 0053) ve ``revenue_amount``
(göç 0047) bu çıktıda YOK. İki gerekçe: (1) çizelge denetime gösterilmek için
üretiliyor, maliyet ve kâr denetimin konusu değil; (2) ``farm.py``deki okuma
yolu kapısı donmuş oran sütunlarını role göre maskeliyor — bu çıktı o maskenin
ETRAFINDAN dolaşan ikinci bir yol olamaz. Sütunları hiç SEÇMEMEK, maskeyi
burada yeniden kurmaktan güvenli.

--- ÇIKTI NEDEN NUMARALI/DONDURULMUŞ BİR DEFTER DEĞİL ----------------------

Ölçüldü: ``field_activities`` ve ``field_harvests`` üzerinde PUT/PATCH/DELETE
YOK — kayıtlar değişmez, iptal bir DURUMDUR. Hasat bekleme süresi de satıra
EKLEME ANINDA yazılıyor, kaynağı sonradan değişse bile eski satırın değeri
kaymıyor. Yani aynı süzgeçle iki kez üretilen çizelge aynı satırları verir;
talep üzerine üretilen çıktı zaten KARARLIDIR ve numaralandırma ya da dondurma
mekanizması EKLEMİYORUZ. Bu, çıktının kendisinde de yazılıdır.

--- KARTEZYEN ÇARPIM VE SINIRSIZ SONUÇ -------------------------------------

Faaliyet, girdi ve hasat AYRI sorgularla toplanıp Python'da birleştiriliyor.
Tek sorguda JOIN'lemek N girdi × M hasat üretirdi; ``parcel_timeline`` aynı
tuzağa aynı çözümle giriyor.

Süzgeçsiz çağrı YASAK (``EN_AZ_BIR_SUZGEC``): bir firmanın bütün tarihçesini
tek istekte akıtan bir uç, hem bellek hem denetim açısından sınırsızdır.
Süzgeç verilse de üst sınır ayrıca zorlanıyor (``AZAMI_SATIR``).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .business_time import ISTANBUL

#: Çizelgenin ne olduğunu SAYFANIN KENDİSİNDE söyleyen tek satır. Çıktıyı eline
#: alan kişi, bunun resmî bir form olmadığını dosyayı açar açmaz görmeli;
#: gerekçe yalnız kod yorumunda kalırsa okuyucuya hiç ulaşmaz.
CIZELGE_NOTU = (
    "Bu çizelge işletmenin kendi kayıtlarından üretilmiş bir UYGULAMA KAYIT "
    "ÇİZELGESİdir. Resmî bir form değildir, hiçbir kurum tarafından "
    "onaylanmamıştır ve resmî Üretici Kayıt Defteri yerine geçmez. Kaynak "
    "kayıtlar değiştirilemez olduğu için aynı süzgeçle yeniden üretilen "
    "çizelge aynı satırları verir."
)

CIZELGE_ADI = "Uygulama Kayıt Çizelgesi"

#: Faaliyet + girdi sayfası. Her başlık, göçlerde ÖLÇÜLEN bir sütuna karşılık
#: gelir; karşılığı olmayan başlık yoktur.
FAALIYET_BASLIKLARI = (
    "Çiftlik Kodu",
    "Çiftlik",
    "Parsel Kodu",
    "Parsel",
    "Ada",
    "Parsel No",
    "Mahalle/Köy",
    "İl",
    "İlçe",
    "Parsel Alanı (da)",
    "Sezon Yılı",
    "Ürün",
    "Çeşit",
    "Ekilen Alan (da)",
    "Uygulama Tarihi",
    "Uygulama Saati",
    "Faaliyet",
    "Uygulanan Alan (da)",
    "Girdi Adı",
    "Miktar",
    "Birim",
    "Doz",
    "Doz Birimi",
    "Tekrar Giriş Süresi (gün)",
    "Hasat Bekleme Süresi (gün)",
    "Uygulayan",
    "Makine",
    "Makine Plaka/No",
    "Alan Aşım Gerekçesi",
    "Not",
)

#: Hasat sayfası.
HASAT_BASLIKLARI = (
    "Çiftlik Kodu",
    "Çiftlik",
    "Parsel Kodu",
    "Parsel",
    "Ada",
    "Parsel No",
    "Sezon Yılı",
    "Ürün",
    "Çeşit",
    "Hasat Tarihi",
    "Miktar",
    "Birim",
    "Hasat Alanı (da)",
    "Kalite",
    "Nem (%)",
    "Sistem Uyarısı",
    "Erken Hasat Gerekçesi",
    "Not",
)

FAALIYET_TURLERI = {
    "SOWING": "Ekim / dikim",
    "FERTILIZING": "Gübreleme",
    "SPRAYING": "İlaçlama",
    "IRRIGATION": "Sulama",
    "TILLAGE": "Toprak işleme",
    "OTHER": "Diğer",
}

#: Kabul edilen KAPSAM süzgeçleri. EN AZ BİRİ zorunlu.
EN_AZ_BIR_SUZGEC = ("farm_id", "parcel_id", "season_id", "season_year")

#: Tarih aralığı TEK BAŞINA süzgeç sayılmaz: "2020-2026 arası" bütün tarihçe
#: demektir ve sınırsız sonucu engellemez. Kapsam süzgeciyle BİRLİKTE daraltır.
TARIH_SUZGECLERI = ("date_from", "date_to")

#: Üst sınır. Süzgeç verilmiş olsa bile çok sezonlu bir çiftlik bu sınırı
#: aşabilir; aşınca sessizce KESMİYORUZ — eksik bir çizelge, eksik olduğunu
#: söylemeyen bir çizelgedir. Çağıran daraltmaya zorlanır.
AZAMI_SATIR = 20_000


class DefterHatasi(Exception):
    """Çağıranın düzeltebileceği süzgeç hatası; uç bunu 400'e çevirir."""


def _yerel_gun(deger: Any) -> date:
    """Zaman damgasını İSTANBUL gününe çevirir.

    ``farm.py::_yerel_gun`` ile AYNI kural. UTC gününe düşmek, uygulamanın iki
    parçasının hangi gün olduğu konusunda anlaşmaması demek olurdu: bekleme
    süresi hesabı İstanbul gününü kullanıyor; çizelge UTC kullansaydı gece
    yarısından sonra girilen bir ilaçlama defterde BİR GÜN GERİDE görünürdü.
    """
    if isinstance(deger, str):
        deger = datetime.fromisoformat(deger)
    if deger.tzinfo is None:
        deger = deger.replace(tzinfo=timezone.utc)
    return deger.astimezone(ISTANBUL).date()


def _yerel_saat(deger: Any) -> str:
    if isinstance(deger, str):
        deger = datetime.fromisoformat(deger)
    if deger.tzinfo is None:
        deger = deger.replace(tzinfo=timezone.utc)
    return deger.astimezone(ISTANBUL).strftime("%H:%M")


def _gun(deger: Any) -> date:
    if isinstance(deger, datetime):
        return deger.date()
    if isinstance(deger, date):
        return deger
    return date.fromisoformat(str(deger)[:10])


def _gun_metni(deger: Any) -> str:
    return "" if deger is None else _gun(deger).strftime("%d.%m.%Y")


def _sayi(deger: Any) -> str:
    """Ondalık değerler METİN olarak veriliyor.

    Depo kuralı: miktar (18,4), alan hesapları Decimal; float'a çevirmek dekar
    başına verimi kaydırır. Excel hücresinde de metin duruyor ki yuvarlama
    okuyanın makinesinde OLUŞMASIN.
    """
    if deger is None:
        return ""
    return str(Decimal(str(deger)))


def _metin(deger: Any) -> str:
    return "" if deger is None else str(deger)


def suzgec_dogrula(suzgec: dict[str, Any]) -> dict[str, Any]:
    """En az bir KAPSAM süzgeci ister; tarih aralığının sırasını denetler."""
    temiz = {ad: suzgec.get(ad) for ad in EN_AZ_BIR_SUZGEC + TARIH_SUZGECLERI}
    if not any(temiz[ad] is not None for ad in EN_AZ_BIR_SUZGEC):
        raise DefterHatasi(
            "Çizelge için en az bir kapsam süzgeci gerekli: "
            "çiftlik, parsel, sezon ya da sezon yılı."
        )
    baslangic, bitis = temiz["date_from"], temiz["date_to"]
    if baslangic is not None and bitis is not None and bitis < baslangic:
        raise DefterHatasi("Bitiş tarihi başlangıç tarihinden önce olamaz.")
    return temiz


def _sezonlar(db: Session, cid: int, suzgec: dict[str, Any]) -> list[dict[str, Any]]:
    kosullar = ["s.company_id=:cid"]
    parametre: dict[str, Any] = {"cid": cid}
    if suzgec["farm_id"] is not None:
        kosullar.append("f.id=:farm_id")
        parametre["farm_id"] = suzgec["farm_id"]
    if suzgec["parcel_id"] is not None:
        kosullar.append("p.id=:parcel_id")
        parametre["parcel_id"] = suzgec["parcel_id"]
    if suzgec["season_id"] is not None:
        kosullar.append("s.id=:season_id")
        parametre["season_id"] = suzgec["season_id"]
    if suzgec["season_year"] is not None:
        kosullar.append("s.season_year=:season_year")
        parametre["season_year"] = suzgec["season_year"]

    # Her JOIN kiracıya bağlı: bileşik yabancı anahtar zaten çapraz firma
    # bağlantısını engelliyor ama sorgu ikinci savunmayı bırakıyor.
    return [dict(r) for r in db.execute(
        text(
            """SELECT s.id AS season_id,s.season_year,s.crop,s.variety,
            s.planted_area_decare,
            p.id AS parcel_id,p.code AS parcel_code,p.name AS parcel_name,
            p.area_decare,p.parcel_no,p.block_no,p.neighborhood,
            p.city AS parcel_city,p.district AS parcel_district,
            f.code AS farm_code,f.name AS farm_name
            FROM crop_seasons s
            JOIN farm_parcels p
              ON p.company_id=s.company_id AND p.id=s.parcel_id
            JOIN farms f
              ON f.company_id=p.company_id AND f.id=p.farm_id
            WHERE """
            + " AND ".join(kosullar)
            + " ORDER BY f.code,p.code,s.season_year DESC,s.id DESC"
        ),
        parametre,
    ).mappings().all()]


def _faaliyetler(db: Session, cid: int, sezon_ids: list[int]) -> list[dict[str, Any]]:
    # Tarih süzgeci SQL'de UTC damgasına değil, Python'da İstanbul gününe
    # uygulanıyor (bkz. `_yerel_gun`): sınırı veritabanı saat diliminde kesmek
    # gece yarısı civarındaki kayıtları yanlış tarafa atardı.
    return [dict(r) for r in db.execute(
        text(
            """SELECT a.id,a.season_id,a.activity_type,a.performed_at,
            a.applied_area_decare,a.reentry_interval_days,
            a.preharvest_interval_days,a.area_override_reason,a.notes,
            a.operator_user_id,a.machine_id
            FROM field_activities a
            WHERE a.company_id=:cid AND a.season_id IN :ids
              AND a.status='RECORDED'
            ORDER BY a.performed_at,a.id"""
        ).bindparams(bindparam("ids", expanding=True)),
        {"cid": cid, "ids": sezon_ids},
    ).mappings().all()]


def _girdiler(db: Session, cid: int, faaliyet_ids: list[int]) -> dict[int, list[dict]]:
    satirlar = db.execute(
        text(
            """SELECT i.activity_id,i.input_name,i.quantity,i.unit,i.dose,i.dose_unit
            FROM field_activity_inputs i
            WHERE i.company_id=:cid AND i.activity_id IN :ids
            ORDER BY i.activity_id,i.id"""
        ).bindparams(bindparam("ids", expanding=True)),
        {"cid": cid, "ids": faaliyet_ids},
    ).mappings().all()
    tablo: dict[int, list[dict]] = {}
    for r in satirlar:
        tablo.setdefault(int(r["activity_id"]), []).append(dict(r))
    return tablo


def _kullanicilar(db: Session, cid: int, user_ids: list[int]) -> dict[int, str]:
    """Uygulayan adları — ÜYELİK üzerinden firmaya bağlanarak.

    ``app_users`` firma sütunu taşımaz; doğrudan id ile okumak, kayıt hatalıysa
    başka firmanın kullanıcı adını çizelgeye taşıyabilirdi. Üyelik JOIN'i bunu
    imkânsız kılar: üyeliği olmayan kimlik satır DÖNDÜRMEZ, ad boş kalır.
    """
    satirlar = db.execute(
        text(
            """SELECT u.id,u.display_name,u.username
            FROM app_users u
            JOIN user_company_memberships m ON m.user_id=u.id
            WHERE m.company_id=:cid AND u.id IN :ids"""
        ).bindparams(bindparam("ids", expanding=True)),
        {"cid": cid, "ids": user_ids},
    ).mappings().all()
    return {int(r["id"]): _metin(r["display_name"] or r["username"]) for r in satirlar}


def _makineler(db: Session, cid: int, machine_ids: list[int]) -> dict[int, dict]:
    satirlar = db.execute(
        text(
            """SELECT m.id,m.brand,m.model,m.registration_number
            FROM machines m
            WHERE m.company_id=:cid AND m.id IN :ids"""
        ).bindparams(bindparam("ids", expanding=True)),
        {"cid": cid, "ids": machine_ids},
    ).mappings().all()
    return {int(r["id"]): dict(r) for r in satirlar}


def _hasatlar(db: Session, cid: int, sezon_ids: list[int]) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(
        text(
            """SELECT h.season_id,h.harvested_on,h.quantity,h.unit,
            h.harvested_area_decare,h.quality_grade,h.moisture_percent,
            h.safety_warning,h.safety_override_reason,h.notes
            FROM field_harvests h
            WHERE h.company_id=:cid AND h.season_id IN :ids
              AND h.status='RECORDED'
            ORDER BY h.harvested_on,h.id"""
        ).bindparams(bindparam("ids", expanding=True)),
        {"cid": cid, "ids": sezon_ids},
    ).mappings().all()]


def _kapsam_disi(gun: date, suzgec: dict[str, Any]) -> bool:
    if suzgec["date_from"] is not None and gun < suzgec["date_from"]:
        return True
    if suzgec["date_to"] is not None and gun > suzgec["date_to"]:
        return True
    return False


def defter_verisi(db: Session, cid: int, ham_suzgec: dict[str, Any]) -> dict[str, Any]:
    """Süzgeci doğrular, kayıtları toplar, satırları Python'da birleştirir."""
    suzgec = suzgec_dogrula(ham_suzgec)

    sezonlar = _sezonlar(db, cid, suzgec)
    sezon_tablosu = {int(s["season_id"]): s for s in sezonlar}
    sezon_ids = list(sezon_tablosu)

    faaliyetler: list[dict[str, Any]] = []
    hasatlar: list[dict[str, Any]] = []
    girdi_tablosu: dict[int, list[dict]] = {}
    kullanici_tablosu: dict[int, str] = {}
    makine_tablosu: dict[int, dict] = {}

    if sezon_ids:
        faaliyetler = [
            f for f in _faaliyetler(db, cid, sezon_ids)
            if not _kapsam_disi(_yerel_gun(f["performed_at"]), suzgec)
        ]
        faaliyet_ids = [int(f["id"]) for f in faaliyetler]
        if faaliyet_ids:
            girdi_tablosu = _girdiler(db, cid, faaliyet_ids)
        user_ids = sorted({
            int(f["operator_user_id"]) for f in faaliyetler
            if f["operator_user_id"] is not None
        })
        if user_ids:
            kullanici_tablosu = _kullanicilar(db, cid, user_ids)
        machine_ids = sorted({
            int(f["machine_id"]) for f in faaliyetler if f["machine_id"] is not None
        })
        if machine_ids:
            makine_tablosu = _makineler(db, cid, machine_ids)

        hasatlar = [
            h for h in _hasatlar(db, cid, sezon_ids)
            if not _kapsam_disi(_gun(h["harvested_on"]), suzgec)
        ]

    faaliyet_satirlari: list[list[Any]] = []
    for f in faaliyetler:
        s = sezon_tablosu[int(f["season_id"])]
        makine = makine_tablosu.get(
            int(f["machine_id"]) if f["machine_id"] is not None else -1, {}
        )
        makine_adi = " ".join(
            parca for parca in (makine.get("brand"), makine.get("model")) if parca
        )
        kimlik = [
            _metin(s["farm_code"]), _metin(s["farm_name"]),
            _metin(s["parcel_code"]), _metin(s["parcel_name"]),
            _metin(s["block_no"]), _metin(s["parcel_no"]),
            _metin(s["neighborhood"]), _metin(s["parcel_city"]),
            _metin(s["parcel_district"]), _sayi(s["area_decare"]),
            int(s["season_year"]), _metin(s["crop"]), _metin(s["variety"]),
            _sayi(s["planted_area_decare"]),
        ]
        uygulama = [
            _gun_metni(_yerel_gun(f["performed_at"])),
            _yerel_saat(f["performed_at"]),
            FAALIYET_TURLERI.get(str(f["activity_type"]), _metin(f["activity_type"])),
            _sayi(f["applied_area_decare"]),
        ]
        kuyruk = [
            f["reentry_interval_days"],
            f["preharvest_interval_days"],
            kullanici_tablosu.get(
                int(f["operator_user_id"]) if f["operator_user_id"] is not None else -1,
                "",
            ),
            makine_adi,
            _metin(makine.get("registration_number")),
            _metin(f["area_override_reason"]),
            _metin(f["notes"]),
        ]
        girdiler = girdi_tablosu.get(int(f["id"]), [])
        if not girdiler:
            # Girdisiz faaliyet GERÇEK bir kayıttır (toprak işleme, sulama).
            # Satırı atlamak, yapılmış bir işi çizelgeden silmek olurdu.
            faaliyet_satirlari.append(kimlik + uygulama + ["", "", "", "", ""] + kuyruk)
            continue
        for g in girdiler:
            faaliyet_satirlari.append(
                kimlik + uygulama + [
                    _metin(g["input_name"]), _sayi(g["quantity"]), _metin(g["unit"]),
                    _sayi(g["dose"]), _metin(g["dose_unit"]),
                ] + kuyruk
            )

    hasat_satirlari: list[list[Any]] = []
    for h in hasatlar:
        s = sezon_tablosu[int(h["season_id"])]
        hasat_satirlari.append([
            _metin(s["farm_code"]), _metin(s["farm_name"]),
            _metin(s["parcel_code"]), _metin(s["parcel_name"]),
            _metin(s["block_no"]), _metin(s["parcel_no"]),
            int(s["season_year"]), _metin(s["crop"]), _metin(s["variety"]),
            _gun_metni(h["harvested_on"]),
            _sayi(h["quantity"]), _metin(h["unit"]),
            _sayi(h["harvested_area_decare"]), _metin(h["quality_grade"]),
            _sayi(h["moisture_percent"]), _metin(h["safety_warning"]),
            _metin(h["safety_override_reason"]), _metin(h["notes"]),
        ])

    toplam = len(faaliyet_satirlari) + len(hasat_satirlari)
    if toplam > AZAMI_SATIR:
        raise DefterHatasi(
            f"Çizelge {AZAMI_SATIR} satır sınırını aşıyor ({toplam}). "
            "Süzgeci daraltın (sezon ya da tarih aralığı verin)."
        )

    return {
        "note": CIZELGE_NOTU,
        "title": CIZELGE_ADI,
        "filters": {
            ad: (deger.isoformat() if isinstance(deger, date) else deger)
            for ad, deger in suzgec.items()
            if deger is not None
        },
        "activity_headers": list(FAALIYET_BASLIKLARI),
        "harvest_headers": list(HASAT_BASLIKLARI),
        "activity_rows": faaliyet_satirlari,
        "harvest_rows": hasat_satirlari,
        "row_count": toplam,
    }

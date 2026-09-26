"""KİRACI DIŞA AKTARIMI — tek firmanın TÜM verisinin akan zip'i.

NEDEN PLATFORM YEDEĞİ BUNU KARŞILAMIYOR
---------------------------------------
``routers/platform_backups.py`` ``pg_dump`` ile KÜMENİN TAMAMINI alır: her
firma, her kiracı, tek dosyada. Bir kiracının "benim verimi ver" talebi bununla
karşılanamaz — çünkü o dosya BAŞKA KİRACILARIN verisini de taşır ve teslim
edilemez. Bu uç tam tersi bir mekanizmadır: TEK firmanın satırları, kiracı
yüklemi HER tabloda açıkça yazılı olarak.

TABLO SIRASI TÜRETİLİR, YAZILMAZ
--------------------------------
İçerik topolojik sırayla yazılır: bir tablo, BAĞLI OLDUĞU tablolardan sonra
gelir. Sıra ELLE YAZILMIŞ BİR LİSTE DEĞİL — çalışma zamanında yansıtılan
(reflect) yabancı anahtar grafiğinden ``sort_tables`` ile türetilir. Elle
yazılmış bir liste, yeni bir göç tablo eklediğinde SESSİZCE eksik kalırdı;
türetilmiş sıra ise yeni tabloyu kendiliğinden yerine koyar.

Kendine referans veren tablolar (``animals.father_id``/``mother_id``,
``payment_allocations`` iki sütun, ``receivable_charge_documents``) grafikte
DÖNGÜ yaratmaz: ``sort_tables`` kendi kendine olan kenarı yok sayar, çünkü
satırlar zaten tek tabloya yazılır ve içe aktarımda sıra tablo İÇİNDEDİR.

TUTARLILIK
----------
Tüm okuma TEK BAĞLANTIDA, TEK İŞLEMDE olur. PostgreSQL'de işlem
``REPEATABLE READ`` + ``READ ONLY``: aksi hâlde 102 tablo ayrı ayrı okunurken
araya giren bir yazma, dışa aktarımın İÇİNDE tutarsız bir kesit bırakırdı
(ör. ``orders`` satırı var, ``order_items`` satırları yok). ``app/db.py``
motorunda ``isolation_level`` YOK, yani varsayılan ``READ COMMITTED`` — bu uç
kendi seviyesini bağlantı düzeyinde AÇIKÇA yükseltir. SQLite'ta tek bağlantının
tek işlemi aynı garantiyi verir.

BELLEK
------
Yanıt AKAR. Zip, bellekte kurulup sonra gönderilmez; ``zipfile`` konumlanamayan
(unseekable) bir akışa yazar ve üretilen her parça anında teslim edilir.
Satırlar ``stream_results`` + ``partitions`` ile parça parça çekilir, bu yüzden
tek bir tablonun tamamı da belleğe alınmaz.

HATA SINIRI — ÖLÇÜLEN PROTOKOL GERÇEĞİ
--------------------------------------
``SonluOlmayanSayiError`` ADI KONMUŞ bir hatadır ve ``main.py``de kayıtlı
işleyici onu 500 + ``{"detail": ..., "code": "EXPORT_NON_FINITE_NUMBER"}``
gövdesine çevirir. AMA bu çeviri yalnız yanıt BAŞLAMADAN ÖNCE mümkündür:
Starlette ``StreamingResponse``ta ``http.response.start``ı üreteci
DÖNDÜRMEDEN gönderir, yani durum kodu 200'de KİLİTLENİR. Akış başladıktan
sonra doğan hata 500'e çevrilemez — istemci YARIM bir zip alır ve merkezi
dizin yazılmadığı için o dosya AÇILAMAZ (sessizce geçerli görünen bir zip
DEĞİL). Bu, HTTP'nin sınırıdır, kodun tercihi değil.

``manifest.json`` zip'in SONUNA yazılır. Satır sayıları ancak satırlar
akıtıldıktan sonra bilinir; manifesti başa koymak, sayıları öğrenmek için tüm
gövdeyi bellekte tutmayı gerektirirdi — yani akışı iptal ederdi. Zip'in merkezi
dizini zaten dosyanın sonundadır, okuyucu girdilere sırasız erişir.
"""
from __future__ import annotations

import base64
import json
import zipfile
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import MetaData, Table, select
from sqlalchemy.engine import Connection
from sqlalchemy.schema import sort_tables

from ..arama_katli import katli_sutun_mu
from ..activity_log import log_activity
from ..auth import users, utcnow
from ..config import settings
from ..db import SessionLocal, engine
from ..disa_aktarim_errors import SonluOlmayanSayiError
from ..tenancy import company_id as aktif_firma

router = APIRouter(prefix="/company/export", tags=["Kiracı Dışa Aktarımı"])

#: Satırlar veritabanından bu büyüklükte parçalar hâlinde çekilir. Amaç tek bir
#: tablonun tamamını belleğe almamak; sayı, sürücü tur sayısı ile bellek
#: arasında ölçülmüş bir dengedir.
_PARCA = 500

#: Zip'e teslim edilmeden önce biriktirilecek en fazla bayt. Küçük tutulur:
#: bu tampon, akışın bellek tavanının kendisidir.
_TAMPON_ESIGI = 256 * 1024

#: E-posta haritası için ``app_users`` bu büyüklükte ``IN (...)`` parçalarıyla
#: okunur; bağlı parametre sayısı sürücü tavanına çarpmasın.
_KIMLIK_PARCASI = 500


class _AkanTampon:
    """``zipfile`` için konumlanamayan yazma hedefi.

    ``seek``/``tell`` BİLEREK tanımlanmadı: ``zipfile`` bunların yokluğunda
    akışı konumlanamaz kabul eder ve veri boyutlarını yerel başlığa geri dönüp
    yazmak yerine veri tanımlayıcısı (data descriptor) kullanır. Tanımlanmış
    olsalardı zipfile geri dönmeye çalışır ve akış bozulurdu.
    """

    def __init__(self) -> None:
        self._tampon = bytearray()

    def write(self, veri: bytes) -> int:
        self._tampon.extend(veri)
        return len(veri)

    def flush(self) -> None:  # pragma: no cover - zipfile sözleşmesi
        return None

    def bosalt(self) -> bytes:
        parca = bytes(self._tampon)
        del self._tampon[:]
        return parca

    def bekleyen(self) -> int:
        return len(self._tampon)


def _seri(deger: Any, tablo: str, sutun: str) -> Any:
    """Bir sütun değerini JSON'a çevrilebilir hâle getirir.

    Kayıplı hiçbir dönüşüm yok: ``Decimal`` METİN olur (float'a düşseydi
    kuruş sessizce kayardı), ``datetime`` UTC ISO-8601 olur, ``bytes`` base64
    olur, JSON sütununun ``dict``/``list`` değeri OLDUĞU GİBİ kalır.

    H49 — JSON SÜTUNLARI GERÇEK JSON. 5.1a'da ``dict``/``list`` son daldaki
    ``str()``e düşüyor ve Python repr'i (``{'a': True, 'b': None}``) yazılıyordu:
    bu JSON DEĞİL, ndjson satırı açılınca değer bir METİN çıkıyordu. Ölçüldü
    (0086 şeması, yansımadan, SQLite ve PostgreSQL): ``str()`` dalına düşen TEK
    tip ``JSON`` → ``dict``, dört sütun (``supplier_import_profiles.column_map``
    ve ``.page_sections``, ``supplier_price_import_lines.raw``,
    ``supplier_price_imports.error_detail``).

    Geri yükleme ESKİ biçimi okumayı SÜRDÜRÜR (``kiraci_geri_yukleme._deseri``
    içindeki ``ast.literal_eval`` dalı): H49'dan önce alınmış arşivler repr
    taşır ve onları reddetmek kiracının yedeğini çöpe atmak olurdu.
    """
    if deger is None or isinstance(deger, (str, bool, int)):
        return deger
    if isinstance(deger, (dict, list)):
        # Sürücü JSON sütununu çözümlenmiş verir; içerik JSON'dan geldiği için
        # yalnız JSON-yerli tipler taşır ve ``json.dumps`` onu olduğu gibi yazar.
        return deger
    if isinstance(deger, Decimal):
        # SONLULUK DENETİMİ. ``Decimal`` NaN/Infinity taşıyabilir ve
        # ``json.dumps`` onları standart DIŞI ``NaN``/``Infinity`` sözcükleriyle
        # yazar; katı bir okuyucu dosyanın tamamını reddeder. Sessizce null'a
        # çevirmek veriyi kaybederdi — burada DURUYORUZ.
        #
        # DENETİM NEDEN YALNIZ BURADA: şemada ikili kayan sayı sütunu YOK
        # (ölçüldü: `Float`/`REAL` sütun sayısı 0; para ve miktar sütunlarının
        # hepsi `NUMERIC`, sürücü onları `Decimal` verir). Ayrıca
        # `test_v2_9_decimal_contract.py::test_runtime_financial_code_does_not_use_binary_float_types`
        # `app/` içinde `float` ADININ GEÇMESİNİ bile yasaklıyor — muafiyeti de
        # yok. Yani ikili kayan sayı dalı hem konusuz hem yasaktı.
        if not deger.is_finite():
            raise SonluOlmayanSayiError(tablo, sutun, deger)
        # ``format(..., "f")`` bilimsel gösterimi ("1E+2") engeller.
        return format(deger, "f")
    if isinstance(deger, datetime):
        # Naive damgalar veritabanında UTC olarak saklanır (``utcnow`` ile
        # yazılırlar); okuyucunun yerel saat varsayması diye açıkça işaretlenir.
        if deger.tzinfo is None:
            deger = deger.replace(tzinfo=timezone.utc)
        return deger.astimezone(timezone.utc).isoformat()
    if isinstance(deger, (date, time)):
        return deger.isoformat()
    if isinstance(deger, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(deger)).decode("ascii")
    return str(deger)


def _kiraci_tablolari(md: MetaData) -> frozenset[str]:
    """Kiracı tabloları ŞEMADAN türetilir: ``company_id`` sütunu olan her tablo.

    ELLE YAZILMIŞ LİSTE YOK. Bir liste, yeni bir göç kiracı tablosu eklediğinde
    sessizce eski kalır ve o tablonun satırları dışa aktarımdan DÜŞERDİ —
    kiracıya eksik veri teslim etmek, hata vermekten kötüdür.

    ``tests/test_tenant_scoping_guard.py``taki ``TENANT_TABLES`` bu kümenin
    KOPYASI DEĞİL, BAĞIMSIZ İKİNCİ BİR TANIĞIDIR; testi o listeyi İTHAL EDİP
    burada türetilenle eşitliğini doğrular (ölçüldü: 102 = 102, sıfır sapma).
    İki taraf ayrıldığı anda kapı kırılır.
    """
    return frozenset(ad for ad, tablo in md.tables.items() if "company_id" in tablo.c)


def _yansit(conn: Connection) -> MetaData:
    md = MetaData()
    md.reflect(bind=conn)
    return md


def _tablo_sirasi(md: MetaData, adlar: frozenset[str]) -> list[Table]:
    """Kiracı tablolarını topolojik sıraya dizer."""
    eksik = sorted(adlar - set(md.tables))
    if eksik:
        raise HTTPException(
            500,
            f"Dışa aktarım durduruldu: şemada bulunamayan kiracı tabloları: {eksik}",
        )
    return list(sort_tables([md.tables[ad] for ad in adlar]))


def _sirali(tablo: Table):
    """Satırları BİRİNCİL ANAHTAR sırasıyla veren seçim.

    Sıra ŞART: sırasız bir okuma, aynı verinin iki dışa aktarımında farklı
    satır sırası üretir ve iki dosyayı karşılaştırılamaz kılar.
    """
    secim = select(tablo)
    for sutun in tablo.primary_key.columns:
        secim = secim.order_by(sutun)
    return secim


def _satirlar(conn: Connection, tablo: Table, cid: int) -> Iterator[dict[str, Any]]:
    secim = _sirali(tablo).where(tablo.c.company_id == cid)
    sonuc = conn.execution_options(stream_results=True).execute(secim)
    for parca in sonuc.partitions(_PARCA):
        for satir in parca:
            # H75: `<kolon>_katli` TURETILMIS arama sutunlaridir; zip'e
            # YAZILMAZ. Geri yukleme onlari kaynak kolonlardan yeniden hesaplar
            # (`kiraci_geri_yukleme.geri_yukle` -> `arama_katli.kiraci_katli_esitle`).
            yield {k: v for k, v in satir._mapping.items() if not katli_sutun_mu(k)}


def _kullanici_epostalari(conn: Connection, kimlikler: set[int]) -> list[dict[str, Any]]:
    """Firmanın ÜYE kimliklerini e-postaya eşler — BİLEREK EN AZ.

    YALNIZ ``id`` ve ``email`` seçilir; ``app_users``ın başka hiçbir sütunu
    (parola özeti dahil) okunmaz, tablonun tamamı da dökülmez. Amaç tek: geri
    yüklemede üyelikleri gerçek kişilere yeniden eşleyebilmek. E-posta kişisel
    veridir; eşleme için gereken en az kimlik odur.

    KAPSAM SINIRI ÜYELİK KÜMESİDİR (karar #115, H49). ``app_users`` çekirdek bir
    tablodur ve FİRMA SINIRI YOKTUR; kiracı kapısı onu korumaz, tek koruma bu
    fonksiyona verilen kimlik kümesidir. Küme "A'nın satırlarında geçen kimlik"
    olsaydı doğruluğu verinin temizliğine bağlı kalırdı: bozuk ya da hatalı bir
    satıra düşmüş başka firmanın kullanıcı kimliği o kişinin e-postasını A'nın
    zip'ine taşırdı. Bu yüzden ``kimlikler`` = dışa aktarılan
    ``user_company_memberships`` satırlarının ``user_id``leridir (aynı okuma
    kesiti, ek sorgu yok). Üyelik satırı kendisi de bir kullanıcı referansıdır;
    yani "anılan ∩ A'nın üyeleri" kesişimi tam olarak bu kümedir.

    BİLİNÇLİ BEDEL — GENİŞLETMEYİN: A'dan AYRILMIŞ (üyeliği silinmiş) ama eski
    kayıtlarda ``created_by`` vb. olarak duran kişi e-postasız kalır. Bu bir
    eksik DEĞİL: geri yükleme onu kimlikle eşlemeye devam eder; e-posta kolaylık,
    anahtar değil. Kümeyi FK/yumuşak kullanıcı sütunlarına genişletmek yukarıdaki
    sızıntıyı geri getirir.

    Boş kümede sorgu HİÇ KOŞMAZ. Satırı olmayan (silinmiş) kimlik haritada yer
    almaz: eşlenecek kişi yok.
    """
    if not kimlikler:
        return []
    sirali = sorted(kimlikler)
    harita: list[dict[str, Any]] = []
    for i in range(0, len(sirali), _KIMLIK_PARCASI):
        parca = sirali[i : i + _KIMLIK_PARCASI]
        secim = (
            select(users.c.id, users.c.email)
            .where(users.c.id.in_(parca))
            .order_by(users.c.id)
        )
        for satir in conn.execute(secim):
            harita.append({"id": int(satir.id), "email": satir.email})
    return harita


def _depo_koku() -> Path:
    return Path(settings.sungur_data_dir).resolve()


def _ek_yolu(kok: Path, saklanan: str) -> Path | None:
    """Saklanan göreli yolu çözer; kökün DIŞINA çıkanı reddeder."""
    hedef = (kok / saklanan).resolve()
    if kok != hedef and kok not in hedef.parents:
        return None
    return hedef


def _uygulama_surumu() -> str:
    from ..main import app as _app

    return str(getattr(_app, "version", "") or "bilinmiyor")


def _sema_seviyesi(conn: Connection, md: MetaData) -> str | None:
    """Alembic başını VERİTABANINDAN okur.

    Koda gömülmez: gömülü bir sürüm, göç uygulandığında sessizce yalan söyler
    ve dosyayı yanlış şemayla etiketlerdi.
    """
    tablo = md.tables.get("alembic_version")
    if tablo is None:
        return None
    return conn.execute(select(tablo.c.version_num)).scalars().first()


def _islem_baslat(conn: Connection) -> None:
    """Okumanın tamamını TEK ve TUTARLI bir kesite bağlar."""
    if conn.dialect.name == "postgresql":
        conn.exec_driver_sql(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )


def _gunlukle(request: Request, cid: int, tablo_sayisi: int) -> None:
    """Dışa aktarım başına TEK aktivite satırı.

    KESİTTEN SONRA yazılır: önce yazılsaydı satır, dışa aktarımın KENDİ
    ``activity_logs`` dosyasına düşerdi ve "boş firma sıfır satır verir"
    iddiası yalan olurdu. Kendi işleminde commit edilir; dışa aktarım işlemi
    salt-okunurdur ve yazamaz.
    """
    kullanici = getattr(request.state, "user", {}) or {}
    with SessionLocal.begin() as db:
        log_activity(
            db,
            cid,
            int(kullanici["id"]) if kullanici.get("id") is not None else None,
            "company.exported",
            "backup",
            None,
            "Kiracı verisi dışa aktarıldı",
            {"table_count": tablo_sayisi},
            correlation_id=getattr(request.state, "request_id", None),
        )


def _uret(request: Request, cid: int) -> Iterator[bytes]:
    tampon = _AkanTampon()
    satir_sayilari: dict[str, int] = {}
    eksik_ekler: list[dict[str, Any]] = []
    ek_sayisi = 0
    # H49 e-posta haritasının SINIRI: bu firmanın dışa aktarılan üyelikleri.
    uye_kimlikleri: set[int] = set()

    with engine.connect() as conn:
        _islem_baslat(conn)
        md = _yansit(conn)
        # İlk okuma kesiti SABİTLER: PostgreSQL'de REPEATABLE READ anlık
        # görüntüsü bu ifadeyle alınır, sonraki yazmalar görünmez.
        sema = _sema_seviyesi(conn, md)
        sirali_tablolar = _tablo_sirasi(md, _kiraci_tablolari(md))

        def teslim(zorla: bool = False) -> Iterator[bytes]:
            if zorla or tampon.bekleyen() >= _TAMPON_ESIGI:
                parca = tampon.bosalt()
                if parca:
                    yield parca

        with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            firmalar = md.tables["companies"]
            firma = (
                conn.execute(select(firmalar).where(firmalar.c.id == cid))
                .mappings()
                .first()
            )
            if firma is None:
                raise HTTPException(404, "Aktif firma bulunamadı")
            zf.writestr(
                f"companies/{cid}.json",
                json.dumps(
                    {k: _seri(v, "companies", k) for k, v in dict(firma).items()},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            yield from teslim()

            for tablo in sirali_tablolar:
                sayi = 0
                uyelik_tablosu = tablo.name == "user_company_memberships"
                with zf.open(f"tables/{tablo.name}.ndjson", "w") as akis:
                    for satir in _satirlar(conn, tablo, cid):
                        if uyelik_tablosu and satir.get("user_id") is not None:
                            uye_kimlikleri.add(int(satir["user_id"]))
                        temiz = {k: _seri(v, tablo.name, k) for k, v in satir.items()}
                        akis.write(
                            (json.dumps(temiz, ensure_ascii=False) + "\n").encode("utf-8")
                        )
                        sayi += 1
                        for parca in teslim():
                            yield parca
                satir_sayilari[tablo.name] = sayi
                yield from teslim()

            # --- EKLER --------------------------------------------------
            # Silinmiş satırların ekleri de DAHİL: dosya diskte duruyor ve
            # kiracının verisi. Manifest bunu açıkça bayrakla söyler.
            ekler = md.tables["work_order_attachments"]
            kok = _depo_koku()
            for satir in _satirlar(conn, ekler, cid):
                saklanan = satir.get("storage_path")
                is_emri = satir.get("work_order_id")
                ad = Path(str(saklanan or "")).name
                hedef = _ek_yolu(kok, str(saklanan)) if saklanan else None
                if hedef is None or not hedef.is_file():
                    # DOSYA YOKSA DIŞA AKTARIM DÜŞMEZ. Diskteki eksiklik
                    # veritabanı satırını geçersiz kılmaz; sessizce atlamak ise
                    # eksikliği gizlerdi. Manifest'e yazılır.
                    eksik_ekler.append(
                        {
                            "id": satir.get("id"),
                            "work_order_id": is_emri,
                            "storage_path": str(saklanan) if saklanan else None,
                        }
                    )
                    continue
                with zf.open(f"attachments/{is_emri}/{ad}", "w") as akis:
                    with hedef.open("rb") as kaynak:
                        while True:
                            blok = kaynak.read(64 * 1024)
                            if not blok:
                                break
                            akis.write(blok)
                            for parca in teslim():
                                yield parca
                ek_sayisi += 1
                yield from teslim()

            manifest = {
                "schema_revision": sema,
                "exported_at": utcnow().astimezone(timezone.utc).isoformat(),
                "company_id": cid,
                "table_order": [t.name for t in sirali_tablolar],
                "row_counts": satir_sayilari,
                "attachment_count": ek_sayisi,
                "attachments_include_deleted": True,
                "missing_attachments": eksik_ekler,
                "app_version": _uygulama_surumu(),
                # H49: kimlik -> e-posta, YALNIZ bu iki alan ve YALNIZ bu
                # firmanın üyeleri (sınırın gerekçesi ``_kullanici_epostalari``da).
                # Geri yükleme bugün kimlikle eşler; e-posta kolaylıktır.
                "user_emails": _kullanici_epostalari(conn, uye_kimlikleri),
            }
            zf.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )

        yield from teslim(zorla=True)

    _gunlukle(request, cid, len(satir_sayilari))


@router.get("")
def disa_aktar(request: Request) -> StreamingResponse:
    """Aktif firmanın tüm verisini akan bir zip olarak döndürür.

    ÜYELİK ve ROL kapıları BU FONKSİYONDA DEĞİL, ara katmandadır ve ikisi
    ayrı ayrı düşer: üye olmayan ``resolve_company`` ile
    (``COMPANY_ACCESS_DENIED``), yetkisi yeten rolü olmayan ise
    ``required_permission`` → ``has_permission`` ile (``PERMISSION_DENIED``).
    İzin adı ``__admin_only__``: dosyanın sonundaki deny-by-default
    nöbetçisiyle AYNI ad. Hiçbir rol tablosunda yazılı değildir; yalnız
    ``admin`` rolünün ``"*"`` jokeri onu taşır — yani var olan EN YÜKSEK rol
    (``ROLE_RANK``: admin=100).
    """
    cid = aktif_firma(request)
    ad = f"kiraci-{cid}-{utcnow().strftime('%Y%m%dT%H%M%SZ')}.zip"
    return StreamingResponse(
        _uret(request, cid),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{ad}"'},
    )

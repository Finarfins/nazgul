"""GENEL İDEMPOTENSİ: `Idempotency-Key` başlığının TEK yorumu (5.4b).

Şema `alembic/versions/20260909_0076_idempotency_keys.py`de; bağlama noktası
`app/main.py`nin `security_and_audit` ara katmanıdır. Bu modül HİÇBİR uç
tanımlamaz ve hiçbir uca özel şey bilmez — bildiği tek şey HTTP'dir.

--- NEREDE DURUYOR: KİRACI ÇÖZÜMÜNDEN SONRA ------------------------------

İddia (`claim`) `security_and_audit` içinde, `resolve_company` BAŞARDIKTAN
SONRA koşar. Sıra KEYFİ DEĞİL, üç şey ona bağlı:

  * Anahtarın kapsamı `(company_id, user_id, key)`; firma çözülmeden o
    üçlünün İKİ bileşeni YOKTUR.
  * `must_change_password` 403'ü ve yetki 403'ü ÖNCE düşer. Zorunlu parola
    rotasyonuna takılan bir istek bir anahtar İDDİA ETMEMELİDİR: iddia
    edilseydi kullanıcı parolasını değiştirdikten sonra AYNI anahtarla
    yeniden denediğinde 409 alırdı.
  * CSRF ve kimlik kapıları da ÖNCE düşer; kimliksiz bir istek kimin adına
    anahtar iddia edeceğini bilemez.

--- İKİ AYRI KISA İŞLEM (TEK TRANSACTION MÜMKÜN DEĞİL) --------------------

Ara katman işleyicinin transaction'ını PAYLAŞAMAZ: işleyici kendi
`SessionLocal`ını `Depends` üzerinden alır, kendi sınırında commit eder ve o
sınır ara katmana GÖRÜNMEZ. Bu yüzden İKİ ayrı kısa işlem var:

  1. İDDİA — işleyiciden ÖNCE, kendi transaction'ında commit edilir.
  2. TAMAMLAMA — işleyiciden SONRA, kendi transaction'ında commit edilir.

PENCERE AÇIK VE YAZILIYOR: işleyici commit ettikten sonra tamamlama yazımı
düşerse (süreç öldü, veritabanı koptu) satır `processing` kalır ve o anahtar
`expires_at`e (24 saat) kadar 409 `IDEMPOTENCY_IN_PROGRESS` döner. Alternatifi
— iddiayı işleyiciyle aynı transaction'a sokmaya çalışmak — MÜMKÜN DEĞİL;
satırı düşürmek ise TAMAMLANMIŞ bir yazmanın ikinci kez koşmasına izin
vermekti. İki kötüden görünür olanı seçildi.

BU TURDA ZAMANLANMIŞ SÜPÜRGE YOK (ÖLÇÜLMEDİ, YAPILMADI). Temizlik TEMBEL ve
ANAHTAR BAŞINADIR: iddia etmeden önce tam olarak O anahtarın süresi dolmuş
satırı silinir. Hiç tekrar edilmeyen anahtarların satırları defterde kalır.

--- HANGİ İSTEKLER: KAPALI KÜME -------------------------------------------

  * `GET`/`HEAD`/`OPTIONS` defterin ADINI BİLE ANMAZ (okuma zaten idempotent).
  * `Idempotency-Key` başlığı YOKSA hiçbir şey olmaz — davranış değişikliği
    OPT-IN'dir ve mevcut hiçbir istemciyi kımıldatmaz.
  * `PUBLIC_API` uçları kiracı çözümüne HİÇ girmez, yani buraya ulaşmaz.
  * `ATLANAN_UCLAR` — KENDİ `Idempotency-Key` defterini tutan ON BİR uç.

--- ATLANAN ON BİR UÇ: NEDEN VE NASIL ÖLÇÜLDÜ ----------------------------

Depoda ölçüldü (`app/routers/` altında `idempotency-key` başlık okuması ya da
`Header(alias="Idempotency-Key")` bağlaması): YEDİ dosya, ON BİR uç. Bu uçlar
başlığı KENDİLERİ okuyor ve kendi defterlerine yazıyor. Ara katman onları
atlamasaydı İKİ defter aynı anahtarı iddia ederdi ve ikisinin çakışma cevabı
FARKLI olurdu (409 mu 422 mi, hangi gövdeyle) — yani aynı istek iki farklı
sözleşmeye uyar hâle gelirdi.

`app/routers/farm.py` ve `app/routers/field.py` bu listede DEĞİL ve olmaması
ÖLÇÜLMÜŞ bir karardır: onların idempotensisi `operation_id` GÖVDE alanındadır,
`Idempotency-Key` başlığını HİÇ OKUMAZLAR. Orada ara katman EKLEMELİDİR, çünkü
başlık gönderen bir istemci ile gövde alanı gönderen bir istemci ÇAKIŞMAZ; iki
koruma da kendi anahtarına bakar.

Şablonlar OpenAPI şemasında GERÇEKTEN VAR olduğu `tests/test_54b_idempotency.py`
tarafından doğrulanıyor: bir uç yeniden adlandırılırsa liste SESSİZCE boşa
düşmez, kapı KIRMIZI olur.

--- CEVABIN SAKLANMASI: SINIR VE SINIRIN DIŞI ----------------------------

Saklanan şey CEVABIN KENDİSİDİR (durum kodu + gövde), iş sonucu değil.

  * `status >= 500` SAKLANMAZ ve satır SİLİNİR — tekrar denemek GÜVENLİ
    kalmalı; saklanmış bir 5xx istemciyi sonsuza kadar o hataya mahkûm ederdi.
  * Gövde YALNIZ `application/json` ise ve `64 KiB`ı aşmıyorsa saklanır. Boş
    gövde (204) da saklanır ve boş olarak oynatılır.
  * BUNUN DIŞINDAKİ HER CEVAP (dosya indirme, PDF, 64 KiB'tan büyük JSON)
    `completed` yazılır ama `response_body` NULL kalır. O anahtarın TEKRARI
    409 `IDEMPOTENCY_RESPONSE_NOT_STORED` alır — işleyici İKİNCİ KEZ
    KOŞTURULMAZ. Gerekçe: tekrar koşturmak, bu modülün var olma sebebini
    (yan etkinin bir kez olması) ihlal ederdi; "cevabı veremiyorum" demek,
    "işi ikinci kez yapıyorum" demekten iyidir.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.responses import Response

from .db import SessionLocal

#: Başlık adı KÜÇÜK HARFLE aranır: HTTP başlıkları büyük/küçük harf duyarsızdır
#: ve Starlette'in `Headers` eşlemesi anahtarları küçük harfe indirir.
BASLIK = "idempotency-key"

#: Tekrar oynatılan cevabın işareti. İstemci bunu görürse isteğinin İKİNCİ KEZ
#: UYGULANMADIĞINI bilir; görmezse cevap TAZE demektir.
TEKRAR_BASLIGI = "Idempotent-Replayed"

#: Göçün `key` sütunu ile AYNI genişlik. Uzun anahtar SESSİZCE KIRPILMAZ —
#: kırpılsaydı iki farklı anahtar aynı satıra düşerdi.
AZAMI_ANAHTAR = 128

#: 24 saat. Bir ağ zaman aşımından sonraki tekrar denemeler dakikalar içinde
#: gelir; 24 saat, gece boyunca kuyrukta bekleyen bir mobil istemciyi de
#: kapsayacak kadar geniş, defteri sonsuza kadar büyütmeyecek kadar dardır.
YASAM_SURESI = timedelta(hours=24)

#: Saklanan gövdenin üst sınırı. Aşan cevap SAKLANMAZ — gerekçe başlıkta.
AZAMI_GOVDE = 64 * 1024

#: TAMPONLANACAK İSTEK GÖVDESİNİN ÜST SINIRI ve NEDEN GEREKLİ OLDUĞU —
#: ÖLÇÜLDÜ, VARSAYILMADI. `security_and_audit` ara katmanların EN DIŞTAKİDİR
#: (`app.add_middleware` listeye BAŞTAN ekler; sıra: bu ara katman -> CORS ->
#: `RequestBodyLimitMiddleware`). Yani gövde burada okunduğunda GÖVDE SINIRI
#: KAPISI HENÜZ KOŞMAMIŞTIR ve 10 MiB'lık bir içe aktarma isteği reddedilmeden
#: ÖNCE belleğe alınırdı.
#:
#: Çare, sınırı BURADA da sormaktır ve sınır BİLEREK DAR: idempotensi bir
#: TEKRAR GÖNDERİM korumasıdır ve tekrar gönderilen şey bir JSON gövdesidir,
#: 10 MiB'lık bir Excel dosyası değil. Aşan istek SESSİZCE KORUMASIZ
#: BIRAKILMAZ — 413 ile REDDEDİLİR; istemci başlığı düşürerek aynı isteği
#: gönderebilir ve o zaman gövde sınırı kapısı kendi işini yapar.
AZAMI_ISTEK_GOVDESI = 1024 * 1024

#: Yalnız bu tip saklanır. Ayrımın sebebi başlıkta.
SAKLANAN_TIP = "application/json"

YAZAN_METOTLAR = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: KENDİ `Idempotency-Key` defterini tutan uçlar — gerekçe başlıkta. Şablon
#: biçimindedir çünkü liste OKUNABİLİR olmak zorunda; ara katman SOMUT yol
#: gördüğü için aşağıda desene çevriliyor.
#:
#: YEDİ DOSYA, ON BİR UÇ. Liste ELLE YAZILMADI, ÖLÇÜLDÜ: kapı
#: (`tests/test_54b_idempotency.py`) `app/routers/` altında `idempotency-key`
#: geçen dosyaları tarıyor ve kümenin BÜYÜMESİ kırmızı oluyor. İlk ölçüm BEŞ
#: dosya / DOKUZ uç demişti ve YANLIŞTI — `finance.py` ile `work_orders.py`
#: kapıyı KIRDI, o yüzden buradalar.
ATLANAN_UCLAR: tuple[tuple[str, str], ...] = (
    # app/routers/machines.py :: create_machine
    ("POST", "/api/machines"),
    # app/routers/pos.py :: create_sale
    ("POST", "/api/pos/sale"),
    # app/routers/supplier_prices.py :: create_reorder_drafts
    ("POST", "/api/purchase-comparison/reorder-drafts"),
    # app/routers/payment_allocations.py :: üç uç
    ("POST", "/api/payment-allocations/manual"),
    ("POST", "/api/payment-allocations/reallocation"),
    ("POST", "/api/payment-allocations/{allocation_id}/reversal"),
    # app/routers/late_fees.py :: üç uç
    ("POST", "/api/finance/late-fees/charges"),
    ("POST", "/api/finance/late-fees/charges/{document_id}/post"),
    ("POST", "/api/finance/late-fees/charges/{document_id}/reversal"),
    # app/routers/finance.py :: create_payment — başlığı KENDİSİ okuyor
    # (`request.headers.get('idempotency-key', '')`) ve `payment_idempotency`
    # defterine veriyor. VARSAYILANI BOŞ DİZGİ olması onu bu listeden
    # ÇIKARMAZ: başlığın o yoldaki ANLAMINI o uç tanımlıyor ve iki defterin
    # aynı anahtarı iddia etmesi tam olarak kaçınılan şey.
    ("POST", "/api/payments"),
    # app/routers/work_orders.py :: reverse_work_order_receivable — başlık
    # ZORUNLU (`Header(alias="Idempotency-Key")`) ve
    # `receivable_charge_idempotency` defterine gidiyor.
    ("POST", "/api/work-orders/{work_order_id}/receivable/reverse"),
)


def _desen(sablon: str) -> re.Pattern[str]:
    """Şablonu SOMUT yola uyan desene çevirir.

    `{...}` parçası `[^/]+` olur: yol parçası SINIRINI aşamaz, yani
    `/api/payment-allocations/1/reversal` uyar ama
    `/api/payment-allocations/1/reversal/extra` UYMAZ.
    """
    parcalar = re.split(r"\{[^/{}]+\}", sablon)
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in parcalar) + "$")


_ATLANAN_DESENLER: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (metot, _desen(sablon)) for metot, sablon in ATLANAN_UCLAR
)


class IdempotensiReddi(Exception):
    """Ara katmanın istek işlenmeden ÖNCE verdiği ret."""

    def __init__(self, status_code: int, detail: str, code: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


@dataclass(frozen=True)
class Iddia:
    """İddia edilmiş anahtar. Tamamlama ve iptal bunu geri ister."""

    company_id: int
    user_id: int
    key: str


@dataclass(frozen=True)
class Tekrar:
    """Saklanmış cevap. `body` None ise cevap SAKLANMAMIŞ demektir."""

    status_code: int
    body: str | None


def kendi_defterini_tutuyor(metot: str, yol: str) -> bool:
    """Bu uç `Idempotency-Key`i KENDİSİ okuyor mu (kapalı liste)."""
    return any(m == metot and d.match(yol) for m, d in _ATLANAN_DESENLER)


def anahtar_gecerli(ham: str | None) -> str | None:
    """Başlığı kanonikleştirir; yoksa None, bozuksa `IdempotensiReddi`."""
    if ham is None:
        return None
    anahtar = ham.strip()
    if not anahtar:
        # BOŞ BAŞLIK YOK SAYILMAZ. İstemci başlığı gönderdiğini sanıyor;
        # sessizce yok saymak korumasız bir yazmayı KORUNUYOR gibi gösterirdi.
        raise IdempotensiReddi(
            400, "Idempotency-Key boş olamaz", "IDEMPOTENCY_KEY_INVALID"
        )
    if len(anahtar) > AZAMI_ANAHTAR:
        raise IdempotensiReddi(
            400,
            "Idempotency-Key en fazla %d karakter olabilir" % AZAMI_ANAHTAR,
            "IDEMPOTENCY_KEY_INVALID",
        )
    return anahtar


def govde_tamponlanabilir(basliklar) -> None:
    """Gövde okunmadan ÖNCE boyutunu sorar; sığmıyorsa reddeder.

    `Transfer-Encoding` varsa uzunluk BİLİNMEZ ve okumak sınırsız tamponlama
    demektir; `Content-Length` YOKSA (ve chunked de değilse) gövde YOKTUR —
    gövdesiz `DELETE` tam olarak budur ve reddedilmesi yanlış olurdu.
    """
    if basliklar.get("transfer-encoding"):
        raise IdempotensiReddi(
            411,
            "Idempotency-Key gönderen istek Content-Length taşımak zorundadır",
            "IDEMPOTENCY_LENGTH_REQUIRED",
        )
    ham = basliklar.get("content-length")
    if ham is None:
        return
    if not ham.strip().isdigit():
        raise IdempotensiReddi(
            411,
            "Idempotency-Key gönderen istek Content-Length taşımak zorundadır",
            "IDEMPOTENCY_LENGTH_REQUIRED",
        )
    if int(ham.strip()) > AZAMI_ISTEK_GOVDESI:
        raise IdempotensiReddi(
            413,
            "Idempotency-Key en fazla %d baytlık gövdeyle kullanılabilir"
            % AZAMI_ISTEK_GOVDESI,
            "IDEMPOTENCY_REQUEST_TOO_LARGE",
        )


def istek_ozeti(metot: str, yol: str, govde: bytes) -> str:
    """`method + route + body` üzerinden sha256.

    Üçü de AYIRICI ile ayrılıyor: ayırıcısız birleştirme, yolun sonundaki bir
    parçayla gövdenin başındaki bir parçanın YER DEĞİŞTİRMESİNİ aynı özete
    düşürürdü.
    """
    ozet = hashlib.sha256()
    ozet.update(metot.encode("utf-8"))
    ozet.update(b"\n")
    ozet.update(yol.encode("utf-8"))
    ozet.update(b"\n")
    ozet.update(govde)
    return ozet.hexdigest()


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


#: Zaman parametreleri TİPLİ bağlanıyor. Tipsiz bırakılsaydı SQLAlchemy
#: `datetime`i DBAPI'ye ham verirdi ve iki diyalekt onu FARKLI biçimde
#: yazardı — karşılaştırma bir diyalektte takvimsel, ötekinde alfabetik olurdu.
_SURESI_DOLANI_SIL = text(
    "DELETE FROM idempotency_keys "
    "WHERE company_id=:cid AND user_id=:uid AND key=:key AND expires_at <= :now"
).bindparams(sa.bindparam("now", type_=sa.DateTime(timezone=True)))

_IDDIA_ET = text(
    "INSERT INTO idempotency_keys("
    "company_id,user_id,key,method,route,request_hash,status,"
    "response_status,response_body,created_at,completed_at,expires_at"
    ") VALUES(:cid,:uid,:key,:method,:route,:hash,'processing',"
    "NULL,NULL,:now,NULL,:expires)"
).bindparams(
    sa.bindparam("now", type_=sa.DateTime(timezone=True)),
    sa.bindparam("expires", type_=sa.DateTime(timezone=True)),
)

_SATIRI_OKU = text(
    "SELECT status,request_hash,response_status,response_body "
    "FROM idempotency_keys "
    "WHERE company_id=:cid AND user_id=:uid AND key=:key"
)

_TAMAMLA = text(
    "UPDATE idempotency_keys "
    "SET status='completed',response_status=:rs,response_body=:rb,completed_at=:now "
    "WHERE company_id=:cid AND user_id=:uid AND key=:key"
).bindparams(sa.bindparam("now", type_=sa.DateTime(timezone=True)))

_IPTAL = text(
    "DELETE FROM idempotency_keys "
    "WHERE company_id=:cid AND user_id=:uid AND key=:key"
)


def iddia_et(
    *, company_id: int, user_id: int, key: str, metot: str, yol: str, govde: bytes
) -> Iddia | Tekrar:
    """Anahtarı iddia eder ya da saklanmış cevabı döndürür.

    Yarışı VERİTABANI çözüyor: iddia bir INSERT'tür ve
    `uq_idempotency_keys_company_user_key` ikinci yazanı reddeder. Reddedilen
    taraf satırı OKUR ve üç durumdan birini görür — tamamlanmış (tekrar
    oynat), farklı özet (422), hâlâ işleniyor (409).
    """
    ozet = istek_ozeti(metot, yol, govde)
    simdi = _simdi()
    kapsam = {"cid": company_id, "uid": user_id, "key": key}

    with SessionLocal.begin() as db:
        # TEMBEL TEMİZLİK, ANAHTAR BAŞINA: süresi dolmuş satır YENİ bir isteğin
        # önünde duramaz. Silme ile INSERT AYNI transaction'da; arada başka bir
        # istek aynı anahtarı iddia ederse INSERT reddedilir ve aşağıdaki
        # okuma yolu doğru cevabı verir.
        db.execute(_SURESI_DOLANI_SIL, {**kapsam, "now": simdi})
        try:
            db.execute(
                _IDDIA_ET,
                {
                    **kapsam,
                    "method": metot,
                    "route": yol[:200],
                    "hash": ozet,
                    "now": simdi,
                    "expires": simdi + YASAM_SURESI,
                },
            )
        except IntegrityError:
            # ROLLBACK AÇIKÇA: `SessionLocal.begin()` çıkışta commit eder ve
            # DÜŞMÜŞ bir transaction commit EDİLEMEZ. Geri alma silmeyi de
            # geri alır — doğrusu budur: INSERT reddedildiyse defterdeki satır
            # süresi DOLMAMIŞ demektir ve o satır BAŞKASININ.
            db.rollback()
        else:
            return Iddia(company_id=company_id, user_id=user_id, key=key)

    with SessionLocal.begin() as db:
        satir = db.execute(_SATIRI_OKU, kapsam).mappings().first()

    if satir is None:
        # İDDİA REDDEDİLDİ AMA SATIR YOK: kazanan ile okuma arasında satır
        # silindi (süresi doldu ya da 5xx iptali). Bu istek için GÜVENLİ olan
        # cevap 409'dur — sessizce işleyiciye geçmek, iki isteğin AYNI ANDA
        # koşmasına izin vermek olurdu.
        raise IdempotensiReddi(
            409,
            "Bu Idempotency-Key şu anda işleniyor; tekrar deneyin",
            "IDEMPOTENCY_IN_PROGRESS",
        )
    if satir["request_hash"] != ozet:
        # AYNI ANAHTAR, BAŞKA İSTEK. Bu bir istemci hatasıdır ve SESSİZCE
        # geçirilemez: geçirilseydi ikinci isteğin cevabı BİRİNCİ isteğin
        # cevabı olurdu.
        raise IdempotensiReddi(
            422,
            "Idempotency-Key farklı bir istekle yeniden kullanılamaz",
            "IDEMPOTENCY_KEY_REUSED",
        )
    if satir["status"] != "completed":
        raise IdempotensiReddi(
            409,
            "Bu Idempotency-Key şu anda işleniyor; tekrar deneyin",
            "IDEMPOTENCY_IN_PROGRESS",
        )
    return Tekrar(
        status_code=int(satir["response_status"]), body=satir["response_body"]
    )


def tekrar_cevabi(tekrar: Tekrar) -> Response:
    """Saklanmış cevabı HTTP cevabına çevirir."""
    if tekrar.body is None:
        # Cevap SAKLANMADI ve işleyici İKİNCİ KEZ KOŞTURULMAZ — gerekçe
        # modül başlığında.
        raise IdempotensiReddi(
            409,
            "Bu isteğin cevabı saklanmadı; tekrar oynatılamaz (özgün durum: %d)"
            % tekrar.status_code,
            "IDEMPOTENCY_RESPONSE_NOT_STORED",
        )
    govde = tekrar.body.encode("utf-8")
    cevap = Response(
        content=govde,
        status_code=tekrar.status_code,
        media_type=SAKLANAN_TIP if govde else None,
    )
    cevap.headers[TEKRAR_BASLIGI] = "true"
    return cevap


def _saklanabilir(cevap: Response, govde: bytes) -> bool:
    if len(govde) > AZAMI_GOVDE:
        return False
    if not govde:
        # 204 ve gövdesiz 200: saklanacak bir şey YOK ama cevap TAMDIR.
        return True
    tip = (cevap.headers.get("content-type") or "").split(";")[0].strip().lower()
    if tip != SAKLANAN_TIP:
        return False
    try:
        govde.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


async def tamamla(iddia: Iddia, cevap: Response) -> Response:
    """İşleyicinin cevabını tamponlar, saklar ve AYNI cevabı geri verir.

    Cevap `call_next`ten akan bir gövdedir; saklamak için tamponlanmak
    ZORUNDA ve tamponlanan gövde istemciye de bu fonksiyondan gider.
    """
    govde = await _govdeyi_tampona_al(cevap)
    kapsam = {
        "cid": iddia.company_id,
        "uid": iddia.user_id,
        "key": iddia.key,
    }
    if cevap.status_code >= 500:
        # SAKLANMAZ, SATIR SİLİNİR — tekrar denemek güvenli kalmalı.
        with SessionLocal.begin() as db:
            db.execute(_IPTAL, kapsam)
        return cevap
    saklanacak = govde.decode("utf-8") if _saklanabilir(cevap, govde) else None
    with SessionLocal.begin() as db:
        db.execute(
            _TAMAMLA,
            {
                **kapsam,
                "rs": int(cevap.status_code),
                "rb": saklanacak,
                "now": _simdi(),
            },
        )
    return cevap


def iptal_et(iddia: Iddia) -> None:
    """İşleyici PATLADI: iddia geri alınır ki tekrar deneme mümkün olsun."""
    with SessionLocal.begin() as db:
        db.execute(_IPTAL, kapsam_of(iddia))


def kapsam_of(iddia: Iddia) -> dict[str, object]:
    return {"cid": iddia.company_id, "uid": iddia.user_id, "key": iddia.key}


async def _govdeyi_tampona_al(cevap: Response) -> bytes:
    """Akan cevabı tampona alır ve cevabı YENİDEN OYNATILABİLİR bırakır."""
    akis = getattr(cevap, "body_iterator", None)
    if akis is None:
        return bytes(getattr(cevap, "body", b"") or b"")
    parcalar = [parca async for parca in akis]
    govde = b"".join(
        parca if isinstance(parca, bytes) else str(parca).encode("utf-8")
        for parca in parcalar
    )

    async def _tekrar_akit():
        yield govde

    cevap.body_iterator = _tekrar_akit()
    return govde

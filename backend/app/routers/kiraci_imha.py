"""KİRACI YUMUŞAK İMHASI — bir firmanın kapatılması (5.1b).

NEDEN "YUMUŞAK" — SERT İMHA MÜMKÜN DEĞİL, TERCİH DEĞİL
------------------------------------------------------
Satırların gerçekten SİLİNMESİ bu şemada YAPILAMAZ. ``activity_logs`` ve
``notifications_archive`` üzerinde ``BEFORE DELETE`` tetikleyicileri var
(göçler ``20260727_0030`` ve ``20260728_0033``): her ikisi de silmeyi
veritabanı düzeyinde REDDEDER. Kiracının satırları bu iki tabloya da
uzandığı için "hepsini sil" denemesi işlemin ORTASINDA patlar ve geriye
YARIM silinmiş bir kiracı bırakırdı — hiç denenmemesinden kötü. Bu yüzden
imha, VERİYE DOKUNMAZ ve denenmez.

O HALDE KİLİT NEREDE — TEK YERDE
--------------------------------
``companies.is_active=false`` ve ``tenancy.resolve_company``. Ara katman
kiracıyı HER ``/api`` isteğinde orada çözer; kapalı firma orada düşünce
uçların TAMAMI birden kapanır. Uçlara tek tek kapı takmak SEÇENEK DEĞİLDİ:
gözden kaçan tek bir uç kilidi yalan yapardı, ve yeni eklenen her uç aynı
riski yeniden doğururdu. Kapı yalnız kiracı çözümündedir; bu dosya o bayrağı
çevirir, kapının kendisi değildir.

ÜYELİKLER SİLİNİR, HESAPLAR SİLİNMEZ
------------------------------------
``user_company_memberships`` M:N'dir. İmha YALNIZ bu firmanın üyelik
satırlarını siler; kullanıcının BAŞKA firmalardaki üyelikleri DOKUNULMADAN
kalır ve o firmalarda çalışmaya devam eder.

TEK ÜYELİĞİ BU FİRMA OLAN KULLANICI HESABINI KORUR. ``app_users`` GLOBALDİR
(``company_id`` taşımaz, ölçüldü): hesabı silmek onu kiracı sınırının DIŞINDA
yok etmek olurdu ve bu ucun yetkisi tek bir kiracıyla sınırlıdır. Hesap
yaşar ama hiçbir firmaya çözülemez — giriş yapabilir, ``/api``de kiracı
gerektiren hiçbir şey yapamaz. Bu, kullanıcıyı başka bir firmaya davet
etmenin (üyelik yazmanın) yolunu da AÇIK bırakır; hesap silinseydi kapanırdı.

OTURUMLAR / JETONLAR — ÖLÇÜLDÜ, DOKUNULMUYOR
--------------------------------------------
``auth_tokens`` ve ``auth_refresh_tokens`` KULLANICI başınadır, firma başına
DEĞİL (ölçüldü: ikisinde de ``company_id`` sütunu yok). Bir kullanıcının
jetonunu silmek onu BAŞKA firmalardaki oturumundan da atardı — bu ucun
yetkisi olmayan bir yan etki. Jeton silmek zaten GEREKMİYOR: geçerli bir
jetonla gelen istek de kiracı çözümünde 403 ile düşer. Kilit jetonda değil,
kiracı çözümünde.

KAPALI FİRMANIN AYRI BİR HATA KODU YOK — BİLİNÇLİ
-------------------------------------------------
İmhadan sonra her istek ``COMPANY_ACCESS_DENIED`` alır; üye olmayanın aldığı
gövdenin AYNISI. Ayrı bir kod (``COMPANY_INACTIVE``) kilidi okunur yapardı
ama kimliği doğrulanmış herhangi bir kullanıcıya, açıkça ``X-Company-ID``
yazarak bir kimliğin "var olan ama kapatılmış firma" olduğunu öğrenme yolu
açardı — kiracı yaşam döngüsü dışarıya sızmaz. Ayrımsızlık
``tests/test_tenancy_inactive_company.py`` içindeki
``test_non_member_cannot_tell_active_from_inactive_from_absent`` ile çivili.

DURUM OKUMA UCU DA YOK. ``GET /api/company/status`` yazılıp KALDIRILDI: kapalı
firma ara katmanda düştüğü için o uç gövdesine HİÇ varılamaz, yani
``is_active: false`` DÖNDÜREMEZ ve sabit ``true`` yazan bir gövdeyle ayırt
edilemezdi — ölü yüzeydi.

BU DİLİMDE OLMAYAN: PII ANONİMLEŞTİRME
--------------------------------------
Kişisel veri taşıyan satırlar OLDUĞU GİBİ KALIR. 5.1 keşfinde adı konmuş
tablolar: ``customers``, ``suppliers``, ``entity_contacts``, ``work_orders``.
Bunların maskelenmesi/anonimleştirilmesi 5.1b'nin KAPSAMI DIŞINDADIR ve
burada YAPILMAMIŞTIR — "kapatıldı" ile "silindi" AYNI ŞEY DEĞİLDİR ve bu
dosya yalnız birincisini iddia eder.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..activity_log import log_activity
from ..db import get_db
from ..tenancy import companies, company_id as aktif_firma, memberships

class FirmaZatenKapaliError(RuntimeError):
    """İkinci kez imha denendi — firma ZATEN kapalı.

    ADI KONMUŞ ve KODU GÖVDEYE YAZILAN bir hata (``disa_aktarim_errors``
    ailesinin aynı biçimi): istemci hata METNİNİ ayrıştırmak zorunda kalmasın
    diye. ``main.py``deki işleyici bunu 409 + ``COMPANY_ALREADY_INACTIVE``
    gövdesine çevirir.

    ÖLÇÜLEN GERÇEK — BU YOL BUGÜN HTTP'DEN ERİŞİLEMEZ. İkinci çağrı ara
    katmanda ``resolve_company``ye takılır (firma kapalı) ve uca hiç varmaz.
    O hâlde neden duruyor: kilit ``tenancy.py``de, bu uçta DEĞİL. Kilit bir
    gün gevşerse — ya da bu uç bir gün kiracı çözümü dışından çağrılırsa —
    savunmasız kalan tek şey "tek denetim kaydı" garantisi olurdu. Kapı
    ``tests/test_kiraci_imha.py``da doğrudan fonksiyon çağrısıyla ölçülür,
    çünkü HTTP üzerinden ölçmek BUGÜN imkânsızdır.
    """

    kod = "COMPANY_ALREADY_INACTIVE"

    def __init__(self) -> None:
        super().__init__("Bu firma zaten kapatıldı")


# SİLİNEBİLEN TEK TABLO ``memberships`` (``user_company_memberships``) ve bu
# yazılı bir kapıdır: ``tests/test_kiraci_imha.py`` bu dosyadaki HER
# ``delete()`` çağrısının argümanının O AD olduğunu ve kaynakta ham bir
# "DELETE FROM" metninin HİÇ geçmediğini doğrular. Kapı GEREKLİ çünkü bu
# dosyanın adı "imha"dır: buraya bir gün ikinci bir ``delete()`` eklemek en
# kolay ve en yıkıcı hatadır.
#
# TABLO NESNESİ TAKMA ADA BAĞLANMAZ. Denendi ve ÖLÇÜLDÜ: ara bir modül sabiti
# (``memberships = memberships``) ``tests/test_core_query_inventory.py``
# tarayıcısının hedef tablosunu ÇÖZEMEMESİNE yol açıyor ve sorgu
# "güvenli sayılamaz" kovasına düşüyor. Okunabilirlik için takılan bir takma
# ad, kiracı nöbetçisini kör etmeye değmez.

router = APIRouter(prefix="/company", tags=["Kiracı İmhası"])


class ImhaTalebi(BaseModel):
    """Onay, firmanın adının BİREBİR yazılmasıdır.

    ``turkce_katla`` UYGULANMAZ ve bu bilinçlidir: bu alanın işi arama
    yapmak değil, kullanıcının doğru firmada olduğunu KANITLAMASIDIR.
    Büyük/küçük harf ya da Türkçe katlama gevşetilseydi "Ada Tarım" ile
    "ADA TARIM" aynı sayılırdı — oysa ikisi AYRI iki kiracı adı olabilir ve
    yanlış olanı kapatmak geri alınması pahalı bir hatadır.
    """

    confirm_name: str = Field(min_length=1, max_length=200)


@router.post("/erase")
def firmayi_imha_et(
    talep: ImhaTalebi, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Aktif firmayı kapatır ve üyeliklerini kaldırır — TEK İŞLEMDE.

    ÜYELİK ve ROL kapıları BU FONKSİYONDA DEĞİL, ara katmandadır ve dışa
    aktarımla AYNI kapılardır (``required_permission`` → ``__admin_only__``,
    ``resolve_company`` → üyelik). Burada yalnız İSİM ONAYI ve
    TEKRARLANABİLİRLİK denetlenir.

    İŞLEM SIRASI RASTGELE DEĞİL: bayrak, üyelikler ve aktivite satırı TEK
    ``db`` işlemindedir ve commit BİR KEZ, hepsinden SONRA atılır. ``get_db``
    commit ETMEZ (ölçüldü: yalnız ``close()`` çağırır), ``log_activity`` de
    etmez — yani üç yazma da bu tek ``commit()``e bağlıdır: bayrak dönüp
    kayıt düşerse denetim izi yalan söylerdi, kayıt yazılıp bayrak dönmezse
    firma kapanmamış görünürken kapanmış sayılırdı.

    TEKRAR ÇAĞRI 409. İmha ÜYELİKLERİ SİLDİĞİ için ikinci çağrının ara
    katmandan geçmesi normalde imkânsızdır (çağıran artık üye değil, üstelik
    firma kapalı). 409 yine de yazılıdır: kilit bir gün gevşerse bu uç
    sessizce ikinci bir "imha edildi" kaydı yazmasın.
    """
    cid = aktif_firma(request)
    satir = db.execute(
        select(companies.c.id, companies.c.name, companies.c.is_active).where(
            companies.c.id == cid
        )
    ).mappings().first()
    if satir is None:
        raise HTTPException(404, "Aktif firma bulunamadı")
    if not bool(satir["is_active"]):
        raise FirmaZatenKapaliError()
    if talep.confirm_name != satir["name"]:
        raise HTTPException(422, "Onay adı firma adıyla birebir aynı olmalı")

    # KAÇ ÜYELİK DÜŞTÜĞÜ ÖNCEDEN SAYILIR: sayı hem yanıtta hem denetim
    # kaydında durur. Sonradan sayılamaz — satırlar artık yoktur.
    uyelik_sayisi = int(
        db.execute(
            select(func.count()).select_from(memberships).where(
                memberships.c.company_id == cid
            )
        ).scalar_one()
    )

    db.execute(
        update(companies).where(companies.c.id == cid).values(is_active=False)
    )
    # KİRACI YÜKLEMİ AÇIK: yalnız BU firmanın üyelikleri. Yüklem düşerse
    # kümedeki HER kiracının her kullanıcısı firmasız kalırdı.
    db.execute(delete(memberships).where(memberships.c.company_id == cid))

    kullanici = getattr(request.state, "user", {}) or {}
    log_activity(
        db,
        cid,
        int(kullanici["id"]) if kullanici.get("id") is not None else None,
        "company.erased",
        # KAYNAK TİPİ dışa aktarımla AYNI ("backup"): ikisi de kiracı yaşam
        # döngüsü olaylarıdır ve panelde aynı ailede görünmeleri istenir.
        # Yeni bir kaynak tipi eklemek bu dilimin kapsamında değil.
        "backup",
        None,
        "Kiracı kapatıldı",
        {"membership_count": uyelik_sayisi, "company_name": satir["name"]},
        correlation_id=getattr(request.state, "request_id", None),
    )
    # TEK COMMIT, EN SONDA. Yukarıdaki üç yazmanın hiçbiri kendi başına
    # kalıcı değildir; buraya varılamazsa firma AÇIK kalır ve denetim izi
    # de boş kalır — yarım bir imha YOKTUR.
    db.commit()

    return {
        "company_id": cid,
        "is_active": False,
        "removed_memberships": uyelik_sayisi,
        # DIŞA AKTARIM İPUCU. Bu uç TAZE BİR YEDEK ARAMAZ (kapsam dışı) —
        # arasaydı "taze"nin ne olduğunu tanımlamak gerekirdi ve o tanım
        # burada ölçülemez. Bunun yerine, veriyi almanın yolu yanıtta AÇIKÇA
        # yazılıdır; imhadan SONRA o uç da 403 verecektir.
        "export_hint": (
            "Veri dışa aktarımı GET /api/company/export ucundadır ve imhadan "
            "SONRA erişilemez; almadıysanız bu isteği geri alacak bir uç yok."
        ),
    }

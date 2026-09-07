"""PUSH CİHAZ UÇLARI (5.4c): kaydet, listele, düşür.

Defterin kendisi `app/push_devices.py`de, şeması göç `20260909_0077`de.

--- ÜÇ UÇ, HEPSİ ÇAĞIRANIN KENDİ CİHAZLARI ÜZERİNDE ----------------------

  * `POST   /api/push/devices`      — jetonu kaydeder ya da GÜNCELLER (upsert)
  * `GET    /api/push/devices`      — YALNIZ çağıranın cihazları
  * `DELETE /api/push/devices/{id}` — sahibi ya da `admin` düşürebilir

--- KİM ÇAĞIRABİLİR: `read` — ÖLÇÜLMÜŞ BİR SEÇİM, KOLAY YOL DEĞİL --------

`app/auth.py`nin `required_permission`ı bu önek için `read` döndürüyor.
Alternatifleri TEK TEK elendi:

  * KURAL YAZMAMAK: `/api/push/...` hiçbir mevcut önekle eşleşmiyor (ölçüldü),
    yani `POST` ve `DELETE` dosyanın SONUNDAKİ deny-by-default nöbetçisine
    düşerdi — `admin` DIŞINDA hiç kimse telefonunu kaydedemezdi. Bu, ucun
    var olma sebebini yok eder.
  * `SELF_SERVICE_API`ye eklemek: o küme yetki kapısından TAMAMEN MUAFTIR ve
    üyeliği TAM EŞLEŞMEDİR — `{device_id}` taşıyan bir yol oraya giremez.
    Ayrıca o muafiyetin gerekçesi "kilitlenme kurtarma yolu yok"tur (oturum
    kapatma, parola değiştirme); cihaz kaydının başarısız olması bir
    kilitlenme DEĞİL, bildirimlerin gelmemesidir.
  * YENİ BİR İZİN ADI (`push.manage`): rol tablosuna yeni bir sütun açardı ve
    hiçbir mevcut rol onu taşımadığı için bugün HERKESİ dışarıda bırakırdı.

SINIR AÇIKÇA YAZILIYOR: `read` taşımayan bir rol cihazını kaydedemez. Bugün
tabloda öyle bir rol YOK (ölçüldü, `ROLE_PERMISSIONS`), ama bir gün olursa o
rol için push kanalı SESSİZCE değil, bu satır sebebiyle kapalı olur.

--- SAHİPLİK 404 DEĞİL 403 — VE NEDEN BURADA SIZINTI DEĞİL --------------

Depoda genel kural "404, 403 değil"dir (`routers/cost_rates.py`): 403 "var ama
sana kapalı" bilgisini sızdırır. Burada 403 veriliyor ve fark ölçüldü: sızan
bilgi, AYNI FİRMADAKİ bir cihaz kimliğinin varlığıdır ve o kimlik zaten
firmanın kendi defterindedir — kiracı sınırı KIRILMIYOR. Başka FİRMANIN
cihazı için 404 dönüyor (satır kiracı yüklemiyle hiç okunamıyor), yani
sızıntının ulaşabildiği en uzak yer çağıranın kendi firmasıdır. Buna karşılık
403, istemciye "bu senin cihazın değil" diyerek YANLIŞ CİHAZ SİLME hatasını
görünür kılıyor — 404 onu "cihaz kaydı düşmüş" sanıp yeniden kayıt
denemesine iterdi.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .. import push_devices
from ..db import get_db
from ..tenancy import company_id

router = APIRouter(tags=["push"])


class PushDeviceWrite(BaseModel):
    """Kayıt gövdesi. `extra="forbid"`: sessizce yok sayılan alan YOK."""

    model_config = ConfigDict(extra="forbid")

    # KAPALI KÜME ve göçün CHECK'i ile AYNI üç değer. Pydantic burada
    # reddederse istemci 422 alır; CHECK'e bırakılsaydı 500 alırdı.
    platform: Literal["android", "ios", "web"]
    token: str = Field(min_length=1, max_length=push_devices.AZAMI_JETON)
    # 5.4a'nın refresh AİLESİ — yabancı anahtar DEĞİL (gerekçe göçün
    # başlığında). İstemci verirse saklanır, vermezse NULL kalır.
    session_family_id: str | None = Field(default=None, max_length=64)


def _kullanici(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        # Ara katman bunu zaten kapatıyor; burada TEKRAR sorulmasının sebebi
        # `int(user["id"])`nin sessizce `TypeError` vermemesidir.
        raise HTTPException(status_code=401, detail="Oturum geçersiz")
    return user


@router.post("/push/devices", status_code=201)
def register_push_device(
    payload: PushDeviceWrite,
    request: Request,
    db: Session = Depends(get_db),
    cid: int = Depends(company_id),
):
    """Jetonu kaydeder ya da VAR OLAN satırı günceller.

    AYNI JETON İKİNCİ KEZ = TEK SATIR: `uq_push_devices_company_token` tekildir
    ve `push_devices.kaydet` önce `UPDATE` dener. Cevap her iki durumda da
    201'dir ve bu bilinçli — istemci için "kaydoldum" olgusu aynıdır ve
    200/201 ayrımı ona satırın daha önce var olup olmadığını, yani BAŞKA BİR
    KURULUMUN izini söylerdi.

    5.4b'nin GENEL İDEMPOTENSİ ara katmanı (`app/idempotency.py`) bu uca
    OLDUĞU GİBİ uygulanır ve BU DOSYADA ONA AİT TEK SATIR KOD YOKTUR — uç
    `ATLANAN_UCLAR` listesinde DEĞİLDİR. Başlığın adı bu dosyada BİLEREK
    GEÇMİYOR: 5.4b'nin kapısı `app/routers/` altında o adı ARAYARAK "kendi
    defterini tutan uçlar" kümesini ölçüyor ve adı anmak bu ucu o kümeye
    SOKARDI — yani ara katman onu ATLARDI. Kapıda ölçülüyor.
    """
    kullanici = _kullanici(request)
    kayit = push_devices.kaydet(
        db,
        company_id=cid,
        user_id=int(kullanici["id"]),
        platform=payload.platform,
        token=payload.token,
        session_family_id=payload.session_family_id,
    )
    db.commit()
    return kayit


@router.get("/push/devices")
def list_push_devices(
    request: Request,
    db: Session = Depends(get_db),
    cid: int = Depends(company_id),
):
    """YALNIZ çağıranın cihazları — `user_id` istekten DEĞİL oturumdan geliyor.

    Sorgu parametresi olarak alınsaydı bir kullanıcı başka bir kullanıcının
    cihaz jetonlarını okuyabilirdi ve o jetonlar, gönderim tarafının TEK
    kimlik bilgisidir.
    """
    kullanici = _kullanici(request)
    return push_devices.listele(
        db, company_id=cid, user_id=int(kullanici["id"])
    )


@router.delete("/push/devices/{device_id}", status_code=204)
def delete_push_device(
    device_id: int,
    request: Request,
    db: Session = Depends(get_db),
    cid: int = Depends(company_id),
):
    """Cihazı düşürür (satır SİLİNMEZ, `is_active=false` olur).

    SAHİPLİK YÜKLEMİ ZORUNLU: olmasaydı aynı firmadaki herhangi bir kullanıcı
    bir başkasının telefonunu bildirimlerden sessizce koparabilirdi.
    """
    kullanici = _kullanici(request)
    kayit = push_devices.oku(db, company_id=cid, device_id=device_id)
    if kayit is None:
        raise HTTPException(status_code=404, detail="Cihaz bulunamadı")
    if int(kayit["user_id"]) != int(kullanici["id"]) and str(
        kullanici["role"]
    ) != "admin":
        raise HTTPException(status_code=403, detail="Bu cihaz size ait değil")
    push_devices.pasiflestir(db, company_id=cid, device_id=device_id)
    db.commit()

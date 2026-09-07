"""Gelen mesajı KALICI kuyruğa yazan tek fonksiyon (WA1).

Kaynak `nazgul_website/backend/app/whatsapp/service.py::gelen_kaydet`.
Gövde BİREBİR taşındı (SAVEPOINT + `IntegrityError` yutma); değişen tek
şey, kaynağın `company_id`/`user_id` sütunlarının BU DEPODA OLMAMASIDIR
(gerekçe: göç `20260910_0078` başlığı).

--- NEDEN SAVEPOINT — VE NEDEN "ÖNCE SELECT" DEĞİL -----------------------

Meta tek teslimatta BİRDEN ÇOK mesaj gönderir ve o mesajlardan YALNIZ
BİRİ kopya olabilir (ilk teslimat kısmen işlenmiş, Meta hepsini yeniden
göndermiş olabilir). Kopya `wamid` UNIQUE kısıta çarptığında, SAVEPOINT
olmasaydı `IntegrityError` TÜM transaction'ı zehirlerdi ve aynı
teslimattaki YENİ mesajlar da yazılamazdı — yani bir kopya, kardeşlerinin
KAYBOLMASINA yol açardı.

"Önce SELECT sonra INSERT" bu işi YAPAMAZ ve gerekçesi bir yarıştır: iki
eşzamanlı webhook çağrısı (Meta'nın paralel teslimatı ya da bizim iki
konteynerimiz) ikisi de "yok" görüp ikisi de yazabilir. Hakem UYGULAMA
DEĞİL, VERİTABANI KISITIDIR.

--- SQL YÜZEYİ: TEK BİR `insert()`, HAM METİN YOK ------------------------

Bu modülde `text()` YOKTUR ve olmaması bilinçlidir: kiracı nöbetçisinin
ham-SQL envanteri (`tests/test_tenant_scoping_guard.py`) `app/` altındaki
her `text()` çağrısını parmak iziyle donduruyor ve bu tabloda
donduracak bir KİRACI YÜKLEMİ yok — tablo platform tablosudur. Core
`insert()` hem o envantere girmez hem de Core sorgu envanterinin
(`tests/test_core_query_inventory.py`) BİLDİRİLMİŞ kapsamı dışındadır
(o kapı `select`/`update`/`delete` sayar). Yani bu dosya iki kapıya da
sessiz bir borç bırakmıyor.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .cloud_api import GelenMesaj
from .schema import RECEIVED, whatsapp_inbound
from .telefon import normalize_phone

log = logging.getLogger("nazgul.whatsapp.giris")


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def gelen_kaydet(db: Session, mesajlar: Sequence[GelenMesaj]) -> int:
    """Doğrulanmış mesajları kuyruğa yazar; YAZILAN satır sayısını döner.

    Kopya `wamid` sessizce ATLANIR ve dönüş değerine GİRMEZ: çağıran
    "kaç yeni mesaj geldi" sorusunun cevabını alır, "kaç satır denendi"
    sorusunun değil.

    `commit` BURADADIR ve bilinçlidir: webhook Meta'ya 200 dönmeden ÖNCE
    satırın KALICI olması gerekir. Kalıcı olmadan 200 dönseydik ve süreç
    o an ölseydi, mesaj HEM kaybolur HEM de Meta bir daha göndermezdi.
    """
    yazilan = 0
    for mesaj in mesajlar:
        try:
            # SAVEPOINT: kopya wamid YALNIZ kendi satırını geri alır, aynı
            # teslimattaki kardeşleri yazılmaya devam eder (başlık).
            with db.begin_nested():
                db.execute(
                    insert(whatsapp_inbound).values(
                        wamid=mesaj.wamid,
                        sender_phone=normalize_phone(mesaj.sender_phone),
                        phone_number_id=mesaj.phone_number_id,
                        text=mesaj.text,
                        media_id=mesaj.media_id,
                        media_mime=mesaj.media_mime,
                        status=RECEIVED,
                        attempt_count=0,
                        received_at=_simdi(),
                    )
                )
            yazilan += 1
        except IntegrityError:
            # Kopya teslimat. Günlüğe NE numara NE içerik yazılır; kopya
            # olduğu bilgisi zaten sayaçtan okunabiliyor.
            log.info("whatsapp: kopya teslimat atlandi")
    db.commit()
    return yazilan


__all__ = ["gelen_kaydet"]

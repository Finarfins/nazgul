"""İzibiz TEST (sandbox) e-İrsaliye duman testi — CI'DA KOŞMAZ.

`izibiz_smoke.py`den AYRILAN NOKTA: o betik `app/einvoice/*`i HİÇ çağırmaz
ve SOAP'ı kendisi kurar (amacı WSDL tahminlerini doğrulamaktı). Bu betik
TAM TERSİNİ yapar — **üretim adaptörünü** (`IzibizEInvoiceProvider`)
çağırır. Sebebi ölçüm hedefinin farklı olması: burada sorulan soru "WSDL
ne diyor" değil, "BİZİM YAZDIĞIMIZ gövde sağlayıcı tarafından KABUL
EDİLİYOR MU".

Cevaplanacak iki açık soru (keşif §2.3 / §2.2, ikisi de **DOĞRULANMADI**):

1. `DESPATCHADVICE/CONTENT` ZIP mi bekliyor, çıplak XML mi? Kod bugün
   e-Arşiv'de ÖLÇÜLEN kuralı (tek dosyalık ZIP) VARSAYIYOR.
2. `GetDespatchAdviceStatus` yanıtındaki durum alanının ÇOCUK ELEMAN ADI
   ne? Kod bir TAHMİN değil bir ARAMA LİSTESİ kullanıyor.

Çalıştırma::

    IZIBIZ_ENV_FILE=/yol/.env.izibiz.local \\
      python backend/sandbox/izibiz_edespatch_smoke.py

Kimlik bilgileri `backend/.env.izibiz.local` (ya da `IZIBIZ_ENV_FILE`)
dosyasından okunur. **HİÇBİR kimlik bilgisi ekrana YAZILMAZ** — çıktı
yalnız HTTP durumu, sağlayıcı durum kodu ve belge kimliklerinden ibarettir.

CANLI KİLİDİ: adaptörün KENDİ kilidi (`izibiz_endpoint_violation`)
devrededir ve `IZIBIZ_ENV=test` zorlanır; taban adres bu betikte SABİT
test adresidir. Ayrıca gönderim öncesi ADRES BİR KEZ DAHA denetlenir —
"adaptör zaten engelliyor" bir sandbox güvencesi değildir.

GÖNDERİM GERİ ALINAMAZ: e-İrsaliye'de İPTAL OPERASYONU YOKTUR (keşif
§2.4). Bu yüzden `--gonder` AÇIK BİR BAYRAKTIR; bayraksız koşu yalnız
LOGIN + DURUM SORGUSU yapar (ikisi de salt-okunur).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.einvoice import edespatch  # noqa: E402
from app.einvoice import endpoints as wire  # noqa: E402
from app.einvoice.provider import IzibizEInvoiceProvider  # noqa: E402

TEST_TABAN = "https://efaturatest.izibiz.com.tr"


class SmokeError(RuntimeError):
    pass


def _kimlik() -> dict[str, str]:
    """Kimlik dosyasını oku. DEĞERLER HİÇBİR YERE BASILMAZ."""
    adaylar = [BACKEND / ".env.izibiz.local", BACKEND / ".env.izibiz.local.txt"]
    ustyaz = os.environ.get("IZIBIZ_ENV_FILE", "").strip()
    if ustyaz:
        adaylar.insert(0, Path(ustyaz))
    yol = next((p for p in adaylar if p.exists()), None)

    degerler: dict[str, str] = {}
    if yol is not None:
        for ham in yol.read_text(encoding="utf-8-sig").splitlines():
            satir = ham.strip()
            if not satir or satir.startswith("#"):
                continue
            for parca in satir.split(","):
                if "=" not in parca:
                    continue
                anahtar, _, deger = parca.partition("=")
                degerler[anahtar.strip().upper()] = deger.strip().strip('"').strip("'")
    for anahtar in ("IZIBIZ_USER", "IZIBIZ_PASS", "IZIBIZ_VKN"):
        ortam = os.environ.get(anahtar)
        if ortam:
            degerler[anahtar] = ortam
    eksik = [k for k in ("IZIBIZ_USER", "IZIBIZ_PASS", "IZIBIZ_VKN") if not degerler.get(k)]
    if eksik:
        raise SmokeError(
            "Kimlik dosyası bulunamadı ya da eksik anahtar var: "
            + ", ".join(eksik)
            + ". `IZIBIZ_ENV_FILE` ile yolu verin."
        )
    return degerler


class _Ayarlar:
    """Adaptörün okuduğu ayar yüzeyi. Taban adres SABİT test adresidir."""

    def __init__(self, kimlik: dict[str, str]) -> None:
        self.einvoice_base_url = TEST_TABAN
        self.einvoice_username = kimlik["IZIBIZ_USER"]
        self.einvoice_password = kimlik["IZIBIZ_PASS"]
        self.einvoice_endpoints_verified = True
        self.izibiz_env = "test"


def _ornek_irsaliye(vkn: str, ettn: str) -> dict:
    """Sentetik bir düz sevk. Gerçek bir müşteriye ait HİÇBİR veri yok."""
    an = datetime.now(timezone.utc) - timedelta(hours=1)
    return {
        # GİB BİÇİMİ: 3 harf + yıl + 9 HANE. `uuid4().hex` HARF de içerir
        # ve sağlayıcı onu REDDEDER (ÖLÇÜLDÜ: `ERROR_CODE=10003`) — sıra
        # bu yüzden rakamdan üretiliyor ve üretici üretim koduyla AYNI
        # fonksiyondur, kopyası değil.
        "despatch_number": edespatch.belge_numarasi_uret(
            an.year, uuid.uuid4().int % 1_000_000_000 or 1
        ),
        "uuid": ettn,
        "issue_date": an.date().isoformat(),
        "invoice_number": edespatch.belge_numarasi_uret(
            an.year, uuid.uuid4().int % 1_000_000_000 or 1, seri="FTR"
        ),
        "supplier": {
            "vkn": vkn,
            "name": "SANDBOX GONDEREN",
            "address": "Test Mah. 1",
            "tax_office": "Test",
        },
        "customer": {
            # Sağlayıcının kendi test VKN'si: sandbox belgeleri kendine
            # gönderilir, üçüncü bir mükellefe DEĞİL.
            "vkn_tckn": vkn,
            "name": "SANDBOX ALICI",
            "address": "Test Cad. 2",
            "tax_office": "Test",
        },
        "shipment": {
            "actual_shipment_at": an,
            "vehicle_plate": "34ABC123",
            "driver_name": "Sandbox Surucu",
            "driver_national_id": "11111111110",
            "delivery_address": "Test Depo Yolu 7",
            "delivery_postal_code": "34000",
        },
        "lines": [{"id": 1, "name": "Bugday", "quantity": "2.5000"}],
    }


def main(argv: list[str] | None = None) -> int:
    ayrist = argparse.ArgumentParser(description="İzibiz e-İrsaliye sandbox duman testi")
    ayrist.add_argument(
        "--gonder",
        action="store_true",
        help="GERÇEK gönderim yap. Bayraksız koşu yalnız salt-okunur adımları yapar.",
    )
    secim = ayrist.parse_args(argv)

    kimlik = _kimlik()
    ayarlar = _Ayarlar(kimlik)

    # ADRES BİR KEZ DAHA DENETLENİYOR — adaptörün kendi kilidi zaten var ama
    # "çağıran zaten engelliyor" bir güvence değildir.
    for op in (wire.IZIBIZ_OP_SUBMIT_DESPATCH, wire.IZIBIZ_OP_STATUS_DESPATCH):
        adres = wire.izibiz_service_url(TEST_TABAN, op)
        ihlal = wire.izibiz_endpoint_violation(adres, "test")
        if ihlal is not None:
            raise SmokeError(f"ortam kilidi reddetti: {ihlal}")
        print(f"[uc] {op} -> {adres}")

    saglayici = IzibizEInvoiceProvider(ayarlar, company_id=None)
    print(f"[yapilandirma] is_configured={saglayici.is_configured()}")

    # --- 1) SALT-OKUNUR: var olmayan bir ETTN sorulur -------------------
    # Bu adım GÖNDERİM YAPMAZ ama TEL SÖZLEŞMESİNİ ölçer: login çalışıyor
    # mu, `GetDespatchAdviceStatus` gövdesi kabul ediliyor mu, yanıt
    # ayrıştırılabiliyor mu. Belge YOK olduğu için beklenen sonuç bir
    # "bulunamadı"dır ve bu BAŞARISIZLIK DEĞİL, ÖLÇÜMDÜR.
    hayali = str(uuid.uuid4())
    sonuc = saglayici.despatch_status(hayali)
    print(
        "[durum-sorgusu/hayali] ic_durum=%s ham_kod=%s belge_yok=%s hata_sinifi=%s"
        % (
            sonuc.status,
            sonuc.gib_status_code,
            (sonuc.raw or {}).get("belge_yok"),
            (sonuc.raw or {}).get("error_class"),
        )
    )
    if sonuc.error:
        print(f"[durum-sorgusu/hayali] hata_metni={sonuc.error[:300]}")

    if not secim.gonder:
        print("[gonderim] ATLANDI (--gonder verilmedi). e-İrsaliye'de İPTAL YOK.")
        return 0

    # --- 2) GERÇEK GÖNDERİM --------------------------------------------
    ettn = str(uuid.uuid4())
    belge = _ornek_irsaliye(kimlik["IZIBIZ_VKN"], ettn)
    print(f"[gonderim] ETTN={ettn} belge_no={belge['despatch_number']}")
    gonderim = saglayici.submit_despatch(belge)
    print(
        "[gonderim] ic_durum=%s saglayici_belge_kimligi=%s ham_kod=%s"
        % (gonderim.status, gonderim.external_id, gonderim.gib_status_code)
    )
    if gonderim.error:
        print(f"[gonderim] hata_metni={gonderim.error[:400]}")

    # --- 3) GÖNDERİLEN BELGENİN DURUMU ----------------------------------
    takip = saglayici.despatch_status(ettn)
    print(
        "[durum-sorgusu/gercek] ic_durum=%s ham_kod=%s"
        % (takip.status, takip.gib_status_code)
    )
    if takip.error:
        print(f"[durum-sorgusu/gercek] hata_metni={takip.error[:400]}")
    print(
        "[esleme] ham=%r -> %s"
        % (takip.gib_status_code, edespatch.kodu_coz(takip.gib_status_code))
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - operatör betiği
    try:
        raise SystemExit(main())
    except SmokeError as hata:
        print(f"HATA: {hata}", file=sys.stderr)
        raise SystemExit(2) from None

#!/usr/bin/env python3
"""PostgreSQL istemcisini HAZIRLAR: önce bakar, gerekirse SINIRLI kurar.

--- KUSUR ----------------------------------------------------------------------

CI'da ``backend-postgresql`` işinin 7. adımı şuydu::

    sudo apt-get update && sudo apt-get install -y postgresql-client

Adımın kendi zaman sınırı YOKTU; yalnız işin ``timeout-minutes: 30`` sınırı
vardı. Paket deposu yavaşladığında adım 30 dakika asılı kaldı, iş İPTAL oldu ve
8. adım — testlerin koştuğu adım — ATLANDI. Sonuç: o head'de **sıfır**
PostgreSQL testi koştu ve koşu "cancelled" göründü. Kırmızı yok, uyarı yok.

Maliyeti görünmezliğinde: bir kapının YOKLUĞU kesinti gibi okunuyordu.

--- ÖLÇÜM ----------------------------------------------------------------------

Üç dalda (#77, #80, #81) altı shard ölçüldü; hepsi 29d31s–29d45s arasında,
``timeout-minutes: 30``a karşı öldü. Sabit olan SHARD değil ADIM'dı: aynı
koşuda (32233412985, deneme 2) shard 1 ve 2 aynı adımı **32 ve 68 saniyede**
bitirdi, shard 0 ve 3 otuz dakika asılı kaldı. Yani arıza belirlenimci değil,
ağ kaynaklı.

İki ölçüm daha, ikisi de bu betiğin biçimini belirledi:

1. **Kurulum sıcak yolda gereksiz.** Başarılı shard'ın log'unda indirilen tek
   paket ``postgresql-client all 16+257build1.1 [11.6 kB]`` — bir METAPAKET.
   ``pg_dump``/``pg_restore``ı sağlayan ``postgresql-client-16`` indirilmedi,
   çünkü koşucu imajında ZATEN vardı. Otuz dakikayı yiyen ``apt-get update``,
   işlevsel olarak hiçbir şey eklemeyen bir metapaket içindi.
2. **91 PG dosyasından yalnız 1'i istemciye ihtiyaç duyuyor**
   (``test_platform_backups_postgresql.py``). Diğer 90'ı ``psycopg`` ile
   konuşuyor ve istemci ikililerine hiç dokunmuyor.

--- BU BETİK NE YAPIYOR --------------------------------------------------------

* **Önce bakar.** Araçlar PATH'te ise hiçbir ağ işlemi YAPMAZ ve ``hazir``
  der. Ölçüme göre bu, koşucu imajında beklenen normal hâldir.
* **Gerekirse KENDİ SINIRIYLA kurar.** Kurulum ``PG_ISTEMCI_SINIR_SN``
  saniyeyle sınırlı; işin bütçesini tüketemez. Bu sınır işin
  ``timeout-minutes``ından BAĞIMSIZDIR — kurulumda zaman tüketmek, testlerin
  başarısız olmasından ayırt edilebilir olsun diye.
* **Sonucu OKUNUR yapar.** Üç durum üç ayrı çıktı ve çıkış kodu üretir:
  ``hazir`` / ``kuruldu`` (0) ve ``YOK`` (1) — sonuncusu ``::error::`` ile ve
  hangi aşamada (sınır mı, komut mu, komut geçti ama araç yok mu) olduğunu
  söyleyerek. "Kurulum başarısız" artık "koşu iptal edildi" gibi okunmuyor.

Kurulum komutu ve sınır ortam değişkeniyle geçersiz kılınabilir; kapı testi üç
durumu ISMARLAMA ürettirmek için bunu kullanıyor.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys

#: Yalnız bu iki ikili gerçekten kullanılıyor (``app/database_backup.py``).
GEREKLI_ARACLAR = ("pg_dump", "pg_restore")

#: Varsayılan kurulum. Kapı testi bunu değiştirerek üç durumu üretiyor.
VARSAYILAN_KURULUM = "sudo apt-get update && sudo apt-get install -y postgresql-client"

#: Kurulumun KENDİ sınırı — işin timeout-minutes'ından bağımsız.
VARSAYILAN_SINIR_SN = 180


def _agaci_oldur(surec: subprocess.Popen) -> None:
    """Kabuğu DEĞİL, tüm süreç ağacını öldürür.

    ÖLÇÜLDÜ: ``subprocess.run(..., shell=True, timeout=N)`` yalnız kabuğu
    öldürüyor; asıl işi yapan torun süreç yaşamaya devam ediyor ve borular
    kapanmadığı için çağrı N saniye SONRA değil, torun bitince dönüyor.
    Kapı testi bunu yakaladı: 2 saniyelik sınır 30.1 saniye sürdü. Sınırın
    RAPORLANMASI ile UYGULANMASI aynı şey değil — düzeltmeye çalıştığımız
    kusurun bir kat aşağısı.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(surec.pid)],
            capture_output=True, check=False,
        )
        return
    try:
        os.killpg(os.getpgid(surec.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        surec.kill()


def _sinirli_kabuk(komut: str, sinir: int) -> subprocess.CompletedProcess:
    """Kabuk komutunu KENDİ süreç grubunda, gerçekten sınırlı çalıştırır."""
    surec = subprocess.Popen(
        komut, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        start_new_session=(os.name != "nt"),
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
    )
    try:
        cikti, hata = surec.communicate(timeout=sinir)
    except subprocess.TimeoutExpired:
        _agaci_oldur(surec)
        try:
            surec.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(komut, surec.returncode, cikti, hata)


def eksik_araclar() -> list[str]:
    return [arac for arac in GEREKLI_ARACLAR if shutil.which(arac) is None]


def main() -> int:
    kurulum = os.environ.get("PG_ISTEMCI_KURULUM_KOMUTU", VARSAYILAN_KURULUM)
    try:
        sinir = int(os.environ.get("PG_ISTEMCI_SINIR_SN", VARSAYILAN_SINIR_SN))
    except ValueError:
        print("::error::PG_ISTEMCI=YOK PG_ISTEMCI_SINIR_SN sayı değil", file=sys.stderr)
        return 1

    eksik = eksik_araclar()
    if not eksik:
        # SICAK YOL: ağ yok. Ölçüme göre koşucu imajında beklenen hâl budur.
        print("PG_ISTEMCI=hazir araçlar PATH'te, kurulum ATLANDI")
        return 0

    print(f"PG_ISTEMCI eksik araç: {eksik}; kurulum deneniyor (sınır {sinir}s)")
    try:
        sonuc = _sinirli_kabuk(kurulum, sinir)
    except subprocess.TimeoutExpired:
        print(
            f"::error::PG_ISTEMCI=YOK kurulum {sinir}s SINIRINI aştı. Bu bir ALTYAPI "
            "yavaşlamasıdır; testlerin başarısızlığı DEĞİLDİR. Testler yine de "
            "koşacak — istemciye ihtiyaç duyan tek dosya kendi hatasını verir.",
            file=sys.stderr,
        )
        return 1

    if sonuc.returncode != 0:
        print(
            f"::error::PG_ISTEMCI=YOK kurulum komutu {sonuc.returncode} ile başarısız. "
            f"stderr: {sonuc.stderr.strip()[:400]}",
            file=sys.stderr,
        )
        return 1

    eksik = eksik_araclar()
    if eksik:
        print(
            f"::error::PG_ISTEMCI=YOK kurulum başarıyla bitti ama araçlar hâlâ yok: "
            f"{eksik}. Paket adı değişmiş olabilir.",
            file=sys.stderr,
        )
        return 1

    print("PG_ISTEMCI=kuruldu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

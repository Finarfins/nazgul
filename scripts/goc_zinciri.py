#!/usr/bin/env python3
"""Alembic göç zincirini okuyan TEK araç.

NİYE TEK: bu zinciri iki ayrı gün, iki ayrı ajan, iki ayrı yerde yeniden
yazdı ve İKİSİ DE YANLIŞ AYRIŞTIRDI.

  * Biri deseni satır başına çiviledi (``^revision``) ve TEK SATIRLIK
    ilanları göremedi::

        revision="20260714_0006"; down_revision="20260714_0005"

    Bu depoda böyle İKİ dosya var (``20260714_0006``, ``20260718_0012``).
    Görülmeyen bir düğüm zinciri KESER ve kesilen yerde sahte bir KÖK
    uydurur — araç "çalıştım" der, cevabı yanlıştır.

  * Öteki BİRLEŞME revizyonundaki demet ``down_revision``'ı kaçırdı::

        down_revision = ('20260716_0009', '20260718_0012')

    sonra düzeltirken fazla düzeltti ve her revizyon BAŞ göründü.

Hiçbirini mutasyon yakalamadı. İkisini de TAMLIK DEĞİŞMEZİ yakaladı:
TAM BİR baş, ve o baştan HER revizyona erişilebilirlik. Kusur ajanların
değil, DEPONUN bir özelliğiydi; bu modül onu bir kez kapatır.

SAYISAL SONEK KULLANILMIYOR — ARANAN VE BULUNMAYAN ŞEY.
Dosya adındaki ``_0044`` gibi sonek bu modülde HİÇBİR yerde sıra, kimlik ya
da ebeveynlik türetmek için OKUNMAZ. Sebebi ölçüldü: bu depoda ``0009``
soneği İKİ dosyada birden var (``20260716_0009_machine_idempotency.py`` ve
``20260717_0009_work_orders.py``) ve bunlar KARDEŞTİR — ikisi de
``20260716_0008``'in çocuğu, yani çatallanma noktası. Soneği sıra sanan bir
araç bu ikisini aynı düğüm ya da ardışık sanar. Sıra YALNIZ graftan gelir.
(`test_SIRA_dosya_adindan_BAGIMSIZ` bunu karıştırılmış adlarla kanıtlar.)

Kullanım::

    python scripts/goc_zinciri.py              # zinciri baştan köke bas
    python scripts/goc_zinciri.py --kontrol    # değişmezleri koştur
    python scripts/goc_zinciri.py --atalar REV # bir revizyonun ataları
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DEPO_KOKU = Path(__file__).resolve().parent.parent
VERSIONS = DEPO_KOKU / "backend" / "alembic" / "versions"


# ---------------------------------------------------------------------------
# İHLALLER — her biri KENDİ değişmezini ADIYLA söyler.
#
# UYARI DEĞİL, İSTİSNA. Bir değişmez çağırana "dikkat et" diye bildirilirse
# çağıranın onu kontrol etmeyi unutması mümkündür — ve bu deponun ölçülmüş
# geçmişi tam olarak budur: iki araç da "başarıyla" koştu ve yanlış cevap
# verdi. Değişmez çözümün İÇİNDEDİR; ihlalde `zinciri_coz` DÖNMEZ.
# ---------------------------------------------------------------------------
class ZincirHatasi(Exception):
    """Göç zinciri değişmezi ihlal edildi. `ihlal` hangi değişmez olduğunu söyler."""

    ihlal = "ZINCIR"

    def __init__(self, mesaj: str) -> None:
        super().__init__(f"[{self.ihlal}] {mesaj}")


class YinelenenIdHatasi(ZincirHatasi):
    """Aynı revision id'sini birden fazla dosya ilan ediyor."""

    ihlal = "YINELENEN_ID"


class KopukIsaretHatasi(ZincirHatasi):
    """`down_revision` var olmayan bir revizyonu gösteriyor."""

    ihlal = "KOPUK_ISARET"


class BasSayisiHatasi(ZincirHatasi):
    """DEĞİŞMEZ: zincirin TAM BİR başı olmalı."""

    ihlal = "TEK_BAS"


class ErisilebilirlikHatasi(ZincirHatasi):
    """DEĞİŞMEZ: her revizyon baştan geriye yürüyerek ERİŞİLEBİLİR olmalı."""

    ihlal = "ERISILEBILIRLIK"


class SayimHatasi(ZincirHatasi):
    """DEĞİŞMEZ: yürünen düğüm sayısı DOSYA sayısına eşit olmalı."""

    ihlal = "SAYIM"


# ---------------------------------------------------------------------------
# AYRIŞTIRMA — AST ile, DESENLE DEĞİL.
#
# Regex'in bu depoda düştüğü iki yer (tek satır, demet) AST için özel durum
# bile değildir: `;` ile ayrılmış ifadeler modül gövdesinde ayrı düğümlerdir,
# demet de bir `ast.Tuple` literal'ıdır. Deseni sağlamlaştırmak yerine
# ayrıştırıcıyı DEĞİŞTİRMEK, aynı sınıf kusurun ÜÇÜNCÜ biçimini de kapatır.
# ---------------------------------------------------------------------------
def _modul_sabiti(agac: ast.Module, ad: str):
    """Modül düzeyinde ``ad = <literal>``; yoksa None.

    ÜÇ BİÇİM de okunur ve üçü de bu depoda GERÇEKTEN var:
      * ``revision = "X"``                      (``Assign``)
      * ``revision: str = "X"``                 (``AnnAssign`` — 10 dosya)
      * ``revision="X"; down_revision="Y"``     (tek satır — 2 dosya)
    """
    for dugum in agac.body:
        if isinstance(dugum, ast.Assign):
            hedefler = dugum.targets
        elif isinstance(dugum, ast.AnnAssign):
            hedefler = [dugum.target]
        else:
            continue
        if dugum.value is None:
            continue
        for hedef in hedefler:
            if isinstance(hedef, ast.Name) and hedef.id == ad:
                try:
                    return ast.literal_eval(dugum.value)
                except ValueError:
                    return None
    return None


def _ebeveynlere_cevir(asagi) -> tuple[str, ...]:
    """``down_revision``ı EBEVEYN DEMETİNE çevirir — tek biçim, tek kod yolu.

    Demet YOK SAYILAMAZ: ``20260719_0013`` gerçek bir birleşme revizyonudur
    ve İKİ ebeveyni vardır. Onu tek ebeveynli sanmak zinciri ikiye böler;
    hiç ebeveynsiz sanmak da her revizyonu baş gösterir. İkisi de olmuştur.
    """
    if asagi is None:
        return ()
    if isinstance(asagi, str):
        return (asagi,)
    if isinstance(asagi, (tuple, list)):
        return tuple(x for x in asagi if isinstance(x, str))
    return ()


@dataclass(frozen=True)
class Goc:
    """Tek bir göç dosyası: İLAN EDİLEN kimlik + ebeveynler."""

    dosya: str
    revision: str
    ebeveynler: tuple[str, ...]

    @property
    def ad_kimlikle_uyusuyor(self) -> bool:
        """Dosya adı ilan edilen id ile başlıyor mu.

        UYUŞMAMASI HATA DEĞİLDİR ve zinciri DEĞİŞTİRMEZ. Kimlik dosya adında
        değil, dosyanın İÇİNDE ilan edilendir; alembic de öyle okur. Bu alan
        yalnız RAPOR içindir — ve `test_DOSYA_ADI_ilan_edilen_id_ile_CELISSE_
        ILAN_KAZANIR` bunun bir kaçış deliği olmadığını sabitler.
        """
        return self.dosya.startswith(self.revision)


def goc_dosyalari(versions: Path | None = None) -> list[Path]:
    """Göç dosyaları. `sorted` YALNIZ belirlenimcilik içindir.

    Bu sıralama sonuçta HİÇBİR yere girmez: kimlik dosyanın içinden, sıra
    graftan gelir. Kanıt `test_SIRA_dosya_adindan_BAGIMSIZ` içindedir —
    dosyalar karıştırılmış adlarla kopyalanır ve zincir DEĞİŞMEZ.
    """
    kok = VERSIONS if versions is None else versions
    return sorted(p for p in kok.glob("*.py") if p.name != "__init__.py")


def gocleri_oku(versions: Path | None = None) -> list[Goc]:
    """Her dosyayı bir `Goc`a çevirir. DEĞİŞMEZ KOŞMAZ — ham okuma."""
    cikti: list[Goc] = []
    for yol in goc_dosyalari(versions):
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        rev = _modul_sabiti(agac, "revision")
        if rev is None:
            continue
        cikti.append(
            Goc(yol.name, rev, _ebeveynlere_cevir(_modul_sabiti(agac, "down_revision")))
        )
    return cikti


@dataclass(frozen=True)
class Zincir:
    """DEĞİŞMEZLERİ GEÇMİŞ bir zincir. Var olması geçerli olduğunun kanıtıdır."""

    bas: str
    gocler: dict[str, Goc]
    sira: tuple[str, ...] = field(repr=False)      # baştan köke, belirlenimci
    dosya_sayisi: int = 0

    @property
    def kokler(self) -> list[str]:
        return sorted(r for r, g in self.gocler.items() if not g.ebeveynler)

    @property
    def birlesmeler(self) -> dict[str, tuple[str, ...]]:
        """1'den çok ebeveyni olan revizyonlar."""
        return {r: g.ebeveynler for r, g in self.gocler.items() if len(g.ebeveynler) > 1}

    @property
    def cocuklar(self) -> dict[str, list[str]]:
        c: dict[str, list[str]] = defaultdict(list)
        for r, g in self.gocler.items():
            for e in g.ebeveynler:
                c[e].append(r)
        return {k: sorted(v) for k, v in c.items()}

    @property
    def catallanmalar(self) -> dict[str, list[str]]:
        """1'den çok çocuğu olan revizyonlar — dallanma noktaları."""
        return {r: c for r, c in self.cocuklar.items() if len(c) > 1}

    def atalar(self, revision: str) -> set[str]:
        """`revision` dahil, ondan geriye erişilen HER revizyon."""
        if revision not in self.gocler:
            raise KeyError(revision)
        goruldu: set[str] = set()
        yigin = [revision]
        while yigin:
            r = yigin.pop()
            if r in goruldu:
                continue
            goruldu.add(r)
            yigin.extend(self.gocler[r].ebeveynler)
        return goruldu

    def atasi_mi(self, ata: str, torun: str) -> bool:
        return ata != torun and ata in self.atalar(torun)


def zinciri_coz(versions: Path | None = None) -> Zincir:
    """Zinciri okur, DEĞİŞMEZLERİ ZORLAR, geçerse `Zincir` döner.

    ÇAĞIRANA BIRAKILAN HİÇBİR KONTROL YOK. Bu fonksiyon ya değişmezleri
    geçen bir zincir döner ya da `ZincirHatasi` fırlatır; "başlar şunlar,
    sen bak" diyen bir ara durum YOKTUR — o ara durum, iki aracın da yanlış
    cevap verebilmesinin sebebiydi.
    """
    gocler_listesi = gocleri_oku(versions)
    dosya_sayisi = len(goc_dosyalari(versions))

    # --- YINELENEN_ID: graf olmanın ÖN KOŞULU ---------------------------
    ilan: dict[str, list[str]] = defaultdict(list)
    for g in gocler_listesi:
        ilan[g.revision].append(g.dosya)
    cakisan = {r: sorted(d) for r, d in ilan.items() if len(d) > 1}
    if cakisan:
        raise YinelenenIdHatasi(
            "Aynı revision id'sini birden fazla dosya ilan ediyor; aynı düğüm "
            "çelişkili ebeveyn iddia edebilir, yani girdi bir GRAF DEĞİLDİR. "
            "Baş kümesi burada TANIMSIZDIR ve hesaplanmadı.\n"
            + "\n".join(f"  {r}: {', '.join(d)}" for r, d in sorted(cakisan.items()))
        )

    gocler = {g.revision: g for g in gocler_listesi}

    # --- KOPUK_ISARET: gösterilen her ebeveyn VAR OLMALI ------------------
    kopuk = sorted(
        f"{g.revision} -> {e}"
        for g in gocler.values()
        for e in g.ebeveynler
        if e not in gocler
    )
    if kopuk:
        raise KopukIsaretHatasi(
            "`down_revision` var olmayan bir revizyonu gösteriyor; zincir "
            "burada KOPUKTUR ve alembic `upgrade head` yapamaz.\n  "
            + "\n  ".join(kopuk)
        )

    # --- TEK_BAS ---------------------------------------------------------
    isaret_edilen = {e for g in gocler.values() for e in g.ebeveynler}
    baslar = sorted(set(gocler) - isaret_edilen)
    if len(baslar) != 1:
        raise BasSayisiHatasi(
            f"Zincirin {len(baslar)} başı var, TAM 1 olmalı. Birden çok başta "
            "alembic 'Multiple head revisions are present' der ve dağıtım "
            "durur; sıfır başta zincir döngüseldir.\n"
            + "\n".join(f"  {b}  ({gocler[b].dosya})" for b in baslar)
        )
    bas = baslar[0]

    # --- ERISILEBILIRLIK: baştan geriye yürü -----------------------------
    #
    # YÜRÜYÜŞ BELİRLENİMCİ ama SIRA DOSYA ADINDAN GELMEZ. Bir düğümün
    # ebeveynleri İLAN EDİLDİKLERİ sırayla (`down_revision` demetindeki
    # sıra) gezilir; kardeşler arasında ise ilk görülen önce gelir. Hiçbir
    # yerde dosya adı ya da sayısal sonek okunmaz.
    sira: list[str] = []
    goruldu: set[str] = set()
    kuyruk = [bas]
    while kuyruk:
        r = kuyruk.pop(0)
        if r in goruldu:
            continue
        goruldu.add(r)
        sira.append(r)
        kuyruk.extend(gocler[r].ebeveynler)

    erisilemeyen = sorted(set(gocler) - goruldu)
    if erisilemeyen:
        raise ErisilebilirlikHatasi(
            f"Baş `{bas}` üzerinden {len(erisilemeyen)} revizyona ERİŞİLEMİYOR. "
            "Zincir kopmuş ya da ayrık bir ada oluşmuş demektir; `upgrade head` "
            "bu göçleri hiç koşturmaz — ve tek-baş kapısı bunu TEK BAŞINA "
            "GÖRMEZ, çünkü ada içindeki en üst düğüm bir ebeveyn olarak "
            "gösteriliyorsa baş sayılmaz.\n  " + "\n  ".join(erisilemeyen)
        )

    # --- SAYIM: yürünen == DOSYA sayısı ----------------------------------
    #
    # NİYE DOSYA SAYISI, `gocler` SAYISI DEĞİL: `revision` ilan etmeyen bir
    # dosya `gocler`e hiç girmez, yani ondan hesaplanan her sayı kendi
    # körlüğüyle TUTARLI olurdu. Ölçüt AYRI bir zeminden gelmeli.
    if len(sira) != dosya_sayisi:
        raise SayimHatasi(
            f"Yürünen düğüm sayısı {len(sira)}, göç DOSYASI sayısı "
            f"{dosya_sayisi}. Fark {dosya_sayisi - len(sira)} dosya; büyük "
            "olasılıkla modül düzeyinde `revision` ilan etmiyor ve tarayıcı "
            "onları hiç görmüyor. Sessizce daralan bir tarama, her kapıyı "
            "boşa geçirir."
        )

    return Zincir(bas=bas, gocler=gocler, sira=tuple(sira), dosya_sayisi=dosya_sayisi)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    ayristirici = argparse.ArgumentParser(description="Alembic göç zinciri")
    ayristirici.add_argument("--kontrol", action="store_true",
                             help="değişmezleri koştur; ihlalde 1 döner")
    ayristirici.add_argument("--atalar", metavar="REV", default=None,
                             help="verilen revizyonun atalarını bas")
    ayristirici.add_argument("--versions", metavar="DIZIN", default=None)
    a = ayristirici.parse_args(argv)
    dizin = Path(a.versions) if a.versions else None

    try:
        zincir = zinciri_coz(dizin)
    except ZincirHatasi as hata:
        print(f"::error::{hata}", file=sys.stderr)
        return 1

    if a.kontrol:
        print(f"BAŞ            : {zincir.bas}")
        print(f"KÖK            : {', '.join(zincir.kokler)}")
        print(f"DÜĞÜM / DOSYA  : {len(zincir.sira)} / {zincir.dosya_sayisi}")
        print(f"BİRLEŞME       : {len(zincir.birlesmeler)}")
        print(f"ÇATALLANMA     : {len(zincir.catallanmalar)}")
        print("TEK BAŞ VAR")
        print("HEPSİ ERİŞİLEBİLİR")
        print("SAYIM TUTUYOR")
        return 0

    if a.atalar:
        try:
            atalar = zincir.atalar(a.atalar)
        except KeyError:
            print(f"::error::bilinmeyen revizyon: {a.atalar}", file=sys.stderr)
            return 1
        for r in zincir.sira:
            if r in atalar:
                print(r)
        return 0

    for r in zincir.sira:
        g = zincir.gocler[r]
        ebeveyn = ", ".join(g.ebeveynler) if g.ebeveynler else "(kök)"
        print(f"{r}  <- {ebeveyn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

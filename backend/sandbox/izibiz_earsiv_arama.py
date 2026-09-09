"""E2b — e-Arşiv belgesi NEDEN geri bulunamıyor? ÖLÇÜM betiği (sandbox).

`docs/izibiz-sandbox-bulgular.md` §8 şunu ölçmüştü: `WriteToArchiveExtended`
belgeyi KABUL ediyor (`RETURN_CODE=0`, gerçek `INVOICE_ID`, `WEB_KEY`) ama
`GetEArchiveInvoiceStatus` BOŞ, `GetEArchiveInvoice` PDF'siz ve
`CancelEArchiveInvoice` `ERROR_CODE=10008` dönüyor. §8 oradan "İzibiz belgeyi
bizim istemci ETTN'imizle anahtarlamıyor" SONUCUNU çıkardı.

Portal ölçümü o sonucu ŞÜPHELİ hâle getirdi: `/earsiv/faturalar` bizim
belgelerimizi LİSTELİYOR (yani arşiv tablolarına GİRMİŞLER) ve hepsi
"İşleniyor" / "Gönderilmedi" durumunda — İzibiz'in KENDİ test belgeleri de
dâhil olmak üzere sandboxtaki HER satır aynı durumda.

Bu betik dört hipotezi AYRI AYRI ölçer; hiçbir sonuç çıkarımla üretilmez:

  H1  Sorguladığımız UUID != gönderdiğimiz UBL'deki `<cbc:UUID>`.
  H2  Durum/PDF işlemleri BAŞKA bir anahtar alanı ister (WSDL'den okundu:
      `GetEArchiveInvoiceList` `ID` **veya** `UUID` kabul eder ve bugüne
      kadar HİÇ denenmedi).
  H3  "İşleniyor" durumundaki belge, imzalanana kadar arama işlemlerine
      GÖRÜNMEZ.
  H4  Doğru tutamak `WEB_KEY`tir, UUID değil.

Çalıştırma (kimlik dosyası BAŞKA bir worktree'de olabilir)::

    IZIBIZ_ENV_FILE=F:/nazgul/backend/.env.izibiz.local \\
    python backend/sandbox/izibiz_earsiv_arama.py --capture <dizin>

Hiçbir kimlik bilgisi (SESSION_ID dâhil) ekrana ya da diske TAM yazılmaz;
istek gövdeleri de yakalanır ve SESSION_ID maskelenerek yazılır.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

sys.path.insert(0, str(Path(__file__).resolve().parent))

import izibiz_smoke as smoke  # noqa: E402

NS_ARCHIVE = smoke.NS_ARCHIVE
EARCHIVE_URL = smoke.EARCHIVE_URL

#: §8.2'de İzibiz'e gönderilen tablo — gönderim anında KAYDEDİLMİŞ eşleşmeler.
#: Bu betik onları YENİDEN TÜRETMEZ, sağlayıcının ne dediğiyle KARŞILAŞTIRIR.
KAYITLI_ESLESMELER = (
    ("SNG2026518354588", "405acba6-ce9c-59c7-bc79-c37e2f141939"),
    ("SNG2026471382557", "5266133a-7bf6-5e58-85ec-6a9854169dee"),
    ("SNG2026589807117", "70dfc021-3013-5443-81e7-53e081fbcc45"),
)

#: İzibiz'in KENDİ sandbox test belgesi (portal listesinden). H3 için: bizim
#: olmayan, aynı "İşleniyor" durumundaki bir belge de bulunamıyor mu?
IZIBIZ_KENDI_BELGESI = "EAR2026000001023"

_ISTEK_YAKALAMA: Path | None = None


def _istek_yaz(ad: str, govde: str) -> None:
    """İstek gövdesini maskeleyerek sakla — yanıtla eşleşen dosya adıyla."""
    if _ISTEK_YAKALAMA is None:
        return
    metin = smoke.scrub(govde)
    metin = re.sub(
        r"(<SESSION_ID>)[^<]+(</SESSION_ID>)", r"\1oturum-jetonu\2", metin
    )
    for deger in smoke._CAPTURE_MASK:
        metin = metin.replace(deger, "9999999999"[: len(deger)])
    _ISTEK_YAKALAMA.mkdir(parents=True, exist_ok=True)
    (_ISTEK_YAKALAMA / f"{ad}.request.xml").write_text(metin, encoding="utf-8")


def _cagir(ad: str, govde: str) -> ElementTree.Element:
    """Tek bir e-Arşiv çağrısı; istek de yanıt da yakalanır.

    `raise_on_error` KASTEN çağrılmaz: bu betiğin ÖLÇTÜĞÜ şey tam da hata
    gövdeleridir, istisnaya çevirmek ölçümü yok ederdi.
    """
    _istek_yaz(ad, govde)
    try:
        return smoke.parse(smoke.soap_call(EARCHIVE_URL, govde, operation=ad))
    except smoke.SmokeError as exc:
        print(f"       [HTTP hata] {exc}")
        raise


def _hata(root: ElementTree.Element) -> str:
    """`ERROR_TYPE` özeti — yoksa boş dizge."""
    parcalar = []
    for element in smoke.find_all(root, "ERROR_TYPE"):
        for alan in ("ERROR_CODE", "ERROR_SHORT_DES"):
            deger = smoke.find_text(element, alan)
            if deger:
                parcalar.append(f"{alan}={deger}")
    return "; ".join(parcalar)


def _basliklar(root: ElementTree.Element) -> list[dict[str, str]]:
    """Yanıttaki her `HEADER` düğümünü sözlüğe çevir."""
    satirlar = []
    for element in smoke.find_all(root, "HEADER"):
        satirlar.append(
            {
                alan: smoke.find_text(element, alan) or ""
                for alan in (
                    "INVOICE_ID", "UUID", "STATUS", "STATUS_DESC",
                    "PROFILE", "WEB_KEY", "INVOICE_DATE",
                )
            }
        )
    return satirlar


# --------------------------------------------------------------------------
# İşlemler — hepsi CANLI WSDL'den (`?wsdl` -> `?xsd=5`) okunmuş şemalara uyar
# --------------------------------------------------------------------------
def liste(
    session_id: str,
    *,
    ad: str,
    invoice_id: str | None = None,
    invoice_uuid: str | None = None,
    baslangic: dt.date | None = None,
    bitis: dt.date | None = None,
    header_only: str = "Y",
    limit: int = 20,
) -> ElementTree.Element:
    """`GetEArchiveInvoiceList` — BUGÜNE KADAR HİÇ DENENMEMİŞ arama işlemi.

    Şema (xsd=5): `REQUEST_HEADER` + hepsi OPSİYONEL olan `LIMIT`, `ID`,
    `UUID`, `START_DATE`, `END_DATE`, `PERIOD`, `PREFIX`, `REPORT_INCLUDED`,
    `HEADER_ONLY`, `CONTENT_TYPE`, `READ_INCLUDED`. Yani belge HEM fatura
    numarasıyla (`ID`) HEM ETTN ile (`UUID`) aranabilir — durum ve iptal
    işlemlerinin aksine.
    """
    parcalar = [f"<LIMIT>{limit}</LIMIT>"]
    if invoice_id:
        parcalar.append(f"<ID>{xml_escape(invoice_id)}</ID>")
    if invoice_uuid:
        parcalar.append(f"<UUID>{xml_escape(invoice_uuid)}</UUID>")
    if baslangic:
        parcalar.append(f"<START_DATE>{baslangic.isoformat()}T00:00:00</START_DATE>")
    if bitis:
        parcalar.append(f"<END_DATE>{bitis.isoformat()}T23:59:59</END_DATE>")
    parcalar.append(f"<HEADER_ONLY>{header_only}</HEADER_ONLY>")
    govde = (
        f'<arc:GetEArchiveInvoiceListRequest xmlns:arc="{NS_ARCHIVE}">'
        f"{smoke.request_header(session_id)}"
        f"{''.join(parcalar)}"
        "</arc:GetEArchiveInvoiceListRequest>"
    )
    return _cagir(ad, govde)


def durum(session_id: str, *, ad: str, invoice_uuid: str) -> ElementTree.Element:
    """`GetEArchiveInvoiceStatus` — §8'de BOŞ dönen çağrının aynısı."""
    govde = (
        f'<arc:GetEArchiveInvoiceStatusRequest xmlns:arc="{NS_ARCHIVE}">'
        f"{smoke.request_header(session_id)}"
        f"<UUID>{xml_escape(invoice_uuid)}</UUID>"
        "</arc:GetEArchiveInvoiceStatusRequest>"
    )
    return _cagir(ad, govde)


def belge(session_id: str, *, ad: str, web_key: str) -> ElementTree.Element:
    """`GetEArchiveInvoice` — şemada TEK anahtar `WEB_VALIDATION_KEY`."""
    govde = (
        f'<arc:GetEArchiveInvoiceRequest xmlns:arc="{NS_ARCHIVE}">'
        f"{smoke.request_header(session_id)}"
        f"<WEB_VALIDATION_KEY>{xml_escape(web_key)}</WEB_VALIDATION_KEY>"
        "</arc:GetEArchiveInvoiceRequest>"
    )
    return _cagir(ad, govde)


def iptal(session_id: str, *, ad: str, invoice_uuid: str) -> ElementTree.Element:
    """`CancelEArchiveInvoice` — §8'de `ERROR_CODE=10008` dönen çağrının aynısı.

    Şema (xsd=5): `CancelEArsivInvoiceContent` içinde ZORUNLU tek alan
    `FATURA_UUID`. Opsiyonel alanların HİÇBİRİ gönderilmiyor; her biri
    sağlayıcı tarafında FARKLI bir işlem anlamına gelir (silme, yerine belge
    koyma, geçmişe tarihli iptal) ve hiçbiri ölçülmedi.
    """
    govde = (
        f'<arc:CancelEArchiveInvoiceRequest xmlns:arc="{NS_ARCHIVE}">'
        f"{smoke.request_header(session_id)}"
        "<CancelEArsivInvoiceContent>"
        f"<FATURA_UUID>{xml_escape(invoice_uuid)}</FATURA_UUID>"
        "</CancelEArsivInvoiceContent>"
        "</arc:CancelEArchiveInvoiceRequest>"
    )
    return _cagir(ad, govde)


def _web_anahtari(ham: str) -> str:
    """`WEB_KEY` bir portal URL'idir; çağrıya giden şey `webValidationKey`."""
    eslesme = re.search(r"webValidationKey=([0-9a-fA-F]+)", ham or "")
    return eslesme.group(1) if eslesme else (ham or "").strip()


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", default=None, help="Ham istek/yanıt dizini")
    parser.add_argument("--gun", type=int, default=30, help="Tarih penceresi (gün)")
    parser.add_argument("--taze", action="store_true",
                        help="YENİ belge gönder ve durumunu t=0'dan yokla (ZAMANLAMA ölçümü)")
    args = parser.parse_args(argv)

    global _ISTEK_YAKALAMA
    if args.capture:
        smoke._CAPTURE_DIR = Path(args.capture)
        _ISTEK_YAKALAMA = Path(args.capture)

    if args.taze:
        return taze_belge(args)

    kimlik = smoke.load_credentials()
    bugun = dt.date.today()
    baslangic = bugun - dt.timedelta(days=args.gun)

    session_id = smoke.login(kimlik["IZIBIZ_USER"], kimlik["IZIBIZ_PASS"])
    print(f"[login] oturum alındı: {smoke.mask(session_id)}")
    bulgular: list[tuple[str, str, str]] = []

    try:
        # ------------------------------------------------------------------
        # P1 (H1 + H2): fatura NUMARASIYLA ara. Belge listelenirse İzibiz'in
        # o belge için TUTTUĞU UUID'i görürüz ve gönderdiğimizle
        # KARŞILAŞTIRABİLİRİZ. §8 bu karşılaştırmayı hiç yapamamıştı.
        # ------------------------------------------------------------------
        for numara, gonderilen in KAYITLI_ESLESMELER:
            print(f"\n[P1] GetEArchiveInvoiceList ID={numara}")
            root = liste(
                session_id, ad=f"P1-list-by-id-{numara}",
                invoice_id=numara, baslangic=baslangic, bitis=bugun,
            )
            hata = _hata(root)
            satirlar = _basliklar(root)
            print(f"     ERROR={hata or '<yok>'} satır={len(satirlar)}")
            for satir in satirlar:
                donen = satir["UUID"]
                ayni = donen.lower() == gonderilen.lower()
                print(f"     INVOICE_ID={satir['INVOICE_ID']} STATUS={satir['STATUS']} "
                      f"({satir['STATUS_DESC']})")
                print(f"     UUID (İzibiz) = {donen}")
                print(f"     UUID (bizim)  = {gonderilen}")
                print(f"     EŞİT Mİ? {'EVET' if ayni else 'HAYIR'}")
                bulgular.append((
                    f"P1 ID={numara}",
                    f"UUID eşit={ayni} status={satir['STATUS']}",
                    "H1 ÇÜRÜK" if ayni else "H1 DOĞRU",
                ))
            if not satirlar:
                bulgular.append((f"P1 ID={numara}", hata or "0 satır", "belge listelenmedi"))

        # ------------------------------------------------------------------
        # P2 (H1): AYNI arama, bu kez bizim ETTN'imizle. P1 belgeyi bulup P2
        # bulamazsa anahtar YANLIŞ demektir; ikisi de bulursa anahtar DOĞRU.
        # ------------------------------------------------------------------
        for numara, gonderilen in KAYITLI_ESLESMELER[:1]:
            print(f"\n[P2] GetEArchiveInvoiceList UUID={gonderilen}")
            root = liste(
                session_id, ad="P2-list-by-uuid",
                invoice_uuid=gonderilen, baslangic=baslangic, bitis=bugun,
            )
            hata = _hata(root)
            satirlar = _basliklar(root)
            print(f"     ERROR={hata or '<yok>'} satır={len(satirlar)}")
            for satir in satirlar:
                print(f"     INVOICE_ID={satir['INVOICE_ID']} UUID={satir['UUID']} "
                      f"STATUS={satir['STATUS']}")
            bulgular.append((
                "P2 UUID ile liste",
                hata or f"{len(satirlar)} satır",
                "UUID aranabilir" if satirlar else "UUID ile bulunamıyor",
            ))

        # ------------------------------------------------------------------
        # P3: §8'in BOŞ dönen durum sorgusu — aynı oturumda YİNELENİYOR ki
        # P1/P2 ile YAN YANA okunabilsin.
        # ------------------------------------------------------------------
        numara, gonderilen = KAYITLI_ESLESMELER[0]
        print(f"\n[P3] GetEArchiveInvoiceStatus UUID={gonderilen}")
        root = durum(session_id, ad="P3-status-by-uuid", invoice_uuid=gonderilen)
        hata = _hata(root)
        satirlar = _basliklar(root)
        print(f"     ERROR={hata or '<yok>'} satır={len(satirlar)}")
        for satir in satirlar:
            print(f"     INVOICE_ID={satir['INVOICE_ID']} STATUS={satir['STATUS']} "
                  f"({satir['STATUS_DESC']}) WEB_KEY={'var' if satir['WEB_KEY'] else 'yok'}")
        durum_satirlari = satirlar
        bulgular.append((
            "P3 GetEArchiveInvoiceStatus",
            hata or f"{len(satirlar)} satır",
            "§8 ile aynı" if not satirlar else "ARTIK DOLU",
        ))

        # ------------------------------------------------------------------
        # P4 (H3): tarih penceresiyle TÜM belgeleri listele — bizimkiler
        # görünüyor mu, hangi durumdalar, İzibiz'in kendi belgeleri de var mı?
        # ------------------------------------------------------------------
        print(f"\n[P4] GetEArchiveInvoiceList (yalnız tarih {baslangic}..{bugun})")
        root = liste(session_id, ad="P4-list-by-date", baslangic=baslangic, bitis=bugun, limit=50)
        hata = _hata(root)
        satirlar = _basliklar(root)
        print(f"     ERROR={hata or '<yok>'} satır={len(satirlar)}")
        durumlar: dict[str, int] = {}
        for satir in satirlar:
            durumlar[satir["STATUS"]] = durumlar.get(satir["STATUS"], 0) + 1
            print(f"     {satir['INVOICE_ID']:<20} {satir['STATUS']:<12} {satir['UUID']}")
        bulgular.append((
            "P4 tarihle liste",
            hata or f"{len(satirlar)} satır, durumlar={durumlar}",
            "arşivde görünüyor" if satirlar else "arşivde YOK",
        ))

        # ------------------------------------------------------------------
        # P5 (H3): İzibiz'in KENDİ belgesi. Bizimki bulunamıyor ama onlarınki
        # bulunuyorsa sorun BİZDE; ikisi de bulunamıyorsa sorun SANDBOXTA.
        # ------------------------------------------------------------------
        print(f"\n[P5] GetEArchiveInvoiceList ID={IZIBIZ_KENDI_BELGESI} (İzibiz'in kendi belgesi)")
        root = liste(
            session_id, ad="P5-list-izibiz-own",
            invoice_id=IZIBIZ_KENDI_BELGESI, baslangic=baslangic, bitis=bugun,
        )
        hata = _hata(root)
        satirlar = _basliklar(root)
        print(f"     ERROR={hata or '<yok>'} satır={len(satirlar)}")
        for satir in satirlar:
            print(f"     INVOICE_ID={satir['INVOICE_ID']} STATUS={satir['STATUS']} UUID={satir['UUID']}")
        bulgular.append((
            f"P5 ID={IZIBIZ_KENDI_BELGESI}",
            hata or f"{len(satirlar)} satır",
            "başkasının belgesi görünüyor" if satirlar else "o da görünmüyor",
        ))

        # ------------------------------------------------------------------
        # P6 (H4): `WEB_KEY` DURUM YANITINDAN gelir (liste onu vermiyor).
        # §8'de anahtar gönderim yanıtından alınmıştı; burada belgenin
        # BUGÜNKÜ hâlinden okunuyor.
        # ------------------------------------------------------------------
        anahtar = ""
        for satir in durum_satirlari:
            if satir.get("WEB_KEY"):
                anahtar = satir["WEB_KEY"]
                break
        if anahtar:
            print(f"\n[P6] GetEArchiveInvoice WEB_VALIDATION_KEY={smoke.mask(anahtar)}")
            root6 = belge(session_id, ad="P6-get-by-webkey", web_key=_web_anahtari(anahtar))
            hata6 = _hata(root6)
            icerik = smoke.find_all(root6, "INVOICE")
            print(f"     ERROR={hata6 or '<yok>'} INVOICE düğümü={len(icerik)}")
            bulgular.append((
                "P6 GetEArchiveInvoice (WEB_KEY)",
                hata6 or f"{len(icerik)} INVOICE düğümü",
                "PDF geldi" if icerik else "PDF YOK",
            ))
        else:
            print("\n[P6] ATLANDI — listeden WEB_KEY çıkmadı")
            bulgular.append(("P6 GetEArchiveInvoice", "WEB_KEY yok", "ATLANDI"))

        # ------------------------------------------------------------------
        # P7: §8'in 10008'i — AYNI belge, AYNI istek şekli, BUGÜN.
        # Durum sorgusu artık cevap verdiğine göre iptal de veriyor mu?
        # ------------------------------------------------------------------
        print(f"\n[P7] CancelEArchiveInvoice FATURA_UUID={gonderilen}")
        root7 = iptal(session_id, ad="P7-cancel-by-uuid", invoice_uuid=gonderilen)
        hata7 = _hata(root7)
        donus7 = smoke.find_text(root7, "RETURN_CODE")
        print(f"     ERROR={hata7 or '<yok>'} RETURN_CODE={donus7 or '<yok>'}")
        bulgular.append((
            "P7 CancelEArchiveInvoice",
            hata7 or f"RETURN_CODE={donus7}",
            "10008 SÜRÜYOR" if "10008" in hata7 else ("iptal KABUL" if not hata7 else "başka hata"),
        ))

    finally:
        smoke.logout(session_id)
        print("\n[logout] oturum kapatıldı")

    print("\n" + "=" * 78)
    print("ÖLÇÜM ÖZETİ")
    print("=" * 78)
    for sonda, sonuc, hukum in bulgular:
        print(f"  {sonda:<34} | {sonuc:<38} | {hukum}")
    return 0




# --------------------------------------------------------------------------
# TAZE BELGE SONDASI — "boş yanıt" ZAMANLAMA mı, ANAHTAR mı?
# --------------------------------------------------------------------------
def taze_belge(args) -> int:
    """Yeni bir e-Arşiv belgesi gönderip durumunu t=0'dan itibaren yoklar.

    §8 durum sorgusunu gönderimden ~100 sn sonrasına kadar yoklamış ve HEP
    BOŞ bulmuştu; oradan "anahtar yanlış" sonucu çıkarılmıştı. P1/P3 o
    sonucu çürüttü (İzibiz belgeyi TAM OLARAK bizim ETTN'imizle tutuyor ve
    durum sorgusu BUGÜN cevap veriyor). Geriye tek bir ayrım kalıyor:
    belge gönderildiği ANDA mı sorgulanabilir, yoksa bir GECİKME mi var?
    Bunu ancak TAZE bir belge ölçebilir.
    """
    import time
    import uuid as _uuid

    kimlik = smoke.load_credentials()
    gonderildi = dt.datetime.now()
    seri = int(gonderildi.strftime("%j%H%M%S"))
    numara = smoke.next_invoice_id("SNG", gonderildi, seri)
    # Üretimdeki `build_client_ettn` ile AYNI biçim: uuid5, KÜÇÜK harf.
    # Smoke'un `uuid4().upper()`ı ölçümü kirletirdi — ölçtüğümüz şey tam da
    # üretimin gönderdiği anahtarın geri bulunup bulunmadığı.
    ettn = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"sungur-tarim-erp:e2b:{numara}"))
    print(f"[taze] INVOICE_ID={numara}")
    print(f"[taze] ETTN={ettn}")

    session_id = smoke.login(kimlik["IZIBIZ_USER"], kimlik["IZIBIZ_PASS"])
    try:
        ubl = smoke.build_ubl(
            invoice_uuid=ettn, invoice_id=numara,
            supplier_vkn=kimlik["IZIBIZ_VKN"], issued=gonderildi,
        )
        sonuc = smoke.write_to_archive_extended(
            session_id, invoice_uuid=ettn, invoice_id=numara,
            ubl_xml=ubl, draft=False,
        )
        print(f"[taze] gönderim: RETURN_CODE={sonuc['RETURN_CODE']} "
              f"INVOICE_ID={sonuc['INVOICE_ID']} WEB_KEY={'var' if sonuc['WEB_KEY'] else 'yok'}")
        gonderim_anahtari = sonuc["WEB_KEY"] or ""

        olcumler: list[tuple[int, int, str, str]] = []
        for gecikme in (0, 5, 15, 30, 60):
            if gecikme:
                time.sleep(gecikme - olcumler[-1][0] if olcumler else gecikme)
            root = durum(session_id, ad=f"T-status-t{gecikme:03d}", invoice_uuid=ettn)
            satirlar = _basliklar(root)
            hata = _hata(root)
            kod = satirlar[0]["STATUS"] if satirlar else ""
            aciklama = satirlar[0]["STATUS_DESC"] if satirlar else (hata or "BOŞ")
            print(f"[taze] t={gecikme:>3}s  satır={len(satirlar)}  STATUS={kod or '-'} {aciklama}")
            olcumler.append((gecikme, len(satirlar), kod, aciklama))

        # Durum yanıtındaki WEB_KEY, GÖNDERİM yanıtındakiyle aynı mı?
        root = durum(session_id, ad="T-status-final", invoice_uuid=ettn)
        satirlar = _basliklar(root)
        durum_anahtari = satirlar[0]["WEB_KEY"] if satirlar else ""
        print(f"[taze] WEB_KEY gönderim==durum ? "
              f"{'EVET' if _web_anahtari(gonderim_anahtari) == _web_anahtari(durum_anahtari) else 'HAYIR'}")

        if durum_anahtari:
            root6 = belge(session_id, ad="T-get-by-webkey", web_key=_web_anahtari(durum_anahtari))
            icerik = smoke.find_all(root6, "INVOICE")
            ham = _icerik_turu(icerik)
            print(f"[taze] GetEArchiveInvoice → {len(icerik)} düğüm, içerik türü={ham}")

        root7 = iptal(session_id, ad="T-cancel", invoice_uuid=ettn)
        print(f"[taze] CancelEArchiveInvoice → {_hata(root7) or 'HATA YOK'}")
    finally:
        smoke.logout(session_id)
    return 0


def _icerik_turu(dugumler) -> str:
    """`INVOICE` base64'ünün GERÇEKTE ne olduğu — PDF mi, ZIP mi, XML mi?"""
    import base64 as _b64
    import io as _io
    import zipfile as _zip

    if not dugumler:
        return "<yok>"
    ham = (dugumler[0].text or "").strip()
    try:
        cozulmus = _b64.b64decode(ham, validate=True)
    except Exception:
        return "base64 DEĞİL"
    if cozulmus[:5] == b"%PDF-":
        return "PDF"
    if cozulmus[:2] == b"PK":
        try:
            with _zip.ZipFile(_io.BytesIO(cozulmus)) as arsiv:
                adlar = arsiv.namelist()
                ilk = arsiv.read(adlar[0])[:5] if adlar else b""
            return f"ZIP{adlar} ilk-bayt={ilk!r}"
        except Exception:
            return "ZIP (okunamadı)"
    return f"bilinmiyor {cozulmus[:8]!r}"


if __name__ == "__main__":
    raise SystemExit(main())

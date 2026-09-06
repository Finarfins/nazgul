"""ALIŞ KALEMİ PARTİ AÇAR (FAZ 1B-A) — 0067'nin deposuna İLK ÇAĞIRAN.

Konu: göç `20260908_0073`, `app/routers/transactions.py` (`_parti_ac`,
`_parti_geri_al` ve alış yolu), `app/routers/products.py`
(`GET /api/products/{id}/lots`), `app/schemas.py` (`TransactionItem`).

--- BU DİLİM NEYİ KAPATIYOR ------------------------------------------------

0067 `product_lots` tablosunu ve `app/parti.py` FEFO seçicisini kurdu ve
İKİSİNİ DE HİÇBİR ŞEYE BAĞLAMADI. İki kapı bunu adıyla çiviliyordu:
`test_PARTI_MIKTARI_bu_PR_da_HICBIR_YERDEN_guncellenmiyor` (PG ikizinde) ve
`test_APP_ALTINDA_app_parti_ITHALI_ve_fefo_sec_REFERANSI_YOKTUR` (burada).
Her ikisinin de düzyazısı ŞUNU söylüyordu: "bir çağıran eklendiği gün
BURASI kırmızı olur ve bu DOĞRUDUR."

O gün BUGÜNDÜR. İki kapı EMEKLİ EDİLDİ ve yerlerine YENİ SINIRI çiviliyen
ikisi geldi (`test_product_lots_YAZICISI_YALNIZ_transactions_py` ve
`test_fefo_sec_HALA_CAGIRANSIZ`). Emeklilik bir gevşeme değildir: eski kapı
"hiç yazıcı yok" diyordu, yenisi "TEK yazıcı var ve adı belli" diyor —
ikincisi daha DAR bir iddiadır, daha geniş değil.

--- KAYIT 0082 (PR #31) ile İLİŞKİ ----------------------------------------

Durum kayıtları PR başına EKLENİR, geriye dönük DÜZENLENMEZ. 0067'nin
kaydı olan `docs/durum/0082-pr-0031.md` bu yüzden ELLENMEDİ; bu dilimin
kaydı 0082'ye ATIFTA BULUNUR ve "çağıranı yoktur" cümlesinin nerede
kapandığını orada söyler.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260908_0073_parti_depo_alis.py"
ISLEMLER = BACKEND / "app" / "routers" / "transactions.py"
IS_AKISI = BACKEND / "app" / "routers" / "workflow.py"

#: Parti defterini YAZMASINA izin verilen TEK dosya. Yol `backend/`e görelidir.
#:
#: 1B-A'da burası `app/routers/transactions.py` idi. 1B-B satış yolunu getirince
#: sınır ARTIK TUTMADI: `routers/workflow.py` de (irsaliye, alış iadesi) parti
#: tüketiyor. Kapı GENİŞLETİLMEDİ, DARALTILDI — yazıcılar bir MODÜLE toplandı
#: ve o modül bir yönlendirici DEĞİLDİR. Gerekçenin tamamı
#: `app/parti_defteri.py` başlığındadır; özeti: "yazıcılar transactions.py VE
#: workflow.py" demek defteri iki dosyadan yazılabilir yapardı ve
#: `app/parti.py` başlığı o kusuru ADIYLA reddediyor.
YAZICI = "app/parti_defteri.py"
#: Defteri OKUYABİLEN dosyalar. Okuma yazma DEĞİLDİR ve kapı ikisini ayırır;
#: ayırmasaydı okuma ucu kendi kapısını ihlal ederdi.
#:
#: `warehouse_counts.py` 1B-C ile defteri ANMAYA BAŞLAMADI — onu ÇAĞIRIYOR
#: (aşağıdaki `CAGIRANLAR` ekseni). Burada durması, bir gün tabloyu adıyla
#: okuması ihtimaline karşı AÇILMIŞ bir üst sınır DEĞİLDİR: `ananlar` kümesi
#: TAM EŞİTLİKLE ölçülmüyor (üst sınır), çünkü okuma eklemek yazma eklemekle
#: aynı ağırlıkta değildir.
OKUYUCULAR = {
    YAZICI,
    "app/routers/products.py",
    "app/routers/warehouse_counts.py",
}
#: Defteri ÇAĞIRABİLEN dosyalar — `app/parti_defteri.py`den ithal edenler.
#:
#: ÜÇÜNCÜ EKSEN ve ayrı durması ZORUNLU: yukarıdaki iki küme tablo ADINI
#: ANANLARI ölçüyor, bu küme ise defteri KULLANANLARI. 1B-B'den sonra
#: `transactions.py` ve `workflow.py` tablo adını HİÇ anmıyor (fonksiyon
#: çağırıyorlar), yani yalnız ilk iki eksen kalsaydı yeni bir yönlendiricinin
#: defteri yazmaya başlaması HİÇBİR kapıyı kırmazdı — sessiz bir genişleme.
CAGIRANLAR = {
    "app/routers/transactions.py",
    "app/routers/workflow.py",
    "app/routers/products.py",
    "app/routers/warehouse_counts.py",
}

_YAZMA_FIILLERI = ("INSERT", "UPDATE", "DELETE")


# ------------------------------------------------------------- statik ---

def test_goc_depoyu_ve_yeni_tekili_getiriyor() -> None:
    """Göç ÜÇ şeyi birlikte yapmalı; biri eksikse öteki ikisi anlamsız."""
    kaynak = GOC.read_text(encoding="utf-8")
    # Kısıt ADLARI modül sabitlerinde duruyor; `upgrade` onları ADIYLA değil
    # SABİTLE anıyor. Dizgeyi kaynağın TAMAMINDA aramak bu yüzden doğru
    # kapsamdır — sabit silinirse burası kırmızı olur.
    for ad in (
        "uq_warehouses_company_id",
        "fk_product_lots_warehouse_same_company",
        # Eski tekil DÜŞÜYOR, yenisi KURULUYOR — ikisi birlikte.
        'UQ_PARTI_KODU_ESKI = "uq_product_lots_company_product_code"',
        'UQ_PARTI_KODU = "uq_product_lots_company_product_code_warehouse"',
    ):
        assert ad in kaynak, ad
    govde = kaynak[kaynak.index("def upgrade"):kaynak.index("def downgrade")]
    assert "UQ_DEPO" in govde and "FK_PARTI_DEPO" in govde
    assert "UQ_PARTI_KODU_ESKI" in govde and "UQ_PARTI_KODU," in govde
    # Alış kalemi parti kodunu ve SKT'yi taşıyor.
    assert '"lot_code"' in govde and '"expiry_date"' in govde


def test_goc_BOS_TABLO_olcumunu_VARSAYMIYOR_soruyor() -> None:
    """`upgrade` DE `downgrade` DE satır sayısını ÖLÇÜP gürültülü ölüyor.

    Bu kapı bir dizge araması gibi görünüyor ama savunduğu şey bir DAVRANIŞ:
    ölçüm silinirse göç, hangi depoda durduğu BİLİNMEYEN satırlara NOT NULL
    bir `warehouse_id` uydurmak zorunda kalırdı. Sessiz bir uydurma, geri
    çağırma defterini sorgulanamaz yapar.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert "SELECT count(*) FROM {PARTI}" in kaynak
    assert kaynak.count('_parti_bos_olmali(bind, "upgrade")') == 1
    assert kaynak.count('_parti_bos_olmali(bind, "downgrade")') == 1


def test_goc_DIYALEKT_AYRIMINI_ACIK_yaziyor() -> None:
    """0072'nin ölçümü: batch, PostgreSQL'de kısıt DDL'ini SESSİZCE atlıyor.

    SQLite dalı TEK batch olmalı (0071'in dersi), öteki dal AÇIK `op.*`
    çağrıları. Bu ikisinden biri silinirse ya SQLite `OperationalError`
    verir ya da PostgreSQL'de kısıt HİÇ kurulmaz — ve ikincisi SESSİZDİR.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    yukari = kaynak[kaynak.index("def upgrade"):kaynak.index("def downgrade")]
    # PARTİ dalı, `warehouses`ın tekilini kuran İLK diyalekt dalından
    # SONRADIR; ilkini almak kapıyı yanlış bloğa bakar hale getirirdi.
    parti_dali = yukari[yukari.index('sutun = sa.Column("warehouse_id"'):]
    sqlite_dali = parti_dali[parti_dali.index('if bind.dialect.name == "sqlite":'):]
    sqlite_dali, acik_dal = sqlite_dali.split("else:", 1)
    # SQLite: sütun + eski tekil + yeni tekil + FK, HEPSİ tek batch içinde.
    assert sqlite_dali.count("batch_alter_table(PARTI)") == 1
    for cagri in ("add_column", "drop_constraint", "create_unique_constraint",
                  "create_foreign_key"):
        assert f"batch.{cagri}" in sqlite_dali, cagri
    # Ötekiler: modül düzeyinde AÇIK DDL.
    for cagri in ("op.add_column", "op.drop_constraint",
                  "op.create_unique_constraint", "op.create_foreign_key"):
        assert cagri in acik_dal, cagri


def test_product_lots_YAZICISI_YALNIZ_parti_defteri_py() -> None:
    """EMEKLİ EDİLEN KAPININ YERİNE GELEN, DAHA DAR İDDİA.

    Eski kapı (`test_PARTI_MIKTARI_bu_PR_da_HICBIR_YERDEN_guncellenmiyor`,
    PG ikizinde) "`backend/app` altında `product_lots` literali YOKTUR"
    diyordu. Artık bir yazıcı VAR ve o cümle yanlıştır; ama onun yerine
    HİÇBİR ŞEY koymamak, defteri her yerden yazılabilir bırakırdı.

    1B-B'DE YAZICI TAŞINDI, ÇOĞALMADI. Bu testin adı `..._transactions_py`
    idi ve iddiası `app/routers/transactions.py` idi; satış yolu ikinci bir
    yönlendiriciden parti tüketmeye başlayınca iki seçenek vardı ve kapıyı
    GENİŞLETEN reddedildi (gerekçe `app/parti_defteri.py` başlığında). AD VE
    İDDİA BİRLİKTE HAREKET ETTİ: `test_pg_test_population_exact_*`ın ölçtüğü
    tuzak burada da geçerlidir — değeri doğru olan ama adı onu yalanlayan bir
    çivi, sonraki okuyucu için tuzaktır.

    YENİ SINIR İKİ KATMANLIDIR ve ikisi de ÖLÇÜLÜYOR:

    1. `product_lots`a YAZAN (INSERT/UPDATE/DELETE) çalıştırılabilir metin
       YALNIZ `app/parti_defteri.py` içindedir. İki yazıcı olsaydı
       ikisi sessizce ayrışırdı — 0067'nin `app/parti.py` başlığında
       "iki farklı yerden çıkan mal iki farklı partiden düşerse geri çağırma
       kaydı YALAN SÖYLER" diye ADIYLA yazılı olan kusur.
    2. Tabloyu ANAN dosyaların kümesi de KAPALIDIR: yazıcı + okuma ucu.
       Üçüncü bir dosya defteri okumaya başlarsa burası kırmızı olur ve o
       gün "bu okuma neden ayrı bir yerde" sorusu İNCELEMEYE zorlanır.

    ARAMA AST ÜZERİNDE ve BELGE DİZGİLERİ DIŞLANARAK yapılıyor — emekli
    edilen kapının ÖLÇÜLMÜŞ dersi: ham `grep` `app/parti.py`yi yakalıyordu,
    çünkü o dosya tabloyu KENDİ DÜZYAZISINDA tarif ediyor. Düzyazıda anmak
    ÇAĞIRMAK DEĞİLDİR.
    """
    yazanlar: list[str] = []
    ananlar: set[str] = set()
    for yol in sorted((BACKEND / "app").rglob("*.py")):
        kaynak = yol.read_text(encoding="utf-8")
        if "product_lots" not in kaynak:
            continue
        yer = yol.relative_to(BACKEND).as_posix()
        for dugum in _calistirilabilir_sabitler(ast.parse(kaynak)):
            if "product_lots" not in dugum.value:
                continue
            ananlar.add(yer)
            buyuk = dugum.value.upper()
            if any(fiil in buyuk for fiil in _YAZMA_FIILLERI):
                yazanlar.append(f"{yer}:{dugum.lineno}")

    assert all(yer.startswith(YAZICI) for yer in yazanlar), (
        f"`product_lots`a YAZAN yer(ler) `{YAZICI}` dışında: {yazanlar}. "
        "Defterin TEK yazıcısı `app/parti_defteri.py`dir; yönlendiriciler onu "
        "ÇAĞIRIR. İkinci bir yazıcı iki defteri sessizce ayrıştırır."
    )
    assert yazanlar, (
        "hiç yazıcı bulunamadı — bu PR'ın getirdiği çağıran KAYBOLMUŞ ya da "
        "kapının taraması bozulmuş demektir (sahte yeşil)"
    )
    assert ananlar <= OKUYUCULAR, (
        f"`product_lots`u anan beklenmedik dosya(lar): {sorted(ananlar - OKUYUCULAR)}. "
        f"Kapalı küme: {sorted(OKUYUCULAR)}."
    )


def test_fefo_sec_CAGIRANI_YALNIZ_parti_defteri_py() -> None:
    """İKİNCİ EMEKLİLİK — "çağıranı yoktur" cümlesi 1B-B'de KAPANDI.

    Emekli edilen `test_fefo_sec_HALA_CAGIRANSIZ` şunu diyordu: seçici hâlâ
    hiçbir şeye bağlı değildir, çünkü tüketim yolu dilim B'nin işidir. O gün
    BUGÜNDÜR ve kapı bir GEVŞEMEYLE değil DAR bir iddiayla değiştirildi:
    "hiç çağıran yok" yerine "TEK çağıran var ve adı belli".

    NEDEN TEK ÇAĞIRAN: `app/parti.py`nin başlığı seçicinin çağırandan ÖNCE
    yazılmasının gerekçesini bir ayrışma üzerine kuruyor — "iki farklı yerden
    çıkan mal iki farklı partiden düşerse geri çağırma kaydı YALAN SÖYLER".
    `fefo_sec`i iki yönlendiriciden ayrı ayrı çağırmak tam olarak o iki yeri
    açardı: bugün ikisi de aynı sırayı kullanır, yarın biri bir `ORDER BY`
    ekler ve ayrışma HİÇBİR kırmızı üretmez.

    KAPI SEÇİCİNİN KENDİSİNİ DIŞLAR (`app/parti.py`) ve tek çağıranı
    (`app/parti_defteri.py`) izinli sayar; kalan her `app/` dosyası için hem
    `app.parti` İTHALİ hem de `fefo_sec` ADI yasaktır. İthali de yasaklamak
    zorunludur: yalnız adı yasaklamak `parti.fefo_sec(...)` biçimini kaçırır
    ve modülü ithal edip başka bir ada bağlamak (`sec = parti.fefo_sec`)
    kapıyı sessizce geçerdi.
    """
    secici = BACKEND / "app" / "parti.py"
    defter = BACKEND / "app" / "parti_defteri.py"
    ihlaller: list[str] = []
    for yol in sorted((BACKEND / "app").rglob("*.py")):
        if yol in (secici, defter):
            continue
        agac = ast.parse(yol.read_text(encoding="utf-8"))
        yer = yol.relative_to(BACKEND).as_posix()
        for dugum in ast.walk(agac):
            if isinstance(dugum, ast.Import):
                for ad in dugum.names:
                    if ad.name.split(".")[-1] == "parti":
                        ihlaller.append(f"{yer}:{dugum.lineno} import {ad.name}")
            elif isinstance(dugum, ast.ImportFrom):
                modul = dugum.module or ""
                if modul.split(".")[-1] == "parti":
                    ihlaller.append(f"{yer}:{dugum.lineno} from ...parti import")
                elif any(ad.name == "parti" for ad in dugum.names):
                    ihlaller.append(f"{yer}:{dugum.lineno} import parti")
            elif isinstance(dugum, ast.Attribute) and dugum.attr == "fefo_sec":
                ihlaller.append(f"{yer}:{dugum.lineno} .fefo_sec")
            elif isinstance(dugum, ast.Name) and dugum.id == "fefo_sec":
                ihlaller.append(f"{yer}:{dugum.lineno} fefo_sec")
    assert ihlaller == [], (
        f"`app/parti.py` seçicisine `{YAZICI}` DIŞINDAN bağlanan yer(ler) "
        f"var: {ihlaller}. FEFO sırası TEK bir çağırandan geçmelidir; ikinci "
        "bir çağıran iki sıralamanın sessizce ayrışabileceği yeri açar."
    )
    # SAHTE YEŞİL KARŞITI: kapı "hiç çağıran yok" diye de yeşil kalabilirdi
    # (emekli edilen iddianın ta kendisi). Çağıranın VARLIĞI ayrıca ölçülüyor.
    defter_agaci = ast.parse(defter.read_text(encoding="utf-8"))
    cagriliyor = any(
        isinstance(dugum, ast.Call)
        and getattr(dugum.func, "id", None) == "fefo_sec"
        for dugum in ast.walk(defter_agaci)
    )
    assert cagriliyor, (
        f"`{YAZICI}` `fefo_sec`i ÇAĞIRMIYOR — bu PR'ın getirdiği tüketim yolu "
        "KAYBOLMUŞ ya da kapının taraması bozulmuş demektir (sahte yeşil)."
    )


def test_parti_defterini_CAGIRANLAR_kapali_kume() -> None:
    """ÜÇÜNCÜ EKSEN: tablo adını ANMAYAN ama defteri YAZAN dosyalar.

    1B-B'den sonra `routers/transactions.py` ve `routers/workflow.py`
    `product_lots` literalini HİÇ taşımıyor — fonksiyon çağırıyorlar. Yani
    yukarıdaki iki kapı (yazıcı + ananlar) onları GÖRMÜYOR ve yalnız onlar
    olsaydı ÜÇÜNCÜ bir yönlendiricinin defteri yazmaya başlaması hiçbir
    kırmızı üretmezdi: literal yok, dolayısıyla ihlal de "yok".

    Bu kapı o boşluğu kapatıyor ve ölçtüğü şey İTHALDİR: `app/parti_defteri`
    modülünden ithal eden her dosya kapalı kümede olmalıdır.
    """
    defter = BACKEND / "app" / "parti_defteri.py"
    cagiranlar: set[str] = set()
    for yol in sorted((BACKEND / "app").rglob("*.py")):
        if yol == defter:
            continue
        agac = ast.parse(yol.read_text(encoding="utf-8"))
        yer = yol.relative_to(BACKEND).as_posix()
        for dugum in ast.walk(agac):
            if isinstance(dugum, ast.ImportFrom):
                if (dugum.module or "").split(".")[-1] == "parti_defteri":
                    cagiranlar.add(yer)
                elif any(ad.name == "parti_defteri" for ad in dugum.names):
                    cagiranlar.add(yer)
            elif isinstance(dugum, ast.Import):
                for ad in dugum.names:
                    if ad.name.split(".")[-1] == "parti_defteri":
                        cagiranlar.add(yer)
    assert cagiranlar <= CAGIRANLAR, (
        f"parti defterini çağıran beklenmedik dosya(lar): "
        f"{sorted(cagiranlar - CAGIRANLAR)}. Kapalı küme: {sorted(CAGIRANLAR)}."
    )
    # SAHTE YEŞİL KARŞITI ve KÜMENİN BİR VAAT DEĞİL ÖLÇÜM OLDUĞU: izin
    # verilenlerin hiçbirinin gerçekten çağırmadığı bir küme, kapı gibi
    # görünen ama hiçbir şey savunmayan bir listedir. 1B-B iki çağıran
    # getirdi, 1B-C iki tane daha; DÖRDÜ DE ADIYLA sorulur.
    assert cagiranlar == CAGIRANLAR, (
        f"çağıran kümesi ile İZİN VERİLEN küme ayrıştı. Fazla: "
        f"{sorted(cagiranlar - CAGIRANLAR)}, eksik: "
        f"{sorted(CAGIRANLAR - cagiranlar)}. Eksik olan, o dilimin getirdiği "
        "çağıranın KAYBOLDUĞU anlamına gelir."
    )


def test_parti_geri_alma_HAREKET_SILINMEDEN_ONCE_cagriliyor() -> None:
    """SIRA ZORUNLUDUR: `DELETE FROM stock_movements`ten SONRA okunacak yok.

    Bir dizge sırası ölçüyor ama savunduğu şey bir DAVRANIŞ ve tersi SESSİZ:
    çağrı DELETE'ten sonraya kayarsa hiçbir satır bulunmaz, hiçbir istisna
    atılmaz ve parti defteri geri alınmayan miktarı SONSUZA kadar taşır.
    Stok geri alınır, defter alınmaz — ikisi sessizce ayrışır.

    1B-B'DE KAPSAM İKİYE ÇIKTI. 1B-A'da tek dosya (`transactions.py`) ve
    "1 tanım + 2 çağrı" sayılıyordu; tanım artık `app/parti_defteri.py`de ve
    ÇAĞIRAN İKİ yönlendirici var. Kapı bu yüzden dosya başına GEZİYOR ve
    her birinde İKİ çağrı (güncelleme + silme yolu) arıyor. Sayıyı gezmeye
    çevirmek zorunluydu: tek dosyada sabit kalsaydı `workflow.py`nin iki yeni
    çağrısı kapının GÖRÜŞ ALANINA hiç girmezdi ve sırası ölçülmezdi.
    """
    for yol in (ISLEMLER, IS_AKISI):
        kaynak = yol.read_text(encoding="utf-8")
        parcalar = kaynak.split("_parti_geri_al(")
        # İKİ çağrı: güncelleme yolu ve silme yolu. Tanım artık bu dosyada
        # DEĞİL (`app/parti_defteri.py`) ve ithal satırı `import (` biçiminde
        # olduğu için bu ayırıcıya TAKILMAZ — yani sayı doğrudan çağrı sayısı.
        assert len(parcalar) == 3, (
            f"{yol.name}: beklenen iki çağrı bulunamadı "
            f"({len(parcalar) - 1} geçiş)"
        )
        for govde in parcalar[1:]:
            silme = govde.find("DELETE FROM stock_movements")
            assert silme > 0, (
                f"{yol.name}: `_parti_geri_al` çağrısından SONRA hareket silme "
                "bulunamadı — çağrı DELETE'in ardına kaymış olabilir."
            )


def test_parti_geri_alma_YONU_HAREKETIN_ISARETINDEN_gelir() -> None:
    """1B-B ÖLÇÜMÜNÜN ÇİVİSİ: geri alma İKİ YÖNÜ TEK ifadeyle çözüyor.

    `_parti_geri_al` satış yolu için ÇOĞALTILMADI (`_parti_iade` YAZILMADI) ve
    bunun tek dayanağı bir SAYI SÖZLEŞMESİDİR: `stock_movements.quantity`
    İŞARETLİ yazılır (alışta `+`, çıkışta `-`) ve parti defterine uygulanan
    delta o işaretli sayının KENDİSİDİR. O yüzden tek bir
    `quantity=quantity-:miktar` hem alışı geri alır (çıkarır) hem satışı
    (negatif çıkarma = ekleme).

    SÖZLEŞME ÖRTÜKTÜR VE TAM BU YÜZDEN ÇİVİLENDİ. İşaret bir gün pozitife
    çevrilip yön `movement_type`a taşınırsa `_parti_geri_al` TERS çalışır:
    satışı geri almak partiyi bir kez daha DÜŞÜRÜR, defter stoktan ayrışır ve
    hiçbir şey bağırmaz — çünkü ne bir istisna atılır ne bir kısıt ısırır.

    Kapı üç şeyi birden ölçüyor, üçü de AST üzerinde:
      1. Geri alma tek bir ÇIKARMA ifadesi kullanıyor (ekleyen ikizi YOK).
      2. `_config`in iki `stock_sign`ı ZIT işaretli (+1 / -1).
      3. `CONFIG`teki çıkış türlerinin `stock`u NEGATİF.
    """
    defter = (BACKEND / "app" / "parti_defteri.py").read_text(encoding="utf-8")
    geri_al = defter[defter.index("def _parti_geri_al"):]
    geri_al = geri_al[:geri_al.index("class Tuketim")]
    yazan = [
        dugum.value
        for dugum in _calistirilabilir_sabitler(ast.parse(geri_al))
        if "UPDATE product_lots" in dugum.value
    ]
    assert yazan == [
        "UPDATE product_lots SET quantity=quantity-:miktar "
        "WHERE company_id=:cid AND id=:id"
    ], (
        f"`_parti_geri_al`ın parti yazması değişmiş: {yazan}. Yön hareketin "
        "İŞARETİNDEN gelir; ikinci bir (ekleyen) ifade, yönü belgenin "
        "türünden yeniden türetmek zorunda kalırdı."
    )
    # ARAMA AST ÜZERİNDE, HAM METİNDE DEĞİL: `_parti_geri_al`ın KENDİ belgesi
    # `_parti_iade`yi ADIYLA anıyor (neden yazılmadığını anlatmak için) ve ham
    # bir `in` araması onu ihlal sanardı. Bu dosyanın emekli edilen kapısının
    # ölçülmüş dersi: DÜZYAZIDA ANMAK TANIMLAMAK DEĞİLDİR.
    tanimlar = {
        dugum.name
        for dugum in ast.walk(ast.parse(defter))
        if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_parti_iade" not in tanimlar, (
        "`_parti_iade` TANIMLANMIŞ — iki yön TEK ifadeyle çözülüyor (bkz. "
        "`_parti_geri_al` belgesi); ikinci bir yön fonksiyonu, sözleşmeyi iki "
        "yere böler ve ikisi yönü ayrı ayrı türetmek zorunda kalır."
    )

    islemler = ISLEMLER.read_text(encoding="utf-8")
    assert '"stock_sign": 1,' in islemler and '"stock_sign": -1,' in islemler, (
        "`_config`in iki yönü ZIT işaretli değil; geri almanın tek ifadesi "
        "yalnız bu zıtlıkla doğrudur."
    )
    is_akisi = IS_AKISI.read_text(encoding="utf-8")
    assert "stock=-1," in is_akisi, (
        "`workflow.CONFIG`te stoktan DÜŞEN tür kalmamış; 1B-B'nin ikinci "
        "çağıranı kaybolmuş olabilir."
    )


def test_ALIS_yolu_units_resolve_CAGIRMIYOR_olcek_hareketle_AYNI() -> None:
    """Kapsam sınırı ADIYLA çivili: parti miktarı = hareket miktarı.

    Alış yolu bugün ham birimle çalışıyor (`purchase_items.quantity` ne
    girildiyse odur). Parti defterine ÜÇÜNCÜ bir ölçek sokmak, üç sayıdan
    hangisinin doğru olduğunu sorulamaz yapardı. Birim çözümü buraya
    girdiği gün bu kapı kırmızı olur ve o gün ölçeğin üç yerde birden
    değişmesi gerektiği İNCELEMEYE zorlanır.
    """
    kaynak = (BACKEND / "app" / "parti_defteri.py").read_text(encoding="utf-8")
    # YORUMLAR DÜŞÜLÜYOR: bu dosyanın kendi gerekçesi `units.resolve`u ADIYLA
    # anıyor ve anmak ÇAĞIRMAK DEĞİLDİR (emekli edilen kapının aynı dersi).
    # AST hem yorumları hem de belge dizgilerinin İÇİNİ zaten dışarıda
    # bırakır — burada aranan ÇAĞRIDIR.
    agac = ast.parse(kaynak)
    cagrilanlar = {
        f"{getattr(dugum.func.value, 'id', '')}.{dugum.func.attr}"
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Attribute)
    }
    assert "units.resolve" not in cagrilanlar
    ithaller = {
        (dugum.module or "") for dugum in ast.walk(agac)
        if isinstance(dugum, ast.ImportFrom)
    }
    assert not any(modul.split(".")[-1] == "units" for modul in ithaller), ithaller
    # Gerekçe kodun yanında duruyor, kaydın içinde değil.
    assert "PARTİ MİKTARI = HAREKET MİKTARI" in kaynak


def test_kalem_lot_kodu_sinirlari_TEK_yerden() -> None:
    """`TransactionItem.lot_code` sınırı göçün sütunuyla AYNI: 80.

    İki yerde iki farklı sınır olsaydı büyük olan sessizce kesilirdi ve
    kesilen kod, defterdeki partiyle EŞLEŞMEZDİ — ikinci alış aynı partiyi
    bulamaz, ayrı bir satır açardı.
    """
    semalar = (BACKEND / "app" / "schemas.py").read_text(encoding="utf-8")
    assert "lot_code: str | None = Field(default=None, max_length=80)" in semalar
    assert "KALEM_KODU_UZUNLUK = 80" in GOC.read_text(encoding="utf-8")


def _calistirilabilir_sabitler(agac: ast.AST):
    """Belge dizgisi OLMAYAN `Constant` dizgeleri. Emekli kapının dersi."""
    belgeler: set[int] = set()
    for dugum in ast.walk(agac):
        if isinstance(
            dugum, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            govde = getattr(dugum, "body", [])
            if (
                govde
                and isinstance(govde[0], ast.Expr)
                and isinstance(govde[0].value, ast.Constant)
                and isinstance(govde[0].value.value, str)
            ):
                belgeler.add(id(govde[0].value))
    for dugum in ast.walk(agac):
        if (
            isinstance(dugum, ast.Constant)
            and isinstance(dugum.value, str)
            and id(dugum) not in belgeler
        ):
            yield dugum


# ----------------------------------------------------------- davranış ---

def test_alis_yolu_parti_defterini_ACIYOR_ve_GERI_ALIYOR(tmp_path: Path) -> None:
    """Uçtan uca: aç, ekle, ayır, çatış, azalt, sil, komşuya gösterme.

    Alt süreçte GERÇEK ŞEMA ile koşuyor (deponun mevcut kalıbı): `app.main`
    açılışta alembic'i sürüyor, yani göç 0073 de bu turda uygulanıyor.
    """
    veritabani = tmp_path / "1b-a-alis-lot.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    tamamlandi = subprocess.run(
        [sys.executable, "-c", _DAVRANIS], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=600,
    )
    assert tamamlandi.returncode == 0, tamamlandi.stdout + "\n" + tamamlandi.stderr


_DAVRANIS = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app

client = TestClient(app)


def ok(cevap):
    assert cevap.status_code < 300, (cevap.status_code, cevap.text)
    return cevap.json() if cevap.content else None


def giris(kullanici, sifre, yeni=None):
    cevap = client.post('/api/auth/login',
                        json={'username': kullanici, 'password': sifre})
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    baslik = {'Authorization': 'Bearer ' + govde['access_token'],
              'X-Company-ID': str(govde['companies'][0]['id'])}
    if yeni:
        degisti = client.post('/api/auth/change-password', headers=baslik,
                              json={'current_password': sifre, 'new_password': yeni})
        assert degisti.status_code == 200, degisti.text
        baslik['Authorization'] = 'Bearer ' + degisti.json()['access_token']
    return baslik, int(govde['companies'][0]['id'])


baslik, cid = giris('admin', 'admin123', 'AlisLot!123')
ok(client.put('/api/company-settings', headers=baslik,
              json={'negative_stock_policy': 'allow', 'credit_limit_policy': 'block'}))

depo_a = ok(client.get('/api/warehouses', headers=baslik))[0]['id']
depo_b = ok(client.post('/api/warehouses', headers=baslik,
                        json={'name': 'Parti B Deposu', 'code': 'PBD'}))['id']
urun = ok(client.post('/api/products', headers=baslik,
                      json={'name': 'Partili Ürün', 'purchase_price': 10,
                            'sale_price': 20, 'vat_rate': 20, 'stock': 0,
                            'unit': 'Adet'}))['id']
# İKİNCİ ÜRÜN ZORUNLU, tercih DEĞİL: `TransactionCreate` aynı ürünü bir
# belgede İKİ SATIRDA kabul ETMİYOR ("Aynı ürün belgede birden fazla satırda
# bulunamaz") — ÖLÇÜLDÜ. Yani "bir alışta iki parti" ancak İKİ AYRI ÜRÜNLE
# kurulabilir; aynı ürünün iki partisi iki AYRI belge ister.
urun_b = ok(client.post('/api/products', headers=baslik,
                        json={'name': 'Partili Ürün B', 'purchase_price': 8,
                              'sale_price': 15, 'vat_rate': 20, 'stock': 0,
                              'unit': 'Adet'}))['id']
urun2 = ok(client.post('/api/products', headers=baslik,
                       json={'name': 'Partisiz Ürün', 'purchase_price': 5,
                             'sale_price': 9, 'vat_rate': 20, 'stock': 0,
                             'unit': 'Adet'}))['id']
tedarikci = ok(client.post('/api/suppliers', headers=baslik,
                           json={'name': 'Parti Tedarikçisi'}))['id']


def alis(kalemler, depo=None, **fazla):
    govde = {'entity_id': tedarikci, 'transaction_date': '2026-09-08',
             'warehouse_id': depo or depo_a, 'items': kalemler}
    govde.update(fazla)
    return client.post('/api/purchases', headers=baslik, json=govde)


def partiler(pid):
    return ok(client.get(f'/api/products/{pid}/lots', headers=baslik))


def kalem(pid, adet, kod=None, skt=None):
    satir = {'product_id': pid, 'quantity': adet, 'unit_price': 10, 'vat_rate': 20}
    if kod is not None:
        satir['lot_code'] = kod
    if skt is not None:
        satir['expiry_date'] = skt
    return satir


# --- 1. İKİ PARTİLİ + BİR PARTİSİZ kalem -> İKİ parti satırı --------------
ilk = ok(alis([
    kalem(urun, 5, 'LOT-A', '2027-01-31'),
    kalem(urun_b, 3, 'LOT-B', '2027-06-30'),
    kalem(urun2, 7),
]))
a = partiler(urun)['lots']
b = partiler(urun_b)['lots']
assert len(a) == 1 and len(b) == 1, (a, b)
assert a[0]['lot_code'] == 'LOT-A' and Decimal(str(a[0]['quantity'])) == Decimal('5'), a
assert b[0]['lot_code'] == 'LOT-B' and Decimal(str(b[0]['quantity'])) == Decimal('3'), b
assert str(a[0]['expiry_date'])[:10] == '2027-01-31', a
assert a[0]['warehouse_id'] == depo_a and b[0]['warehouse_id'] == depo_a, (a, b)
# Partisiz ürünün hiç parti satırı YOK.
assert partiler(urun2)['lots'] == []

# Hareketler: partili kalemler `lot_id` TAŞIR, partisiz kalem NULL.
with SessionLocal() as db:
    satirlar = db.execute(text(
        "SELECT product_id, lot_id FROM stock_movements "
        "WHERE company_id=:cid AND reference_type='purchases' AND reference_id=:rid"
    ), {'cid': cid, 'rid': ilk['id']}).all()
tasiyan = {int(p): l for p, l in satirlar}
assert tasiyan[int(urun2)] is None, satirlar
assert tasiyan[int(urun)] is not None and tasiyan[int(urun_b)] is not None, satirlar

# --- 2. AYNI kod, AYNI depo -> miktar EKLENİR (yeni satır DEĞİL) ----------
ikinci = ok(alis([kalem(urun, 4, 'LOT-A', '2027-01-31')]))
cevap = partiler(urun)
assert len(cevap['lots']) == 1, cevap
assert Decimal(str(cevap['lots'][0]['quantity'])) == Decimal('9'), cevap

# --- 3. AYNI kod, BAŞKA depo -> AYRI satır --------------------------------
ucuncu = ok(alis([kalem(urun, 2, 'LOT-A', '2027-01-31')], depo=depo_b))
cevap = partiler(urun)
assert len(cevap['lots']) == 2, cevap
b_deposu = [s for s in cevap['lots'] if s['warehouse_id'] == depo_b]
assert len(b_deposu) == 1 and Decimal(str(b_deposu[0]['quantity'])) == Decimal('2'), cevap

# --- 4. SKT ÇATIŞMASI -> 422 LOT_SKT_CELISKI, hiçbir şey yazılmaz ---------
catisma = alis([kalem(urun, 1, 'LOT-A', '2028-12-31')])
assert catisma.status_code == 422, (catisma.status_code, catisma.text)
assert catisma.json()['detail']['code'] == 'LOT_SKT_CELISKI', catisma.text
cevap = partiler(urun)
kodlar = {(s['lot_code'], s['warehouse_id']): s for s in cevap['lots']}
assert Decimal(str(kodlar[('LOT-A', depo_a)]['quantity'])) == Decimal('9'), cevap

# --- 5. GÜNCELLEME kalemi azaltır -> parti AZALIR -------------------------
guncel = client.put(f"/api/purchases/{ikinci['id']}", headers=baslik, json={
    'entity_id': tedarikci, 'transaction_date': '2026-09-08',
    'warehouse_id': depo_a, 'items': [kalem(urun, 1, 'LOT-A', '2027-01-31')],
})
assert guncel.status_code < 300, guncel.text
cevap = partiler(urun)
kodlar = {(s['lot_code'], s['warehouse_id']): s for s in cevap['lots']}
# 9 -> (4 geri alındı) 5 -> (1 eklendi) 6
assert Decimal(str(kodlar[('LOT-A', depo_a)]['quantity'])) == Decimal('6'), cevap

# --- 6. SİLME -> parti SIFIRA döner, SATIR KALIR --------------------------
assert client.delete(f"/api/purchases/{ikinci['id']}", headers=baslik).status_code == 204
assert client.delete(f"/api/purchases/{ilk['id']}", headers=baslik).status_code == 204
cevap = partiler(urun)
kodlar = {(s['lot_code'], s['warehouse_id']): s for s in cevap['lots']}
assert Decimal(str(kodlar[('LOT-A', depo_a)]['quantity'])) == Decimal('0'), cevap
assert Decimal(str(partiler(urun_b)['lots'][0]['quantity'])) == Decimal('0'), cevap
# Tükenmiş parti SİLİNMEDİ — satır geri çağırmanın kanıtıdır (0067).
assert len(cevap['lots']) == 2, cevap
assert len(partiler(urun_b)['lots']) == 1, cevap

# --- 7. BAŞKA YERDE TÜKETİLMİŞ parti -> silme 409 ------------------------
dorduncu = ok(alis([kalem(urun, 6, 'LOT-C', '2027-09-30')]))
with SessionLocal() as db:
    db.execute(text(
        "UPDATE product_lots SET quantity=0 "
        "WHERE company_id=:cid AND product_id=:pid AND lot_code='LOT-C'"
    ), {'cid': cid, 'pid': urun})
    db.commit()
reddedildi = client.delete(f"/api/purchases/{dorduncu['id']}", headers=baslik)
assert reddedildi.status_code == 409, (reddedildi.status_code, reddedildi.text)
assert reddedildi.json()['detail']['code'] == 'LOT_MIKTARI_EKSIYE_DUSER', reddedildi.text
# Belge DURUYOR: red bir iptal değil, bir DURDURMADIR.
assert ok(client.get(f"/api/purchases/{dorduncu['id']}", headers=baslik))

# --- 8. KİRACI SINIRI: komşunun partisi GÖRÜNMEZ -------------------------
# Komşu SATIRDAN kuruluyor, uçtan DEĞİL: burada ölçülen şey firma kurma
# akışı değil, OKUMA UCUNUN kiracı yüklemidir. Uçtan kurmak testi ikinci bir
# yolun sağlığına bağlardı.
from datetime import datetime, timezone

with SessionLocal() as db:
    komsu_cid = db.execute(text(
        "INSERT INTO companies (name, is_active, created_at) "
        "VALUES ('Parti Komşu A.Ş.', 1, :simdi) RETURNING id"
    ), {'simdi': datetime.now(timezone.utc)}).scalar_one()
    komsu_depo = db.execute(text(
        "INSERT INTO warehouses (company_id, name, is_active, is_default) "
        "VALUES (:cid, 'Komşu Deposu', 1, 1) RETURNING id"
    ), {'cid': komsu_cid}).scalar_one()
    komsu_urun = db.execute(text(
        "INSERT INTO products (company_id, name, unit, sale_price, active) "
        "VALUES (:cid, 'Komşu Ürünü', 'Adet', 10, 1) RETURNING id"
    ), {'cid': komsu_cid}).scalar_one()
    db.execute(text(
        "INSERT INTO product_lots "
        "(company_id, product_id, lot_code, expiry_date, quantity, warehouse_id, created_at) "
        "VALUES (:cid, :pid, 'LOT-A', '2027-01-31', 42, :wid, :simdi)"
    ), {'cid': komsu_cid, 'pid': komsu_urun, 'wid': komsu_depo,
        'simdi': datetime.now(timezone.utc)})
    db.commit()

assert komsu_cid != cid
# Komşunun ÜRÜNÜ bizim başlığımızla 404 — parti listesi ürün kapısının
# ARKASINDADIR ve o kapı kiracı yüklemi taşıyor.
gorunen = client.get(f'/api/products/{komsu_urun}/lots', headers=baslik)
assert gorunen.status_code == 404, (gorunen.status_code, gorunen.text)
# Komşunun AYNI KODLU partisi bizim listemize SIZMIYOR.
bizim = partiler(urun)['lots']
assert all(Decimal(str(s['quantity'])) != Decimal('42') for s in bizim), bizim
assert all(s['warehouse_id'] != komsu_depo for s in bizim), bizim

client.close()
print('1B-A DAVRANIS TAMAM')
'''

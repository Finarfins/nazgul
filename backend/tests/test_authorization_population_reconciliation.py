"""Yetki denetiminin 67 / 23 / 28 popülasyonları — SAYIYLA DEĞİL, ÜYELİKLE.

Konu: yetki denetiminin ürettiği üç rakamın uzlaştırılması.

--- NEDEN BU DOSYA VAR ---------------------------------------------------------

Denetim iki rakam üretti ve ikisi bir süre "uzlaştırılmamış" kaldı:

* **89** — middleware izni ``read`` olan kimlikli operasyon sayısı. "Rolü
  çözülemeyen hesap, fail-closed düzeltmesinden ÖNCE nereye ulaşırdı?"
  sorusunun doğru cevabı: eski varsayılan tam olarak ``{"read"}`` veriyordu.
* **94** — hiçbir kontrolün altı rolden birini reddedemediği operasyon sayısı.
  "Yetki nerede yapısal olarak HAYIR diyemiyor?" sorusunun doğru cevabı.
* **67** — kesişim: 95'in ``read`` alt kümesi (denetim anında 66 idi).

İlişki ``90 = 67 + 23`` ve ``97 = 67 + 30`` (denetim anında ``89 = 66 + 23``
ve ``94 = 66 + 28``; ``95 -> 97`` outbox okuma yüzeyinin iki ucudur, bkz.
``EXPECTED_UNDENIABLE`` üstündeki SAYAÇ HAREKETİ notu). Bu ilişki bir süre yalnız DÜZ YAZI
olarak vardı ve inceleme haklı olarak reddetti: **yanlış sınıflanmış ya da
atlanmış tek bir uç, büyüklükleri KORUYARAK ilişkiyi geçersiz kılabilir.**
Doğru kanıt sayı değil, ÜYELİKTİR — bu yüzden aşağıdaki iki küme elle yazıldı
ve türetilen kümelerle **eşitlik** olarak karşılaştırılıyor.

--- ÇAPALAR NEDEN ELLE YAZILDI -------------------------------------------------

Kümeleri ``ROLE_PERMISSIONS``tan ya da türetimin kendisinden okusaydık, kaynağı
bozan bir mutasyon çapayı da birlikte kaydırır ve kapı sessizce yeşil kalırdı.
Bu depoda artık standart kural: **bir iddianın çapası, doğruladığı kaynaktan
bağımsız yazılır.**

--- İKİ SINIR, DÜRÜSTÇE --------------------------------------------------------

1. ``required_permission`` yol parametrelerini bilmez; somut yol üretirken
   ``{kind}`` için ``orders`` kullanılıyor. Denetimin ilk ölçümünde her parametre
   yerine ``1`` konmuştu ve ``DELETE /api/{kind}/{transaction_id}`` sahte biçimde
   ``__admin_only__``a düşmüştü. Doğru sayı 0'dı; bu dosya o düzeltmeyi taşır.
2. Rakamlar denetim anındaki ağacı (`e6f46b8`) tarif eder; PR #57 sonrasında
   üçü de birer arttı (bkz. sabitlerin üstündeki SAYAÇ HAREKETİ notu). Bu head'de üç
   self-servis ucu yetki kapısına HİÇ girmiyor (bkz. ``SELF_SERVICE_API``);
   ``required_permission`` onlar için hâlâ ``read`` döndürdüğü için yukarıdaki
   aritmetik aynen üretilebiliyor. Aradaki fark ayrıca ölçülüyor.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "sqlite:///./__x.db")

from fastapi.routing import APIRoute  # noqa: E402

from app.auth import SELF_SERVICE_API, required_permission  # noqa: E402
from app.main import PUBLIC_API, app  # noqa: E402

#: --- SAYAÇ HAREKETİ: 326/89/94 -> 327/90/95 (PR #57 birleşmesi) --------------
#:
#: PR #57 tek bir uç ekledi: ``GET /api/payment-allocations/engine-state``.
#: Uç tahsis motorunun AÇIK/KAPALI olduğunu bildiren salt-okuma bir yapılandırma
#: sinyalidir; arayüz "hiç tahsis yok" ile "özellik kapalı"yı ayırt edebilsin
#: diye eklendi. Hiçbir ödeme davranışını ve üretim varsayılanını değiştirmez.
#:
#: ÜÇ SAYAÇ DA BİRER ARTAR, çünkü uç:
#:   * kimlik ister (PUBLIC_API'de değil)          -> 326 -> 327
#:   * ``required_permission`` ile ``read``e çözülür -> 89 -> 90
#:   * HANDLER'da hiçbir ek kapısı YOKTUR, yani hiçbir rol değeriyle
#:     reddedilemez; ``GUARDED_READ_OPERATIONS``a girmez -> 94 -> 95
#:
#: 94 -> 95 ARTIŞI SESSİZ DEĞİLDİR. Bu sayaç izlenen bir metriktir: middleware'in
#: hiçbir rol için HAYIR diyemediği operasyon sayısı. 95'inci rota yukarıdaki
#: uçtur ve artış, yetki yeniden tasarımı beklenirken KABUL EDİLMİŞ bir artıştır.
#: Uca gerçek bir izin vermek bilinçli olarak BU DEĞİŞİKLİĞİN DIŞINDA bırakıldı;
#: o bir tasarım kararıdır ve bir kesinti düzeltmesine bağlanmamalıdır.
#: AÇIK SORU: ``engine-state`` kendi izniyle mi korunmalı, yoksa self-servis
#: kümesine mi taşınmalı? Yetki yeniden tasarımında karara bağlanacak.
#:
#: Maruziyet: tek bir boolean yapılandırma bayrağı. Kiracı verisi yok; uç
#: ``company_id(request)`` çağırdığı için kiracı kapısı korunur.
#: SAYAÇ HAREKETİ — outbox OKUMA yüzeyi (FIELD_STOK_OUTBOX açılış koşulu 2).
#: İki salt-okuma uç eklendi: GET /api/field-integration-events ve
#: .../summary. İkisi de `farm.view` iznine çözülür.
#:
#: ÜÇ SAYACIN İKİSİ ARTAR:
#:   * kimlik ister (PUBLIC_API'de değil)            -> 328 -> 330
#:   * `read`E ÇÖZÜLMEZ (`farm.view`), yani EXPECTED_READ DEĞİŞMEZ -> 91
#:   * altı rolün altısı da `farm.view` taşıdığı için hiçbir rol
#:     değeriyle reddedilemez; FARM_HERD_VIEW_OPERATIONS'a girer
#:     ve reddedilemez küme büyür                    -> 95 -> 97
#:
#: 95 -> 97 ARTIŞI SESSİZ DEĞİLDİR ve bedeli AÇIKÇA yazılıyor: kuyruğu
#: okumak `farm.view` demektir ve bu izni altı rolün altısı da taşır, yani
#: tarla verisini görebilen HERKES kuyruğu da görür. Bu bilinçli: kuyruk
#: parsel/sezon/hasat listeleriyle AYNI veriyi başka bir açıdan gösteriyor
#: (hangi faaliyet/hasat stoğa işlenmedi ve neden) — ondan daha dar bir
#: izin, aynı bilgiyi zaten görebilen bir role kapı kapatmak olurdu.
#: Uçlar SALT OKUR; yeniden kuyruklama (koşul 3) bu yüzeyde YOKTUR ve
#: geldiği gün kendi YAZMA iznini gerektirir — `farm.view` ona yetmez.
#: Maruziyet: olayın kimliği, kaynak tipi/kimliği, durumu, deneme sayısı,
#: gerekçe metni ve zaman damgaları. `idempotency_key` DÖNDÜRÜLMÜYOR.
EXPECTED_AUTHENTICATED = 335
EXPECTED_READ = 91
EXPECTED_UNDENIABLE = 99

#: ``read`` isteyen ama HANDLER'da reddedilebilen uçlar: middleware'i geçerler,
#: sonra kendi kapılarına takılırlar. 89'a dahil, 94'e DEĞİL.
#: ELLE YAZILDI — türetimden okunmuyor.
GUARDED_READ_OPERATIONS = {
    ("GET", "/api/customers/{customer_id}/statement.pdf"),
    ("GET", "/api/documents/{kind}/{document_id}/pdf"),
    ("GET", "/api/documents/{kind}/{document_id}/xlsx"),
    ("GET", "/api/exports/products.xlsx"),
    ("GET", "/api/exports/warehouse-count-variance.xlsx"),
    ("GET", "/api/notifications/consents"),
    ("GET", "/api/notifications/consents/{consent_id}/events"),
    ("GET", "/api/notifications/counters"),
    ("GET", "/api/notifications/outbox"),
    ("GET", "/api/notifications/rules"),
    ("GET", "/api/notifications/templates"),
    ("GET", "/api/notifications/{notification_id}/preview"),
    # 20260812: firmasız güvenlik denetim satırlarının okuma yolu. `read`
    # görünür ama handler `require_platform_operator` uygular — /platform/backups
    # ile AYNI sınıf, bu yüzden ÇIPLAK read değil KORUMALI read.
    ("GET", "/api/platform/audit"),
    ("GET", "/api/platform/backups"),
    ("GET", "/api/platform/backups/{name}/download"),
    ("GET", "/api/products/{product_id}"),
    ("GET", "/api/products/{product_id}/barcode-label.pdf"),
    ("GET", "/api/products/{product_id}/label.pdf"),
    ("GET", "/api/products/{product_id}/qr.png"),
    ("GET", "/api/suppliers/{supplier_id}/statement.pdf"),
    ("GET", "/api/warehouse-transfers/{transfer_id}"),
    ("POST", "/api/platform/backups"),
    ("POST", "/api/platform/backups/{name}/restore"),
    ("POST", "/api/platform/backups/{name}/verify"),
}

#: Altı rolün ALTISI da taşıdığı için reddedilemeyen, ama eski ``{"read"}``
#: varsayılanının HİÇ vermediği uçlar. 94'e dahil, 89'a DEĞİL. ELLE YAZILDI.
FARM_HERD_VIEW_OPERATIONS = {
    ("GET", "/api/animal-births"),
    ("GET", "/api/animal-breedings"),
    ("GET", "/api/animal-breedings/{breeding_id}"),
    ("GET", "/api/animal-groups"),
    ("GET", "/api/animal-groups/{group_id}"),
    ("GET", "/api/animal-movements"),
    ("GET", "/api/animal-vaccinations"),
    ("GET", "/api/animal-weights"),
    ("GET", "/api/animals"),
    ("GET", "/api/animals/{animal_id}"),
    ("GET", "/api/crop-seasons"),
    ("GET", "/api/crop-seasons/{season_id}"),
    ("GET", "/api/farm-parcels"),
    ("GET", "/api/farm-parcels/{parcel_id}"),
    ("GET", "/api/farm-parcels/{parcel_id}/timeline"),
    ("GET", "/api/farms"),
    ("GET", "/api/farms/{farm_id}"),
    ("GET", "/api/field-activities"),
    ("GET", "/api/field-activities/{activity_id}"),
    ("GET", "/api/field-dashboard"),
    ("GET", "/api/field-harvest-decision"),
    ("GET", "/api/field-harvests"),
    ("GET", "/api/field-integration-events"),
    ("GET", "/api/field-integration-events/summary"),
    ("GET", "/api/field-safety"),
    ("GET", "/api/field-tasks"),
    # BKÜ kataloğu (göç 20260901_0063). Okuma yüzeyi parsel/sezon/hasat
    # listeleriyle AYNI role bağlı: katalog, o listelerin yanında duran ve
    # aynı tarla verisini besleyen bir tanım tablosudur. Maruziyet: ürün
    # kimliği ve adı, bitki, ruhsat no, iki bekleme süresi, not ve durum.
    # YAZMA `farm.manage` ister ve bu kümede DEĞİLDİR — `farm.view` yasal
    # bekleme sürelerini değiştirmeye yetmez.
    ("GET", "/api/plant-protection-products"),
    ("GET", "/api/plant-protection-products/{ppp_id}"),
    ("GET", "/api/herd-dashboard"),
    ("GET", "/api/herd-fertility"),
    ("GET", "/api/milk-yields"),
    ("GET", "/api/vaccination-calendar"),
}

#: REDDEDEBİLEN handler kapıları. ``require_logout_csrf`` KASITLI olarak yok:
#: CSRF doğrular, ROL üzerinden reddedemez — onu bu listeye koymak
#: ``POST /api/auth/logout``u sahte biçimde "korunuyor" sayardı (ölçüldü).
DENYING_GUARDS = frozenset(
    {
        "require_platform_operator",
        "require_product_detail_stock",
        "require_audit_user_management",
        "require_user_directory_management",
        "_require_permission",
        "_require_stock_permission",
        "_require",
    }
)

_module_functions: dict[str, dict[str, ast.AST]] = {}


def _functions(module_name: str) -> dict[str, ast.AST]:
    if module_name not in _module_functions:
        source = Path(sys.modules[module_name].__file__).read_text(encoding="utf-8")
        table: dict[str, ast.AST] = {}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                table.setdefault(node.name, node)
        _module_functions[module_name] = table
    return _module_functions[module_name]


def _calls_denying_guard(node, module_name: str, depth: int, seen: set[str]) -> bool:
    if node is None:
        return False
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        target = sub.func
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
        if name in DENYING_GUARDS:
            return True
        if name and depth and name not in seen and name in _functions(module_name):
            seen.add(name)
            if _calls_denying_guard(_functions(module_name)[name], module_name, depth - 1, seen):
                return True
    return False


def _walk(routes, prefix: str = ""):
    """``app.routes`` dahil edilen router'ları iç düğümde saklar; düz gezinti
    neredeyse hiçbir şey bulmaz ve bu dosya HİÇBİR ŞEY ÖLÇMEDEN yeşil kalırdı."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield f"{prefix}{route.path}", route
            continue
        original_router = getattr(route, "original_router", None)
        include_context = getattr(route, "include_context", None)
        if original_router is not None and include_context is not None:
            yield from _walk(original_router.routes, f"{prefix}{include_context.prefix}")


#: ``read`` isteyen ve HANDLER'da da hiçbir ek kapısı OLMAYAN uçlar — yani
#: middleware'in hiçbir rol değeriyle reddedemediği ÇIPLAK read popülasyonu.
#: 90'ın içinde, 23'ün dışında; 95'in ``read`` alt kümesi.
#: ELLE YAZILDI — türetimden okunmuyor.
#:
#: NEDEN BU ÇAPA VAR: ``naked_read`` yalnızca ``read_ops - guarded`` olarak
#: türetiliyordu ve SADECE SAYIYLA pinliydi. Bölünme iddiaları (kesişim boş,
#: birleşim tam, ``undeniable - naked_read == farm_herd``) korunurken iki
#: korumasız rota TAKAS EDİLEBİLİR: 90/23/67/95 aynı kalır, her özdeşlik
#: geçerli kalır, ama REDDEDİLEMEYEN KÜME SESSİZCE BAŞKA BİR KÜME OLUR. Bu
#: dosyanın koruduğu metrik tam olarak o kümedir; diğer iki popülasyon zaten
#: üyelikle çapalıydı, güvenlik iddiasını taşıyan popülasyon değildi.
NAKED_READ_OPERATIONS = {
    ("GET", "/api/analytics/seasonal-plan"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/branches"),
    ("GET", "/api/companies"),
    ("GET", "/api/company-settings"),
    ("GET", "/api/customers"),
    ("GET", "/api/customers/{customer_id}"),
    ("GET", "/api/customers/{customer_id}/documents"),
    ("GET", "/api/customers/{customer_id}/statement"),
    ("GET", "/api/dashboard"),
    ("GET", "/api/demo/summary"),
    ("GET", "/api/exchange-rates"),
    ("GET", "/api/imports/customers/template.xlsx"),
    ("GET", "/api/imports/products/template.xlsx"),
    ("GET", "/api/imports/suppliers/template.xlsx"),
    ("GET", "/api/invoices"),
    ("GET", "/api/invoices/{invoice_id}"),
    ("GET", "/api/invoices/{invoice_id}/einvoice/status"),
    ("GET", "/api/invoices/{invoice_id}/history"),
    ("GET", "/api/invoices/{invoice_id}/pdf"),
    ("GET", "/api/machines"),
    ("GET", "/api/machines/{machine_id}"),
    ("GET", "/api/machines/{machine_id}/hour-readings"),
    ("GET", "/api/machines/{machine_id}/ownership-history"),
    ("GET", "/api/notifications"),
    ("GET", "/api/orders"),
    ("GET", "/api/orders/last-sale-price"),
    ("GET", "/api/part-supersessions"),
    ("GET", "/api/payment-allocations/charges/{receivable_charge_id}"),
    ("GET", "/api/payment-allocations/engine-state"),
    ("GET", "/api/payment-allocations/orders/{order_id}"),
    ("GET", "/api/payment-allocations/payments/{payment_id}"),
    ("GET", "/api/pos/lookup"),
    ("GET", "/api/products"),
    ("GET", "/api/products/stock/movements/all"),
    ("GET", "/api/products/{product_id}/current"),
    ("GET", "/api/products/{product_id}/warehouse-stock"),
    ("GET", "/api/purchases"),
    ("GET", "/api/purchases/last-purchase-price"),
    ("GET", "/api/quick-pick"),
    ("GET", "/api/search"),
    ("GET", "/api/search/parts"),
    ("GET", "/api/suppliers"),
    ("GET", "/api/suppliers/{supplier_id}"),
    ("GET", "/api/suppliers/{supplier_id}/documents"),
    ("GET", "/api/suppliers/{supplier_id}/statement"),
    ("GET", "/api/technician-profiles"),
    ("GET", "/api/warehouses"),
    ("GET", "/api/warehouses/counts"),
    ("GET", "/api/warehouses/counts/{count_id}"),
    ("GET", "/api/warehouses/replenishment"),
    ("GET", "/api/warehouses/stock"),
    ("GET", "/api/warehouses/transfers"),
    ("GET", "/api/warehouses/{warehouse_id}"),
    ("GET", "/api/work-order-attachments/{work_order_id}"),
    ("GET", "/api/work-order-attachments/{work_order_id}/{attachment_id}/download"),
    ("GET", "/api/work-orders"),
    ("GET", "/api/work-orders/technicians"),
    ("GET", "/api/work-orders/{work_order_id}"),
    ("GET", "/api/work-orders/{work_order_id}/invoice"),
    ("GET", "/api/work-orders/{work_order_id}/labor-lines"),
    ("GET", "/api/work-orders/{work_order_id}/parts"),
    ("GET", "/api/workflow/{kind}"),
    ("GET", "/api/workflow/{kind}/{doc_id}"),
    ("GET", "/api/{kind}/{transaction_id}"),
    ("POST", "/api/auth/change-password"),
    ("POST", "/api/auth/logout"),
}

def _populations():
    authenticated, read_ops, guarded, farm_herd = set(), set(), set(), set()
    for path, route in _walk(app.routes):
        if not path.startswith("/api") or path in PUBLIC_API:
            continue
        module_name = route.endpoint.__module__
        guarded_route = _calls_denying_guard(
            _functions(module_name).get(route.endpoint.__name__), module_name, 4, set()
        ) or any(
            getattr(getattr(dependency, "dependency", None), "__name__", None)
            in DENYING_GUARDS
            for dependency in route.dependencies
        )
        for method in sorted(route.methods):
            concrete = path
            for parameter in route.param_convertors:
                concrete = concrete.replace(
                    "{%s}" % parameter, "orders" if parameter == "kind" else "1"
                )
            permission = required_permission(method, concrete)
            operation = (method, path)
            authenticated.add(operation)
            if permission == "read":
                read_ops.add(operation)
                if guarded_route:
                    guarded.add(operation)
            elif permission in {"farm.view", "herd.view"}:
                farm_herd.add(operation)
    return authenticated, read_ops, guarded, farm_herd


def test_authenticated_operation_count() -> None:
    authenticated, _, _, _ = _populations()
    assert len(authenticated) == EXPECTED_AUTHENTICATED


def test_guarded_read_membership_not_just_magnitude() -> None:
    """Türetilen küme, elle yazılan çapaya ÜYE ÜYE eşit olmalı."""
    _, _, guarded, _ = _populations()
    assert guarded == GUARDED_READ_OPERATIONS
    assert len(guarded) == 24


def test_farm_and_herd_view_membership_not_just_magnitude() -> None:
    _, _, _, farm_herd = _populations()
    assert farm_herd == FARM_HERD_VIEW_OPERATIONS
    # 28 -> 30: outbox okuma yüzeyinin iki ucu (bkz. SAYAÇ HAREKETİ notu).
    # 30 -> 32: BKÜ kataloğunun iki OKUMA ucu (göç 20260901_0063). Yazma
    # uçları (POST/PUT) bu kümede DEĞİL — onlar `farm.manage` istiyor.
    assert len(farm_herd) == 32


def test_eightynine_partitions_into_sixtysix_and_twentythree() -> None:
    _, read_ops, guarded, _ = _populations()
    naked_read = read_ops - guarded
    assert len(read_ops) == EXPECTED_READ
    # ÜYELİK — sayı değil. Bir takas büyüklükleri koruyarak kümeyi değiştirirse
    # burası kırmızı yanar; sayı iddiaları o takası göremez.
    assert naked_read == NAKED_READ_OPERATIONS
    # 66 -> 67: PR #57'nin eklediği ``GET /api/payment-allocations/engine-state``
    # handler kapısı olmadığı için ÇIPLAK read'e düşer (bkz. SAYAÇ HAREKETİ notu).
    assert len(naked_read) == 67
    # Bölünme: kesişim boş ve birleşim TAM. Sayılar tutup üyelik tutmazsa burası kırmızı.
    assert guarded <= read_ops
    assert naked_read | guarded == read_ops
    assert naked_read & guarded == set()


def test_ninetyfour_partitions_into_sixtysix_and_twentyeight() -> None:
    _, read_ops, guarded, farm_herd = _populations()
    naked_read = read_ops - guarded
    undeniable = naked_read | farm_herd
    assert len(undeniable) == EXPECTED_UNDENIABLE
    assert naked_read & farm_herd == set()
    assert undeniable - naked_read == farm_herd


def test_the_detector_actually_ran() -> None:
    """Boş bir tarama her bölünmeyi sahte biçimde sağlardı."""
    authenticated, read_ops, guarded, farm_herd = _populations()
    assert len(authenticated) > 300
    assert guarded and farm_herd
    assert _calls_denying_guard.__doc__ is None  # yardımcı, davranış değil
    # Tarayıcı gerçekten AST okuyor: en az bir modül tablosu doldu.
    assert _module_functions


def test_self_service_routes_were_inside_the_sixtysix() -> None:
    """İki turun bağlantısı: muaf tutulan üç uç, reddedilemeyenlerin içindeydi.

    Bu yüzden muafiyet yeni bir açıklık yaratmıyor — o üç uç zaten hiçbir rol
    tarafından reddedilemiyordu; değişen tek şey, rolü ÇÖZÜLEMEYEN hesabın da
    onlara ulaşabilmesi.
    """
    _, read_ops, guarded, _ = _populations()
    naked_read = read_ops - guarded
    self_service_operations = {
        operation for operation in naked_read if operation[1] in SELF_SERVICE_API
    }
    assert {path for _method, path in self_service_operations} == set(SELF_SERVICE_API)
    assert len(self_service_operations) == 3

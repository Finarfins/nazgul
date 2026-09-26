# H84 — SQLAlchemy 2.1 denetimi (ölçüm raporu)

**Tarih:** 2026-09-26 · **Taban:** `origin/develop` `78b9ad0` (#162 birleşmesi)
**Bağlam:** H83 (#158) `backend/requirements.txt` satırını `sqlalchemy>=2.0,<2.1`
olarak sabitledi. Sebep: 2.1.0'da `JSON().python_type` `dict` yerine `object`
döndürmeye başladı ve `tests/test_kiraci_disa_aktarim.py::test_str_dalina_dusen_sutun_tipi_yok`
kırmızıya döndü. Bu rapor, 2.1'in **bu kod tabanında** neyi değiştirdiğini
ölçüyor. Görüş bildirmiyor, ölçtüğünü yazıyor. Kod değişikliği YOK. Önerilen
her değişiklik bir **bulgu** olarak yazıldı.

---

## 0. Yöntem

| Adım | Ayrıntı |
|---|---|
| Ortam | Proje dışında ayrı bir **karalama venv** (Python 3.12.10). Önce `pip install -r backend/requirements-dev.txt`, ardından `pip install "sqlalchemy==2.1.*"`. Sonuç **SQLAlchemy 2.1.1** oldu (2.1.0'ın ardılı, ölçüm günündeki en yeni 2.1). Proje venv'ine dokunulmadı. |
| Koşu | `PYTHONUTF8=1 PYTHONIOENCODING=utf-8 <venv21>/python backend/run_isolated_tests.py --workers 4 --timeout 900 --report-json …`. Runner alt süreçleri `sys.executable` ile başlattığı için bütün dosyalar 2.1.1 altında koştu. |
| Kontrol | Kırmızı dosya ve SAWarning kaynağı olan bir dosya, proje yorumlayıcısında (SQLAlchemy **2.0.51**, lock sürümü) ikinci kez koşturuldu. |
| Envanter | 2.1 göç kılavuzu (`migration_21`) ve `changelog_21` maddeleri `backend/app`, `backend/alembic` ve testlerde `grep -rnE` ile sayıldı. |

Karalama venv'inde lock'tan sapan paketler şunlar oldu (gevşek `requirements-dev.txt` en yeni sürümleri seçtiği için): alembic 1.20.0 (lock 1.18.5),
psycopg 3.3.6 (lock 3.3.4), fastapi 0.141.1 (lock 0.139.2), pydantic 2.13.5
(lock 2.13.4). CI de aynı gevşek kurulumu yapıyor (§3, PR-3). Bu yüzden bu
ölçüm CI'ın 2.1 altında göreceğiyle aynı koşulda.

---

## 1. Ölçüm: 2.1 altında kırmızı dosyalar ve nedenleri

**Sonuç: 494 dosyadan 493'ü PASS, 1'i FAIL. Toplanan test sayısı 4312. Süre 1344.9 sn.**

| Dosya | Sonuç | İlk başarısız doğrulama | Neden |
|---|---|---|---|
| `backend/tests/test_kiraci_disa_aktarim.py` | FAIL (1 failed, 19 passed) | `test_str_dalina_dusen_sutun_tipi_yok`: `AssertionError: ['supplier_import_profiles.column_map:JSON', 'supplier_import_profiles.page_sections:JSON', 'supplier_price_import_lines.raw:JSON', 'supplier_price_imports.error_detail:JSON']` `assert [...] == []` | 2.1'de `sa.JSON().python_type` → `object`. 2.0.51'de `dict` (aşağıda doğrudan ölçüldü). Test, H49 sınıflandırıcısında (`tests/test_kiraci_disa_aktarim.py:283`) sütun tipini `python_type.__name__` üzerinden `ELE_ALINAN` kümesine eşliyor. `object` bu kümede olmadığı için dört JSON sütunu "str dalına düşen" listesine giriyor. |

Doğrudan ölçüm:

```
2.1.1  JSON=<class 'object'>  Numeric=<class 'decimal.Decimal'>  Integer=<class 'int'>
2.0.51 JSON=<class 'dict'>    Numeric=<class 'decimal.Decimal'>  Integer=<class 'int'>
```

**Kontrol (2.0.51):** `test_kiraci_disa_aktarim.py` 20/20 ve `test_cs1_cek_senet.py` 79/79 yeşil.

**Kırılan uygulama değil, test.** `app/` içinde `python_type` kullanan satır
yok (0 satır). Dışa aktarımın `_seri` işlevi değerin çalışma zamanındaki
tipine göre dallanıyor. JSON sütunundan okunan değer 2.1'de de `dict`/`list`
geliyor, ve bu dalı 19 yeşil test (dışa aktarım/geri yükleme gidiş-dönüşü dahil)
kapsıyor. H83'ün teşhisi 2.1.1'de de aynen geçerli.

**Uyarılar:** 2.1'e özgü `SADeprecationWarning`, `RemovedIn*` ya da
`MovedIn20Warning` **sıfır**. Günlükte görülen tek SQLAlchemy uyarısı şu:
`SAWarning: Skipped unsupported reflection of expression-based index uq_app_users_email_lower`
(36 satır). Aynı uyarı 2.0.51 kontrol koşusunda da çıkıyor, yani 2.1 ile
ilgisi yok (SQLite yansıtmasının ifade-tabanlı indeksi atlaması).

**Kapsam sınırı (ölçülmedi):** `run_isolated_tests.py` SQLite'ı zorluyor.
PG ikizleri bu koşuda Postgres üzerinde **koşmadı**. 2.1 + psycopg 3 + PG16
bileşimi hâlâ ölçülmedi. Bunu kapatmak §4'teki K2 ölçütü.

---

## 2. Kod envanteri: etkilenen kalıplar (dosya/satır sayıları)

Sayım `grep -rnE`, `backend/` altında. "Uygulama" = `app/` + `alembic/`.
"Test" = geri kalan `*.py` (`sandbox/` hariç).

| 2.1 maddesi (kaynak) | Desen | Uygulama satır / dosya | Test satır / dosya | Etki |
|---|---|---|---|---|
| `JSON.python_type` → `object` (2.1.0'da ölçüldü; H83) | `python_type` | **0 / 0** | 4 / 3 | **Tek gerçek kırılma.** `tests/test_kiraci_disa_aktarim.py:283` kırılıyor. `test_54c_push_devices_postgresql.py:299` `Boolean.python_type is bool` kullanıyor (2.1'de değişmedi). `tests/test_requirements_lock.py:55` yalnız docstring. |
| JSON sütunları | `sa.JSON()` sütun tanımı | 4 sütun / 2 göç dosyası (`20260728_0037…:60,96,110`, `20260729_0038…:42`) | — | Yazma/okuma yolu değişmedi (19 dışa aktarım testi yeşil). |
| Row tipleme `Row[Tuple[...]]` → `Row[int, str]` (PEP 646) | `Row[`, `\bRow\b` | **0 / 0** | 1 / 1 (`sqlite3.Row`, SQLAlchemy değil) | Etkisiz. |
| Row erişimi | `._mapping` | 2 / 2 (`app/migrations.py:33`, `app/routers/kiraci_disa_aktarim.py:231`) | 0 | API 2.1'de aynı. Etkisiz. |
| | `._asdict()` | 1 / 1 (`app/routers/products.py:366`) | 0 | `parti_mutabakat.Satir` bir `NamedTuple`, `Row` değil. Etkisiz. |
| `Result.freeze()` yinelenen sütun (#9427) | `.freeze()`, `.tuples()` | 0 / 0 | 0 | Etkisiz. |
| `Session.execute` dönüşleri | `execute(...).fetchall/first/one/scalar/mappings/all` (tek satır) | 20 / 17 | 7 / 5 | Dönüş tipleri 2.1'de değişmedi. Suite yeşil. |
| Session autoflush artık her yürütmede (migration_21) | `autoflush` | 1 / 1 (`app/db.py:148` `autoflush=False`) | 0 | Uygulama saf Core: `declarative_base`/`DeclarativeBase` 0, eşlenmiş sınıf yok. `SungurSession` autoflush'ı kapatıyor. Etkisiz. |
| `declarative_base` / dataclass / composite / loader derinliği | `declarative_base`, `DeclarativeBase`, `MappedAsDataclass`, `composite(` | **0 / 0** | 2 / 1 (`tests/test_field_outbox_writer.py:37,221`: ORM **yokluğunu** doğrulayan kapı) | Etkisiz. |
| `filter_by()` bütün FROM'larda arar (migration_21) | `.filter_by(` | 0 / 0 | 0 | Etkisiz. |
| `text()` / bağ parametresi ad kaçışı (#13534) | `text(` | 1278 / 151 | 2645 / 299 | #13534 yalnız kaçış karakteri içeren adları (`a.b` ile `a_b`) ayırıyor. |
| | `bindparam(` | 51 / 20 | 17 / 10 | Özel karakterli `bindparam` adı: **0** (hepsi `[A-Za-z0-9_]`). Etkisiz. |
| `ClauseElement.params()` kullanımdan kalktı | `.params(` | 0 / 0 | 0 | Etkisiz. |
| `Numeric.decimal_return_scale` (#13424) | `Numeric(` | 111 / 39 | 3 / 3 | `decimal_return_scale` 0, `asdecimal` 0. Parametre verilmediği için davranış aynı. Para testleri yeşil. |
| PG adlı tipler MetaData'ya bağlandı, `inherit_schema` kullanımdan kalktı | `sa.Enum`/`Enum(`, `inherit_schema` | 0 / 0 | 0 | PG ENUM yok. Etkisiz. |
| PG varsayılan sürücü psycopg2 → psycopg 3 | `postgresql+psycopg2`/`psycopg2` | 1 / 1 (`app/routers/mustahsil.py:109`, yorum) | 1 / 1 | Depo zaten açık `postgresql+psycopg://` kullanıyor (test 64 / 60). Etkisiz, çünkü varsayılana güvenilmiyor. |
| Cursor olaylarında DBAPI hatası sarmalanıyor (#13381) | `before_cursor_execute`/`after_cursor_execute` | 0 / 0 | 19 / 12 | Uygulamada yok. Testlerde sayaç ve casus olarak kullanılıyor, hiçbiri hata tipini doğrulamıyor. Suite yeşil. |
| Diğer olay dinleyicileri | `event.listen`/`listens_for` | 4 / 1 (`app/db.py:51,58,71,141`) | 19 / 12 | `connect`/`checkout`/`before_flush` sözleşmesi değişmedi. |
| SQLite yansıtması `Table.kwargs`'a `sqlite_with_rowid`/`sqlite_strict` ekliyor (#13543) | `reflect(`, `autoload_with`, `inspect(` | 272 / 89 (çalışma zamanı `md.reflect`: `app/routers/kiraci_disa_aktarim.py:196`) | 42 / 21 | `.kwargs`/`dialect_options` okuyan satır 0. Yansıtılan tablo DDL'e geri basılmıyor. Kiracı dışa/geri yükleme yeşil. |
| `Session.binds` herkese açık ve değişmez (#13563) | `.binds` | 0 / 0 | 0 | Etkisiz. |
| greenlet artık varsayılan bağımlılık değil (migration_21) | `greenlet`, `asyncio`, `AsyncSession` | 1 / 1 (yorum, `app/whatsapp/bekleyen.py:9`) | 82 / 19 (pytest-asyncio) | Async motor yok. `requirements.lock` içinde `greenlet==3.5.4  # via sqlalchemy` var. Lock yeniden derlenince düşer (bkz. PR-4). |
| Python ≥ 3.11 | CI `python-version`, `Dockerfile` | CI 3.12 (9 iş), `FROM python:3.12-slim` | — | Uyumlu. |
| Legacy `Query` | `.query(`/`Query\b` | 183 / 37 | 16 / 9 | Eşlemler SQL "query" sözcüğü ve FastAPI `Query`. ORM `Session.query` yok. 2.1'de değişiklik yok. |

**Özet:** Uygulama kodunda 2.1'in kırdığı kalıp **sıfır**. Tek etkilenen yer
bir testin tip sınıflandırıcısı (1 dosya, 1 satır bloğu). Kalan maddeler ya
kullanılmıyor ya da 2.1'de davranışı aynı kalıyor, ve bu SQLite hattında
ölçüldü. PG hattı açık (K2).

---

## 3. Göç planı: sıralı küçük PR'lar (her biri tek kalıp)

| Sıra | PR | Tek kalıp | Tahmini dosya | Kabul |
|---|---|---|---|---|
| PR-1 | **Sınıflandırıcıyı tipe bağla** (bulgu, kod değil) | `tests/test_kiraci_disa_aktarim.py:280-288`: `python_type.__name__` yerine önce `isinstance(sutun.type, sa.JSON)` → `"dict"` eşlemesi, geri kalanı `python_type`. Tip SINIFINA bağlı olduğu için 2.0 ve 2.1'de aynı sonucu verir. | 1 test dosyası (+ `docs/durum/pr-NNNN.md`) | 2.0.51'de ve 2.1.1 karalama venv'inde `test_kiraci_disa_aktarim.py` 20/20. Mutasyon: JSON dalı silinince 2.1'de KIRMIZI. |
| PR-2 | **PG ikizlerini 2.1 altında ölç** (dok/ölçüm) | 2.1.1 karalama venv'i, CI'daki gibi dosya başına taze DB, bütün `@pytest.mark.postgresql` ikizleri, yerel PG16. Kırmızılar bu rapora ek olarak yazılır. | 1 dok dosyası | Her ikiz taze DB'de yeşil, ya da kırmızıların listesi ve nedeni. |
| PR-3 | **CI'ı lock'tan kur** (H83'ün ikinci önerisi) | `ci.yml` içinde `pip install -r requirements-dev.txt` adımları (satır 258, 359, 451, 912, 963) → önce `--require-hashes -r requirements.lock`, ardından yalnız dev eklentileri. Dockerfile bunu zaten yapıyor (satır 55). Böylece bir bağımlılık sürümü bir daha "sessizce" CI'a girmez. | 1 (`ci.yml`) + gerekirse shard kapısı dizesi (`deploy/ci-postgresql-shard-kapisi.py`, PG pytest satırına dokunulursa) | CI yeşil. `pip freeze` sürümleri lock ile aynı. |
| PR-4 | **Pini kaldır** | `requirements.txt`: `sqlalchemy>=2.0,<2.1` → `>=2.1,<2.2`. `requirements.lock` gerçek artefakt hash'leriyle yeniden derlenir (greenlet düşebilir). `tests/test_requirements_lock.py::test_sqlalchemy_upper_bound_below_2_1` yeni sınırı isteyecek şekilde yeniden adlandırılır. H83 yorum satırı silinir. | 3 (`requirements.txt`, `requirements.lock`, `test_requirements_lock.py`) | §4'teki K1–K4. |

Sıra gerekçesi: PR-1 pinden bağımsız olarak doğru (tipe bağlı sınıflandırma
sürümden bağımsız). PR-2 ve PR-3 pini kaldırmadan önce kör noktaları kapatıyor.
PR-4 tek satırlık bir sürüm değişikliği olarak kalıyor.

---

## 4. Karar önerisi: pin ne zaman kalkar

Bir tarih değil, bir **ölçüt** kümesi. Dördü birlikte sağlanınca PR-4 açılır:

- **K1.** PR-1 birleşti. `run_isolated_tests.py` 2.1.x altında 0 kırmızı dosya
  veriyor (bugün: 1).
- **K2.** PR-2 ölçümü: bütün PG ikizleri 2.1.x + psycopg 3 + PG16 altında,
  dosya başına taze DB'de yeşil (bugün: **ölçülmedi**).
- **K3.** PR-3 birleşti: CI lock'tan kuruyor. Pin kalkınca sürüm artışı lock
  diff'inde görünür oluyor, gevşek kurulumla sessizce gelmiyor.
- **K4.** 2.1 serisinde kod tabanımızı etkileyen açık bir gerileme yok:
  yeni `2.1.x` changelog'u §2 tablosuna göre taranır ve tabloya satır eklenmez.

O zamana kadar `<2.1` pini **doğru ve yeterli**. Uygulama kodu 2.1 altında
ölçülen hiçbir davranış farkı göstermiyor. Pin yalnız bir test sınıflandırıcısını
ve ölçülmemiş PG hattını koruyor.

---

## Ek: ham çıktılar (karalama dizininde kaldı, depoya girmedi)

- Koşu günlüğü: `h84-21.log` (`exit=1`, "Başarısız test dosyaları: tests/test_kiraci_disa_aktarim.py").
- Rapor: `h84-21.json` (`results` 494 kayıt, `collected` toplamı 4312).
- Kontrol: `h84-20-kontrol.log` (2.0.51, 2/2 PASS).
- Karalama venv'i ölçümden sonra silindi. Proje venv'inde `pip show sqlalchemy` → `2.0.51`.

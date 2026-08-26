# Görev Spesifikasyonu — Test Altyapısı (task_test_infrastructure)

**Bağlam:** 2026-07-24 prod POS çökmesi (`d.replace is not a function`, PR #121/#122).
Frontend, para/miktar alanlarını her zaman string sanıyordu; API bazı uçlarda
(quick-pick, lookup) Decimal'i JSON **sayısı** olarak serileştiriyordu. Elle yazılmış
string mock'lar gerçeği gizledi, bug dört test katmanının dördünün de yokluğunda
CI'dan geçti. Bu görev o dört katmanı kurar ve **her katmanın bugünkü bug'ı
yakaladığını mutasyonla kanıtlar**.

## Kanonik Serileştirme Kararı

**Para/miktar alanları JSON'da sabit ölçekli STRING'dir:** para `"12.50"` (2 ondalık),
miktar `"6.0000"` (4 ondalık). Tek uygulama noktası: `backend/app/pos_contracts.py`
(`MoneyOut` / `QuantityOut`). Pydantic v2 `response_model` + `Decimal` bunu doğal
olarak üretir; ham dict dönen uçlar FastAPI encoder'ı yüzünden sayı sızdırıyordu.

> **Gate notu:** Cowork'e danışma talimatı verildi ancak hiçbir kanaldan erişilemedi
> (CCD oturumu yok, Slack MCP bağlanamadı). Sahibin beyan ettiği eğilim (string)
> uygulandı; karar tek dosyadan çevrilebilir. Nihai onay bu PR'ın gate'inde.

Kapsam bilinçli olarak POS yüzeyiyle başlar (`/api/quick-pick`, `/api/pos/lookup`,
`/api/pos/sale`). Diğer yüzeyler aynı kalıpla (response_model + MoneyOut/QuantityOut +
`COVERED_ENDPOINTS` listesine ekleme) kademeli geçer.

## Katmanlar

### 1. OpenAPI → Frontend tipleri (derleme zamanı)
- `backend/export_openapi.py` şemayı deterministik dışa aktarır (yan etkisiz: geçici SQLite).
- `frontend`: `npm run types:gen` → `src/api/types.gen.ts` (**commit edilir**).
- `Pos.tsx` tipleri üretilen şemadan alır; elle tip yazımı POS yüzeyinde bitti.
- Koruma zinciri: şema ⇔ çalışma anı (`response_model` + katman 3) ve şema ⇔ tipler
  (CI `contract-drift`). Şema gerçeği "sayı" deseydi üretilen tip `number` olur,
  `.replace` çağrısı **tsc'de** patlardı.

### 2. Playwright E2E (`frontend/e2e/`)
- Gerçek yığın: uvicorn + taze SQLite + build edilmiş frontend + Chromium (port 5599;
  5060/5061 Chromium'un güvensiz port listesinde).
- Senaryolar: giriş (+hatalı şifre), POS quick-pick çipi → toplam, barkod → satış,
  şubeler arası transfer, 4 ekran (/, /urunler, /satislar, /raporlar).
- **Konsol-temiz sözleşmesi** (`e2e/helpers.ts`): tek `console.error` veya yakalanmamış
  istisna testi kırmızı yapar. Tek istisna: oturum öncesi `/api/auth/*` 401/403 ağ satırları.
- Tohumlama API üzerinden `e2e/global-setup.ts`'te (bootstrap admin zorunlu şifre
  değişimi dahil).

### 3. API kontrat testleri (pytest + jsonschema)
- `backend/test_api_contract.py` (SQLite) + `test_api_contract_postgresql.py` (PG 16 ikizi).
- Üç garanti: kapsanan uç **şema bildirmek zorunda**; canlı yanıt şemaya uymak zorunda;
  para/miktar alanları kanonik string kalıbına uymak zorunda.
- İki motor aynı JSON şeklini üretmek zorunda — bug'ın sızdığı SQLite/PG ayrışması kapandı.

### 4. Gerçek-fixture vitest
- `backend/tools/capture_frontend_fixtures.py` gerçek yanıt gövdelerini
  `frontend/src/test/fixtures/pos-api.json`'a yakalar (**commit edilir**, elle düzenlenmez).
- `Pos.fixtures.test.tsx` bu gerçek şekillerle POS akışlarını koşar + kanonik kalıp
  bekçisi içerir.

### CI (`.github/workflows/ci.yml` — bu hattın tek sahibi)
- `contract-drift`: üretilenleri yeniden üret + `git diff --exit-code`.
- `e2e`: build + Playwright (artefakt yüklemesi failure'da).
- Kontrat pytest'leri mevcut `backend-quality` ve `backend-postgresql` lane'lerinde
  otomatik koşar (`CONTRACT_TEST_DATABASE_URL` eklendi; PG dosya alt sınırı 16 geçerli).

## Kabul Kanıtı (mutasyon kontrolü)

Hotfix-öncesi durum (backend ham dict + hotfix-öncesi `Pos.tsx`, `84aa544` içerikleri)
geçici uygulanarak her katmanın KIRMIZI, geri alınınca YEŞİL olduğu koşuldu — sonuçlar
PR açıklamasında komut çıktılarıyla birlikte.

## Yan bulgular (bu PR'da düzeltildi)
- `index.html`'deki inline preload script'i backend CSP'si (`script-src 'self'`)
  tarafından prod'da sessizce engelleniyordu → `main.tsx`'e taşındı (artık gerçekten
  çalışıyor ve konsol temiz).

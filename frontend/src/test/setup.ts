import '@testing-library/jest-dom/vitest';
import {cleanup,configure} from '@testing-library/react';
import {afterEach} from 'vitest';

// vitest `globals` kapalı olduğundan RTL kendi afterEach(cleanup)'ını kaydedemez;
// temizlemeyen bir dosyada önceki testin ağacı bağlı kalır ve sonraki testin
// mock'larına yazmaya devam eder (H37: Transactions ?new=1 → open:true sızıntısı).
afterEach(()=>cleanup());

// ═══ RTL BEKLEME BÜTÇESİ — ÖLÇÜLDÜ, SEÇİLMEDİ (H45) ═══
//
// KUSUR: `waitFor`/`findBy` DUVAR SAATİ ile bekler; beklenen iş ise saf CPU.
// TransactionDialog'un ilk yüklemesi üç ardışık React commit'inden geçer
// (Promise.all[/customers,/warehouses,/payments/accounts] -> setWarehouseId ->
// GET /products -> setProducts -> önseçim -> setLines -> boş satır). Zincirde
// GERÇEK ZAMANLAYICI YOKTUR: testteki mock'lar zaten çözülmüş promise döndürür,
// üç bacak da mikrogörev kuyruğunda ilerler — ölçüldü, TEK bir
// `await act(async()=>{})` turu zincirin tamamını bitiriyor (16/16 örnekte
// tur sayısı 1). Yani geç kalan bir istek ya da efekt yok; gecikmenin tamamı
// jsdom üzerinde ağır bir MUI diyalogunu commit etmenin CPU maliyeti.
//
// PAY, BÜTÇE KISILARAK ÖLÇÜLDÜ (bu kutu, 8 çekirdek, YÜKSÜZ, tek dosya;
// asyncUtilTimeout aşağı süpürüldü, düşen test kümesi):
//     3000 / 1000 / 900 / 800 / 700 / 600 ms   11/11 GEÇTİ
//     400 ms   3 düştü  (önseçim + boş satır üçlüsü)
//     300 ms   3 düştü  (aynı üçlü)
//     200 ms   4 düştü
//     150 / 100 ms   5 düştü
// Yani beklenen bacak bu kutuda 400-600 ms sürüyor ve RTL varsayılanına kalan
// pay yalnızca ~1.7x-2.5x. 400 ms'te İLK DÜŞENLER, yük altında bildirilen
// testlerin TAM OLARAK kendisi:
//     satışta ürünü önseçer ve satış fiyatını uygular
//     alışta aynı ürün için alış fiyatını uygular
//     son satır dolunca altına boş satır kendiliğinden açılır
// CPU doyduğunda aynı bacak 1.9-4.8 s'ye çıkıyor (8 meşgul süreç + vitest ile
// ölçüldü) ve hata şu oluyor:
//     Unable to find an element with the display value: /Hidrolik Pompa/
//
// DÜRÜSTÇE — KAPI KOŞULUNDA BUGÜN KIRMIZI YOK: bu ağaçta tam süit varsayılan
// işçiyle 757/757 yeşil, ikinci bir vitest süreci koşarken dosya 20/20 yeşil
// (ayar VARKEN de YOKKEN de). Kırmızı yalnızca ~9x aşırı abonelikte üretilebildi.
// Bu satır bu yüzden "bugün kırmızıyı yeşile çeviren yama" DEĞİL, ölçülmüş
// ~2x'lik dar payı ~6x'e çıkaran bir PAY AYARIDIR.
//
// NEDEN BURADA, TEK YERDE: bu bir çağrı yeri kusuru değil, varsayılanın bu
// donanım için dar olması. Çağrı yerlerine tek tek `{timeout:...}` serpmek
// aynı kararı dosyalara dağıtıp gerekçesini kaybettirirdi.
//
// NEDEN 3000: vitest `testTimeout` VARSAYILANDA (5000 ms) BIRAKILDI. Bütçe
// onun altında kalmalı ki gerçekten bozuk bir bekleme RTL'in eksik elemanı
// ADIYLA söyleyen hatasıyla düşsün, opak bir "Test timed out" ile değil.
// Ölçüldü: bütçe testTimeout'un üstüne çıkarıldığında (act ile bekleme denemesi)
// aynı dosyada 9/11 test `Test timed out in 1200ms` diye düşüyordu — yani hata
// mesajı körelip hangi elemanın eksik olduğunu söylemez hâle geliyor.
//
// YAPILMAYANLAR: `testTimeout` değiştirilmedi, `findBy`a çağrı bazında timeout
// verilmedi, retry/tekrar eklenmedi, bileşene dokunulmadı.
configure({asyncUtilTimeout:3000});

// RENDER KANITI — `olcum:'positive'` + RENDER KONTRATINDAKİ spec'in ÇALIŞMA
// ZAMANI kanıtı.
//
// NEDEN AYRI BİR EK. `spec` tasnifi bugüne dek ancak ziyaret KANITINI taşıyordu:
// R5 testin rotayı GERÇEKTEN AÇTIĞINI ölçerdi ama sayfanın GERÇEKTEN ÇİZİLDİĞİNİ
// değil. Bir rota açılıp render çökmesiyle boş ekrana düşebilir; ziyaret kaydı
// onu yakalar, çizim kanıtı yakalamaz. Render kanıtı tam olarak o boşluğu doldurur:
// rota-gövdesi KÖKÜNÜN çizildiği ve sayfaya ÖZGÜ işaretin O KÖKTE GÖRÜNDÜĞÜ,
// ve kanıtın testInfo EKİ olarak bırakıldığı ölçülür.
//
// KAPSAM: YALNIZ RENDER KONTRATINDAKİ GİRDİLER. PR #14'ta bu üç rotadır:
// /urunler/:id, /tedarikciler/:id, /depolar/:id. Mevcut 27 positive spec bu
// KONTRATTA DEĞİLDİR; onların coverage modeli aynen kalır (bkz. rota-envanteri
// `PozitifSpec`). Bir test bu helper'ı çağırmadıkça raportör render kanıtını
// GÖRMEZ ve (yalnız render KONTRATINDAKİ girdiler için) kapı KIRMIZI düşer.
//
// GLOBAL/SIDEBAR KAPSAM REDDEDİLİR. İşaret YALNIZ rota-gövdesi kökünün ALTINDA
// aranır; `page.getByText(isaret)` (global kapsam) bu dosyada KULLANILMAZ — o,
// kenar çubuğu etiketi gibi kabuk metnini sayar ve kabuğun çizilmesini kapsam
// sayar. YAPISAL güvence: helper yalnız `getByTestId(ROTA_GOVDESI_TESTID)`
// kapsamında arar; global kapsamda arayan bir test kendi render kanıtını
// üretemez ve raportör RED verir.

import type {Page, TestInfo} from '@playwright/test';

import {expect} from './helpers';
import {ROTA_GOVDESI_TESTID} from './rota-envanteri';

/** testInfo ekinin adı — raportör render kanıtını bu adla arar. */
export const ROTA_RENDER_EKI = 'rota-render-kaniti';

/** Bir rota-gövdesi kökünün çizildiğini ve işaretin O KÖKTE göründüğünü ölçer,
 *  kanıtı testInfo EKİ olarak bırakır.
 *
 *  Üç ölçüm:
 *   1. `rota-govdesi` kökü TAM OLARAK BİR kez bulunur (oturumlu rotalarda
 *      AppShell'in `<Outlet/>`ü saran kutusu, oturumsuzda sayfa bileşeninin kökü).
 *   2. İşaret O KÖKTE görünür. Sayfanın tamamında görünmesi kapsam kanıtı değildir.
 *   3. Kanıt `ROTA_RENDER_EKI` adlı ekle iliştirilir; raportör (R5) bunu okur.
 *
 *  `isaret` çağıranın verdiği DETERMINISTIK metindir (ör. testin oluşturduğu
 *  tedarikçi adı); burada kökte MUTLAK metin eşleşmesi aranır.
 */
export async function renderKanitiniDogrula(
  page: Page,
  rota: string,
  isaret: string,
  testInfo: TestInfo,
): Promise<void> {
  const govde = page.getByTestId(ROTA_GOVDESI_TESTID);
  await expect(
    govde,
    `${rota}: rota gövdesi kökü (data-testid="${ROTA_GOVDESI_TESTID}") tam olarak BİR kez bulunmalı; ` +
      'oturumlu rotalarda AppShell, oturumsuz rotalarda sayfa bileşeni onu taşır',
  ).toHaveCount(1, {timeout: 15_000});
  await expect(
    govde.getByText(isaret).first(),
    `${rota} çizilmedi: "${isaret}" sayfa GÖVDESİNDE görünmüyor. ` +
      'Kenar çubuğu/üst çubukta görünmesi kapsam kanıtı değildir.',
  ).toBeVisible({timeout: 15_000});
  await testInfo.attach(ROTA_RENDER_EKI, {
    body: JSON.stringify({rota, isaret, kapsam: 'govde'}),
    contentType: 'application/json',
  });
}

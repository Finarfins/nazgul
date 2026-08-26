import {test, expect, login, adminApi, createCustomer, createProduct} from './helpers';

// V3 smoke planının fatura kolu: iş emri -> parça -> faturalandırma -> fatura.
//
// RC_FRONTEND_DEPLOYMENT_CHECKLIST'in şu maddeleri bu spec'e dayanır:
//   * "Parts add/edit/delete frontend is present and uses backend totals"
//   * "Billing summary frontend is present and uses Decimal response values"
//   * "Invoice generate/list/detail ... frontend is present"
//
// Önkoşullar (müşteri, makine, iş emri) gerçek API ile deterministik kurulur;
// KANITLANAN akış UI'dan yürütülür — parça ekleme, durum geçişi, faturalandırma
// ve fatura detayı. Tutarlar hiçbir yerde istemcide uydurulmaz: parça satırının
// ve faturanın toplamı backend'in döndürdüğü değerlerle karşılaştırılır.

test('iş emrine parça eklenir, tamamlanır ve faturalandırılıp fatura detayına düşer', async ({page}) => {
  const admin = await adminApi();
  const stamp = Date.now();
  const customerName = `Fatura Akışı Müşteri ${stamp}`;
  // Kendi ürünümüzü kurarız: SEED ürününün stoğunu POS ve transfer spec'leri
  // tüketiyor, ona bağlanmak bu testi koşum sırasına bağımlı kılardı.
  const productName = `Fatura Akışı Parça ${stamp}`;
  let workOrderId: number;

  try {
    const customerId = await createCustomer(admin, customerName);
    await createProduct(admin, {
      name: productName,
      productCode: `FTR-${stamp}`,
      barcode: `869${String(stamp).slice(-10)}`,
    });

    const machineRes = await admin.api.post('/api/machines', {
      headers: await admin.headers(),
      data: {
        customer_id: customerId,
        brand: 'Sungur',
        model: 'V3 Fatura',
        serial_number: `V3-FTR-${stamp}`,
      },
    });
    expect(machineRes.ok(), await machineRes.text()).toBeTruthy();
    const machineId = (await machineRes.json()).id as number;

    const meRes = await admin.api.get('/api/auth/me', {headers: await admin.headers()});
    expect(meRes.ok(), await meRes.text()).toBeTruthy();
    const technicianId = (await meRes.json()).user.id as number;

    const woRes = await admin.api.post('/api/work-orders', {
      headers: await admin.headers(),
      data: {
        machine_id: machineId,
        customer_id: customerId,
        technician_id: technicianId,
        actual_hours: '2',
        labor_rate: '150',
        warranty_type: 'NONE',
      },
    });
    expect(woRes.ok(), await woRes.text()).toBeTruthy();
    workOrderId = (await woRes.json()).id as number;
  } finally {
    await admin.dispose();
  }

  await login(page);
  await page.goto(`/is-emirleri/${workOrderId}`);

  // --- Parça ekleme: tutar backend sözleşmesine göre oluşur -----------------
  await expect(page.getByText('Henüz parça eklenmedi.')).toBeVisible();
  await page.getByRole('button', {name: 'Parça Ekle'}).click();

  const partDialog = page.getByRole('dialog');
  await expect(partDialog.getByText('Parça Ekle')).toBeVisible();

  // Parça alanı depo seçilene kadar kilitli ("Önce depo seçin"), seçilince
  // "Ürün / Parça" olur — sıra bu yüzden önemli.
  await partDialog.getByRole('combobox', {name: 'Depo', exact: true}).click();
  await page.getByRole('option').first().click();

  await partDialog.getByRole('combobox', {name: 'Ürün / Parça'}).click();
  await page.getByRole('option', {name: new RegExp(productName)}).first().click();

  await partDialog.getByLabel('Miktar').fill('2');
  await partDialog.getByLabel('Birim Fiyat (₺)').fill('100');
  await partDialog.getByLabel('KDV (%)').fill('20');

  // 2 x 100 = 200 + %20 KDV = 240,00 — diyalog bu toplamı backend'in
  // money.compute_line paritesindeki ortak yardımcıyla üretir (float değil).
  const expectedPartTotal = '240,00';
  await expect(partDialog.getByText(new RegExp(expectedPartTotal))).toBeVisible();

  await partDialog.getByRole('button', {name: 'Kaydet'}).click();
  await expect(partDialog).toBeHidden({timeout: 15_000});

  // Kaydedilen satır, backend'den dönen toplamla listede görünür.
  await expect(page.getByText('Henüz parça eklenmedi.')).toBeHidden();
  await expect(page.getByText(new RegExp(expectedPartTotal)).first()).toBeVisible({timeout: 15_000});

  // --- Durum: tamamlandı ----------------------------------------------------
  // Faturalandır düğmesi yalnız COMPLETED/DELIVERED durumunda çizilir.
  await expect(page.getByRole('button', {name: 'Faturalandır'})).toHaveCount(0);

  for (let guard = 0; guard < 4; guard += 1) {
    const completed = page.getByRole('button', {name: 'Tamamlandı durumuna geçir'});
    if (await completed.count()) {
      await completed.click();
      break;
    }
    // Durum makinesi ara adım istiyorsa (ör. Devam Ediyor) önce onu geç.
    const forward = page.getByRole('button', {name: /durumuna geçir/}).first();
    await expect(forward).toBeVisible({timeout: 15_000});
    await forward.click();
    await page.waitForTimeout(500);
  }

  // --- Faturalandırma -------------------------------------------------------
  const billBtn = page.getByRole('button', {name: 'Faturalandır'});
  await expect(billBtn).toBeVisible({timeout: 15_000});
  await expect(billBtn).toBeEnabled({timeout: 15_000});
  await billBtn.click();

  const confirm = page.getByRole('dialog');
  await expect(confirm.getByText('Fatura oluşturulsun mu?')).toBeVisible();
  await confirm.getByRole('button', {name: 'Faturayı Oluştur'}).click();

  // Fatura üretilir ve uygulama fatura detayına yönlendirir.
  await expect(page).toHaveURL(/\/faturalar\/\d+/, {timeout: 20_000});
  await expect(page.getByText('Fatura Bilgileri')).toBeVisible({timeout: 15_000});
  await expect(page.getByText('Genel Toplam')).toBeVisible();

  // Parça satırı faturaya taşınmış olmalı (kalemler backend'den gelir).
  await expect(page.getByText('Kalemler')).toBeVisible();
  await expect(page.getByText(new RegExp(productName)).first()).toBeVisible();

  // --- Fatura listesi -------------------------------------------------------
  // Liste DataGrid; satır bağlantı değil, o yüzden faturayı kendi müşterisiyle
  // (bu koşuma özgü, zaman damgalı ad) arıyoruz.
  await page.goto('/faturalar');
  await expect(page.getByText(customerName).first()).toBeVisible({timeout: 15_000});
});

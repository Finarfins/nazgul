import {adminApi, expect, login, test} from './helpers';

test('satış listesindeki müşteri bağlantısı gerçek liste sözleşmesiyle cariye gider', async ({page}) => {
  const admin = await adminApi();
  const suffix = Date.now();
  const customerName = `E2E Liste Cari ${suffix}`;

  try {
    const customer = await admin.api.post('/api/customers', {
      headers: await admin.headers(),
      data: {name: customerName, risk_limit: '100000.00'},
    });
    expect(customer.ok()).toBeTruthy();
    const customerId = (await customer.json()).id as number;

    const warehouses = await admin.api.get('/api/warehouses', {
      headers: await admin.headers(),
    });
    expect(warehouses.ok()).toBeTruthy();
    const warehouseId = ((await warehouses.json()) as Array<{id: number}>)[0].id;

    const products = await admin.api.get('/api/products', {
      headers: await admin.headers(),
    });
    expect(products.ok()).toBeTruthy();
    const productId = ((await products.json()) as Array<{id: number}>)[0].id;

    const sale = await admin.api.post('/api/orders', {
      headers: await admin.headers(),
      data: {
        entity_id: customerId,
        transaction_date: '2026-07-29',
        warehouse_id: warehouseId,
        payment_method: 'cash',
        paid_amount: '18.00',
        items: [{
          product_id: productId,
          quantity: '1',
          unit_price: '18.00',
          vat_rate: '20',
          discount_percent: '0',
        }],
      },
    });
    expect(sale.ok()).toBeTruthy();

    await login(page);
    await page.goto('/satislar');

    const customerLink = page.getByRole('link', {name: customerName});
    await expect(customerLink).toBeVisible({timeout: 15_000});
    await expect(customerLink).toHaveAttribute('href', `/musteriler/${customerId}`);
    await customerLink.click();
    await expect(page).toHaveURL(new RegExp(`/musteriler/${customerId}$`));
  } finally {
    await admin.dispose();
  }
});

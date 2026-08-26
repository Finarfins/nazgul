import {request, type FullConfig, type APIRequestContext} from '@playwright/test';
import {rm} from 'node:fs/promises';

import {E2E_PASSWORD, SEED, TOHUM_DIZINI} from './helpers';

// Taze E2E veritabanını API üzerinden deterministik tohumlar:
// 1. bootstrap admin (admin/admin123) ile giriş + zorunlu şifre değişimi,
// 2. barkodlu ürün (POS satış + transfer senaryolarının hammaddesi),
// 3. bir tamamlanmış POS satışı (quick-pick öğrenme geçmişi — bugünkü prod
//    çökmesini tetikleyen veri YOLU tam olarak budur),
// 4. ikinci depo (şubeler arası transfer senaryosu için hedef).

async function csrfToken(api: APIRequestContext): Promise<string | undefined> {
  const state = await api.storageState();
  return state.cookies.find((cookie) => cookie.name === 'yhp_csrf_token')?.value;
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  // TOHUM BELLEĞİ HER KOŞUDA SIFIRDAN. Bu dizin işçiler ARASINDA paylaşılır
  // (bkz. helpers.tohumHatirla); veritabanı her koşuda TAZE olduğu için önceki
  // koşudan kalan bir tohum artık var olmayan kayıtlara işaret ederdi.
  // globalSetup koşu başına TAM BİR KEZ ve tüm işçilerden ÖNCE koşar, yani
  // temizlik için doğru yer burasıdır.
  await rm(TOHUM_DIZINI, {recursive: true, force: true});

  const baseURL = config.projects[0]?.use?.baseURL ?? 'http://127.0.0.1:5599';
  const api = await request.newContext({baseURL});

  const login = await api.post('/api/auth/login', {
    data: {username: 'admin', password: 'admin123'},
  });
  if (!login.ok()) {
    throw new Error(`E2E setup: bootstrap girişi başarısız (${login.status()}): ${await login.text()}`);
  }
  const loginBody = await login.json();
  const companyId = String(loginBody.companies[0].id);
  const headers = async (token: string) => ({
    Authorization: `Bearer ${token}`,
    'X-Company-ID': companyId,
    ...((await csrfToken(api)) ? {'X-CSRF-Token': (await csrfToken(api))!} : {}),
  });

  const changed = await api.post('/api/auth/change-password', {
    headers: await headers(loginBody.access_token),
    data: {current_password: 'admin123', new_password: E2E_PASSWORD},
  });
  if (!changed.ok()) {
    throw new Error(`E2E setup: şifre değişimi başarısız (${changed.status()}): ${await changed.text()}`);
  }
  const token = (await changed.json()).access_token as string;

  const product = await api.post('/api/products', {
    headers: await headers(token),
    data: {
      name: SEED.productName,
      product_code: SEED.productCode,
      barcode: SEED.barcode,
      purchase_price: '9.00',
      sale_price: SEED.unitPrice,
      vat_rate: 20,
      stock: '8',
      unit: 'Adet',
    },
  });
  if (!product.ok()) {
    throw new Error(`E2E setup: ürün oluşturulamadı (${product.status()}): ${await product.text()}`);
  }
  const productId = (await product.json()).id as number;

  const sale = await api.post('/api/pos/sale', {
    headers: {...await headers(token), 'Idempotency-Key': 'e2e-seed-pos-sale'},
    data: {
      items: [{product_id: productId, quantity: '2', unit_price: SEED.unitPrice}],
      payment_type: 'cash',
    },
  });
  if (!sale.ok()) {
    throw new Error(`E2E setup: tohum satışı başarısız (${sale.status()}): ${await sale.text()}`);
  }

  const warehouse = await api.post('/api/warehouses', {
    headers: await headers(token),
    data: {name: SEED.secondWarehouse, code: 'SB2'},
  });
  if (!warehouse.ok()) {
    throw new Error(`E2E setup: ikinci depo oluşturulamadı (${warehouse.status()}): ${await warehouse.text()}`);
  }

  await api.dispose();
}

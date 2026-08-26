import {describe, expect, it} from 'vitest';

import {validateSnapshot} from './store';

/**
 * Snapshot'taki parça listesinin allow-list kapısı.
 *
 * Bu kapının varlık sebebi şu: sunucu tarafında `FieldPart`'a fiyat eklemek
 * TEK satırlık bir değişiklik. O satır atıldığı gün satır tutarı teknisyenin
 * cihazında şifresiz, saatlerce durmaya başlar ve hiçbir test kırılmazdı.
 * Allow-list istemcide olduğu için kırılıyor.
 */

const part = (over: Record<string, unknown> = {}) => ({
  id: 1, product_name: 'FİLTRE', quantity: '2.000', unit: 'ADET', line_status: 'ISSUED', ...over,
});

const snapshot = (parts: unknown) => ({
  snapshot_version: 5, generated_at: new Date().toISOString(), user_id: 3, company_id: 2,
  work_orders: [{
    id: 9, work_order_no: 'WO-9', status: 'OPEN', version: 'v1',
    updated_at: new Date().toISOString(), priority: 'NORMAL', scheduled_date: null,
    complaint: null, diagnosis: null, repair_summary: null, technician_notes: null,
    machine: {id: 1, brand: null, model: null, serial_number: null, chassis_number: null, working_hours: null},
    customer: {display_name: 'Müşteri', service_address: null},
    parts,
  }],
});

describe('saha snapshot parça kapısı', () => {
  it('geçerli parça listesini kabul eder', () => {
    const ok = validateSnapshot(snapshot([part()]), 2, 3);
    expect(ok.work_orders[0].parts).toHaveLength(1);
  });

  it('allow-list DIŞI bir alan geldiğinde snapshot REDDEDİLİR', () => {
    // Asıl korunan durum: sunucu bir gün fiyatı da göndermeye başlarsa.
    expect(() => validateSnapshot(snapshot([part({unit_price: '250.00'})]), 2, 3))
      .toThrow('allow-list dışında');
  });

  it('miktarı sayı olarak gönderen sunucuyu reddeder', () => {
    // Miktar metin taşınıyor; sayıya düşerse 0.1+0.2 yuvarlaması faturaya
    // giden bir değeri bozabilir.
    expect(() => validateSnapshot(snapshot([part({quantity: 2})]), 2, 3))
      .toThrow('parça bütünlüğü geçersiz');
  });

  it('parça listesi hiç gelmezse boş kabul eder (eski sunucuyla uyum)', () => {
    // Zorunlu tutsaydık, henüz güncellenmemiş bir sunucuyla konuşan yeni
    // istemcide snapshot komple reddedilir ve teknisyen bayat veriyle kalırdı.
    const wo = snapshot(undefined);
    delete (wo.work_orders[0] as Record<string, unknown>).parts;
    expect(validateSnapshot(wo, 2, 3).work_orders[0].parts).toEqual([]);
  });

  it('parça listesi dizi değilse reddeder', () => {
    expect(() => validateSnapshot(snapshot({}), 2, 3)).toThrow('parça listesi geçersiz');
  });
});

// Shared stock helpers used by ProductDetail, WarehouseDetail and StockMovements.

// Movement types the system actually writes (transactions / workflow / warehouses /
// products / imports). Nothing here is invented; unknown types fall back to the raw value.
export const movementLabels: Record<string, string> = {
  sale: 'Satış',
  purchase: 'Alış',
  opening: 'Açılış Stoku',
  manual: 'Manuel Düzeltme',
  set: 'Sayım Düzeltmesi',
  add: 'Stok Girişi',
  remove: 'Stok Çıkışı',
  bulk: 'Toplu Güncelleme',
  excel: 'Excel Aktarımı',
  transfer_in: 'Transfer Girişi',
  transfer_out: 'Transfer Çıkışı',
  quote: 'Teklif',
  sales_order: 'Sipariş',
  delivery: 'İrsaliye',
  sale_return: 'Satış İadesi',
  purchase_return: 'Alış İadesi',
};

export const movementLabel = (type: unknown): string =>
  movementLabels[String(type || '').toLowerCase()] || String(type || 'Hareket');

// Only reference types with a real frontend route resolve to a document.
// Demo data writes singular (order/purchase); app code writes plural (orders/purchases).
export function movementTarget(
  referenceType: unknown,
  referenceId: unknown,
): { path: string; openId: number } | null {
  const id = Number(referenceId);
  if (!referenceId || !Number.isFinite(id) || id <= 0) return null;
  const type = String(referenceType || '').toLowerCase();
  if (type === 'order' || type === 'orders') return { path: '/satislar', openId: id };
  if (type === 'purchase' || type === 'purchases') return { path: '/alislar', openId: id };
  if (type === 'transfer' || type === 'stock_transfer' || type === 'stock_transfers') {
    return { path: `/depo-transferleri/${id}`, openId: id };
  }
  if (type === 'inventory_count' || type === 'stock_count') {
    return { path: `/stok-sayimlari/${id}`, openId: id };
  }
  return null;
}

// Classify a stock line from quantity + critical threshold. A zero threshold is never
// treated as critical (matches the dashboard/product-detail rule); negative stock is
// surfaced as an exceptional condition, never clamped.
export type StockCondition = {
  key: 'negative' | 'out' | 'critical' | 'normal';
  label: string;
  color: 'error' | 'warning' | 'success';
};

export function stockCondition(quantity: unknown, threshold: unknown): StockCondition {
  const qty = Number(quantity || 0);
  const thr = Number(threshold || 0);
  if (qty < 0) return { key: 'negative', label: 'Negatif Stok', color: 'error' };
  if (qty === 0) return { key: 'out', label: 'Stok Yok', color: 'error' };
  if (thr > 0 && qty <= thr) return { key: 'critical', label: 'Kritik', color: 'warning' };
  return { key: 'normal', label: 'Normal', color: 'success' };
}

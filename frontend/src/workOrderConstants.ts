// İş emri (work order) ortak sabitleri — liste, detay, dialog ve makine detay
// sayfası aynı kaynaktan beslenir. Durumlar, öncelikler, garanti türleri ve
// izinli durum geçişleri backend'deki work_orders.py / work_order_schemas.py ile
// birebir aynı tutulmalıdır (geçiş kuralları backend'de de zorlanır).

export const WO_STATUS_LABELS: Record<string, string> = {
  SCHEDULED: 'Planlandı',
  OPEN: 'Açık',
  IN_PROGRESS: 'Devam Ediyor',
  WAITING_PARTS: 'Parça Bekliyor',
  WAITING_CUSTOMER: 'Müşteri Bekliyor',
  COMPLETED: 'Tamamlandı',
  DELIVERED: 'Teslim Edildi',
  CANCELLED: 'İptal',
};

export const WO_STATUS_COLORS: Record<string, 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  SCHEDULED: 'default',
  OPEN: 'info',
  IN_PROGRESS: 'warning',
  WAITING_PARTS: 'warning',
  WAITING_CUSTOMER: 'warning',
  COMPLETED: 'success',
  DELIVERED: 'success',
  CANCELLED: 'error',
};

export const WO_PRIORITY_LABELS: Record<string, string> = {
  LOW: 'Düşük',
  NORMAL: 'Normal',
  HIGH: 'Yüksek',
  URGENT: 'Acil',
};

export const WO_PRIORITY_COLORS: Record<string, 'default' | 'info' | 'warning' | 'error'> = {
  LOW: 'default',
  NORMAL: 'info',
  HIGH: 'warning',
  URGENT: 'error',
};

export const WARRANTY_TYPE_LABELS: Record<string, string> = {
  FULL: 'Tam Garanti',
  PARTIAL: 'Kısmi Garanti',
  NONE: 'Garanti Yok',
};

// Backend ALLOWED_TRANSITIONS ile aynı: bir durumdan yalnızca listelenen
// durumlara geçilebilir; DELIVERED ve CANCELLED terminaldir.
export const WO_ALLOWED_TRANSITIONS: Record<string, string[]> = {
  // SCHEDULED yalnız oluşturma anında seçilebilir; hiçbir durumdan SCHEDULED'a
  // geçilemez (backend de reddeder).
  SCHEDULED: ['OPEN', 'CANCELLED'],
  OPEN: ['IN_PROGRESS', 'CANCELLED'],
  IN_PROGRESS: ['WAITING_PARTS', 'WAITING_CUSTOMER', 'COMPLETED', 'CANCELLED'],
  WAITING_PARTS: ['IN_PROGRESS', 'WAITING_CUSTOMER', 'CANCELLED'],
  WAITING_CUSTOMER: ['IN_PROGRESS', 'WAITING_PARTS', 'CANCELLED'],
  COMPLETED: ['DELIVERED', 'IN_PROGRESS'],
  DELIVERED: [],
  CANCELLED: [],
};

export const WO_TERMINAL_STATES = new Set(['DELIVERED', 'CANCELLED']);

// Ana ilerleme adımları (stepper): mutlu yol. Bekleme durumları ve iptal bu
// çizginin dışında rozet olarak gösterilir.
export const WO_MAIN_FLOW = ['OPEN', 'IN_PROGRESS', 'COMPLETED', 'DELIVERED'] as const;

import {describe, expect, it} from 'vitest';

import {ANONYMOUS_PATHS, PUBLIC_PATHS, isAnonymousPath} from './publicPaths';

/**
 * İki listenin anlamı ayrıdır ve karışırsa sessiz hatalar doğar:
 *   PUBLIC_PATHS    -> izin haritası (ROUTE_PERMISSIONS) gerektirmeyen rotalar
 *   ANONYMOUS_PATHS -> OTURUM gerektirmeyen rotalar
 *
 * `/sifre-degistir` yalnız birincisine girer. E2E tarafında bu ayrımı ayırt
 * edici biçimde ölçmek mümkün değil: `expireBrowserSession` önce
 * `yhp:auth-expired` olayını yayıyor, `AuthContext` bunu dinleyip `user`'ı
 * düşürüyor ve `ChangePassword` `<Navigate to="/giris">` ile zaten çıkıyor —
 * yani davranış İKİ bağımsız mekanizmayla korunuyor ve listeyi bozmak e2e'yi
 * kırmıyor (mutasyonla doğrulandı). Listenin kendisi bu yüzden burada,
 * birim seviyesinde kilitlenir.
 */
describe('publicPaths', () => {
  it('/sifre-degistir oturum gerektirir: ANONYMOUS_PATHS içinde OLMAMALI', () => {
    expect(isAnonymousPath('/sifre-degistir')).toBe(false);
    expect(ANONYMOUS_PATHS as readonly string[]).not.toContain('/sifre-degistir');
  });

  it('/sifre-degistir kabuk dışıdır: PUBLIC_PATHS içinde OLMALI', () => {
    // İzin haritası tutarlılık testi (navigation.consistency.test.ts) bu listeyi
    // "ROUTE_PERMISSIONS gerektirmeyenler" olarak okur; çıkarılırsa orası kırılır.
    expect(PUBLIC_PATHS as readonly string[]).toContain('/sifre-degistir');
  });

  it('oturumsuz sayfalar anonim erişime açıktır', () => {
    for (const path of ['/tanitim', '/giris', '/kayit', '/eposta-dogrula']) {
      expect(isAnonymousPath(path), `${path} anonim olmalı`).toBe(true);
    }
  });

  it('korumalı bir rota anonim sayılmaz', () => {
    for (const path of ['/urunler', '/musteriler', '/', '/faturalar']) {
      expect(isAnonymousPath(path), `${path} anonim sayılmamalı`).toBe(false);
    }
  });

  it('PUBLIC_PATHS, ANONYMOUS_PATHS’in üst kümesidir (ayrışamazlar)', () => {
    for (const path of ANONYMOUS_PATHS) {
      expect(PUBLIC_PATHS as readonly string[]).toContain(path);
    }
    // Tek fark /sifre-degistir olmalı; yeni bir sapma eklenirse burada görülür.
    const extra = (PUBLIC_PATHS as readonly string[]).filter(
      (path) => !(ANONYMOUS_PATHS as readonly string[]).includes(path),
    );
    expect(extra).toEqual(['/sifre-degistir']);
  });
});

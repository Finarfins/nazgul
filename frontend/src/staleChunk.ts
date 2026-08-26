// Yeni bir imaj dağıtıldığında eski hash'li chunk dosyaları kaybolur; açık bir
// SPA sekmesi eski chunk URL'lerini belleğinde taşıdığı için ilk sayfa
// geçişinde dynamic import "Failed to fetch dynamically imported module" ile
// patlar. index.html zaten no-cache sunulduğundan tek gereken şey bir kere
// otomatik yenilemektir; kısa aralıklı sonsuz reload döngüsüne girmemek için
// sessionStorage'da zaman damgalı bir koruma tutulur.
const GUARD_KEY = 'yhp_stale_chunk_reload_at';
const GUARD_WINDOW_MS = 30_000;

export const isStaleChunkError = (error: unknown): boolean =>
  /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i
    .test(String((error as {message?: unknown})?.message ?? error ?? ''));

// Yenileme hakkı varsa reload'u tetikler ve true döner; kısa süre önce zaten
// denendiyse false döner (çağıran hata ekranını göstermelidir).
export const reloadOnceForStaleChunk = (): boolean => {
  let last = 0;
  try { last = Number(sessionStorage.getItem(GUARD_KEY) || 0); } catch { /* storage kapalı */ }
  const now = Date.now();
  if (now - last < GUARD_WINDOW_MS) return false;
  try { sessionStorage.setItem(GUARD_KEY, String(now)); } catch { /* storage kapalı */ }
  location.reload();
  return true;
};

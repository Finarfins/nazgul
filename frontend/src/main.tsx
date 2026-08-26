import React from 'react';
import ReactDOM from 'react-dom/client';
// Inter değişken yazı tipi, kendi paketimizden servis edilir — CSP
// (style-src/font-src 'self') dış kaynağa izin vermiyor. Dosyalar
// unicode-range taşır: tarayıcı Türkçe için yalnız latin + latin-ext
// alt kümesini indirir, kiril/yunan/vietnam boşuna inmez.
import '@fontsource-variable/inter';
import App from './App';
import { AuthProvider } from './AuthContext';
import { AppThemeProvider } from './ThemeContext';
import { reloadOnceForStaleChunk } from './staleChunk';
import {startFieldPwaBridge} from './field/pwa';
import './field/coordinator';

// Tanıtım sayfasının kahraman görselini erken preload et. Eskiden index.html
// içinde inline <script> idi; backend'in CSP'si (script-src 'self') inline
// script'i engellediği için prod'da hiç çalışmıyor ve her sayfa açılışında
// konsola CSP ihlali düşürüyordu. Modül içine taşımak hem CSP uyumlu hem de
// E2E konsol-temiz sözleşmesinin gereği.
if (location.pathname === '/tanitim') {
  const link = document.createElement('link');
  link.rel = 'preload';
  link.as = 'image';
  link.href = '/media/sungur-hero.webp';
  link.fetchPriority = 'high';
  document.head.append(link);
}

// Vite'ın kendi modül ön-yüklemesi bayat bir chunk'a çarptığında (yeni imaj
// dağıtımı sonrası) React hata sınırına düşmeden yakala ve bir kez yenile.
window.addEventListener('vite:preloadError', (event) => {
  if (reloadOnceForStaleChunk()) event.preventDefault();
});

// Eski sürümlerdeki hatalı PWA önbelleğini temizle.
void startFieldPwaBridge(import.meta.env.MODE === 'field-pwa').catch(()=>undefined);

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('React root elementi bulunamadı.');
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <AppThemeProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </AppThemeProvider>
  </React.StrictMode>,
);

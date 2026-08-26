import {Camera, CameraResultType, CameraSource} from '@capacitor/camera';
import {Capacitor} from '@capacitor/core';

/**
 * Saha fotoğrafı çekme.
 *
 * İki ortamda da çalışması gerekiyor: native kabuk (Capacitor Camera) ve
 * tarayıcı (dosya seçici). Tarayıcı yolu şart, çünkü geliştirme ve masaüstü
 * kullanımı orada; native yolu olmadan ise gerçek kullanım senaryosu yok.
 *
 * Sonuç her iki yolda da Blob. Kuyruk, base64 değil Blob saklıyor: base64
 * aynı veriyi ~%33 şişirir ve fotoğraflar zaten cihaz depolamasının en büyük
 * kalemi olacak.
 */

export type CekilenFotograf = {blob: Blob; fileName: string};

const uzanti = (mime: string) => (mime.includes('png') ? 'png' : 'jpg');

const dosyaAdi = (mime: string) => {
  // Zaman damgası dosya adında: sunucu adı benzersizleştiriyor ama teknisyen
  // ve ofis listede hangi fotoğrafın ne zaman çekildiğini görebilmeli.
  const damga = new Date().toISOString().replaceAll(/[-:T]/g, '').slice(0, 14);
  return `saha-${damga}.${uzanti(mime)}`;
};

export const nativeKamera = () => Capacitor.isNativePlatform();

/** Native kabukta kamerayı açar. */
export async function cekFotograf(): Promise<CekilenFotograf | null> {
  const foto = await Camera.getPhoto({
    // `Uri` seçildi, `Base64` DEĞİL: base64'ü belleğe alıp sonra Blob'a
    // çevirmek büyük fotoğraflarda gereksiz iki kopya demek.
    resultType: CameraResultType.Uri,
    source: CameraSource.Camera,
    // Sıkıştırma bilinçli: saha fotoğrafı belge, sanat eseri değil. 12 MP ham
    // bir kare zayıf şebekede dakikalarca yüklenir ve kuyrukta bekler.
    quality: 70,
    width: 1600,
    correctOrientation: true,
    saveToGallery: false,
  });
  if (!foto.webPath) return null;
  const yanit = await fetch(foto.webPath);
  const blob = await yanit.blob();
  return {blob, fileName: dosyaAdi(blob.type || 'image/jpeg')};
}

/** Tarayıcıda dosya seçiciyi açar (geliştirme ve masaüstü kullanımı). */
export function seçDosya(): Promise<CekilenFotograf | null> {
  return new Promise(resolve => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    // Telefon tarayıcısında doğrudan kamerayı açar.
    input.capture = 'environment';
    input.addEventListener('change', () => {
      const file = input.files?.[0];
      resolve(file ? {blob: file, fileName: file.name || dosyaAdi(file.type)} : null);
    });
    // İptal edildiğinde `change` hiç tetiklenmez; bu dinleyici sözü
    // sonsuza kadar bekletmemek için.
    input.addEventListener('cancel', () => resolve(null));
    input.click();
  });
}

export const fotografAl = () => (nativeKamera() ? cekFotograf() : seçDosya());

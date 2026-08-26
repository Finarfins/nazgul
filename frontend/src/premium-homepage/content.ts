export const homepageContent = {
  hero: {
    eyebrow: 'SUNGUR TARIM TEKNOLOJİLERİ',
    title: 'Toprağın gücünü geleceğin zekâsıyla buluşturuyoruz.',
    description: 'Makine, servis ve operasyonlarınızı tek bir güvenilir platformda yönetin.',
    poster: '/media/sungur-hero.webp',
    video: '',
  },
  services: [
    ['01', 'Akıllı Servis', 'Makine geçmişinden iş emrine kadar kusursuz operasyon görünürlüğü.'],
    ['02', 'Parça Yönetimi', 'Doğru parçayı, doğru depodan, ihtiyaç duyulduğu anda yönetin.'],
    ['03', 'Filo Zekâsı', 'Çalışma saati, bakım ve maliyet verilerini tek merkezde birleştirin.'],
  ],
  stats: [['25+', 'Yıllık saha deneyimi'], ['4.800+', 'Yönetilen makine'], ['98%', 'Servis memnuniyeti'], ['7/24', 'Operasyon görünürlüğü']],
  machines: [
    ['Hassas Tarım', 'Her hektarda daha akıllı kararlar.', '01'],
    ['Servis Gücü', 'Duruş süresini azaltan planlama.', '02'],
    ['Filo Kontrolü', 'Makinenin tüm yaşam döngüsü.', '03'],
  ],
  testimonials: [
    ['Sungur Platform, servis ekibimizin sahadaki karar hızını tamamen değiştirdi.', 'Murat Kaya', 'Operasyon Direktörü'],
    ['Makine ve parça geçmişini tek ekranda görmek bize gerçek kontrol sağlıyor.', 'Selin Aras', 'Satış Sonrası Müdürü'],
  ],
};

export type HomepageContent = typeof homepageContent;

# Harman Zamanı — eksik özellik raporu (şef, 9 Eylül 2026)

Kapsam: bugün olan ve Faz 9/10'da planlı olanların DIŞINDA kalanlar. Kaynak gösterebildiklerim işaretli; gerisi sektör bilgisi, Gemini raporuyla çapraz kontrol edilmeli. Aynı soru Gemini'ye `docs/prompts/ARASTIRMA-eksik-ozellikler-gemini.txt` ile soruldu; iki rapor birleştirilip Faz 11 listesi çıkarılacak.

## Öncelik tablosu

| # | Özellik | Segment | Zorunlu mu | Boyut | Öncelik | Not |
|---|---|---|---|---|---|---|
| 1 | **BKÜ e-reçete (B-Reçete) ile satış**: reçete no doğrulama, reçetesiz satış engeli, bakanlık sistemine satış bildirimi, ruhsat/etken madde kataloğu | bayi | Yasal — pilot dışı illerde 1 Temmuz 2026'dan itibaren zorunlu; reçetesiz satışa idari ceza | L | **P1** | Zirai ilaç bayisi için satışı engeller. Kaynak: AA, TİM haberleri (aşağıda). |
| 2 | **e-İrsaliye** (E4) — planlıydı ama tarih baskısı: 2025 cirosu ≥10 M TL e-Fatura mükellefleri 1 Temmuz 2026'dan itibaren zorunlu; gübre üretimi ciro şartsız | tüccar/bayi | Yasal | M | **P1** | İzibiz EIrsaliyeWS var; E2c sonrası hemen. |
| 3 | **e-Müstahsil** (E3) — fındık/pamuk/tütün alımında ciro sınırı üstü zorunlu | tüccar | Yasal | M | **P1** | Müstahsil makbuzu D1'de var, e-belge eşleniği yok. |
| 4 | **Hububat alım kalite kesintisi**: rutubet, hektolitre, yabancı madde, kırık tane → analiz bazlı fiyat/net ağırlık formülü; alım fişine analiz satırı | tüccar | Sektör beklentisi | M | **P1** | Kantar var, kalite yok. Tüccara "senin programın kesinti hesaplamıyor" dedirtir. |
| 5 | **Hal Kayıt Sistemi (HKS) bildirimi**: yaş meyve-sebze alış/satış/sevk bildirimi, künye | hal/komisyoncu | Yasal (tüm ticari aktörler kayıt zorunlu) | L | P2 | Hal segmentine girmeyeceksek kapsam dışı; girilecekse P1. |
| 6 | **Tohum/gübre bayi bildirimleri**: tohum sertifika-lot izleme, gübre satış bildirimi (bakanlık gübre takip) | bayi | Yasal/sektörel | M | P2 | Lot altyapısı (1B) hazır; sadece bildirim ve sertifika alanı. |
| 7 | **Banka entegrasyonu**: hesap hareketi çekme (açık bankacılık/CSV), otomatik tahsilat eşleştirme; DBS | tüccar/bayi | Beklenti | M | P2 | Tahsilat tahsisi var, kaynağı elle. |
| 8 | **Tarım kartı taksit** (Ziraat Bereket, Tarım Kredi Başak, banka POS tarım kampanyaları): hasat vadeli taksit planı, banka komisyon/vade farkı | bayi | Beklenti | S | P2 | POS var, tarım-kart vade mantığı yok. |
| 9 | **Yazarkasa / ÖKC-POS entegrasyonu** ve banka POS gün sonu mutabakatı | bayi | Yasal (perakende fiş) | M | P2 | e-Arşiv perakende yerine geçebilir; ölçülmeli. |
| 10 | **Çoklu şube / çoklu kasa** ve şubeler arası transfer, şube bazlı rapor | bayi | Beklenti | M | P2 | Çoklu depo var; kasa/şube boyutu yok. |
| 11 | **Sözleşmeli üretim**: çiftçiyle sezon sözleşmesi, girdi avansı (ilaç/gübre/tohum) → hasatta mahsup, sözleşme kotası | tüccar/bayi | Sektör | M | P2 | Avans/mahsup D2'de var; sözleşme nesnesi yok. |
| 12 | **Pamuk/yağlı tohum randıman**: çırçır randımanı, kütlü→lif/çiğit dönüşümü | tüccar (pamuk) | Sektör | S | P3 | Bölgesel. |
| 13 | **Yaş meyve-sebze kasa/ambalaj depozito** takibi | hal/tüccar | Sektör | S | P3 | |
| 14 | **Süt toplama**: yağ/protein/somatik primli fiyat, günlük litre, 15 günlük hakediş | hayvancılık | Sektör | M | P3 | Süt kilidi var, ödeme modeli yok. |
| 15 | **Küpe/HAYBİS/TÜRKVET** dışa aktarım ve küpe doğrulama | hayvancılık | Yasal (bildirim) | S | P3 | Karantina/tedavi var. |
| 16 | **Yem rasyonu / yem tüketim maliyeti** | hayvancılık | Beklenti | M | P3 | |
| 17 | **TARSİM / destek başvurusu belgeleri** (mazot-gübre desteği için satış belgesi çıktısı, ÇKS uyumlu) | bayi/çiftlik | Beklenti | S | P3 | Belge şablonu Faz 10-10 ile. |
| 18 | **Personel prim/komisyon**, saha tahsilatçı, araç/rota/teslimat | bayi/tüccar | Beklenti | M | P3 | |
| 19 | **Çevrimdışı satış** (köy bayisinde internet kopar) | bayi | Beklenti | L | P3 | Mobil aşamasında karar. |
| 20 | **Muhasebe dışa aktarımı** Faz 9-5'te var; eksik alt madde: **Ba-Bs ve KDV beyan destek raporu**, e-Defter'e giden fiş standardı | tüm | Yasal (mali müşavir) | S | P2 | Faz 9-5'e ekle. |
| 21 | **Kullanıcı davet** Faz 9-2'de var; eksik alt madde: **2FA/TOTP**, oturum cihaz listesi | tüm | Beklenti (SaaS) | S | P2 | Güvenlik incelemesi sonrası. |
| 22 | **KVKK**: açık rıza kaydı, veri silme/anonimleştirme talebi, aydınlatma metni şablonu | tüm | Yasal | S | P2 | TCKN tutuyoruz. |

## En kritik 10 (gerekçe)

Zirai ilaç bayisi hedef segmentin en büyüğü ve 1 Temmuz 2026 itibarıyla reçetesiz satış yapamıyor; B-Reçete doğrulama ve satış bildirimi olmayan bir program o bayiye satılamaz (1). Aynı tarih e-İrsaliye'yi 10 M TL üstü tüccara zorunlu kıldı (2); e-Müstahsil de fındık/pamuk/tütün alan tüccar için aynı sınıfta (3) — ikisi de planlıydı ama "sonra" değil "hemen" sınıfına taşınmalı. Hububat tüccarı kantarı olan ama rutubet/hektolitre kesintisi hesaplamayan programı ciddiye almaz (4); bu iş küçük ve mevcut kantar fişine bir analiz satırı eklemekle çözülür. HKS (5) ancak hal segmentine girersek anlamlı; girmeyeceksek listeden düşer. Tohum/gübre bildirimleri (6) lot altyapısı hazır olduğu için ucuz. Banka hareketi eşleştirme (7) ve tarım kartı taksit (8) tahsilat tarafında rakiplerin (Logo, Mikro, Paraşüt) standart sunduğu şeyler. Çoklu şube/kasa (10) büyüyen bayide ilk duvar. KVKK (22) TCKN tuttuğumuz için ertelenemez ama küçük.

Faz 9/10 dokunmadan Faz 11 adayı olarak öneriyorum; Gemini raporu geldiğinde bu tabloyla birleştirip berkay'a önceliklendirme için sunulacak.

## Kaynaklar
- [AA — B-Reçete zorunlu oldu](https://www.aa.com.tr/tr/ekonomi/bitki-koruma-urunlerinin-satisinda-elektronik-b-recete-takip-sistemi-zorunlu-oldu/3770103)
- [TİM — B-Reçete](https://tim.org.tr/tr/bitki-koruma-urunlerinin-receteli-satisinda-elektronik-b-recete-takip-sistemi-zorunl)
- [Uyumsoft — e-İrsaliye 2026](https://www.uyumsoft.com/blog/e-irsaliye-gecis-zorunlulugu-2026)
- [Müşavirler Kulübü — e-Müstahsil 2026](https://musavirlerkulubu.com.tr/makale/e-mustahsil-makbuzu-duzenleme-kurallari-2026-guncel-rehber)
- [Ticaret Bakanlığı — HKS](https://ticaret.gov.tr/ic-ticaret/bilgi-sistemleri/hal-kayit-sistemi-hks)
- [Ticaret Bakanlığı — Lisanslı depo inceleme rehberi](https://ticaret.gov.tr/data/6148975f13b87689cc1df761/Hububat,%20Baklagiller%20ve%20Ya%C4%9Fl%C4%B1%20Tohumlar%20Lisansl%C4%B1%20Depolar%C4%B1%20%C4%B0nceleme%20Rehberi.pdf)

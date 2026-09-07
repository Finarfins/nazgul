"""WhatsApp katmanı: niyet çözücü + köprü imzacısı (WA3) + giriş (WA1).

WA3 bu paketi SAF KOD olarak açtı — veritabanı yok, tablo yok, rota yok.
WA1 o cümleyi DEĞİŞTİRDİ ve nerede değiştirdiği modül modül yazılı:

SAF (WA3-core) — yalnız standart kütüphane ve ``app.config``:

* ``telefon``   — numara kanonikleştirme (Meta ``from`` biçimi ve E.164).
* ``niyet``     — metin → niyet + argüman; deterministik, LLM'siz, DB'siz.
* ``kopru``     — Harman AI köprüsü için imza üretimi/doğrulaması, istemci.
* ``cloud_api`` — Meta webhook gövdesinin çözümlemesi ve ``X-Hub-Signature-
  256`` doğrulaması. BAĞIMLILIKSIZ: ne veritabanı ne HTTP çerçevesi bilir;
  ``bytes`` ile bir başlık alır, ``bool`` ya da veri sınıfı döner.

VERİTABANINA DOKUNAN (WA1) — SQLAlchemy'ye bağlıdır:

* ``schema``    — ``whatsapp_inbound`` ve ``whatsapp_pairing_attempts``
  Core tanımları. İKİSİ DE PLATFORM TABLOSUDUR (``company_id`` YOK);
  gerekçe göç ``20260910_0078``in başlığında.
* ``giris``     — doğrulanmış mesajı kalıcı kuyruğa yazan tek fonksiyon.

ROTA BU PAKETTE YİNE DE YOK: uçlar ``app/routers/whatsapp.py``dedir. Ayrım
korunuyor çünkü bu paketi içe aktaran bir test ya da işçi, FastAPI
uygulamasını da yüklemek ZORUNDA OLMAMALI.

Kaynak: ``nazgul_website/backend/app/whatsapp`` (niyet.py, danisman.py,
telefon.py, cloud_api.py, schema.py, service.py). Hangi parçanın birebir,
hangisinin uyarlanarak taşındığı her modülün başlığında yazar.

Bu ``__init__`` BİLEREK boş: alt modülleri içe aktarmaz. Paketi içe aktaran
biri ``kopru``yu (dolayısıyla ``app.config``i) ya da ``schema``yı
(dolayısıyla SQLAlchemy'yi) istemeden yüklememeli.
"""

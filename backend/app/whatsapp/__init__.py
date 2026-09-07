"""WhatsApp katmanı (WA3-core): niyet çözücü + köprü imzacısı — SAF KOD.

Bu paket bu dilimde HİÇBİR ŞEYE BAĞLI DEĞİLDİR: veritabanı yok, tablo yok,
rota yok, ``main.py`` kablosu yok. İçindeki her modül yalnız standart
kütüphane ve ``app.config`` (``kopru``) ile ayakta durur.

* ``telefon``  — numara kanonikleştirme (Meta ``from`` biçimi ve E.164).
* ``niyet``    — metin → niyet + argüman; deterministik, LLM'siz, DB'siz.
* ``kopru``    — Harman AI köprüsü için imza üretimi/doğrulaması ve istemci.

Kaynak: ``nazgul_website/backend/app/whatsapp`` (niyet.py, danisman.py,
telefon.py). Hangi parçanın birebir, hangisinin uyarlanarak taşındığı her
modülün başlığında yazar.

Bu ``__init__`` BİLEREK boş: alt modülleri içe aktarmaz. Paketi içe aktaran
biri ``kopru``yu (dolayısıyla ``app.config``i) istemeden yüklememeli.
"""

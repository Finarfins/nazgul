"""Konsol kodlamasına dayanıklı günlük akışı (H70).

ÖLÇÜLDÜ: `yerel_hesap` günlüğü `logging.StreamHandler(sys.stdout)` ile
yazıyordu. Windows'ta konsol cp1252 ise (`PYTHONIOENCODING=cp1252` ile
yeniden üretildi) `sys.stdout` KATI kodlar ve cp1252'de OLMAYAN Türkçe harf
(`ı`, `ğ`, `ş`, `İ`) içeren HER satır `UnicodeEncodeError` verir; `logging`
bunu yutar, `--- Logging error ---` basar ve SATIR KAYBOLUR — ör.
"Veritabanı migration seviyesi doğrulandı". cp1254'te (Türkçe Windows) ve
UTF-8'de görünmez; CI Linux/UTF-8 olduğu için yeşil kalır.

`sys.stderr` zaten `backslashreplace` ile açılır, o yüzden alembic'in
`handler_console`ı (`sys.stderr`) bu sorunu yaşamaz. Burada da aynı kural
uygulanır: kodlanamayan karakter `\\u0131` biçiminde yazılır, satır DÜŞMEZ.
UTF-8 akışta hiçbir şey değişmez.
"""

from __future__ import annotations

import logging


class KodlamayaDayanikliAkis(logging.StreamHandler):
    """`StreamHandler`; akışın kodlaması karakteri taşıyamazsa kaçışla yazar."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            metin = self.format(record) + self.terminator
            try:
                self.stream.write(metin)
            except UnicodeEncodeError:
                kodlama = getattr(self.stream, "encoding", None) or "ascii"
                self.stream.write(
                    metin.encode(kodlama, "backslashreplace").decode(kodlama)
                )
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)

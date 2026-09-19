"""Uçlarda paylaşılan sayısal sınırlar — TEK EV.

``INT4_UST`` burada yaşar ve her uç onu buradan alır. Aynı sayıyı iki
yönlendiriciye ayrı ayrı yazmak, biri değiştiğinde ötekinin sessizce eski
kalması demekti (H69: sabit ``routers/cek_senetler.py``deydi,
``despatch_notes.py`` onu KARDEŞ bir yönlendiriciden içe aktarıyordu).
"""

from __future__ import annotations

#: Kimlik ve sayfa (``offset``) parametrelerinin tavanı: PostgreSQL ``INTEGER``
#: (int4) sütununun üst sınırı, 2^31-1.
#:
#: NEDEN int4 ve NEDEN 2^63-1 DEĞİL: tavan VERİTABANININ tavanıdır, sürücünün
#: değil. psycopg 2^63-1'e kadar her tamsayıyı bağlar; ama kimlik sütunları
#: int4'tür ve ``::INTEGER`` bağı ya da int4 sütunla karşılaştırma bu aralığın
#: dışını ``NumericValueOutOfRange`` (500) yapar. Sınır uçta ``le=INT4_UST``
#: olunca aralık dışı 422 döner ve sürücüye hiç ulaşmaz. ``offset`` da aynı
#: tavanı taşır (H54): hiçbir tablo 2^31 satıra yaklaşmaz, yani bu tavan
#: geçerli bir sayfayı reddetmez — yalnız sürücüye taşan değeri keser.
INT4_UST = 2147483647

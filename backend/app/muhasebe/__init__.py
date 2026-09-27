"""Muhasebe fişi / KDV özeti / hesap planı eşlemesi (F9-5a).

Tasarım: `docs/f9-5-muhasebe-disa-aktarim-kesif-2026-09-24.md` §3.6, §4, §7.1.

* `fis.py`         — SAF: `FisSatiri`/`Fis` + dengeli-fiş kapısı (SQL yok).
* `schema.py`      — `muhasebe_hesap_eslemeleri` Core tanımı (göç 0093 ile
  BİREBİR) ve okuma için dar belge tabloları.
* `hesap_plani.py` — Tek Düzen varsayılanları (KODDA) + firma eşlemesi.
* `kaynak.py`      — belge tablolarının TEK okuma yeri: dönem → `Fis`.
* `kdv_ozeti.py`   — SAF: `kaynak`ın satır gruplarından aylık KDV özeti.
"""

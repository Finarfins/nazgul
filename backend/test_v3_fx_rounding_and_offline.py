"""V3 Purchase Comparison — FX rounding contract + TCMB offline degradation.

Two regression areas guarded here:

  1. ``POST /api/exchange-rates/refresh`` must NOT hard-fail when TCMB is
     unreachable or returns XML we cannot parse. On an offline / LAN install the
     request returns a 200 degraded body and the company keeps using its manual
     overrides; the comparison still works with a missing rate (price_in_try is
     null and the offer sorts last).

  2. ``to_try`` quantizes the normalised TRY value to 4dp (ROUND_HALF_UP) and the
     API response, the price-history snapshot, and the best-offer ranking all use
     that one identical value — so a rounding tie stays a tie.

The pure-function checks run in-process (``app.exchange_rates`` touches no DB).
The endpoint checks run in a subprocess against a throwaway SQLite database so
each test gets an isolated ``DATABASE_URL`` (the engine is bound at import time).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

import pytest

from app.exchange_rates import NORMALIZED_TRY_QUANTUM, _parse_tcmb, to_try


BACKEND = Path(__file__).resolve().parent


# --- Pure-function contract (no DB) ---------------------------------------
def test_to_try_quantizes_to_four_dp_round_half_up() -> None:
    # Canonical contract: normalised TRY is carried at exactly 4 decimal places.
    assert NORMALIZED_TRY_QUANTUM == Decimal("0.0001")
    # 5th decimal == 5 rounds up (HALF_UP); one less rounds down. Rate is 6dp.
    assert to_try(Decimal("1"), Decimal("1.00005")) == Decimal("1.0001")
    assert to_try(Decimal("1"), Decimal("1.00004")) == Decimal("1.0000")
    # The returned value is normalised to 4dp regardless of input scale.
    assert to_try(Decimal("2"), Decimal("1.000017")).as_tuple().exponent == -4


def test_to_try_missing_rate_returns_none() -> None:
    # A missing rate is not an error — the caller renders price_in_try as null.
    assert to_try(Decimal("3"), None) is None


def test_parse_tcmb_malformed_xml_raises() -> None:
    # Malformed XML must raise so the refresh endpoint can degrade gracefully.
    with pytest.raises(ET.ParseError):
        _parse_tcmb(b"<Tarih_Date><Currency this is not valid xml <<")


def test_parse_tcmb_valid_xml_extracts_forex_selling() -> None:
    xml = (
        b"<Tarih_Date>"
        b'<Currency CurrencyCode="EUR"><ForexSelling>35,1234</ForexSelling></Currency>'
        b'<Currency CurrencyCode="USD"><ForexSelling>32,0000</ForexSelling></Currency>'
        b'<Currency CurrencyCode="GBP"><ForexSelling>40,0000</ForexSelling></Currency>'
        b"</Tarih_Date>"
    )
    rates = _parse_tcmb(xml)
    assert rates == {"EUR": Decimal("35.1234"), "USD": Decimal("32.0000")}


# --- Endpoint contract (subprocess, isolated SQLite) ----------------------
# ``_Fake`` stands in for urlopen()'s return value so the fetch path runs for
# real up to (and including) _parse_tcmb, without touching the network.
_SETUP = r"""
from decimal import Decimal
from fastapi.testclient import TestClient
import app.exchange_rates as ex
from app.db import engine
from sqlalchemy import text as _sql
from app.main import app


class _Fake:
    def __init__(self, data): self._data = data
    def read(self): return self._data
    def __enter__(self): return self
    def __exit__(self, *exc): return False


with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    cid = login['companies'][0]['id']
    headers = {'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'FxContract123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
    product = client.post('/api/products', headers=headers,
                          json={'name':'Kur Testi','purchase_price':'10'}).json()['id']
    supplier_a = client.post('/api/suppliers', headers=headers, json={'name':'Ted A'}).json()['id']
    supplier_b = client.post('/api/suppliers', headers=headers, json={'name':'Ted B'}).json()['id']
    supplier_c = client.post('/api/suppliers', headers=headers, json={'name':'Ted C'}).json()['id']
"""


def _run(body: str, tmp_path: Path, name: str) -> None:
    database = tmp_path / f"{name}.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    script = _SETUP + textwrap.indent(textwrap.dedent(body), "    ")
    completed = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert f"{name}_OK" in completed.stdout, completed.stdout + completed.stderr


def test_refresh_degrades_on_network_error(tmp_path: Path) -> None:
    # (a) Network error: the real fetch path raises URLError; the endpoint must
    # return a 200 degraded body instead of 503.
    _run(
        r"""
        import urllib.error
        def _boom(*a, **k):
            raise urllib.error.URLError('offline install')
        ex.urlopen = _boom
        resp = client.post('/api/exchange-rates/refresh', headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            'updated': {}, 'source': 'MANUAL_OVERRIDE',
            'warning': 'TCMB unavailable; manual overrides remain active',
        }, resp.text
        print('refresh_network_OK')
        """,
        tmp_path,
        "refresh_network",
    )


def test_refresh_degrades_on_malformed_xml(tmp_path: Path) -> None:
    # (b) Malformed XML: the fetch succeeds at the socket but _parse_tcmb raises;
    # the endpoint must still degrade to 200 rather than 502.
    _run(
        r"""
        ex.urlopen = lambda *a, **k: _Fake(b'<Tarih_Date><Currency <<not xml')
        resp = client.post('/api/exchange-rates/refresh', headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['source'] == 'MANUAL_OVERRIDE' and body['updated'] == {}, resp.text
        print('refresh_malformed_OK')
        """,
        tmp_path,
        "refresh_malformed",
    )


def test_refresh_stores_rates_on_success(tmp_path: Path) -> None:
    # Positive control: valid TCMB XML still stores rates and reports source TCMB.
    _run(
        r"""
        xml = (b'<Tarih_Date>'
               b'<Currency CurrencyCode="EUR"><ForexSelling>35,0000</ForexSelling></Currency>'
               b'<Currency CurrencyCode="USD"><ForexSelling>32,0000</ForexSelling></Currency>'
               b'</Tarih_Date>')
        ex.urlopen = lambda *a, **k: _Fake(xml)
        resp = client.post('/api/exchange-rates/refresh', headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['source'] == 'TCMB', resp.text
        assert Decimal(body['updated']['EUR']) == Decimal('35') and 'USD' in body['updated'], resp.text
        print('refresh_success_OK')
        """,
        tmp_path,
        "refresh_success",
    )


def test_missing_rate_sorts_last_with_null_price(tmp_path: Path) -> None:
    # Comparison keeps working when a rate is missing: supplier B's EUR price has
    # no override and no stored TCMB rate, so price_in_try is null and it sorts
    # last; supplier A (TRY, rankable) is the best offer.
    _run(
        r"""
        client.post('/api/supplier-prices', headers=headers,
                    json={'supplier_id':supplier_a,'product_id':product,'price':'50','currency':'TRY'})
        client.post('/api/supplier-prices', headers=headers,
                    json={'supplier_id':supplier_b,'product_id':product,'price':'3','currency':'EUR'})
        payload = client.get(f'/api/products/{product}/supplier-prices', headers=headers).json()
        assert payload['rates']['EUR'] is None, payload['rates']
        rows = {r['supplier_id']: r for r in payload['items']}
        assert rows[supplier_b]['manual_price_in_try'] is None
        assert rows[supplier_b]['price_in_try'] is None
        assert rows[supplier_b]['best_offer'] is False
        assert rows[supplier_a]['best_offer'] is True
        # Rankable offer first, un-rankable (null) last.
        assert [r['supplier_id'] for r in payload['items']] == [supplier_a, supplier_b]
        print('missing_rate_OK')
        """,
        tmp_path,
        "missing_rate",
    )


def test_four_dp_boundary_same_value_response_history_ranking(tmp_path: Path) -> None:
    # 4dp boundary: 1 EUR @ 1.00005 -> 1.00005 -> rounds HALF_UP to 1.0001. The
    # SAME rounded value appears in the response's manual_price_in_try, in the
    # ranking price (price_in_try), and in the persisted history snapshot.
    _run(
        r"""
        client.put('/api/exchange-rates/override', headers=headers,
                   json={'currency':'EUR','rate_to_try':'1.00005'})
        client.post('/api/supplier-prices', headers=headers,
                    json={'supplier_id':supplier_b,'product_id':product,'price':'1','currency':'EUR'})
        row = next(r for r in client.get(f'/api/products/{product}/supplier-prices',
                                         headers=headers).json()['items']
                   if r['supplier_id'] == supplier_b)
        assert row['manual_price_in_try'] == '1.0001', row
        assert row['price_in_try'] == '1.0001', row   # ranking uses the same value
        with engine.connect() as conn:
            snap = conn.execute(_sql(
                "SELECT price_in_try FROM supplier_price_history "
                "WHERE supplier_id=:s AND product_id=:p ORDER BY id DESC LIMIT 1"),
                {'s': supplier_b, 'p': product}).scalar()
        assert Decimal(str(snap)) == Decimal('1.0001'), snap
        print('four_dp_OK')
        """,
        tmp_path,
        "four_dp",
    )


def test_visually_equal_tie_offers_single_best(tmp_path: Path) -> None:
    # Two offers whose raw normalisations differ (3.000051 vs 3.000050) but round
    # to the SAME 4dp value (3.0001). They display an identical price_in_try and
    # exactly one is flagged best_offer — the rounding tie is deterministic.
    _run(
        r"""
        client.put('/api/exchange-rates/override', headers=headers,
                   json={'currency':'EUR','rate_to_try':'1.000017'})
        client.put('/api/exchange-rates/override', headers=headers,
                   json={'currency':'USD','rate_to_try':'3.00005'})
        client.post('/api/supplier-prices', headers=headers,
                    json={'supplier_id':supplier_b,'product_id':product,'price':'3','currency':'EUR'})
        client.post('/api/supplier-prices', headers=headers,
                    json={'supplier_id':supplier_c,'product_id':product,'price':'1','currency':'USD'})
        rows = {r['supplier_id']: r for r in
                client.get(f'/api/products/{product}/supplier-prices', headers=headers).json()['items']}
        assert rows[supplier_b]['price_in_try'] == '3.0001', rows[supplier_b]
        assert rows[supplier_c]['price_in_try'] == '3.0001', rows[supplier_c]
        best = [sid for sid, r in rows.items() if r['best_offer']]
        assert len(best) == 1, best   # exactly one best despite the tie
        assert best[0] in (supplier_b, supplier_c)
        print('tie_OK')
        """,
        tmp_path,
        "tie",
    )

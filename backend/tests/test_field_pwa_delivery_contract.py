from pathlib import Path


def test_field_worker_is_the_only_asset_granted_saha_scope() -> None:
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert 'path == "field-pwa/sw-v1.js"' in source
    assert '"Service-Worker-Allowed": "/saha/"' in source
    assert '"Cache-Control": "no-cache"' in source

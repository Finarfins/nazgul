from __future__ import annotations

import logging
import traceback


def test_unhandled_500_emits_traceback_to_app_logger() -> None:
    """Regression for the production logging blindspot.

    Startup migrations run at ``app.main`` import time and alembic's env.py used
    to call ``fileConfig()`` with the default ``disable_existing_loggers=True``.
    That silently set ``.disabled = True`` on the already-created ``yerel_hesap``
    logger (and uvicorn's error/access loggers), so production 500s emitted no
    traceback at all — the only evidence was UNHANDLED_EXCEPTION rows in
    security_audit_logs. This test imports the app the same way and proves the
    logger survives startup and an unhandled exception is logged with exc_info.
    """
    from fastapi.testclient import TestClient

    from app.main import app  # import runs migrations -> alembic fileConfig

    app_logger = logging.getLogger("yerel_hesap")
    assert not app_logger.disabled, "alembic fileConfig disabled the app logger"
    assert app_logger.handlers, "app logger must own a stdout handler"

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.ERROR)
    app_logger.addHandler(handler)

    def _boom() -> None:
        raise RuntimeError("boom-logging-visibility")

    # The app ends with a catch-all SPA route, so a route appended normally
    # would never match — insert at the front of the route table instead.
    from fastapi.routing import APIRoute

    boom_route = APIRoute("/_boom_logging_test", endpoint=_boom, methods=["GET"])
    app.router.routes.insert(0, boom_route)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/_boom_logging_test")
        assert response.status_code == 500, response.text

        errored = [record for record in records if record.exc_info]
        assert errored, "unhandled 500 must be logged with exc_info (traceback)"
        rendered = "".join(traceback.format_exception(*errored[-1].exc_info))
        assert "boom-logging-visibility" in rendered
    finally:
        app.router.routes.remove(boom_route)
        app_logger.removeHandler(handler)

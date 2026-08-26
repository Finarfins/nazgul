from __future__ import annotations

import sqlite3

from app.routers.search import _like


def _literal_like_matches(values: list[str], query: str) -> list[str]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE samples (value TEXT NOT NULL)")
        connection.executemany("INSERT INTO samples(value) VALUES (?)", ((value,) for value in values))
        rows = connection.execute(
            "SELECT value FROM samples WHERE value LIKE ? ESCAPE '\\' ORDER BY value",
            (_like(query),),
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        connection.close()


def test_like_escapes_sql_wildcard_characters() -> None:
    assert _like("  %_\\  ") == "%\\%\\_\\\\%"


def test_percent_is_searched_as_literal_text() -> None:
    assert _literal_like_matches(["100%", "1000", "percent"], "%") == ["100%"]


def test_underscore_is_searched_as_literal_text() -> None:
    assert _literal_like_matches(["OEM_123", "OEMX123", "OEM-123"], "OEM_123") == ["OEM_123"]


def test_backslash_remains_searchable_as_literal_text() -> None:
    assert _literal_like_matches([r"A\B", "AB", "A/B"], r"A\B") == [r"A\B"]

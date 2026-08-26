from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, select, table, column
from sqlalchemy.engine import Engine

from .numeric_manifest import iter_numeric_columns

SNAPSHOT_VERSION = 1


@dataclass(frozen=True)
class NumericDifference:
    table: str
    column: str
    field: str
    before: Any
    after: Any


def _decimal_text(value: object, scale: int) -> str | None:
    if value is None:
        return None
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Sayısal değere dönüştürülemedi: {value!r}") from exc
    quantum = Decimal(1).scaleb(-scale)
    return format(decimal_value.quantize(quantum, rounding=ROUND_HALF_UP), f".{scale}f")


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def capture_numeric_snapshot(target_engine: Engine) -> dict[str, Any]:
    """Capture a deterministic accounting fingerprint for all known numeric columns.

    Values are quantized with the same ``ROUND_HALF_UP`` policy used by the
    PostgreSQL hardening migration. This lets operators prove that row counts,
    NULL counts and rounded totals did not change across the migration.
    """

    inspector = inspect(target_engine)
    available_tables = set(inspector.get_table_names())
    columns_payload: list[dict[str, Any]] = []

    with target_engine.connect().execution_options(stream_results=True) as connection:
        preparer = target_engine.dialect.identifier_preparer
        for table_name, column_name, precision, scale, kind in iter_numeric_columns():
            if table_name not in available_tables:
                continue
            declared_columns = {item["name"] for item in inspector.get_columns(table_name)}
            if column_name not in declared_columns:
                continue

            quoted_table = preparer.quote(table_name)
            quoted_column = preparer.quote(column_name)
            rows = connection.exec_driver_sql(
                f"SELECT {quoted_column} FROM {quoted_table}"
            )

            total_rows = 0
            null_count = 0
            non_null_count = 0
            total = Decimal("0")
            minimum: Decimal | None = None
            maximum: Decimal | None = None
            quantum = Decimal(1).scaleb(-scale)

            for (raw_value,) in rows:
                total_rows += 1
                if raw_value is None:
                    null_count += 1
                    continue
                try:
                    decimal_value = (
                        raw_value if isinstance(raw_value, Decimal) else Decimal(str(raw_value))
                    )
                except (InvalidOperation, ValueError, TypeError) as exc:
                    raise ValueError(
                        f"{table_name}.{column_name} sayısal olmayan değer içeriyor: {raw_value!r}"
                    ) from exc
                rounded = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
                non_null_count += 1
                total += rounded
                minimum = rounded if minimum is None or rounded < minimum else minimum
                maximum = rounded if maximum is None or rounded > maximum else maximum

            columns_payload.append(
                {
                    "table": table_name,
                    "column": column_name,
                    "kind": kind,
                    "precision": precision,
                    "scale": scale,
                    "rows": total_rows,
                    "non_null": non_null_count,
                    "nulls": null_count,
                    "sum": _decimal_text(total, scale),
                    "min": _decimal_text(minimum, scale),
                    "max": _decimal_text(maximum, scale),
                }
            )

    payload: dict[str, Any] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dialect": target_engine.dialect.name,
        "database": target_engine.url.render_as_string(hide_password=True),
        "columns": sorted(columns_payload, key=lambda item: (item["table"], item["column"])),
    }
    payload["sha256"] = _checksum(payload)
    return payload


def verify_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("snapshot_version") != SNAPSHOT_VERSION:
        raise ValueError("Desteklenmeyen mutabakat snapshot sürümü")
    supplied = snapshot.get("sha256")
    if not isinstance(supplied, str):
        raise ValueError("Snapshot checksum içermiyor")
    unsigned = dict(snapshot)
    unsigned.pop("sha256", None)
    expected = _checksum(unsigned)
    if supplied != expected:
        raise ValueError("Snapshot checksum doğrulaması başarısız")


def compare_numeric_snapshots(
    before: dict[str, Any], after: dict[str, Any]
) -> list[NumericDifference]:
    verify_snapshot(before)
    verify_snapshot(after)

    before_columns = {
        (item["table"], item["column"]): item for item in before.get("columns", [])
    }
    after_columns = {
        (item["table"], item["column"]): item for item in after.get("columns", [])
    }

    differences: list[NumericDifference] = []
    for key in sorted(set(before_columns) | set(after_columns)):
        left = before_columns.get(key)
        right = after_columns.get(key)
        table_name, column_name = key
        if left is None or right is None:
            differences.append(
                NumericDifference(table_name, column_name, "presence", left is not None, right is not None)
            )
            continue
        for field in ("precision", "scale", "rows", "non_null", "nulls", "sum", "min", "max"):
            if left.get(field) != right.get(field):
                differences.append(
                    NumericDifference(
                        table_name,
                        column_name,
                        field,
                        left.get(field),
                        right.get(field),
                    )
                )
    return differences


def write_snapshot(snapshot: dict[str, Any], output_path: Path) -> None:
    verify_snapshot(snapshot)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output_path)


def read_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Snapshot JSON nesnesi olmalı")
    verify_snapshot(payload)
    return payload

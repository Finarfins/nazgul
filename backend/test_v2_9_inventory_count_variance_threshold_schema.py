from app.main import app


def test_variance_threshold_query_is_non_negative() -> None:
    operation = app.openapi()["paths"]["/api/exports/warehouse-count-variance.xlsx"]["get"]
    parameter = next(
        item for item in operation["parameters"]
        if item["name"] == "min_absolute_variance"
    )
    schema = parameter["schema"]
    numeric = next(item for item in schema["anyOf"] if item.get("type") == "number")
    assert numeric["minimum"] == 0
    assert schema["default"] == "0"

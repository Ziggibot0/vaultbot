from __future__ import annotations

import pytest
from custom_tools import table_query as table_query_tool
from table_query_engine import TableQueryError, query_table, resolve_vault_table

pytestmark = pytest.mark.unit


def test_grouped_aggregate_returns_exact_values_and_provenance(tmp_path):
    table = tmp_path / "sales.csv"
    table.write_text(
        "Region,Revenue\nWest,120.5\nEast,80\nWest,29.5\n",
        encoding="utf-8",
    )

    result = query_table(
        tmp_path,
        source="sales.csv",
        operation="aggregate",
        columns=["Revenue"],
        aggregate="sum",
        group_by=["Region"],
    )

    assert result["columns"] == ["Region", "value"]
    assert sorted(result["rows"]) == [["East", 80.0], ["West", 150.0]]
    assert result["provenance"]["source"] == str(table.resolve())
    assert result["provenance"]["input_rows"] == 3
    assert result["provenance"]["output_rows"] == 2


def test_preview_is_bounded(tmp_path):
    table = tmp_path / "items.tsv"
    table.write_text("id\tname\n1\tone\n2\ttwo\n3\tthree\n", encoding="utf-8")

    result = query_table(tmp_path, source="items.tsv", operation="preview", limit=2)

    assert result["columns"] == ["id", "name"]
    assert len(result["rows"]) == 2


def test_rejects_path_outside_vault(tmp_path):
    outside = tmp_path.parent / "outside.csv"
    outside.write_text("id\n1\n", encoding="utf-8")

    with pytest.raises(TableQueryError, match="inside the vault"):
        resolve_vault_table(tmp_path, "../outside.csv")


def test_rejects_unknown_columns(tmp_path):
    table = tmp_path / "items.csv"
    table.write_text("id,name\n1,one\n", encoding="utf-8")

    with pytest.raises(TableQueryError, match="Unknown columns"):
        query_table(
            tmp_path,
            source="items.csv",
            operation="aggregate",
            columns=["missing"],
            aggregate="sum",
        )


def test_xlsx_requires_sheet_and_supports_preview_and_aggregate(tmp_path):
    from openpyxl import Workbook

    table = tmp_path / "sales.xlsx"
    workbook = Workbook()
    sales = workbook.active
    sales.title = "Sales"
    sales.append(["Region", "Revenue"])
    sales.append(["West", 120.5])
    sales.append(["West", 29.5])
    targets = workbook.create_sheet("Targets")
    targets.append(["Region", "Target"])
    targets.append(["West", 100])
    workbook.save(table)

    with pytest.raises(TableQueryError, match="multiple sheets"):
        query_table(tmp_path, source="sales.xlsx", operation="preview")

    result = query_table(
        tmp_path,
        source="sales.xlsx",
        operation="preview",
        sheet="Sales",
    )

    assert result["rows"] == [["West", 120.5], ["West", 29.5]]
    assert result["provenance"]["sheet"] == "Sales"

    aggregate = query_table(
        tmp_path,
        source="sales.xlsx",
        operation="aggregate",
        sheet="Sales",
        columns=["Revenue"],
        aggregate="sum",
        group_by=["Region"],
    )
    assert aggregate["rows"] == [["West", 150.0]]


def test_filters_are_parameterized_and_null_aware(tmp_path):
    table = tmp_path / "sales.csv"
    table.write_text(
        "Region,Revenue,Note\n"
        "West,120.5,normal\n"
        "East,,missing\n"
        "North,90,x' OR 1=1 --\n",
        encoding="utf-8",
    )

    injected = query_table(
        tmp_path,
        source="sales.csv",
        operation="preview",
        filters=[{"column": "Note", "operator": "eq", "value": "x' OR 1=1 --"}],
    )
    assert injected["rows"] == [["North", 90.0, "x' OR 1=1 --"]]

    missing = query_table(
        tmp_path,
        source="sales.csv",
        operation="preview",
        columns=["Region"],
        filters=[{"column": "Revenue", "operator": "is_null"}],
    )
    assert missing["rows"] == [["East"]]
    assert missing["provenance"]["filters"][0]["operator"] == "is_null"


def test_inner_and_left_join_return_multi_source_provenance(tmp_path):
    sales = tmp_path / "sales.csv"
    sales.write_text("Region,Revenue\nWest,120\nEast,80\nNorth,20\n", encoding="utf-8")
    targets = tmp_path / "targets.csv"
    targets.write_text("Region,Target\nWest,100\nEast,90\nEast,95\n", encoding="utf-8")

    inner = query_table(
        tmp_path,
        source="sales.csv",
        operation="join",
        columns=["Region", "Revenue"],
        join_source="targets.csv",
        left_on="Region",
        right_on="Region",
        right_columns=["Target"],
        filters=[{"column": "Revenue", "operator": "gte", "value": 80}],
    )
    assert inner["columns"] == ["left__Region", "left__Revenue", "right__Target"]
    assert sorted(inner["rows"]) == [
        ["East", 80, 90],
        ["East", 80, 95],
        ["West", 120, 100],
    ]
    assert inner["provenance"]["join"]["source"] == str(targets.resolve())
    assert inner["provenance"]["join"]["join_type"] == "inner"

    left = query_table(
        tmp_path,
        source="sales.csv",
        operation="join",
        columns=["Region"],
        join_source="targets.csv",
        left_on="Region",
        right_on="Region",
        right_columns=["Target"],
        join_type="left",
    )
    assert ["North", None] in left["rows"]


def test_join_validates_required_fields_and_secondary_path(tmp_path):
    table = tmp_path / "sales.csv"
    table.write_text("Region\nWest\n", encoding="utf-8")
    outside = tmp_path.parent / "outside.csv"
    outside.write_text("Region\nWest\n", encoding="utf-8")

    with pytest.raises(TableQueryError, match="requires"):
        query_table(tmp_path, source="sales.csv", operation="join")
    with pytest.raises(TableQueryError, match="inside the vault"):
        query_table(
            tmp_path,
            source="sales.csv",
            operation="join",
            join_source="../outside.csv",
            left_on="Region",
            right_on="Region",
        )


def test_custom_tool_schema_and_error_envelope():
    schema = table_query_tool.SCHEMA

    assert schema["name"] == "table_query"
    assert "join" in schema["parameters"]["properties"]["operation"]["enum"]
    assert schema["parameters"]["properties"]["filters"]["items"]["required"] == [
        "column",
        "operator",
    ]
    result = table_query_tool.run({"source": "missing.csv", "operation": "inspect"})
    assert result["status"] == "error"

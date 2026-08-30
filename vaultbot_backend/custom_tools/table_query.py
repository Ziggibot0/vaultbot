"""Read-only structured queries over CSV, TSV, and XLSX vault files."""

from custom_tools._table_query_engine import TableQueryError, query_table
from paths import VAULT_ROOT

SCHEMA = {
    "name": "table_query",
    "description": (
        "Inspect, preview, filter, aggregate, or join CSV, TSV, and XLSX files "
        "in the vault. Use this for exact table arithmetic; never calculate from "
        "embedded sample rows. Returns source and sheet provenance."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Vault-relative path."},
            "operation": {
                "type": "string",
                "enum": ["inspect", "preview", "aggregate", "join"],
            },
            "sheet": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "aggregate": {
                "type": "string",
                "enum": ["sum", "avg", "min", "max", "count"],
            },
            "group_by": {"type": "array", "items": {"type": "string"}},
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": [
                                "eq",
                                "ne",
                                "gt",
                                "gte",
                                "lt",
                                "lte",
                                "contains",
                                "is_null",
                                "not_null",
                            ],
                        },
                        "value": {},
                    },
                    "required": ["column", "operator"],
                },
            },
            "join_source": {"type": "string"},
            "join_sheet": {"type": "string"},
            "left_on": {"type": "string"},
            "right_on": {"type": "string"},
            "right_columns": {"type": "array", "items": {"type": "string"}},
            "join_type": {"type": "string", "enum": ["inner", "left"]},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["source", "operation"],
    },
}


def run(args: dict) -> dict:
    try:
        return query_table(
            VAULT_ROOT,
            source=args.get("source", ""),
            operation=args.get("operation", ""),
            sheet=args.get("sheet"),
            columns=args.get("columns"),
            aggregate=args.get("aggregate"),
            group_by=args.get("group_by"),
            filters=args.get("filters"),
            join_source=args.get("join_source"),
            join_sheet=args.get("join_sheet"),
            left_on=args.get("left_on"),
            right_on=args.get("right_on"),
            right_columns=args.get("right_columns"),
            join_type=args.get("join_type", "inner"),
            limit=args.get("limit", 20),
        )
    except TableQueryError as exc:
        return {"status": "error", "error": str(exc)}

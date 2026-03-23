from typing import Annotated

# Parameter types — descriptions surface in the MCP tool schema

Schema = Annotated[str, "Postgres schema name, defaults to 'public'"]

TableName = Annotated[str, "Table name without schema prefix, e.g. 'orders' not 'public.orders'"]

SQL = Annotated[
    str,
    "Read-only SQL query. Use $1, $2 for parameters. "
    "Prefer specific columns over SELECT *. "
    "Use JOINs from describe_table/find_related_tables output.",
]

ColumnKeyword = Annotated[
    str,
    "Substring to match against column names, case-insensitive. e.g. 'email', 'status', 'created'.",
]

ColumnName = Annotated[str, "Exact column name to inspect"]

EnumKeyword = Annotated[
    str,
    "Substring to match against enum type names, case-insensitive. "
    "Pass empty string to list all enums.",
]

SearchKeyword = Annotated[str, "Text to search for (case-insensitive substring match)"]

SampleSize = Annotated[int, "Number of sample rows to return (1-100)"]

TopN = Annotated[int, "Number of distinct values to return, ordered by frequency"]

ObjectName = Annotated[
    str,
    "Name of a database object (table, view, function, etc.) to inspect dependencies for.",
]

ObjectType = Annotated[
    str,
    "Type of database object: 'table', 'view', 'matview', 'function', 'sequence', or 'index'.",
]

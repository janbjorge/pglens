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

SourceTable = Annotated[
    str,
    "Starting table name for join path discovery, e.g. 'users'.",
]

TargetTable = Annotated[
    str,
    "Destination table name for join path discovery, e.g. 'order_items'.",
]

MaxDepth = Annotated[
    int,
    "Maximum number of FK hops to traverse (1-6). "
    "Higher values find longer paths but take more time.",
]

Analyze = Annotated[
    bool,
    "When true, actually execute the query to show real timings and row counts. Default false.",
]

Buffers = Annotated[
    bool,
    "When true (requires analyze=True), show buffer usage and cache hit statistics. Default false.",
]

Limit = Annotated[int, "Maximum number of rows to return (1-500, default 500)"]

Offset = Annotated[
    int,
    "Number of rows to skip before returning results (default 0). "
    "Warning: large offsets cause PostgreSQL to scan and discard rows, degrading performance. "
    "For paginating large result sets, prefer keyset pagination using WHERE clauses "
    "(e.g. WHERE id > last_seen_id ORDER BY id) instead of increasing offset.",
]

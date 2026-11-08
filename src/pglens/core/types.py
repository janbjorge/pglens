"""Parameter types shared by the MCP tools.

Every alias is ``Annotated[T, Field(...)]``. The mcp SDK builds each tool's
input schema from pydantic ``FieldInfo`` metadata only, so a bare string inside
``Annotated`` is silently dropped and never reaches the client. Descriptions,
``enum`` values (from ``Literal``) and ``ge``/``le`` ranges declared here are what
the model sees when it picks arguments. Range limits mirror the clamps in
``AsyncpgDatabase``, which stay in place as defense in depth.
"""

from typing import Annotated, Literal

from pydantic import Field

Database = Annotated[
    str | None,
    Field(
        description=(
            "Database alias to target. Default None is the primary alias (PGDATABASE); "
            "database_info lists every configured alias (available_databases)."
        )
    ),
]

Schema = Annotated[str, Field(description="Postgres schema name, defaults to 'public'")]

TableName = Annotated[
    str,
    Field(description="Table name without schema prefix, e.g. 'orders' not 'public.orders'"),
]

SQL = Annotated[
    str,
    Field(
        description=(
            "Read-only SQL SELECT statement. Inline literal values directly; parameter "
            "placeholders ($1, $2) are not supported and are rejected. "
            "Prefer specific columns over SELECT *. "
            "Use JOINs from describe_table (foreign_keys/referenced_by) or find_join_path output."
        )
    ),
]

ColumnKeyword = Annotated[
    str,
    Field(
        description=(
            "Substring to match against column names, case-insensitive. "
            "e.g. 'email', 'status', 'created'."
        )
    ),
]

ColumnName = Annotated[str, Field(description="Exact column name to inspect")]

EnumKeyword = Annotated[
    str,
    Field(
        description=(
            "Substring to match against enum type names, case-insensitive. "
            "Pass empty string to list all enums."
        )
    ),
]

SearchKeyword = Annotated[
    str, Field(description="Text to search for (case-insensitive substring match)")
]

SampleSize = Annotated[
    int, Field(ge=1, le=100, description="Number of sample rows to return (1-100)")
]

TopN = Annotated[
    int,
    Field(
        ge=1,
        le=100,
        description="Number of distinct values to return, ordered by frequency (1-100)",
    ),
]

TopQueries = Annotated[
    int,
    Field(
        ge=1,
        le=100,
        description=(
            "Number of statements to return, ordered by total execution time descending (1-100)."
        ),
    ),
]

ObjectName = Annotated[
    str,
    Field(
        description=(
            "Name of a database object (table, view, function, etc.) to inspect dependencies for."
        )
    ),
]

ObjectType = Annotated[
    Literal["table", "view", "matview", "function", "sequence", "index"],
    Field(description="Type of the database object named by object_name."),
]

SourceTable = Annotated[
    str,
    Field(description="Starting table name for join path discovery, e.g. 'users'."),
]

TargetTable = Annotated[
    str,
    Field(description="Destination table name for join path discovery, e.g. 'order_items'."),
]

MaxDepth = Annotated[
    int,
    Field(
        ge=1,
        le=6,
        description=(
            "Maximum number of FK hops to traverse (1-6). "
            "Higher values find longer paths but take more time."
        ),
    ),
]

Analyze = Annotated[
    bool,
    Field(
        description=(
            "When true, actually execute the query to show real timings and row counts. "
            "Default false."
        )
    ),
]

Buffers = Annotated[
    bool,
    Field(
        description=(
            "When true (requires analyze=True), show buffer usage and cache hit statistics. "
            "When false, the server default applies: PostgreSQL 18+ includes buffers "
            "automatically with analyze; older versions omit them. Default false."
        )
    ),
]

Limit = Annotated[
    int,
    Field(ge=1, le=500, description="Maximum number of rows to return (1-500, default 500)"),
]

Offset = Annotated[
    int,
    Field(
        ge=0,
        description=(
            "Number of rows to skip before returning results (default 0). "
            "Warning: large offsets cause PostgreSQL to scan and discard rows, degrading "
            "performance. For paginating large result sets, prefer keyset pagination using "
            "WHERE clauses (e.g. WHERE id > last_seen_id ORDER BY id) instead of increasing "
            "offset."
        ),
    ),
]

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
            "Use JOINs from describe_object (foreign_keys/referenced_by) or find_join_path output."
        )
    ),
]

ColumnName = Annotated[str, Field(description="Exact column name to inspect")]

TopN = Annotated[
    int,
    Field(
        ge=1,
        le=100,
        description="Number of distinct values to return, ordered by frequency (1-100)",
    ),
]

HealthLimit = Annotated[
    int,
    Field(
        ge=1,
        le=100,
        description=(
            "Rows per check (1-100): the top statements for slow_queries, the largest or "
            "most at-risk objects for the other checks."
        ),
    ),
]

ObjectName = Annotated[
    str,
    Field(description="Name of the object to describe, without schema prefix, e.g. 'orders'."),
]

ObjectType = Annotated[
    Literal["table", "view", "matview", "function", "sequence", "index"],
    Field(description="Kind of the object named by name."),
]

ObjectKind = Annotated[
    Literal[
        "table", "view", "matview", "index", "function", "sequence", "enum", "extension", "column"
    ],
    Field(description="Which catalogue to list."),
]

NamePattern = Annotated[
    str | None,
    Field(
        description=(
            "Case-insensitive substring to match on the object name; for kind='column' and "
            "kind='enum' it is the search keyword. None lists everything."
        )
    ),
]

HealthCheck = Literal["tables", "sizes", "indexes", "sequences", "matviews", "io", "slow_queries"]

HealthChecks = Annotated[
    list[HealthCheck] | None,
    Field(description="Checks to run, in this order. None runs all seven."),
]

ActivityView = Literal["sessions", "locks", "maintenance", "replication"]

ActivityViews = Annotated[
    list[ActivityView] | None,
    Field(description="Views to return, in this order. None returns all four."),
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

DetailLevel = Literal["names", "summary", "full"]

Detail = Annotated[
    DetailLevel,
    Field(
        description=(
            "How much to return per row: names (identifiers only), summary (default; "
            "everything except long definitions and source code) or full (everything)."
        )
    ),
]

ListLimit = Annotated[
    int,
    Field(
        ge=1,
        le=1000,
        description=(
            "Maximum number of rows to return (1-1000, default 100). If exactly this many "
            "rows come back, more may exist: raise the limit or narrow the question."
        ),
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
            "Rows to skip before returning results (default 0). Large offsets make PostgreSQL "
            "scan and discard rows; prefer keyset pagination (WHERE id > last_seen_id ORDER BY id)."
        ),
    ),
]

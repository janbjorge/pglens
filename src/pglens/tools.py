"""The nine MCP tools.

Each tool is a thin wrapper over one or more AsyncpgDatabase methods. The
dispatching tools (list_objects, describe_object, health, activity) match on
an enum parameter, one branch per member, so the catalogue stays behind a few
entry points. Importing this module registers the tools on the server.
"""

from pglens.database import select_detail
from pglens.server import ALWAYS_LOAD, READ_ONLY, Ctx, databases, db, mcp
from pglens.types import (
    SQL,
    ActivityView,
    ActivityViews,
    Analyze,
    Buffers,
    ColumnName,
    Database,
    Detail,
    HealthCheck,
    HealthChecks,
    HealthLimit,
    Limit,
    ListLimit,
    MaxDepth,
    NamePattern,
    ObjectKind,
    ObjectName,
    ObjectType,
    Offset,
    Schema,
    SourceTable,
    TableName,
    TargetTable,
    TopN,
)

# A name filter is applied after the fetch, so a filtered listing reads a whole
# catalogue page first. This is the adapter's upper bound on limit.
CATALOGUE_PAGE = 1000

HEALTH_CHECKS: tuple[HealthCheck, ...] = (
    "tables",
    "sizes",
    "indexes",
    "sequences",
    "matviews",
    "io",
    "slow_queries",
)
ACTIVITY_VIEWS: tuple[ActivityView, ...] = ("sessions", "locks", "maintenance", "replication")


@mcp.tool(annotations=READ_ONLY)
async def database_info(ctx: Ctx, database: Database = None) -> dict[str, object]:
    """Get PostgreSQL server and database identity: version, database name, current user,
    encoding, timezone, max_connections, uptime, total database size, the schemas with
    their table and view counts (`schemas`), and the configured database aliases
    (`available_databases`) that can be passed as the `database` argument on any tool.
    The first alias is the primary (PGDATABASE); extras come from PGLENS_DATABASES.
    Call this first when you need the Postgres version, want to verify which database
    you are connected to, or suspect the data lives outside the public schema.
    """
    source = db(ctx, database)
    info = await source.database_info()
    info["schemas"] = await source.list_schemas()
    info["available_databases"] = databases(ctx).names()
    return info


@mcp.tool(annotations=READ_ONLY, meta=ALWAYS_LOAD)
async def list_objects(
    ctx: Ctx,
    kind: ObjectKind,
    schema: Schema = "public",
    pattern: NamePattern = None,
    detail: Detail = "summary",
    limit: ListLimit = 100,
    database: Database = None,
) -> list[dict[str, object]]:
    """List objects of one kind in a schema. table: estimated row count and description.
    view and matview: description, populated status and size (definition with
    detail='full'). index: table, type, uniqueness, validity, size and scan counts
    (CREATE INDEX text with detail='full'). function: arguments, return type, language and
    volatility (source with detail='full'). sequence: current value and percentage
    consumed. enum: the allowed values. extension: installed extensions and versions.
    column: every column whose name contains pattern, across all tables in the schema,
    with its type. pattern is a case-insensitive substring on the object name. Start with
    kind='table' to see what the database contains.
    """
    source = db(ctx, database)
    page = CATALOGUE_PAGE if pattern else limit
    rows: list[dict[str, object]]
    name_keys: tuple[str, ...]
    match kind:
        case "table":
            rows, name_keys = await source.list_tables(schema, page), ("table_name",)
        case "view":
            rows, name_keys = await source.list_views(schema, page, detail), ("view_name",)
        case "matview":
            rows = await source.matview_status(schema, page, detail)
            name_keys = ("matview_name",)
        case "index":
            rows = await source.list_indexes(schema, page, detail)
            name_keys = ("index_name", "table_name")
        case "function":
            rows = await source.list_functions(schema, page, detail)
            name_keys = ("function_name",)
        case "sequence":
            rows, name_keys = await source.sequence_health(schema, page), ("sequence_name",)
        case "enum":
            rows = await source.search_enum_values(pattern or "", schema, limit)
            name_keys = ("enum_name",)
        case "extension":
            rows, name_keys = await source.list_extensions(), ("extname",)
        case "column":
            rows = await source.search_columns(pattern or "", schema, limit)
            name_keys = ("table_name", "column_name")
        case _:
            raise ValueError(f"Unknown kind {kind!r}")
    if pattern and kind not in ("enum", "column"):
        needle = pattern.lower()
        rows = [row for row in rows if needle in str(row[name_keys[0]]).lower()]
    return select_detail(rows[:limit], detail, names=name_keys, heavy=())


@mcp.tool(annotations=READ_ONLY, meta=ALWAYS_LOAD)
async def describe_object(
    ctx: Ctx,
    name: ObjectName,
    kind: ObjectType = "table",
    schema: Schema = "public",
    detail: Detail = "summary",
    database: Database = None,
) -> dict[str, object]:
    """Describe one object. For a table (the default kind): columns with types, defaults
    and nullability, primary keys, foreign keys in both directions, indexes, check
    constraints, triggers, row-level security policies, and dependents (the views,
    functions and constraints that would break on DROP or ALTER). detail='full' adds the
    CREATE INDEX text; detail='names' returns only column names and primary keys.
    For view, matview, function, sequence or index: the full catalogue row (definition
    or source) under `definitions`, plus dependents. Call this before writing queries
    against a table, and before suggesting any DDL.
    """
    source = db(ctx, database)
    if kind == "table":
        result = await source.describe_table(name, schema, detail)
        result["triggers"] = await source.list_triggers(name, schema)
        result["policies"] = await source.list_policies(name, schema)
        result["dependents"] = await source.object_dependencies(name, kind, schema)
        return result
    rows: list[dict[str, object]]
    match kind:
        case "view":
            rows = await source.describe_view(name, schema)
        case "matview":
            rows = await source.describe_matview(name, schema)
        case "function":
            rows = await source.describe_function(name, schema)
        case "sequence":
            rows = await source.describe_sequence(name, schema)
        case "index":
            rows = await source.describe_index(name, schema)
        case _:
            raise ValueError(f"Unknown kind {kind!r}")
    return {
        "object": f"{schema}.{name}",
        "kind": kind,
        "definitions": rows,
        "dependents": await source.object_dependencies(name, kind, schema),
    }


@mcp.tool(annotations=READ_ONLY)
async def find_join_path(
    ctx: Ctx,
    source_table: SourceTable,
    target_table: TargetTable,
    schema: Schema = "public",
    max_depth: MaxDepth = 4,
    database: Database = None,
) -> dict[str, object]:
    """Find how to JOIN two tables that may not be directly related.
    Traverses foreign key relationships and returns all paths from source to target
    with the exact join conditions for each hop. Use this when you need to query
    across tables that are multiple foreign keys apart.
    """
    return await db(ctx, database).find_join_path(source_table, target_table, schema, max_depth)


@mcp.tool(annotations=READ_ONLY, meta=ALWAYS_LOAD)
async def column_profile(
    ctx: Ctx,
    table_name: TableName,
    column_name: ColumnName,
    top_n: TopN = 20,
    schema: Schema = "public",
    database: Database = None,
) -> dict[str, object]:
    """Profile one column. `stats`: null fraction, distinct count, min, max, most common
    values and correlation from ANALYZE statistics, so it is cheap even on big tables.
    `top_values`: the most frequent distinct values with their row counts, from a live
    GROUP BY. Call this before filtering on status, type or category columns so WHERE
    clauses use values that exist, and on numeric or date columns to learn the range.
    """
    source = db(ctx, database)
    return {
        "column": f"{schema}.{table_name}.{column_name}",
        "stats": await source.column_stats(table_name, column_name, schema),
        "top_values": await source.column_values(table_name, column_name, top_n, schema),
    }


@mcp.tool(annotations=READ_ONLY, meta=ALWAYS_LOAD)
async def query(
    ctx: Ctx,
    sql: SQL,
    limit: Limit = 500,
    offset: Offset = 0,
    database: Database = None,
) -> list[dict[str, object]]:
    """Execute a read-only SQL query and return up to 500 rows.
    Use describe_object and column_profile first to ensure correct column names and
    filter values. Sample rows with ORDER BY random() LIMIT 5; search text columns
    with ILIKE '%keyword%'.
    Supports limit/offset for pagination, but large offsets degrade performance.
    For large result sets, prefer keyset pagination in your SQL
    (e.g. WHERE id > last_seen_id ORDER BY id) over high offset values.
    Text cells longer than the server's limit (default 1000 characters) are cut
    and end with "…[truncated, N chars total]"; select a substring or a
    json path when you need the rest.
    """
    return await db(ctx, database).query(sql, limit=limit, offset=offset)


@mcp.tool(annotations=READ_ONLY)
async def explain_query(
    ctx: Ctx,
    sql: SQL,
    analyze: Analyze = False,
    buffers: Buffers = False,
    database: Database = None,
) -> str:
    """Get the EXPLAIN plan for a SQL query.
    Use to check whether a query will use indexes before running it.
    With analyze=True, actually executes the query to show real timings and row counts.
    With buffers=True (requires analyze=True), shows buffer/cache hit statistics.
    """
    return await db(ctx, database).explain_query(sql, analyze=analyze, buffers=buffers)


@mcp.tool(annotations=READ_ONLY)
async def health(
    ctx: Ctx,
    checks: HealthChecks = None,
    schema: Schema = "public",
    limit: HealthLimit = 20,
    database: Database = None,
) -> dict[str, object]:
    """Database health, one key per check. tables: per-table dead-tuple percentage, index
    hit rate, transaction-ID wraparound risk and last vacuum/analyze (PostgreSQL 18 adds
    vacuum/analyze time and the all-frozen fraction). sizes: disk per table including
    indexes. indexes: never-scanned and invalid indexes, which cost writes for nothing.
    sequences: percentage consumed; near 100 means INSERTs will start failing. matviews:
    populated status and whether REFRESH CONCURRENTLY is possible. io: pg_stat_io by
    backend type (PostgreSQL 16+). slow_queries: top statements by total execution time
    from pg_stat_statements, or install instructions when the extension is missing.
    checks=None runs all seven; name one or more to narrow. limit caps the rows per
    check: biggest tables and indexes, most consumed sequences and slowest statements
    first; matviews come by name and io returns every row.
    """
    source = db(ctx, database)
    result: dict[str, object] = {}
    for check in dict.fromkeys(checks or HEALTH_CHECKS):
        match check:
            case "tables":
                result[check] = await source.table_health(schema, limit)
            case "sizes":
                result[check] = await source.table_sizes(schema, limit)
            case "indexes":
                result[check] = await source.unused_indexes(schema, limit)
            case "sequences":
                result[check] = await source.sequence_health(schema, limit)
            case "matviews":
                result[check] = await source.matview_status(schema, limit)
            case "io":
                result[check] = await source.io_stats()
            case "slow_queries":
                result[check] = await source.slow_queries(limit)
    return result


@mcp.tool(annotations=READ_ONLY)
async def activity(
    ctx: Ctx, views: ActivityViews = None, database: Database = None
) -> dict[str, object]:
    """What the server is doing right now, one key per view. sessions: every session
    with its query, state, wait event and durations. locks: blocked sessions and the
    sessions blocking them. maintenance: progress of running VACUUM, ANALYZE, CREATE
    INDEX / REINDEX and CLUSTER / VACUUM FULL. replication: standbys with lag,
    replication slots (an inactive slot retains WAL until the disk fills) and logical
    subscription error and conflict counts. views=None returns all four.
    """
    source = db(ctx, database)
    result: dict[str, object] = {}
    for view in dict.fromkeys(views or ACTIVITY_VIEWS):
        match view:
            case "sessions":
                result[view] = await source.active_queries()
            case "locks":
                result[view] = await source.blocking_locks()
            case "maintenance":
                result[view] = await source.maintenance_progress()
            case "replication":
                result[view] = await source.replication_status()
    return result

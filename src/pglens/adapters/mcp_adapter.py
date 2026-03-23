"""MCP server backed by asyncpg."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from mcp.server.fastmcp import Context, FastMCP

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.core.types import (
    SQL,
    ColumnKeyword,
    ColumnName,
    EnumKeyword,
    ObjectName,
    ObjectType,
    SampleSize,
    Schema,
    SearchKeyword,
    TableName,
    TopN,
)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AsyncpgDatabase]:
    async with asyncpg.create_pool(min_size=1, max_size=5) as pool:
        yield AsyncpgDatabase(pool)


mcp = FastMCP("pglens", lifespan=app_lifespan)


def db(ctx: Context) -> AsyncpgDatabase:
    return ctx.request_context.lifespan_context


@mcp.prompt()
def query_guide() -> str:
    """Suggested workflow for exploring this database."""
    return """\
You are connected to a PostgreSQL database via MCP tools.

Suggested workflow:

1. `list_tables` -- see what exists (names, row counts, descriptions).
2. `describe_table` -- get column names, types, PKs, FKs, indexes.
3. `find_related_tables` -- discover FK relationships for JOINs.
4. `column_values` / `search_enum_values` -- check actual values before
   writing WHERE clauses. Guessing enum values is a common source of
   empty results.
5. `sample_rows` -- see real data, NULL patterns, value formats.
6. `search_columns` -- find a column by name across all tables.
   `search_data` -- find rows matching a keyword across text columns.
7. `explain_query` -- check the query plan before running expensive queries.
8. `query` -- run read-only SQL (capped at 500 rows).

Schema and discovery:
- `list_views` -- views often have complex joins already done.
- `list_extensions` -- check for PostGIS, pg_trgm, etc.
- `list_functions` -- stored functions/procedures and their source code.
- `list_triggers` -- triggers on a table (can silently modify data).
- `list_policies` -- row-level security policies (can silently filter rows).

Safety before DDL changes:
- `object_dependencies` -- what views, functions, constraints depend on an
  object. Always check before DROP or ALTER.

Performance and health:
- `table_stats` -- index hit rates, dead tuples, vacuum timestamps.
- `table_sizes` -- disk usage per table, ranked by size.
- `unused_indexes` -- indexes that are never scanned (wasted disk + write overhead).
- `bloat_stats` -- dead tuples, vacuum status, transaction wraparound risk.
- `active_queries` -- currently running sessions and their queries.
- `blocking_locks` -- lock wait chains (who blocks whom).
- `sequence_health` -- sequences approaching exhaustion.
- `matview_status` -- materialized view freshness and refresh eligibility.

Tips:
- Call describe_table before querying a table for the first time.
- Check enum values instead of guessing.
- Prefer indexed columns in WHERE/JOIN (visible in describe_table).
- Use specific columns instead of SELECT *.
- Call object_dependencies before suggesting DDL changes.
- Check list_policies if queries return fewer rows than expected.
"""


@mcp.tool()
async def list_tables(ctx: Context, schema: Schema = "public") -> list[dict]:
    """List tables in the schema with estimated row counts and table descriptions.
    Good starting point to see what the database contains.
    """
    return await db(ctx).list_tables(schema)


@mcp.tool()
async def list_views(ctx: Context, schema: Schema = "public") -> list[dict]:
    """List views with their SQL definitions.
    Views often contain pre-joined queries that can save you from writing complex joins.
    """
    return await db(ctx).list_views(schema)


@mcp.tool()
async def list_extensions(ctx: Context) -> list[dict]:
    """List installed Postgres extensions and versions.
    Check this before using extension-specific functions like PostGIS or pg_trgm.
    """
    return await db(ctx).list_extensions()


@mcp.tool()
async def describe_table(ctx: Context, table_name: TableName, schema: Schema = "public") -> dict:
    """Get detailed table structure: columns, types, defaults, nullability,
    primary keys, foreign keys, indexes, and check constraints.
    Call this before writing queries against a table to get correct column names and types.
    """
    return await db(ctx).describe_table(table_name, schema)


@mcp.tool()
async def find_related_tables(
    ctx: Context, table_name: TableName, schema: Schema = "public"
) -> dict:
    """Show tables that this table references and tables that reference it via foreign keys.
    Use this to determine correct JOIN conditions between tables.
    """
    return await db(ctx).find_related_tables(table_name, schema)


@mcp.tool()
async def sample_rows(
    ctx: Context,
    table_name: TableName,
    n: SampleSize = 5,
    schema: Schema = "public",
) -> list[dict]:
    """Get random rows from a table to see real data shapes, NULL patterns, and value formats.
    Useful before writing WHERE clauses or aggregations.
    """
    return await db(ctx).sample_rows(table_name, n, schema)


@mcp.tool()
async def column_values(
    ctx: Context,
    table_name: TableName,
    column_name: ColumnName,
    top_n: TopN = 20,
    schema: Schema = "public",
) -> list[dict]:
    """Show the most common distinct values in a column with their row counts.
    Call this before filtering on status, type, category, or other low-cardinality
    columns to use the correct values in WHERE clauses.
    """
    return await db(ctx).column_values(table_name, column_name, top_n, schema)


@mcp.tool()
async def search_data(
    ctx: Context,
    table_name: TableName,
    keyword: SearchKeyword,
    schema: Schema = "public",
) -> list[dict]:
    """Search for a keyword across all text/varchar columns in a table (case-insensitive).
    Returns up to 50 matching rows. Useful when you know a value exists but not which column.
    """
    return await db(ctx).search_data(table_name, keyword, schema)


@mcp.tool()
async def search_columns(
    ctx: Context, keyword: ColumnKeyword, schema: Schema = "public"
) -> list[dict]:
    """Search for columns by name across all tables in the schema.
    Use when you need a field like 'email' or 'created_at' but don't know which table has it.
    """
    return await db(ctx).search_columns(keyword, schema)


@mcp.tool()
async def search_enum_values(ctx: Context, keyword: EnumKeyword = "") -> list[dict]:
    """List Postgres enum types and their allowed values, filtered by keyword.
    Check this before filtering on enum columns to avoid using values that don't exist.
    """
    return await db(ctx).search_enum_values(keyword)


@mcp.tool()
async def table_stats(ctx: Context, schema: Schema = "public") -> list[dict]:
    """Per-table stats: index hit rates, sequential vs index scan counts, dead tuples,
    and last vacuum/analyze timestamps. Useful for understanding query performance.
    """
    return await db(ctx).table_stats(schema)


@mcp.tool()
async def explain_query(ctx: Context, sql: SQL) -> str:
    """Get the EXPLAIN plan for a query without executing it.
    Use to check whether a query will use indexes before running it.
    """
    return await db(ctx).explain_query(sql)


@mcp.tool()
async def query(ctx: Context, sql: SQL) -> list[dict]:
    """Execute a read-only SQL query and return up to 500 rows.
    Use describe_table and column_values first to ensure correct column names and filter values.
    """
    return await db(ctx).query(sql)


@mcp.tool()
async def object_dependencies(
    ctx: Context,
    object_name: ObjectName,
    object_type: ObjectType = "table",
    schema: Schema = "public",
) -> list[dict]:
    """Show all database objects that depend on the given object (views, functions,
    constraints, rules). Call this before any DDL change (DROP, ALTER, rename) to
    understand what will break.
    """
    return await db(ctx).object_dependencies(object_name, object_type, schema)


@mcp.tool()
async def active_queries(ctx: Context) -> list[dict]:
    """Show all current database sessions with their queries, durations, and wait events.
    Use this to see what is running right now and identify long-running or stuck queries.
    """
    return await db(ctx).active_queries()


@mcp.tool()
async def blocking_locks(ctx: Context) -> list[dict]:
    """Show lock wait chains: which sessions are blocked and which sessions are blocking them.
    Use this when queries are hanging or the application reports timeouts.
    """
    return await db(ctx).blocking_locks()


@mcp.tool()
async def table_sizes(ctx: Context, schema: Schema = "public") -> list[dict]:
    """Show disk usage per table: total size (table + toast + indexes), table-only size,
    and index size. Ranked by total size descending. Use to find the largest tables.
    """
    return await db(ctx).table_sizes(schema)


@mcp.tool()
async def unused_indexes(ctx: Context, schema: Schema = "public") -> list[dict]:
    """List indexes that have never been scanned since the last statistics reset.
    Excludes unique and primary key indexes. Each unused index wastes disk space
    and slows down writes.
    """
    return await db(ctx).unused_indexes(schema)


@mcp.tool()
async def bloat_stats(ctx: Context, schema: Schema = "public") -> list[dict]:
    """Per-table bloat indicators: dead tuple count and percentage, transaction ID age,
    wraparound risk percentage, and vacuum timestamps. Tables with high dead_tuple_pct
    need VACUUM; tables with high wraparound_pct need urgent attention.
    """
    return await db(ctx).bloat_stats(schema)


@mcp.tool()
async def list_functions(ctx: Context, schema: Schema = "public") -> list[dict]:
    """List stored functions and procedures with their arguments, return type, language,
    volatility, and source code. Use to understand what business logic lives in the database.
    """
    return await db(ctx).list_functions(schema)


@mcp.tool()
async def list_triggers(
    ctx: Context, table_name: TableName, schema: Schema = "public"
) -> list[dict]:
    """Show triggers on a table: name, full definition, enabled/disabled status, and
    which function they call. Triggers can silently modify data on INSERT/UPDATE/DELETE.
    """
    return await db(ctx).list_triggers(table_name, schema)


@mcp.tool()
async def list_policies(
    ctx: Context, table_name: TableName, schema: Schema = "public"
) -> list[dict]:
    """Show row-level security policies on a table: command scope (SELECT/INSERT/UPDATE/DELETE),
    permissive vs restrictive, USING and WITH CHECK expressions, and applicable roles.
    RLS policies silently filter rows — check these if queries return fewer rows than expected.
    """
    return await db(ctx).list_policies(table_name, schema)


@mcp.tool()
async def sequence_health(ctx: Context, schema: Schema = "public") -> list[dict]:
    """Show all sequences with their current value, min/max, and percentage consumed.
    Sequences approaching max_value will cause hard application failures on INSERT.
    Sorted by consumption percentage descending.
    """
    return await db(ctx).sequence_health(schema)


@mcp.tool()
async def matview_status(ctx: Context, schema: Schema = "public") -> list[dict]:
    """Show materialized views with their populated status, size, definition, and whether
    they have a unique index (required for REFRESH CONCURRENTLY). Stale matviews return
    outdated query results.
    """
    return await db(ctx).matview_status(schema)

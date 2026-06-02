"""Performance and health monitoring tools."""

from pglens.adapters.mcp_adapter import Ctx, db, mcp
from pglens.core.types import Database, Schema


@mcp.tool()
async def table_stats(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """Per-table stats: index hit rates, sequential vs index scan counts, dead tuples,
    and last vacuum/analyze timestamps. Useful for understanding query performance.
    """
    return await db(ctx, database).table_stats(schema)


@mcp.tool()
async def table_sizes(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """Show disk usage per table: total size (table + toast + indexes), table-only size,
    and index size. Ranked by total size descending. Use to find the largest tables.
    """
    return await db(ctx, database).table_sizes(schema)


@mcp.tool()
async def unused_indexes(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """List indexes that have never been scanned since the last statistics reset.
    Excludes unique and primary key indexes. Each unused index wastes disk space
    and slows down writes.
    """
    return await db(ctx, database).unused_indexes(schema)


@mcp.tool()
async def bloat_stats(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """Per-table bloat indicators: dead tuple count and percentage, transaction ID age,
    wraparound risk percentage, and vacuum timestamps. Tables with high dead_tuple_pct
    need VACUUM; tables with high wraparound_pct need urgent attention.
    """
    return await db(ctx, database).bloat_stats(schema)


@mcp.tool()
async def active_queries(ctx: Ctx, database: Database = None) -> list[dict[str, object]]:
    """Show all current database sessions with their queries, durations, and wait events.
    Use this to see what is running right now and identify long-running or stuck queries.
    """
    return await db(ctx, database).active_queries()


@mcp.tool()
async def blocking_locks(ctx: Ctx, database: Database = None) -> list[dict[str, object]]:
    """Show lock wait chains: which sessions are blocked and which sessions are blocking them.
    Use this when queries are hanging or the application reports timeouts.
    """
    return await db(ctx, database).blocking_locks()


@mcp.tool()
async def sequence_health(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """Show all sequences with their current value, min/max, and percentage consumed.
    Sequences approaching max_value will cause hard application failures on INSERT.
    Sorted by consumption percentage descending.
    """
    return await db(ctx, database).sequence_health(schema)


@mcp.tool()
async def matview_status(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """Show materialized views with their populated status, size, definition, and whether
    they have a unique index (required for REFRESH CONCURRENTLY). Stale matviews return
    outdated query results.
    """
    return await db(ctx, database).matview_status(schema)

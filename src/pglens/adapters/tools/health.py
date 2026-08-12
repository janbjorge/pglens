"""Performance and health monitoring tools."""

from pglens.adapters.mcp_adapter import Ctx, db, mcp
from pglens.core.types import Database, Schema, TopQueries


@mcp.tool()
async def table_health(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """Per-table health: index hit rates, sequential vs index scan counts, live/dead tuples
    with dead-tuple percentage, transaction ID age and wraparound risk percentage, and last
    vacuum/analyze timestamps. Tables with high dead_tuple_pct need VACUUM; tables with high
    wraparound_pct need urgent attention. Use for query performance and maintenance triage.
    """
    return await db(ctx, database).table_health(schema)


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
    and slows down writes. is_valid=false marks an invalid index left behind by a
    failed CREATE INDEX CONCURRENTLY. It costs writes and serves no reads, so drop
    or rebuild it.
    """
    return await db(ctx, database).unused_indexes(schema)


@mcp.tool()
async def slow_queries(
    ctx: Ctx, limit: TopQueries = 20, database: Database = None
) -> list[dict[str, object]]:
    """Top SQL statements by total execution time from pg_stat_statements: calls,
    total/mean/stddev execution time, rows, and shared-buffer cache hit percentage.
    Start here for performance investigations: it shows where the database spends
    its time. Requires the pg_stat_statements extension; when the extension is
    missing, returns a single error entry with install instructions.
    """
    return await db(ctx, database).slow_queries(limit)


@mcp.tool()
async def replication_status(ctx: Ctx, database: Database = None) -> dict[str, object]:
    """Show replication health: connected standbys from pg_stat_replication (state,
    sync_state, write/flush/replay lag) and replication slots from pg_replication_slots
    (active flag, wal_status, retained WAL bytes). An inactive slot retains WAL until
    the disk fills. Investigate any slot with active=false or a large
    retained_wal_bytes.
    """
    return await db(ctx, database).replication_status()


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

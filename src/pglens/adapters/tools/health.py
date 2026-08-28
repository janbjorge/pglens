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
    wraparound_pct need urgent attention. On PostgreSQL 18+, also reports cumulative
    (auto)vacuum/(auto)analyze time in ms (including cost-delay sleep) and frozen_pct, the
    fraction of pages all-frozen; a low frozen_pct on a huge table means an expensive
    future anti-wraparound vacuum. Use for query performance and maintenance triage.
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
    its time. On PostgreSQL 18's pg_stat_statements, also reports parallel workers
    planned vs launched and wal_buffers_full per statement. Requires the
    pg_stat_statements extension; when the extension is missing, returns a single
    error entry with install instructions.
    """
    return await db(ctx, database).slow_queries(limit)


@mcp.tool()
async def replication_status(ctx: Ctx, database: Database = None) -> dict[str, object]:
    """Show replication health: connected standbys from pg_stat_replication (state,
    sync_state, write/flush/replay lag) and replication slots from pg_replication_slots
    (active flag, wal_status, retained WAL bytes). An inactive slot retains WAL until
    the disk fills. Investigate any slot with active=false or a large
    retained_wal_bytes. Also reports logical replication subscriptions from
    pg_stat_subscription_stats (apply/sync error counts; on PostgreSQL 18+ per-type
    conflict counts such as confl_insert_exists, where a nonzero count means incoming
    logical changes clashed with local data).
    """
    return await db(ctx, database).replication_status()


@mcp.tool()
async def io_stats(ctx: Ctx, database: Database = None) -> list[dict[str, object]]:
    """Cluster-wide I/O statistics from pg_stat_io (PostgreSQL 16+): reads, writes,
    extends, hits, evictions, reuses, fsyncs and their timings, broken down by
    backend_type, object (relation, temp relation, wal) and context (normal, vacuum,
    bulkread, bulkwrite, init). On PostgreSQL 18+ also includes byte counts
    (read_bytes/write_bytes/extend_bytes) and WAL I/O rows (object='wal'). Timing
    columns are NULL unless track_io_timing is enabled (track_wal_io_timing for WAL).
    Rows with no activity are omitted. Use to find which subsystem (autovacuum,
    checkpointer, client backends) drives disk I/O. On servers older than 16,
    returns a single error entry.
    """
    return await db(ctx, database).io_stats()


@mcp.tool()
async def maintenance_progress(ctx: Ctx, database: Database = None) -> dict[str, object]:
    """Live progress of maintenance operations currently running: VACUUM
    (pg_stat_progress_vacuum), ANALYZE (pg_stat_progress_analyze), CREATE INDEX /
    REINDEX (pg_stat_progress_create_index), and CLUSTER / VACUUM FULL
    (pg_stat_progress_cluster). Each entry has the phase, blocks/tuples done vs
    total with a percentage, and the table/index name. On PostgreSQL 18+ vacuum
    and analyze also report delay_time_ms, cumulative cost-based delay sleep
    (requires track_cost_delay_timing). Empty lists mean nothing is running.
    Use to answer "how far along is this vacuum / index build / cluster".
    """
    return await db(ctx, database).maintenance_progress()


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

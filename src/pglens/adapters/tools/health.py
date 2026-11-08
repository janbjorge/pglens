"""Health and activity tools."""

from pglens.adapters.mcp_adapter import READ_ONLY, Ctx, db, mcp
from pglens.core.types import (
    ActivityView,
    ActivityViews,
    Database,
    HealthCheck,
    HealthChecks,
    HealthLimit,
    Schema,
)

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
    check, worst first.
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

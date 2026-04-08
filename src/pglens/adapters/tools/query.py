"""Query execution tools."""

from pglens.adapters.mcp_adapter import Ctx, db, mcp
from pglens.core.types import SQL, Analyze, Buffers, Limit, Offset


@mcp.tool()
async def explain_query(
    ctx: Ctx,
    sql: SQL,
    analyze: Analyze = False,
    buffers: Buffers = False,
) -> str:
    """Get the EXPLAIN plan for a SQL query.
    Use to check whether a query will use indexes before running it.
    With analyze=True, actually executes the query to show real timings and row counts.
    With buffers=True (requires analyze=True), shows buffer/cache hit statistics.
    """
    return await db(ctx).explain_query(sql, analyze=analyze, buffers=buffers)


@mcp.tool()
async def query(
    ctx: Ctx, sql: SQL, limit: Limit = 500, offset: Offset = 0
) -> list[dict[str, object]]:
    """Execute a read-only SQL query and return up to 500 rows.
    Use describe_table and column_values first to ensure correct column names and filter values.
    Supports limit/offset for pagination, but large offsets degrade performance.
    For large result sets, prefer keyset pagination in your SQL
    (e.g. WHERE id > last_seen_id ORDER BY id) over high offset values.
    """
    return await db(ctx).query(sql, limit=limit, offset=offset)

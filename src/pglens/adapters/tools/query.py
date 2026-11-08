"""Query execution tools."""

from pglens.adapters.mcp_adapter import ALWAYS_LOAD, READ_ONLY, Ctx, db, mcp
from pglens.core.types import SQL, Analyze, Buffers, Database, Limit, Offset


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

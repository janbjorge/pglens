"""Query execution tools."""

from pglens.adapters.mcp_adapter import Ctx, db, mcp
from pglens.core.types import SQL


@mcp.tool()
async def explain_query(ctx: Ctx, sql: SQL) -> str:
    """Get the EXPLAIN plan for a query without executing it.
    Use to check whether a query will use indexes before running it.
    """
    return await db(ctx).explain_query(sql)


@mcp.tool()
async def query(ctx: Ctx, sql: SQL) -> list[dict[str, object]]:
    """Execute a read-only SQL query and return up to 500 rows.
    Use describe_table and column_values first to ensure correct column names and filter values.
    """
    return await db(ctx).query(sql)

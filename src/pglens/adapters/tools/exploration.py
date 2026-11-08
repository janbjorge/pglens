"""Data exploration tools."""

from pglens.adapters.mcp_adapter import ALWAYS_LOAD, READ_ONLY, Ctx, db, mcp
from pglens.core.types import ColumnName, Database, Schema, TableName, TopN


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

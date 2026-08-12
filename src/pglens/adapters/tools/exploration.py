"""Data exploration tools — sample data, column values, search."""

from pglens.adapters.mcp_adapter import Ctx, db, mcp
from pglens.core.types import (
    ColumnKeyword,
    ColumnName,
    Database,
    EnumKeyword,
    SampleSize,
    Schema,
    SearchKeyword,
    TableName,
    TopN,
)


@mcp.tool()
async def sample_rows(
    ctx: Ctx,
    table_name: TableName,
    n: SampleSize = 5,
    schema: Schema = "public",
    database: Database = None,
) -> list[dict[str, object]]:
    """Get random rows from a table to see real data shapes, NULL patterns, and value formats.
    Useful before writing WHERE clauses or aggregations.
    """
    return await db(ctx, database).sample_rows(table_name, n, schema)


@mcp.tool()
async def column_values(
    ctx: Ctx,
    table_name: TableName,
    column_name: ColumnName,
    top_n: TopN = 20,
    schema: Schema = "public",
    database: Database = None,
) -> list[dict[str, object]]:
    """Show the most common distinct values in a column with their row counts.
    Call this before filtering on status, type, category, or other low-cardinality
    columns to use the correct values in WHERE clauses.
    """
    return await db(ctx, database).column_values(table_name, column_name, top_n, schema)


@mcp.tool()
async def column_stats(
    ctx: Ctx,
    table_name: TableName,
    column_name: ColumnName,
    schema: Schema = "public",
    database: Database = None,
) -> dict[str, object]:
    """Get statistics for a column: min, max, null fraction, distinct count,
    most common values, and correlation. Uses data already collected by ANALYZE
    so it is essentially free. Use this for numeric or date columns where
    column_values would return too many distinct values.
    """
    return await db(ctx, database).column_stats(table_name, column_name, schema)


@mcp.tool()
async def search_data(
    ctx: Ctx,
    table_name: TableName,
    keyword: SearchKeyword,
    schema: Schema = "public",
    database: Database = None,
) -> list[dict[str, object]]:
    """Search for a keyword across all text/varchar columns in a table (case-insensitive).
    Returns up to 50 matching rows. Useful when you know a value exists but not which column.
    """
    return await db(ctx, database).search_data(table_name, keyword, schema)


@mcp.tool()
async def search_columns(
    ctx: Ctx,
    keyword: ColumnKeyword,
    schema: Schema = "public",
    database: Database = None,
) -> list[dict[str, object]]:
    """Search for columns by name across all tables in the schema.
    Use when you need a field like 'email' or 'created_at' but don't know which table has it.
    """
    return await db(ctx, database).search_columns(keyword, schema)


@mcp.tool()
async def search_enum_values(
    ctx: Ctx, keyword: EnumKeyword = "", database: Database = None
) -> list[dict[str, object]]:
    """List Postgres enum types and their allowed values, filtered by keyword.
    Check this before filtering on enum columns to avoid using values that don't exist.
    """
    return await db(ctx, database).search_enum_values(keyword)

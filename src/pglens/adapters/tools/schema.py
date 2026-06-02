"""Schema and discovery tools."""

from pglens.adapters.mcp_adapter import Ctx, databases, db, mcp
from pglens.core.types import (
    Database,
    MaxDepth,
    Schema,
    SourceTable,
    TableName,
    TargetTable,
)


@mcp.tool()
async def list_databases(ctx: Ctx) -> list[str]:
    """List configured database aliases that can be passed as the `database` argument.
    Aliases come from PGLENS_DATABASES (comma-separated dbnames sharing libpq env
    credentials, e.g. `PGLENS_DATABASES=app,azure_sys`). With no PGLENS_DATABASES set,
    a single 'default' alias relies on PGDATABASE. Use this to discover targets such
    as Azure's `azure_sys` system-metric database.
    """
    return databases(ctx).names()


@mcp.tool()
async def database_info(ctx: Ctx, database: Database = None) -> dict[str, object]:
    """Get PostgreSQL server and database identity: version, database name, current user,
    encoding, timezone, max_connections, uptime, and total database size.
    Call this first when you need to know the Postgres version (to use version-appropriate
    SQL syntax), verify which database you are connected to, or check server capacity.
    """
    return await db(ctx, database).database_info()


@mcp.tool()
async def list_schemas(ctx: Ctx, database: Database = None) -> list[dict[str, object]]:
    """List all user-visible schemas with table and view counts.
    Call this first to discover non-public schemas before exploring tables.
    """
    return await db(ctx, database).list_schemas()


@mcp.tool()
async def list_tables(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """List tables in the schema with estimated row counts and table descriptions.
    Good starting point to see what the database contains.
    """
    return await db(ctx, database).list_tables(schema)


@mcp.tool()
async def list_views(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """List views with their SQL definitions.
    Views often contain pre-joined queries that can save you from writing complex joins.
    """
    return await db(ctx, database).list_views(schema)


@mcp.tool()
async def list_extensions(ctx: Ctx, database: Database = None) -> list[dict[str, object]]:
    """List installed Postgres extensions and versions.
    Check this before using extension-specific functions like PostGIS or pg_trgm.
    """
    return await db(ctx, database).list_extensions()


@mcp.tool()
async def describe_table(
    ctx: Ctx,
    table_name: TableName,
    schema: Schema = "public",
    database: Database = None,
) -> dict[str, object]:
    """Get detailed table structure: columns, types, defaults, nullability,
    primary keys, foreign keys, indexes, and check constraints.
    Call this before writing queries against a table to get correct column names and types.
    """
    return await db(ctx, database).describe_table(table_name, schema)


@mcp.tool()
async def find_related_tables(
    ctx: Ctx,
    table_name: TableName,
    schema: Schema = "public",
    database: Database = None,
) -> dict[str, object]:
    """Show tables that this table references and tables that reference it via foreign keys.
    Use this to determine correct JOIN conditions between tables.
    """
    return await db(ctx, database).find_related_tables(table_name, schema)


@mcp.tool()
async def find_join_path(
    ctx: Ctx,
    source_table: SourceTable,
    target_table: TargetTable,
    schema: Schema = "public",
    max_depth: MaxDepth = 4,
    database: Database = None,
) -> dict[str, object]:
    """Find how to JOIN two tables that may not be directly related.
    Traverses foreign key relationships and returns all paths from source to target
    with the exact join conditions for each hop. Use this when you need to query
    across tables that are multiple foreign keys apart.
    """
    return await db(ctx, database).find_join_path(source_table, target_table, schema, max_depth)


@mcp.tool()
async def list_indexes(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """List all indexes across every table in the schema: name, table, type (btree/hash/gin/gist),
    uniqueness, full CREATE INDEX definition, size on disk, and usage stats (scans, tuples read).
    Use this to audit indexing strategy across the whole schema — unlike describe_table which
    shows indexes for one table, this gives you the big picture. Combine with unused_indexes
    to find waste, or with explain_query to verify a query uses the index you expect.
    """
    return await db(ctx, database).list_indexes(schema)


@mcp.tool()
async def list_functions(
    ctx: Ctx, schema: Schema = "public", database: Database = None
) -> list[dict[str, object]]:
    """List stored functions and procedures with their arguments, return type, language,
    volatility, and source code. Use to understand what business logic lives in the database.
    """
    return await db(ctx, database).list_functions(schema)


@mcp.tool()
async def list_triggers(
    ctx: Ctx,
    table_name: TableName,
    schema: Schema = "public",
    database: Database = None,
) -> list[dict[str, object]]:
    """Show triggers on a table: name, full definition, enabled/disabled status, and
    which function they call. Triggers can silently modify data on INSERT/UPDATE/DELETE.
    """
    return await db(ctx, database).list_triggers(table_name, schema)


@mcp.tool()
async def list_policies(
    ctx: Ctx,
    table_name: TableName,
    schema: Schema = "public",
    database: Database = None,
) -> list[dict[str, object]]:
    """Show row-level security policies on a table: command scope (SELECT/INSERT/UPDATE/DELETE),
    permissive vs restrictive, USING and WITH CHECK expressions, and applicable roles.
    RLS policies silently filter rows — check these if queries return fewer rows than expected.
    """
    return await db(ctx, database).list_policies(table_name, schema)

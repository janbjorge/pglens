"""Schema and discovery tools."""

from pglens.adapters.asyncpg_adapter import select_detail
from pglens.adapters.mcp_adapter import ALWAYS_LOAD, READ_ONLY, Ctx, databases, db, mcp
from pglens.core.types import (
    Database,
    Detail,
    ListLimit,
    MaxDepth,
    NamePattern,
    ObjectKind,
    ObjectName,
    ObjectType,
    Schema,
    SourceTable,
    TargetTable,
)

# A name filter is applied after the fetch, so a filtered listing reads a whole
# catalogue page first. This is the adapter's upper bound on limit.
CATALOGUE_PAGE = 1000


@mcp.tool(annotations=READ_ONLY)
async def database_info(ctx: Ctx, database: Database = None) -> dict[str, object]:
    """Get PostgreSQL server and database identity: version, database name, current user,
    encoding, timezone, max_connections, uptime, total database size, the schemas with
    their table and view counts (`schemas`), and the configured database aliases
    (`available_databases`) that can be passed as the `database` argument on any tool.
    The first alias is the primary (PGDATABASE); extras come from PGLENS_DATABASES.
    Call this first when you need the Postgres version, want to verify which database
    you are connected to, or suspect the data lives outside the public schema.
    """
    source = db(ctx, database)
    info = await source.database_info()
    info["schemas"] = await source.list_schemas()
    info["available_databases"] = databases(ctx).names()
    return info


@mcp.tool(annotations=READ_ONLY, meta=ALWAYS_LOAD)
async def list_objects(
    ctx: Ctx,
    kind: ObjectKind,
    schema: Schema = "public",
    pattern: NamePattern = None,
    detail: Detail = "summary",
    limit: ListLimit = 100,
    database: Database = None,
) -> list[dict[str, object]]:
    """List objects of one kind in a schema. table: estimated row count and description.
    view and matview: description, populated status and size (definition with
    detail='full'). index: table, type, uniqueness, validity, size and scan counts
    (CREATE INDEX text with detail='full'). function: arguments, return type, language and
    volatility (source with detail='full'). sequence: current value and percentage
    consumed. enum: the allowed values. extension: installed extensions and versions.
    column: every column whose name contains pattern, across all tables in the schema,
    with its type. pattern is a case-insensitive substring on the object name. Start with
    kind='table' to see what the database contains.
    """
    source = db(ctx, database)
    page = CATALOGUE_PAGE if pattern else limit
    rows: list[dict[str, object]]
    name_keys: tuple[str, ...]
    match kind:
        case "table":
            rows, name_keys = await source.list_tables(schema, page), ("table_name",)
        case "view":
            rows, name_keys = await source.list_views(schema, page, detail), ("view_name",)
        case "matview":
            rows = await source.matview_status(schema, page, detail)
            name_keys = ("matview_name",)
        case "index":
            rows = await source.list_indexes(schema, page, detail)
            name_keys = ("index_name", "table_name")
        case "function":
            rows = await source.list_functions(schema, page, detail)
            name_keys = ("function_name",)
        case "sequence":
            rows, name_keys = await source.sequence_health(schema, page), ("sequence_name",)
        case "enum":
            rows, name_keys = await source.search_enum_values(pattern or ""), ("enum_name",)
        case "extension":
            rows, name_keys = await source.list_extensions(), ("extname",)
        case "column":
            rows = await source.search_columns(pattern or "", schema, limit)
            name_keys = ("table_name", "column_name")
        case _:
            raise ValueError(f"Unknown kind {kind!r}")
    if pattern and kind not in ("enum", "column"):
        needle = pattern.lower()
        rows = [row for row in rows if needle in str(row[name_keys[0]]).lower()]
    return select_detail(rows[:limit], detail, names=name_keys, heavy=())


@mcp.tool(annotations=READ_ONLY, meta=ALWAYS_LOAD)
async def describe_object(
    ctx: Ctx,
    name: ObjectName,
    kind: ObjectType = "table",
    schema: Schema = "public",
    detail: Detail = "summary",
    database: Database = None,
) -> dict[str, object]:
    """Describe one object. For a table (the default kind): columns with types, defaults
    and nullability, primary keys, foreign keys in both directions, indexes, check
    constraints, triggers, row-level security policies, and dependents (the views,
    functions and constraints that would break on DROP or ALTER). detail='full' adds the
    CREATE INDEX text; detail='names' returns only column names and primary keys.
    For view, matview, function, sequence or index: the full catalogue row (definition
    or source) under `definitions`, plus dependents. Call this before writing queries
    against a table, and before suggesting any DDL.
    """
    source = db(ctx, database)
    if kind == "table":
        result = await source.describe_table(name, schema, detail)
        result["triggers"] = await source.list_triggers(name, schema)
        result["policies"] = await source.list_policies(name, schema)
        result["dependents"] = await source.object_dependencies(name, kind, schema)
        return result
    rows: list[dict[str, object]]
    match kind:
        case "view":
            rows, key = await source.list_views(schema, CATALOGUE_PAGE, "full"), "view_name"
        case "matview":
            rows = await source.matview_status(schema, CATALOGUE_PAGE, "full")
            key = "matview_name"
        case "function":
            rows = await source.list_functions(schema, CATALOGUE_PAGE, "full")
            key = "function_name"
        case "sequence":
            rows, key = await source.sequence_health(schema, CATALOGUE_PAGE), "sequence_name"
        case "index":
            rows, key = await source.list_indexes(schema, CATALOGUE_PAGE, "full"), "index_name"
        case _:
            raise ValueError(f"Unknown kind {kind!r}")
    return {
        "object": f"{schema}.{name}",
        "kind": kind,
        "definitions": [row for row in rows if row[key] == name],
        "dependents": await source.object_dependencies(name, kind, schema),
    }


@mcp.tool(annotations=READ_ONLY)
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

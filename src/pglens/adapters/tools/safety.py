"""Safety tools — dependency analysis before DDL changes."""

from pglens.adapters.mcp_adapter import Ctx, db, mcp
from pglens.core.types import ObjectName, ObjectType, Schema


@mcp.tool()
async def object_dependencies(
    ctx: Ctx,
    object_name: ObjectName,
    object_type: ObjectType = "table",
    schema: Schema = "public",
) -> list[dict[str, object]]:
    """Show all database objects that depend on the given object (views, functions,
    constraints, rules). Call this before any DDL change (DROP, ALTER, rename) to
    understand what will break.
    """
    return await db(ctx).object_dependencies(object_name, object_type, schema)

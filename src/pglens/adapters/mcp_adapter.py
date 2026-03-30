"""MCP server backed by asyncpg."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.core.settings import Settings

Ctx = Context[ServerSession, AsyncpgDatabase, object]


mcp = FastMCP("pglens")


def configure(settings: Settings) -> None:
    """Bind a Settings instance to the MCP server lifespan. Call before mcp.run()."""

    @asynccontextmanager
    async def app_lifespan(server: FastMCP) -> AsyncIterator[AsyncpgDatabase]:
        async with asyncpg.create_pool(dsn=settings.dsn, min_size=1, max_size=5) as pool:
            yield AsyncpgDatabase(pool, settings=settings)

    mcp.settings.lifespan = app_lifespan


def db(ctx: Ctx) -> AsyncpgDatabase:
    lifespan_context: AsyncpgDatabase = ctx.request_context.lifespan_context
    return lifespan_context


@mcp.prompt()
def query_guide() -> str:
    """Suggested workflow for exploring this database."""
    return """\
You are connected to a PostgreSQL database via MCP tools.

Suggested workflow:

1. `list_schemas` -- discover what schemas exist (not just public).
2. `list_tables` -- see what exists (names, row counts, descriptions).
3. `describe_table` -- get column names, types, PKs, FKs, indexes.
4. `find_related_tables` -- discover direct FK relationships.
   `find_join_path` -- find how to JOIN two tables that are multiple
   FKs apart, with exact join conditions for each hop.
5. `column_values` -- check actual values in low-cardinality columns
   (status, type, category) before writing WHERE clauses.
   `column_stats` -- get min/max/nulls/distribution for numeric or
   date columns where column_values would return too many values.
   `search_enum_values` -- check enum values before filtering.
6. `sample_rows` -- see real data, NULL patterns, value formats.
7. `search_columns` -- find a column by name across all tables.
   `search_data` -- find rows matching a keyword across text columns.
8. `explain_query` -- check the query plan before running expensive queries.
9. `query` -- run read-only SQL (capped at 500 rows).

Schema and discovery:
- `list_views` -- views often have complex joins already done.
- `list_extensions` -- check for PostGIS, pg_trgm, etc.
- `list_functions` -- stored functions/procedures and their source code.
- `list_triggers` -- triggers on a table (can silently modify data).
- `list_policies` -- row-level security policies (can silently filter rows).

Safety before DDL changes:
- `object_dependencies` -- what views, functions, constraints depend on an
  object. Always check before DROP or ALTER.

Performance and health:
- `table_stats` -- index hit rates, dead tuples, vacuum timestamps.
- `table_sizes` -- disk usage per table, ranked by size.
- `unused_indexes` -- indexes that are never scanned (wasted disk + write overhead).
- `bloat_stats` -- dead tuples, vacuum status, transaction wraparound risk.
- `active_queries` -- currently running sessions and their queries.
- `blocking_locks` -- lock wait chains (who blocks whom).
- `sequence_health` -- sequences approaching exhaustion.
- `matview_status` -- materialized view freshness and refresh eligibility.

Tips:
- Call list_schemas first if you suspect non-public schemas.
- Call describe_table before querying a table for the first time.
- Use find_join_path when you need to join tables that aren't directly related.
- Check enum values and column_stats instead of guessing.
- Prefer indexed columns in WHERE/JOIN (visible in describe_table).
- Use specific columns instead of SELECT *.
- Call object_dependencies before suggesting DDL changes.
- Check list_policies if queries return fewer rows than expected.
"""


# Register tool modules — each module decorates tools onto `mcp` at import time.
import pglens.adapters.tools.exploration as _exploration  # noqa: E402, F401
import pglens.adapters.tools.health as _health  # noqa: E402, F401
import pglens.adapters.tools.query as _query  # noqa: E402, F401
import pglens.adapters.tools.safety as _safety  # noqa: E402, F401
import pglens.adapters.tools.schema as _schema  # noqa: E402, F401

"""MCP server backed by asyncpg."""

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import asyncpg
from mcp.server.mcpserver import Context, MCPServer

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase

DEFAULT_DB = "default"


@dataclass
class Databases:
    """Registry of named asyncpg-backed databases."""

    databases: dict[str, AsyncpgDatabase]
    default_alias: str = DEFAULT_DB

    def get(self, name: str | None) -> AsyncpgDatabase:
        key = name or self.default_alias
        if key not in self.databases:
            available = ", ".join(sorted(self.databases)) or "<none>"
            raise ValueError(
                f"Unknown database alias '{key}'. Configured: {available}. "
                f"Add via PGLENS_DATABASES env var."
            )
        return self.databases[key]

    def names(self) -> list[str]:
        return sorted(self.databases)


Ctx = Context[Databases, object]


def _collect_databases() -> tuple[list[str], str]:
    """Resolve configured aliases and default alias from env.

    Configuration uses libpq env vars only (no connection strings):

    - ``PGDATABASE`` is the primary alias and the default target when a tool
      is called without ``database=``. ``PGHOST``, ``PGUSER``, ``PGPASSWORD``,
      ``PGSSLMODE`` (etc.) supply host and credentials shared by every pool.
    - ``PGLENS_DATABASES=a,b,c`` lists additional dbnames on the same host
      that get their own pool/alias. Entries equal to ``PGDATABASE`` are
      deduplicated.

    If neither ``PGDATABASE`` nor ``PGLENS_DATABASES`` is set, a single
    ``default`` alias is configured that relies entirely on libpq's own
    default behavior (e.g. dbname = ``PGUSER``).

    Names are used verbatim as Postgres dbnames, which are case-sensitive.
    """
    primary = os.environ.get("PGDATABASE", "").strip() or None

    extras: list[str] = []
    raw = os.environ.get("PGLENS_DATABASES", "").strip()
    if raw:
        for part in raw.split(","):
            name = part.strip()
            if name and name not in extras and name != primary:
                extras.append(name)

    if primary is not None:
        return [primary, *extras], primary
    if extras:
        return extras, extras[0]
    return [DEFAULT_DB], DEFAULT_DB


def _statement_timeout_ms() -> int:
    """Statement timeout from PGLENS_STATEMENT_TIMEOUT (seconds).

    Defaults to 60 seconds; 0 disables the timeout.
    """
    raw = os.environ.get("PGLENS_STATEMENT_TIMEOUT", "").strip()
    if not raw:
        return 60_000
    try:
        seconds = int(raw)
    except ValueError:
        raise ValueError(
            f"PGLENS_STATEMENT_TIMEOUT must be an integer number of seconds, got {raw!r}"
        ) from None
    if seconds < 0:
        raise ValueError(f"PGLENS_STATEMENT_TIMEOUT must be >= 0, got {seconds}")
    return seconds * 1000


def _server_settings() -> dict[str, str]:
    return {
        "application_name": "pglens",
        # Belt and braces: methods also use explicit readonly transactions,
        # but this makes every connection read-only by default.
        "default_transaction_read_only": "on",
        # 0 means disabled, matching Postgres semantics.
        "statement_timeout": str(_statement_timeout_ms()),
    }


@asynccontextmanager
async def app_lifespan(_server: MCPServer[Databases]) -> AsyncIterator[Databases]:
    names, default_alias = _collect_databases()
    server_settings = _server_settings()
    async with AsyncExitStack() as stack:
        databases: dict[str, AsyncpgDatabase] = {}
        for name in names:
            # When alias is the synthetic 'default' (no PGLENS_DATABASES set),
            # let libpq env (including PGDATABASE) decide the dbname.
            dbname = None if name == DEFAULT_DB and len(names) == 1 else name
            pool = await stack.enter_async_context(
                asyncpg.create_pool(
                    database=dbname,
                    min_size=1,
                    max_size=5,
                    server_settings=server_settings,
                )
            )
            databases[name] = AsyncpgDatabase(pool)
        yield Databases(databases, default_alias)


mcp = MCPServer("pglens", lifespan=app_lifespan)


def db(ctx: Ctx, database: str | None = None) -> AsyncpgDatabase:
    registry: Databases = ctx.request_context.lifespan_context
    return registry.get(database)


def databases(ctx: Ctx) -> Databases:
    registry: Databases = ctx.request_context.lifespan_context
    return registry


@mcp.prompt()
def query_guide() -> str:
    """Suggested workflow for exploring this database."""
    return """\
You are connected to a PostgreSQL database via MCP tools.

Suggested workflow:

1. `database_info` -- check Postgres version, database name, current user.
2. `list_schemas` -- discover what schemas exist (not just public).
3. `list_tables` -- see what exists (names, estimated row counts, descriptions).
4. `describe_table` -- get column names, types, PKs, FKs, indexes.
5. `find_related_tables` -- discover direct FK relationships.
   `find_join_path` -- find how to JOIN two tables that are multiple
   FKs apart, with exact join conditions for each hop.
6. `column_values` -- check actual values in low-cardinality columns
   (status, type, category) before writing WHERE clauses.
   `column_stats` -- get min/max/nulls/distribution for numeric or
   date columns where column_values would return too many values.
   `search_enum_values` -- check enum values before filtering.
7. `sample_rows` -- see real data, NULL patterns, value formats.
8. `search_columns` -- find a column by name across all tables.
   `search_data` -- find rows matching a keyword across text columns.
9. `explain_query` -- check the query plan before running expensive queries.
10. `query` -- run read-only SQL (capped at 500 rows).

Schema and discovery:
- `list_views` -- views often have complex joins already done.
- `list_extensions` -- check for PostGIS, pg_trgm, etc.
- `list_indexes` -- all indexes across the schema with types, sizes, usage stats.
- `list_functions` -- stored functions/procedures and their source code.
- `list_triggers` -- triggers on a table (can silently modify data).
- `list_policies` -- row-level security policies (can silently filter rows).

Data validation:
- `table_row_counts` -- exact row count via COUNT(*) when estimates aren't enough.

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

Multi-database:
- `list_databases` -- list configured database aliases. Pass the alias as the
  `database` argument on any tool to target it (e.g. `database='azure_sys'`
  to read Azure system metrics). Default targets the primary alias.

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

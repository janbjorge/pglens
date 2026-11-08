"""MCP server backed by asyncpg."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import asyncpg
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.core.settings import DEFAULT_DB, Settings


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


@asynccontextmanager
async def app_lifespan(_server: MCPServer[Databases]) -> AsyncIterator[Databases]:
    # The single place the environment is read; everything downstream takes
    # what it needs from this instance.
    settings = Settings()
    aliases = settings.database_aliases
    pool_options = settings.pool_options
    async with AsyncExitStack() as stack:
        databases: dict[str, AsyncpgDatabase] = {}
        for name in aliases.names:
            # When alias is the synthetic 'default' (no PGLENS_DATABASES set),
            # let libpq env (including PGDATABASE) decide the dbname.
            dbname = None if name == DEFAULT_DB and len(aliases.names) == 1 else name
            pool = await stack.enter_async_context(
                asyncpg.create_pool(
                    database=dbname,
                    min_size=1,
                    max_size=5,
                    server_settings=pool_options.server_settings,
                    command_timeout=pool_options.command_timeout,
                )
            )
            databases[name] = AsyncpgDatabase(pool, max_cell_chars=settings.max_cell_chars)
        yield Databases(databases, aliases.default)


# Sent to the client as server instructions on connect and returned by the
# query_guide prompt. Clients cut instructions at 4,096 characters, so the
# important part comes first.
INSTRUCTIONS = """\
pglens gives read-only access to one PostgreSQL database: schema, data, query plans,
performance and current activity. It cannot write or run DDL. Use its tools for any
question about this database's tables, columns, values, relationships, query plans,
slow queries, locks, replication or maintenance, instead of guessing from application code.

Workflow for a data question:
1. `database_info` for the PostgreSQL version, the database name and the configured aliases.
2. `list_tables` to see what exists; `list_schemas` first when the data may live outside
   public.
3. `describe_table` for column names, types, primary and foreign keys and indexes before
   writing any query against a table.
4. `column_values` before filtering on status, type or category columns, so WHERE clauses
   use values that exist. `column_stats` for numeric and date columns.
5. `query` for read-only SQL, capped at 500 rows. `explain_query` first when a query may be
   expensive.

SQL idioms for `query` (inline literal values; $1 placeholders are rejected):
- random sample: SELECT col_a, col_b FROM t ORDER BY random() LIMIT 5
- distinct values with counts: SELECT status, count(*) FROM t GROUP BY 1 ORDER BY 2 DESC
  LIMIT 20
- exact row count: SELECT count(*) FROM t (list_tables shows estimates that can be stale)
- large results: keyset pagination, WHERE id > last_seen_id ORDER BY id LIMIT 500, not a
  growing OFFSET

Other tools: `find_join_path` for tables several foreign keys apart; `sample_rows`,
`search_data`, `search_columns` and `search_enum_values` for exploration; `list_views`,
`list_indexes`, `list_functions`, `list_triggers`, `list_policies` and `list_extensions`
for schema detail; `object_dependencies` before suggesting any DROP or ALTER;
`slow_queries`, `table_health`, `table_sizes`, `unused_indexes`, `sequence_health`,
`matview_status` and `io_stats` for health; `active_queries`, `blocking_locks`,
`maintenance_progress` and `replication_status` for what is happening right now.

Multiple databases: `database_info` returns available_databases, the configured aliases.
Pass one of them as the database argument on any tool to target it; the default is the
primary alias.

Row-level security can hide rows: check `list_policies` when a query returns fewer rows
than expected.
"""

mcp = MCPServer("pglens", instructions=INSTRUCTIONS, lifespan=app_lifespan)

# Every pglens tool reads and never writes. Clients that honor these hints
# run read-only calls in parallel or skip confirmation prompts; the read-only
# transactions in AsyncpgDatabase are the enforcement.
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

# Claude Code defers MCP tool definitions behind tool search and loads only
# the tools carrying this meta key at session start. Keep the set at three
# to five tools; every one costs context in every session.
ALWAYS_LOAD = {"anthropic/alwaysLoad": True}


def db(ctx: Ctx, database: str | None = None) -> AsyncpgDatabase:
    registry: Databases = ctx.request_context.lifespan_context
    return registry.get(database)


def databases(ctx: Ctx) -> Databases:
    registry: Databases = ctx.request_context.lifespan_context
    return registry


@mcp.prompt()
def query_guide() -> str:
    """Suggested workflow for exploring this database."""
    return INSTRUCTIONS


# Register tool modules — each module decorates tools onto `mcp` at import time.
import pglens.adapters.tools.exploration as _exploration  # noqa: E402, F401
import pglens.adapters.tools.health as _health  # noqa: E402, F401
import pglens.adapters.tools.query as _query  # noqa: E402, F401
import pglens.adapters.tools.safety as _safety  # noqa: E402, F401
import pglens.adapters.tools.schema as _schema  # noqa: E402, F401

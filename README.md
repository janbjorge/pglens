# pglens

Read-only PostgreSQL introspection for AI agents — 28 MCP tools for schema discovery, data exploration, query execution, and health monitoring. Pure `pg_catalog`, no extensions required.

## Why pglens

Most Postgres MCP servers expose `query` and `list_tables`, and little else. Agents end up guessing column names, enum values, and join paths, burning several failed attempts before landing on working SQL.

pglens closes those gaps: it lets the agent check what values actually exist in a column, discover foreign-key relationships, preview sample data, and validate query plans — so it can look before it leaps.

> **`column_values` in particular:** agents frequently write `WHERE status = 'active'` when the real value is `'Active'` or `'enabled'`. `column_values` returns the actual distinct values with counts, so the agent picks the right one instead of guessing.

## How it works

```
AI agent (MCP client)  ──►  pglens  ──►  your PostgreSQL
   Claude, etc.            MCP server      (read-only)
```

1. Your MCP client (Claude Desktop, Claude Code, Zed, …) launches `pglens` and connects over MCP.
2. pglens connects to PostgreSQL using standard libpq environment variables and opens a connection pool.
3. The agent calls pglens tools to introspect the schema, sample data, run read-only queries, and inspect health, instead of guessing.

Every query runs inside a `readonly=True` transaction, identifiers are escaped with PostgreSQL's `quote_ident()`, and no DDL tools are exposed. All introspection uses `pg_catalog` directly, so no PostgreSQL extensions are needed. See [Safety](#safety).

## Tools

### Schema and discovery

| Tool | What it does |
|---|---|
| `list_databases` | List configured database aliases (e.g. `default`, `azure_sys`) |
| `database_info` | Server version, database name, current user, encoding, timezone, uptime, size |
| `list_schemas` | Schemas with table and view counts |
| `list_tables` | Tables with row counts and descriptions |
| `list_views` | Views with their SQL definitions |
| `list_extensions` | Installed extensions and versions |
| `describe_table` | Columns, types, PKs, FKs, indexes, check constraints |
| `find_related_tables` | FK relationships in both directions |
| `find_join_path` | Multi-hop join paths between two tables via foreign keys |
| `list_indexes` | All indexes across a schema with types, sizes, and usage stats |
| `list_functions` | Stored functions/procedures with source code |
| `list_triggers` | Triggers on a table with definitions and status |
| `list_policies` | Row-level security policies on a table |

### Data exploration

| Tool | What it does |
|---|---|
| `table_row_counts` | Exact row count via COUNT(*) (vs estimated in list_tables) |
| `sample_rows` | Random rows from a table |
| `column_values` | Distinct values with frequency counts |
| `column_stats` | Min, max, null fraction, distinct count, common values |
| `search_data` | Case-insensitive search across text columns |
| `search_columns` | Find columns by name across all tables |
| `search_enum_values` | Enum types and their allowed values |

### Query execution

| Tool | What it does |
|---|---|
| `explain_query` | Query plan without execution |
| `query` | Read-only SQL with limit/offset pagination (default 500 rows) |

### Performance and health

| Tool | What it does |
|---|---|
| `table_stats` | Index hit rates, dead tuples, vacuum timestamps |
| `table_sizes` | Disk usage per table, ranked by size |
| `unused_indexes` | Indexes that are never scanned |
| `bloat_stats` | Dead tuples, vacuum status, wraparound risk |
| `active_queries` | Currently running sessions and their queries |
| `blocking_locks` | Lock wait chains (who blocks whom) |
| `sequence_health` | Sequences approaching exhaustion |
| `matview_status` | Materialized view freshness and refresh eligibility |

### Safety before DDL

| Tool | What it does |
|---|---|
| `object_dependencies` | What depends on a given object (views, functions, constraints) |

There is also a `query_guide` prompt that describes a reasonable workflow for using these tools together.

## Installation

### uvx (recommended)

[uvx](https://docs.astral.sh/uv/) runs pglens straight from PyPI with no install step, always fetching the latest published version:

```bash
uvx pglens
```

There is nothing to upgrade; each launch resolves the newest release. Pin a version when you need: `uvx pglens@0.4.0`.

### pip

```bash
pip install pglens            # install
pip install --upgrade pglens  # upgrade later
```

**Requirements:** Python 3.11+ and a reachable PostgreSQL server.

## Configuration

pglens needs two things: PostgreSQL connection details (via libpq env vars) and an entry in your MCP client. A minimal Claude Desktop config:

```json
{
  "mcpServers": {
    "pglens": {
      "command": "uvx",
      "args": ["pglens"],
      "env": {
        "PGHOST": "localhost",
        "PGPORT": "5432",
        "PGUSER": "myuser",
        "PGPASSWORD": "mypassword",
        "PGDATABASE": "mydb"
      }
    }
  }
}
```

### Connecting to PostgreSQL

pglens reads standard PostgreSQL environment variables (libpq). Connection strings (DSNs) are not supported. Credentials live entirely in `PG*` env vars, so they never appear in arguments or command lines.

| Variable | Required | Purpose |
|---|---|---|
| `PGHOST` | yes | Postgres host |
| `PGPORT` | no (default 5432) | Postgres port |
| `PGUSER` | yes | Username |
| `PGPASSWORD` | yes (or `PGPASSFILE`) | Password |
| `PGDATABASE` | recommended | Primary dbname; also the default alias when a tool is called without `database=` |
| `PGSSLMODE` | no | `disable`, `prefer`, `require`, `verify-ca`, `verify-full` |
| `PGSERVICE`, `PGPASSFILE`, `PGAPPNAME`, … | no | Other libpq env vars, honored by asyncpg automatically |
| `PGLENS_DATABASES` | no | Comma-separated extra dbnames on the same host (see [Multiple databases](#multiple-databases)) |

No connection-string env vars are read. Configuration is libpq env vars only.

To run pglens directly from a shell (e.g. for testing):

```bash
export PGHOST=localhost
export PGUSER=myuser
export PGPASSWORD=mypassword
export PGDATABASE=mydb
uvx pglens
```

Check the installed version with `pglens --version`.

### MCP clients

If you installed pglens with `pip` instead of using `uvx`, replace `"command": "uvx", "args": ["pglens"]` with `"command": "pglens"` in any of the configs below.

#### Claude Desktop

```json
{
  "mcpServers": {
    "pglens": {
      "command": "uvx",
      "args": ["pglens"],
      "env": {
        "PGHOST": "localhost",
        "PGPORT": "5432",
        "PGUSER": "myuser",
        "PGPASSWORD": "mypassword",
        "PGDATABASE": "mydb"
      }
    }
  }
}
```

#### Claude Code

```json
{
  "mcpServers": {
    "pglens": {
      "command": "uvx",
      "args": ["pglens"],
      "env": {
        "PGHOST": "localhost",
        "PGDATABASE": "mydb"
      }
    }
  }
}
```

#### Zed

```json
{
  "context_servers": {
    "pglens": {
      "command": {
        "path": "uvx",
        "args": ["pglens"]
      }
    }
  }
}
```

### Multiple databases

Every tool accepts an optional `database` argument to target an alternate connection. This is useful for Postgres setups that expose system metrics in a separate database — for example, Azure Database for PostgreSQL Flexible Server keeps server metrics in `azure_sys`.

`PGDATABASE` is the primary alias and the default target when a tool is called without `database=`. List any additional dbnames on the same host in `PGLENS_DATABASES`; each becomes its own alias with its own pool. Host, user, password, and TLS mode come from the standard libpq env vars and are shared across every pool:

```json
{
  "mcpServers": {
    "pglens": {
      "command": "uvx",
      "args": ["pglens"],
      "env": {
        "PGHOST": "myhost.postgres.database.azure.com",
        "PGPORT": "5432",
        "PGUSER": "admin",
        "PGPASSWORD": "...",
        "PGSSLMODE": "require",
        "PGDATABASE": "app",
        "PGLENS_DATABASES": "azure_sys"
      }
    }
  }
}
```

- Names are used verbatim as Postgres dbnames, which are case-sensitive (e.g. `PGDATABASE=MyApp` connects to `MyApp`, not `myapp`).
- If `PGDATABASE` is unset but `PGLENS_DATABASES` is set, the first listed alias becomes the default.
- If both are unset, a single `default` alias relies on libpq's own default behavior.
- If the databases you need live on different hosts or require different credentials, run a separate `pglens` server per host with its own `PG*` env block.

Discover what is configured with `list_databases`, then pass the alias as the `database` argument:

```text
list_databases() -> ["app", "azure_sys"]
table_sizes(schema="public", database="azure_sys")
query(sql="SELECT * FROM query_store.qs_view LIMIT 10", database="azure_sys")
```

Omit `database` (or pass `None`) to use `PGDATABASE` (the primary alias).

### Transport

By default the server uses stdio transport (what every MCP client config above expects). To run as an HTTP server for remote use:

```bash
uvx pglens --transport streamable-http
```

| Flag | Choices | Default | Description |
|---|---|---|---|
| `--transport` | `stdio`, `streamable-http` | `stdio` | MCP transport type |

## Safety

- All user-influenced queries run inside `readonly=True` transactions.
- Table and column identifiers are escaped via PostgreSQL's `quote_ident()`; values are passed as parameters (`$1`, `$2`).
- No DDL tools are exposed.

## How it's built

```
adapters/tools/*.py          (MCP tool definitions, organized by category)
        │
adapters/mcp_adapter.py      (FastMCP server, lifespan, pool management)
        │
adapters/asyncpg_adapter.py  (SQL queries, asyncpg pool)
        │
   PostgreSQL
```

`AsyncpgDatabase` holds the asyncpg pool and all query methods. Tool modules in `adapters/tools/` are thin wrappers that register MCP tools via decorators and delegate to it. All queries use pure `pg_catalog` introspection — no PostgreSQL extensions required.

**Adding a tool:**

1. Add a method to `AsyncpgDatabase` in `adapters/asyncpg_adapter.py`.
2. Add a `@mcp.tool()` function in the appropriate `adapters/tools/*.py` module.

## License

MIT

# pglens

A PostgreSQL MCP server with tools for schema inspection, data exploration, query execution, and database health monitoring.

## Motivation

Most Postgres MCP servers expose `query` and `list_tables`, and that's about it. Agents end up guessing column names, enum values, and join paths, which leads to multiple failed attempts before landing on working SQL.

pglens adds the tools that close those gaps: checking what values actually exist in a column, discovering foreign-key relationships, previewing sample data, and validating query plans. The idea is straightforward: let the agent look before it leaps.

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

### A note on `column_values`

Agents frequently write `WHERE status = 'active'` when the actual value is `'Active'` or `'enabled'`. `column_values` returns the real distinct values in a column with counts, so the agent can pick the right one instead of guessing.

## Installation

```bash
pip install pglens
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install pglens
```

## Usage

pglens reads standard PostgreSQL environment variables (libpq). Connection strings (DSNs) are
not supported — credentials live entirely in `PG*` env vars so they never appear in arguments
or command lines.

```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=myuser
export PGPASSWORD=mypassword
export PGDATABASE=mydb

pglens
```

`PGSSLMODE`, `PGSERVICE`, `PGPASSFILE` and the rest of the libpq env vars are honored by
asyncpg automatically.

### Multiple databases

Every tool accepts an optional `database` argument to target an alternate connection. This is
useful for Postgres setups that expose system metrics in a separate database — for example,
Azure Database for PostgreSQL Flexible Server keeps server metrics in `azure_sys`.

List the dbnames you want pools for via `PGLENS_DATABASES`. Host, user, password and TLS mode
come from the standard libpq env vars and are shared across every alias; only the Postgres
`dbname` varies per pool:

```bash
export PGHOST=myhost.postgres.database.azure.com
export PGUSER=admin
export PGPASSWORD=...
export PGSSLMODE=require
export PGLENS_DATABASES=app,azure_sys
pglens
```

Each comma-separated name becomes a lowercased alias and is used as the Postgres `dbname` for
that pool. By default the first alias in the list is the primary; override with
`PGLENS_DEFAULT_DB=<alias>`.

If the databases you need live on different hosts or require different credentials, run a
separate `pglens` MCP server per host with its own `PG*` env block.

Discover what is configured with the `list_databases` tool, then pass the alias as the
`database` argument:

```text
list_databases() -> ["app", "azure_sys"]
table_sizes(schema="public", database="azure_sys")
query(sql="SELECT * FROM query_store.qs_view LIMIT 10", database="azure_sys")
```

Omit `database` (or pass `None`) to use the primary connection.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `PGHOST` | yes | Postgres host |
| `PGPORT` | no (default 5432) | Postgres port |
| `PGUSER` | yes | Username |
| `PGPASSWORD` | yes (or `PGPASSFILE`) | Password |
| `PGDATABASE` | only when `PGLENS_DATABASES` is unset | dbname for the single `default` alias |
| `PGSSLMODE` | no | `disable`, `prefer`, `require`, `verify-ca`, `verify-full` |
| `PGSERVICE`, `PGPASSFILE`, `PGAPPNAME`, ... | no | Other libpq env vars honored by asyncpg |
| `PGLENS_DATABASES` | no | Comma-separated dbnames -> one alias per dbname, sharing the libpq credentials above |
| `PGLENS_DEFAULT_DB` | no | Alias used when a tool is called without `database=` (defaults to the first entry of `PGLENS_DATABASES`) |

No connection-string env vars are read. Configuration is libpq env vars only.

### Transport

By default the server uses stdio transport. To run as an HTTP server for remote use:

```bash
pglens --transport streamable-http
```

| Flag | Choices | Default | Description |
|---|---|---|---|
| `--transport` | `stdio`, `streamable-http` | `stdio` | MCP transport type |

### Claude Desktop

Single database:

```json
{
  "mcpServers": {
    "pglens": {
      "command": "pglens",
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

Multiple databases on the same host (shared libpq credentials, one pool per dbname):

```json
{
  "mcpServers": {
    "pglens": {
      "command": "pglens",
      "env": {
        "PGHOST": "myhost.postgres.database.azure.com",
        "PGPORT": "5432",
        "PGUSER": "admin",
        "PGPASSWORD": "...",
        "PGSSLMODE": "require",
        "PGLENS_DATABASES": "app,azure_sys",
        "PGLENS_DEFAULT_DB": "app"
      }
    }
  }
}
```

### Claude Code

```json
{
  "mcpServers": {
    "pglens": {
      "command": "pglens",
      "env": {
        "PGHOST": "localhost",
        "PGDATABASE": "mydb"
      }
    }
  }
}
```

### Zed

```json
{
  "context_servers": {
    "pglens": {
      "command": {
        "path": "pglens",
        "args": []
      }
    }
  }
}
```

## Architecture

```
adapters/tools/*.py   (MCP tool definitions, organized by category)
    |
adapters/mcp_adapter.py  (FastMCP server, lifespan, pool management)
    |
adapters/asyncpg_adapter.py  (SQL queries, asyncpg pool)
    |
PostgreSQL
```

`AsyncpgDatabase` holds the asyncpg pool and all query methods. Tool modules in `adapters/tools/` are thin wrappers that register MCP tools via decorators and delegate to it. All queries use pure `pg_catalog` introspection — no PostgreSQL extensions required.

## Adding a tool

1. Add a method to `AsyncpgDatabase` in `adapters/asyncpg_adapter.py`
2. Add a `@mcp.tool()` function in the appropriate `adapters/tools/*.py` module

## Safety

- All user-influenced queries run inside `readonly=True` transactions
- Table and column identifiers are escaped via PostgreSQL's `quote_ident()`
- No DDL tools are exposed

## Requirements

- Python 3.11+
- PostgreSQL

## License

MIT

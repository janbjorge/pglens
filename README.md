# pglens

A PostgreSQL MCP server with tools for schema inspection, data exploration, query execution, and database health monitoring.

## Motivation

Most Postgres MCP servers expose `query` and `list_tables`, and that's about it. Agents end up guessing column names, enum values, and join paths, which leads to multiple failed attempts before landing on working SQL.

pglens adds the tools that close those gaps: checking what values actually exist in a column, discovering foreign-key relationships, previewing sample data, and validating query plans. The idea is straightforward: let the agent look before it leaps.

## Tools

### Schema and discovery

| Tool | What it does |
|---|---|
| `list_schemas` | Schemas with table and view counts |
| `list_tables` | Tables with row counts and descriptions |
| `list_views` | Views with their SQL definitions |
| `list_extensions` | Installed extensions and versions |
| `describe_table` | Columns, types, PKs, FKs, indexes, check constraints |
| `find_related_tables` | FK relationships in both directions |
| `find_join_path` | Multi-hop join paths between two tables via foreign keys |
| `list_functions` | Stored functions/procedures with source code |
| `list_triggers` | Triggers on a table with definitions and status |
| `list_policies` | Row-level security policies on a table |

### Data exploration

| Tool | What it does |
|---|---|
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

pglens reads standard PostgreSQL environment variables.

```bash
export PGHOST=localhost
export PGUSER=myuser
export PGPASSWORD=mypassword
export PGDATABASE=mydb

pglens
```

By default the server uses stdio transport. To run as an HTTP server for remote use:

```bash
pglens --transport streamable-http
```

| Flag | Choices | Default | Description |
|---|---|---|---|
| `--transport` | `stdio`, `streamable-http` | `stdio` | MCP transport type |

### Claude Desktop

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

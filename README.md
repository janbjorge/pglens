# pglens

A PostgreSQL MCP server with tools for schema inspection, data exploration, and query execution.

## Motivation

Most Postgres MCP servers expose `query` and `list_tables`, and that's about it. Agents end up guessing column names, enum values, and join paths, which leads to multiple failed attempts before landing on working SQL.

pglens adds the tools that close those gaps: checking what values actually exist in a column, discovering foreign-key relationships, previewing sample data, and validating query plans. The idea is straightforward: let the agent look before it leaps.

## Tools

| Tool | What it does |
|---|---|
| `list_tables` | Tables with row counts and descriptions |
| `list_views` | Views with their SQL definitions |
| `list_extensions` | Installed extensions and versions |
| `describe_table` | Columns, types, PKs, FKs, indexes, check constraints |
| `find_related_tables` | FK relationships in both directions |
| `sample_rows` | Random rows from a table |
| `column_values` | Distinct values with frequency counts |
| `search_data` | Case-insensitive search across text columns |
| `search_columns` | Find columns by name across all tables |
| `search_enum_values` | Enum types and their allowed values |
| `table_stats` | Index hit rates, dead tuples, vacuum timestamps |
| `explain_query` | Query plan without execution |
| `query` | Read-only SQL, capped at 500 rows |

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

pglens reads standard PostgreSQL environment variables. No config files, no flags.

```bash
export PGHOST=localhost
export PGUSER=myuser
export PGPASSWORD=mypassword
export PGDATABASE=mydb

pglens
```

The server uses stdio transport.

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

pglens uses ports and adapters (hexagonal architecture):

```
MCP Server (input adapter)
    |
DatabasePort (protocol)
    |
AsyncpgDatabase (output adapter) --> PostgreSQL
```

`DatabasePort` is a Python Protocol with 13 methods. `AsyncpgDatabase` implements it with asyncpg. The MCP layer is a thin wrapper that delegates to the port.

To swap the database driver, implement `DatabasePort` with a different library. To test without a database, pass in a fake.

## Adding a tool

1. Add a method to `DatabasePort` in `core/ports.py`
2. Implement it in `adapters/asyncpg_adapter.py`
3. Add a `@mcp.tool()` function in `adapters/mcp_adapter.py`

## Safety

All queries run inside `readonly=True` transactions. No DDL tools are exposed.

## Requirements

- Python 3.11+
- PostgreSQL

## License

MIT

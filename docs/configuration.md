# Configuration

pglens uses standard PostgreSQL environment variables for connection settings. It
does not accept connection strings.

## Install

```bash
uvx pglens
```

`uvx` runs the latest published release. Pin a release when needed:

```bash
uvx pglens@1.1.0
```

Or install it with pip:

```bash
pip install pglens
```

Python 3.11+ and a reachable PostgreSQL server are required.

## MCP client configuration

Configure the client to launch pglens and provide the PostgreSQL settings:

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

When pglens is installed with `pip`, use `"command": "pglens"` and omit `args`.

### Claude Code

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

Claude Code loads `query`, `describe_object`, `list_objects`, and
`column_profile` upfront. It finds the remaining tools through tool search. Add
`"alwaysLoad": false` to the server entry to defer every pglens tool.

### Zed

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

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `PGHOST` | usually | PostgreSQL host. When omitted, libpq can use a local socket. |
| `PGPORT` | no | PostgreSQL port. Default: `5432`. |
| `PGUSER` | usually | PostgreSQL role. When omitted, libpq can use the operating-system user. |
| `PGPASSWORD` | yes, or `PGPASSFILE` | Password. |
| `PGDATABASE` | recommended | Primary database and default alias. |
| `PGSSLMODE` | no | `disable`, `prefer`, `require`, `verify-ca`, or `verify-full`. |
| `PGSERVICE`, `PGPASSFILE`, `PGAPPNAME`, … | no | Other libpq variables honored by asyncpg. |
| `PGLENS_DATABASES` | no | Extra database names on the same host. |
| `PGLENS_STATEMENT_TIMEOUT` | no | Per-statement timeout in seconds. Default: `60`; `0` disables it. |
| `PGLENS_LOCK_TIMEOUT` | no | Lock-wait timeout in seconds. Default: `5`; `0` disables it. |
| `PGLENS_IDLE_TX_TIMEOUT` | no | Idle transaction timeout in seconds. Default: `30`; `0` disables it. |
| `PGLENS_MAX_CELL_CHARS` | no | Maximum text cell length from `query`. Default: `1000`; `0` disables truncation. |

`PGLENS_*` values are validated when pglens starts. Invalid or negative numeric
values cause startup to fail.

To run pglens from a shell:

```bash
export PGHOST=localhost
export PGUSER=myuser
export PGPASSWORD=mypassword
export PGDATABASE=mydb
uvx pglens
```

Run `pglens --version` to check the installed version.

## Multiple databases

Every tool accepts an optional `database` argument. `PGDATABASE` is the default;
list additional databases on the same PostgreSQL host in `PGLENS_DATABASES`.

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

All aliases share host, credentials, and TLS settings. Run a separate pglens
server for databases on a different host or with different credentials.

Use `database_info` to list aliases, then pass one to a tool:

```text
database_info() -> {..., "available_databases": ["app", "azure_sys"]}
health(checks=["sizes"], database="azure_sys")
```

If `PGDATABASE` is unset, the first name in `PGLENS_DATABASES` is the default. If
both are unset, pglens uses one `default` alias and leaves database selection to
libpq.

## Transport

stdio is the default transport. For remote use:

```bash
uvx pglens --transport streamable-http
```

`--transport` accepts `stdio` or `streamable-http`.

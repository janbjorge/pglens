# pglens

Read-only PostgreSQL introspection for AI agents, delivered through MCP.

Give an agent schema discovery, join paths, real column values, read-only SQL,
query plans, and database health checks without giving it write access.

## Install

```bash
uvx pglens
```

Python 3.11+ and a reachable PostgreSQL server are required. To install in an
environment instead:

```bash
pip install pglens
```

## Configure your MCP client

Point your client at pglens and pass PostgreSQL's standard `PG*` environment
variables:

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

[Client examples and configuration reference](docs/configuration.md)

## What agents can do

| Ask | pglens uses |
|---|---|
| What tables, columns, keys, and policies exist? | `list_objects`, `describe_object` |
| How do these tables join? | `find_join_path` |
| What values should this filter use? | `column_profile` |
| What does the data say? | `query`, `explain_query` |
| Is the database healthy or blocked? | `health`, `activity` |

For example, before an agent writes `WHERE status = 'active'`, it can inspect the
actual values and avoid guessing between `active`, `Active`, and `enabled`.

[Complete tool reference](docs/tools.md)

## Safety

pglens sets every database connection to read-only, validates user SQL as a single
`SELECT`, quotes identifiers, and applies statement and lock timeouts. It exposes no
DDL tools.

Read-only access still depends on the PostgreSQL role you configure. Use a role with
only the data the agent should access.

[Safety model and limits](docs/safety.md)

## More

- [Configuration](docs/configuration.md): environment variables, multiple databases,
  and HTTP transport.
- [Tools](docs/tools.md): all nine MCP tools and the usual workflow.
- [Safety](docs/safety.md): enforcement layers and role guidance.
- [Architecture](docs/architecture.md): design and implementation details.
- [Evaluation](docs/evaluation.md): tool-surface benchmark harness.

## License

MIT

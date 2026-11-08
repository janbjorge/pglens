# Tools

pglens has nine read-only MCP tools. Catalogue tools use PostgreSQL system
catalogues; query tools can read application tables that the configured role may
access.

| Tool | Use it for |
|---|---|
| `database_info` | Server version, database name, current role, encoding, timezone, uptime, connection limit, size, schemas, and configured aliases. |
| `list_objects` | Listing tables, views, matviews, indexes, functions, sequences, enums, extensions, or columns. Filter by name and choose `names`, `summary`, or `full` detail. |
| `describe_object` | Inspecting one table, view, matview, function, sequence, or index. Tables include columns, keys, indexes, constraints, triggers, policies, and dependents. |
| `find_join_path` | Finding foreign-key join paths between two tables. |
| `column_profile` | Checking column statistics and the most common values before filtering. |
| `query` | Running a read-only `SELECT` with limit/offset pagination. |
| `explain_query` | Reading a query plan, with optional `ANALYZE`. |
| `health` | Checking table health, sizes, unused indexes, sequences, materialized views, I/O, or slow queries. |
| `activity` | Inspecting sessions, blocking locks, maintenance progress, or replication. |

## Typical workflow

For a data question:

1. Call `database_info` when the target database is unknown.
2. Use `list_objects` and `describe_object` to inspect the schema.
3. Use `find_join_path` for relationships that cross several tables.
4. Use `column_profile` before filtering status, type, or category columns.
5. Run `query`, and use `explain_query` when the plan matters.

For an operational question, start with `health` for stored statistics and
`activity` for current database activity.

## PostgreSQL support

All tools except `health(checks=["slow_queries"])` work on stock PostgreSQL.
`slow_queries` uses `pg_stat_statements` when installed and otherwise returns
installation instructions.

The I/O health check requires PostgreSQL 16+. On older servers it returns an error
entry. pglens adapts returned columns to the PostgreSQL version, including newer
PostgreSQL 18 vacuum and analyze statistics.

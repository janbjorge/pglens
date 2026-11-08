# Safety

pglens is designed for read-only database access. PostgreSQL permissions remain the
ultimate boundary: use a role with only the data it should expose.

## Enforcement

- Every pooled connection sets `default_transaction_read_only=on`.
- User-influenced SQL runs in an explicit read-only transaction.
- `query` and `explain_query` accept one plain `SELECT` only. pglens rejects
  multiple statements, `SELECT INTO`, data-modifying CTEs, and placeholders.
- Identifiers are quoted and catalogue values use bound parameters.
- Statements have configurable statement, lock, and idle-transaction timeouts.
- Tool annotations declare every tool read-only. These are client hints; PostgreSQL
  read-only transactions enforce the restriction.

## Limits

A read-only transaction does not make every function harmless. pglens rejects known
side-effecting functions, including session termination, settings changes, advisory
locks, statistics resets, replication/WAL control, `pg_notify`, and `dblink`.

That list cannot detect a view or user-defined function that calls a side-effecting
function. Do not use a superuser or a role with `pg_signal_backend` unless it is
needed.

`query` truncates long text cells at `PGLENS_MAX_CELL_CHARS`, which defaults to
1,000 characters. Set it to `0` to disable truncation.

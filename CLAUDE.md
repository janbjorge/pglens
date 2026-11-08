# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is pglens?

A Model Context Protocol (MCP) server that gives AI agents read-only introspection into PostgreSQL databases. Nine tools: `database_info`, `list_objects`, `describe_object`, `find_join_path`, `column_profile`, `query`, `explain_query`, `health` and `activity`, all using `pg_catalog` with no extensions required (the `slow_queries` check of `health` reads `pg_stat_statements` when installed and returns install instructions when not; the `io` check needs PostgreSQL 16+ and returns an error entry on older servers). `list_objects`, `describe_object`, `health` and `activity` dispatch on an enum parameter (`kind`, `checks`, `views`) to the matching `AsyncpgDatabase` methods, so the catalogue stays behind a few entry points.

Tools adapt to the server version: `AsyncpgDatabase` lazily caches `server_version_num` (injectable in tests via the constructor) and selects static SQL fragments per version branch. PostgreSQL 18 adds I/O byte counts, vacuum/analyze timing totals, and subscription conflict counts.

## Commands

```bash
# Install dev dependencies
uv sync --group dev

# Lint & format
uv run ruff check .
uv run ruff format --check .

# Type check
uv run ty check

# Run all tests
uv run pytest tests/ -v

# Run a single test
uv run pytest tests/test_database.py::TestSafeRefs::test_safe_table_ref -v
```

## Architecture

```
tools.py     the nine MCP tools (thin wrappers, enum dispatch)
    ↓ call
server.py    MCPServer, INSTRUCTIONS, lifespan (one asyncpg pool per alias)
    ↓ call
database.py  AsyncpgDatabase, where all SQL lives
    ↓
asyncpg pool → PostgreSQL (read-only transactions)
```

Six modules under `src/pglens/`, no subpackages.

- **`database.py`** — `AsyncpgDatabase`: all database methods (~30), each a SQL string handed to `_fetch()` (catalogue SQL, values bound as parameters) or `_fetch_user()` (SQL carrying quoted identifiers or a validated SELECT). Connections are read-only at the server level (`default_transaction_read_only=on` via pool `server_settings`, plus `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`, and `application_name=pglens`); `_fetch_user()` also wraps its statement in `transaction(readonly=True)`. The pool also carries a client-side `command_timeout` set above the statement timeout. Identifiers are quoted with `quote_ident()` from `sql.py`. `max_cell_chars` (from `Settings`, env `PGLENS_MAX_CELL_CHARS`) caps text cells in `query` via `_rows()`. Schema-wide list methods take `limit` (bound as `LIMIT $n`) and the definition-heavy ones take `detail` (`names`/`summary`/`full`, trimmed by `select_detail()`).
- **`server.py`** — Creates the `MCPServer` (mcp 2.x) with `INSTRUCTIONS` as its server instructions (the `query_guide` prompt returns the same constant, so the two cannot drift), the `READ_ONLY` annotations and `ALWAYS_LOAD` meta every tool carries, the `Databases` alias registry with the `db(ctx, database)` helper, and `app_lifespan`, which opens one asyncpg pool per alias.
- **`tools.py`** — The nine tools. Each is a thin wrapper over one or more `AsyncpgDatabase` methods; the dispatching tools use a `match` on the enum parameter, one branch per member. Decorators register the tools at import time, and `pglens/__init__.py` imports this module, so `from pglens import mcp` is a fully registered server.
- **`types.py`** — `Annotated[T, Field(description=...)]` type aliases (e.g., `Schema`, `TableName`, `SQL`). The mcp SDK builds tool input schemas from pydantic `FieldInfo` only, so a bare string inside `Annotated` is silently dropped; descriptions, `Literal` enums and `ge`/`le` ranges declared here are what the model sees. The SDK enforces the ranges before a tool runs; `AsyncpgDatabase` does not re-check them.
- **`settings.py`** — `Settings`, a pydantic-settings `BaseSettings` holding all configuration (database aliases and timeouts). Parsing and validation are pydantic's; don't hand-roll env parsing. Derived config is exposed as `computed_field` properties returning frozen models (`DatabaseAliases`, `PoolOptions`) — not tuples or dicts. **Instantiated exactly once, in `app_lifespan`, and passed down from there**; nothing else reads the environment, and there is no module-level singleton.
- **`sql.py`** — `quote_ident()` for identifiers and `validate_select()`, which parses user SQL with pglast.

## Adding a capability

1. Add the database method to `AsyncpgDatabase` in `database.py`, with unit tests.
2. Expose it through an existing tool: add a member to the matching `Literal` in `types.py` (`ObjectKind`, `ObjectType`, `HealthCheck`, `ActivityView`) and a `match` branch in the tool in `tools.py`.
3. Add a new tool only when no existing entry point fits. `tests/test_tool_registration.py` locks the surface to the expected names, wires every enum member to its database methods, and caps the total definition size.

## Key conventions

- **SQL safety**: Always use `quote_ident()` (from `sql.py`) for identifiers, parameterized queries (`$1`, `$2`) for values, and `readonly=True` transactions. User SQL goes through `validate_select()`, which parses the statement with pglast, rejects anything that is not a single plain SELECT (no multi-statement input, no `SELECT INTO`, no writes hidden in CTEs, no `$n` placeholders), and returns normalized SQL that is safe to embed in a subquery.
- **Parameter types**: Use the `Annotated` aliases from `types.py` for all MCP tool parameters. New aliases need `Field(description=...)`; `tests/test_tool_registration.py` fails on any parameter without a description.
- **Return values**: Convert asyncpg Records to dicts (`[dict(r) for r in rows]`) for JSON serialization.
- **Schema default**: Most tools default the schema parameter to `"public"`.
- **Tool registration**: `tools.py` registers tools as a decorator side effect at import time. `pglens/__init__.py` imports it, and Python runs a package's `__init__` before any of its submodules, so every import path ends with a registered server. Keep `server.py` free of imports from `tools.py`.
- **Testing**: Unit tests mock the asyncpg pool/connection using `make_record(**kwargs)`. Integration tests run against a real PostgreSQL via testcontainers (requires Docker). Tests use `pytest-asyncio` with `asyncio_mode="auto"` and session-scoped event loops.

## Important

- Open work on the MCP surface is tracked in `TODO.md`, ranked by win with refs and required regression tests. Delete an item when it merges; never mark it done. Re-run the baseline script there after each merge.
- Keep `README.md` up to date when making user-facing changes (new tools, config changes, usage changes).
- Python 3.11+ required. Strict mypy enabled.
- Linting (`ruff check`, `ruff format --check`) and type checking (`ty check`) currently pass — keep them green.
- CI runs ruff check, ruff format, ty check, and pytest on every push/PR.
- **Never use global variables.** Pass state explicitly via function parameters, class attributes, or dependency injection.
- **Never use `Any` for typing.** Use specific types, `object`, generics, `Protocol`, or `TypeVar` instead. If a type is truly unknown, prefer `object` over `Any`.
- **Never use `getattr`/`setattr`/`delattr` or other dynamic attribute access.** Use explicit attribute access instead.

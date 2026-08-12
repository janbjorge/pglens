# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is pglens?

A Model Context Protocol (MCP) server that gives AI agents read-only introspection into PostgreSQL databases. 29 tools across schema discovery, data exploration, query execution, and performance monitoring, all using `pg_catalog` with no extensions required (`slow_queries` reads `pg_stat_statements` when installed and returns install instructions when not).

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
uv run pytest tests/test_asyncpg_adapter.py::TestSafeRefs::test_safe_table_ref -v
```

## Architecture

```
MCP Tool definitions (adapters/tools/*.py)
        ↓ call
MCP Server (adapters/mcp_adapter.py)  ←  lifespan manages asyncpg pool
        ↓ call
AsyncpgDatabase (adapters/asyncpg_adapter.py)  ←  all SQL lives here
        ↓
asyncpg pool → PostgreSQL (readonly transactions)
```

- **`src/pglens/adapters/asyncpg_adapter.py`** — Single file containing all database methods (~28). Connections are read-only at the server level (`default_transaction_read_only=on` via pool `server_settings`, plus a statement timeout and `application_name=pglens`); user-influenced queries also use `transaction(readonly=True)`. Identifiers are quoted with `quote_ident()` from `core/sql.py`.
- **`src/pglens/adapters/mcp_adapter.py`** — Creates the `MCPServer` (mcp 2.x) server, manages the asyncpg pool lifespan, and imports tool modules (which register themselves via decorators at import time).
- **`src/pglens/adapters/tools/`** — Tool modules organized by category: `schema.py`, `exploration.py`, `query.py`, `health.py`, `safety.py`. Each tool is a thin wrapper that calls the corresponding `AsyncpgDatabase` method.
- **`src/pglens/core/types.py`** — `Annotated` type aliases (e.g., `Schema`, `TableName`, `SQL`) whose descriptions surface in MCP tool schemas.

## Adding a new tool

1. Add the database method to `AsyncpgDatabase` in `asyncpg_adapter.py`
2. Add an MCP tool decorator in the appropriate `tools/*.py` module

## Key conventions

- **SQL safety**: Always use `quote_ident()` (from `core/sql.py`) for identifiers, parameterized queries (`$1`, `$2`) for values, and `readonly=True` transactions. User SQL goes through `validate_select()`, which parses the statement with pglast, rejects anything that is not a single plain SELECT (no multi-statement input, no `SELECT INTO`, no writes hidden in CTEs, no `$n` placeholders), and returns normalized SQL that is safe to embed in a subquery.
- **Parameter types**: Use `Annotated` types from `core/types.py` for all MCP tool parameters.
- **Return values**: Convert asyncpg Records to dicts (`[dict(r) for r in rows]`) for JSON serialization.
- **Schema default**: Most tools default the schema parameter to `"public"`.
- **Tool registration**: Tool modules use decorator side effects at import time — they're imported as `import pglens.adapters.tools.schema as _schema` in `mcp_adapter.py`.
- **Testing**: Unit tests mock the asyncpg pool/connection using `make_record(**kwargs)`. Integration tests run against a real PostgreSQL via testcontainers (requires Docker). Tests use `pytest-asyncio` with `asyncio_mode="auto"` and session-scoped event loops.

## Important

- Keep `README.md` up to date when making user-facing changes (new tools, config changes, usage changes).
- Python 3.11+ required. Strict mypy enabled.
- Linting (`ruff check`, `ruff format --check`) and type checking (`ty check`) currently pass — keep them green.
- CI runs ruff check, ruff format, ty check, and pytest on every push/PR.
- **Never use global variables.** Pass state explicitly via function parameters, class attributes, or dependency injection.
- **Never use `Any` for typing.** Use specific types, `object`, generics, `Protocol`, or `TypeVar` instead. If a type is truly unknown, prefer `object` over `Any`.
- **Never use `getattr`/`setattr`/`delattr` or other dynamic attribute access.** Use explicit attribute access instead.

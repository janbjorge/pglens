# pglens design

This document models how pglens works. The code is the source of truth: every claim
below points at the line that implements it, and where the code and a docstring
disagree, the code wins. Paths are relative to the repository root.

## Overview

pglens is a Model Context Protocol (MCP) server that gives an AI agent read-only
access to PostgreSQL. Four participants take part in every call:

- **AI agent** (the model): the active party. It reads the server instructions,
  picks a tool, fills in the arguments and decides what to call next.
- **MCP client** (Claude Code, Claude Desktop, Zed, …): launches `pglens`, forwards
  tool calls and returns results to the model.
- **pglens** (`src/pglens/`): a passive server. It validates arguments, runs
  catalogue SQL or a validated SELECT and returns rows as JSON. It keeps no
  per-request state between calls.
- **PostgreSQL**: the source of every fact, and the enforcer of read-only access and
  timeouts.

The agent acts like an analyst at a `psql` prompt who is not allowed to write: it
orients itself, reads the schema, checks real values, then runs SQL. pglens answers
one question at a time.

We support three high-level use cases:

- answer a question about the data
- inspect the schema before writing SQL or suggesting DDL
- diagnose the server's health and current activity

---

## Connection and trust

pglens has no authentication of its own. It inherits trust from two places.

### Upstream: who may call pglens

- **stdio** (the default, `src/pglens/__main__.py:16-23`): the MCP client launches
  pglens as a child process. Only that client can talk to it.
- **streamable-http**: pglens listens over HTTP. The source contains no auth code;
  anyone who reaches the endpoint gets the configured role's read access.

### Downstream: what pglens may do in PostgreSQL

pglens connects with the standard libpq environment variables (`PGHOST`, `PGUSER`,
`PGPASSWORD`, `PGSSLMODE`, …), which asyncpg reads directly. pglens reads only
`PGDATABASE` and the `PGLENS_*` variables itself (`src/pglens/settings.py:57-91`).
There is no connection-string support.

The role in `PGUSER` bounds everything: row-level security, column privileges and
`information_schema` visibility all apply to pglens as to any other session.

### Read-only enforcement, in layers

| # | Layer | Mechanism | Where |
|---|-------|-----------|-------|
| 1 | Tool annotations | `readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`. Hints to the client only | `src/pglens/server.py:113-118` |
| 2 | Argument schema | pydantic `Literal` enums and `ge`/`le` ranges, enforced by the SDK before the tool runs | `src/pglens/types.py` |
| 3 | SQL validation | pglast parses user SQL; only one plain SELECT without side-effecting function calls passes | `src/pglens/sql.py:99-125` |
| 4 | Identifier quoting | `quote_ident()` for names, `$n` parameters for values | `src/pglens/sql.py:57-59` |
| 5 | Read-only transaction | `conn.transaction(readonly=True)` around user-influenced SQL | `src/pglens/database.py:109` |
| 6 | Session default | `default_transaction_read_only=on` on every pooled connection | `src/pglens/settings.py:121` |
| 7 | Timeouts | `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`, and a client-side `command_timeout` | `src/pglens/settings.py:122-126` |

Layers 1 to 4 run inside pglens and the SDK. Layers 5 to 7 are PostgreSQL settings:
pglens requests them and the server enforces them. Catalogue SQL (`_fetch`) runs
without an explicit transaction and relies on layer 6 alone.

Read-only stops writes to tables. It does not stop every side effect. Against
`postgres:18` (2026-11-08), a SELECT inside a read-only transaction could still run
`pg_terminate_backend`, `pg_cancel_backend`, `set_config`, advisory locks,
`pg_stat_reset`, `pg_notify`, `pg_switch_wal`, `pg_reload_conf`,
`pg_create_physical_replication_slot`, `pg_clear_relation_stats` and
`pg_import_system_collations`. Layer 3 therefore rejects calls to 28 named functions
and 7 name prefixes (`SIDE_EFFECT_FUNCTIONS`, `SIDE_EFFECT_PREFIXES`,
`src/pglens/sql.py:14-54`). That is a denylist: a view or a user-defined function
can still wrap one of them. The role's privileges remain the real limit; run pglens
as a role without superuser or `pg_signal_backend`.

### Configuration

`Settings` is instantiated exactly once, in `app_lifespan`
(`src/pglens/server.py:44`). Nothing else reads the environment.

| Setting | Env var | Default | Effect |
|---------|---------|---------|--------|
| `primary_database` | `PGDATABASE` | empty | Primary alias and default target |
| `extra_databases` | `PGLENS_DATABASES` | empty | Comma-separated extra dbnames on the same host |
| `statement_timeout` | `PGLENS_STATEMENT_TIMEOUT` | 60 s | Server-side cap per statement |
| `lock_timeout` | `PGLENS_LOCK_TIMEOUT` | 5 s | Server-side cap on waiting for a lock |
| `idle_tx_timeout` | `PGLENS_IDLE_TX_TIMEOUT` | 30 s | Kills a session idle inside a transaction |
| `max_cell_chars` | `PGLENS_MAX_CELL_CHARS` | 1000 | Longest text cell `query` returns |

`0` disables any of the four numeric settings. The client-side `command_timeout` is
`statement_timeout + 5` seconds, or `None` when the statement timeout is disabled
(`src/pglens/settings.py:126`).

### Alias resolution

`Settings.database_aliases` (`src/pglens/settings.py:99-112`) turns the two env vars
into a list of aliases and a default. Each alias is used verbatim as a dbname.

| `PGDATABASE` | `PGLENS_DATABASES` | Aliases | Default |
|--------------|--------------------|---------|---------|
| `app` | unset | `app` | `app` |
| `app` | `azure_sys` | `app`, `azure_sys` | `app` |
| unset | `a,b` | `a`, `b` | `a` |
| unset | unset | `default` (dbname left to libpq) | `default` |

Extras are stripped, and empty names, duplicates and the primary are dropped
(`src/pglens/settings.py:104-107`). All aliases share one host and one set of
credentials. Databases on another host need a second pglens process.

---

## Architecture

```
┌──────────────┐
│   AI agent   │  picks tools, writes SQL
│   (model)    │
└──────┬───────┘
       │ tool call
       ▼
┌──────────────┐
│  MCP client  │  Claude Code, Claude Desktop, Zed, …
└──────┬───────┘
       │ MCP over stdio (default) or streamable-http
       ▼
┌─────────────────────────────────────────────────────┐
│  pglens  (src/pglens/)                              │
│                                                     │
│  tools.py     nine tools, enum dispatch             │
│  server.py    MCPServer, INSTRUCTIONS, lifespan,    │
│               Databases registry                    │
│  database.py  AsyncpgDatabase: all SQL              │
│  sql.py       quote_ident, validate_select          │
│  types.py     parameter schemas the model sees      │
│  settings.py  Settings, read once from env          │
└──────┬──────────────────────────────────────────────┘
       │ one asyncpg pool per alias, 1 to 5 connections
       ▼
┌─────────────────────────────────────────────────────┐
│  PostgreSQL: one host, one or more databases        │
│  default_transaction_read_only=on, timeouts set     │
└─────────────────────────────────────────────────────┘
```

### Module dependencies

Arrows point from importer to imported module.

```
__main__ ──► pglens/__init__ ──► tools ──► server ──► database ──► sql
                                   │         │           │
                                   │         ▼           ▼
                                   │      settings     types
                                   ├──► database
                                   └──► types
```

`server.py` never imports `tools.py`. Importing the package runs
`pglens/__init__.py`, which imports `tools.py`, whose decorators register the tools
on `mcp`. Every import path therefore ends with a fully registered server
(`src/pglens/__init__.py:3-4`).

---

### Use Cases

```
Answer a data question ──includes──► Explore the schema ──includes──► Run read-only SQL

Diagnose the server  (independent)
```

#### Use Case 1: Answer a data question (User-goal level)

The top-level use case. A human asks a question, and the agent answers it from the
database instead of guessing from application code.

| Aspect        | Description                                          |
|---------------|------------------------------------------------------|
| Primary Actor | AI agent, on behalf of a human                       |
| Input         | Natural-language question, optional **Alias**        |
| Output        | Answer grounded in tool results                      |

**Steps** (the workflow in `INSTRUCTIONS`, `src/pglens/server.py:75-85`):
1. `database_info`: server version, schemas, configured aliases
2. **Explore the schema** (UC2)
3. `column_profile` before filtering on status, type or category columns
4. **Run read-only SQL** (UC3)
5. Compose the answer

**Alternatives:**
- Fewer rows than expected: `describe_object` shows the table's row-level security
  policies.
- A DROP or ALTER is about to be suggested: `describe_object` lists dependents first.

#### Use Case 2: Explore the schema (Subfunction level)

| Aspect        | Description                                          |
|---------------|------------------------------------------------------|
| Primary Actor | AI agent                                             |
| Input         | **Schema**, optional name **Pattern**                |
| Output        | Table names, columns, keys, join conditions, real values |

**Steps:**
1. `list_objects(kind='table')` to see what exists; `kind='column'` with a pattern to
   find a column across tables
2. `describe_object` on each table the query touches
3. `find_join_path` when two tables are several foreign keys apart
4. `column_profile` on status, type and category columns before filtering on them

#### Use Case 3: Run read-only SQL (Subfunction level)

| Aspect        | Description                                          |
|---------------|------------------------------------------------------|
| Primary Actor | AI agent                                             |
| Input         | **SQL**, `limit` (1-500), `offset`                   |
| Output        | Up to `limit` rows, long text cells cut              |

**Steps:**
1. Optionally `explain_query` to check the plan
2. `query`: validate, wrap, run in a read-only transaction, truncate cells
3. Page with keyset pagination when more rows are needed

**Alternatives:**
- Validation rejects the statement: the agent rewrites it (inline literals instead
  of `$1`, no CTE writes, one statement, no side-effecting function calls).
- `statement_timeout` or `lock_timeout` fires: the agent narrows the query.

#### Use Case 4: Diagnose the server (User-goal level)

| Aspect        | Description                                          |
|---------------|------------------------------------------------------|
| Primary Actor | AI agent, on behalf of an operator                   |
| Input         | Health: **checks**, **schema**, `limit`; activity: **views** |
| Output        | One key per check or view                            |

**Steps:**
1. `health` for stored statistics: bloat, sizes, unused indexes, sequences,
   matviews, I/O, slow statements
2. `activity` for the present: sessions, lock waits, maintenance progress,
   replication

---

### Runtime Flow

1. **Client launches pglens**
   **MCP client** → `pglens --transport stdio` → `__main__.main()`
   (`src/pglens/__main__.py:9-23`)

2. **Import registers the tools**
   `from pglens import mcp` → `tools.py` → nine `@mcp.tool` decorators

3. **Lifespan opens the pools**
   `app_lifespan` → `Settings()` → one `asyncpg.create_pool` per alias →
   `Databases` registry yielded as the lifespan context
   (`src/pglens/server.py:40-63`)

4. **Client connects**
   **pglens** → **MCP client**: server instructions (`INSTRUCTIONS`), tool list.
   Four tools carry `anthropic/alwaysLoad`; Claude Code defers the others behind
   tool search.

5. **For each tool call:**
   a. The SDK validates arguments against the schema built from `types.py`
   b. The tool resolves the alias: `db(ctx, database)` → `Databases.get()`
   c. The tool calls one or more `AsyncpgDatabase` methods
   d. Each method runs SQL through `_fetch`, `_fetch_user` or the pool directly, and
      returns `list[dict]` or `dict`
   e. The SDK serializes the result to the client

6. **Shutdown**
   The transport closes; `AsyncExitStack` closes every pool.

#### Startup and connect

```
MCP client               pglens                    PostgreSQL
  │                        │                          │
  │ spawn `pglens`         │                          │
  │───────────────────────►│                          │
  │                        │ import tools.py:         │
  │                        │ register nine tools      │
  │                        │                          │
  │                        │ Settings() from env      │
  │                        │                          │
  │                        │ create_pool per alias    │
  │                        │ (min_size=1, max_size=5, │
  │                        │  server_settings)        │
  │                        │─────────────────────────►│
  │                        │◄─────────────────────────│
  │ initialize             │                          │
  │───────────────────────►│                          │
  │ instructions + tools   │                          │
  │◄───────────────────────│                          │
```

#### `query` call

```
Agent / client           pglens                              PostgreSQL
  │                        │                                    │
  │ query(sql, limit,      │                                    │
  │       offset)          │                                    │
  │───────────────────────►│                                    │
  │                        │ validate_select(sql)               │
  │                        │ (pglast parse, reject, deparse)    │
  │                        │                                    │
  │                        │ BEGIN READ ONLY                    │
  │                        │ SELECT * FROM (<sql>) sub          │
  │                        │   LIMIT $1 OFFSET $2               │
  │                        │ COMMIT                             │
  │                        │───────────────────────────────────►│
  │                        │ rows                               │
  │                        │◄───────────────────────────────────│
  │                        │ truncate_cell on every value       │
  │ rows                   │                                    │
  │◄───────────────────────│                                    │
```

#### `describe_object(kind='table')` fan-out

One tool call issues several independent statements, each on whichever pooled
connection is free. They do not share a snapshot, so concurrent DDL can make the
parts disagree.

```
Agent / client           pglens                              PostgreSQL
  │                        │                                    │
  │ describe_object(name)  │                                    │
  │───────────────────────►│                                    │
  │                        │ describe_table:                    │
  │                        │   columns, primary keys            │
  │                        │   (detail='names' stops here)      │
  │                        │   foreign keys, referenced by,     │
  │                        │   indexes, check constraints       │
  │                        │ list_triggers                      │
  │                        │ list_policies                      │
  │                        │ object_dependencies                │
  │                        │───────────────────────────────────►│
  │                        │◄───────────────────────────────────│
  │ one merged dict        │                                    │
  │◄───────────────────────│                                    │
```

---

### Model

Two models: the objects pglens holds at runtime, and the PostgreSQL catalogue as
pglens sees it. `A ──► B` means A holds or derives B.

#### Runtime model

```
┌────────────┐       ┌─────────────────┐
│            │──────►│ DatabaseAliases │
│  Settings  │     1 └─────────────────┘
│            │       ┌─────────────────┐
│            │──────►│   PoolOptions   │◄──────────────────────────────┐
└────────────┘     1 └─────────────────┘                             1 │
                                                                       │
┌────────────┐       ┌─────────────┐       ┌──────────────────┐   ┌────┴─────────┐
│ MCPServer  │──────►│  Databases  │──────►│ AsyncpgDatabase  │──►│ asyncpg Pool │
│   (mcp)    │     1 │ (registry)  │  1..* │  one per alias   │ 1 └────┬─────────┘
└─────┬──────┘       └─────────────┘       └──────────────────┘        │ 1..5
      │ 9                                                              ▼
      ▼                                                         ┌──────────────┐
┌────────────┐       ┌─────────────────┐                        │  Connection  │
│    Tool    │──────►│ Parameter type  │                        └──────────────┘
└────────────┘  0..* └─────────────────┘
```

#### Catalogue model

```
┌──────────┐       ┌──────────┐       ┌──────────┐       ┌──────────┐       ┌──────────────┐
│  Server  │──────►│ Database │──────►│  Schema  │──────►│  Table   │──────►│    Column    │
└────┬─────┘  1..* └────┬─────┘  1..* └────┬─────┘  0..* └────┬─────┘  1..* └──────┬───────┘
     │                  │ 0..*             │ 0..*             │                    │ 0..1
     ▼                  ▼                  ▼                  │                    ▼
 Session, Lock     Extension      View, Matview, Index,       │             ┌──────────────┐
 wait, Progress,                  Function, Sequence, Enum    │             │ Column stats │
 Replication,                                                 │             └──────────────┘
 I/O, Statement                                               │
 stats                                                        │
                     ┌────────────────────────────────────────┤
                     │ 0..*          │ 0..*        │ 0..*     │ 0..*
                     ▼               ▼             ▼          ▼
               ┌────────────┐  ┌─────────┐  ┌─────────┐  ┌──────────────────┐
               │Foreign key │  │  Index  │  │ Trigger │  │ Policy, Check    │
               └─────┬──────┘  └─────────┘  └─────────┘  │ constraint       │
                     │ 1..*                              └──────────────────┘
                     ▼
               ┌────────────┐
               │Column pair │
               └────────────┘

Any table, view, matview, index, sequence or function ──► Dependent 0..*  (pg_depend)
```

`find_join_path` treats each foreign key as one undirected edge, whatever its number
of column pairs. A hop over a composite key joins on all its columns, combined with
`AND` (`src/pglens/database.py:742-770`).

#### pglens Components

| Component | Type | Where | Description |
|-----------|------|-------|-------------|
| `mcp` | Service | `src/pglens/server.py:108` | The `MCPServer`: instructions, lifespan, tools, `query_guide` prompt |
| `Databases` | Registry | `src/pglens/server.py:16` | Alias → `AsyncpgDatabase`; `get(None)` returns the default alias; `names()` lists the default first, then declaration order |
| `AsyncpgDatabase` | Service | `src/pglens/database.py:84` | Holds the pool; 33 methods, all SQL lives here |
| `Settings` | Configuration | `src/pglens/settings.py:50` | Env parsing and validation, read once |
| `DatabaseAliases` | Value Object | `src/pglens/settings.py:27` | Ordered alias names and the default |
| `PoolOptions` | Value Object | `src/pglens/settings.py:36` | Server GUCs and client `command_timeout` |
| `INSTRUCTIONS` | Constant | `src/pglens/server.py:69` | Server instructions; also the `query_guide` prompt |
| `READ_ONLY` | Value Object | `src/pglens/server.py:113` | Tool annotations carried by all nine tools |
| `ALWAYS_LOAD` | Value Object | `src/pglens/server.py:123` | `_meta` key on four tools |
| `validate_select` | Function | `src/pglens/sql.py:99` | Parse, reject unsafe nodes, deparse |
| `SIDE_EFFECT_FUNCTIONS`, `SIDE_EFFECT_PREFIXES` | Constant | `src/pglens/sql.py:14`, `src/pglens/sql.py:46` | Function names `validate_select` rejects |
| `quote_ident` | Function | `src/pglens/sql.py:57` | Identifier quoting, Postgres semantics |
| `select_detail` | Function | `src/pglens/database.py:58` | Trims rows to `names`, `summary` or `full` |
| `truncate_cell` | Function | `src/pglens/database.py:47` | Cuts long `str` cells in `query` results |
| `like_pattern` | Function | `src/pglens/database.py:78` | Escapes `%`, `_`, `\` for ILIKE search |
| `FOREIGN_KEY_PAIRS` | SQL fragment | `src/pglens/database.py:31` | One row per foreign-key column pair |
| `PROGRESS_VIEWS` | Constant | `src/pglens/database.py:17` | The four `pg_stat_progress_*` views `maintenance` reads |

#### Catalogue Entities

| Entity | Type | Read from | Read by |
|--------|------|-----------|---------|
| Schema | Entity | `pg_namespace` (minus `pg_*`, `information_schema`) | `list_schemas` |
| Table | Entity | `information_schema.tables`, `pg_stat_user_tables` | `list_tables` |
| View | Entity | `information_schema.views` | `list_views` |
| Matview | Entity | `pg_matviews` | `matview_status` |
| Index | Entity | `pg_index`, `pg_class`, `pg_am`, `pg_stat_user_indexes` | `list_indexes`, `unused_indexes` |
| Function | Entity | `pg_proc` (aggregates excluded) | `list_functions` |
| Sequence | Entity | `pg_sequences` | `sequence_health` |
| Enum | Entity | `pg_type`, `pg_enum`, one schema | `search_enum_values` |
| Extension | Entity | `pg_extension` | `list_extensions` |
| Column | Value Object | `information_schema.columns` | `describe_table`, `search_columns` |
| Foreign key | Value Object | `pg_constraint` (`contype='f'`) | `describe_table`, `find_join_path` |
| Trigger | Value Object | `pg_trigger` (non-internal) | `list_triggers` |
| Policy | Value Object | `pg_policy` | `list_policies` |
| Check constraint | Value Object | `pg_constraint` (`contype='c'`) | `describe_table` |
| Dependent | Value Object | `pg_depend`, named by `pg_identify_object` | `object_dependencies` |
| Column stats | Value Object | `pg_stats` plus live table aggregates | `column_stats` |
| Session | Value Object | `pg_stat_activity` | `active_queries`, `blocking_locks` |
| Progress | Value Object | `pg_stat_progress_*`, whole rows | `maintenance_progress` |
| Replication | Value Object | `pg_stat_replication`, `pg_replication_slots`, `pg_stat_subscription_stats` (whole rows) | `replication_status` |
| I/O stats | Value Object | `pg_stat_io`, whole rows | `io_stats` |
| Statement stats | Value Object | `pg_stat_statements` | `slow_queries` |

A dependent is returned as `{dependent_type, dependent}`, both from
`pg_identify_object`. `object_dependencies` keeps normal dependencies and drops the
target's own parts (indexes, constraints, triggers, defaults), except owned
sequences and partitions, which go with it. A view's `_RETURN` rule is mapped back
to the view (`src/pglens/database.py:673-721`).

#### Tool → method dispatch

The tool surface is nine entry points over `AsyncpgDatabase`. Four tools dispatch on
an enum parameter; `tests/test_tool_registration.py` locks the names and wires every
enum member to its methods.

| Tool | Member | `AsyncpgDatabase` method(s) |
|------|--------|-----------------------------|
| `database_info` | | `database_info`, `list_schemas`, plus `Databases.names()` |
| `list_objects` | `table` | `list_tables` |
| | `view` | `list_views` |
| | `matview` | `matview_status` |
| | `index` | `list_indexes` |
| | `function` | `list_functions` |
| | `sequence` | `sequence_health` |
| | `enum` | `search_enum_values` |
| | `extension` | `list_extensions` |
| | `column` | `search_columns` |
| `describe_object` | `table` | `describe_table`, `list_triggers`, `list_policies`, `object_dependencies` |
| | `view`, `matview`, `function`, `sequence`, `index` | matching list method with `detail='full'`, filtered to `name`, plus `object_dependencies` |
| `find_join_path` | | `find_join_path` |
| `column_profile` | | `column_stats`, `column_values` |
| `query` | | `query` |
| `explain_query` | | `explain_query` |
| `health` | `tables` | `table_health` |
| | `sizes` | `table_sizes` |
| | `indexes` | `unused_indexes` |
| | `sequences` | `sequence_health` |
| | `matviews` | `matview_status` |
| | `io` | `io_stats` |
| | `slow_queries` | `slow_queries` |
| `activity` | `sessions` | `active_queries` |
| | `locks` | `blocking_locks` |
| | `maintenance` | `maintenance_progress` |
| | `replication` | `replication_status` |

`health` and `activity` run their members in the order given, once each
(`dict.fromkeys`, `src/pglens/tools.py:275`, `src/pglens/tools.py:307`).

#### SQL execution paths

| Path | Transaction | SQL text | Used by |
|------|-------------|----------|---------|
| `_fetch` (`src/pglens/database.py:99`) | none explicit; session default is read-only | fixed text, values bound as `$n` | every catalogue method |
| `_fetch_user` (`src/pglens/database.py:103`) | `transaction(readonly=True)` on one connection | quoted identifiers or a validated SELECT | `column_values`, `column_stats` (counts), `query`, `explain_query` |
| `pool.fetchrow` / `fetchval` | none explicit | fixed text | `database_info`, `column_stats` (`pg_stats`), version probe, `slow_queries` probes, `replication_status` (`pg_is_in_recovery`) |

Only those four `_fetch_user` methods put user input into SQL text. Everywhere else
a user-supplied name travels as a bound value, either compared to a catalogue column
or cast with `$1::regclass` (`src/pglens/database.py:520-608`).

#### Detail levels

`detail` is `names`, `summary` or `full` (`src/pglens/types.py:148`).
`select_detail` applies it at two levels:

- The method drops heavy keys at `summary`: `definition` for views, matviews and
  indexes; `source` for functions.
- `list_objects` reduces every kind to its name keys at `names`
  (`src/pglens/tools.py:123`).

`describe_object` honours `detail` for tables only. Other kinds always fetch `full`.

#### Parameter Value Objects

| Type | Values | Where |
|------|--------|-------|
| `ObjectKind` | `table`, `view`, `matview`, `index`, `function`, `sequence`, `enum`, `extension`, `column` | `src/pglens/types.py:74` |
| `ObjectType` | `table`, `view`, `matview`, `function`, `sequence`, `index` | `src/pglens/types.py:69` |
| `HealthCheck` | `tables`, `sizes`, `indexes`, `sequences`, `matviews`, `io`, `slow_queries` | `src/pglens/types.py:91` |
| `ActivityView` | `sessions`, `locks`, `maintenance`, `replication` | `src/pglens/types.py:98` |
| `DetailLevel` | `names`, `summary`, `full` | `src/pglens/types.py:148` |
| `ListLimit` | 1-1000, default 100 | `src/pglens/types.py:160` |
| `Limit` | 1-500, default 500 | `src/pglens/types.py:172` |
| `HealthLimit` | 1-100, default 20 | `src/pglens/types.py:55` |
| `TopN` | 1-100, default 20 | `src/pglens/types.py:46` |
| `MaxDepth` | 1-6, default 4 | `src/pglens/types.py:115` |
| `Offset` | ≥ 0, default 0 | `src/pglens/types.py:177` |

---

## Server Lifecycle

pglens holds almost no state. The server moves through a few process states, and
each `AsyncpgDatabase` caches one value.

### Process states

```
              launch
                │
                ▼
         ┌────────────┐
         │  imported  │  tools.py registers nine tools
         └─────┬──────┘
               │ mcp.run(transport)
               ▼
         ┌─────────────┐   ValidationError
         │ configuring │──────────────────────► exit
         └─────┬───────┘   (bad PGLENS_* value)
               │ Settings() read once
               ▼
         ┌─────────────┐   connect error
         │opening pools│──────────────────────► exit, opened pools closed
         └─────┬───────┘
               │ every alias has a pool
               ▼
         ┌─────────────┐
         │   serving   │◄──── tool calls
         └─────┬───────┘
               │ transport closes
               ▼
         ┌─────────────┐
         │   stopped   │  AsyncExitStack closes every pool
         └─────────────┘
```

Pools open eagerly, one per alias, inside one `AsyncExitStack`
(`src/pglens/server.py:47-63`). asyncpg opens `min_size` connections when the pool
is entered, so a single unreachable alias stops startup (inferred from asyncpg; no
test covers it).

### Per-database state

`AsyncpgDatabase.server_version_num` starts as `None` and is filled on the first
version-gated call (`src/pglens/database.py:91-97`). It is never refreshed: an
in-place server upgrade is seen only after pglens restarts. Tests inject it through
the constructor.

### Version branches

Stat views that gain columns with each release are read whole (`SELECT *` or
`p.*`), so a newer server's columns appear without a code change. Version gates
remain only where a view does not exist yet, or where a method computes over named
columns.

| Feature | Gate | Where |
|---------|------|-------|
| `health` `tables`: vacuum/analyze time totals, `relallfrozen`, `frozen_pct` | ≥ 18 | `src/pglens/database.py:884-899` |
| `health` `io`: whole check | ≥ 16, otherwise one error row | `src/pglens/database.py:977-983` |
| `health` `io`: columns | whatever `pg_stat_io` has | `src/pglens/database.py:985-993` |
| `activity` `maintenance`: columns | whatever each `pg_stat_progress_*` view has | `src/pglens/database.py:1098-1110` |
| `activity` `replication`: subscriptions | ≥ 15 | `src/pglens/database.py:1151-1155` |
| `activity` `replication`: subscription columns | whatever `pg_stat_subscription_stats` has | `src/pglens/database.py:1153-1154` |
| `health` `slow_queries`: parallel worker and `wal_buffers_full` columns | column exists | `src/pglens/database.py:1012-1029` |
| `explain_query`: buffers when `buffers=False` | server default (on with ANALYZE from 18) | `src/pglens/database.py:870-879` |

`slow_queries` gates on the column, not the version: a `pg_upgrade`d cluster can run
an older `pg_stat_statements` schema on a newer server.

### User SQL pipeline

```
 sql text
   │ parse_sql (pglast)                    ── syntax error ──► parse error
   ▼
 exactly one statement?                    ── no ──► ValueError
   │
   ▼
 SelectStmt?                               ── no ──► ValueError
 (explain_query also unwraps
  DECLARE CURSOR … FOR SELECT)
   │
   ▼
 _RejectUnsafeNodes walks the whole tree:
 INSERT, UPDATE, DELETE, MERGE anywhere,
 SELECT INTO, side-effecting function
 calls, $n placeholders                    ── found ──► ValueError
   │
   ▼
 RawStream deparse: one clean statement,
 no trailing semicolon or comments
   │
   ├── query:   SELECT * FROM (<sql>) sub LIMIT $1 OFFSET $2
   └── explain: EXPLAIN (ANALYZE <b>[, BUFFERS TRUE], FORMAT TEXT) <sql>
   │
   ▼
 _fetch_user: BEGIN READ ONLY … COMMIT
   │
   ▼
 query only: truncate_cell on every str value longer than max_cell_chars
```

A function call is matched on the last part of its name, so `pg_catalog.set_config`
is caught as `set_config` (`src/pglens/sql.py:86-90`).

### Error model

pglens raises and never catches, with one exception: `column_stats` retries without
`min`/`max` when the column type has no ordering (`src/pglens/database.py:840-846`).
Exceptions reach the mcp SDK, which reports them to the client as a failed tool
call. Sources:

- `ValueError` from `validate_select`, `Databases.get` (unknown alias) and the
  unreachable `case _` branches in `tools.py`
- asyncpg errors: unknown table or column, `statement_timeout`, `lock_timeout`
- SDK validation errors for out-of-range or wrong-typed arguments

Three conditions return data instead of raising, so the rest of a multi-check result
survives:

- `column_stats`: `{"error": "No statistics … Run ANALYZE."}`
- `io_stats` below PostgreSQL 16: one error row
- `slow_queries` without `pg_stat_statements`: one row with install instructions

---

## Key Design Decisions

### 1. PostgreSQL enforces read-only, pglens only helps

**Decision:** Every pooled connection sets `default_transaction_read_only=on`;
user-influenced SQL also runs in an explicit read-only transaction.

**Reasoning:**
- The server refuses writes even if validation has a bug
  (`src/pglens/settings.py:120-121`)
- Tool annotations are hints for the client, not enforcement
  (`src/pglens/server.py:110-112`)

**Tradeoff:** Catalogue SQL relies on the session default alone. Read-only blocks
writes to tables but not side-effecting functions; see decision 3.

---

### 2. Parse user SQL with PostgreSQL's own parser

**Decision:** `validate_select` parses with pglast, walks the whole tree, and returns
the deparsed statement.

**Reasoning:**
- Writes can hide in CTEs (`WITH x AS (DELETE …) SELECT …`); a keyword check misses
  them (`src/pglens/sql.py:65-69`)
- The deparsed text has no trailing semicolon or comment, so it is safe to embed in
  a subquery or after `EXPLAIN` (`src/pglens/sql.py:104-106`)

**Tradeoff:** `$n` placeholders are rejected, because the tools have no way to pass
values; the agent must inline literals.

---

### 3. Deny side-effecting functions by name

**Decision:** `validate_select` rejects calls to the functions in
`SIDE_EFFECT_FUNCTIONS` and to names starting with one of `SIDE_EFFECT_PREFIXES`,
anywhere in the tree (`src/pglens/sql.py:14-54`, `src/pglens/sql.py:86-90`).

**Reasoning:**
- A read-only transaction does not stop them. Probed against `postgres:18`, they
  ended other sessions, changed settings (`set_config` can lift
  `statement_timeout`), took session locks, reset statistics, wrote WAL, created a
  replication slot and rewrote planner statistics.
- Ordinary functions (`count`, `lower`, `now`, `current_setting`) stay allowed.

**Tradeoff:** A denylist is incomplete by nature. A view or a user-defined function
can wrap a listed function, and a function added in a later PostgreSQL release is
allowed until someone lists it. The role's privileges are the boundary that holds.

---

### 4. Nine tools, enum dispatch

**Decision:** Group about thirty database methods behind nine tools. Four of them
(`list_objects`, `describe_object`, `health`, `activity`) take an enum that selects
the method.

**Reasoning:**
- Tool definitions cost context in every session; the surface is capped at 16,000
  characters by `test_tool_definition_budget`
- The move from 31 tools to 9 kept the eval pass rate (21 of 21) and lowered input
  tokens (see `evaluation.md`)

**Tradeoff:** The dispatching tools carry the longest descriptions, and a model must
know the enum member to reach a capability.

---

### 5. Four tools load upfront

**Decision:** `list_objects`, `describe_object`, `column_profile` and `query` carry
`anthropic/alwaysLoad` (`src/pglens/server.py:120-123`).

**Reasoning:** Claude Code defers MCP tools behind tool search. The common path of
UC1 then needs no search step.

**Tradeoff:** Those four definitions cost context in every session. The comment caps
the set at three to five tools.

---

### 6. Instructions and prompt share one constant

**Decision:** `INSTRUCTIONS` is both the server instructions and the `query_guide`
prompt (`src/pglens/server.py:136-139`).

**Reasoning:** The two cannot drift. Clients cut instructions at 4,096 characters, so
the workflow comes first (`src/pglens/server.py:66-68`).

---

### 7. Environment-only configuration, read once

**Decision:** Configuration comes from libpq env vars and `PGLENS_*` only, parsed by
pydantic-settings in one `Settings()` call.

**Reasoning:**
- Credentials never appear in arguments or command lines
- A bad value fails at startup with a `ValidationError` naming the field
- No module-level singleton: state flows from `app_lifespan` down

**Tradeoff:** One host and one set of credentials per pglens process.

---

### 8. One pool per alias

**Decision:** Each alias gets its own asyncpg pool, `min_size=1`, `max_size=5`
(`src/pglens/server.py:53-60`). Every tool takes an optional `database` alias.

**Reasoning:** Some setups keep metrics in a separate database, for example
`azure_sys` on Azure Flexible Server.

**Tradeoff:** All pools open at startup; one unreachable database stops the server.

---

### 9. Four timeouts

**Decision:** `statement_timeout`, `lock_timeout` and
`idle_in_transaction_session_timeout` on the server, plus a client `command_timeout`
5 seconds above the statement timeout.

**Reasoning** (`src/pglens/settings.py:36-47`, `src/pglens/settings.py:70-83`):
- A SELECT takes `ACCESS SHARE`; without a lock timeout pglens would queue behind a
  pending `ALTER` for the whole statement timeout, and block everything behind it
- A stalled client inside a transaction would pin `xmin` and hold locks
- A blackholed connection never sees the server's timeout; the client deadline frees
  the pool slot

---

### 10. Read growing stat views whole

**Decision:** `pg_stat_io`, the four `pg_stat_progress_*` views and
`pg_stat_subscription_stats` are read with `SELECT *`. The cached
`server_version_num` only skips views that do not exist yet and adds the PostgreSQL
18 columns `table_health` computes over.

**Reasoning:** These views gain columns with each release
(`src/pglens/database.py:15-16`). Reading them whole shows a newer server's columns
without a code change.

**Tradeoff:** The result shape and units follow the server version: columns keep
their raw PostgreSQL names and values, with no `_ms` rounding. The cached version is
never refreshed.

---

### 11. Name filters apply after the fetch

**Decision:** With a `pattern`, `list_objects` fetches a page of up to
`CATALOGUE_PAGE` (1000) rows and filters names in Python
(`src/pglens/tools.py:38-40`, `src/pglens/tools.py:121-123`). `describe_object` finds
non-table objects the same way. `enum` and `column` are the exceptions: their
pattern goes into the SQL.

**Reasoning:** One SQL statement per kind, with no per-kind name predicate.

**Tradeoff:** Objects past row 1000 of a schema's catalogue are invisible to a
pattern search and to `describe_object`, which then returns empty `definitions`
without an error.

---

### 12. Cut long text cells in `query` only

**Decision:** `query` cuts `str` cells longer than `max_cell_chars` and appends
`…[truncated, N chars total]` (`src/pglens/database.py:47-55`).

**Reasoning:** One wide json or text column must not flood the model's context.

**Tradeoff:** Other tools return text uncut: view and function definitions,
`activity` `sessions` query text, `slow_queries` statement text.

---

### 13. Decided against: Code Mode sandbox

**Decision:** No `execute(code)` tool.

**Reasoning:** SQL already is the code, and PostgreSQL under the read-only default
and the timeouts already is the sandbox. Details and references in `CLAUDE.md`.

---

## Known Limitations

Behaviour of the current code.

1. **Join paths stay inside one schema.**
   Both ends of every edge must be in `schema` (`src/pglens/database.py:729-740`).
   Foreign keys that cross schemas are not traversed.

2. **Join path search lists every simple path.**
   The BFS keeps all simple paths up to `max_depth`, shortest first
   (`src/pglens/database.py:756-780`). In a dense FK graph the output grows quickly.

3. **Mixed catalogue sources.**
   Tables, views and columns come from `information_schema`; indexes, functions,
   matviews and everything else from `pg_catalog`. `information_schema` shows only
   objects the role holds a privilege on (PostgreSQL docs, inferred), so a restricted
   role can see an index in `list_objects(kind='index')` whose table
   `kind='table'` does not list.

4. **`health` ordering is per check.**

   | Check | Order | Honours `schema` | Honours `limit` |
   |-------|-------|------------------|-----------------|
   | `tables` | live tuples, descending | yes | yes |
   | `sizes` | total size, descending | yes | yes |
   | `indexes` | index size, descending | yes | yes |
   | `sequences` | percent consumed, descending | yes | yes |
   | `matviews` | name, ascending | yes | yes |
   | `io` | backend type, object, context | no (cluster) | no |
   | `slow_queries` | total execution time, descending | no (all databases) | yes |

5. **`describe_object` is not a snapshot.**
   Its statements run as separate implicit transactions on pooled connections.

6. **The function denylist can be wrapped.**
   `validate_select` sees only the statement text. A view or a user-defined
   function that calls a listed function runs it inside `query` or
   `explain_query(analyze=True)`.

---

## References

- [`README.md`](../README.md): user-facing tools, configuration and safety
- [`CLAUDE.md`](../CLAUDE.md): contributor conventions and design constraints
- [`evaluation.md`](evaluation.md): tool-surface baseline and eval harness
- `tests/test_tool_registration.py`: the locked tool surface and enum wiring

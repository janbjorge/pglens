# pglens — Design

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
`PGDATABASE` and the `PGLENS_*` variables itself (`src/pglens/settings.py:68-122`).
There is no connection-string support.

The role in `PGUSER` bounds everything: row-level security, column privileges and
`information_schema` visibility all apply to pglens as to any other session.

### Read-only enforcement, in layers

| # | Layer | Mechanism | Where |
|---|-------|-----------|-------|
| 1 | Tool annotations | `readOnlyHint`, `destructiveHint=false`. A hint to the client only | `src/pglens/server.py:112-117` |
| 2 | Argument schema | pydantic `Literal` enums and `ge`/`le` ranges, enforced by the SDK before the tool runs | `src/pglens/types.py` |
| 3 | SQL validation | pglast parses user SQL; only one plain SELECT passes | `src/pglens/sql.py:43-68` |
| 4 | Identifier quoting | `quote_ident()` for names, `$n` parameters for values | `src/pglens/sql.py:8-10` |
| 5 | Read-only transaction | `conn.transaction(readonly=True)` around user-influenced SQL | `src/pglens/database.py:96` |
| 6 | Session default | `default_transaction_read_only=on` on every pooled connection | `src/pglens/settings.py:161` |
| 7 | Timeouts | `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`, and a client-side `command_timeout` | `src/pglens/settings.py:162-166` |

Layers 1 to 4 run inside pglens and the SDK. Layers 5 to 7 are PostgreSQL settings:
pglens requests them and the server enforces them. Catalogue SQL (`_fetch`) runs
without an explicit transaction and relies on layer 6 alone.

Read-only stops writes to data. It does not stop every side effect. A validated
SELECT may call any function the role is allowed to call, such as
`pg_terminate_backend`, `pg_cancel_backend` or `set_config`. Against `postgres:18`,
`query("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE …")` ended
another session through a read-only pool (verified 2026-11-08). The role's
privileges are the only limit on these calls; run pglens as a role without
superuser or `pg_signal_backend`.

### Configuration

`Settings` is instantiated exactly once, in `app_lifespan`
(`src/pglens/server.py:43`). Nothing else reads the environment.

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
(`src/pglens/settings.py:166`).

### Alias resolution

`Settings.database_aliases` (`src/pglens/settings.py:131-151`) turns the two env vars
into a list of aliases and a default. Each alias is used verbatim as a dbname.

| `PGDATABASE` | `PGLENS_DATABASES` | Aliases | Default |
|--------------|--------------------|---------|---------|
| `app` | unset | `app` | `app` |
| `app` | `azure_sys` | `app`, `azure_sys` | `app` |
| unset | `a,b` | `a`, `b` | `a` |
| unset | unset | `default` (dbname left to libpq) | `default` |

Extras are stripped, and empty names, duplicates and the primary are dropped
(`src/pglens/settings.py:142-145`). All aliases share one host and one set of
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

**Steps** (the workflow in `INSTRUCTIONS`, `src/pglens/server.py:74-83`):
1. `database_info`: server version, schemas, configured aliases
2. **Explore the schema** (UC2)
3. **Run read-only SQL** (UC3)
4. Compose the answer

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
  of `$1`, no CTE writes, one statement).
- `statement_timeout` or `lock_timeout` fires: the agent narrows the query.

#### Use Case 4: Diagnose the server (User-goal level)

| Aspect        | Description                                          |
|---------------|------------------------------------------------------|
| Primary Actor | AI agent, on behalf of an operator                   |
| Input         | **Health checks** or **Activity views**, **Schema**, `limit` |
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
   (`src/pglens/__main__.py:9-24`)

2. **Import registers the tools**
   `from pglens import mcp` → `tools.py` → nine `@mcp.tool` decorators

3. **Lifespan opens the pools**
   `app_lifespan` → `Settings()` → one `asyncpg.create_pool` per alias →
   `Databases` registry yielded as the lifespan context
   (`src/pglens/server.py:39-62`)

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
 Statement stats                                              │
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

#### pglens Components

| Component | Type | Where | Description |
|-----------|------|-------|-------------|
| `mcp` | Service | `src/pglens/server.py:107` | The `MCPServer`: instructions, lifespan, tools, `query_guide` prompt |
| `Databases` | Registry | `src/pglens/server.py:16` | Alias → `AsyncpgDatabase`; `get(None)` returns the default alias |
| `AsyncpgDatabase` | Service | `src/pglens/database.py:71` | Holds the pool; 33 methods, all SQL lives here |
| `Settings` | Configuration | `src/pglens/settings.py:55` | Env parsing and validation, read once |
| `DatabaseAliases` | Value Object | `src/pglens/settings.py:27` | Ordered alias names and the default |
| `PoolOptions` | Value Object | `src/pglens/settings.py:36` | Server GUCs and client `command_timeout` |
| `INSTRUCTIONS` | Constant | `src/pglens/server.py:68` | Server instructions; also the `query_guide` prompt |
| `READ_ONLY` | Value Object | `src/pglens/server.py:112` | Tool annotations carried by all nine tools |
| `ALWAYS_LOAD` | Value Object | `src/pglens/server.py:122` | `_meta` key on four tools |
| `validate_select` | Function | `src/pglens/sql.py:43` | Parse, reject unsafe nodes, deparse |
| `quote_ident` | Function | `src/pglens/sql.py:8` | Identifier quoting, Postgres semantics |
| `select_detail` | Function | `src/pglens/database.py:45` | Trims rows to `names`, `summary` or `full` |
| `truncate_cell` | Function | `src/pglens/database.py:34` | Cuts long `str` cells in `query` results |
| `like_pattern` | Function | `src/pglens/database.py:65` | Escapes `%`, `_`, `\` for ILIKE search |
| `FOREIGN_KEY_PAIRS` | SQL fragment | `src/pglens/database.py:18` | One row per foreign-key column pair |

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
| Enum | Entity | `pg_type`, `pg_enum` (all schemas) | `search_enum_values` |
| Extension | Entity | `pg_extension` | `list_extensions` |
| Column | Value Object | `information_schema.columns` | `describe_table`, `search_columns` |
| Foreign key | Value Object | `pg_constraint` (`contype='f'`) | `describe_table`, `find_join_path` |
| Trigger | Value Object | `pg_trigger` (non-internal) | `list_triggers` |
| Policy | Value Object | `pg_policy` | `list_policies` |
| Check constraint | Value Object | `pg_constraint` (`contype='c'`) | `describe_table` |
| Dependent | Value Object | `pg_depend` (`deptype` `n`, `a`) | `object_dependencies` |
| Column stats | Value Object | `pg_stats` | `column_stats` |
| Session | Value Object | `pg_stat_activity` | `active_queries`, `blocking_locks` |
| Progress | Value Object | `pg_stat_progress_*` | `maintenance_progress` |
| Replication | Value Object | `pg_stat_replication`, `pg_replication_slots`, `pg_stat_subscription_stats` | `replication_status` |
| Statement stats | Value Object | `pg_stat_statements` | `slow_queries` |

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
(`dict.fromkeys`, `src/pglens/tools.py:274`, `src/pglens/tools.py:306`).

#### SQL execution paths

| Path | Transaction | SQL text | Used by |
|------|-------------|----------|---------|
| `_fetch` (`src/pglens/database.py:86`) | none explicit; session default is read-only | fixed text, values bound as `$n` | every catalogue method |
| `_fetch_user` (`src/pglens/database.py:90`) | `transaction(readonly=True)` on one connection | quoted identifiers or a validated SELECT | `column_values`, `column_stats` (counts), `query`, `explain_query` |
| `pool.fetchrow` / `fetchval` | none explicit | fixed text | `database_info`, `column_stats` (`pg_stats`), version probe, `slow_queries` probes, `replication_status` (`pg_is_in_recovery`) |

Only those four `_fetch_user` methods put user input into SQL text. Everywhere else
a user-supplied name travels as a bound value, either compared to a catalogue column
or cast with `$1::regclass` (`src/pglens/database.py:376`).

#### Detail levels

`detail` is `names`, `summary` or `full` (`src/pglens/types.py:151`).
`select_detail` applies it at two levels:

- The method drops heavy keys at `summary`: `definition` for views, matviews and
  indexes; `source` for functions.
- `list_objects` reduces every kind to its name keys at `names`
  (`src/pglens/tools.py:123`).

`describe_object` honours `detail` for tables only. Other kinds always fetch `full`.

#### Parameter Value Objects

| Type | Values | Where |
|------|--------|-------|
| `ObjectKind` | `table`, `view`, `matview`, `index`, `function`, `sequence`, `enum`, `extension`, `column` | `src/pglens/types.py:77` |
| `ObjectType` | `table`, `view`, `matview`, `function`, `sequence`, `index` | `src/pglens/types.py:72` |
| `HealthCheck` | `tables`, `sizes`, `indexes`, `sequences`, `matviews`, `io`, `slow_queries` | `src/pglens/types.py:94` |
| `ActivityView` | `sessions`, `locks`, `maintenance`, `replication` | `src/pglens/types.py:101` |
| `DetailLevel` | `names`, `summary`, `full` | `src/pglens/types.py:151` |
| `ListLimit` | 1-1000, default 100 | `src/pglens/types.py:163` |
| `Limit` | 1-500, default 500 | `src/pglens/types.py:175` |
| `HealthLimit` | 1-100, default 20 | `src/pglens/types.py:55` |
| `TopN` | 1-100, default 20 | `src/pglens/types.py:46` |
| `MaxDepth` | 1-6, default 4 | `src/pglens/types.py:118` |
| `Offset` | ≥ 0, default 0 | `src/pglens/types.py:180` |

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
(`src/pglens/server.py:46-62`). asyncpg opens `min_size` connections when the pool
is entered, so a single unreachable alias stops startup (inferred from asyncpg; no
test covers it).

### Per-database state

`AsyncpgDatabase.server_version_num` starts as `None` and is filled on the first
version-gated call (`src/pglens/database.py:78-84`). It is never refreshed: an
in-place server upgrade is seen only after pglens restarts. Tests inject it through
the constructor.

### Version branches

| Feature | Gate | Where |
|---------|------|-------|
| `health` `tables`: vacuum/analyze time totals, `relallfrozen`, `frozen_pct` | ≥ 18 | `src/pglens/database.py:815` |
| `health` `io`: whole check | ≥ 16, otherwise one error row | `src/pglens/database.py:897` |
| `health` `io`: `read_bytes`, `write_bytes`, `extend_bytes` | ≥ 18 | `src/pglens/database.py:911` |
| `activity` `maintenance`: vacuum `indexes_total`, `indexes_processed` | ≥ 17 | `src/pglens/database.py:1053` |
| `activity` `maintenance`: `delay_time_ms` | ≥ 18 | `src/pglens/database.py:1061` |
| `activity` `replication`: subscriptions | ≥ 15 | `src/pglens/database.py:1206` |
| `activity` `replication`: conflict counters | ≥ 18 | `src/pglens/database.py:1218` |
| `health` `slow_queries`: parallel worker and `wal_buffers_full` columns | column exists | `src/pglens/database.py:959-967` |
| `explain_query`: buffers when `buffers=False` | server default (on with ANALYZE from 18) | `src/pglens/database.py:791-796` |

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
 SELECT INTO, $n placeholders              ── found ──► ValueError
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

### Error model

pglens raises and never catches, with one exception: `column_stats` retries without
`min`/`max` when the column type has no ordering (`src/pglens/database.py:761`).
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
  (`src/pglens/settings.py:159-161`, "belt and braces")
- Tool annotations are hints for the client, not enforcement
  (`src/pglens/server.py:109-111`)

**Tradeoff:** Catalogue SQL relies on the session default alone. Read-only blocks
data writes but not side-effecting functions called from a SELECT (see
[Connection and trust](#connection-and-trust)); the role's privileges must cover
that gap.

---

### 2. Parse user SQL with PostgreSQL's own parser

**Decision:** `validate_select` parses with pglast, walks the whole tree, and returns
the deparsed statement.

**Reasoning:**
- Writes can hide in CTEs (`WITH x AS (DELETE …) SELECT …`); a keyword check misses
  them (`src/pglens/sql.py:16-18`)
- The deparsed text has no trailing semicolon or comment, so it is safe to embed in
  a subquery or after `EXPLAIN` (`src/pglens/sql.py:48-50`)

**Tradeoff:** `$n` placeholders are rejected, because the tools have no way to pass
values; the agent must inline literals.

---

### 3. Nine tools, enum dispatch

**Decision:** Group about thirty database methods behind nine tools. Four of them
(`list_objects`, `describe_object`, `health`, `activity`) take an enum that selects
the method.

**Reasoning:**
- Tool definitions cost context in every session; the surface is capped at 16,000
  characters by `test_tool_definition_budget`
- The move from 31 tools to 9 kept the eval pass rate (21 of 21) and lowered input
  tokens (see `evals/README.md`)

**Tradeoff:** The dispatching tools carry the longest descriptions, and a model must
know the enum member to reach a capability.

---

### 4. Four tools load upfront

**Decision:** `list_objects`, `describe_object`, `column_profile` and `query` carry
`anthropic/alwaysLoad` (`src/pglens/server.py:119-122`).

**Reasoning:** Claude Code defers MCP tools behind tool search. The common path of
UC1 then needs no search step.

**Tradeoff:** Those four definitions cost context in every session. The comment caps
the set at three to five tools.

---

### 5. Instructions and prompt share one constant

**Decision:** `INSTRUCTIONS` is both the server instructions and the `query_guide`
prompt (`src/pglens/server.py:135-138`).

**Reasoning:** The two cannot drift. Clients cut instructions at 4,096 characters, so
the workflow comes first (`src/pglens/server.py:65-67`).

---

### 6. Environment-only configuration, read once

**Decision:** Configuration comes from libpq env vars and `PGLENS_*` only, parsed by
pydantic-settings in one `Settings()` call.

**Reasoning:**
- Credentials never appear in arguments or command lines
- A bad value fails at startup with a `ValidationError` naming the field
- No module-level singleton: state flows from `app_lifespan` down

**Tradeoff:** One host and one set of credentials per pglens process.

---

### 7. One pool per alias

**Decision:** Each alias gets its own asyncpg pool, `min_size=1`, `max_size=5`
(`src/pglens/server.py:52-59`). Every tool takes an optional `database` alias.

**Reasoning:** Some setups keep metrics in a separate database, for example
`azure_sys` on Azure Flexible Server.

**Tradeoff:** All pools open at startup; one unreachable database stops the server.

---

### 8. Four timeouts

**Decision:** `statement_timeout`, `lock_timeout` and
`idle_in_transaction_session_timeout` on the server, plus a client `command_timeout`
5 seconds above the statement timeout.

**Reasoning** (`src/pglens/settings.py:86-122`, `src/pglens/settings.py:44-52`):
- A SELECT takes `ACCESS SHARE`; without a lock timeout pglens would queue behind a
  pending `ALTER` for the whole statement timeout, and block everything behind it
- A stalled client inside a transaction would pin `xmin` and hold locks
- A blackholed connection never sees the server's timeout; the client deadline frees
  the pool slot

---

### 9. Version-adaptive SQL from static fragments

**Decision:** Methods select fixed SQL fragments by `server_version_num`, cached per
database.

**Reasoning:** Newer columns appear when the server has them; older servers get the
base set. No SQL is assembled from runtime data.

**Tradeoff:** The cached version is never refreshed.

---

### 10. Name filters apply after the fetch

**Decision:** With a `pattern`, `list_objects` fetches a page of up to
`CATALOGUE_PAGE` (1000) rows and filters names in Python
(`src/pglens/tools.py:38-40`, `src/pglens/tools.py:120-122`). `describe_object` finds
non-table objects the same way.

**Reasoning:** One SQL statement per kind, with no per-kind name predicate.

**Tradeoff:** Objects past row 1000 of a schema's catalogue are invisible to a
pattern search and to `describe_object`, which then returns empty `definitions`
without an error.

---

### 11. Cut long text cells in `query` only

**Decision:** `query` cuts `str` cells longer than `max_cell_chars` and appends
`…[truncated, N chars total]` (`src/pglens/database.py:34-42`).

**Reasoning:** One wide json or text column must not flood the model's context.

**Tradeoff:** Other tools return text uncut: view and function definitions,
`activity` `sessions` query text, `slow_queries` statement text.

---

### 12. Decided against: Code Mode sandbox

**Decision:** No `execute(code)` tool.

**Reasoning:** SQL already is the code, and PostgreSQL under the read-only default
and the timeouts already is the sandbox. Details and references in `CLAUDE.md`.

---

## Known Limitations

Behaviour of the current code. Items marked *verified* were reproduced against a
`postgres:18` container on 2026-11-08.

1. **Function dependents are incomplete** (*verified*).
   `object_dependencies(kind='function')` keeps only `pg_depend` rows with
   `classid = 'pg_class'` (`src/pglens/database.py:548`). A view, column default or
   check constraint that calls the function is missing; only an expression index
   came back. Triggers record their dependency under `pg_trigger`, so the same filter
   drops them too (inferred). `tests/test_integration.py:524-526` asserts only that
   a list comes back.

2. **Composite foreign keys split into half-joins** (*verified*).
   `find_join_path` adds one graph edge per column pair
   (`src/pglens/database.py:659-664`). A two-column key `c(x, y) → p(x, y)` yields two
   one-hop paths, `c.x = p.x` and `c.y = p.y`, each of which fans out if used alone.

3. **Join paths stay inside one schema.**
   Both ends of every edge must be in `schema` (`src/pglens/database.py:653-654`).
   Foreign keys that cross schemas are not traversed.

4. **Join path search lists every simple path.**
   The BFS keeps all simple paths up to `max_depth`, shortest first
   (`src/pglens/database.py:666-686`). In a dense FK graph the output grows quickly.

5. **`list_objects(kind='enum')` ignores `schema`** (*verified*).
   `search_enum_values` takes no schema and searches every schema
   (`src/pglens/database.py:345-360`); each row carries its own `schema` key.
   `kind='extension'` is database-wide by nature.

6. **Mixed catalogue sources.**
   Tables, views and columns come from `information_schema`; indexes, functions,
   matviews and everything else from `pg_catalog`. `information_schema` shows only
   objects the role holds a privilege on (PostgreSQL docs, inferred), so a restricted
   role can see an index in `list_objects(kind='index')` whose table
   `kind='table'` does not list.

7. **`health` ordering is per check.**

   | Check | Order | Honours `schema` | Honours `limit` |
   |-------|-------|------------------|-----------------|
   | `tables` | live tuples, descending | yes | yes |
   | `sizes` | total size, descending | yes | yes |
   | `indexes` | index size, descending | yes | yes |
   | `sequences` | percent consumed, descending | yes | yes |
   | `matviews` | name, ascending | yes | yes |
   | `io` | backend type, object, context | no (cluster) | no |
   | `slow_queries` | total execution time, descending | no (all databases) | yes |

8. **`describe_object` is not a snapshot.**
   Its statements run as separate implicit transactions on pooled connections.

9. **A SELECT can have side effects** (*verified*).
   `validate_select` rejects write statements, not function calls. Whatever the role
   may call from a SELECT, `query` and `explain_query(analyze=True)` can run,
   including `pg_terminate_backend`.

---

## References

- `README.md`: user-facing tools, configuration and safety
- `CLAUDE.md`: conventions, adding a capability, decided-against items
- `evals/README.md`: tool-surface baseline and eval harness
- `tests/test_tool_registration.py`: the locked tool surface and enum wiring

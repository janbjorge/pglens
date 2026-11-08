# TODO

Open work on the pglens MCP surface, ranked by win. Audit date: 2026-11-08, mcp 2.0.0.

Rules
- Delete an item when it is merged to main. Never mark items done, never keep a done list.
- Item numbers are stable identifiers. Do not renumber after a deletion; other items cross-reference them.
- If scope changes, edit the item in place. Keep the refs: they are the reason the item exists.
- After each merge, re-run the baseline script below and update the Baseline table.
- Every item ships with its regression tests and its README/CLAUDE.md edits in the same PR.
- Scoped test runs only, for example `uv run pytest tests/test_tool_registration.py -q`. CI runs the rest.

## Baseline

| Metric | Value (2026-11-08, after items 1-4) |
|---|---|
| Tools | 31 |
| Tool-definition JSON, compact | 32,996 chars (about 8k tokens at 4 chars/token) |
| Average per tool | 1,064 chars |
| Largest tools | query 1,770, explain_query 1,578, find_join_path 1,377 |
| Parameters with a `description` in the schema | 76 of 76 |
| Tools with annotations | 31 of 31 |
| Server `instructions` | 2,289 chars |
| `query_guide` prompt | same text as the instructions |

Measure with:

```bash
uv run python - <<'EOF'
import asyncio, json
from pglens.adapters.mcp_adapter import mcp

async def main():
    tools = await mcp.list_tools()
    total = 0
    for t in sorted(tools, key=lambda t: t.name):
        s = json.dumps(t.model_dump(exclude_none=True, by_alias=True), separators=(",", ":"))
        total += len(s)
        described = sum(1 for p in t.input_schema["properties"].values() if p.get("description"))
        print(f"{t.name:24} {len(s):>6} chars  described params {described}/{len(t.input_schema['properties'])}  annotations={t.annotations is not None}")
    print("tools", len(tools), "total chars", total, "approx tokens", total // 4)
    print("instructions chars", len(mcp.instructions or ""))

asyncio.run(main())
EOF
```

Token figures are chars/4 estimates, not tokenizer counts. Good enough to track direction.

## 5. Put tool output on a diet

Win: high. Effort: medium. All clients.

Why
- Tool definitions cost about 8k tokens once per session and are prompt-cached. Tool output is paid on every call and stays in context. `list_functions` selects `p.prosrc` for every function in the schema, `list_views` returns every view definition, `list_indexes` returns every index definition, `describe_table` returns six sections, and `query`, `sample_rows`, `search_data` return `SELECT *` rows with unbounded cell sizes (text, json, bytea).
- Anthropic: use "pagination, range selection, filtering, and/or truncation" with sensible defaults, and treat every tool response as spend from a finite attention budget. https://www.anthropic.com/engineering/writing-tools-for-agents
- Claude Code warns above 10,000 output tokens per call, caps at 25,000 by default (`MAX_MCP_OUTPUT_TOKENS`), and writes text results over 50,000 characters to a file unless the tool declares `_meta["anthropic/maxResultSizeChars"]`. https://code.claude.com/docs/en/mcp
- DBHub's `search_objects` uses `detail_level` `names`, `summary`, `full` for the same reason. https://dbhub.ai/tools/search-objects.md

Change
- New alias `Detail = Literal["names", "summary", "full"]` in `core/types.py`, default `"summary"`. `names`: name and schema only. `summary`: today's row minus `source`, `definition`, `most_common_vals`. `full`: today's row. Apply to `list_functions`, `list_views`, `list_indexes`, `matview_status`, and to `describe_table` (`summary` skips `check_constraints` and index definitions). After item 7 these become parameters of `list_objects` and `describe_object`.
- New alias `ListLimit = Annotated[int, Field(ge=1, le=1000, description=...)]`, default 100, on the schema-wide list tools. Pass it as a bind parameter (`LIMIT $2`), never by string formatting.
- Cell truncation in `query`, `sample_rows`, `search_data`: cut any `str` cell longer than `max_cell_chars` to that length and append `…[truncated, N chars total]`. New `Settings.max_cell_chars: NonNegativeInt = 1000`, env `PGLENS_MAX_CELL_CHARS`, `0` disables, matching the existing "0 disables" convention. Pass it into `AsyncpgDatabase(pool, max_cell_chars=...)` from `app_lifespan`; no module state.

Regression tests
- `tests/test_asyncpg_adapter.py` with `make_record`: `list_functions(detail="summary")` rows have no `source`; `detail="full"` rows have it; `limit` appears as `LIMIT $2` in the SQL text and the value is passed. `query` with a 5,000-character cell returns 1,000 characters plus the marker; with `max_cell_chars=0` the cell is untouched. Non-string cells (int, datetime, bytes) are never touched.
- `tests/test_settings.py`: `PGLENS_MAX_CELL_CHARS=abc` raises `ValidationError`; unset gives 1000.
- `tests/test_integration.py`: create one plpgsql function, call `list_functions` at all three detail levels, assert `source` only at `full`.

Done when
- Every list tool has `detail` and `limit`; a `query` on a table with a 1 MB text cell returns under 5 KB.

Docs
- README: tool table rows for the affected tools; configuration table gets `PGLENS_MAX_CELL_CHARS`.
- CLAUDE.md: mention `max_cell_chars` on `AsyncpgDatabase`.

## 6. Eval harness, prerequisite for item 7

Win: enabling only. Effort: medium. Nobody until item 7.

Why
- Anthropic: "Start by generating lots of evaluation tasks, grounded in real world uses", pair each with a verifiable outcome, and "Tracking tool calls can help reveal common workflows that agents pursue and offer some opportunities for tools to consolidate." https://www.anthropic.com/engineering/writing-tools-for-agents
- pglens has no usage data. Without a before/after measurement item 7 is opinion.

Change
- New directory `evals/`, not collected by pytest, not run in CI.
- `evals/schema.sql`: a seed schema with 8 to 10 tables, FKs two hops deep, an enum type, a view, a matview, one plpgsql function, one trigger, one RLS policy, a never-scanned index, mixed-case status values. Reuse the fixtures in `tests/test_integration.py` where they fit.
- `evals/tasks.json`: 15 to 20 tasks, each with `prompt`, `expected` (exact value, set of names, or regex), and `expect_tools` (the tool names a good run uses). Cover: distinct values before a WHERE, a two-hop join, "which tables reference X", a query plan question, an unused index question, one health and one activity question, one multi-database question.
- `evals/run.py`: runs each task through `claude -p --mcp-config evals/mcp.json --output-format json` (or the Agent SDK), on the testcontainers postgres:18 image, and records per task: tool call sequence, wrong-tool picks (tools called that are not in `expect_tools`), input and output tokens, errors, wall time, pass/fail. Writes `evals/results/<git-sha>.json` and prints a summary table.
- Cost note in `evals/README.md`: each full run spends API tokens; run by hand, compare two shas.

Regression tests
- `tests/test_evals_tasks.py`: `evals/tasks.json` parses, every task has the three keys, every `expect_tools` entry is a registered tool name (catches stale names after item 7), every `expected` compiles if it is a regex.

Done when
- One baseline run on main is committed under `evals/results/`.

Docs
- README "How it's built": one line pointing at `evals/`.

## 7. Consolidate 31 tools into 9

Win: high. Effort: large. Largest for non-Claude clients. Ship only after item 6 shows parity or better.

Why
- Selection accuracy falls as the catalogue grows: 92.4% at 15 tools to 78.2% at 640 (arXiv 2607.15593, https://arxiv.org/pdf/2607.15593); shorter candidate lists lift Claude Sonnet 4.6 from 87.1% to 93.1% (arXiv 2605.24660, https://arxiv.org/html/2605.24660v1); survey of the problem in https://doi.org/10.3390/a19060447.
- Anthropic: "Too many tools or overlapping tools can also distract agents from pursuing efficient strategies." https://www.anthropic.com/engineering/writing-tools-for-agents
- Cursor sends only the first 40 tools across all servers; pglens alone is 31. https://forum.cursor.com/t/tools-limited-to-40-total/67976
- Comparable servers: DBHub ships 2 tools by default (`execute_sql`, `search_objects`), about 1.4k tokens, https://github.com/bytebase/dbhub; crystaldba postgres-mcp ships 9, with one `analyze_db_health(health_type=...)` for all health checks, https://github.com/crystaldba/postgres-mcp; Google MCP Toolbox's postgres preset ships 28 at about 19k tokens and gets criticized for it, https://dbhub.ai/blog/postgres-mcp-server-review-mcp-toolbox.
- Code Mode (Cloudflare, `search()` plus `execute()`, https://blog.cloudflare.com/code-mode-mcp/; Anthropic, https://www.anthropic.com/engineering/code-execution-with-mcp) is the same shape: discovery plus execution. For Postgres the execution tool is `query`, SQL is the code, and the discovery tool is object search. See "Decided against" for why no extra sandbox.
- In Claude Code the token win is already taken by tool search. The win there is fewer wrong picks and fewer overlapping tools. On every other client it is both.

Target surface (adapter methods and their tests stay; only `adapters/tools/*.py` changes)

| New tool | Replaces | Signature |
|---|---|---|
| `database_info` | database_info, list_schemas | unchanged, plus a `schemas` key with the `list_schemas` rows |
| `list_objects` | list_tables, list_views, list_indexes, list_functions, list_extensions, search_columns, search_enum_values, matview_status, sequence_health (listing part) | `kind: Literal["table","view","matview","index","function","sequence","enum","extension","column"]`, `schema="public"`, `pattern: str | None = None` (case-insensitive substring, the `ILIKE` escaping from `search_columns`), `detail: Detail = "summary"`, `limit: ListLimit = 100`, `database` |
| `describe_object` | describe_table, list_triggers, list_policies, object_dependencies | `name`, `kind: Literal["table","view","matview","function","sequence","index"] = "table"`, `schema`, `database`. For `table`: today's `describe_table` dict plus `triggers`, `policies`, `dependents`. Other kinds: `definition` plus `dependents` |
| `find_join_path` | unchanged | unchanged |
| `column_profile` | column_values, column_stats | `table_name`, `column_name`, `top_n: TopN = 20`, `schema`, `database`; returns `{"stats": <column_stats>, "top_values": <column_values>}` |
| `query` | query, sample_rows, search_data | unchanged; sampling and keyword search become SQL idioms in the server instructions |
| `explain_query` | unchanged | unchanged |
| `health` | table_health, table_sizes, unused_indexes, sequence_health, matview_status, io_stats, slow_queries | `checks: list[Literal["tables","sizes","indexes","sequences","matviews","io","slow_queries"]] | None = None` (None means all), `schema`, `limit: TopQueries = 20` for slow_queries, `database`; returns a dict keyed by check. Use a list of literals, not a comma string, so the schema carries the enum |
| `activity` | active_queries, blocking_locks, maintenance_progress, replication_status | `views: list[Literal["sessions","locks","maintenance","replication"]] | None = None`, `database`; dict keyed by view |

- `sample_rows` and `search_data` are the most opinionated removals. Keep them if the item 6 baseline shows the model picking them often and succeeding; otherwise remove.
- Version-gated SQL (PG16/17/18 branches in `table_health`, `slow_queries`, `io_stats`, `maintenance_progress`, `replication_status`) is the knowledge worth keeping server-side. It moves untouched behind `health` and `activity`.
- Breaking change: `feat!:` commit, BREAKING CHANGE footer listing all 22 removed names, major version tag, same as #20 did.

Regression tests
- `tests/test_tool_registration.py`: `EXPECTED_TOOLS` becomes the 9 names; `test_no_unexpected_tools` locks the surface. `test_tool_delegates_to_db_method` currently asserts exactly one db call per tool; change it to a mapping from (tool, arguments) to the exact ordered list of `AsyncpgDatabase` methods expected, and parametrize over every `kind`, `check` and `view` value so each enum member is wired. `health(checks=None)` must call all seven methods; `health(checks=["io"])` exactly one.
- `test_tool_definition_budget`: total compact JSON of `mcp.list_tools()` stays under 14,000 characters. Stops regrowth. Today's 31 described tools measure 32,996 characters, 1,064 each; 9 richer tools at about 1,500 each fit.
- `test_tool_count`: `len(mcp.list_tools()) <= 10`.
- Item 2's `test_instructions_name_only_real_tools` and item 6's task test catch stale names.
- `tests/test_integration.py`: one test per new tool against postgres:18. `list_objects(kind="column", pattern="email")` finds the seeded column; `describe_object` on the seeded table returns the trigger and the policy; `column_profile` returns both sections; `health()` returns all seven keys; `activity()` returns all four.
- Run the item 6 eval on the branch and commit the result file next to the baseline. Ship only if pass rate is equal or better and wrong-tool picks are fewer.

Done when
- Baseline table shows 9 tools and under 14,000 characters, eval result committed, README tables rewritten.

Docs
- README: the four tool tables collapse into one 9-row table; the opening line's "31 MCP tools" changes; the `column_values` call-out becomes `column_profile`.
- CLAUDE.md: "31 tools" in the first paragraph; "Adding a new tool" becomes "Adding a capability": add a db method, then add a `kind`, `check` or `view` literal and its wiring, not a new tool.
- Server instructions (`INSTRUCTIONS` in `mcp_adapter.py`) and `evals/tasks.json` (item 6) updated to the new names.

## 8. Feature groups, fallback only

Win: low once item 7 lands. Effort: medium. Non-Claude clients, locked-down setups. Do this only if item 7 stalls.

Why
- Supabase added `--features` groups because "many MCP clients limit the number of tools you can connect to them" and to shrink the blast radius. https://supabase.com/blog/remote-mcp-server
- Application developers rarely need DBA tools; a smaller surface is also a smaller attack surface.

Change
- `Settings.tool_groups: Annotated[list[StrippedStr], NoDecode]`, env `PGLENS_TOOLS`, comma-separated, default all groups, same validator pattern as `extra_databases`. Groups: `core` (schema, exploration, query, safety modules) and `health` (health module). Unknown group name fails at startup with a `ValidationError`.
- Each tool module exposes `GROUP = "core"` or `"health"`; `mcp_adapter.py` builds a name-to-group map at import.
- In `app_lifespan`, before `yield`, call `mcp.remove_tool(name)` for every tool whose group is disabled. `MCPServer.remove_tool(name)` exists in mcp 2.0.0. The lifespan runs before any `tools/list`, and Settings stays instantiated once, in `app_lifespan`.

Regression tests
- `tests/test_settings.py`: `PGLENS_TOOLS=core` parses to `["core"]`; `PGLENS_TOOLS=nope` raises `ValidationError`; unset means all groups.
- `tests/test_multi_database.py` pattern (patched `asyncpg.create_pool`): entering `app_lifespan` with `PGLENS_TOOLS=core` leaves no health-module tool in `mcp.list_tools()`; with the default, all tools remain.

Done when
- `PGLENS_TOOLS=core uvx pglens` lists only core tools in a client.

Docs
- README configuration table: `PGLENS_TOOLS` row and a short "Feature groups" subsection.

## Decided against

Code Mode sandbox tool (a JavaScript or Python `execute(code)` tool). SQL already is the code and PostgreSQL under `default_transaction_read_only=on`, `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout` already is the sandbox. Cloudflare: client-side Code Mode "requires the agent to ship with secure sandbox access" and "solves context costs for a single API" (https://blog.cloudflare.com/code-mode-mcp/). Anthropic: "Running agent-generated code requires a secure execution environment with appropriate sandboxing" (https://www.anthropic.com/engineering/code-execution-with-mcp). A sandbox adds attack surface and buys no context, because tool search already defers definitions on Claude clients and item 7 shrinks them everywhere else. Revisit only if the item 6 eval shows the model chaining five or more catalogue calls that one script would replace.

## References

- Anthropic, Writing effective tools for agents (2025-09-11): https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, Advanced tool use, tool search and programmatic tool calling (2025-11): https://www.anthropic.com/engineering/advanced-tool-use
- Anthropic, Code execution with MCP (2025-11-04): https://www.anthropic.com/engineering/code-execution-with-mcp
- Cloudflare, Code Mode: give agents an entire API in 1,000 tokens (2026-02-20): https://blog.cloudflare.com/code-mode-mcp/
- Armin Ronacher, Your MCP Doesn't Need 30 Tools: It Needs Code (2025-08-18): https://lucumr.pocoo.org/2025/8/18/code-mcps/
- Claude Code MCP docs (tool search, instructions, annotations, output limits, alwaysLoad): https://code.claude.com/docs/en/mcp
- Claude platform docs, Tool search tool: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- MCP spec 2025-11-25, tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- MCP spec 2025-11-25, prompts: https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
- MCP schema (instructions, ToolAnnotations): https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-11-25/schema.ts
- DBHub, two-tool database MCP server: https://github.com/bytebase/dbhub and https://dbhub.ai/tools/search-objects.md
- crystaldba postgres-mcp, 9 tools: https://github.com/crystaldba/postgres-mcp
- Supabase remote MCP, feature groups: https://supabase.com/blog/remote-mcp-server
- Google MCP Toolbox prebuilt tools: https://mcp-toolbox.dev/reference/prebuilt-tools/
- Tool-count studies: https://arxiv.org/pdf/2607.15593, https://arxiv.org/html/2605.24660v1, https://doi.org/10.3390/a19060447
- Cursor 40-tool limit thread: https://forum.cursor.com/t/tools-limited-to-40-total/67976
- Claude Code issue on annotation-based permissions: https://github.com/anthropics/claude-code/issues/87452
- HN threads on the two-tool pattern: https://news.ycombinator.com/item?id=47099142, https://news.ycombinator.com/item?id=49810670, https://news.ycombinator.com/item?id=50381773

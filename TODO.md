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

| Metric | Value (2026-11-08, after items 1-7) |
|---|---|
| Tools | 9 |
| Tool-definition JSON, compact | 15,540 chars (about 4k tokens at 4 chars/token); `test_tool_definition_budget` caps it at 16,000 |
| Average per tool | 1,727 chars |
| Largest tools | list_objects 2,481, query 2,059, describe_object 1,935 |
| Parameters with a `description` in the schema | 36 of 36 |
| Tools with annotations | 9 of 9 |
| Server `instructions` | 2,252 chars |
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

## Decided against

Code Mode sandbox tool (a JavaScript or Python `execute(code)` tool). SQL already is the code and PostgreSQL under `default_transaction_read_only=on`, `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout` already is the sandbox. Cloudflare: client-side Code Mode "requires the agent to ship with secure sandbox access" and "solves context costs for a single API" (https://blog.cloudflare.com/code-mode-mcp/). Anthropic: "Running agent-generated code requires a secure execution environment with appropriate sandboxing" (https://www.anthropic.com/engineering/code-execution-with-mcp). A sandbox adds attack surface and buys no context, because tool search already defers definitions on Claude clients and item 7 shrinks them everywhere else. Revisit only if `evals/run.py` shows the model chaining five or more catalogue calls that one script would replace.

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

# pglens evals

Measures how well a model uses the pglens tool surface on a seeded database:
which tools it picks, how many calls it needs, how many tokens it spends, and
whether it lands on the right answer. Run it before and after a change to the
tool surface and compare the two result files.

Each run drives `claude -p` once per task in `tasks.json` against a throwaway
`postgres:18` container seeded with `schema.sql`. It spends API tokens on every
task, so it is run by hand and never in CI.

```bash
uv run python evals/run.py                 # all tasks
uv run python evals/run.py --only exact_count --only two_hop_join
uv run python evals/run.py --model claude-sonnet-5-5
```

Results land in `results/<git-sha>.json` (`-dirty` is appended when the working
tree has uncommitted changes), with a summary table printed at the end. The
`results/` directory is gitignored: copy the summary numbers into the baseline
table below rather than committing the file. Each
task records the ordered pglens tool calls, the calls that were not in
`expect_tools` (unexpected picks), the built-in tools used (ToolSearch), the token
usage, cost, wall time and pass/fail.

Requirements: Docker (for testcontainers), the `claude` CLI logged in, and
`uv sync --group dev`.

`tests/test_evals_tasks.py` keeps `tasks.json` valid and makes sure every
`expect_tools` entry is a registered tool, so a tool rename cannot silently
break the harness.

## Tool-surface baseline

Measured on `main` on 2026-11-08, after the consolidation to nine tools. Re-run after
any change to a tool, its parameters or the server instructions, and update the table.

| Metric | Value |
|---|---|
| Tools | 9 |
| Tool-definition JSON, compact | 15,540 chars (about 4k tokens at 4 chars/token); `test_tool_definition_budget` caps it at 16,000 |
| Average per tool | 1,727 chars |
| Largest tools | list_objects 2,481, query 2,059, describe_object 1,935 |
| Parameters with a `description` in the schema | 36 of 36 |
| Tools with annotations | 9 of 9 |
| Server `instructions` | 2,252 chars |
| `query_guide` prompt | same text as the instructions |
| Eval (`run.py`, claude-opus-5-5, 21 tasks) | 21 of 21 passed, 39 tool calls, 11 picks outside `expect_tools`, 753k input tokens (run at 0601988); the 31-tool baseline was 21 of 21, 38 calls, 13 picks, 841k tokens (run at 5674c3e) |

Measure with:

```bash
uv run python - <<'EOF'
import asyncio, json
from pglens import mcp

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

## References

Background for the shape of the tool surface.

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

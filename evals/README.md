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
tree has uncommitted changes), with a summary table printed at the end. Each
task records the ordered pglens tool calls, the calls that were not in
`expect_tools` (unexpected picks), the built-in tools used (ToolSearch), the token
usage, cost, wall time and pass/fail.

Requirements: Docker (for testcontainers), the `claude` CLI logged in, and
`uv sync --group dev`.

`tests/test_evals_tasks.py` keeps `tasks.json` valid and makes sure every
`expect_tools` entry is a registered tool, so a tool rename cannot silently
break the harness.

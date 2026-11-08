"""Drive claude -p through evals/tasks.json against a seeded PostgreSQL and record
which pglens tools it picks, what it spends, and whether it lands the answer.

Run by hand, never in CI: every task costs API tokens. See evals/README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import asyncpg
from testcontainers.postgres import PostgresContainer

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent
TOOL_PREFIX = "mcp__pglens__"
# Built-in tools that would let the model answer without pglens, or wander.
DISALLOWED_BUILTINS = "Read,Edit,Write,Glob,Grep,WebSearch,WebFetch,Task,TodoWrite,NotebookEdit"
SYSTEM_PROMPT = (
    "You answer questions about one PostgreSQL database through the pglens MCP tools. "
    "Use the tools for every fact; never guess schema or data. End with a short final "
    "answer that contains the requested names or numbers."
)


@dataclass(frozen=True)
class Task:
    name: str
    prompt: str
    expected: dict[str, object]
    expect_tools: list[str]


@dataclass
class Outcome:
    name: str
    passed: bool
    tool_calls: list[str]  # pglens tools, in call order
    unexpected_tools: list[str]  # pglens tools called that are not in expect_tools
    missing_tools: list[str]  # expect_tools never called
    builtin_calls: list[str]  # non-pglens tools, e.g. ToolSearch
    num_turns: int
    input_tokens: int  # all input, including cache reads and cache writes
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    duration_ms: int
    is_error: bool
    answer: str


def load_tasks(path: Path, only: list[str]) -> list[Task]:
    raw = json.loads(path.read_text())
    tasks = [
        Task(
            name=str(entry["name"]),
            prompt=str(entry["prompt"]),
            expected=dict(entry["expected"]),
            expect_tools=[str(t) for t in entry["expect_tools"]],
        )
        for entry in raw
    ]
    if only:
        unknown = set(only) - {t.name for t in tasks}
        if unknown:
            sys.exit(f"unknown task names: {sorted(unknown)}")
        tasks = [t for t in tasks if t.name in only]
    return tasks


def grade(expected: dict[str, object], answer: str) -> bool:
    text = " ".join(answer.split()).lower()
    contains = expected.get("contains")
    if isinstance(contains, list):
        return all(str(needle).lower() in text for needle in contains)
    regex = expected.get("regex")
    if isinstance(regex, str):
        return re.search(regex, answer) is not None
    raise ValueError(f"expected needs 'contains' or 'regex': {expected}")


def mcp_config(container: PostgresContainer) -> dict[str, object]:
    return {
        "mcpServers": {
            "pglens": {
                "command": "uv",
                "args": ["run", "--project", str(ROOT), "pglens"],
                "env": {
                    "PGHOST": container.get_container_host_ip(),
                    "PGPORT": str(container.get_exposed_port(5432)),
                    "PGUSER": container.username,
                    "PGPASSWORD": container.password,
                    "PGDATABASE": container.dbname,
                },
            }
        }
    }


async def seed(container: PostgresContainer, schema: Path) -> None:
    conn = await asyncpg.connect(container.get_connection_url(driver=None))
    try:
        await conn.execute(schema.read_text())
    finally:
        await conn.close()


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _tool_uses(message: object) -> list[str]:
    """Names of the tool_use blocks in one assistant message, prefixes intact."""
    if not isinstance(message, dict):
        return []
    content = cast(dict[str, object], message).get("content")
    if not isinstance(content, list):
        return []
    names: list[str] = []
    for block in content:
        if isinstance(block, dict):
            typed = cast(dict[str, object], block)
            if typed.get("type") == "tool_use":
                names.append(str(typed.get("name", "")))
    return names


def run_task(
    task: Task, config_path: Path, cwd: Path, model: str | None, budget_usd: float
) -> tuple[Outcome, str | None]:
    cmd = [
        "claude",
        "-p",
        task.prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--restricted",
        "--strict-mcp-config",
        "--mcp-config",
        str(config_path),
        "--allowedTools",
        "mcp__pglens",
        "--disallowedTools",
        DISALLOWED_BUILTINS,
        "--max-budget-usd",
        str(budget_usd),
        "--append-system-prompt",
        SYSTEM_PROMPT,
    ]
    if model:
        cmd += ["--model", model]
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)
    wall_ms = int((time.monotonic() - started) * 1000)

    all_calls: list[str] = []
    answer = ""
    num_turns = 0
    usage: dict[str, object] = {}
    cost = 0.0
    is_error = proc.returncode != 0
    seen_model: str | None = None
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            seen_model = str(event.get("model", "")) or None
        elif kind == "assistant":
            all_calls += _tool_uses(event.get("message"))
        elif kind == "result":
            answer = str(event.get("result", ""))
            num_turns = _as_int(event.get("num_turns"))
            raw_cost = event.get("total_cost_usd")
            cost = float(raw_cost) if isinstance(raw_cost, (int, float)) else 0.0
            raw_usage = event.get("usage")
            usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
            is_error = is_error or bool(event.get("is_error"))
            wall_ms = _as_int(event.get("duration_ms")) or wall_ms
    if not answer and proc.stderr:
        answer = proc.stderr.strip()[-500:]
        is_error = True

    tool_calls = [c.removeprefix(TOOL_PREFIX) for c in all_calls if c.startswith(TOOL_PREFIX)]
    expected_set = set(task.expect_tools)
    called_set = set(tool_calls)
    outcome = Outcome(
        name=task.name,
        passed=not is_error and grade(task.expected, answer),
        tool_calls=tool_calls,
        unexpected_tools=sorted(called_set - expected_set),
        missing_tools=sorted(expected_set - called_set),
        builtin_calls=[c for c in all_calls if not c.startswith(TOOL_PREFIX)],
        num_turns=num_turns,
        input_tokens=_as_int(usage.get("input_tokens"))
        + _as_int(usage.get("cache_read_input_tokens"))
        + _as_int(usage.get("cache_creation_input_tokens")),
        output_tokens=_as_int(usage.get("output_tokens")),
        cache_read_tokens=_as_int(usage.get("cache_read_input_tokens")),
        cost_usd=cost,
        duration_ms=wall_ms,
        is_error=is_error,
        answer=answer[-800:],
    )
    return outcome, seen_model


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def print_table(outcomes: list[Outcome]) -> None:
    header = f"{'task':28} {'pass':4} {'calls':>5} {'extra':>5} {'in_tok':>8} {'out_tok':>7}"
    print(f"\n{header} {'ms':>7}  tools")
    for o in outcomes:
        mark = "ok" if o.passed else ("ERR" if o.is_error else "FAIL")
        print(
            f"{o.name:28} {mark:4} {len(o.tool_calls):>5} {len(o.unexpected_tools):>5} "
            f"{o.input_tokens:>8} {o.output_tokens:>7} {o.duration_ms:>7}  "
            + " > ".join(o.tool_calls)
        )
    passed = sum(o.passed for o in outcomes)
    extra = sum(len(o.unexpected_tools) for o in outcomes)
    calls = sum(len(o.tool_calls) for o in outcomes)
    cost = sum(o.cost_usd for o in outcomes)
    print(
        f"\npassed {passed}/{len(outcomes)}  tool calls {calls}  unexpected tool picks {extra}  "
        f"cost ${cost:.2f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=EVALS / "tasks.json")
    parser.add_argument("--only", action="append", default=[], help="task name; repeatable")
    parser.add_argument("--model", default=None, help="passed to claude --model")
    parser.add_argument("--budget-per-task", type=float, default=0.75, help="USD cap per task")
    parser.add_argument("--out", type=Path, default=None, help="result file (default by git sha)")
    args = parser.parse_args()

    tasks = load_tasks(args.tasks, list(args.only))
    sha = git("rev-parse", "--short", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    out = args.out or EVALS / "results" / f"{sha}{'-dirty' if dirty else ''}.json"

    # Pinned so the run does not depend on POSTGRES_* in the caller's shell
    # (testcontainers reads POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD).
    container = PostgresContainer(
        "postgres:18", username="pglens", password="pglens", dbname="pglens_eval", driver=None
    )
    with container:
        asyncio.run(seed(container, EVALS / "schema.sql"))
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "mcp.json"
            config_path.write_text(json.dumps(mcp_config(container)))
            cwd = Path(tmp) / "cwd"
            cwd.mkdir()
            outcomes: list[Outcome] = []
            model: str | None = None
            for task in tasks:
                print(f"running {task.name} ...", flush=True)
                outcome, seen_model = run_task(
                    task, config_path, cwd, args.model, args.budget_per_task
                )
                model = model or seen_model
                outcomes.append(outcome)

    print_table(outcomes)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "git_sha": sha,
                "dirty": dirty,
                "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "model": model,
                "claude_version": subprocess.run(
                    ["claude", "--version"], capture_output=True, text=True
                ).stdout.strip(),
                "tasks": len(outcomes),
                "passed": sum(o.passed for o in outcomes),
                "tool_calls": sum(len(o.tool_calls) for o in outcomes),
                "unexpected_tool_picks": sum(len(o.unexpected_tools) for o in outcomes),
                "builtin_calls": sum(len(o.builtin_calls) for o in outcomes),
                "input_tokens": sum(o.input_tokens for o in outcomes),
                "output_tokens": sum(o.output_tokens for o in outcomes),
                "cache_read_tokens": sum(o.cache_read_tokens for o in outcomes),
                "cost_usd": round(sum(o.cost_usd for o in outcomes), 4),
                "outcomes": [asdict(o) for o in outcomes],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")


if __name__ == "__main__":
    main()

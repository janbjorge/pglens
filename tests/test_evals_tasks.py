"""evals/tasks.json stays loadable and names only tools that exist.

The eval harness itself (evals/run.py) spends API tokens and is run by hand;
this guards the task file so a tool rename cannot silently break the harness.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pglens.adapters.mcp_adapter import mcp

TASKS = Path(__file__).resolve().parents[1] / "evals" / "tasks.json"


def _tasks() -> list[dict[str, object]]:
    tasks = json.loads(TASKS.read_text())
    assert isinstance(tasks, list) and len(tasks) >= 15
    return tasks


def test_every_task_has_the_required_keys() -> None:
    for task in _tasks():
        assert set(task) == {"name", "prompt", "expected", "expect_tools"}, task.get("name")
        assert isinstance(task["prompt"], str) and task["prompt"]
        expected = task["expected"]
        assert isinstance(expected, dict) and set(expected) in ({"contains"}, {"regex"}), task[
            "name"
        ]


def test_task_names_are_unique() -> None:
    names = [task["name"] for task in _tasks()]
    assert len(names) == len(set(names))


def test_expect_tools_are_registered_tools() -> None:
    registered = set(mcp._tool_manager._tools)
    for task in _tasks():
        expect_tools = task["expect_tools"]
        assert isinstance(expect_tools, list) and expect_tools, task["name"]
        unknown = set(expect_tools) - registered
        assert not unknown, f"{task['name']} expects unknown tools {unknown}"


def test_regex_expectations_compile() -> None:
    for task in _tasks():
        expected = task["expected"]
        assert isinstance(expected, dict)
        if "regex" in expected:
            pattern = expected["regex"]
            assert isinstance(pattern, str)
            re.compile(pattern)
        else:
            contains = expected["contains"]
            assert isinstance(contains, list) and all(isinstance(c, str) for c in contains)

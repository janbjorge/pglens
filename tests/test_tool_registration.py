"""The MCP tool surface: which tools exist, how they delegate, and what the schema says.

These tests catch a tool module that is no longer imported, an enum member that
is not wired to a database method, a parameter that reaches the client without a
description, and regrowth of the tool-definition budget.
"""

import json
import re
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import ListToolsResult

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.adapters.mcp_adapter import mcp, query_guide

EXPECTED_TOOLS = {
    "database_info",
    "list_objects",
    "describe_object",
    "find_join_path",
    "column_profile",
    "query",
    "explain_query",
    "health",
    "activity",
}
TOOL_MODULES = ("schema", "exploration", "query", "health")
# Compact JSON of every tool definition. Measured after the consolidation; stops regrowth.
DEFINITION_BUDGET_CHARS = 16_000


def _registered_tool_names() -> set[str]:
    return set(mcp._tool_manager._tools)


class TestToolRegistration:
    def test_surface_is_exactly_the_expected_tools(self) -> None:
        assert _registered_tool_names() == EXPECTED_TOOLS

    async def test_tool_count(self) -> None:
        assert len(await mcp.list_tools()) <= 10

    async def test_tool_definition_budget(self) -> None:
        total = sum(
            len(json.dumps(t.model_dump(exclude_none=True, by_alias=True), separators=(",", ":")))
            for t in await mcp.list_tools()
        )
        assert total < DEFINITION_BUDGET_CHARS, total


def _make_ctx(mock_db: MagicMock) -> MagicMock:
    """Build a fake MCP Context whose lifespan_context is mock_db."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = mock_db
    return ctx


def _mock_db() -> AsyncMock:
    """An AsyncpgDatabase stand-in whose methods return empty results of the right shape."""
    # spec= ensures only real methods exist, so a typo raises AttributeError.
    mock_db = AsyncMock(spec=AsyncpgDatabase)
    mock_db.database_info.return_value = {}
    mock_db.describe_table.return_value = {}
    mock_db.column_stats.return_value = {}
    mock_db.replication_status.return_value = {}
    mock_db.maintenance_progress.return_value = {}
    mock_db.find_join_path.return_value = {}
    mock_db.explain_query.return_value = ""
    mock_db.list_schemas.return_value = []
    mock_db.list_tables.return_value = []
    mock_db.list_views.return_value = []
    mock_db.list_indexes.return_value = []
    mock_db.list_functions.return_value = []
    mock_db.list_extensions.return_value = []
    mock_db.list_triggers.return_value = []
    mock_db.list_policies.return_value = []
    mock_db.search_columns.return_value = []
    mock_db.search_enum_values.return_value = []
    mock_db.matview_status.return_value = []
    mock_db.sequence_health.return_value = []
    mock_db.column_values.return_value = []
    mock_db.query.return_value = []
    mock_db.table_health.return_value = []
    mock_db.table_sizes.return_value = []
    mock_db.unused_indexes.return_value = []
    mock_db.io_stats.return_value = []
    mock_db.slow_queries.return_value = []
    mock_db.active_queries.return_value = []
    mock_db.blocking_locks.return_value = []
    mock_db.object_dependencies.return_value = []
    return mock_db


async def _run(tool_name: str, mock_db: AsyncMock, **kwargs: object) -> object:
    """Call a tool function with every tool module's db() patched to mock_db."""
    tool = mcp._tool_manager._tools[tool_name]
    mock_registry = MagicMock()
    mock_registry.names.return_value = ["default"]
    patches = [
        patch(f"pglens.adapters.tools.{mod}.db", return_value=mock_db) for mod in TOOL_MODULES
    ]
    patches.append(patch("pglens.adapters.tools.schema.databases", return_value=mock_registry))
    for p in patches:
        p.start()
    try:
        return await tool.fn(ctx=_make_ctx(mock_db), **kwargs)
    finally:
        for p in patches:
            p.stop()


def _called(mock_db: AsyncMock) -> list[str]:
    return [name for name, _args, _kwargs in mock_db.method_calls]


class TestToolWiring:
    """Every tool, and every enum member of the dispatching tools, reaches the right
    AsyncpgDatabase methods in the documented order."""

    @pytest.mark.parametrize(
        ("tool_name", "kwargs", "expected_methods"),
        [
            ("database_info", {}, ["database_info", "list_schemas"]),
            ("list_objects", {"kind": "table"}, ["list_tables"]),
            ("list_objects", {"kind": "view"}, ["list_views"]),
            ("list_objects", {"kind": "matview"}, ["matview_status"]),
            ("list_objects", {"kind": "index"}, ["list_indexes"]),
            ("list_objects", {"kind": "function"}, ["list_functions"]),
            ("list_objects", {"kind": "sequence"}, ["sequence_health"]),
            ("list_objects", {"kind": "enum"}, ["search_enum_values"]),
            ("list_objects", {"kind": "extension"}, ["list_extensions"]),
            ("list_objects", {"kind": "column", "pattern": "email"}, ["search_columns"]),
            (
                "describe_object",
                {"name": "orders"},
                ["describe_table", "list_triggers", "list_policies", "object_dependencies"],
            ),
            (
                "describe_object",
                {"name": "v", "kind": "view"},
                ["list_views", "object_dependencies"],
            ),
            (
                "describe_object",
                {"name": "m", "kind": "matview"},
                ["matview_status", "object_dependencies"],
            ),
            (
                "describe_object",
                {"name": "f", "kind": "function"},
                ["list_functions", "object_dependencies"],
            ),
            (
                "describe_object",
                {"name": "s", "kind": "sequence"},
                ["sequence_health", "object_dependencies"],
            ),
            (
                "describe_object",
                {"name": "i", "kind": "index"},
                ["list_indexes", "object_dependencies"],
            ),
            (
                "find_join_path",
                {"source_table": "users", "target_table": "items"},
                ["find_join_path"],
            ),
            (
                "column_profile",
                {"table_name": "orders", "column_name": "status"},
                ["column_stats", "column_values"],
            ),
            ("query", {"sql": "SELECT 1"}, ["query"]),
            ("explain_query", {"sql": "SELECT 1"}, ["explain_query"]),
            (
                "health",
                {},
                [
                    "table_health",
                    "table_sizes",
                    "unused_indexes",
                    "sequence_health",
                    "matview_status",
                    "io_stats",
                    "slow_queries",
                ],
            ),
            ("health", {"checks": ["io"]}, ["io_stats"]),
            (
                "health",
                {"checks": ["slow_queries", "tables", "tables"]},
                ["slow_queries", "table_health"],
            ),
            (
                "activity",
                {},
                ["active_queries", "blocking_locks", "maintenance_progress", "replication_status"],
            ),
            ("activity", {"views": ["locks"]}, ["blocking_locks"]),
        ],
    )
    async def test_tool_delegates_in_order(
        self, tool_name: str, kwargs: dict[str, object], expected_methods: list[str]
    ) -> None:
        mock_db = _mock_db()
        await _run(tool_name, mock_db, **kwargs)
        assert _called(mock_db) == expected_methods, tool_name

    async def test_list_objects_pattern_reads_a_full_page_then_filters(self) -> None:
        mock_db = _mock_db()
        mock_db.list_tables.return_value = [{"table_name": "orders"}, {"table_name": "users"}]
        result = await _run("list_objects", mock_db, kind="table", pattern="ORD", limit=5)
        mock_db.list_tables.assert_awaited_once_with("public", 1000)
        assert result == [{"table_name": "orders"}]

    async def test_list_objects_names_detail_keeps_identifiers_only(self) -> None:
        mock_db = _mock_db()
        mock_db.list_tables.return_value = [{"table_name": "orders", "estimated_row_count": 3}]
        result = await _run("list_objects", mock_db, kind="table", detail="names")
        assert result == [{"table_name": "orders"}]

    async def test_describe_object_non_table_filters_the_catalogue_by_name(self) -> None:
        mock_db = _mock_db()
        mock_db.list_views.return_value = [
            {"view_name": "active_orders", "definition": "SELECT 1"},
            {"view_name": "other", "definition": "SELECT 2"},
        ]
        mock_db.object_dependencies.return_value = [{"dependent_name": "x"}]
        result = await _run("describe_object", mock_db, name="active_orders", kind="view")
        mock_db.list_views.assert_awaited_once_with("public", 1000, "full")
        assert result == {
            "object": "public.active_orders",
            "kind": "view",
            "definitions": [{"view_name": "active_orders", "definition": "SELECT 1"}],
            "dependents": [{"dependent_name": "x"}],
        }

    async def test_health_passes_limit_to_every_check(self) -> None:
        mock_db = _mock_db()
        await _run("health", mock_db, checks=["slow_queries", "sizes"], schema="s", limit=7)
        mock_db.slow_queries.assert_awaited_once_with(7)
        mock_db.table_sizes.assert_awaited_once_with("s", 7)

    async def test_column_profile_returns_both_sections(self) -> None:
        mock_db = _mock_db()
        mock_db.column_stats.return_value = {"null_fraction": 0.0}
        mock_db.column_values.return_value = [{"value": "pending", "frequency": 2}]
        result = await _run("column_profile", mock_db, table_name="orders", column_name="status")
        assert result == {
            "column": "public.orders.status",
            "stats": {"null_fraction": 0.0},
            "top_values": [{"value": "pending", "frequency": 2}],
        }


async def _properties(tool_name: str) -> dict[str, dict[str, object]]:
    """Return the input-schema properties a client sees for one tool, via tools/list."""
    tools = {t.name: t for t in await mcp.list_tools()}
    return {name: dict(prop) for name, prop in tools[tool_name].input_schema["properties"].items()}


def _array_items(prop: dict[str, object]) -> dict[str, object]:
    """The items schema of a `list[...] | None` property."""
    branches = prop["anyOf"]
    assert isinstance(branches, list)
    for branch in branches:
        if isinstance(branch, dict) and branch.get("type") == "array":
            return cast(dict[str, object], branch["items"])
    raise AssertionError(f"no array branch in {prop}")


class TestToolSchemas:
    """The schema is the only thing the model sees when it picks arguments.

    The mcp SDK builds it from pydantic FieldInfo only, so a bare string in
    Annotated is dropped without warning. These tests pin what must reach the client.
    """

    async def test_every_parameter_has_a_description(self) -> None:
        missing = [
            f"{tool.name}.{name}"
            for tool in await mcp.list_tools()
            for name, prop in tool.input_schema["properties"].items()
            if not prop.get("description")
        ]
        assert not missing, f"Parameters without a description: {missing}"

    async def test_describe_object_kind_is_an_enum(self) -> None:
        props = await _properties("describe_object")
        assert props["kind"]["enum"] == [
            "table",
            "view",
            "matview",
            "function",
            "sequence",
            "index",
        ]
        assert props["kind"]["default"] == "table"

    async def test_list_objects_kind_is_an_enum(self) -> None:
        props = await _properties("list_objects")
        assert props["kind"]["enum"] == [
            "table",
            "view",
            "matview",
            "index",
            "function",
            "sequence",
            "enum",
            "extension",
            "column",
        ]
        assert "default" not in props["kind"]

    async def test_health_checks_and_activity_views_are_enums(self) -> None:
        checks = _array_items((await _properties("health"))["checks"])
        assert checks["enum"] == [
            "tables",
            "sizes",
            "indexes",
            "sequences",
            "matviews",
            "io",
            "slow_queries",
        ]
        views = _array_items((await _properties("activity"))["views"])
        assert views["enum"] == ["sessions", "locks", "maintenance", "replication"]

    @pytest.mark.parametrize("tool_name", ["list_objects", "describe_object"])
    async def test_detail_is_an_enum_defaulting_to_summary(self, tool_name: str) -> None:
        prop = (await _properties(tool_name))["detail"]
        assert prop["enum"] == ["names", "summary", "full"]
        assert prop["default"] == "summary"

    @pytest.mark.parametrize(
        "tool_name, param, minimum, maximum",
        [
            ("column_profile", "top_n", 1, 100),
            ("health", "limit", 1, 100),
            ("list_objects", "limit", 1, 1000),
            ("find_join_path", "max_depth", 1, 6),
            ("query", "limit", 1, 500),
            ("query", "offset", 0, None),
        ],
    )
    async def test_ranges_are_in_schema(
        self, tool_name: str, param: str, minimum: int, maximum: int | None
    ) -> None:
        prop = (await _properties(tool_name))[param]
        assert prop.get("minimum") == minimum
        assert prop.get("maximum") == maximum

    @pytest.mark.parametrize("tool_name", ["query", "explain_query"])
    async def test_sql_description_mentions_placeholders(self, tool_name: str) -> None:
        description = (await _properties(tool_name))["sql"]["description"]
        assert isinstance(description, str)
        assert "$1" in description

    async def test_out_of_range_argument_is_rejected_before_the_db_call(self) -> None:
        """The range is enforced by the SDK's argument validation, not only advertised."""
        mock_db = AsyncMock(spec=AsyncpgDatabase)
        with (
            patch("pglens.adapters.tools.exploration.db", return_value=mock_db),
            pytest.raises(Exception, match="(?i)less than or equal to 100"),
        ):
            await mcp._tool_manager.call_tool(
                "column_profile",
                {"table_name": "users", "column_name": "bio", "top_n": 1000},
                _make_ctx(mock_db),
            )
        assert mock_db.method_calls == []


class TestServerInstructions:
    """Server instructions are the only guidance a client forwards to the model on connect.

    Prompts are user-invoked, so the workflow has to travel as instructions too.
    """

    def test_server_has_instructions(self) -> None:
        assert mcp.instructions
        # Claude Code cuts server instructions at 4,096 characters.
        assert len(mcp.instructions) <= 4096

    def test_query_guide_matches_instructions(self) -> None:
        assert query_guide() == mcp.instructions

    def test_instructions_name_only_real_tools(self) -> None:
        """Every backticked identifier in the instructions is a registered tool."""
        assert mcp.instructions is not None
        named = set(re.findall(r"`([a-z_]+)`", mcp.instructions))
        unknown = named - _registered_tool_names()
        assert not unknown, f"Instructions name tools that do not exist: {unknown}"


class TestToolAnnotations:
    """Every tool is read-only, and clients must be told so in the tools/list response."""

    async def test_every_tool_is_annotated_read_only(self) -> None:
        for tool in await mcp.list_tools():
            assert tool.annotations is not None, f"{tool.name} has no annotations"
            assert tool.annotations.read_only_hint is True, tool.name
            assert tool.annotations.destructive_hint is False, tool.name
            assert tool.annotations.idempotent_hint is True, tool.name
            assert tool.annotations.open_world_hint is False, tool.name

    async def test_read_only_hint_survives_serialization(self) -> None:
        """The wire form of tools/list carries the camelCase readOnlyHint."""
        payload = ListToolsResult(tools=await mcp.list_tools()).model_dump(
            by_alias=True, exclude_none=True
        )
        hints = {tool["name"]: tool["annotations"]["readOnlyHint"] for tool in payload["tools"]}
        assert hints and all(hints.values()), hints


class TestAlwaysLoad:
    async def test_always_load_set(self) -> None:
        """Claude Code loads these upfront; every other tool stays deferred behind tool search."""
        tools = await mcp.list_tools()
        always = {
            tool.name
            for tool in tools
            if tool.meta is not None and tool.meta.get("anthropic/alwaysLoad") is True
        }
        assert always == {"query", "describe_object", "list_objects", "column_profile"}
        assert len(always) <= 5
        # The wire form carries the key under _meta.
        payload = ListToolsResult(tools=tools).model_dump(by_alias=True, exclude_none=True)
        wire = {tool["name"] for tool in payload["tools"] if "_meta" in tool}
        assert wire == always

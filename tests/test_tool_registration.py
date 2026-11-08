"""Verify all MCP tools are registered and wired correctly.

These tests catch:
- Missing imports in mcp_adapter.py (tool silently not registered)
- Decorator typos or misconfigurations
- Parameter name mismatches between tool layer and AsyncpgDatabase
"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import ListToolsResult

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.adapters.mcp_adapter import mcp, query_guide

# Every tool that should be registered, mapped to the db method it delegates to.
# Tools that do not delegate to AsyncpgDatabase (e.g. registry-level tools) map to None.
EXPECTED_TOOLS: dict[str, str | None] = {
    # schema.py
    "database_info": "database_info",
    "list_schemas": "list_schemas",
    "list_tables": "list_tables",
    "list_views": "list_views",
    "list_extensions": "list_extensions",
    "describe_table": "describe_table",
    "find_join_path": "find_join_path",
    "list_indexes": "list_indexes",
    "list_functions": "list_functions",
    "list_triggers": "list_triggers",
    "list_policies": "list_policies",
    # exploration.py
    "sample_rows": "sample_rows",
    "column_values": "column_values",
    "column_stats": "column_stats",
    "search_data": "search_data",
    "search_columns": "search_columns",
    "search_enum_values": "search_enum_values",
    # query.py
    "explain_query": "explain_query",
    "query": "query",
    # health.py
    "table_health": "table_health",
    "table_sizes": "table_sizes",
    "unused_indexes": "unused_indexes",
    "slow_queries": "slow_queries",
    "replication_status": "replication_status",
    "active_queries": "active_queries",
    "blocking_locks": "blocking_locks",
    "sequence_health": "sequence_health",
    "matview_status": "matview_status",
    "io_stats": "io_stats",
    "maintenance_progress": "maintenance_progress",
    # safety.py
    "object_dependencies": "object_dependencies",
}


def _registered_tool_names() -> set[str]:
    """Extract tool names from the MCP server."""
    # MCPServer stores tools in _tool_manager._tools dict
    tools = mcp._tool_manager._tools
    return set(tools.keys())


class TestToolRegistration:
    def test_all_expected_tools_registered(self) -> None:
        registered = _registered_tool_names()
        missing = set(EXPECTED_TOOLS) - registered
        assert not missing, f"Tools not registered: {missing}"

    def test_no_unexpected_tools(self) -> None:
        registered = _registered_tool_names()
        unexpected = registered - set(EXPECTED_TOOLS)
        assert not unexpected, f"Unexpected tools registered: {unexpected}"


def _make_ctx(mock_db: MagicMock) -> MagicMock:
    """Build a fake MCP Context whose lifespan_context is mock_db."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = mock_db
    return ctx


class TestToolWiring:
    """Verify each tool delegates to the correct AsyncpgDatabase method with the right args."""

    @pytest.mark.parametrize(
        "tool_name, db_method, kwargs",
        [
            ("database_info", "database_info", {}),
            ("list_schemas", "list_schemas", {}),
            ("list_tables", "list_tables", {"schema": "public"}),
            ("list_views", "list_views", {"schema": "myschema"}),
            ("list_extensions", "list_extensions", {}),
            ("describe_table", "describe_table", {"table_name": "orders", "schema": "public"}),
            (
                "find_join_path",
                "find_join_path",
                {
                    "source_table": "users",
                    "target_table": "items",
                    "schema": "public",
                    "max_depth": 4,
                },
            ),
            ("list_indexes", "list_indexes", {"schema": "public"}),
            ("list_functions", "list_functions", {"schema": "public"}),
            ("list_triggers", "list_triggers", {"table_name": "orders", "schema": "public"}),
            ("list_policies", "list_policies", {"table_name": "orders", "schema": "public"}),
            (
                "sample_rows",
                "sample_rows",
                {"table_name": "orders", "n": 5, "schema": "public"},
            ),
            (
                "column_values",
                "column_values",
                {"table_name": "orders", "column_name": "status", "top_n": 20, "schema": "public"},
            ),
            (
                "column_stats",
                "column_stats",
                {"table_name": "orders", "column_name": "total", "schema": "public"},
            ),
            (
                "search_data",
                "search_data",
                {"table_name": "users", "keyword": "test", "schema": "public"},
            ),
            ("search_columns", "search_columns", {"keyword": "email", "schema": "public"}),
            ("search_enum_values", "search_enum_values", {"keyword": ""}),
            ("explain_query", "explain_query", {"sql": "SELECT 1"}),
            ("query", "query", {"sql": "SELECT 1"}),
            ("table_health", "table_health", {"schema": "public"}),
            ("table_sizes", "table_sizes", {"schema": "public"}),
            ("unused_indexes", "unused_indexes", {"schema": "public"}),
            ("slow_queries", "slow_queries", {"limit": 20}),
            ("replication_status", "replication_status", {}),
            ("active_queries", "active_queries", {}),
            ("blocking_locks", "blocking_locks", {}),
            ("sequence_health", "sequence_health", {"schema": "public"}),
            ("matview_status", "matview_status", {"schema": "public"}),
            ("io_stats", "io_stats", {}),
            ("maintenance_progress", "maintenance_progress", {}),
            (
                "object_dependencies",
                "object_dependencies",
                {"object_name": "users", "object_type": "table", "schema": "public"},
            ),
        ],
    )
    async def test_tool_delegates_to_db_method(
        self, tool_name: str, db_method: str, kwargs: dict[str, object]
    ) -> None:
        # spec=AsyncpgDatabase ensures only real methods exist on the mock,
        # so calling a non-existent method raises AttributeError.
        mock_db = AsyncMock(spec=AsyncpgDatabase)
        ctx = _make_ctx(mock_db)

        tool = mcp._tool_manager._tools[tool_name]
        mock_registry = MagicMock()
        mock_registry.names.return_value = ["default"]
        patches = [
            patch(f"pglens.adapters.tools.{mod}.db", return_value=mock_db)
            for mod in ("schema", "exploration", "query", "health", "safety")
        ]
        # database_info also reads the alias registry, which is not an AsyncpgDatabase call
        patches.append(patch("pglens.adapters.tools.schema.databases", return_value=mock_registry))
        for p in patches:
            p.start()
        try:
            await tool.fn(ctx=ctx, **kwargs)
        finally:
            for p in patches:
                p.stop()

        # Verify exactly one method was called, and it was the expected one.
        called_methods = [name for name, _args, _kwargs in mock_db.method_calls]
        assert called_methods == [db_method], (
            f"{tool_name}: expected call to db.{db_method}(), got {called_methods}"
        )


async def _properties(tool_name: str) -> dict[str, dict[str, object]]:
    """Return the input-schema properties a client sees for one tool, via tools/list."""
    tools = {t.name: t for t in await mcp.list_tools()}
    return {name: dict(prop) for name, prop in tools[tool_name].input_schema["properties"].items()}


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

    async def test_object_type_is_an_enum(self) -> None:
        props = await _properties("object_dependencies")
        assert props["object_type"]["enum"] == [
            "table",
            "view",
            "matview",
            "function",
            "sequence",
            "index",
        ]

    @pytest.mark.parametrize(
        "tool_name",
        ["list_views", "list_indexes", "list_functions", "matview_status", "describe_table"],
    )
    async def test_detail_is_an_enum_defaulting_to_summary(self, tool_name: str) -> None:
        prop = (await _properties(tool_name))["detail"]
        assert prop["enum"] == ["names", "summary", "full"]
        assert prop["default"] == "summary"

    @pytest.mark.parametrize(
        "tool_name, param, minimum, maximum",
        [
            ("sample_rows", "n", 1, 100),
            ("column_values", "top_n", 1, 100),
            ("slow_queries", "limit", 1, 100),
            ("find_join_path", "max_depth", 1, 6),
            ("query", "limit", 1, 500),
            ("query", "offset", 0, None),
            ("list_tables", "limit", 1, 1000),
            ("search_columns", "limit", 1, 1000),
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
                "sample_rows", {"table_name": "users", "n": 1000}, _make_ctx(mock_db)
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
        assert always == {"query", "describe_table", "list_tables", "column_values"}
        assert len(always) <= 5
        # The wire form carries the key under _meta.
        payload = ListToolsResult(tools=tools).model_dump(by_alias=True, exclude_none=True)
        wire = {tool["name"] for tool in payload["tools"] if "_meta" in tool}
        assert wire == always

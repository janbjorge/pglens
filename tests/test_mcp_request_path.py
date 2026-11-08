"""End-to-end tests through the MCP request path against a real PostgreSQL.

tests/test_integration.py calls AsyncpgDatabase directly, and
tests/test_tool_registration.py calls tool functions with a mocked context.
Neither exercises what the mcp server itself does on an inbound tools/call:
build a Context from the lifespan result, inject it into the tool function,
validate arguments against the generated schema, and convert the result.

That is exactly the layer the mcp 1.x -> 2.x move rewrote (FastMCP -> MCPServer,
Context dropping its ServerSession type parameter), so it gets real coverage
here: a break shows up as a failing test instead of a server that dies at
startup and reports a bare -32000 to the client.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import asyncpg
import pytest
from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import Context

from pglens.database import AsyncpgDatabase
from pglens.server import DEFAULT_DB, Databases, mcp

pytestmark = pytest.mark.usefixtures("pool")


def _context(pool: asyncpg.Pool) -> Context[Databases, object]:
    """Build the Context an inbound tools/call would receive.

    The session is a mock: pglens tools never send server-to-client requests,
    they only read `ctx.request_context.lifespan_context`.
    """
    registry = Databases({DEFAULT_DB: AsyncpgDatabase(pool=pool)}, DEFAULT_DB)
    request_context: ServerRequestContext[Databases, object] = ServerRequestContext(
        session=MagicMock(),
        lifespan_context=registry,
        protocol_version="2025-06-18",
        method="tools/call",
        request_id="1",
    )
    return Context(request_context=request_context, mcp_server=mcp)


class TestToolsCall:
    async def test_call_tool_with_context_injection(self, pool: asyncpg.Pool) -> None:
        result = await mcp._tool_manager.call_tool(
            "list_objects",
            {"kind": "table", "schema": "public"},
            _context(pool),
            convert_result=True,
        )
        assert "users" in str(result)

    async def test_call_tool_defaults_are_applied(self, pool: asyncpg.Pool) -> None:
        """Omitted optional args (schema, database) come from the tool signature."""
        result = await mcp._tool_manager.call_tool(
            "describe_object", {"name": "orders"}, _context(pool), convert_result=True
        )
        assert "user_id" in str(result)

    async def test_call_tool_runs_user_sql(self, pool: asyncpg.Pool) -> None:
        result = await mcp._tool_manager.call_tool(
            "query", {"sql": "SELECT username FROM users ORDER BY id"}, _context(pool), True
        )
        assert "alice" in str(result)

    async def test_call_tool_rejects_bad_arguments(self, pool: asyncpg.Pool) -> None:
        """Argument validation still happens at the tool boundary."""
        with pytest.raises(Exception, match="(?i)valid|error"):
            await mcp._tool_manager.call_tool(
                "column_profile",
                {"table_name": "users", "column_name": "bio", "top_n": "many"},
                _context(pool),
                True,
            )

    async def test_unknown_tool_raises(self, pool: asyncpg.Pool) -> None:
        with pytest.raises(Exception, match="(?i)unknown tool"):
            await mcp._tool_manager.call_tool("drop_everything", {}, _context(pool), True)


async def _call(pool: asyncpg.Pool, name: str, arguments: dict[str, object]) -> object:
    """Run a tool through the server's call path and return the Python result."""
    return await mcp._tool_manager.call_tool(name, arguments, _context(pool))


def _rows(result: object) -> list[dict[str, object]]:
    assert isinstance(result, list)
    return [cast(dict[str, object], row) for row in result]


class TestConsolidatedTools:
    """One end-to-end call per tool that dispatches on an enum, against the seeded schema."""

    async def test_database_info_includes_schemas(self, pool: asyncpg.Pool) -> None:
        info = await _call(pool, "database_info", {})
        assert isinstance(info, dict)
        schemas = info["schemas"]
        assert isinstance(schemas, list)
        assert "public" in {s["schema_name"] for s in schemas}

    async def test_list_objects_column_search(self, pool: asyncpg.Pool) -> None:
        rows = _rows(await _call(pool, "list_objects", {"kind": "column", "pattern": "email"}))
        assert any(r["table_name"] == "users" and r["column_name"] == "email" for r in rows)

    async def test_list_objects_pattern_on_tables(self, pool: asyncpg.Pool) -> None:
        rows = _rows(await _call(pool, "list_objects", {"kind": "table", "pattern": "order"}))
        assert {r["table_name"] for r in rows} == {"orders", "order_items"}

    @pytest.mark.parametrize(
        ("kind", "expected_name"),
        [
            ("view", "active_orders"),
            ("matview", "order_summary"),
            ("index", "idx_users_bio"),
            ("function", "update_modified_column"),
            ("sequence", "countdown"),
            ("enum", "order_status"),
            ("extension", "plpgsql"),
        ],
    )
    async def test_list_objects_every_kind(
        self, pool: asyncpg.Pool, kind: str, expected_name: str
    ) -> None:
        rows = _rows(await _call(pool, "list_objects", {"kind": kind, "detail": "names"}))
        assert expected_name in {next(iter(r.values())) for r in rows}

    async def test_describe_object_table_has_triggers_policies_dependents(
        self, pool: asyncpg.Pool
    ) -> None:
        result = await _call(pool, "describe_object", {"name": "orders"})
        assert isinstance(result, dict)
        assert [t["trigger_name"] for t in result["triggers"]] == ["trg_orders_modified"]
        assert [p["policy_name"] for p in result["policies"]] == ["orders_owner_policy"]
        assert "active_orders" in {d["dependent_name"] for d in result["dependents"]}

    async def test_describe_object_view(self, pool: asyncpg.Pool) -> None:
        result = await _call(pool, "describe_object", {"name": "active_orders", "kind": "view"})
        assert isinstance(result, dict)
        [definition] = result["definitions"]
        assert "orders" in definition["definition"]

    async def test_column_profile(self, pool: asyncpg.Pool) -> None:
        result = await _call(
            pool, "column_profile", {"table_name": "orders", "column_name": "status"}
        )
        assert isinstance(result, dict)
        assert set(result) == {"column", "stats", "top_values"}
        assert "pending" in {v["value"] for v in result["top_values"]}

    async def test_health_all_and_one(self, pool: asyncpg.Pool) -> None:
        everything = await _call(pool, "health", {})
        assert isinstance(everything, dict)
        assert set(everything) == {
            "tables",
            "sizes",
            "indexes",
            "sequences",
            "matviews",
            "io",
            "slow_queries",
        }
        assert "idx_users_bio" in {i["index_name"] for i in everything["indexes"]}
        only_io = await _call(pool, "health", {"checks": ["io"]})
        assert isinstance(only_io, dict) and set(only_io) == {"io"}

    async def test_activity_all(self, pool: asyncpg.Pool) -> None:
        result = await _call(pool, "activity", {})
        assert isinstance(result, dict)
        assert set(result) == {"sessions", "locks", "maintenance", "replication"}


class TestPromptGet:
    async def test_query_guide_prompt_renders(self, pool: asyncpg.Pool) -> None:
        result = await mcp._prompt_manager.render_prompt("query_guide", {}, _context(pool))
        assert "read-only" in str(result)

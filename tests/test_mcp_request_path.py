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

from unittest.mock import MagicMock

import asyncpg
import pytest
from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import Context

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.adapters.mcp_adapter import DEFAULT_DB, Databases, mcp

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
            "list_tables", {"schema": "public"}, _context(pool), convert_result=True
        )
        assert "users" in str(result)

    async def test_call_tool_defaults_are_applied(self, pool: asyncpg.Pool) -> None:
        """Omitted optional args (schema, database) come from the tool signature."""
        result = await mcp._tool_manager.call_tool(
            "describe_table", {"table_name": "orders"}, _context(pool), convert_result=True
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
                "sample_rows", {"table_name": "users", "n": "many"}, _context(pool), True
            )

    async def test_unknown_tool_raises(self, pool: asyncpg.Pool) -> None:
        with pytest.raises(Exception, match="(?i)unknown tool"):
            await mcp._tool_manager.call_tool("drop_everything", {}, _context(pool), True)


class TestPromptGet:
    async def test_query_guide_prompt_renders(self, pool: asyncpg.Pool) -> None:
        result = await mcp._prompt_manager.render_prompt("query_guide", {}, _context(pool))
        assert "read-only" in str(result)

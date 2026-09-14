"""Multi-database registry: alias resolution, env discovery, alias exposure."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.adapters.mcp_adapter import (
    DEFAULT_DB,
    Databases,
    db,
    mcp,
)


def _registry(*aliases: str) -> Databases:
    return Databases({a: AsyncpgDatabase(MagicMock()) for a in aliases})


class TestDatabasesRegistry:
    def test_get_default_when_none(self) -> None:
        reg = _registry(DEFAULT_DB)
        assert reg.get(None) is reg.databases[DEFAULT_DB]

    def test_get_named(self) -> None:
        reg = _registry(DEFAULT_DB, "azure_sys")
        assert reg.get("azure_sys") is reg.databases["azure_sys"]

    def test_get_unknown_raises(self) -> None:
        reg = _registry(DEFAULT_DB, "azure_sys")
        with pytest.raises(ValueError, match="missing"):
            reg.get("missing")

    def test_names_sorted(self) -> None:
        reg = _registry("zeta", DEFAULT_DB, "alpha")
        assert reg.names() == ["alpha", DEFAULT_DB, "zeta"]


class TestDbHelper:
    def test_db_routes_by_alias(self) -> None:
        reg = _registry(DEFAULT_DB, "azure_sys")
        ctx = MagicMock()
        ctx.request_context.lifespan_context = reg
        assert db(ctx) is reg.databases[DEFAULT_DB]
        assert db(ctx, "azure_sys") is reg.databases["azure_sys"]


class TestDatabaseInfoAliases:
    async def test_database_info_includes_available_databases(self) -> None:
        primary = AsyncMock(spec=AsyncpgDatabase)
        primary.database_info.return_value = {"database_name": "app"}
        reg = Databases({DEFAULT_DB: primary, "azure_sys": AsyncMock(spec=AsyncpgDatabase)})
        ctx = MagicMock()
        ctx.request_context.lifespan_context = reg
        tool = mcp._tool_manager._tools["database_info"]
        result = await tool.fn(ctx=ctx)
        assert result["database_name"] == "app"
        assert result["available_databases"] == ["azure_sys", DEFAULT_DB]


class TestToolForwardsDatabaseArg:
    """Smoke test: a representative tool actually passes `database` through `db()`."""

    async def test_query_uses_named_database(self) -> None:
        primary = AsyncMock(spec=AsyncpgDatabase)
        azure = AsyncMock(spec=AsyncpgDatabase)
        primary.query.return_value = ["primary"]
        azure.query.return_value = ["azure"]

        reg = Databases({DEFAULT_DB: primary, "azure_sys": azure})
        ctx = MagicMock()
        ctx.request_context.lifespan_context = reg

        tool = mcp._tool_manager._tools["query"]
        out_default = await tool.fn(ctx=ctx, sql="SELECT 1")
        out_azure = await tool.fn(ctx=ctx, sql="SELECT 1", database="azure_sys")

        assert out_default == ["primary"]
        assert out_azure == ["azure"]
        primary.query.assert_awaited_once()
        azure.query.assert_awaited_once()

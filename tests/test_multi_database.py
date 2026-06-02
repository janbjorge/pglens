"""Multi-database registry: alias resolution, env discovery, list_databases tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.adapters.mcp_adapter import (
    DEFAULT_DB,
    Databases,
    _collect_databases,
    db,
    mcp,
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os as _os

    for key in list(_os.environ):
        if key.startswith("PGLENS_") or key == "PGDATABASE":
            monkeypatch.delenv(key, raising=False)


def _registry(*aliases: str) -> Databases:
    return Databases({a: AsyncpgDatabase(MagicMock()) for a in aliases})


class TestDatabasesRegistry:
    def test_get_default_when_none(self) -> None:
        reg = _registry(DEFAULT_DB)
        assert reg.get(None) is reg.databases[DEFAULT_DB]

    def test_get_named(self) -> None:
        reg = _registry(DEFAULT_DB, "azure_sys")
        assert reg.get("azure_sys") is reg.databases["azure_sys"]

    def test_get_lowercases(self) -> None:
        reg = _registry(DEFAULT_DB, "azure_sys")
        assert reg.get("AZURE_SYS") is reg.databases["azure_sys"]

    def test_get_unknown_raises(self) -> None:
        reg = _registry(DEFAULT_DB, "azure_sys")
        with pytest.raises(KeyError, match="missing"):
            reg.get("missing")

    def test_names_sorted(self) -> None:
        reg = _registry("zeta", DEFAULT_DB, "alpha")
        assert reg.names() == ["alpha", DEFAULT_DB, "zeta"]


class TestCollectDatabases:
    def test_no_env_returns_single_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        names, default = _collect_databases()
        assert names == [DEFAULT_DB]
        assert default == DEFAULT_DB

    def test_pgdatabase_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGDATABASE", "app")
        names, default = _collect_databases()
        assert names == ["app"]
        assert default == "app"

    def test_pgdatabase_plus_extras(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGDATABASE", "app")
        monkeypatch.setenv("PGLENS_DATABASES", "azure_sys, Analytics")
        names, default = _collect_databases()
        assert names == ["app", "azure_sys", "analytics"]
        assert default == "app"

    def test_pgdatabase_dedupes_from_extras(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGDATABASE", "app")
        monkeypatch.setenv("PGLENS_DATABASES", "app,azure_sys")
        names, _ = _collect_databases()
        assert names == ["app", "azure_sys"]

    def test_extras_only_without_pgdatabase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGLENS_DATABASES", "app, azure_sys ,Analytics")
        names, default = _collect_databases()
        # Without PGDATABASE the first listed alias becomes the default.
        assert names == ["app", "azure_sys", "analytics"]
        assert default == "app"

    def test_pglens_databases_dedupes_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGLENS_DATABASES", "app,app,azure_sys")
        names, _ = _collect_databases()
        assert names == ["app", "azure_sys"]

    def test_pgdatabase_lowercased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGDATABASE", "MyApp")
        names, default = _collect_databases()
        assert names == ["myapp"]
        assert default == "myapp"


class TestDbHelper:
    def test_db_routes_by_alias(self) -> None:
        reg = _registry(DEFAULT_DB, "azure_sys")
        ctx = MagicMock()
        ctx.request_context.lifespan_context = reg
        assert db(ctx) is reg.databases[DEFAULT_DB]
        assert db(ctx, "azure_sys") is reg.databases["azure_sys"]


class TestListDatabasesTool:
    async def test_returns_alias_names(self) -> None:
        reg = _registry(DEFAULT_DB, "azure_sys")
        ctx = MagicMock()
        ctx.request_context.lifespan_context = reg
        tool = mcp._tool_manager._tools["list_databases"]
        result = await tool.fn(ctx=ctx)
        assert result == ["azure_sys", DEFAULT_DB]


class TestToolForwardsDatabaseArg:
    """Smoke test: a representative tool actually passes `database` through `db()`."""

    async def test_query_uses_named_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

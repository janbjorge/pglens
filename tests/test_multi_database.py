"""Multi-database registry: alias resolution, env discovery, alias exposure."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.adapters.mcp_adapter import (
    DEFAULT_DB,
    Databases,
    _collect_databases,
    _command_timeout_seconds,
    _idle_in_transaction_timeout_ms,
    _lock_timeout_ms,
    _server_settings,
    _statement_timeout_ms,
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

    def test_get_unknown_raises(self) -> None:
        reg = _registry(DEFAULT_DB, "azure_sys")
        with pytest.raises(ValueError, match="missing"):
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
        assert names == ["app", "azure_sys", "Analytics"]
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
        assert names == ["app", "azure_sys", "Analytics"]
        assert default == "app"

    def test_pglens_databases_dedupes_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGLENS_DATABASES", "app,app,azure_sys")
        names, _ = _collect_databases()
        assert names == ["app", "azure_sys"]

    def test_pgdatabase_preserves_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Postgres dbnames are case-sensitive; the original case must survive
        # so asyncpg connects to the right database (issue #16).
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGDATABASE", "MyApp")
        names, default = _collect_databases()
        assert names == ["MyApp"]
        assert default == "MyApp"


class TestStatementTimeout:
    def test_default_is_60_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PGLENS_STATEMENT_TIMEOUT", raising=False)
        assert _statement_timeout_ms() == 60_000

    def test_env_override_in_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_STATEMENT_TIMEOUT", "5")
        assert _statement_timeout_ms() == 5_000

    def test_zero_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_STATEMENT_TIMEOUT", "0")
        assert _statement_timeout_ms() == 0

    def test_rejects_non_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_STATEMENT_TIMEOUT", "5s")
        with pytest.raises(ValueError, match="integer number of seconds"):
            _statement_timeout_ms()

    def test_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_STATEMENT_TIMEOUT", "-1")
        with pytest.raises(ValueError, match=">= 0"):
            _statement_timeout_ms()

    def test_server_settings_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGLENS_STATEMENT_TIMEOUT", "30")
        settings = _server_settings()
        assert settings["application_name"] == "pglens"
        assert settings["default_transaction_read_only"] == "on"
        assert settings["statement_timeout"] == "30000"
        assert settings["lock_timeout"] == "5000"
        assert settings["idle_in_transaction_session_timeout"] == "30000"


class TestLockTimeout:
    def test_default_is_5_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PGLENS_LOCK_TIMEOUT", raising=False)
        assert _lock_timeout_ms() == 5_000

    def test_env_override_in_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_LOCK_TIMEOUT", "2")
        assert _lock_timeout_ms() == 2_000

    def test_zero_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_LOCK_TIMEOUT", "0")
        assert _lock_timeout_ms() == 0

    def test_rejects_non_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_LOCK_TIMEOUT", "2s")
        with pytest.raises(ValueError, match="PGLENS_LOCK_TIMEOUT must be an integer"):
            _lock_timeout_ms()

    def test_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_LOCK_TIMEOUT", "-1")
        with pytest.raises(ValueError, match=">= 0"):
            _lock_timeout_ms()


class TestIdleInTransactionTimeout:
    def test_default_is_30_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PGLENS_IDLE_TX_TIMEOUT", raising=False)
        assert _idle_in_transaction_timeout_ms() == 30_000

    def test_env_override_in_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_IDLE_TX_TIMEOUT", "10")
        assert _idle_in_transaction_timeout_ms() == 10_000

    def test_zero_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_IDLE_TX_TIMEOUT", "0")
        assert _idle_in_transaction_timeout_ms() == 0

    def test_rejects_non_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_IDLE_TX_TIMEOUT", "30s")
        with pytest.raises(ValueError, match="PGLENS_IDLE_TX_TIMEOUT must be an integer"):
            _idle_in_transaction_timeout_ms()

    def test_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_IDLE_TX_TIMEOUT", "-5")
        with pytest.raises(ValueError, match=">= 0"):
            _idle_in_transaction_timeout_ms()


class TestCommandTimeout:
    def test_sits_above_statement_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Grace margin lets the server-side cancel win, so callers see a
        # Postgres error rather than an asyncpg TimeoutError.
        monkeypatch.setenv("PGLENS_STATEMENT_TIMEOUT", "30")
        assert _command_timeout_seconds() == 35.0

    def test_disabled_when_statement_timeout_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PGLENS_STATEMENT_TIMEOUT", "0")
        assert _command_timeout_seconds() is None


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

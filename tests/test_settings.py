"""Settings: env parsing, validation, and the values handed to asyncpg."""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from pglens.core.settings import Settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("PGLENS_"):
            monkeypatch.delenv(key, raising=False)


class TestDefaults:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        settings = Settings()
        assert settings.statement_timeout == 60
        assert settings.lock_timeout == 5
        assert settings.idle_tx_timeout == 30

    def test_unrelated_pglens_env_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # PGLENS_DATABASES shares the prefix but is not a Settings field.
        _clear_env(monkeypatch)
        monkeypatch.setenv("PGLENS_DATABASES", "app,azure_sys")
        assert Settings().statement_timeout == 60


TIMEOUT_FIELDS: list[tuple[str, Callable[[Settings], int]]] = [
    ("PGLENS_STATEMENT_TIMEOUT", lambda s: s.statement_timeout),
    ("PGLENS_LOCK_TIMEOUT", lambda s: s.lock_timeout),
    ("PGLENS_IDLE_TX_TIMEOUT", lambda s: s.idle_tx_timeout),
]
TIMEOUT_ENV_VARS = [env_var for env_var, _ in TIMEOUT_FIELDS]


class TestEnvParsing:
    @pytest.mark.parametrize(("env_var", "read"), TIMEOUT_FIELDS)
    def test_override_in_seconds(
        self, monkeypatch: pytest.MonkeyPatch, env_var: str, read: Callable[[Settings], int]
    ) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv(env_var, "7")
        assert read(Settings()) == 7

    @pytest.mark.parametrize(("env_var", "read"), TIMEOUT_FIELDS)
    def test_zero_disables(
        self, monkeypatch: pytest.MonkeyPatch, env_var: str, read: Callable[[Settings], int]
    ) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv(env_var, "0")
        assert read(Settings()) == 0

    @pytest.mark.parametrize("env_var", TIMEOUT_ENV_VARS)
    def test_rejects_non_integer(self, monkeypatch: pytest.MonkeyPatch, env_var: str) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv(env_var, "5s")
        with pytest.raises(ValidationError, match="valid integer"):
            Settings()

    @pytest.mark.parametrize("env_var", TIMEOUT_ENV_VARS)
    def test_rejects_negative(self, monkeypatch: pytest.MonkeyPatch, env_var: str) -> None:
        _clear_env(monkeypatch)
        monkeypatch.setenv(env_var, "-1")
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            Settings()


class TestServerSettings:
    def test_shape_and_millisecond_conversion(self) -> None:
        settings = Settings(statement_timeout=30, lock_timeout=2, idle_tx_timeout=10)
        assert settings.server_settings() == {
            "application_name": "pglens",
            "default_transaction_read_only": "on",
            "statement_timeout": "30000",
            "lock_timeout": "2000",
            "idle_in_transaction_session_timeout": "10000",
        }


class TestCommandTimeout:
    def test_sits_above_statement_timeout(self) -> None:
        # Grace margin lets the server-side cancel win, so callers see a
        # Postgres error rather than an asyncpg TimeoutError.
        assert Settings(statement_timeout=30).command_timeout == 35.0

    def test_disabled_when_statement_timeout_disabled(self) -> None:
        assert Settings(statement_timeout=0).command_timeout is None

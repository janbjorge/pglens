"""app_lifespan reads Settings once and hands each AsyncpgDatabase what it needs."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from pglens.adapters.mcp_adapter import app_lifespan, mcp


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("PGLENS_") or key == "PGDATABASE":
            monkeypatch.delenv(key, raising=False)


async def test_lifespan_passes_max_cell_chars_to_every_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("PGLENS_MAX_CELL_CHARS", "7")
    monkeypatch.setenv("PGLENS_DATABASES", "app,analytics")
    # create_pool returns an async context manager yielding the pool; a MagicMock
    # provides both, so no PostgreSQL is needed.
    with patch("asyncpg.create_pool", return_value=MagicMock()) as create_pool:
        async with app_lifespan(mcp) as registry:
            assert registry.names() == ["analytics", "app"]
            assert {db.max_cell_chars for db in registry.databases.values()} == {7}
    assert create_pool.call_count == 2

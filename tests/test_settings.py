from unittest.mock import AsyncMock, MagicMock

import pytest

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.core.settings import Settings


class TestSettingsParsing:
    def test_defaults(self) -> None:
        s = Settings(dsn=None, schemas=None)
        assert s.dsn is None
        assert s.schemas is None

    def test_schemas_from_comma_string(self) -> None:
        s = Settings(schemas="public, app, analytics")
        assert s.schemas == frozenset({"public", "app", "analytics"})

    def test_schemas_single_value(self) -> None:
        s = Settings(schemas="public")
        assert s.schemas == frozenset({"public"})

    def test_schemas_empty_string_is_none(self) -> None:
        s = Settings(schemas="")
        assert s.schemas is None

    def test_schemas_whitespace_only_is_none(self) -> None:
        s = Settings(schemas=" , , ")
        assert s.schemas is None

    def test_schemas_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_SCHEMAS", "public,staging")
        s = Settings()
        assert s.schemas == frozenset({"public", "staging"})

    def test_dsn_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGLENS_DSN", "postgresql://localhost/mydb")
        s = Settings()
        assert s.dsn == "postgresql://localhost/mydb"


def make_record(**kwargs: object) -> MagicMock:
    record = MagicMock()
    record.__iter__ = lambda self: iter(kwargs.items())
    record.__getitem__ = lambda self, key: kwargs[key]
    record.keys = lambda: kwargs.keys()
    return record


class TestSchemaAllowlist:
    def _make_db(self, schemas: frozenset[str] | None = None) -> AsyncpgDatabase:
        pool = AsyncMock()
        settings = Settings(schemas=schemas)
        return AsyncpgDatabase(pool=pool, settings=settings)

    def test_check_schema_allowed_no_allowlist(self) -> None:
        db = self._make_db(schemas=None)
        db.check_schema_allowed("anything")  # should not raise

    def test_check_schema_allowed_in_list(self) -> None:
        db = self._make_db(schemas=frozenset({"public", "app"}))
        db.check_schema_allowed("public")  # should not raise
        db.check_schema_allowed("app")  # should not raise

    def test_check_schema_blocked(self) -> None:
        db = self._make_db(schemas=frozenset({"public"}))
        with pytest.raises(ValueError, match="not allowed"):
            db.check_schema_allowed("secret")

    async def test_list_tables_blocked_schema(self) -> None:
        db = self._make_db(schemas=frozenset({"public"}))
        with pytest.raises(ValueError, match="not allowed"):
            await db.list_tables("secret")

    async def test_list_tables_allowed_schema(self) -> None:
        db = self._make_db(schemas=frozenset({"public"}))
        db.pool.fetch.return_value = [
            make_record(table_name="orders", description=None, estimated_row_count=10),
        ]
        result = await db.list_tables("public")
        assert len(result) == 1

    async def test_list_schemas_filters_output(self) -> None:
        db = self._make_db(schemas=frozenset({"public"}))
        db.pool.fetch.return_value = [
            make_record(
                schema_name="public", owner="pg", description=None, table_count=5, view_count=1
            ),
            make_record(
                schema_name="internal", owner="pg", description=None, table_count=3, view_count=0
            ),
        ]
        result = await db.list_schemas()
        assert len(result) == 1
        assert result[0]["schema_name"] == "public"

    async def test_list_schemas_no_filter_when_none(self) -> None:
        db = self._make_db(schemas=None)
        db.pool.fetch.return_value = [
            make_record(
                schema_name="public", owner="pg", description=None, table_count=5, view_count=1
            ),
            make_record(
                schema_name="internal", owner="pg", description=None, table_count=3, view_count=0
            ),
        ]
        result = await db.list_schemas()
        assert len(result) == 2

    async def test_describe_table_blocked(self) -> None:
        db = self._make_db(schemas=frozenset({"public"}))
        with pytest.raises(ValueError, match="not allowed"):
            await db.describe_table("users", "secret")

    async def test_sample_rows_blocked(self) -> None:
        db = self._make_db(schemas=frozenset({"public"}))
        with pytest.raises(ValueError, match="not allowed"):
            await db.sample_rows("users", 5, "secret")

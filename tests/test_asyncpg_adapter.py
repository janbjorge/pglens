from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase


def make_record(**kwargs: object) -> MagicMock:
    record = MagicMock()
    record.__iter__ = lambda self: iter(kwargs.items())
    record.__getitem__ = lambda self, key: kwargs[key]
    record.keys = lambda: kwargs.keys()
    return record


def mock_conn(fetch_return: list[MagicMock] | None = None) -> AsyncMock:
    conn = AsyncMock()
    conn.fetch.return_value = fetch_return or []

    @asynccontextmanager
    async def fake_transaction(readonly: bool = False) -> AsyncIterator[None]:
        yield

    conn.transaction = fake_transaction
    return conn


def mock_pool_with_conn(conn: AsyncMock) -> AsyncMock:
    pool = AsyncMock()

    @asynccontextmanager
    async def fake_acquire() -> AsyncIterator[AsyncMock]:
        yield conn

    pool.acquire = fake_acquire
    return pool


@pytest.fixture
def pool() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def db(pool: AsyncMock) -> AsyncpgDatabase:
    return AsyncpgDatabase(pool=pool)


class TestSafeRefs:
    async def test_safe_table_ref(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetchval.return_value = '"public"."orders"'
        result = await db.safe_table_ref("public", "orders")
        assert result == '"public"."orders"'
        pool.fetchval.assert_called_once_with(
            "SELECT quote_ident($1) || '.' || quote_ident($2)",
            "public",
            "orders",
        )

    async def test_safe_column_ref(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetchval.return_value = '"status"'
        result = await db.safe_column_ref("status")
        assert result == '"status"'
        pool.fetchval.assert_called_once_with("SELECT quote_ident($1)", "status")


class TestListMethods:
    async def test_list_tables_returns_dicts(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = [
            make_record(table_name="orders", description="Order table", estimated_row_count=100),
        ]
        result = await db.list_tables("public")
        assert result == [
            {"table_name": "orders", "description": "Order table", "estimated_row_count": 100}
        ]

    async def test_list_tables_empty(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = []
        result = await db.list_tables("public")
        assert result == []

    async def test_list_views_returns_dicts(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = [
            make_record(view_name="active_orders", description=None, definition="SELECT ..."),
        ]
        result = await db.list_views("public")
        assert result == [
            {"view_name": "active_orders", "description": None, "definition": "SELECT ..."}
        ]

    async def test_list_extensions(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = [
            make_record(extname="plpgsql", extversion="1.0", description="PL/pgSQL"),
        ]
        result = await db.list_extensions()
        assert result == [{"extname": "plpgsql", "extversion": "1.0", "description": "PL/pgSQL"}]


class TestSearchData:
    async def test_search_data_no_text_columns(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = []
        result = await db.search_data("numbers_only", "foo", "public")
        assert result == []

    async def test_search_data_builds_conditions(self) -> None:
        conn = mock_conn()
        pool = mock_pool_with_conn(conn)
        pool.fetch.return_value = [
            make_record(safe_name='"name"'),
            make_record(safe_name='"email"'),
        ]
        pool.fetchval.return_value = '"public"."users"'
        db = AsyncpgDatabase(pool=pool)

        await db.search_data("users", "test", "public")

        sql = conn.fetch.call_args[0][0]
        assert '"name" ILIKE $1' in sql
        assert '"email" ILIKE $1' in sql
        assert " OR " in sql


class TestSampleRows:
    async def test_sample_rows_clamps_to_100(self) -> None:
        conn = mock_conn()
        pool = mock_pool_with_conn(conn)
        pool.fetchval.return_value = '"public"."big_table"'
        db = AsyncpgDatabase(pool=pool)

        await db.sample_rows("big_table", 999, "public")

        assert conn.fetch.call_args[0][1] == 100

    async def test_sample_rows_respects_small_n(self) -> None:
        conn = mock_conn()
        pool = mock_pool_with_conn(conn)
        pool.fetchval.return_value = '"public"."small_table"'
        db = AsyncpgDatabase(pool=pool)

        await db.sample_rows("small_table", 3, "public")

        assert conn.fetch.call_args[0][1] == 3


class TestColumnValues:
    async def test_column_values_clamps_top_n(self) -> None:
        conn = mock_conn(fetch_return=[make_record(value="active", frequency=42)])
        pool = mock_pool_with_conn(conn)
        pool.fetchval.side_effect = ['"public"."orders"', '"status"']
        db = AsyncpgDatabase(pool=pool)

        result = await db.column_values("orders", "status", 200, "public")

        assert conn.fetch.call_args[0][1] == 100
        assert result == [{"value": "active", "frequency": 42}]


class TestDescribeTable:
    async def test_describe_table_structure(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.side_effect = [
            [
                make_record(
                    column_name="id",
                    data_type="integer",
                    udt_name="int4",
                    is_nullable="NO",
                    column_default="nextval(...)",
                    description=None,
                )
            ],
            [make_record(column_name="id")],
            [],
            [
                make_record(
                    index_name="orders_pkey",
                    is_unique=True,
                    definition="CREATE UNIQUE INDEX ...",
                )
            ],
            [],
        ]

        result = await db.describe_table("orders", "public")

        assert result["table"] == "public.orders"
        columns = result["columns"]
        assert isinstance(columns, list) and len(columns) == 1
        assert result["primary_keys"] == ["id"]
        assert result["foreign_keys"] == []
        indexes = result["indexes"]
        assert isinstance(indexes, list) and len(indexes) == 1
        assert result["check_constraints"] == []


class TestQueryAndExplain:
    async def test_query_wraps_in_subquery_with_limit(self) -> None:
        conn = mock_conn(fetch_return=[make_record(id=1, name="test")])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        result = await db.query("SELECT id, name FROM users")

        sql = conn.fetch.call_args[0][0]
        assert "LIMIT 500" in sql
        assert "sub" in sql
        assert result == [{"id": 1, "name": "test"}]

    async def test_explain_returns_plan_text(self) -> None:
        conn = mock_conn(
            fetch_return=[
                make_record(**{"QUERY PLAN": "Seq Scan on users"}),
                make_record(**{"QUERY PLAN": "  Filter: (id > 0)"}),
            ]
        )
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        result = await db.explain_query("SELECT * FROM users WHERE id > 0")

        assert "Seq Scan on users" in result
        assert "Filter: (id > 0)" in result
        assert "\n" in result

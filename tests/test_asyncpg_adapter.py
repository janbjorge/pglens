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
        pool.fetchval.return_value = '"public"."orders"'
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
        assert "LIMIT $1 OFFSET $2" in sql
        assert "sub" in sql
        assert conn.fetch.call_args[0][1] == 500
        assert conn.fetch.call_args[0][2] == 0
        assert result == [{"id": 1, "name": "test"}]

    async def test_query_custom_limit_offset(self) -> None:
        conn = mock_conn(fetch_return=[make_record(id=2)])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.query("SELECT id FROM users", limit=100, offset=50)

        assert conn.fetch.call_args[0][1] == 100
        assert conn.fetch.call_args[0][2] == 50

    async def test_query_clamps_limit(self) -> None:
        conn = mock_conn(fetch_return=[])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.query("SELECT 1", limit=9999)
        assert conn.fetch.call_args[0][1] == 500

        await db.query("SELECT 1", limit=-5)
        assert conn.fetch.call_args[0][1] == 1

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


class TestListSchemas:
    async def test_list_schemas_returns_dicts(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = [
            make_record(
                schema_name="public",
                owner="postgres",
                description=None,
                table_count=10,
                view_count=2,
            ),
        ]
        result = await db.list_schemas()
        assert result == [
            {
                "schema_name": "public",
                "owner": "postgres",
                "description": None,
                "table_count": 10,
                "view_count": 2,
            }
        ]

    async def test_list_schemas_empty(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = []
        result = await db.list_schemas()
        assert result == []


class TestColumnStats:
    async def test_column_stats_no_stats(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetchval.side_effect = ['"public"."orders"', '"total"']
        pool.fetchrow.return_value = None
        result = await db.column_stats("orders", "total", "public")
        assert "error" in result

    async def test_column_stats_returns_combined(self) -> None:
        conn = mock_conn()
        pool = mock_pool_with_conn(conn)
        # safe_table_ref and safe_column_ref
        pool.fetchval.side_effect = ['"public"."orders"', '"total"']
        # pg_stats query
        pool.fetchrow.return_value = make_record(
            null_frac=0.05,
            n_distinct=-0.8,
            most_common_vals="{100,200,300}",
            most_common_freqs="{0.3,0.2,0.1}",
            correlation=0.95,
        )
        # summary query via conn inside readonly transaction
        conn.fetchrow.return_value = make_record(
            min_value="10",
            max_value="9999",
            total_rows=1000,
            non_null_count=950,
            null_count=50,
        )
        db = AsyncpgDatabase(pool=pool)
        result = await db.column_stats("orders", "total", "public")
        assert result["min_value"] == "10"
        assert result["max_value"] == "9999"
        assert result["total_rows"] == 1000
        assert result["null_fraction"] == 0.05
        assert result["correlation"] == 0.95


class TestFindJoinPath:
    async def test_find_join_path_direct(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = [
            make_record(
                from_table="orders",
                from_column="user_id",
                to_table="users",
                to_column="id",
            ),
        ]
        result = await db.find_join_path("users", "orders", "public", 4)
        assert isinstance(result["paths_found"], int) and result["paths_found"] >= 1
        paths = result["paths"]
        assert isinstance(paths, list) and len(paths) >= 1
        assert paths[0]["hops"] == 1
        assert "users" in paths[0]["tables"]
        assert "orders" in paths[0]["tables"]

    async def test_find_join_path_no_path(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = []
        result = await db.find_join_path("users", "products", "public", 4)
        assert result["paths_found"] == 0
        assert result["paths"] == []

    async def test_find_join_path_multi_hop(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = [
            make_record(
                from_table="orders",
                from_column="user_id",
                to_table="users",
                to_column="id",
            ),
            make_record(
                from_table="order_items",
                from_column="order_id",
                to_table="orders",
                to_column="id",
            ),
        ]
        result = await db.find_join_path("users", "order_items", "public", 4)
        assert isinstance(result["paths_found"], int) and result["paths_found"] >= 1
        paths = result["paths"]
        assert isinstance(paths, list)
        two_hop = [p for p in paths if p["hops"] == 2]
        assert len(two_hop) >= 1
        assert two_hop[0]["tables"] == ["users", "orders", "order_items"]

    async def test_find_join_path_clamps_depth(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = []
        result = await db.find_join_path("a", "b", "public", 99)
        # Should not error — max_depth clamped to 6
        assert result["paths_found"] == 0

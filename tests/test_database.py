from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from pglens.database import (
    AsyncpgDatabase,
    like_pattern,
    select_detail,
    truncate_cell,
)
from pglens.sql import validate_select


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


class TestLikePattern:
    def test_escapes_wildcards_and_backslashes(self) -> None:
        assert like_pattern("100%_x\\") == "100\\%\\_x\\\\"

    def test_plain_keyword_is_unchanged(self) -> None:
        assert like_pattern("email") == "email"


class TestServerVersion:
    async def test_injected_version_skips_probe(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=180000)
        assert await db._get_server_version_num() == 180000
        pool.fetchval.assert_not_called()

    async def test_version_is_cached_after_first_probe(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool)
        pool.fetchval.return_value = 160000
        pool.fetch.return_value = []
        await db.table_health("public")
        await db.table_health("public")
        assert pool.fetchval.call_count == 1


class TestTableHealth:
    async def test_joins_pg_class_by_relid(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=160000)
        pool.fetch.return_value = []
        await db.table_health("public")
        sql = pool.fetch.call_args[0][0]
        # xid age must come from pg_class joined by oid, not by table name,
        # so same-named tables in other schemas cannot collide
        assert "relfrozenxid" in sql
        assert "c.oid = s.relid" in sql

    async def test_pg18_adds_maintenance_timing_columns(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=180000)
        pool.fetch.return_value = []
        await db.table_health("public")
        sql = pool.fetch.call_args[0][0]
        assert "total_vacuum_time" in sql
        assert "relallfrozen" in sql

    async def test_pre_pg18_omits_maintenance_timing_columns(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=160000)
        pool.fetch.return_value = []
        await db.table_health("public")
        sql = pool.fetch.call_args[0][0]
        assert "total_vacuum_time" not in sql
        assert "relallfrozen" not in sql


class TestSlowQueries:
    async def test_pg18_extension_schema_adds_columns(
        self, db: AsyncpgDatabase, pool: AsyncMock
    ) -> None:
        # First fetchval: extension installed; second: wal_buffers_full column exists.
        pool.fetchval.side_effect = [True, True]
        pool.fetch.return_value = []
        await db.slow_queries(20)
        sql = pool.fetch.call_args[0][0]
        assert "wal_buffers_full" in sql
        assert "parallel_workers_launched" in sql

    async def test_old_extension_schema_omits_columns(
        self, db: AsyncpgDatabase, pool: AsyncMock
    ) -> None:
        pool.fetchval.side_effect = [True, False]
        pool.fetch.return_value = []
        await db.slow_queries(20)
        sql = pool.fetch.call_args[0][0]
        assert "wal_buffers_full" not in sql
        assert "parallel_workers_launched" not in sql


class TestReplicationStatus:
    async def test_reads_subscription_stats_whole(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=150000)
        pool.fetchval.return_value = False
        pool.fetch.side_effect = [[], [], [make_record(subname="sub1", apply_error_count=0)]]
        result = await db.replication_status()
        assert result["in_recovery"] is False
        assert result["subscriptions"] == [{"subname": "sub1", "apply_error_count": 0}]
        assert "SELECT * FROM pg_stat_subscription_stats" in pool.fetch.call_args_list[2][0][0]

    async def test_pre_pg15_skips_subscription_stats(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=140000)
        pool.fetchval.return_value = False
        pool.fetch.side_effect = [[], []]
        result = await db.replication_status()
        assert result["subscriptions"] == []
        assert pool.fetch.call_count == 2


class TestIoStats:
    async def test_pre_pg16_returns_error_entry(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=150000)
        result = await db.io_stats()
        assert len(result) == 1
        assert "pg_stat_io" in str(result[0]["error"])
        pool.fetch.assert_not_called()

    async def test_reads_pg_stat_io_whole(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=160000)
        pool.fetch.return_value = [make_record(backend_type="client backend", hits=5000)]
        result = await db.io_stats()
        sql = pool.fetch.call_args[0][0]
        assert "SELECT *" in sql and "FROM pg_stat_io" in sql
        assert result == [{"backend_type": "client backend", "hits": 5000}]


class TestMaintenanceProgress:
    async def test_reads_every_progress_view_whole(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool)
        pool.fetch.side_effect = [[make_record(pid=1, phase="scanning heap")], [], [], []]
        result = await db.maintenance_progress()
        assert result == {
            "vacuum": [{"pid": 1, "phase": "scanning heap"}],
            "analyze": [],
            "create_index": [],
            "cluster": [],
        }
        statements = [call[0][0] for call in pool.fetch.call_args_list]
        assert all("p.*" in sql and "c.oid = p.relid" in sql for sql in statements)
        assert "FROM pg_stat_progress_create_index" in statements[2]
        assert "AS index_name" in statements[2]


class TestValidateSelect:
    """Tests for SQL parsing defense using pglast (PostgreSQL's own parser)."""

    def test_allows_select(self) -> None:
        validate_select("SELECT 1")

    def test_allows_cte(self) -> None:
        validate_select("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_allows_union(self) -> None:
        validate_select("SELECT 1 UNION SELECT 2")

    def test_rejects_multi_statement(self) -> None:
        with pytest.raises(ValueError, match="single SQL statement"):
            validate_select("SELECT 1; DROP TABLE users")

    def test_rejects_non_select(self) -> None:
        with pytest.raises(ValueError, match="Only SELECT"):
            validate_select("DELETE FROM users")

    def test_allows_declare_cursor_with_select(self) -> None:
        validate_select("DECLARE cur CURSOR FOR SELECT 1", allow_cursor=True)

    def test_allows_declare_cursor_with_cte(self) -> None:
        validate_select(
            "DECLARE cur CURSOR FOR WITH cte AS (SELECT 1) SELECT * FROM cte",
            allow_cursor=True,
        )

    def test_rejects_declare_cursor_by_default(self) -> None:
        with pytest.raises(ValueError, match="DECLARE CURSOR"):
            validate_select("DECLARE cur CURSOR FOR SELECT 1")

    def test_rejects_invalid_sql(self) -> None:
        with pytest.raises(Exception):
            validate_select("NOT VALID SQL")

    def test_returns_normalized_sql(self) -> None:
        # Trailing semicolons and comments would break the query() subquery wrap
        assert validate_select("SELECT 1;") == "SELECT 1"
        assert validate_select("SELECT 1 ; -- trailing comment") == "SELECT 1"

    def test_rejects_select_into(self) -> None:
        with pytest.raises(ValueError, match="SELECT INTO"):
            validate_select("SELECT * INTO evil FROM users")

    def test_rejects_select_into_in_set_operation(self) -> None:
        with pytest.raises(ValueError, match="SELECT INTO"):
            validate_select("SELECT 1 INTO evil UNION SELECT 2")

    @pytest.mark.parametrize(
        "sql, verb",
        [
            ("WITH x AS (DELETE FROM users RETURNING *) SELECT * FROM x", "DELETE"),
            ("WITH x AS (UPDATE users SET a = 1 RETURNING *) SELECT * FROM x", "UPDATE"),
            ("WITH x AS (INSERT INTO users DEFAULT VALUES RETURNING *) SELECT * FROM x", "INSERT"),
        ],
    )
    def test_rejects_writes_hidden_in_cte(self, sql: str, verb: str) -> None:
        with pytest.raises(ValueError, match=verb):
            validate_select(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity",
            "SELECT pg_catalog.set_config('statement_timeout', '0', true)",
            "SELECT * FROM pg_advisory_lock(1)",
            "SELECT 1 WHERE EXISTS (SELECT pg_stat_reset())",
            "WITH x AS (SELECT dblink_exec('db', 'DROP TABLE t')) SELECT * FROM x",
        ],
    )
    def test_rejects_side_effecting_functions(self, sql: str) -> None:
        with pytest.raises(ValueError, match="acts outside the read-only query"):
            validate_select(sql)

    def test_allows_ordinary_functions(self) -> None:
        validate_select("SELECT count(*), lower(name), now(), current_setting('x') FROM t")

    def test_rejects_parameter_placeholders(self) -> None:
        with pytest.raises(ValueError, match="placeholders"):
            validate_select("SELECT * FROM users WHERE id = $1")

    async def test_query_validates_before_executing(self) -> None:
        conn = mock_conn(fetch_return=[])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        with pytest.raises(ValueError):
            await db.query("SELECT 1; DROP TABLE users")
        conn.fetch.assert_not_called()

    async def test_explain_validates_before_executing(self) -> None:
        conn = mock_conn(fetch_return=[])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        with pytest.raises(ValueError):
            await db.explain_query("DELETE FROM users")
        conn.fetch.assert_not_called()


class TestCellTruncation:
    """One wide text or json column must not flood the model's context."""

    def test_truncate_cell_cuts_long_strings_and_marks_the_full_length(self) -> None:
        assert truncate_cell("x" * 5000, 1000) == "x" * 1000 + "…[truncated, 5000 chars total]"

    def test_truncate_cell_leaves_strings_within_the_limit(self) -> None:
        assert truncate_cell("x" * 1000, 1000) == "x" * 1000

    @pytest.mark.parametrize(
        "value", [42, 3.5, True, None, b"\x00" * 5000, datetime(2024, 1, 1), ["a" * 50]]
    )
    def test_truncate_cell_leaves_non_strings_alone(self, value: object) -> None:
        assert truncate_cell(value, 1) is value

    def test_zero_disables_the_cut(self) -> None:
        assert truncate_cell("x" * 5000, 0) == "x" * 5000


class TestDetailLevels:
    """names keeps identifiers, summary drops definitions and source, full keeps everything."""

    ROW = {"name": "f", "language": "plpgsql", "source": "BEGIN END"}

    def test_select_detail(self) -> None:
        rows = [dict(self.ROW)]
        assert select_detail(rows, "names", ("name",), ("source",)) == [{"name": "f"}]
        assert select_detail(rows, "summary", ("name",), ("source",)) == [
            {"name": "f", "language": "plpgsql"}
        ]
        assert select_detail(rows, "full", ("name",), ("source",)) == [self.ROW]

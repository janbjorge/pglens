from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase
from pglens.core.sql import validate_select


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
    def test_safe_table_ref(self, db: AsyncpgDatabase) -> None:
        assert db.safe_table_ref("public", "orders") == '"public"."orders"'

    def test_safe_table_ref_escapes_embedded_quotes(self, db: AsyncpgDatabase) -> None:
        assert db.safe_table_ref('sch"ema', 'ta"ble') == '"sch""ema"."ta""ble"'

    def test_safe_column_ref(self, db: AsyncpgDatabase) -> None:
        assert db.safe_column_ref("status") == '"status"'

    def test_safe_column_ref_escapes_embedded_quotes(self, db: AsyncpgDatabase) -> None:
        assert db.safe_column_ref('col"umn') == '"col""umn"'


class TestDatabaseInfo:
    async def test_database_info_returns_dict(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetchrow.return_value = make_record(
            database_name="mydb",
            current_user="postgres",
            version="PostgreSQL 16.2",
            server_version="16.2",
            server_encoding="UTF8",
            timezone="UTC",
            max_connections="100",
            server_start_time="2024-01-01T00:00:00Z",
            uptime="1 day",
            database_size="50 MB",
            database_size_bytes=52428800,
        )
        result = await db.database_info()
        assert result["database_name"] == "mydb"
        assert result["server_version"] == "16.2"
        assert result["max_connections"] == "100"
        assert result["database_size_bytes"] == 52428800

    async def test_database_info_empty_row(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetchrow.return_value = None
        result = await db.database_info()
        assert result == {}


class TestListIndexes:
    async def test_list_indexes_returns_dicts(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = [
            make_record(
                index_name="users_pkey",
                table_name="users",
                is_unique=True,
                is_primary=True,
                index_type="btree",
                definition="CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id)",
                index_size="16 kB",
                index_size_bytes=16384,
                scans_since_reset=100,
                tuples_read=500,
                tuples_fetched=500,
            ),
        ]
        result = await db.list_indexes("public")
        assert len(result) == 1
        assert result[0]["index_name"] == "users_pkey"
        assert result[0]["is_primary"] is True
        assert result[0]["index_type"] == "btree"

    async def test_list_indexes_empty(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetch.return_value = []
        result = await db.list_indexes("public")
        assert result == []

    async def test_list_indexes_selects_validity(
        self, db: AsyncpgDatabase, pool: AsyncMock
    ) -> None:
        pool.fetch.return_value = []
        await db.list_indexes("public")
        assert "indisvalid" in pool.fetch.call_args[0][0]

    async def test_unused_indexes_selects_validity(
        self, db: AsyncpgDatabase, pool: AsyncMock
    ) -> None:
        pool.fetch.return_value = []
        await db.unused_indexes("public")
        assert "indisvalid" in pool.fetch.call_args[0][0]


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
    async def test_returns_merged_stats_and_bloat_columns(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=160000)
        pool.fetch.return_value = [
            make_record(
                table_name="users",
                seq_scan=10,
                idx_scan=90,
                index_hit_pct=90.0,
                n_live_tup=1000,
                n_dead_tup=50,
                dead_tuple_pct=5.0,
                xid_age=12345,
                wraparound_pct=6.2,
                last_vacuum=None,
                last_autovacuum=None,
                last_analyze=None,
                last_autoanalyze=None,
            ),
        ]
        result = await db.table_health("public")
        assert result[0]["index_hit_pct"] == 90.0
        assert result[0]["xid_age"] == 12345
        assert result[0]["wraparound_pct"] == 6.2

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
    async def test_missing_extension_returns_error_entry(
        self, db: AsyncpgDatabase, pool: AsyncMock
    ) -> None:
        pool.fetchval.return_value = False
        result = await db.slow_queries(20)
        assert len(result) == 1
        assert "pg_stat_statements" in str(result[0]["error"])
        pool.fetch.assert_not_called()

    async def test_returns_statements_when_installed(
        self, db: AsyncpgDatabase, pool: AsyncMock
    ) -> None:
        pool.fetchval.return_value = True
        pool.fetch.return_value = [
            make_record(
                username="app",
                database="app",
                calls=100,
                total_exec_time_ms=1234.5,
                mean_exec_time_ms=12.35,
                stddev_exec_time_ms=1.0,
                rows=5000,
                cache_hit_pct=99.1,
                query="SELECT * FROM orders",
            ),
        ]
        result = await db.slow_queries(20)
        assert len(result) == 1
        assert result[0]["calls"] == 100

    async def test_limit_is_clamped(self, db: AsyncpgDatabase, pool: AsyncMock) -> None:
        pool.fetchval.return_value = True
        pool.fetch.return_value = []
        await db.slow_queries(5000)
        assert pool.fetch.call_args[0][1] == 100
        await db.slow_queries(0)
        assert pool.fetch.call_args[0][1] == 1

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
    async def test_shape(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=180000)
        pool.fetchval.return_value = False
        pool.fetch.side_effect = [
            [make_record(application_name="standby1", state="streaming")],
            [make_record(slot_name="slot1", active=False, retained_wal_bytes=1024)],
            [make_record(subname="sub1", apply_error_count=0, confl_update_missing=2)],
        ]
        result = await db.replication_status()
        assert result["in_recovery"] is False
        standbys = result["standbys"]
        slots = result["slots"]
        subscriptions = result["subscriptions"]
        assert isinstance(standbys, list) and standbys[0]["state"] == "streaming"
        assert isinstance(slots, list) and slots[0]["active"] is False
        assert isinstance(subscriptions, list)
        assert subscriptions[0]["confl_update_missing"] == 2
        subscription_sql = pool.fetch.call_args_list[2][0][0]
        assert "confl_insert_exists" in subscription_sql

    async def test_pre_pg15_skips_subscription_stats(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=140000)
        pool.fetchval.return_value = False
        pool.fetch.side_effect = [[], []]
        result = await db.replication_status()
        assert result["subscriptions"] == []
        assert pool.fetch.call_count == 2

    async def test_pre_pg18_omits_conflict_columns(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=170000)
        pool.fetchval.return_value = False
        pool.fetch.side_effect = [[], [], []]
        await db.replication_status()
        subscription_sql = pool.fetch.call_args_list[2][0][0]
        assert "pg_stat_subscription_stats" in subscription_sql
        assert "confl_" not in subscription_sql


class TestIoStats:
    async def test_pre_pg16_returns_error_entry(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=150000)
        result = await db.io_stats()
        assert len(result) == 1
        assert "pg_stat_io" in str(result[0]["error"])
        pool.fetch.assert_not_called()

    async def test_pg16_omits_byte_columns(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=160000)
        pool.fetch.return_value = []
        await db.io_stats()
        sql = pool.fetch.call_args[0][0]
        assert "pg_stat_io" in sql
        assert "read_bytes" not in sql

    async def test_pg18_adds_byte_columns(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=180000)
        pool.fetch.return_value = [
            make_record(
                backend_type="client backend",
                object="relation",
                context="normal",
                reads=100,
                read_bytes=819200,
                hits=5000,
            ),
        ]
        result = await db.io_stats()
        sql = pool.fetch.call_args[0][0]
        assert "read_bytes" in sql
        assert "write_bytes" in sql
        assert "extend_bytes" in sql
        assert result[0]["hits"] == 5000


class TestMaintenanceProgress:
    async def test_shape(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=180000)
        pool.fetch.side_effect = [
            [make_record(pid=1, phase="scanning heap", scanned_pct=42.0)],
            [],
            [],
            [],
        ]
        result = await db.maintenance_progress()
        assert set(result.keys()) == {"vacuum", "analyze", "create_index", "cluster"}
        vacuum = result["vacuum"]
        assert isinstance(vacuum, list) and vacuum[0]["scanned_pct"] == 42.0
        assert result["cluster"] == []

    async def test_pg18_includes_delay_time(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=180000)
        pool.fetch.side_effect = [[], [], [], []]
        await db.maintenance_progress()
        vacuum_sql = pool.fetch.call_args_list[0][0][0]
        analyze_sql = pool.fetch.call_args_list[1][0][0]
        assert "delay_time" in vacuum_sql
        assert "indexes_total" in vacuum_sql
        assert "delay_time" in analyze_sql

    async def test_pg16_omits_gated_columns(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=160000)
        pool.fetch.side_effect = [[], [], [], []]
        await db.maintenance_progress()
        vacuum_sql = pool.fetch.call_args_list[0][0][0]
        analyze_sql = pool.fetch.call_args_list[1][0][0]
        assert "delay_time" not in vacuum_sql
        assert "indexes_total" not in vacuum_sql
        assert "delay_time" not in analyze_sql

    async def test_pg17_has_index_progress_but_no_delay(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=170000)
        pool.fetch.side_effect = [[], [], [], []]
        await db.maintenance_progress()
        vacuum_sql = pool.fetch.call_args_list[0][0][0]
        assert "indexes_total" in vacuum_sql
        assert "delay_time" not in vacuum_sql

    async def test_joins_pg_class_by_relid(self, pool: AsyncMock) -> None:
        db = AsyncpgDatabase(pool=pool, server_version_num=180000)
        pool.fetch.side_effect = [[], [], [], []]
        await db.maintenance_progress()
        for call in pool.fetch.call_args_list:
            assert "c.oid = p.relid" in call[0][0]


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
        db = AsyncpgDatabase(pool=pool)

        await db.search_data("users", "test", "public")

        sql = conn.fetch.call_args[0][0]
        assert '"name"::text ILIKE $1' in sql
        assert '"email"::text ILIKE $1' in sql
        assert " OR " in sql


class TestSampleRows:
    async def test_sample_rows_clamps_to_100(self) -> None:
        conn = mock_conn()
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.sample_rows("big_table", 999, "public")

        assert conn.fetch.call_args[0][1] == 100

    async def test_sample_rows_respects_small_n(self) -> None:
        conn = mock_conn()
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.sample_rows("small_table", 3, "public")

        assert conn.fetch.call_args[0][1] == 3

    @pytest.mark.parametrize("n", [0, -5])
    async def test_sample_rows_clamps_low_n_to_1(self, n: int) -> None:
        conn = mock_conn()
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.sample_rows("small_table", n, "public")

        assert conn.fetch.call_args[0][1] == 1


class TestColumnValues:
    async def test_column_values_clamps_top_n(self) -> None:
        conn = mock_conn(fetch_return=[make_record(value="active", frequency=42)])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        result = await db.column_values("orders", "status", 200, "public")

        assert conn.fetch.call_args[0][1] == 100
        assert result == [{"value": "active", "frequency": 42}]

    @pytest.mark.parametrize("top_n", [0, -5])
    async def test_column_values_clamps_low_top_n_to_1(self, top_n: int) -> None:
        conn = mock_conn()
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.column_values("orders", "status", top_n, "public")

        assert conn.fetch.call_args[0][1] == 1


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
        assert result["referenced_by"] == []
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

    async def test_explain_default_options(self) -> None:
        conn = mock_conn(fetch_return=[make_record(**{"QUERY PLAN": "Seq Scan"})])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.explain_query("SELECT 1")

        sql_arg = conn.fetch.call_args[0][0]
        assert "ANALYZE False" in sql_arg
        # BUFFERS is omitted so the server default applies (on with ANALYZE since PG18).
        assert "BUFFERS" not in sql_arg

    async def test_explain_with_analyze(self) -> None:
        conn = mock_conn(fetch_return=[make_record(**{"QUERY PLAN": "Seq Scan"})])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.explain_query("SELECT 1", analyze=True)

        sql_arg = conn.fetch.call_args[0][0]
        assert "ANALYZE True" in sql_arg
        assert "BUFFERS" not in sql_arg

    async def test_explain_with_analyze_and_buffers(self) -> None:
        conn = mock_conn(fetch_return=[make_record(**{"QUERY PLAN": "Seq Scan"})])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.explain_query("SELECT 1", analyze=True, buffers=True)

        sql_arg = conn.fetch.call_args[0][0]
        assert "ANALYZE True" in sql_arg
        assert "BUFFERS TRUE" in sql_arg


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

    async def test_query_rejects_declare_cursor(self) -> None:
        conn = mock_conn(fetch_return=[])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        with pytest.raises(ValueError, match="DECLARE CURSOR"):
            await db.query("DECLARE cur CURSOR FOR SELECT 1")
        conn.fetch.assert_not_called()

    async def test_query_strips_trailing_semicolon(self) -> None:
        conn = mock_conn(fetch_return=[])
        pool = mock_pool_with_conn(conn)
        db = AsyncpgDatabase(pool=pool)

        await db.query("SELECT 1;")

        sql = conn.fetch.call_args[0][0]
        assert ";" not in sql.replace("LIMIT $1 OFFSET $2", "")
        assert "SELECT * FROM (SELECT 1) sub" in sql


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
        pool.fetchrow.return_value = None
        result = await db.column_stats("orders", "total", "public")
        assert "error" in result

    async def test_column_stats_returns_combined(self) -> None:
        conn = mock_conn()
        pool = mock_pool_with_conn(conn)
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

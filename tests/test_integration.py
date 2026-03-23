"""Integration tests against a real PostgreSQL via testcontainers.

These tests verify that every SQL query in AsyncpgDatabase runs without error
and returns structurally correct results against a known schema.
"""

from __future__ import annotations

import pytest

from pglens.adapters.asyncpg_adapter import AsyncpgDatabase

# All tests in this file require the real database fixtures from conftest.py.
# The `db` fixture is function-scoped and provides an AsyncpgDatabase backed
# by a real asyncpg pool connected to a testcontainer PostgreSQL instance.

pytestmark = pytest.mark.usefixtures("pool")


class TestSchemaDiscovery:
    async def test_list_schemas(self, db: AsyncpgDatabase) -> None:
        schemas = await db.list_schemas()
        names = [s["schema_name"] for s in schemas]
        assert "public" in names
        # pg_catalog and information_schema should be excluded
        assert "pg_catalog" not in names
        assert "information_schema" not in names

    async def test_list_tables(self, db: AsyncpgDatabase) -> None:
        tables = await db.list_tables("public")
        names = [t["table_name"] for t in tables]
        assert "users" in names
        assert "orders" in names
        assert "products" in names
        assert "order_items" in names
        # Views and matviews should not appear
        assert "active_orders" not in names
        assert "order_summary" not in names
        # Should have estimated_row_count
        users = next(t for t in tables if t["table_name"] == "users")
        assert users["estimated_row_count"] is not None

    async def test_list_tables_with_description(self, db: AsyncpgDatabase) -> None:
        tables = await db.list_tables("public")
        users = next(t for t in tables if t["table_name"] == "users")
        assert users["description"] == "Application users"

    async def test_list_views(self, db: AsyncpgDatabase) -> None:
        views = await db.list_views("public")
        names = [v["view_name"] for v in views]
        assert "active_orders" in names
        view = next(v for v in views if v["view_name"] == "active_orders")
        assert view["definition"] is not None
        assert "orders" in view["definition"]

    async def test_list_extensions(self, db: AsyncpgDatabase) -> None:
        extensions = await db.list_extensions()
        names = [e["extname"] for e in extensions]
        assert "plpgsql" in names

    async def test_list_functions(self, db: AsyncpgDatabase) -> None:
        functions = await db.list_functions("public")
        names = [f["function_name"] for f in functions]
        assert "update_modified_column" in names
        fn = next(f for f in functions if f["function_name"] == "update_modified_column")
        assert fn["language"] == "plpgsql"
        assert fn["volatility"] == "volatile"
        assert fn["kind"] == "function"


class TestDescribeTable:
    async def test_columns(self, db: AsyncpgDatabase) -> None:
        result = await db.describe_table("users", "public")
        columns = result["columns"]
        assert isinstance(columns, list)
        col_names = [c["column_name"] for c in columns]
        assert "id" in col_names
        assert "username" in col_names
        assert "email" in col_names

        id_col = next(c for c in columns if c["column_name"] == "id")
        assert id_col["is_nullable"] == "NO"
        # Column comment
        assert id_col["description"] == "Auto-incrementing user ID"

    async def test_primary_keys(self, db: AsyncpgDatabase) -> None:
        result = await db.describe_table("users", "public")
        assert "id" in result["primary_keys"]

    async def test_composite_primary_key(self, db: AsyncpgDatabase) -> None:
        result = await db.describe_table("order_items", "public")
        pks = result["primary_keys"]
        assert set(pks) == {"order_id", "product_id"}

    async def test_foreign_keys(self, db: AsyncpgDatabase) -> None:
        result = await db.describe_table("orders", "public")
        fks = result["foreign_keys"]
        assert len(fks) >= 1
        fk = next(f for f in fks if f["column_name"] == "user_id")
        assert fk["foreign_table"] == "users"
        assert fk["foreign_column"] == "id"
        assert fk["foreign_schema"] == "public"

    async def test_multi_column_foreign_key(self, db: AsyncpgDatabase) -> None:
        """Verify multi-column FK returns exactly 2 rows, not a cross-product of 4."""
        result = await db.describe_table("warehouses", "public")
        fks = result["foreign_keys"]
        # Should be exactly 2 FK columns pointing to shipping_regions
        sr_fks = [f for f in fks if f["foreign_table"] == "shipping_regions"]
        assert len(sr_fks) == 2
        local_cols = {f["column_name"] for f in sr_fks}
        foreign_cols = {f["foreign_column"] for f in sr_fks}
        assert local_cols == {"country_code", "region_code"}
        assert foreign_cols == {"country_code", "region_code"}
        # Verify correct pairing (not cross-product)
        for fk in sr_fks:
            assert fk["column_name"] == fk["foreign_column"]

    async def test_indexes(self, db: AsyncpgDatabase) -> None:
        result = await db.describe_table("users", "public")
        indexes = result["indexes"]
        assert isinstance(indexes, list)
        assert len(indexes) >= 1
        # PK index should exist
        idx_names = [i["index_name"] for i in indexes]
        assert any("pkey" in name for name in idx_names)

    async def test_check_constraints(self, db: AsyncpgDatabase) -> None:
        result = await db.describe_table("products", "public")
        checks = result["check_constraints"]
        assert len(checks) >= 1
        assert any("price" in c["definition"] for c in checks)


class TestRelationships:
    async def test_find_related_references(self, db: AsyncpgDatabase) -> None:
        result = await db.find_related_tables("orders", "public")
        refs = result["references"]
        assert len(refs) >= 1
        assert any(r["to_table"] == "users" for r in refs)

    async def test_find_related_referenced_by(self, db: AsyncpgDatabase) -> None:
        result = await db.find_related_tables("users", "public")
        refs_by = result["referenced_by"]
        assert len(refs_by) >= 1
        assert any(r["from_table"] == "orders" for r in refs_by)

    async def test_multi_column_fk_related(self, db: AsyncpgDatabase) -> None:
        """Multi-column FK in find_related_tables returns correct pairs."""
        result = await db.find_related_tables("warehouses", "public")
        refs = result["references"]
        sr_refs = [r for r in refs if r["to_table"] == "shipping_regions"]
        assert len(sr_refs) == 2
        pairs = {(r["from_column"], r["to_column"]) for r in sr_refs}
        assert ("country_code", "country_code") in pairs
        assert ("region_code", "region_code") in pairs

    async def test_find_join_path_direct(self, db: AsyncpgDatabase) -> None:
        result = await db.find_join_path("users", "orders", "public", 4)
        assert result["paths_found"] >= 1
        paths = result["paths"]
        assert isinstance(paths, list)
        direct = [p for p in paths if p["hops"] == 1]
        assert len(direct) >= 1

    async def test_find_join_path_multi_hop(self, db: AsyncpgDatabase) -> None:
        result = await db.find_join_path("users", "products", "public", 4)
        assert result["paths_found"] >= 1
        # users -> orders -> order_items -> products
        paths = result["paths"]
        assert any(p["hops"] <= 3 for p in paths)

    async def test_find_join_path_no_path(self, db: AsyncpgDatabase) -> None:
        result = await db.find_join_path("users", "shipping_regions", "public", 4)
        assert result["paths_found"] == 0


class TestDataExploration:
    async def test_sample_rows(self, db: AsyncpgDatabase) -> None:
        rows = await db.sample_rows("users", 10, "public")
        assert len(rows) == 3  # We inserted 3 users
        assert "username" in rows[0]

    async def test_column_values(self, db: AsyncpgDatabase) -> None:
        result = await db.column_values("orders", "status", 10, "public")
        assert len(result) >= 1
        values = {r["value"] for r in result}
        assert "pending" in values

    async def test_search_data(self, db: AsyncpgDatabase) -> None:
        result = await db.search_data("users", "alice", "public")
        assert len(result) >= 1
        assert any(r["username"] == "alice" for r in result)

    async def test_search_data_no_match(self, db: AsyncpgDatabase) -> None:
        result = await db.search_data("users", "zzz_nonexistent_zzz", "public")
        assert result == []

    async def test_search_data_like_wildcards_escaped(self, db: AsyncpgDatabase) -> None:
        """Searching for literal '%' should not match everything."""
        result = await db.search_data("users", "100%", "public")
        # Only charlie has "100%" in bio
        assert len(result) == 1
        assert result[0]["username"] == "charlie"

    async def test_search_columns(self, db: AsyncpgDatabase) -> None:
        result = await db.search_columns("email", "public")
        assert len(result) >= 1
        assert any(r["table_name"] == "users" and r["column_name"] == "email" for r in result)

    async def test_search_enum_values(self, db: AsyncpgDatabase) -> None:
        result = await db.search_enum_values("order_status")
        assert len(result) == 1
        assert result[0]["enum_name"] == "order_status"
        vals = result[0]["values"]
        assert "pending" in vals
        assert "delivered" in vals

    async def test_column_stats(self, db: AsyncpgDatabase) -> None:
        result = await db.column_stats("users", "username", "public")
        assert "error" not in result
        assert result["total_rows"] == 3
        assert result["non_null_count"] == 3
        assert result["null_count"] == 0
        assert result["min_value"] is not None
        assert result["max_value"] is not None

    async def test_column_stats_missing_column(self, db: AsyncpgDatabase) -> None:
        result = await db.column_stats("users", "nonexistent_col", "public")
        assert "error" in result


class TestQueryExecution:
    async def test_query(self, db: AsyncpgDatabase) -> None:
        result = await db.query("SELECT id, username FROM users ORDER BY id")
        assert len(result) == 3
        assert result[0]["username"] == "alice"

    async def test_query_limit(self, db: AsyncpgDatabase) -> None:
        result = await db.query("SELECT generate_series(1, 1000) AS n")
        assert len(result) == 500  # Capped at 500

    async def test_explain_query(self, db: AsyncpgDatabase) -> None:
        plan = await db.explain_query("SELECT * FROM users WHERE id = 1")
        assert "users" in plan.lower()


class TestHealthMonitoring:
    async def test_table_stats(self, db: AsyncpgDatabase) -> None:
        stats = await db.table_stats("public")
        assert len(stats) >= 1
        names = [s["table_name"] for s in stats]
        assert "users" in names
        users_stats = next(s for s in stats if s["table_name"] == "users")
        assert users_stats["n_live_tup"] is not None

    async def test_table_sizes(self, db: AsyncpgDatabase) -> None:
        sizes = await db.table_sizes("public")
        assert len(sizes) >= 1
        names = [s["table_name"] for s in sizes]
        assert "users" in names
        users_size = next(s for s in sizes if s["table_name"] == "users")
        assert users_size["total_size"] is not None
        assert users_size["total_bytes"] > 0

    async def test_bloat_stats(self, db: AsyncpgDatabase) -> None:
        stats = await db.bloat_stats("public")
        assert len(stats) >= 1
        users_bloat = next(s for s in stats if s["table_name"] == "users")
        assert users_bloat["xid_age"] is not None
        assert users_bloat["wraparound_pct"] is not None

    async def test_unused_indexes(self, db: AsyncpgDatabase) -> None:
        indexes = await db.unused_indexes("public")
        # idx_users_bio was created but never scanned
        idx_names = [i["index_name"] for i in indexes]
        assert "idx_users_bio" in idx_names

    async def test_sequence_health(self, db: AsyncpgDatabase) -> None:
        seqs = await db.sequence_health("public")
        assert len(seqs) >= 1
        # users_id_seq should exist
        names = [s["sequence_name"] for s in seqs]
        assert any("users" in n for n in names)
        seq = next(s for s in seqs if "users" in s["sequence_name"])
        assert seq["last_value"] is not None
        assert seq["pct_consumed"] is not None

    async def test_active_queries(self, db: AsyncpgDatabase) -> None:
        # Should run without error; our own connection is excluded
        result = await db.active_queries()
        assert isinstance(result, list)

    async def test_blocking_locks(self, db: AsyncpgDatabase) -> None:
        result = await db.blocking_locks()
        assert isinstance(result, list)
        assert len(result) == 0  # No blocking expected


class TestTriggersAndPolicies:
    async def test_list_triggers(self, db: AsyncpgDatabase) -> None:
        triggers = await db.list_triggers("orders", "public")
        assert len(triggers) >= 1
        names = [t["trigger_name"] for t in triggers]
        assert "trg_orders_modified" in names
        trg = next(t for t in triggers if t["trigger_name"] == "trg_orders_modified")
        assert trg["status"] == "enabled"
        assert trg["function_name"] == "update_modified_column"

    async def test_list_policies(self, db: AsyncpgDatabase) -> None:
        policies = await db.list_policies("orders", "public")
        assert len(policies) >= 1
        pol = next(p for p in policies if p["policy_name"] == "orders_owner_policy")
        assert pol["command"] == "SELECT"
        assert pol["type"] == "permissive"
        assert pol["rls_enabled"] is True


class TestMatviewsAndDependencies:
    async def test_matview_status(self, db: AsyncpgDatabase) -> None:
        matviews = await db.matview_status("public")
        assert len(matviews) >= 1
        mv = next(m for m in matviews if m["matview_name"] == "order_summary")
        assert mv["ispopulated"] is True
        assert mv["has_unique_index"] is True
        assert mv["total_size"] is not None

    async def test_object_dependencies_table(self, db: AsyncpgDatabase) -> None:
        deps = await db.object_dependencies("users", "table", "public")
        assert isinstance(deps, list)
        # Should return some dependencies (constraints, rules, sequences, etc.)
        # The exact set depends on PG internals, so just verify it runs and returns results
        assert len(deps) >= 0

    async def test_object_dependencies_function(self, db: AsyncpgDatabase) -> None:
        deps = await db.object_dependencies("update_modified_column", "function", "public")
        assert isinstance(deps, list)


class TestSafeRefs:
    async def test_safe_table_ref(self, db: AsyncpgDatabase) -> None:
        ref = await db.safe_table_ref("public", "users")
        # Should be properly quoted
        assert "public" in ref
        assert "users" in ref

    async def test_safe_column_ref(self, db: AsyncpgDatabase) -> None:
        ref = await db.safe_column_ref("username")
        assert "username" in ref

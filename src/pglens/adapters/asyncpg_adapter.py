"""Query class backed by asyncpg."""

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import cast

import asyncpg


def object_type_to_relkind(object_type: str) -> str:
    match object_type:
        case "table":
            return "r"
        case "view":
            return "v"
        case "matview":
            return "m"
        case "sequence":
            return "S"
        case "index":
            return "i"
        case _:
            return "r"


@dataclass
class AsyncpgDatabase:
    pool: asyncpg.Pool

    async def safe_table_ref(self, schema: str, table_name: str) -> str:
        return cast(
            str,
            await self.pool.fetchval(
                "SELECT quote_ident($1) || '.' || quote_ident($2)",
                schema,
                table_name,
            ),
        )

    async def safe_column_ref(self, column_name: str) -> str:
        return cast(str, await self.pool.fetchval("SELECT quote_ident($1)", column_name))

    async def list_tables(self, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    t.table_name,
                    obj_description((quote_ident(t.table_schema) || '.' ||
                        quote_ident(t.table_name))::regclass) AS description,
                    s.n_live_tup AS estimated_row_count
                FROM information_schema.tables t
                LEFT JOIN pg_stat_user_tables s
                    ON s.schemaname = t.table_schema
                    AND s.relname = t.table_name
                WHERE t.table_schema = $1 AND t.table_type = 'BASE TABLE'
                ORDER BY s.n_live_tup DESC NULLS LAST
                """,
                schema,
            )
        ]

    async def list_views(self, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    v.table_name AS view_name,
                    obj_description(
                        (quote_ident(v.table_schema) || '.' ||
                         quote_ident(v.table_name))::regclass
                    ) AS description,
                    pg_get_viewdef(
                        (quote_ident(v.table_schema) || '.' ||
                         quote_ident(v.table_name))::regclass, true
                    ) AS definition
                FROM information_schema.views v
                WHERE v.table_schema = $1
                ORDER BY v.table_name
                """,
                schema,
            )
        ]

    async def list_extensions(self) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT extname, extversion, obj_description(e.oid, 'pg_extension') AS description
                FROM pg_extension e
                ORDER BY extname
                """
            )
        ]

    async def describe_table(self, table_name: str, schema: str) -> dict[str, object]:
        ref = await self.safe_table_ref(schema, table_name)

        columns = [
            dict(r)
            for r in await self.pool.fetch(
                f"""
                SELECT
                    c.column_name,
                    c.data_type,
                    c.udt_name,
                    c.is_nullable,
                    c.column_default,
                    col_description({ref}::regclass, c.ordinal_position) AS description
                FROM information_schema.columns c
                WHERE c.table_schema = $1 AND c.table_name = $2
                ORDER BY c.ordinal_position
                """,
                schema,
                table_name,
            )
        ]

        primary_keys = [
            r["column_name"]
            for r in await self.pool.fetch(
                f"""
                SELECT a.attname AS column_name
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid
                    AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = {ref}::regclass AND i.indisprimary
                """,
            )
        ]

        foreign_keys = [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    a1.attname AS column_name,
                    ns2.nspname AS foreign_schema,
                    cl2.relname AS foreign_table,
                    a2.attname AS foreign_column
                FROM pg_constraint con
                JOIN pg_class cl1 ON con.conrelid = cl1.oid
                JOIN pg_namespace ns1 ON cl1.relnamespace = ns1.oid
                JOIN pg_class cl2 ON con.confrelid = cl2.oid
                JOIN pg_namespace ns2 ON cl2.relnamespace = ns2.oid
                CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
                    WITH ORDINALITY AS u(local_attnum, foreign_attnum, ord)
                JOIN pg_attribute a1
                    ON a1.attrelid = con.conrelid AND a1.attnum = u.local_attnum
                JOIN pg_attribute a2
                    ON a2.attrelid = con.confrelid AND a2.attnum = u.foreign_attnum
                WHERE con.contype = 'f'
                    AND ns1.nspname = $1 AND cl1.relname = $2
                ORDER BY con.conname, u.ord
                """,
                schema,
                table_name,
            )
        ]

        indexes = [
            dict(r)
            for r in await self.pool.fetch(
                f"""
                SELECT
                    i.relname AS index_name,
                    ix.indisunique AS is_unique,
                    pg_get_indexdef(ix.indexrelid) AS definition
                FROM pg_index ix
                JOIN pg_class i ON i.oid = ix.indexrelid
                WHERE ix.indrelid = {ref}::regclass
                ORDER BY i.relname
                """,
            )
        ]

        check_constraints = [
            dict(r)
            for r in await self.pool.fetch(
                f"""
                SELECT
                    conname AS constraint_name,
                    pg_get_constraintdef(oid) AS definition
                FROM pg_constraint
                WHERE conrelid = {ref}::regclass AND contype = 'c'
                ORDER BY conname
                """,
            )
        ]

        return {
            "table": f"{schema}.{table_name}",
            "columns": columns,
            "primary_keys": primary_keys,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
            "check_constraints": check_constraints,
        }

    async def find_related_tables(self, table_name: str, schema: str) -> dict[str, object]:
        references = [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    a1.attname AS from_column,
                    ns2.nspname AS to_schema,
                    cl2.relname AS to_table,
                    a2.attname AS to_column
                FROM pg_constraint con
                JOIN pg_class cl1 ON con.conrelid = cl1.oid
                JOIN pg_namespace ns1 ON cl1.relnamespace = ns1.oid
                JOIN pg_class cl2 ON con.confrelid = cl2.oid
                JOIN pg_namespace ns2 ON cl2.relnamespace = ns2.oid
                CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
                    WITH ORDINALITY AS u(local_attnum, foreign_attnum, ord)
                JOIN pg_attribute a1
                    ON a1.attrelid = con.conrelid AND a1.attnum = u.local_attnum
                JOIN pg_attribute a2
                    ON a2.attrelid = con.confrelid AND a2.attnum = u.foreign_attnum
                WHERE con.contype = 'f'
                    AND ns1.nspname = $1 AND cl1.relname = $2
                ORDER BY con.conname, u.ord
                """,
                schema,
                table_name,
            )
        ]

        referenced_by = [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    ns1.nspname AS from_schema,
                    cl1.relname AS from_table,
                    a1.attname AS from_column,
                    a2.attname AS to_column
                FROM pg_constraint con
                JOIN pg_class cl1 ON con.conrelid = cl1.oid
                JOIN pg_namespace ns1 ON cl1.relnamespace = ns1.oid
                JOIN pg_class cl2 ON con.confrelid = cl2.oid
                JOIN pg_namespace ns2 ON cl2.relnamespace = ns2.oid
                CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
                    WITH ORDINALITY AS u(local_attnum, foreign_attnum, ord)
                JOIN pg_attribute a1
                    ON a1.attrelid = con.conrelid AND a1.attnum = u.local_attnum
                JOIN pg_attribute a2
                    ON a2.attrelid = con.confrelid AND a2.attnum = u.foreign_attnum
                WHERE con.contype = 'f'
                    AND ns2.nspname = $1 AND cl2.relname = $2
                ORDER BY con.conname, u.ord
                """,
                schema,
                table_name,
            )
        ]

        return {
            "table": f"{schema}.{table_name}",
            "references": references,
            "referenced_by": referenced_by,
        }

    async def find_join_path(
        self, source_table: str, target_table: str, schema: str, max_depth: int
    ) -> dict[str, object]:
        max_depth = min(max(max_depth, 1), 6)

        fk_rows = await self.pool.fetch(
            """
            SELECT
                cl1.relname AS from_table,
                a1.attname AS from_column,
                cl2.relname AS to_table,
                a2.attname AS to_column
            FROM pg_constraint con
            JOIN pg_class cl1 ON con.conrelid = cl1.oid
            JOIN pg_namespace ns1 ON cl1.relnamespace = ns1.oid
            JOIN pg_class cl2 ON con.confrelid = cl2.oid
            CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
                WITH ORDINALITY AS u(local_attnum, foreign_attnum, ord)
            JOIN pg_attribute a1
                ON a1.attrelid = con.conrelid AND a1.attnum = u.local_attnum
            JOIN pg_attribute a2
                ON a2.attrelid = con.confrelid AND a2.attnum = u.foreign_attnum
            WHERE con.contype = 'f'
                AND ns1.nspname = $1
            """,
            schema,
        )

        # Build bidirectional adjacency list
        graph: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in fk_rows:
            fr = row["from_table"]
            to = row["to_table"]
            fc = row["from_column"]
            tc = row["to_column"]
            graph[fr].append({"table": to, "from_col": fc, "to_col": tc, "direction": "outgoing"})
            graph[to].append({"table": fr, "from_col": tc, "to_col": fc, "direction": "incoming"})

        # BFS to find all paths up to max_depth
        paths: list[list[dict[str, str]]] = []
        # Queue items: (current_table, path_of_edges, visited_tables)
        queue: deque[tuple[str, list[dict[str, str]], set[str]]] = deque()
        queue.append((source_table, [], {source_table}))

        while queue:
            current, path, visited = queue.popleft()
            if len(path) > max_depth:
                continue
            if current == target_table and path:
                paths.append(path)
                continue
            if len(path) >= max_depth:
                continue
            for edge in graph.get(current, []):
                next_table = edge["table"]
                if next_table not in visited:
                    new_edge = {
                        "from_table": current,
                        "from_column": edge["from_col"],
                        "to_table": next_table,
                        "to_column": edge["to_col"],
                    }
                    queue.append((next_table, path + [new_edge], visited | {next_table}))

        # Format results
        formatted_paths = []
        for path in paths:
            joins = []
            for edge in path:
                joins.append(
                    f"{edge['from_table']}.{edge['from_column']}"
                    f" = {edge['to_table']}.{edge['to_column']}"
                )
            tables = [path[0]["from_table"]] + [e["to_table"] for e in path]
            formatted_paths.append(
                {
                    "tables": tables,
                    "joins": joins,
                    "hops": len(path),
                }
            )

        formatted_paths.sort(key=lambda p: cast(int, p["hops"]))

        return {
            "source": source_table,
            "target": target_table,
            "schema": schema,
            "paths_found": len(formatted_paths),
            "paths": formatted_paths,
        }

    async def sample_rows(self, table_name: str, n: int, schema: str) -> list[dict[str, object]]:
        ref = await self.safe_table_ref(schema, table_name)
        async with self.pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                rows = await conn.fetch(
                    f"SELECT * FROM {ref} ORDER BY random() LIMIT $1",
                    min(n, 100),
                )
                return [dict(r) for r in rows]

    async def column_values(
        self, table_name: str, column_name: str, top_n: int, schema: str
    ) -> list[dict[str, object]]:
        ref = await self.safe_table_ref(schema, table_name)
        col = await self.safe_column_ref(column_name)
        async with self.pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                return [
                    dict(r)
                    for r in await conn.fetch(
                        f"""
                        SELECT
                            {col}::text AS value,
                            count(*) AS frequency
                        FROM {ref}
                        GROUP BY 1
                        ORDER BY 2 DESC
                        LIMIT $1
                        """,
                        min(top_n, 100),
                    )
                ]

    async def search_data(
        self, table_name: str, keyword: str, schema: str
    ) -> list[dict[str, object]]:
        text_cols = await self.pool.fetch(
            """
            SELECT quote_ident(column_name) AS safe_name
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
                AND data_type IN ('text', 'character varying', 'character', 'name')
            ORDER BY ordinal_position
            """,
            schema,
            table_name,
        )
        if not text_cols:
            return []
        ref = await self.safe_table_ref(schema, table_name)
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions = " OR ".join(f"{r['safe_name']} ILIKE $1" for r in text_cols)
        async with self.pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM {ref}
                    WHERE {conditions}
                    LIMIT 50
                    """,
                    f"%{escaped}%",
                )
                return [dict(r) for r in rows]

    async def search_columns(self, keyword: str, schema: str) -> list[dict[str, object]]:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT table_name, column_name, data_type, udt_name
                FROM information_schema.columns
                WHERE table_schema = $1
                    AND column_name ILIKE '%' || $2 || '%'
                ORDER BY table_name, ordinal_position
                """,
                schema,
                escaped,
            )
        ]

    async def search_enum_values(self, keyword: str) -> list[dict[str, object]]:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    t.typname AS enum_name,
                    array_agg(e.enumlabel ORDER BY e.enumsortorder) AS values
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname ILIKE '%' || $1 || '%'
                GROUP BY t.typname
                ORDER BY t.typname
                """,
                escaped,
            )
        ]

    async def table_stats(self, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    relname AS table_name,
                    seq_scan,
                    idx_scan,
                    CASE WHEN (seq_scan + idx_scan) > 0
                        THEN round(100.0 * idx_scan / (seq_scan + idx_scan), 1)
                    END AS index_hit_pct,
                    n_live_tup,
                    n_dead_tup,
                    CASE WHEN n_live_tup > 0
                        THEN round(100.0 * n_dead_tup / n_live_tup, 1)
                    END AS dead_tuple_pct,
                    last_vacuum,
                    last_autovacuum,
                    last_analyze,
                    last_autoanalyze
                FROM pg_stat_user_tables
                WHERE schemaname = $1
                ORDER BY n_live_tup DESC NULLS LAST
                """,
                schema,
            )
        ]

    async def explain_query(self, sql: str) -> str:
        async with self.pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                rows = await conn.fetch(f"EXPLAIN (ANALYZE false, FORMAT TEXT) {sql}")
                return "\n".join(r["QUERY PLAN"] for r in rows)

    async def query(self, sql: str) -> list[dict[str, object]]:
        async with self.pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                rows = await conn.fetch(f"SELECT * FROM ({sql}) sub LIMIT 500")
                return [dict(r) for r in rows]

    async def list_schemas(self) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    n.nspname AS schema_name,
                    pg_catalog.pg_get_userbyid(n.nspowner) AS owner,
                    obj_description(n.oid) AS description,
                    count(c.oid) FILTER (WHERE c.relkind = 'r') AS table_count,
                    count(c.oid) FILTER (WHERE c.relkind = 'v') AS view_count
                FROM pg_namespace n
                LEFT JOIN pg_class c ON c.relnamespace = n.oid
                    AND c.relkind IN ('r', 'v')
                WHERE n.nspname NOT LIKE 'pg_%'
                    AND n.nspname != 'information_schema'
                GROUP BY n.nspname, n.nspowner, n.oid
                ORDER BY n.nspname
                """
            )
        ]

    async def column_stats(
        self, table_name: str, column_name: str, schema: str
    ) -> dict[str, object]:
        ref = await self.safe_table_ref(schema, table_name)
        col = await self.safe_column_ref(column_name)
        stats = await self.pool.fetchrow(
            """
            SELECT
                s.null_frac,
                s.n_distinct,
                s.most_common_vals::text AS most_common_vals,
                s.most_common_freqs::text AS most_common_freqs,
                s.correlation
            FROM pg_stats s
            WHERE s.schemaname = $1
                AND s.tablename = $2
                AND s.attname = $3
            """,
            schema,
            table_name,
            column_name,
        )
        if stats is None:
            return {"error": f"No statistics for {schema}.{table_name}.{column_name}. Run ANALYZE."}

        async with self.pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                summary_row = await conn.fetchrow(
                    f"""
                    SELECT
                        min({col})::text AS min_value,
                        max({col})::text AS max_value,
                        count(*) AS total_rows,
                        count({col}) AS non_null_count,
                        count(*) - count({col}) AS null_count
                    FROM {ref}
                    """
                )

        summary = dict(summary_row) if summary_row else {}

        result: dict[str, object] = {
            "column": f"{schema}.{table_name}.{column_name}",
            "total_rows": summary.get("total_rows"),
            "non_null_count": summary.get("non_null_count"),
            "null_count": summary.get("null_count"),
            "null_fraction": float(stats["null_frac"]) if stats["null_frac"] is not None else None,
            "n_distinct": float(stats["n_distinct"]) if stats["n_distinct"] is not None else None,
            "min_value": summary.get("min_value"),
            "max_value": summary.get("max_value"),
            "most_common_values": stats["most_common_vals"],
            "most_common_frequencies": stats["most_common_freqs"],
            "correlation": float(stats["correlation"])
            if stats["correlation"] is not None
            else None,
        }
        return result

    # -- Object dependencies --

    async def object_dependencies(
        self, object_name: str, object_type: str, schema: str
    ) -> list[dict[str, object]]:
        if object_type == "function":
            return [
                dict(r)
                for r in await self.pool.fetch(
                    """
                    WITH target AS (
                        SELECT oid FROM pg_proc
                        WHERE proname = $1
                            AND pronamespace = $2::regnamespace
                        LIMIT 1
                    )
                    SELECT DISTINCT
                        dep_ns.nspname AS dependent_schema,
                        dep_cl.relname AS dependent_name,
                        CASE dep_cl.relkind
                            WHEN 'r' THEN 'table'
                            WHEN 'v' THEN 'view'
                            WHEN 'm' THEN 'matview'
                            WHEN 'S' THEN 'sequence'
                            WHEN 'i' THEN 'index'
                            ELSE dep_cl.relkind::text
                        END AS dependent_type
                    FROM pg_depend d
                    JOIN target t ON d.refobjid = t.oid
                    JOIN pg_class dep_cl ON d.objid = dep_cl.oid
                    JOIN pg_namespace dep_ns ON dep_cl.relnamespace = dep_ns.oid
                    WHERE d.deptype IN ('n', 'a')
                        AND d.classid = 'pg_class'::regclass
                    ORDER BY dependent_type, dependent_name
                    """,
                    object_name,
                    schema,
                )
            ]

        relkind = object_type_to_relkind(object_type)
        escaped_name = object_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                WITH target AS (
                    SELECT c.oid
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE c.relname = $1 AND n.nspname = $2 AND c.relkind = $3
                )
                SELECT DISTINCT
                    CASE d.classid
                        WHEN 'pg_class'::regclass THEN
                            CASE dep_cl.relkind
                                WHEN 'r' THEN 'table'
                                WHEN 'v' THEN 'view'
                                WHEN 'm' THEN 'matview'
                                WHEN 'S' THEN 'sequence'
                                WHEN 'i' THEN 'index'
                                ELSE dep_cl.relkind::text
                            END
                        WHEN 'pg_proc'::regclass THEN 'function'
                        WHEN 'pg_constraint'::regclass THEN 'constraint'
                        WHEN 'pg_rewrite'::regclass THEN 'rule'
                        ELSE d.classid::regclass::text
                    END AS dependent_type,
                    CASE d.classid
                        WHEN 'pg_class'::regclass THEN dep_cl.relname
                        WHEN 'pg_proc'::regclass THEN dep_proc.proname
                        WHEN 'pg_constraint'::regclass THEN dep_con.conname
                        WHEN 'pg_rewrite'::regclass THEN dep_rw.rulename
                        ELSE d.objid::text
                    END AS dependent_name,
                    CASE d.classid
                        WHEN 'pg_class'::regclass THEN dep_ns.nspname
                        WHEN 'pg_proc'::regclass THEN proc_ns.nspname
                        WHEN 'pg_constraint'::regclass THEN con_ns.nspname
                        ELSE NULL
                    END AS dependent_schema
                FROM pg_depend d
                JOIN target t ON d.refobjid = t.oid
                LEFT JOIN pg_class dep_cl ON d.classid = 'pg_class'::regclass
                    AND d.objid = dep_cl.oid
                LEFT JOIN pg_namespace dep_ns ON dep_cl.relnamespace = dep_ns.oid
                LEFT JOIN pg_proc dep_proc ON d.classid = 'pg_proc'::regclass
                    AND d.objid = dep_proc.oid
                LEFT JOIN pg_namespace proc_ns ON dep_proc.pronamespace = proc_ns.oid
                LEFT JOIN pg_constraint dep_con ON d.classid = 'pg_constraint'::regclass
                    AND d.objid = dep_con.oid
                LEFT JOIN pg_namespace con_ns ON dep_con.connamespace = con_ns.oid
                LEFT JOIN pg_rewrite dep_rw ON d.classid = 'pg_rewrite'::regclass
                    AND d.objid = dep_rw.oid
                WHERE d.deptype IN ('n', 'a')
                    AND d.objid != t.oid
                    AND NOT (d.classid = 'pg_class'::regclass
                             AND dep_cl.relkind = 'i'
                             AND dep_cl.relname LIKE $4 || '%')
                ORDER BY dependent_type, dependent_name
                """,
                object_name,
                schema,
                relkind,
                escaped_name,
            )
        ]

    # -- Lock & session monitor --

    async def active_queries(self) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    pid,
                    usename,
                    application_name,
                    client_addr::text,
                    state,
                    wait_event_type,
                    wait_event,
                    query,
                    now() - query_start AS query_duration,
                    now() - state_change AS state_duration
                FROM pg_stat_activity
                WHERE pid != pg_backend_pid()
                    AND state IS NOT NULL
                ORDER BY query_start ASC NULLS LAST
                """
            )
        ]

    async def blocking_locks(self) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    blocked.pid AS blocked_pid,
                    blocked.usename AS blocked_user,
                    blocked.query AS blocked_query,
                    now() - blocked.query_start AS blocked_duration,
                    blocker.pid AS blocker_pid,
                    blocker.usename AS blocker_user,
                    blocker.query AS blocker_query,
                    blocker.state AS blocker_state
                FROM pg_stat_activity blocked
                JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS bp(pid) ON true
                JOIN pg_stat_activity blocker ON blocker.pid = bp.pid
                WHERE blocked.state = 'active'
                    AND blocked.wait_event_type = 'Lock'
                ORDER BY blocked_duration DESC
                """
            )
        ]

    # -- Size & bloat analysis --

    async def table_sizes(self, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    c.relname AS table_name,
                    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
                    pg_size_pretty(pg_table_size(c.oid)) AS table_size,
                    pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size,
                    pg_total_relation_size(c.oid) AS total_bytes
                FROM pg_class c
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = $1 AND c.relkind = 'r'
                ORDER BY pg_total_relation_size(c.oid) DESC
                """,
                schema,
            )
        ]

    async def unused_indexes(self, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    s.indexrelname AS index_name,
                    s.relname AS table_name,
                    pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size,
                    pg_relation_size(s.indexrelid) AS index_bytes,
                    s.idx_scan AS scans_since_reset,
                    pg_get_indexdef(s.indexrelid) AS definition
                FROM pg_stat_user_indexes s
                JOIN pg_index i ON s.indexrelid = i.indexrelid
                WHERE s.schemaname = $1
                    AND s.idx_scan = 0
                    AND NOT i.indisunique
                    AND NOT i.indisprimary
                ORDER BY pg_relation_size(s.indexrelid) DESC
                """,
                schema,
            )
        ]

    async def bloat_stats(self, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    s.relname AS table_name,
                    s.n_live_tup,
                    s.n_dead_tup,
                    CASE WHEN s.n_live_tup > 0
                        THEN round(100.0 * s.n_dead_tup / s.n_live_tup, 1)
                    END AS dead_tuple_pct,
                    age(c.relfrozenxid) AS xid_age,
                    round(
                        100.0 * age(c.relfrozenxid)
                        / current_setting('autovacuum_freeze_max_age')::bigint, 1
                    ) AS wraparound_pct,
                    s.last_vacuum,
                    s.last_autovacuum,
                    s.last_analyze,
                    s.last_autoanalyze
                FROM pg_stat_user_tables s
                JOIN pg_class c ON c.relname = s.relname
                JOIN pg_namespace n ON c.relnamespace = n.oid
                    AND n.nspname = s.schemaname
                WHERE s.schemaname = $1
                ORDER BY s.n_dead_tup DESC
                """,
                schema,
            )
        ]

    # -- Functions, triggers, policies --

    async def list_functions(self, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    p.proname AS function_name,
                    pg_get_function_arguments(p.oid) AS arguments,
                    pg_get_function_result(p.oid) AS return_type,
                    l.lanname AS language,
                    CASE p.provolatile
                        WHEN 'i' THEN 'immutable'
                        WHEN 's' THEN 'stable'
                        WHEN 'v' THEN 'volatile'
                    END AS volatility,
                    CASE p.prokind
                        WHEN 'f' THEN 'function'
                        WHEN 'p' THEN 'procedure'
                        WHEN 'a' THEN 'aggregate'
                        WHEN 'w' THEN 'window'
                    END AS kind,
                    obj_description(p.oid) AS description,
                    p.prosrc AS source
                FROM pg_proc p
                JOIN pg_namespace n ON p.pronamespace = n.oid
                JOIN pg_language l ON p.prolang = l.oid
                WHERE n.nspname = $1
                    AND p.prokind != 'a'
                ORDER BY p.proname
                """,
                schema,
            )
        ]

    async def list_triggers(self, table_name: str, schema: str) -> list[dict[str, object]]:
        ref = await self.safe_table_ref(schema, table_name)
        return [
            dict(r)
            for r in await self.pool.fetch(
                f"""
                SELECT
                    t.tgname AS trigger_name,
                    pg_get_triggerdef(t.oid) AS definition,
                    CASE t.tgenabled
                        WHEN 'O' THEN 'enabled'
                        WHEN 'D' THEN 'disabled'
                        WHEN 'R' THEN 'replica'
                        WHEN 'A' THEN 'always'
                    END AS status,
                    p.proname AS function_name
                FROM pg_trigger t
                JOIN pg_proc p ON t.tgfoid = p.oid
                WHERE t.tgrelid = {ref}::regclass
                    AND NOT t.tgisinternal
                ORDER BY t.tgname
                """,
            )
        ]

    async def list_policies(self, table_name: str, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    pol.polname AS policy_name,
                    CASE pol.polcmd
                        WHEN 'r' THEN 'SELECT'
                        WHEN 'a' THEN 'INSERT'
                        WHEN 'w' THEN 'UPDATE'
                        WHEN 'd' THEN 'DELETE'
                        WHEN '*' THEN 'ALL'
                    END AS command,
                    CASE WHEN pol.polpermissive THEN 'permissive'
                        ELSE 'restrictive'
                    END AS type,
                    pg_get_expr(pol.polqual, pol.polrelid) AS using_expression,
                    pg_get_expr(pol.polwithcheck, pol.polrelid) AS with_check,
                    array(
                        SELECT rolname FROM pg_roles
                        WHERE oid = ANY(pol.polroles)
                    ) AS roles,
                    c.relrowsecurity AS rls_enabled,
                    c.relforcerowsecurity AS rls_forced
                FROM pg_policy pol
                JOIN pg_class c ON pol.polrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = $1 AND c.relname = $2
                ORDER BY pol.polname
                """,
                schema,
                table_name,
            )
        ]

    # -- Sequences & materialized view health --

    async def sequence_health(self, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    s.sequencename AS sequence_name,
                    s.last_value,
                    s.start_value,
                    s.min_value,
                    s.max_value,
                    s.increment_by,
                    s.cycle AS is_cycled,
                    CASE WHEN s.max_value != s.min_value AND s.last_value IS NOT NULL
                        THEN round(
                            100.0 * (s.last_value - s.min_value)
                            / (s.max_value - s.min_value), 2
                        )
                    END AS pct_consumed
                FROM pg_sequences s
                WHERE s.schemaname = $1
                ORDER BY pct_consumed DESC NULLS LAST
                """,
                schema,
            )
        ]

    async def matview_status(self, schema: str) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in await self.pool.fetch(
                """
                SELECT
                    m.matviewname AS matview_name,
                    m.ispopulated,
                    m.definition,
                    pg_size_pretty(pg_total_relation_size(
                        (quote_ident(m.schemaname) || '.' ||
                         quote_ident(m.matviewname))::regclass
                    )) AS total_size,
                    EXISTS (
                        SELECT 1 FROM pg_index i
                        WHERE i.indrelid = (
                            quote_ident(m.schemaname) || '.' ||
                            quote_ident(m.matviewname)
                        )::regclass AND i.indisunique
                    ) AS has_unique_index
                FROM pg_matviews m
                WHERE m.schemaname = $1
                ORDER BY m.matviewname
                """,
                schema,
            )
        ]

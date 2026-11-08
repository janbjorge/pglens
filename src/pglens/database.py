"""Query class backed by asyncpg."""

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import cast

import asyncpg

from pglens.sql import quote_ident, validate_select
from pglens.types import DetailLevel

# describe_object kinds that live in pg_class, mapped to relkind.
RELKIND = {"table": "r", "view": "v", "matview": "m", "sequence": "S", "index": "i"}

# (result key, progress view, extra select list) for maintenance_progress. The views
# gain columns with each release, so they are read whole rather than column by column.
PROGRESS_VIEWS = (
    ("vacuum", "pg_stat_progress_vacuum", ""),
    ("analyze", "pg_stat_progress_analyze", ""),
    (
        "create_index",
        "pg_stat_progress_create_index",
        "p.index_relid::regclass::text AS index_name, ",
    ),
    ("cluster", "pg_stat_progress_cluster", ""),
)

# One row per column pair of every foreign key, composite keys included. Callers
# prepend the SELECT list and append conditions on the referencing side (ns1, cl1,
# a1) or the referenced side (ns2, cl2, a2).
FOREIGN_KEY_PAIRS = """
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
"""


def truncate_cell(value: object, max_chars: int) -> object:
    """Cut a text cell longer than max_chars and mark it with its full length.

    Only str cells are touched: numbers, bytes, timestamps and NULLs pass
    through unchanged. max_chars <= 0 disables the cut.
    """
    if max_chars > 0 and isinstance(value, str) and len(value) > max_chars:
        return f"{value[:max_chars]}…[truncated, {len(value)} chars total]"
    return value


def select_detail(
    rows: list[dict[str, object]],
    detail: DetailLevel,
    names: tuple[str, ...],
    heavy: tuple[str, ...],
) -> list[dict[str, object]]:
    """Trim rows to the requested detail level.

    names keeps only the identifying keys, summary drops the heavy keys
    (definitions, source code), full returns the rows untouched.
    """
    match detail:
        case "names":
            return [{key: row[key] for key in names} for row in rows]
        case "summary":
            return [{key: value for key, value in row.items() if key not in heavy} for row in rows]
        case _:
            return rows


def like_pattern(keyword: str) -> str:
    """Escape LIKE wildcards so keyword matches literally inside '%' || $n || '%'."""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class AsyncpgDatabase:
    pool: asyncpg.Pool
    # Cached numeric server version (e.g. 180001). None until first use; injectable in tests.
    server_version_num: int | None = None
    # Longest text cell query returns. Longer cells are cut and marked; 0 disables the cut.
    max_cell_chars: int = 1000

    async def _get_server_version_num(self) -> int:
        if self.server_version_num is None:
            self.server_version_num = cast(
                int,
                await self.pool.fetchval("SELECT current_setting('server_version_num')::int"),
            )
        return self.server_version_num

    async def _fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        """Run a catalogue query: fixed SQL text, values bound as parameters."""
        return [dict(r) for r in await self.pool.fetch(sql, *args)]

    async def _fetch_user(self, sql: str, *args: object) -> list[dict[str, object]]:
        """Run SQL that carries user input (quoted identifiers or a validated SELECT).

        The pool already sets default_transaction_read_only=on; the explicit
        read-only transaction is the second lock on the door.
        """
        async with self.pool.acquire() as conn, conn.transaction(readonly=True):
            return [dict(r) for r in await conn.fetch(sql, *args)]

    def safe_table_ref(self, schema: str, table_name: str) -> str:
        return f"{quote_ident(schema)}.{quote_ident(table_name)}"

    async def database_info(self) -> dict[str, object]:
        row = await self.pool.fetchrow(
            """
            SELECT
                current_database() AS database_name,
                current_user AS current_user,
                version() AS version,
                current_setting('server_version') AS server_version,
                current_setting('server_encoding') AS server_encoding,
                current_setting('TimeZone') AS timezone,
                current_setting('max_connections') AS max_connections,
                pg_postmaster_start_time() AS server_start_time,
                now() - pg_postmaster_start_time() AS uptime,
                pg_size_pretty(pg_database_size(current_database())) AS database_size,
                pg_database_size(current_database()) AS database_size_bytes
            """
        )
        return dict(row) if row else {}

    async def list_schemas(self) -> list[dict[str, object]]:
        return await self._fetch(
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

    async def list_extensions(self) -> list[dict[str, object]]:
        return await self._fetch(
            """
            SELECT extname, extversion, obj_description(e.oid, 'pg_extension') AS description
            FROM pg_extension e
            ORDER BY extname
            """
        )

    # -- Catalogue listings, one schema at a time --

    async def list_tables(
        self, schema: str, limit: int = 100, pattern: str | None = None
    ) -> list[dict[str, object]]:
        return await self._fetch(
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
                AND ($2::text IS NULL OR t.table_name ILIKE '%' || $2 || '%')
            ORDER BY s.n_live_tup DESC NULLS LAST
            LIMIT $3
            """,
            schema,
            like_pattern(pattern) if pattern else None,
            limit,
        )

    async def list_views(
        self,
        schema: str,
        limit: int = 100,
        detail: DetailLevel = "summary",
        pattern: str | None = None,
    ) -> list[dict[str, object]]:
        rows = await self._fetch(
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
                AND ($2::text IS NULL OR v.table_name ILIKE '%' || $2 || '%')
            ORDER BY v.table_name
            LIMIT $3
            """,
            schema,
            like_pattern(pattern) if pattern else None,
            limit,
        )
        return select_detail(rows, detail, names=("view_name",), heavy=("definition",))

    async def matview_status(
        self,
        schema: str,
        limit: int = 100,
        detail: DetailLevel = "summary",
        pattern: str | None = None,
    ) -> list[dict[str, object]]:
        rows = await self._fetch(
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
                AND ($2::text IS NULL OR m.matviewname ILIKE '%' || $2 || '%')
            ORDER BY m.matviewname
            LIMIT $3
            """,
            schema,
            like_pattern(pattern) if pattern else None,
            limit,
        )
        return select_detail(rows, detail, names=("matview_name",), heavy=("definition",))

    async def list_indexes(
        self,
        schema: str,
        limit: int = 100,
        detail: DetailLevel = "summary",
        pattern: str | None = None,
    ) -> list[dict[str, object]]:
        rows = await self._fetch(
            """
            SELECT
                i.relname AS index_name,
                t.relname AS table_name,
                ix.indisunique AS is_unique,
                ix.indisprimary AS is_primary,
                am.amname AS index_type,
                pg_get_indexdef(ix.indexrelid) AS definition,
                ix.indisvalid AS is_valid,
                pg_size_pretty(pg_relation_size(ix.indexrelid)) AS index_size,
                pg_relation_size(ix.indexrelid) AS index_size_bytes,
                s.idx_scan AS scans_since_reset,
                s.idx_tup_read AS tuples_read,
                s.idx_tup_fetch AS tuples_fetched
            FROM pg_index ix
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN pg_namespace n ON t.relnamespace = n.oid
            JOIN pg_am am ON i.relam = am.oid
            LEFT JOIN pg_stat_user_indexes s
                ON s.indexrelid = ix.indexrelid
            WHERE n.nspname = $1
                AND ($2::text IS NULL OR i.relname ILIKE '%' || $2 || '%')
            ORDER BY t.relname, i.relname
            LIMIT $3
            """,
            schema,
            like_pattern(pattern) if pattern else None,
            limit,
        )
        return select_detail(
            rows, detail, names=("index_name", "table_name"), heavy=("definition",)
        )

    async def list_functions(
        self,
        schema: str,
        limit: int = 100,
        detail: DetailLevel = "summary",
        pattern: str | None = None,
    ) -> list[dict[str, object]]:
        rows = await self._fetch(
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
                AND ($2::text IS NULL OR p.proname ILIKE '%' || $2 || '%')
            ORDER BY p.proname
            LIMIT $3
            """,
            schema,
            like_pattern(pattern) if pattern else None,
            limit,
        )
        return select_detail(rows, detail, names=("function_name",), heavy=("source",))

    async def sequence_health(
        self, schema: str, limit: int = 100, pattern: str | None = None
    ) -> list[dict[str, object]]:
        return await self._fetch(
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
                        CASE WHEN s.increment_by < 0
                            THEN 100.0 * (s.max_value - s.last_value)
                                / (s.max_value - s.min_value)
                            ELSE 100.0 * (s.last_value - s.min_value)
                                / (s.max_value - s.min_value)
                        END, 2
                    )
                END AS pct_consumed
            FROM pg_sequences s
            WHERE s.schemaname = $1
                AND ($2::text IS NULL OR s.sequencename ILIKE '%' || $2 || '%')
            ORDER BY pct_consumed DESC NULLS LAST
            LIMIT $3
            """,
            schema,
            like_pattern(pattern) if pattern else None,
            limit,
        )

    async def search_columns(
        self, keyword: str, schema: str, limit: int = 100
    ) -> list[dict[str, object]]:
        return await self._fetch(
            """
            SELECT table_name, column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = $1
                AND column_name ILIKE '%' || $2 || '%'
            ORDER BY table_name, ordinal_position
            LIMIT $3
            """,
            schema,
            like_pattern(keyword),
            limit,
        )

    async def search_enum_values(
        self, keyword: str, schema: str, limit: int = 100
    ) -> list[dict[str, object]]:
        return await self._fetch(
            """
            SELECT
                n.nspname AS schema,
                t.typname AS enum_name,
                array_agg(e.enumlabel ORDER BY e.enumsortorder) AS values
            FROM pg_type t
            JOIN pg_namespace n ON t.typnamespace = n.oid
            JOIN pg_enum e ON t.oid = e.enumtypid
            WHERE n.nspname = $1
                AND t.typname ILIKE '%' || $2 || '%'
            GROUP BY n.nspname, t.typname
            ORDER BY t.typname
            LIMIT $3
            """,
            schema,
            like_pattern(keyword),
            limit,
        )

    async def describe_view(self, name: str, schema: str) -> list[dict[str, object]]:
        return await self._fetch(
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
            WHERE v.table_schema = $1 AND v.table_name = $2
            """,
            schema,
            name,
        )

    async def describe_matview(self, name: str, schema: str) -> list[dict[str, object]]:
        return await self._fetch(
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
            WHERE m.schemaname = $1 AND m.matviewname = $2
            """,
            schema,
            name,
        )

    async def describe_index(self, name: str, schema: str) -> list[dict[str, object]]:
        return await self._fetch(
            """
            SELECT
                i.relname AS index_name,
                t.relname AS table_name,
                ix.indisunique AS is_unique,
                ix.indisprimary AS is_primary,
                am.amname AS index_type,
                pg_get_indexdef(ix.indexrelid) AS definition,
                ix.indisvalid AS is_valid,
                pg_size_pretty(pg_relation_size(ix.indexrelid)) AS index_size,
                pg_relation_size(ix.indexrelid) AS index_size_bytes,
                s.idx_scan AS scans_since_reset,
                s.idx_tup_read AS tuples_read,
                s.idx_tup_fetch AS tuples_fetched
            FROM pg_index ix
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN pg_namespace n ON t.relnamespace = n.oid
            JOIN pg_am am ON i.relam = am.oid
            LEFT JOIN pg_stat_user_indexes s
                ON s.indexrelid = ix.indexrelid
            WHERE n.nspname = $1 AND i.relname = $2
            ORDER BY t.relname, i.relname
            """,
            schema,
            name,
        )

    async def describe_function(self, name: str, schema: str) -> list[dict[str, object]]:
        return await self._fetch(
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
            WHERE n.nspname = $1 AND p.proname = $2 AND p.prokind != 'a'
            ORDER BY p.proname
            """,
            schema,
            name,
        )

    async def describe_sequence(self, name: str, schema: str) -> list[dict[str, object]]:
        return await self._fetch(
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
                        CASE WHEN s.increment_by < 0
                            THEN 100.0 * (s.max_value - s.last_value)
                                / (s.max_value - s.min_value)
                            ELSE 100.0 * (s.last_value - s.min_value)
                                / (s.max_value - s.min_value)
                        END, 2
                    )
                END AS pct_consumed
            FROM pg_sequences s
            WHERE s.schemaname = $1 AND s.sequencename = $2
            """,
            schema,
            name,
        )

    # -- One table in depth --

    async def describe_table(
        self, table_name: str, schema: str, detail: DetailLevel = "summary"
    ) -> dict[str, object]:
        ref = self.safe_table_ref(schema, table_name)
        columns = await self._fetch(
            """
            SELECT
                c.column_name,
                c.data_type,
                c.udt_name,
                c.is_nullable,
                c.column_default,
                col_description($1::regclass, c.ordinal_position) AS description
            FROM information_schema.columns c
            WHERE c.table_schema = $2 AND c.table_name = $3
            ORDER BY c.ordinal_position
            """,
            ref,
            schema,
            table_name,
        )
        primary_keys = [
            r["column_name"]
            for r in await self._fetch(
                """
                SELECT a.attname AS column_name
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid
                    AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = $1::regclass AND i.indisprimary
                """,
                ref,
            )
        ]
        if detail == "names":
            return {
                "table": f"{schema}.{table_name}",
                "columns": [c["column_name"] for c in columns],
                "primary_keys": primary_keys,
            }
        foreign_keys = await self._fetch(
            f"""
            SELECT
                a1.attname AS column_name,
                ns2.nspname AS foreign_schema,
                cl2.relname AS foreign_table,
                a2.attname AS foreign_column
            {FOREIGN_KEY_PAIRS}
                AND ns1.nspname = $1 AND cl1.relname = $2
            ORDER BY con.conname, u.ord
            """,
            schema,
            table_name,
        )
        referenced_by = await self._fetch(
            f"""
            SELECT
                ns1.nspname AS from_schema,
                cl1.relname AS from_table,
                a1.attname AS from_column,
                a2.attname AS to_column
            {FOREIGN_KEY_PAIRS}
                AND ns2.nspname = $1 AND cl2.relname = $2
            ORDER BY con.conname, u.ord
            """,
            schema,
            table_name,
        )
        indexes = await self._fetch(
            """
            SELECT
                i.relname AS index_name,
                ix.indisunique AS is_unique,
                pg_get_indexdef(ix.indexrelid) AS definition
            FROM pg_index ix
            JOIN pg_class i ON i.oid = ix.indexrelid
            WHERE ix.indrelid = $1::regclass
            ORDER BY i.relname
            """,
            ref,
        )
        check_constraints = await self._fetch(
            """
            SELECT
                conname AS constraint_name,
                pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid = $1::regclass AND contype = 'c'
            ORDER BY conname
            """,
            ref,
        )
        return {
            "table": f"{schema}.{table_name}",
            "columns": columns,
            "primary_keys": primary_keys,
            "foreign_keys": foreign_keys,
            "referenced_by": referenced_by,
            "indexes": select_detail(indexes, detail, names=("index_name",), heavy=("definition",)),
            "check_constraints": check_constraints,
        }

    async def list_triggers(self, table_name: str, schema: str) -> list[dict[str, object]]:
        return await self._fetch(
            """
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
            WHERE t.tgrelid = $1::regclass
                AND NOT t.tgisinternal
            ORDER BY t.tgname
            """,
            self.safe_table_ref(schema, table_name),
        )

    async def list_policies(self, table_name: str, schema: str) -> list[dict[str, object]]:
        return await self._fetch(
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

    async def object_dependencies(
        self, object_name: str, object_type: str, schema: str
    ) -> list[dict[str, object]]:
        """Objects that would break or be dropped with the target, by type and identity.

        pg_identify_object() names each dependent. A view reaches its base tables
        through its _RETURN rule, so pg_rewrite rows are mapped back to the view.
        Normal dependencies are other objects; auto dependencies are the target's
        own parts (indexes, constraints, triggers, defaults) and are skipped, except
        owned sequences and partitions, which go with the table.
        """
        if object_type == "function":
            targets = await self._fetch(
                """
                SELECT p.oid FROM pg_proc p
                JOIN pg_namespace n ON p.pronamespace = n.oid
                WHERE p.proname = $1 AND n.nspname = $2
                """,
                object_name,
                schema,
            )
        else:
            targets = await self._fetch(
                """
                SELECT c.oid FROM pg_class c
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE c.relname = $1 AND n.nspname = $2 AND c.relkind::text = $3
                """,
                object_name,
                schema,
                RELKIND[object_type],
            )
        return await self._fetch(
            """
            SELECT DISTINCT obj.type AS dependent_type, obj.identity AS dependent
            FROM pg_depend d
            LEFT JOIN pg_rewrite rw ON d.classid = 'pg_rewrite'::regclass AND d.objid = rw.oid
            CROSS JOIN LATERAL pg_identify_object(
                CASE WHEN rw.oid IS NULL THEN d.classid ELSE 'pg_class'::regclass END,
                coalesce(rw.ev_class, d.objid),
                0
            ) AS obj
            WHERE d.refobjid = ANY($1::oid[])
                AND coalesce(rw.ev_class, d.objid) <> ALL($1::oid[])
                AND (d.deptype = 'n' OR (d.deptype = 'a' AND obj.type IN ('table', 'sequence')))
            ORDER BY 1, 2
            """,
            [row["oid"] for row in targets],
        )

    async def find_join_path(
        self, source_table: str, target_table: str, schema: str, max_depth: int
    ) -> dict[str, object]:
        fk_rows = await self._fetch(
            f"""
            SELECT
                con.oid AS constraint_oid,
                cl1.relname AS from_table,
                a1.attname AS from_column,
                cl2.relname AS to_table,
                a2.attname AS to_column
            {FOREIGN_KEY_PAIRS}
                AND ns1.nspname = $1
                AND ns2.nspname = $1
            ORDER BY con.oid, u.ord
            """,
            schema,
        )
        # One edge per foreign key, not per column pair: joining on part of a
        # composite key fans out.
        keys: dict[object, list[dict[str, object]]] = defaultdict(list)
        for row in fk_rows:
            keys[row["constraint_oid"]].append(row)
        # Undirected adjacency: a foreign key can be joined from either side.
        graph: dict[str, list[tuple[str, list[tuple[str, str]]]]] = defaultdict(list)
        for pairs in keys.values():
            fr, to = str(pairs[0]["from_table"]), str(pairs[0]["to_table"])
            columns = [(str(p["from_column"]), str(p["to_column"])) for p in pairs]
            graph[fr].append((to, columns))
            graph[to].append((fr, [(tc, fc) for fc, tc in columns]))

        # Breadth-first search over simple paths of at most max_depth hops.
        paths: list[list[dict[str, str]]] = []
        queue: deque[tuple[str, list[dict[str, str]], set[str]]] = deque()
        queue.append((source_table, [], {source_table}))
        while queue:
            current, path, visited = queue.popleft()
            if current == target_table and path:
                paths.append(path)
                continue
            if len(path) >= max_depth:
                continue
            for nxt, columns in graph.get(current, []):
                if nxt not in visited:
                    hop = {
                        "to_table": nxt,
                        "join": " AND ".join(f"{current}.{fc} = {nxt}.{tc}" for fc, tc in columns),
                    }
                    queue.append((nxt, [*path, hop], visited | {nxt}))

        formatted: list[dict[str, object]] = [
            {
                "tables": [source_table, *(hop["to_table"] for hop in path)],
                "joins": [hop["join"] for hop in path],
                "hops": len(path),
            }
            for path in paths  # BFS yields shortest paths first
        ]
        return {
            "source": source_table,
            "target": target_table,
            "schema": schema,
            "paths_found": len(formatted),
            "paths": formatted,
        }

    # -- Data: user-influenced SQL, run inside explicit read-only transactions --

    async def column_values(
        self, table_name: str, column_name: str, top_n: int, schema: str
    ) -> list[dict[str, object]]:
        ref = self.safe_table_ref(schema, table_name)
        col = quote_ident(column_name)
        return await self._fetch_user(
            f"""
            SELECT
                {col}::text AS value,
                count(*) AS frequency
            FROM {ref}
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT $1
            """,
            top_n,
        )

    async def column_stats(
        self, table_name: str, column_name: str, schema: str
    ) -> dict[str, object]:
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

        ref = self.safe_table_ref(schema, table_name)
        col = quote_ident(column_name)
        counts = (
            f"count(*) AS total_rows, count({col}) AS non_null_count, "
            f"count(*) - count({col}) AS null_count FROM {ref}"
        )
        try:
            [summary] = await self._fetch_user(
                f"SELECT min({col})::text AS min_value, max({col})::text AS max_value, {counts}"
            )
        except asyncpg.UndefinedFunctionError:
            # Types without ordering (json, point, xid, ...) have no min/max
            [summary] = await self._fetch_user(
                f"SELECT NULL::text AS min_value, NULL::text AS max_value, {counts}"
            )
        return {
            "column": f"{schema}.{table_name}.{column_name}",
            "total_rows": summary["total_rows"],
            "non_null_count": summary["non_null_count"],
            "null_count": summary["null_count"],
            "null_fraction": stats["null_frac"],
            "n_distinct": stats["n_distinct"],
            "min_value": summary["min_value"],
            "max_value": summary["max_value"],
            "most_common_values": stats["most_common_vals"],
            "most_common_frequencies": stats["most_common_freqs"],
            "correlation": stats["correlation"],
        }

    async def query(self, sql: str, limit: int = 500, offset: int = 0) -> list[dict[str, object]]:
        rows = await self._fetch_user(
            f"SELECT * FROM ({validate_select(sql)}) sub LIMIT $1 OFFSET $2", limit, offset
        )
        return [
            {key: truncate_cell(value, self.max_cell_chars) for key, value in row.items()}
            for row in rows
        ]

    async def explain_query(self, sql: str, analyze: bool = False, buffers: bool = False) -> str:
        normalized = validate_select(sql, allow_cursor=True)
        # Omit BUFFERS when false so the server default applies (on with ANALYZE since PG18).
        options = (
            f"ANALYZE {analyze}, BUFFERS TRUE, FORMAT TEXT"
            if buffers
            else f"ANALYZE {analyze}, FORMAT TEXT"
        )
        rows = await self._fetch_user(f"EXPLAIN ({options}) {normalized}")
        return "\n".join(str(r["QUERY PLAN"]) for r in rows)

    # -- Health --

    async def table_health(self, schema: str, limit: int = 100) -> list[dict[str, object]]:
        # PG18: cumulative maintenance timings on pg_stat_all_tables, relallfrozen on pg_class.
        pg18_columns = (
            """
                round(s.total_vacuum_time::numeric, 1) AS total_vacuum_time_ms,
                round(s.total_autovacuum_time::numeric, 1) AS total_autovacuum_time_ms,
                round(s.total_analyze_time::numeric, 1) AS total_analyze_time_ms,
                round(s.total_autoanalyze_time::numeric, 1) AS total_autoanalyze_time_ms,
                c.relallfrozen,
                CASE WHEN c.relpages > 0
                    THEN round(100.0 * c.relallfrozen / c.relpages, 1)
                END AS frozen_pct,
            """
            if await self._get_server_version_num() >= 180000
            else ""
        )
        return await self._fetch(
            f"""
            SELECT
                s.relname AS table_name,
                s.seq_scan,
                s.idx_scan,
                CASE WHEN (s.seq_scan + s.idx_scan) > 0
                    THEN round(100.0 * s.idx_scan / (s.seq_scan + s.idx_scan), 1)
                END AS index_hit_pct,
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
                {pg18_columns}
                s.last_vacuum,
                s.last_autovacuum,
                s.last_analyze,
                s.last_autoanalyze
            FROM pg_stat_user_tables s
            JOIN pg_class c ON c.oid = s.relid
            WHERE s.schemaname = $1
            ORDER BY s.n_live_tup DESC NULLS LAST
            LIMIT $2
            """,
            schema,
            limit,
        )

    async def table_sizes(self, schema: str, limit: int = 100) -> list[dict[str, object]]:
        return await self._fetch(
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
            LIMIT $2
            """,
            schema,
            limit,
        )

    async def unused_indexes(self, schema: str, limit: int = 100) -> list[dict[str, object]]:
        return await self._fetch(
            """
            SELECT
                s.indexrelname AS index_name,
                s.relname AS table_name,
                pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size,
                pg_relation_size(s.indexrelid) AS index_bytes,
                s.idx_scan AS scans_since_reset,
                i.indisvalid AS is_valid,
                pg_get_indexdef(s.indexrelid) AS definition
            FROM pg_stat_user_indexes s
            JOIN pg_index i ON s.indexrelid = i.indexrelid
            WHERE s.schemaname = $1
                AND s.idx_scan = 0
                AND NOT i.indisunique
                AND NOT i.indisprimary
            ORDER BY pg_relation_size(s.indexrelid) DESC
            LIMIT $2
            """,
            schema,
            limit,
        )

    async def io_stats(self) -> list[dict[str, object]]:
        version = await self._get_server_version_num()
        if version < 160000:
            return [
                {
                    "error": "pg_stat_io requires PostgreSQL 16 or newer "
                    f"(connected server is {version // 10000})."
                }
            ]
        # Whole rows: PostgreSQL 18 added byte counters, later releases will add more.
        return await self._fetch(
            """
            SELECT *
            FROM pg_stat_io
            WHERE coalesce(reads, 0) + coalesce(writes, 0) + coalesce(extends, 0)
                + coalesce(hits, 0) + coalesce(evictions, 0) + coalesce(reuses, 0)
                + coalesce(fsyncs, 0) + coalesce(writebacks, 0) <> 0
            ORDER BY backend_type, object, context
            """
        )

    async def slow_queries(self, limit: int) -> list[dict[str, object]]:
        installed = await self.pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')"
        )
        if not installed:
            return [
                {
                    "error": "pg_stat_statements extension is not installed. "
                    "Enable it with CREATE EXTENSION pg_stat_statements "
                    "(requires shared_preload_libraries = 'pg_stat_statements' "
                    "and a server restart)."
                }
            ]
        # Gate on column existence, not server version: a pg_upgraded cluster can run
        # an older pg_stat_statements schema on a newer server.
        has_pg18_columns = await self.pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_attribute
                WHERE attrelid = to_regclass('pg_stat_statements')
                  AND attname = 'wal_buffers_full'
            )
            """
        )
        pg18_columns = (
            """
                s.parallel_workers_to_launch,
                s.parallel_workers_launched,
                s.wal_buffers_full,
            """
            if has_pg18_columns
            else ""
        )
        return await self._fetch(
            f"""
            SELECT
                r.rolname AS username,
                d.datname AS database,
                s.calls,
                round(s.total_exec_time::numeric, 1) AS total_exec_time_ms,
                round(s.mean_exec_time::numeric, 2) AS mean_exec_time_ms,
                round(s.stddev_exec_time::numeric, 2) AS stddev_exec_time_ms,
                s.rows,
                round(
                    100.0 * s.shared_blks_hit
                    / nullif(s.shared_blks_hit + s.shared_blks_read, 0), 1
                ) AS cache_hit_pct,
                {pg18_columns}
                s.query
            FROM pg_stat_statements s
            JOIN pg_roles r ON r.oid = s.userid
            JOIN pg_database d ON d.oid = s.dbid
            ORDER BY s.total_exec_time DESC
            LIMIT $1
            """,
            limit,
        )

    # -- Activity --

    async def active_queries(self) -> list[dict[str, object]]:
        return await self._fetch(
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

    async def blocking_locks(self) -> list[dict[str, object]]:
        return await self._fetch(
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

    async def maintenance_progress(self) -> dict[str, object]:
        return {
            key: await self._fetch(
                f"""
                SELECT n.nspname AS schema, c.relname AS table_name, {extra}p.*
                FROM {view} p
                LEFT JOIN pg_class c ON c.oid = p.relid
                LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
                ORDER BY p.pid
                """
            )
            for key, view, extra in PROGRESS_VIEWS
        }

    async def replication_status(self) -> dict[str, object]:
        in_recovery = await self.pool.fetchval("SELECT pg_is_in_recovery()")
        standbys = await self._fetch(
            """
            SELECT
                pid,
                usename,
                application_name,
                client_addr::text,
                state,
                sync_state,
                write_lag,
                flush_lag,
                replay_lag,
                CASE WHEN pg_is_in_recovery() THEN NULL
                    ELSE pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)
                END AS replay_lag_bytes
            FROM pg_stat_replication
            ORDER BY application_name, pid
            """
        )
        slots = await self._fetch(
            """
            SELECT
                slot_name,
                slot_type,
                plugin,
                database,
                active,
                wal_status,
                safe_wal_size,
                CASE WHEN pg_is_in_recovery() OR restart_lsn IS NULL THEN NULL
                    ELSE pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)
                END AS retained_wal_bytes
            FROM pg_replication_slots
            ORDER BY slot_name
            """
        )
        subscriptions: list[dict[str, object]] = []
        if await self._get_server_version_num() >= 150000:
            # Whole rows: PostgreSQL 18 added per-type conflict counters.
            subscriptions = await self._fetch(
                "SELECT * FROM pg_stat_subscription_stats ORDER BY subname"
            )
        return {
            "in_recovery": in_recovery,
            "standbys": standbys,
            "slots": slots,
            "subscriptions": subscriptions,
        }

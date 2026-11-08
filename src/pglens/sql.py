"""SQL validation using pglast (PostgreSQL's own parser)."""

from pglast import ast, parse_sql
from pglast.stream import RawStream
from pglast.visitors import Ancestor, Visitor

# Functions a SELECT can call that act beyond its own result. A read-only
# transaction does not stop them: they signal other sessions, change settings
# (set_config can lift statement_timeout), take session locks, reset statistics,
# write WAL, create replication slots, consume logical changes, rewrite planner
# statistics or catalogue rows, or touch server files and other databases.
# This is a denylist: a view or a user-defined function can still wrap one, so
# the role's privileges remain the real boundary.
SIDE_EFFECT_FUNCTIONS = frozenset(
    {
        "lo_export",
        "pg_cancel_backend",
        "pg_clear_attribute_stats",
        "pg_clear_relation_stats",
        "pg_copy_logical_replication_slot",
        "pg_copy_physical_replication_slot",
        "pg_create_logical_replication_slot",
        "pg_create_physical_replication_slot",
        "pg_create_restore_point",
        "pg_drop_replication_slot",
        "pg_import_system_collations",
        "pg_log_backend_memory_contexts",
        "pg_log_standby_snapshot",
        "pg_logical_emit_message",
        "pg_logical_slot_get_binary_changes",
        "pg_logical_slot_get_changes",
        "pg_notify",
        "pg_promote",
        "pg_reload_conf",
        "pg_replication_slot_advance",
        "pg_restore_attribute_stats",
        "pg_restore_relation_stats",
        "pg_rotate_logfile",
        "pg_stat_statements_reset",
        "pg_switch_wal",
        "pg_sync_replication_slots",
        "pg_terminate_backend",
        "set_config",
    }
)
SIDE_EFFECT_PREFIXES = (
    "dblink",
    "pg_advisory_",
    "pg_backup_",
    "pg_replication_origin_",
    "pg_stat_reset",
    "pg_try_advisory_",
    "pg_wal_replay_",
)


def quote_ident(name: str) -> str:
    """Quote a SQL identifier, matching Postgres quote_ident semantics."""
    return '"' + name.replace('"', '""') + '"'


class _RejectUnsafeNodes(Visitor):
    """Reject AST nodes that make a statement unsafe or unexecutable here.

    Write statements can hide inside CTEs (WITH x AS (DELETE ...) SELECT ...),
    SELECT INTO creates a table, side-effecting functions act outside the
    read-only transaction, and parameter placeholders ($1) have no way to
    receive values through the MCP tools.
    """

    def visit_InsertStmt(self, _ancestors: Ancestor, _node: ast.InsertStmt) -> None:
        raise ValueError("INSERT is not allowed, including inside CTEs")

    def visit_UpdateStmt(self, _ancestors: Ancestor, _node: ast.UpdateStmt) -> None:
        raise ValueError("UPDATE is not allowed, including inside CTEs")

    def visit_DeleteStmt(self, _ancestors: Ancestor, _node: ast.DeleteStmt) -> None:
        raise ValueError("DELETE is not allowed, including inside CTEs")

    def visit_MergeStmt(self, _ancestors: Ancestor, _node: ast.MergeStmt) -> None:
        raise ValueError("MERGE is not allowed, including inside CTEs")

    def visit_IntoClause(self, _ancestors: Ancestor, _node: ast.IntoClause) -> None:
        raise ValueError("SELECT INTO is not allowed; it creates a table")

    def visit_FuncCall(self, _ancestors: Ancestor, node: ast.FuncCall) -> None:
        # The last name part, so pg_catalog.set_config is caught as set_config.
        name = node.funcname[-1].sval if node.funcname else ""
        if name in SIDE_EFFECT_FUNCTIONS or name.startswith(SIDE_EFFECT_PREFIXES):
            raise ValueError(f"{name}() is not allowed; it acts outside the read-only query")

    def visit_ParamRef(self, _ancestors: Ancestor, _node: ast.ParamRef) -> None:
        raise ValueError(
            "Parameter placeholders ($1, $2, ...) are not supported; "
            "inline literal values in the SQL instead"
        )


def validate_select(sql: str, allow_cursor: bool = False) -> str:
    """Validate that sql is a single SELECT (or WITH ... SELECT) statement.

    With allow_cursor=True, also accepts DECLARE CURSOR ... FOR SELECT.

    Returns the statement deparsed back from the parse tree: a single clean
    statement with no trailing semicolons or comments, safe to embed in a
    subquery or EXPLAIN.

    Raises ValueError for multi-statement input, non-SELECT statements,
    write statements hidden in CTEs, SELECT INTO, side-effecting function
    calls, parameter placeholders, or syntactically invalid SQL (via
    pglast/PostgreSQL's own parser).
    """
    stmts = parse_sql(sql)
    if len(stmts) != 1:
        raise ValueError(f"Expected a single SQL statement, got {len(stmts)}")
    stmt = stmts[0].stmt
    inner = stmt
    if isinstance(stmt, ast.DeclareCursorStmt):
        if not allow_cursor:
            raise ValueError("DECLARE CURSOR is not supported here; pass the SELECT directly")
        inner = stmt.query
    if not isinstance(inner, ast.SelectStmt):
        raise ValueError(f"Only SELECT statements are allowed, got {type(stmt).__name__}")
    _RejectUnsafeNodes()(stmt)
    return RawStream()(stmt)

"""SQL validation using pglast (PostgreSQL's own parser)."""

from pglast import ast, parse_sql
from pglast.stream import RawStream
from pglast.visitors import Ancestor, Visitor


class _RejectUnsafeNodes(Visitor):
    """Reject AST nodes that make a statement unsafe or unexecutable here.

    Write statements can hide inside CTEs (WITH x AS (DELETE ...) SELECT ...),
    SELECT INTO creates a table, and parameter placeholders ($1) have no way
    to receive values through the MCP tools.
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
    write statements hidden in CTEs, SELECT INTO, parameter placeholders,
    or syntactically invalid SQL (via pglast/PostgreSQL's own parser).
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

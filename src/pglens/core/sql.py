"""SQL validation using pglast (PostgreSQL's own parser)."""

from pglast import ast, parse_sql


def validate_select(sql: str) -> None:
    """Validate that sql is a single SELECT (or WITH ... SELECT) statement.

    Also accepts DECLARE CURSOR ... FOR SELECT, extracting the inner SELECT.

    Raises ValueError for multi-statement input, non-SELECT statements,
    or syntactically invalid SQL (via pglast/PostgreSQL's own parser).
    """
    stmts = parse_sql(sql)
    if len(stmts) != 1:
        raise ValueError(f"Expected a single SQL statement, got {len(stmts)}")
    stmt = stmts[0].stmt
    if isinstance(stmt, ast.DeclareCursorStmt):
        stmt = stmt.query
    if not isinstance(stmt, ast.SelectStmt):
        raise ValueError(f"Only SELECT statements are allowed, got {type(stmts[0].stmt).__name__}")

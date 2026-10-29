"""
expr_eval.py

Evaluates one expression against one row. Shared by:
  * executor.py  - to evaluate WHERE / ON / HAVING / SELECT / ORDER BY
                   expressions for every row while running a plan, and
  * optimizer.py - for constant folding, which is the *same* evaluation done
                   at compile time on sub-expressions that contain no columns.
Sharing one evaluator guarantees that folding can never change a result.

A row is a dict from column key to value (see query_plan.py):
    {"b.id": 1, "b.title": "1984", ...}   or   {"b.genre": "Fantasy", "COUNT(*)": 2}

SQL semantics implemented here (matching SQLite):

  * NULL is Python None. In this grammar NULL only arises at run time, e.g.
    MAX() over an empty input or a division by zero.
  * Three-valued logic: comparisons involving NULL give NULL ("unknown");
        FALSE AND NULL = FALSE      TRUE OR NULL = TRUE
        TRUE AND NULL  = NULL       FALSE OR NULL = NULL     NOT NULL = NULL
    A WHERE/HAVING/ON condition keeps a row only if it is TRUE.
  * Integer / integer is integer division truncated toward zero (7 / 2 = 3,
    -7 / 2 = -3); x / 0 is NULL.
  * LIKE is case-insensitive for ASCII letters; % matches any run of
    characters (including none) and _ matches exactly one character.
"""

from ast_nodes import (
    AggregateCall, BetweenExpr, BinaryOp, ColumnRef, InExpr, LikeExpr, Literal,
    UnaryOp, to_sql,
)


def evaluate(expr, row):
    """Value of `expr` for `row` (an int, float, str, bool, or None for NULL)."""
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, ColumnRef):
        return row[expr.key()]
    if isinstance(expr, AggregateCall):
        # Aggregates were computed by the AGGREGATE step and stored in the row.
        return row[to_sql(expr, resolved=True)]

    if isinstance(expr, BinaryOp):
        if expr.op == "AND":
            return sql_and(evaluate(expr.left, row), lambda: evaluate(expr.right, row))
        if expr.op == "OR":
            return sql_or(evaluate(expr.left, row), lambda: evaluate(expr.right, row))
        left, right = evaluate(expr.left, row), evaluate(expr.right, row)
        if expr.op in COMPARE:
            return compare(expr.op, left, right)
        return arithmetic(expr.op, left, right)

    if isinstance(expr, UnaryOp):
        value = evaluate(expr.operand, row)
        if value is None:
            return None
        return (not value) if expr.op == "NOT" else -value

    if isinstance(expr, LikeExpr):
        text, pattern = evaluate(expr.expr, row), evaluate(expr.pattern, row)
        if text is None or pattern is None:
            return None
        return sql_not(like_match(pattern, text)) if expr.negated else like_match(pattern, text)

    if isinstance(expr, InExpr):
        result = in_list(evaluate(expr.expr, row), [evaluate(i, row) for i in expr.items])
        return sql_not(result) if expr.negated else result

    if isinstance(expr, BetweenExpr):
        value = evaluate(expr.expr, row)
        low, high = evaluate(expr.low, row), evaluate(expr.high, row)
        result = sql_and(compare(">=", value, low), lambda: compare("<=", value, high))
        return sql_not(result) if expr.negated else result

    raise TypeError(f"evaluate: unexpected node {expr!r}")


def is_true(value):
    """Does a condition keep the row? Only TRUE does - FALSE and NULL do not."""
    return value is True


# -- three-valued logic ------------------------------------------------------------
# The right operand is passed as a function so it is evaluated only when needed.

def sql_and(left, right_thunk):
    if left is False:
        return False
    right = right_thunk()
    if right is False:
        return False
    if left is None or right is None:
        return None
    return True


def sql_or(left, right_thunk):
    if left is True:
        return True
    right = right_thunk()
    if right is True:
        return True
    if left is None or right is None:
        return None
    return False


def sql_not(value):
    return None if value is None else not value


# -- comparison and arithmetic ----------------------------------------------------------

COMPARE = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
}


def compare(op, left, right):
    if left is None or right is None:
        return None
    return COMPARE[op](left, right)


def arithmetic(op, left, right):
    if left is None or right is None:
        return None
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    # division
    if right == 0:
        return None
    if isinstance(left, int) and isinstance(right, int):
        quotient = abs(left) // abs(right)          # truncate toward zero, like SQLite
        return quotient if (left >= 0) == (right >= 0) else -quotient
    return left / right


def in_list(value, items):
    """x IN (a, b, ...): TRUE if x equals some item; otherwise NULL if x or
    any item is NULL (it *might* have matched); otherwise FALSE."""
    if value is None:
        return None
    if any(item == value for item in items if item is not None):
        return True
    return None if any(item is None for item in items) else False


# -- LIKE -----------------------------------------------------------------------------

def ascii_lower(text):
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def like_match(pattern, text):
    """Does `text` match the LIKE `pattern`? Dynamic programming:
    match[i][j] is True when pattern[:i] matches text[:j].

        '%'  : match[i][j] = match[i-1][j]      (% matches nothing)
                            or match[i][j-1]     (% absorbs one more char)
        '_'  : match[i][j] = match[i-1][j-1]    (any single char)
        other: match[i][j] = match[i-1][j-1] and the characters are equal
    """
    p, t = ascii_lower(pattern), ascii_lower(text)
    match = [[False] * (len(t) + 1) for _ in range(len(p) + 1)]
    match[0][0] = True
    for i in range(1, len(p) + 1):
        if p[i - 1] == "%":
            match[i][0] = match[i - 1][0]   # leading %s can match the empty string
    for i in range(1, len(p) + 1):
        for j in range(1, len(t) + 1):
            if p[i - 1] == "%":
                match[i][j] = match[i - 1][j] or match[i][j - 1]
            elif p[i - 1] == "_" or p[i - 1] == t[j - 1]:
                match[i][j] = match[i - 1][j - 1]
    return match[len(p)][len(t)]

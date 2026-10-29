"""
ast_nodes.py

AST node definitions for the SELECT-only grammar in docs/GRAMMAR.md.

Two kinds of node:

  * Expression nodes - ColumnRef, Literal, Star, AggregateCall, BinaryOp,
    UnaryOp, LikeExpr, InExpr, BetweenExpr. These appear in the SELECT list,
    WHERE, JOIN ... ON, HAVING and ORDER BY.
  * Clause / statement nodes - SelectItem, TableRef, JoinClause,
    OrderByItem and the root SelectStatement.

Every node records the 1-based `line` / `col` where it starts in the SQL
source, so the semantic analyzer can report *where* an error is. Those
fields use compare=False, so two ASTs parsed from differently formatted SQL
still compare equal.

The semantic analyzer "decorates" the tree after parsing (a classic
compiler technique - an *annotated* AST):
  ColumnRef.qualifier   the table alias the column was resolved to
  ColumnRef.sql_type    INTEGER / REAL / TEXT
  ColumnRef.alias_index set when an ORDER BY name refers to a SELECT alias
  AggregateCall.sql_type
The planner and interpreter rely on these annotations.

This module also contains `to_sql()`, a pretty-printer that turns any
expression (or a whole statement) back into SQL text. It inserts
parentheses only where operator precedence requires them. It is used to
print query plans, optimizer output, and in the parser round-trip tests.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Union


def _pos():
    """A source-position field that is ignored by == and repr()."""
    return field(default=0, compare=False, repr=False)


def _annotation(default=None):
    """A field filled in by the semantic analyzer, ignored by == and repr()."""
    return field(default=default, compare=False, repr=False)


# =============================================================================
# Expression nodes
# =============================================================================

@dataclass
class ColumnRef:
    """A column reference, optionally qualified: `title` or `b.title`."""
    table: Optional[str]          # qualifier as written (table name or alias), or None
    name: str
    line: int = _pos()
    col: int = _pos()
    qualifier: Optional[str] = _annotation()    # resolved alias (set by analyzer)
    sql_type: Optional[str] = _annotation()     # INTEGER / REAL / TEXT
    alias_index: Optional[int] = _annotation()  # ORDER BY reference to a SELECT alias

    def key(self):
        """The name this column has inside a row of the interpreter,
        e.g. 'b.title'. Only valid after semantic analysis."""
        return f"{self.qualifier}.{self.name}"

    def __repr__(self):
        return to_sql(self)


@dataclass
class Literal:
    """A constant: a string, an integer, a decimal number, or TRUE/FALSE."""
    value: Union[str, int, float, bool]
    line: int = _pos()
    col: int = _pos()

    def __repr__(self):
        return to_sql(self)


@dataclass
class Star:
    """`*` in `SELECT *`, `SELECT b.*` or `COUNT(*)`."""
    table: Optional[str] = None   # qualifier for `b.*`
    line: int = _pos()
    col: int = _pos()

    def __repr__(self):
        return to_sql(self)


@dataclass
class AggregateCall:
    """COUNT / SUM / AVG / MIN / MAX, e.g. COUNT(*) or COUNT(DISTINCT author)."""
    func: str                        # upper-case function name
    arg: "Expr"                      # an expression, or Star for COUNT(*)
    distinct: bool = False
    line: int = _pos()
    col: int = _pos()
    sql_type: Optional[str] = _annotation()

    def __repr__(self):
        return to_sql(self)


@dataclass
class BinaryOp:
    """Arithmetic (+ - * /), comparison (= != < > <= >=) or logical (AND OR)."""
    op: str
    left: "Expr"
    right: "Expr"
    line: int = _pos()   # position of the operator token
    col: int = _pos()

    def __repr__(self):
        return to_sql(self)


@dataclass
class UnaryOp:
    """NOT <expr> or -<expr>."""
    op: str              # "NOT" or "-"
    operand: "Expr"
    line: int = _pos()
    col: int = _pos()

    def __repr__(self):
        return to_sql(self)


@dataclass
class LikeExpr:
    """<expr> [NOT] LIKE <pattern>   (% = any run of characters, _ = one character)"""
    expr: "Expr"
    pattern: "Expr"
    negated: bool = False
    line: int = _pos()
    col: int = _pos()

    def __repr__(self):
        return to_sql(self)


@dataclass
class InExpr:
    """<expr> [NOT] IN (<item>, <item>, ...)"""
    expr: "Expr"
    items: List["Expr"]
    negated: bool = False
    line: int = _pos()
    col: int = _pos()

    def __repr__(self):
        return to_sql(self)


@dataclass
class BetweenExpr:
    """<expr> [NOT] BETWEEN <low> AND <high>   (inclusive on both ends)"""
    expr: "Expr"
    low: "Expr"
    high: "Expr"
    negated: bool = False
    line: int = _pos()
    col: int = _pos()

    def __repr__(self):
        return to_sql(self)


Expr = Union[ColumnRef, Literal, Star, AggregateCall, BinaryOp, UnaryOp,
             LikeExpr, InExpr, BetweenExpr]

ARITHMETIC_OPS = {"+", "-", "*", "/"}
COMPARISON_OPS = {"=", "!=", "<", ">", "<=", ">="}
LOGICAL_OPS = {"AND", "OR"}


# =============================================================================
# Clause and statement nodes
# =============================================================================

@dataclass
class SelectItem:
    """One entry in the SELECT list, with an optional alias (`AS name`)."""
    expr: Expr
    alias: Optional[str] = None

    def __repr__(self):
        text = to_sql(self.expr)
        return f"{text} AS {self.alias}" if self.alias else text


@dataclass
class TableRef:
    """A table in FROM / JOIN with an optional alias: `Books AS b`."""
    name: str
    alias: Optional[str] = None
    line: int = _pos()
    col: int = _pos()

    @property
    def binding(self):
        """The name other clauses must use to refer to this table."""
        return self.alias or self.name

    def __repr__(self):
        return f"{self.name} AS {self.alias}" if self.alias else self.name


@dataclass
class JoinClause:
    """[INNER] JOIN <table> ON <condition>"""
    table: TableRef
    on: Expr

    def __repr__(self):
        return f"JOIN {self.table!r} ON {to_sql(self.on)}"


@dataclass
class OrderByItem:
    expr: Expr
    direction: str = "ASC"   # ASC or DESC

    def __repr__(self):
        return f"{to_sql(self.expr)} {self.direction}"


@dataclass
class SelectStatement:
    """Root of the AST for one SELECT query."""
    select_items: List[SelectItem]
    from_table: TableRef
    joins: List[JoinClause] = field(default_factory=list)
    where: Optional[Expr] = None
    group_by: List[ColumnRef] = field(default_factory=list)
    having: Optional[Expr] = None
    order_by: List[OrderByItem] = field(default_factory=list)
    distinct: bool = False
    limit: Optional[int] = None

    def tables(self):
        """FROM table followed by each JOIN table, in source order."""
        return [self.from_table] + [j.table for j in self.joins]


# =============================================================================
# Tree helpers
# =============================================================================

def children(node):
    """Direct sub-expressions of an expression node."""
    if isinstance(node, AggregateCall):
        return [node.arg]
    if isinstance(node, BinaryOp):
        return [node.left, node.right]
    if isinstance(node, UnaryOp):
        return [node.operand]
    if isinstance(node, LikeExpr):
        return [node.expr, node.pattern]
    if isinstance(node, InExpr):
        return [node.expr] + list(node.items)
    if isinstance(node, BetweenExpr):
        return [node.expr, node.low, node.high]
    return []   # ColumnRef, Literal, Star are leaves


def walk(node):
    """Yield `node` and every expression below it (pre-order)."""
    yield node
    for child in children(node):
        yield from walk(child)


def contains_aggregate(node):
    return any(isinstance(n, AggregateCall) for n in walk(node))


def column_refs(node, inside_aggregates=True):
    """Every ColumnRef under `node`. With inside_aggregates=False, columns
    that only appear as an aggregate's argument are skipped - that is the
    set the GROUP BY rule cares about."""
    if isinstance(node, ColumnRef):
        return [node]
    if isinstance(node, AggregateCall) and not inside_aggregates:
        return []
    refs = []
    for child in children(node):
        refs.extend(column_refs(child, inside_aggregates))
    return refs


# =============================================================================
# Pretty-printer: AST -> SQL text
# =============================================================================
#
# Precedence levels, lowest to highest. A child expression is wrapped in
# parentheses only if its level is lower than what its parent position needs,
# so `(a OR b) AND c` keeps its parentheses but `a AND b AND c` gets none.

PREC_OR, PREC_AND, PREC_NOT, PREC_PREDICATE, PREC_ADD, PREC_MUL, PREC_UNARY, PREC_ATOM = range(1, 9)

_BINARY_PREC = {
    "OR": PREC_OR, "AND": PREC_AND,
    "=": PREC_PREDICATE, "!=": PREC_PREDICATE, "<": PREC_PREDICATE,
    ">": PREC_PREDICATE, "<=": PREC_PREDICATE, ">=": PREC_PREDICATE,
    "+": PREC_ADD, "-": PREC_ADD,
    "*": PREC_MUL, "/": PREC_MUL,
}


def precedence(node):
    if isinstance(node, BinaryOp):
        return _BINARY_PREC[node.op]
    if isinstance(node, UnaryOp):
        return PREC_NOT if node.op == "NOT" else PREC_UNARY
    if isinstance(node, (LikeExpr, InExpr, BetweenExpr)):
        return PREC_PREDICATE
    return PREC_ATOM


def format_literal(value):
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(value)


def to_sql(node, resolved=False):
    """Render an expression as SQL. With resolved=True, column references
    are printed with the alias the analyzer resolved them to (b.title
    instead of title) - clearer when reading a query plan."""

    def wrap(child, min_prec):
        text = to_sql(child, resolved)
        return f"({text})" if precedence(child) < min_prec else text

    if isinstance(node, ColumnRef):
        table = node.qualifier if (resolved and node.qualifier) else node.table
        return f"{table}.{node.name}" if table else node.name
    if isinstance(node, Literal):
        return format_literal(node.value)
    if isinstance(node, Star):
        return f"{node.table}.*" if node.table else "*"
    if isinstance(node, AggregateCall):
        inner = to_sql(node.arg, resolved)
        return f"{node.func}({'DISTINCT ' if node.distinct else ''}{inner})"
    if isinstance(node, BinaryOp):
        prec = _BINARY_PREC[node.op]
        # Comparisons are non-associative, so a comparison on the left of a
        # comparison needs parentheses too.
        left_min = prec + 1 if prec == PREC_PREDICATE else prec
        return f"{wrap(node.left, left_min)} {node.op} {wrap(node.right, prec + 1)}"
    if isinstance(node, UnaryOp):
        if node.op == "NOT":
            return f"NOT {wrap(node.operand, PREC_NOT)}"
        return f"-{wrap(node.operand, PREC_UNARY)}"
    if isinstance(node, LikeExpr):
        op = "NOT LIKE" if node.negated else "LIKE"
        return f"{wrap(node.expr, PREC_ADD)} {op} {wrap(node.pattern, PREC_ADD)}"
    if isinstance(node, InExpr):
        op = "NOT IN" if node.negated else "IN"
        items = ", ".join(to_sql(i, resolved) for i in node.items)
        return f"{wrap(node.expr, PREC_ADD)} {op} ({items})"
    if isinstance(node, BetweenExpr):
        op = "NOT BETWEEN" if node.negated else "BETWEEN"
        return (f"{wrap(node.expr, PREC_ADD)} {op} "
                f"{wrap(node.low, PREC_ADD)} AND {wrap(node.high, PREC_ADD)}")
    raise TypeError(f"to_sql: not an expression node: {node!r}")


def statement_to_sql(stmt: SelectStatement):
    """Render a whole SelectStatement back to (normalized) SQL text."""
    parts = ["SELECT"]
    if stmt.distinct:
        parts.append("DISTINCT")
    parts.append(", ".join(repr(item) for item in stmt.select_items))
    parts.append(f"FROM {stmt.from_table!r}")
    for join in stmt.joins:
        parts.append(repr(join))
    if stmt.where is not None:
        parts.append(f"WHERE {to_sql(stmt.where)}")
    if stmt.group_by:
        parts.append("GROUP BY " + ", ".join(to_sql(c) for c in stmt.group_by))
    if stmt.having is not None:
        parts.append(f"HAVING {to_sql(stmt.having)}")
    if stmt.order_by:
        parts.append("ORDER BY " + ", ".join(repr(o) for o in stmt.order_by))
    if stmt.limit is not None:
        parts.append(f"LIMIT {stmt.limit}")
    return " ".join(parts)

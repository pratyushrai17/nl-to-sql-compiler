"""
query_plan.py

Intermediate code generation: annotated AST -> logical query plan.

The plan is a small tree of relational operators. Each operator consumes
one or two input relations (tables of rows) and produces a new relation:

    SCAN       read every row of a base table
    JOIN       inner join of two inputs on a condition (nested loops)
    FILTER     keep rows where the WHERE condition is true
    AGGREGATE  group rows by the GROUP BY columns and compute aggregates
    HAVING     keep groups where the HAVING condition is true
    SORT       order rows by the ORDER BY keys
    PROJECT    compute the SELECT list (the output columns)
    DISTINCT   remove duplicate output rows
    LIMIT      keep the first N rows

For display and execution the tree is *linearized* into numbered
three-address-style steps, children first (a post-order walk), so each step
only refers to temporaries that were computed before it:

    t1 = SCAN Books
    t2 = FILTER t1 WHERE Books.genre = 'Dystopian'
    t3 = PROJECT t2 [Books.title]

Why a tree internally and numbered steps externally: optimizer passes are
naturally written as recursive tree rewrites, while numbered steps are easy
to read, print, and execute one at a time - the same split a compiler makes
between an IR tree and linear three-address code.

Order of operators. SQL's logical order is FROM -> WHERE -> GROUP BY ->
HAVING -> SELECT -> DISTINCT -> ORDER BY -> LIMIT. This planner puts SORT
*before* PROJECT so that ORDER BY can use columns that are not in the SELECT
list (SELECT title FROM Books ORDER BY id). This is safe with DISTINCT
because the analyzer requires DISTINCT's ORDER BY terms to be in the SELECT
list, and removing duplicates from a sorted list keeps it sorted.

Column keys. Inside the interpreter every value in a row is stored under a
key: base columns as "<alias>.<column>" (e.g. "b.title"), aggregate results
as their SQL text (e.g. "COUNT(*)"), and projected outputs as "$1", "$2",
... Each plan node knows its output keys (`columns`) and the user-facing
names (`headers`) statically, before anything runs.
"""

import copy
from dataclasses import dataclass, field
from typing import List, Optional

from ast_nodes import (
    AggregateCall, ColumnRef, SelectStatement, Star, map_children, to_sql, walk,
)
from symbol_table import get_columns

OPERATORS = ("SCAN", "JOIN", "FILTER", "AGGREGATE", "HAVING", "SORT", "PROJECT",
             "DISTINCT", "LIMIT")


def aggregate_key(agg: AggregateCall):
    """The key an aggregate's result is stored under, e.g. 'COUNT(*)'."""
    return to_sql(agg, resolved=True)


@dataclass
class PlanNode:
    """One relational operator. Only the fields relevant to `op` are used."""
    op: str
    children: List["PlanNode"] = field(default_factory=list)
    table: Optional[str] = None         # SCAN: base table name
    alias: Optional[str] = None         # SCAN: binding used for its columns
    predicate: object = None            # JOIN / FILTER / HAVING condition
    group_by: list = field(default_factory=list)     # AGGREGATE: ColumnRefs
    aggregates: list = field(default_factory=list)   # AGGREGATE: AggregateCalls
    items: list = field(default_factory=list)        # PROJECT: (expr, header) pairs
    sort_keys: list = field(default_factory=list)    # SORT: (expr, "ASC"/"DESC") pairs
    limit: Optional[int] = None         # LIMIT
    columns: List[str] = field(default_factory=list)  # output keys
    headers: List[str] = field(default_factory=list)  # output names shown to the user

    def describe(self, input_names):
        """The right-hand side of this node's three-address step."""
        args = ", ".join(input_names)
        if self.op == "SCAN":
            alias = f" AS {self.alias}" if self.alias != self.table else ""
            return f"SCAN {self.table}{alias}"
        if self.op == "JOIN":
            return f"JOIN {args} ON {to_sql(self.predicate, resolved=True)}"
        if self.op == "FILTER":
            return f"FILTER {args} WHERE {to_sql(self.predicate, resolved=True)}"
        if self.op == "AGGREGATE":
            groups = ", ".join(to_sql(c, resolved=True) for c in self.group_by)
            aggs = ", ".join(aggregate_key(a) for a in self.aggregates)
            by = f"GROUP BY [{groups}]" if self.group_by else "(all rows as one group)"
            return f"AGGREGATE {args} {by} COMPUTE [{aggs}]"
        if self.op == "HAVING":
            return f"HAVING {args} {to_sql(self.predicate, resolved=True)}"
        if self.op == "SORT":
            keys = ", ".join(f"{to_sql(e, resolved=True)} {d}" for e, d in self.sort_keys)
            return f"SORT {args} BY [{keys}]"
        if self.op == "PROJECT":
            cols = []
            for expr, header in self.items:
                text = to_sql(expr, resolved=True)
                plain = expr.name if isinstance(expr, ColumnRef) else to_sql(expr)
                cols.append(text if header == plain else f"{text} AS {header}")
            return f"PROJECT {args} [{', '.join(cols)}]"
        if self.op == "DISTINCT":
            return f"DISTINCT {args}"
        if self.op == "LIMIT":
            return f"LIMIT {args} {self.limit}"
        raise ValueError(f"unknown plan operator {self.op}")


@dataclass
class Step:
    """One numbered three-address step: t<id> = <node> applied to <inputs>."""
    id: int
    node: PlanNode
    inputs: List[int]

    def __str__(self):
        return f"t{self.id} = {self.node.describe([f't{i}' for i in self.inputs])}"


class QueryPlan:
    def __init__(self, root: PlanNode):
        self.root = root

    def steps(self) -> List[Step]:
        """Linearize the tree: post-order, so inputs are numbered first."""
        steps = []

        def visit(node):
            input_ids = [visit(child) for child in node.children]
            steps.append(Step(len(steps) + 1, node, input_ids))
            return len(steps)

        visit(self.root)
        return steps

    def ops(self):
        return [s.node.op for s in self.steps()]

    def copy(self):
        return QueryPlan(copy.deepcopy(self.root))

    def __str__(self):
        return "\n".join(str(s) for s in self.steps())


# =============================================================================
# Planner: SelectStatement -> QueryPlan
# =============================================================================

def node(op, children, **fields):
    """Create a PlanNode and fill in its static output columns/headers."""
    n = PlanNode(op, children, **fields)
    set_columns(n)
    return n


def refresh_columns(n):
    """Recompute columns/headers for a whole tree, bottom-up. The optimizer
    calls this after it removes or inserts nodes."""
    for child in n.children:
        refresh_columns(child)
    set_columns(n)


def set_columns(n):
    """Fill in n.columns / n.headers from its operator and its inputs."""
    op, children = n.op, n.children
    if op == "SCAN":
        names = get_columns(n.table)
        n.columns = [f"{n.alias}.{c}" for c in names]
        n.headers = names
    elif op == "JOIN":
        left, right = children
        n.columns = left.columns + right.columns
        n.headers = left.headers + right.headers
    elif op == "AGGREGATE":
        n.columns = [c.key() for c in n.group_by] + [aggregate_key(a) for a in n.aggregates]
        n.headers = list(n.columns)
    elif op == "PROJECT":
        n.columns = [f"${i}" for i in range(1, len(n.items) + 1)]
        n.headers = [header for _, header in n.items]
    else:   # FILTER, HAVING, SORT, DISTINCT, LIMIT pass their input through
        n.columns = list(children[0].columns)
        n.headers = list(children[0].headers)


def build_plan(stmt: SelectStatement) -> QueryPlan:
    """Translate a semantically valid, annotated AST into a QueryPlan."""

    # FROM / JOIN: a left-deep tree of joins over table scans.
    tables = stmt.tables()
    current = scan(tables[0])
    for join in stmt.joins:
        current = node("JOIN", [current, scan(join.table)], predicate=join.on)

    if stmt.where is not None:
        current = node("FILTER", [current], predicate=stmt.where)

    order_exprs = [substitute_aliases(o.expr, stmt) for o in stmt.order_by]

    # GROUP BY / aggregates. Every distinct aggregate used anywhere later
    # (SELECT, HAVING, ORDER BY) is computed once, here.
    later_exprs = [i.expr for i in stmt.select_items if not isinstance(i.expr, Star)]
    later_exprs += order_exprs + ([stmt.having] if stmt.having is not None else [])
    aggregates = unique_aggregates(later_exprs)
    if stmt.group_by or aggregates:
        current = node("AGGREGATE", [current], group_by=list(stmt.group_by),
                       aggregates=aggregates)
    if stmt.having is not None:
        current = node("HAVING", [current], predicate=stmt.having)

    if stmt.order_by:
        keys = [(expr, o.direction) for expr, o in zip(order_exprs, stmt.order_by)]
        current = node("SORT", [current], sort_keys=keys)

    current = node("PROJECT", [current], items=projection_items(stmt))

    if stmt.distinct:
        current = node("DISTINCT", [current])
    if stmt.limit is not None:
        current = node("LIMIT", [current], limit=stmt.limit)

    return QueryPlan(current)


def scan(table_ref):
    return node("SCAN", [], table=table_ref.name, alias=table_ref.binding)


def unique_aggregates(exprs):
    seen, result = set(), []
    for expr in exprs:
        for n in walk(expr):
            if isinstance(n, AggregateCall) and aggregate_key(n) not in seen:
                seen.add(aggregate_key(n))
                result.append(n)
    return result


def substitute_aliases(expr, stmt):
    """Replace ORDER BY references to a SELECT alias with the aliased
    expression itself: ORDER BY n  (n = COUNT(*))  ->  ORDER BY COUNT(*)."""
    if isinstance(expr, ColumnRef) and expr.alias_index is not None:
        return copy.deepcopy(stmt.select_items[expr.alias_index].expr)
    return map_children(expr, lambda child: substitute_aliases(child, stmt))


def projection_items(stmt):
    """(expression, header) pairs for the SELECT list, with * expanded into
    the columns of every table in scope (or of one table for b.*)."""
    items = []
    for item in stmt.select_items:
        if isinstance(item.expr, Star):
            for ref in stmt.tables():
                if item.expr.table in (None, ref.binding):
                    for column in get_columns(ref.name):
                        col = ColumnRef(ref.binding, column, qualifier=ref.binding)
                        items.append((col, column))
            continue
        if item.alias:
            header = item.alias
        elif isinstance(item.expr, ColumnRef):
            header = item.expr.name
        else:
            header = to_sql(item.expr)
        items.append((item.expr, header))
    return items

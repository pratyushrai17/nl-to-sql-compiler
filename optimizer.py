"""
optimizer.py

Rewrites a QueryPlan into an equivalent, cheaper one. Three small passes,
run in this order:

  1. constant_folding
       Evaluate at compile time every sub-expression that contains no column
       references, and simplify boolean logic:
           id > 2 * 3          ->  id > 6
           1 = 1 AND x         ->  x          (TRUE AND x  = x)
           x OR 2 > 3          ->  x          (x OR FALSE = x)
           NOT NOT x           ->  x
       A FILTER / HAVING whose condition folds to TRUE is removed entirely.

  2. predicate_pushdown
       A WHERE condition sitting above a JOIN is split into its AND-ed parts
       (conjuncts). Each conjunct is moved down the tree as far as it can go:
         - if it only uses columns of one side of a join, it moves into that
           side - ultimately to just above that table's SCAN;
         - if it uses both sides, it becomes part of that JOIN's ON condition.
       Rows are then discarded *before* the join instead of after, so the
       nested-loop join compares fewer pairs. Valid because every join here
       is an INNER join (for a LEFT JOIN this would change the result).

  3. remove_redundant_projection
       A PROJECT that outputs exactly its input's columns, in order and with
       the same names (typically SELECT * from one table), does no work and
       is removed.

Each pass returns (new_root, notes) where notes are human-readable
descriptions of what it changed; a pass "fired" if it produced any notes.
The input plan is never modified - optimize() works on a deep copy.
"""

from dataclasses import dataclass, field
from typing import List

from ast_nodes import (
    AggregateCall, BinaryOp, ColumnRef, Literal, Star, UnaryOp, map_children,
    to_sql, walk,
)
from expr_eval import evaluate
from query_plan import QueryPlan, node, refresh_columns


@dataclass
class PassReport:
    name: str
    notes: List[str] = field(default_factory=list)

    @property
    def fired(self):
        return bool(self.notes)


@dataclass
class OptimizationResult:
    before: QueryPlan
    after: QueryPlan
    passes: List[PassReport]

    @property
    def fired(self):
        return [p.name for p in self.passes if p.fired]


def optimize(plan: QueryPlan) -> OptimizationResult:
    working = plan.copy()
    root = working.root
    reports = []
    for name, pass_fn in PASSES:
        root, notes = pass_fn(root)
        refresh_columns(root)
        reports.append(PassReport(name, notes))
    return OptimizationResult(before=plan, after=QueryPlan(root), passes=reports)


# =============================================================================
# Pass 1: constant folding
# =============================================================================

def is_constant(expr):
    """True if the expression contains no column, aggregate or '*'."""
    return not any(isinstance(n, (ColumnRef, AggregateCall, Star)) for n in walk(expr))


def fold(expr):
    """Bottom-up: fold the children first, then this node."""
    expr = map_children(expr, fold)

    if isinstance(expr, Literal):
        return expr

    if is_constant(expr):
        value = evaluate(expr, {})
        if value is None:
            return expr     # e.g. 1 / 0 is NULL; there is no NULL literal, so keep it
        return Literal(value, line=expr.line, col=expr.col)

    # Boolean simplification when one side of AND / OR is a known constant.
    if isinstance(expr, BinaryOp) and expr.op in ("AND", "OR"):
        identity = expr.op == "AND"           # TRUE is the identity for AND, FALSE for OR
        for const, other in ((expr.left, expr.right), (expr.right, expr.left)):
            if isinstance(const, Literal) and isinstance(const.value, bool):
                if const.value == identity:
                    return other              # TRUE AND x -> x,   FALSE OR x -> x
                return const                  # FALSE AND x -> FALSE,  TRUE OR x -> TRUE

    if isinstance(expr, UnaryOp) and expr.op == "NOT" \
            and isinstance(expr.operand, UnaryOp) and expr.operand.op == "NOT":
        return expr.operand.operand           # NOT NOT x -> x

    return expr


def constant_folding(root):
    notes = []

    def fold_and_note(expr, where):
        folded = fold(expr)
        before, after = to_sql(expr, resolved=True), to_sql(folded, resolved=True)
        if before != after:
            notes.append(f"{where}: {before}  ->  {after}")
        return folded

    def rewrite(n):
        n.children = [rewrite(c) for c in n.children]
        if n.predicate is not None:
            n.predicate = fold_and_note(n.predicate, n.op)
            if n.op in ("FILTER", "HAVING") and is_literal(n.predicate, True):
                notes.append(f"{n.op} condition is always TRUE, so the {n.op} step was removed")
                return n.children[0]
        if n.op == "PROJECT":
            n.items = [(fold_and_note(e, "PROJECT"), h) for e, h in n.items]
        if n.op == "SORT":
            n.sort_keys = [(fold_and_note(e, "SORT"), d) for e, d in n.sort_keys]
        return n

    return rewrite(root), notes


def is_literal(expr, value):
    return isinstance(expr, Literal) and expr.value is value


# =============================================================================
# Pass 2: predicate pushdown
# =============================================================================

def conjuncts(expr):
    """Split a condition on its top-level ANDs: a AND (b AND c) -> [a, b, c]."""
    if isinstance(expr, BinaryOp) and expr.op == "AND":
        return conjuncts(expr.left) + conjuncts(expr.right)
    return [expr]


def conjoin(exprs):
    """Inverse of conjuncts(): [a, b, c] -> a AND b AND c."""
    result = exprs[0]
    for e in exprs[1:]:
        result = BinaryOp("AND", result, e)
    return result


def tables_used(expr):
    """The table aliases an expression's columns come from."""
    return {n.qualifier for n in walk(expr) if isinstance(n, ColumnRef)}


def tables_produced(n):
    """The table aliases whose columns appear in a node's output rows."""
    return {key.split(".", 1)[0] for key in n.columns}


def describe_node(n):
    if n.op == "SCAN":
        return f"SCAN {n.table}" + (f" AS {n.alias}" if n.alias != n.table else "")
    return n.op


def predicate_pushdown(root):
    notes = []

    def push(n, conjunct):
        """Return `n` rewritten so that `conjunct` is applied as low as possible."""
        if n.op == "JOIN":
            left, right = n.children
            needed = tables_used(conjunct)
            if needed <= tables_produced(left):
                n.children[0] = push(left, conjunct)
            elif needed <= tables_produced(right):
                n.children[1] = push(right, conjunct)
            else:
                n.predicate = conjoin([n.predicate, conjunct])
                notes.append(f"{to_sql(conjunct, resolved=True)}  ->  merged into "
                             f"JOIN ON (uses both sides)")
            return n
        if n.op == "FILTER":
            n.predicate = conjoin([n.predicate, conjunct])
            return n
        notes.append(f"{to_sql(conjunct, resolved=True)}  ->  pushed below the JOIN, "
                     f"directly above {describe_node(n)}")
        return node("FILTER", [n], predicate=conjunct)

    def rewrite(n):
        n.children = [rewrite(c) for c in n.children]
        if n.op == "FILTER" and n.children[0].op == "JOIN":
            child = n.children[0]
            stay = []
            for conjunct in conjuncts(n.predicate):
                if tables_used(conjunct):
                    child = push(child, conjunct)
                else:
                    stay.append(conjunct)      # constant condition: leave it where it is
            if not stay:
                return child                   # every conjunct moved: drop this FILTER
            n.children[0] = child
            n.predicate = conjoin(stay)
        return n

    return rewrite(root), notes


# =============================================================================
# Pass 3: remove redundant projection
# =============================================================================

def remove_redundant_projection(root):
    notes = []

    def rewrite(n):
        n.children = [rewrite(c) for c in n.children]
        if n.op == "PROJECT":
            child = n.children[0]
            keys = [e.key() if isinstance(e, ColumnRef) else None for e, _ in n.items]
            headers = [h for _, h in n.items]
            if keys == child.columns and headers == child.headers:
                notes.append(f"PROJECT outputs exactly the columns of its input "
                             f"({describe_node(child)}), so it was removed")
                return child
        return n

    return rewrite(root), notes


PASSES = [
    ("constant_folding", constant_folding),
    ("predicate_pushdown", predicate_pushdown),
    ("remove_redundant_projection", remove_redundant_projection),
]

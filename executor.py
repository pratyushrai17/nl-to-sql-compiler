"""
executor.py

Plan interpreter: runs a (usually optimized) QueryPlan step by step in
Python, then cross-checks the answer against SQLite.

Execution model
---------------
The plan's numbered steps (QueryPlan.steps()) are executed in order. Each
step reads the relations produced by earlier steps (its inputs, t1, t2, ...)
and stores its own output relation under its number - exactly like
executing three-address code with temporaries.

A relation is a list of rows; a row is a dict from column key to value
(keys are described in query_plan.py). Expressions are evaluated by
expr_eval.evaluate(), the same evaluator the optimizer uses for constant
folding.

Algorithms (chosen for clarity, the tables are tiny):
    SCAN       read rows from SQLite (db.load_table)
    JOIN       nested-loop join: test every (left, right) pair
    FILTER /   keep rows whose condition is TRUE (not FALSE, not NULL)
    HAVING
    AGGREGATE  hash grouping: dict from group-key tuple -> list of rows,
               then compute each aggregate over each group
    SORT       stable multi-key sort (sort by the last key first)
    PROJECT    evaluate the SELECT expressions for each row
    DISTINCT   keep the first row with each distinct set of output values
    LIMIT      keep the first N rows

Cross-check
-----------
cross_check() runs the *original* SQL text in SQLite and compares results.
Without ORDER BY, row order is unspecified in SQL, so rows are compared as
multisets. With ORDER BY, rows must appear in the same order, except that
rows which tie on every ORDER BY key may appear in any order relative to
each other (SQL does not define their order either). A mismatch means a
bug in our compiler/interpreter and is reported loudly.
"""

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from ast_nodes import Star
from db import load_table, run_query
from expr_eval import evaluate, is_true
from query_plan import QueryPlan, aggregate_key
from symbol_table import get_columns

SORT_KEY = "__sort__"   # reserved row key: the row's ORDER BY values, used by cross_check


@dataclass
class StepTrace:
    step: str          # e.g. "t2 = FILTER t1 WHERE ..."
    rows_out: int
    ms: float


@dataclass
class ExecutionResult:
    headers: List[str]
    rows: List[tuple]
    trace: List[StepTrace] = field(default_factory=list)
    ordered: bool = False                      # did the plan contain a SORT?
    sort_keys: List[tuple] = field(default_factory=list)   # per output row
    ties_at_limit: bool = False                # LIMIT cut through a group of tied rows


@dataclass
class CrossCheck:
    match: bool
    sqlite_headers: List[str]
    sqlite_rows: List[tuple]
    detail: str


def output_value(value):
    """SQLite has no boolean type: TRUE/FALSE come back as 1/0."""
    return int(value) if isinstance(value, bool) else value


# =============================================================================
# Operators
# =============================================================================

def run_scan(node, conn):
    names = get_columns(node.table)
    return [{f"{node.alias}.{c}": v for c, v in zip(names, values)}
            for values in load_table(conn, node.table)]


def run_join(node, left, right):
    out = []
    for l_row in left:
        for r_row in right:
            row = {**l_row, **r_row}
            if is_true(evaluate(node.predicate, row)):
                out.append(row)
    return out


def run_filter(node, rows):
    return [row for row in rows if is_true(evaluate(node.predicate, row))]


def run_aggregate(node, rows):
    groups = {}   # group-key tuple -> rows; dicts keep first-seen order
    for row in rows:
        key = tuple(row[c.key()] for c in node.group_by)
        groups.setdefault(key, []).append(row)
    if not node.group_by and not groups:
        groups[()] = []   # no GROUP BY: always exactly one group, even for no rows

    out = []
    for key, members in groups.items():
        result = {c.key(): value for c, value in zip(node.group_by, key)}
        for agg in node.aggregates:
            result[aggregate_key(agg)] = compute_aggregate(agg, members)
        out.append(result)
    return out


def compute_aggregate(agg, rows):
    if isinstance(agg.arg, Star):           # COUNT(*) counts rows, NULLs included
        return len(rows)
    values = [evaluate(agg.arg, row) for row in rows]
    values = [v for v in values if v is not None]     # aggregates ignore NULLs
    if agg.distinct:
        values = list(dict.fromkeys(values))          # dedupe, keep order
    if agg.func == "COUNT":
        return len(values)
    if not values:
        return None                                   # SUM/AVG/MIN/MAX of nothing is NULL
    if agg.func == "SUM":
        return sum(values)
    if agg.func == "AVG":
        return sum(values) / len(values)
    if agg.func == "MIN":
        return min(values)
    return max(values)


def null_first(value):
    """Sort key placing NULL before every value, as SQLite does for ASC."""
    return (0, 0) if value is None else (1, value)


def run_sort(node, rows):
    rows = [dict(row) for row in rows]
    for row in rows:
        row[SORT_KEY] = tuple(evaluate(expr, row) for expr, _ in node.sort_keys)
    # Python's sort is stable, so sorting by the last key first and the first
    # key last gives a correct multi-key ORDER BY (each with its own ASC/DESC).
    for index in range(len(node.sort_keys) - 1, -1, -1):
        descending = node.sort_keys[index][1] == "DESC"
        rows.sort(key=lambda r: null_first(r[SORT_KEY][index]), reverse=descending)
    return rows


def run_project(node, rows):
    out = []
    for row in rows:
        new_row = {key: output_value(evaluate(expr, row))
                   for key, (expr, _) in zip(node.columns, node.items)}
        if SORT_KEY in row:
            new_row[SORT_KEY] = row[SORT_KEY]
        out.append(new_row)
    return out


def run_distinct(node, rows):
    seen, out = set(), []
    for row in rows:
        values = tuple(row[c] for c in node.columns)
        if values not in seen:
            seen.add(values)
            out.append(row)
    return out


# =============================================================================
# Driver
# =============================================================================

def execute(plan: QueryPlan, conn) -> ExecutionResult:
    temps = {}            # step id -> relation (list of row dicts)
    trace = []
    ties_at_limit = False

    for step in plan.steps():
        start = time.perf_counter()
        n = step.node
        inputs = [temps[i] for i in step.inputs]

        if n.op == "SCAN":
            rows = run_scan(n, conn)
        elif n.op == "JOIN":
            rows = run_join(n, inputs[0], inputs[1])
        elif n.op in ("FILTER", "HAVING"):
            rows = run_filter(n, inputs[0])
        elif n.op == "AGGREGATE":
            rows = run_aggregate(n, inputs[0])
        elif n.op == "SORT":
            rows = run_sort(n, inputs[0])
        elif n.op == "PROJECT":
            rows = run_project(n, inputs[0])
        elif n.op == "DISTINCT":
            rows = run_distinct(n, inputs[0])
        elif n.op == "LIMIT":
            source = inputs[0]
            rows = source[:n.limit]
            # Did the cut separate rows that tie on the ORDER BY keys? Then SQL
            # does not define which of the tied rows are kept.
            if 0 < n.limit < len(source) and SORT_KEY in source[0]:
                ties_at_limit = source[n.limit - 1][SORT_KEY] == source[n.limit][SORT_KEY]
        else:
            raise ValueError(f"unknown plan operator {n.op}")

        temps[step.id] = rows
        trace.append(StepTrace(str(step), len(rows), (time.perf_counter() - start) * 1000.0))

    root = plan.root
    final = temps[len(trace)]
    ordered = "SORT" in plan.ops()
    return ExecutionResult(
        headers=list(root.headers),
        rows=[tuple(output_value(row[c]) for c in root.columns) for row in final],
        trace=trace,
        ordered=ordered,
        sort_keys=[row.get(SORT_KEY, ()) for row in final],
        ties_at_limit=ties_at_limit,
    )


# =============================================================================
# Differential check against SQLite
# =============================================================================

def normalize(row):
    """Make values comparable: round floats (summation order can differ in
    the last bits) and map booleans to 0/1."""
    out = []
    for value in row:
        value = output_value(value)
        if isinstance(value, float) and math.isfinite(value):
            value = round(value, 9)
        out.append(value)
    return tuple(out)


def compare_results(ours: ExecutionResult, sqlite_rows):
    """Return (match, detail)."""
    mine = [normalize(r) for r in ours.rows]
    theirs = [normalize(r) for r in sqlite_rows]

    if len(mine) != len(theirs):
        return False, f"row count differs: interpreter {len(mine)}, SQLite {len(theirs)}"

    if not ours.ordered:
        if Counter(mine) == Counter(theirs):
            return True, f"same {len(mine)} row(s) (compared as a multiset: no ORDER BY)"
        return False, "same row count but different rows"

    # ORDER BY: walk through runs of rows that tie on the sort key.
    start = 0
    while start < len(mine):
        end = start
        while end < len(mine) and ours.sort_keys[end] == ours.sort_keys[start]:
            end += 1
        last_run = end == len(mine)
        if not (last_run and ours.ties_at_limit):
            if Counter(mine[start:end]) != Counter(theirs[start:end]):
                return False, f"order differs at row {start + 1}"
        start = end
    detail = f"same {len(mine)} row(s) in the same ORDER BY order"
    if ours.ties_at_limit:
        detail += " (LIMIT cut through tied rows; the tied group was compared by size only)"
    return True, detail


def cross_check(sql: str, ours: ExecutionResult, conn) -> CrossCheck:
    headers, rows = run_query(conn, sql)
    match, detail = compare_results(ours, rows)
    return CrossCheck(match, headers, rows, detail)

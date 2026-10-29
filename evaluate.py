"""
evaluate.py

Measures the system and writes docs/EVALUATION.md. Every number in that
file comes from an actual run of this script - nothing is typed by hand.

1. Mutation test (no API key needed)
   Take each valid query from examples.VALID_QUERIES, apply small,
   deliberate faults ("mutations") - rename a column, drop a GROUP BY
   column, put an aggregate in WHERE, break a string literal, and so on -
   and check that the compiler rejects every mutant. Reported per mutation
   operator: how many mutants were detected, and how many were detected
   with the *expected* error code.

2. Stage timings (no API key needed)
   Run every valid query through every stage several times and report the
   average and maximum time per stage.

3. Live LLM evaluation (ONLY if ANTHROPIC_API_KEY is set)
   Ask the live LLM the 20 questions in examples.GOLD_QUESTIONS and report
   first-attempt validity, validity after repair, execution-match accuracy
   against the gold SQL, the number of LLM calls, and an estimated cost.
   Without a key this section is skipped and says so.

Run with:  python evaluate.py            (--repeat N to change timing repetitions)
"""

import argparse
import copy
import datetime
import platform
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from ast_nodes import (
    AggregateCall, BinaryOp, ColumnRef, Literal, Star, statement_to_sql, walk,
)
from db import get_connection, run_query
from examples import GOLD_QUESTIONS, VALID_QUERIES
from executor import normalize
from pipeline import compile_sql, run_compiled
from symbol_table import SCHEMA

OUTPUT = Path(__file__).resolve().parent / "docs" / "EVALUATION.md"
STAGES = [("lex", "Lexer"), ("parse", "Parser"), ("semantic", "Semantic analysis"),
          ("plan", "Planner"), ("optimize", "Optimizer"), ("execute", "Interpreter"),
          ("sqlite_check", "SQLite cross-check")]


# =============================================================================
# 1. Mutation operators
# =============================================================================
# Each operator takes (sql, analyzed_ast) and returns mutated SQL text, or
# None if it does not apply to this query. AST mutations work on a deep copy
# and are turned back into text with statement_to_sql().

def all_expressions(stmt):
    exprs = [i.expr for i in stmt.select_items]
    exprs += [j.on for j in stmt.joins] + list(stmt.group_by) + [o.expr for o in stmt.order_by]
    exprs += [e for e in (stmt.where, stmt.having) if e is not None]
    return exprs


def real_columns(stmt):
    """Column references that name a real column (not an ORDER BY alias)."""
    return [n for e in all_expressions(stmt) for n in walk(e)
            if isinstance(n, ColumnRef) and n.alias_index is None]


def add_condition(stmt, condition):
    stmt.where = condition if stmt.where is None else BinaryOp("AND", stmt.where, condition)


def m_rename_column(sql, ast):
    stmt = copy.deepcopy(ast)
    cols = real_columns(stmt)
    if not cols:
        return None
    cols[0].name += "_x"
    return statement_to_sql(stmt)


def m_rename_table(sql, ast):
    stmt = copy.deepcopy(ast)
    stmt.from_table.name += "s"
    return statement_to_sql(stmt)


def m_drop_group_by_column(sql, ast):
    """Remove a GROUP BY column that is also selected outside an aggregate."""
    stmt = copy.deepcopy(ast)
    selected = {c.key() for i in stmt.select_items if not isinstance(i.expr, (Star, AggregateCall))
                for c in walk(i.expr) if isinstance(c, ColumnRef)}
    for col in stmt.group_by:
        if col.key() in selected:
            stmt.group_by.remove(col)
            return statement_to_sql(stmt)
    return None


def m_aggregate_in_where(sql, ast):
    stmt = copy.deepcopy(ast)
    add_condition(stmt, BinaryOp(">", AggregateCall("COUNT", Star()), Literal(0)))
    return statement_to_sql(stmt)


def m_compare_text_with_number(sql, ast):
    """Compare a TEXT column with a number, e.g. title = 42."""
    stmt = copy.deepcopy(ast)
    ref = stmt.from_table
    text_cols = [c for c, t in SCHEMA[ref.name].items() if t == "TEXT"]
    add_condition(stmt, BinaryOp("=", ColumnRef(ref.binding, text_cols[0]), Literal(42)))
    return statement_to_sql(stmt)


def m_unqualify_shared_column(sql, ast):
    """Drop the table qualifier from a column that exists in two joined tables."""
    if not ast.joins:
        return None
    stmt = copy.deepcopy(ast)
    tables = [t.name for t in stmt.tables()]
    for col in real_columns(stmt):
        if col.table and sum(col.name in SCHEMA[t] for t in tables) > 1:
            col.table = None
            return statement_to_sql(stmt)
    return None


def m_having_without_group_by(sql, ast):
    if ast.group_by or ast.having is not None:
        return None
    stmt = copy.deepcopy(ast)
    stmt.having = BinaryOp(">", AggregateCall("COUNT", Star()), Literal(0))
    return statement_to_sql(stmt)


def m_sum_of_text(sql, ast):
    """Add SUM(<text column>) - SUM needs a number."""
    stmt = copy.deepcopy(ast)
    ref = stmt.from_table
    text_cols = [c for c, t in SCHEMA[ref.name].items() if t == "TEXT"]
    from ast_nodes import SelectItem
    stmt.select_items.append(SelectItem(AggregateCall("SUM", ColumnRef(ref.binding, text_cols[0]))))
    return statement_to_sql(stmt)


def m_alias_in_where(sql, ast):
    aliases = [i.alias for i in ast.select_items if i.alias]
    if not aliases:
        return None
    stmt = copy.deepcopy(ast)
    add_condition(stmt, BinaryOp("=", ColumnRef(None, aliases[0]), ColumnRef(None, aliases[0])))
    return statement_to_sql(stmt)


def m_unknown_function(sql, ast):
    # Works on the normalized text, where the keyword is always upper-case SELECT.
    return statement_to_sql(ast).replace("SELECT ", "SELECT UPPER(1), ", 1)


def m_drop_from_keyword(sql, ast):
    text = statement_to_sql(ast)
    return text.replace(" FROM ", " ", 1)


def m_unterminated_string(sql, ast):
    text = statement_to_sql(ast)
    last = text.rfind("'")
    return text[:last] + text[last + 1:] if last != -1 else None


def m_illegal_character(sql, ast):
    return statement_to_sql(ast).replace("SELECT ", "SELECT # ", 1)


def m_turn_into_delete(sql, ast):
    return f"DELETE FROM {ast.from_table.name}"


MUTATIONS = [
    # (name, description, expected error code, function)
    ("rename_column", "rename a column to one that does not exist", "S002", m_rename_column),
    ("rename_table", "rename the FROM table", "S001", m_rename_table),
    ("drop_group_by_column", "drop a selected column from GROUP BY", "S010", m_drop_group_by_column),
    ("aggregate_in_where", "add COUNT(*) > 0 to WHERE", "S008", m_aggregate_in_where),
    ("text_vs_number", "compare a TEXT column with 42", "S006", m_compare_text_with_number),
    ("unqualify_join_column", "remove the qualifier of a column shared by joined tables",
     "S003", m_unqualify_shared_column),
    ("having_without_group_by", "add HAVING to a query without GROUP BY", "S011",
     m_having_without_group_by),
    ("sum_of_text", "add SUM(<text column>)", "S007", m_sum_of_text),
    ("alias_in_where", "use a SELECT alias in WHERE", "S016", m_alias_in_where),
    ("unknown_function", "call a function that is not supported", "P004", m_unknown_function),
    ("drop_from_keyword", "delete the FROM keyword", "P001", m_drop_from_keyword),
    ("unterminated_string", "delete the closing quote of a string", "L002", m_unterminated_string),
    ("illegal_character", "insert a '#' character", "L001", m_illegal_character),
    ("turn_into_delete", "replace the query with DELETE", "P002", m_turn_into_delete),
]


def run_mutation_test():
    stats = {name: Counter() for name, *_ in MUTATIONS}
    undetected = []
    for sql in VALID_QUERIES:
        original = compile_sql(sql)
        assert original.ok, f"corpus query is invalid: {sql}"
        for name, _, expected, fn in MUTATIONS:
            mutant = fn(sql, original.ast)
            if mutant is None:
                continue
            result = compile_sql(mutant)
            stats[name]["applied"] += 1
            if result.ok:
                undetected.append((name, mutant))
                continue
            stats[name]["detected"] += 1
            if expected in {e.code for e in result.errors}:
                stats[name]["expected_code"] += 1
    return stats, undetected


# =============================================================================
# 2. Stage timings
# =============================================================================

def run_timings(repeat):
    conn = get_connection()
    samples = defaultdict(list)
    for _ in range(repeat):
        for sql in VALID_QUERIES:
            result = run_compiled(compile_sql(sql), conn)
            for stage, ms in result.timings.items():
                samples[stage].append(ms)
    conn.close()
    return samples


# =============================================================================
# 3. Live LLM evaluation
# =============================================================================

def results_match(ours_rows, gold_rows):
    """Execution match: same rows as the gold query (compared as multisets)."""
    return Counter(normalize(r) for r in ours_rows) == Counter(normalize(r) for r in gold_rows)


def run_live_evaluation(llm, questions=GOLD_QUESTIONS):
    """Score `llm` on (question, gold_sql) pairs. Takes the LLM as a
    parameter so the scoring logic can be tested offline with a fake."""
    from llm_client import LLMError
    from repair_loop import run_repair_loop

    conn = get_connection()
    rows = []
    for question, gold_sql in questions:
        row = {"question": question, "first_valid": False, "final_valid": False,
               "match": False, "attempts": 0, "error": None}
        try:
            result = run_repair_loop(question, llm)
            row["attempts"] = len(result.attempts)
            row["first_valid"] = result.attempts[0].valid
            row["final_valid"] = result.success
            if result.success:
                compiled = run_compiled(compile_sql(result.final.sql), conn)
                _, gold_rows = run_query(conn, gold_sql)
                row["match"] = results_match(compiled.execution.rows, gold_rows)
        except LLMError as e:
            row["error"] = str(e)
        rows.append(row)
    conn.close()
    return rows


# =============================================================================
# Report
# =============================================================================

def pct(part, whole):
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def write_report(stats, undetected, samples, repeat, live):
    total_applied = sum(s["applied"] for s in stats.values())
    total_detected = sum(s["detected"] for s in stats.values())
    total_expected = sum(s["expected_code"] for s in stats.values())

    lines = [
        "# Evaluation",
        "",
        "Generated automatically by `python evaluate.py` - every number below comes from that "
        "run. Re-run the script to refresh it.",
        "",
        f"* Date: {datetime.date.today().isoformat()}",
        f"* Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}, "
        f"{platform.system()} {platform.release()}",
        f"* Corpus: {len(VALID_QUERIES)} valid queries (`examples.VALID_QUERIES`)",
        "",
        "## 1. Mutation test (no API key needed)",
        "",
        "Each operator injects one kind of fault into every corpus query it applies to. A "
        "mutant is *detected* if the compiler rejects it; *expected code* means the error "
        "reported includes the specific code the fault should trigger.",
        "",
        "| Operator | Fault injected | Expected | Mutants | Detected | Rate | With expected code |",
        "|----------|----------------|----------|--------:|---------:|-----:|-------------------:|",
    ]
    for name, description, expected, _ in MUTATIONS:
        s = stats[name]
        lines.append(f"| `{name}` | {description} | {expected} | {s['applied']} | "
                     f"{s['detected']} | {pct(s['detected'], s['applied'])} | "
                     f"{s['expected_code']} ({pct(s['expected_code'], s['applied'])}) |")
    lines += [
        f"| **Total** | | | **{total_applied}** | **{total_detected}** | "
        f"**{pct(total_detected, total_applied)}** | **{total_expected} "
        f"({pct(total_expected, total_applied)})** |",
        "",
    ]
    if undetected:
        lines += ["Undetected mutants (accepted by the compiler):", ""]
        lines += [f"* `{name}`: `{sql}`" for name, sql in undetected]
    else:
        lines.append("No mutant was accepted by the compiler.")
    lines += [
        "",
        "Limitation: every mutant here is invalid *by construction*, so this measures how "
        "reliably the checks fire, not whether the compiler can spot a query that is valid "
        "but answers the wrong question (that needs the live evaluation in section 3).",
        "",
        "## 2. Stage timings (no API key needed)",
        "",
        f"Every corpus query was run through all stages {repeat} times "
        f"({repeat * len(VALID_QUERIES)} runs per stage). Wall-clock milliseconds on this "
        "machine; sub-millisecond numbers are noisy.",
        "",
        "| Stage | Runs | Average (ms) | Median (ms) | Max (ms) |",
        "|-------|-----:|-------------:|------------:|---------:|",
    ]
    for key, label in STAGES:
        values = samples.get(key, [])
        if values:
            lines.append(f"| {label} | {len(values)} | {statistics.mean(values):.4f} | "
                         f"{statistics.median(values):.4f} | {max(values):.4f} |")
    lines += ["", "## 3. Live LLM evaluation", ""]

    if live is None:
        lines += [
            "**Not run:** `ANTHROPIC_API_KEY` was not set when this report was generated, so "
            "no live LLM was called and no accuracy, call-count or cost numbers are reported. "
            "To run it, set the key and re-run `python evaluate.py`.",
            "",
            f"When run, it asks the live model the {len(GOLD_QUESTIONS)} questions in "
            "`examples.GOLD_QUESTIONS` and reports first-attempt validity, validity after "
            "repair, execution-match accuracy against the gold SQL, the number of LLM calls "
            "and an estimated cost.",
        ]
    else:
        llm, rows = live
        n = len(rows)
        cost = llm.estimated_cost()
        lines += [
            f"Model: `{llm.model}`. {n} questions, max 3 attempts each.",
            "",
            "| Metric | Result |",
            "|--------|--------|",
            f"| Valid on first attempt | {sum(r['first_valid'] for r in rows)} / {n} "
            f"({pct(sum(r['first_valid'] for r in rows), n)}) |",
            f"| Valid after repair | {sum(r['final_valid'] for r in rows)} / {n} "
            f"({pct(sum(r['final_valid'] for r in rows), n)}) |",
            f"| Execution match with gold SQL | {sum(r['match'] for r in rows)} / {n} "
            f"({pct(sum(r['match'] for r in rows), n)}) |",
            f"| LLM calls | {llm.calls} |",
            f"| Tokens (input / output) | {llm.input_tokens} / {llm.output_tokens} |",
            f"| Estimated cost (USD) | {'n/a' if cost is None else f'{cost:.4f}'} |",
            "",
            "Cost is estimated from the token counts the API reported and the list prices in "
            "`llm_client.PRICE_PER_MTOK`. Execution match compares result rows as multisets.",
            "",
            "| # | Question | 1st valid | Final valid | Match | Attempts |",
            "|--:|----------|:---------:|:-----------:|:-----:|---------:|",
        ]
        for i, r in enumerate(rows, 1):
            mark = lambda b: "yes" if b else "no"
            note = f" (error: {r['error']})" if r["error"] else ""
            lines.append(f"| {i} | {r['question']}{note} | {mark(r['first_valid'])} | "
                         f"{mark(r['final_valid'])} | {mark(r['match'])} | {r['attempts']} |")
    lines.append("")
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    return total_applied, total_detected, total_expected


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate the NL->SQL compiler.")
    parser.add_argument("--repeat", type=int, default=20, help="timing repetitions (default 20)")
    args = parser.parse_args(argv)

    print("1. Mutation test ...")
    stats, undetected = run_mutation_test()
    print("2. Stage timings ...")
    samples = run_timings(args.repeat)

    from llm_client import LiveLLM, api_key_available
    live = None
    if api_key_available():
        print(f"3. Live LLM evaluation on {len(GOLD_QUESTIONS)} questions ...")
        llm = LiveLLM()
        live = (llm, run_live_evaluation(llm))
    else:
        print("3. Live LLM evaluation skipped: ANTHROPIC_API_KEY is not set.")

    applied, detected, expected = write_report(stats, undetected, samples, args.repeat, live)
    print(f"\nMutants: {applied}, detected: {detected} ({pct(detected, applied)}), "
          f"with expected code: {expected} ({pct(expected, applied)})")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

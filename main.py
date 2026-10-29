"""
main.py

Command-line entry point and live demo. Every compiler stage is printed:

    [1] Lexical analysis   tokens
    [2] Syntax analysis    the AST as a tree
    [3] Semantic analysis  OK, or each structured error with a ^ marker
    [4] Intermediate code  numbered query plan (t1 = SCAN ...)
    [5] Optimization       which passes fired, and the optimized plan
    [6] Execution          our plan interpreter's result
    [7] Cross-check        the same SQL run by SQLite, compared with [6]

Modes (none of them needs an API key):

    python main.py --sql "SELECT ..."   compile and run SQL directly (no LLM)
    python main.py -i                   interactive: "sql> ..." or an English question
    python main.py --list               list the built-in English questions
    python main.py --demo N             run built-in question N
    python main.py                      run all built-in questions

Options: --stub / --live (choose the LLM), --show-plan (plan before and
after optimization in the English-question modes; always on for --sql),
--verbose (token positions, per-step execution timings).
"""

import argparse
import sys

from ast_nodes import statement_tree
from db import get_connection
from errors import caret_line
from examples import BUILTIN_QUESTIONS
from llm_client import LLMError, UnknownQuestionError, get_llm
from pipeline import compile_sql, run_compiled
from repair_loop import MAX_ATTEMPTS, run_repair_loop

WIDTH = 78
STAGE_ORDER = ["lex", "parse", "semantic", "plan", "optimize", "execute", "sqlite_check"]


# =============================================================================
# Output helpers
# =============================================================================

def banner(title):
    print("\n" + "=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def stage(number, title, status=""):
    head = f"[{number}] {title} "
    print(f"\n{head}{'.' * max(3, WIDTH - len(head) - len(status) - 1)} {status}".rstrip())


def indent(text, spaces=4):
    pad = " " * spaces
    return "\n".join(pad + line for line in str(text).splitlines())


def wrap_items(items, spaces=4):
    """Print items separated by two spaces, wrapping at the screen width."""
    line = ""
    for item in items:
        if line and len(line) + len(item) + 2 > WIDTH - spaces:
            print(" " * spaces + line)
            line = ""
        line = f"{line}  {item}" if line else item
    if line:
        print(" " * spaces + line)


def print_tree(node, prefix="    ", is_last=True, is_root=True):
    """ASCII tree:  SelectStatement / +-- SELECT / |   `-- title ..."""
    if is_root:
        print(prefix + node["label"])
        child_prefix = prefix
    else:
        print(prefix + ("`-- " if is_last else "+-- ") + node["label"])
        child_prefix = prefix + ("    " if is_last else "|   ")
    for i, child in enumerate(node["children"]):
        print_tree(child, child_prefix, i == len(node["children"]) - 1, False)


def format_value(value):
    return "NULL" if value is None else str(value)


def print_table(headers, rows, spaces=4, max_rows=50):
    if not rows:
        print(" " * spaces + "(no rows)")
        return
    shown = rows[:max_rows]
    cells = [[format_value(v) for v in row] for row in shown]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    pad = " " * spaces
    print(pad + " | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print(pad + "-+-".join("-" * w for w in widths))
    for row in cells:
        print(pad + " | ".join(v.ljust(w) for v, w in zip(row, widths)))
    if len(rows) > max_rows:
        print(pad + f"... ({len(rows) - max_rows} more rows)")
    print(pad + f"({len(rows)} row{'s' if len(rows) != 1 else ''})")


def print_errors(compiled):
    for err in compiled.errors:
        print(indent(err.format()))
        marker = caret_line(compiled.sql, err.line, err.col)
        if marker:
            print(indent(marker, 8))


# =============================================================================
# Printing the compiler stages for one SQL string
# =============================================================================

def print_front_end(compiled, opts):
    """Stages 1-3. Returns True if the query is valid."""
    if compiled.tokens is None:
        stage(1, "Lexical analysis", "FAILED")
        print_errors(compiled)
        return False
    tokens = [t for t in compiled.tokens if t.type != "EOF"]
    stage(1, "Lexical analysis", f"{len(tokens)} tokens")
    if opts.verbose:
        wrap_items([f"{t.type}:{t.value}@{t.line}:{t.col}" if t.type in ("IDENT", "NUMBER", "STRING")
                    else f"{t.type}@{t.line}:{t.col}" for t in tokens])
    else:
        wrap_items([f"{t.type}:{t.value}" if t.type in ("IDENT", "NUMBER", "STRING") else t.type
                    for t in tokens])

    if compiled.ast is None:
        stage(2, "Syntax analysis", "FAILED")
        print_errors(compiled)
        return False
    stage(2, "Syntax analysis", "AST built")
    print_tree(statement_tree(compiled.ast))

    if compiled.errors:
        n = len(compiled.errors)
        stage(3, "Semantic analysis", f"FAILED ({n} error{'s' if n > 1 else ''})")
        print_errors(compiled)
        return False
    stage(3, "Semantic analysis", "OK")
    print("    All tables and columns resolved; types, aggregates and GROUP BY rules checked.")
    return True


def print_plan_stages(compiled, show_unoptimized):
    """Stages 4-5."""
    opt = compiled.optimization
    if show_unoptimized:
        stage(4, "Intermediate code (query plan)", f"{len(opt.before.steps())} steps")
        print(indent(opt.before))
    fired = opt.fired
    stage(5, "Optimization", f"{len(fired)} of {len(opt.passes)} passes fired")
    if show_unoptimized:
        for report in opt.passes:
            print(f"    {report.name:<28} {'FIRED' if report.fired else 'no change'}")
            for note in report.notes:
                print(indent("- " + note, 8))
    elif fired:
        print("    Passes fired: " + ", ".join(fired))
    print("    Optimized plan:")
    print(indent(opt.after, 6))


def print_execution_stages(compiled, opts):
    """Stages 6-7. Returns True if the interpreter agrees with SQLite."""
    result, check = compiled.execution, compiled.check
    stage(6, "Execution (plan interpreter)", f"{len(result.rows)} rows")
    for step in result.trace:
        timing = f"  {step.ms:7.3f} ms" if opts.verbose else ""
        print(f"    {step.step:<62.62} -> {step.rows_out:>3} rows{timing}")
    print()
    print_table(result.headers, result.rows)

    if check.match:
        stage(7, "Cross-check vs SQLite", "MATCH")
        print(f"    {check.detail}")
    else:
        stage(7, "Cross-check vs SQLite", "MISMATCH - BUG")
        print(f"    {check.detail}")
        print("    The interpreter disagrees with SQLite. SQLite's result:")
        print_table(check.sqlite_headers, check.sqlite_rows, spaces=6)
    return check.match


def print_timings(compiled):
    parts = [f"{name} {compiled.timings[name]:.2f}" for name in STAGE_ORDER
             if name in compiled.timings]
    print("\n    Stage timings (ms): " + " | ".join(parts))


def compile_and_run(sql, conn, opts, show_unoptimized):
    """Compile, print every stage, and (if valid) execute. Returns True on success."""
    compiled = compile_sql(sql)
    if not print_front_end(compiled, opts):
        print("\n    Stages 4-7 skipped: the query did not pass validation.")
        return False
    run_compiled(compiled, conn)
    print_plan_stages(compiled, show_unoptimized)
    ok = print_execution_stages(compiled, opts)
    print_timings(compiled)
    return ok


# =============================================================================
# Modes
# =============================================================================

def run_sql_mode(sql, conn, opts):
    banner("SQL: " + " ".join(sql.split()))
    return compile_and_run(sql, conn, opts, show_unoptimized=True)


def run_question(question, llm, conn, opts, title=None):
    banner(title or f"Question: {question}")
    print(f'Question: "{question}"')
    try:
        result = run_repair_loop(question, llm)
    except UnknownQuestionError as e:
        print("\n" + indent(e, 2))
        return False
    except LLMError as e:
        print(f"\n  LLM call failed: {e}")
        return False

    source = "stub LLM (pre-written)" if llm.mode == "stub" else f"live LLM ({llm.model})"
    for attempt in result.attempts:
        status = "valid" if attempt.valid else f"{len(attempt.errors)} error(s)"
        print(f"\n--- Attempt {attempt.number} of max {MAX_ATTEMPTS}, from {source}: {status} ---")
        print(indent(attempt.sql))
        if not attempt.valid:
            print_errors(attempt.compiled)
            if attempt is not result.final:
                print("    -> errors (code, message, line/col) sent back to the LLM for repair")

    if not result.success:
        print(f"\nFAILED: no valid SQL after {len(result.attempts)} attempts.")
        return False
    note = " (after repair)" if len(result.attempts) > 1 else ""
    print(f"\nValid SQL found on attempt {len(result.attempts)}{note}. Compiling it:")
    return compile_and_run(result.final.sql, conn, opts, show_unoptimized=opts.show_plan)


def list_questions():
    print("Built-in English questions (work offline with the stub LLM):\n")
    for i, q in enumerate(BUILTIN_QUESTIONS, start=1):
        tag = f"   [fails first: {q['repair']}, then repaired]" if q.get("repair") else ""
        print(f"  {i:>2}. {q['question']}{tag}")
    print('\nRun one with:  python main.py --demo N')
    print('Or compile your own SQL:  python main.py --sql "SELECT title FROM Books"')


INTERACTIVE_HELP = """\
  sql> SELECT ...     compile and run SQL directly (no LLM involved)
  <English question>  ask the LLM (stub mode knows only the built-in questions)
  :list               show the built-in questions
  :demo N             run built-in question N
  :help               show this help
  :quit               exit (or Ctrl+C / Ctrl+Z / Ctrl+D)"""


def interactive(llm, conn, opts):
    print("NL -> SQL compiler: interactive mode")
    print(INTERACTIVE_HELP)
    while True:
        try:
            line = input("\nnl2sql> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        lower = line.lower()
        if lower in (":quit", ":q", "quit", "exit"):
            return
        if lower == ":help":
            print(INTERACTIVE_HELP)
        elif lower == ":list":
            list_questions()
        elif lower.startswith(":demo"):
            run_demo_number(lower[5:].strip(), llm, conn, opts)
        elif lower.startswith("sql>"):
            sql = line[4:].strip()
            if sql:
                run_sql_mode(sql, conn, opts)
            else:
                print("  Type a query after 'sql>', e.g.  sql> SELECT title FROM Books")
        else:
            run_question(line, llm, conn, opts)


def run_demo_number(text, llm, conn, opts):
    try:
        n = int(text)
    except ValueError:
        n = 0
    if not 1 <= n <= len(BUILTIN_QUESTIONS):
        print(f"  There are {len(BUILTIN_QUESTIONS)} built-in questions; "
              f"choose 1-{len(BUILTIN_QUESTIONS)} (see --list).")
        return None
    q = BUILTIN_QUESTIONS[n - 1]["question"]
    return run_question(q, llm, conn, opts, title=f"Example {n}: {q}")


# =============================================================================
# Entry point
# =============================================================================

def parse_args(argv):
    p = argparse.ArgumentParser(
        description="NL -> SQL compiler: hand-written lexer, parser, semantic analyzer, "
                    "planner, optimizer and interpreter. Works without an API key.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--sql", metavar="QUERY", help="compile and run this SQL (no LLM)")
    mode.add_argument("-i", "--interactive", action="store_true", help="interactive loop")
    mode.add_argument("--list", action="store_true", help="list built-in English questions")
    mode.add_argument("--demo", type=int, metavar="N", help="run built-in question N")
    llm_choice = p.add_mutually_exclusive_group()
    llm_choice.add_argument("--stub", action="store_true", help="force the offline stub LLM")
    llm_choice.add_argument("--live", action="store_true",
                            help="use the Anthropic API (needs ANTHROPIC_API_KEY)")
    p.add_argument("--show-plan", action="store_true",
                   help="show the plan before and after optimization (always on for --sql)")
    p.add_argument("--verbose", action="store_true",
                   help="token positions and per-step execution timings")
    return p.parse_args(argv)


def main(argv=None):
    # Never crash on a console that cannot print some character.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    opts = parse_args(argv)

    if opts.list:
        list_questions()
        return 0

    conn = get_connection()
    try:
        if opts.sql is not None:
            return 0 if run_sql_mode(opts.sql, conn, opts) else 1

        llm, notice = get_llm("stub" if opts.stub else "live" if opts.live else None)
        print(notice)

        if opts.interactive:
            interactive(llm, conn, opts)
            return 0
        if opts.demo is not None:
            ok = run_demo_number(str(opts.demo), llm, conn, opts)
            return 2 if ok is None else (0 if ok else 1)

        outcomes = []
        for n, q in enumerate(BUILTIN_QUESTIONS, start=1):
            outcomes.append(run_question(q["question"], llm, conn, opts,
                                         title=f"Example {n}: {q['question']}"))
        banner("Summary")
        for n, (q, ok) in enumerate(zip(BUILTIN_QUESTIONS, outcomes), start=1):
            print(f"  [{'OK' if ok else 'FAILED'}] {n:>2}. {q['question']}")
        print(f"\n  {sum(outcomes)} of {len(outcomes)} examples succeeded.")
        if llm.mode == "stub":
            print("  (STUB MODE: English->SQL answers were pre-written; all compiler stages ran for real.)")
        return 0 if all(outcomes) else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

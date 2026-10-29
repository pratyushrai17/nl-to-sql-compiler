"""
tests/run_all.py

Runs the whole unittest suite and writes docs/TEST_RESULTS.md with the
real counts (per module and in total). Nothing in that file is typed by
hand - re-run this script to refresh it.

Run with:  python -m tests.run_all
(plain `python -m unittest` runs the same tests without writing the file)
"""

import datetime
import platform
import sqlite3
import sys
import time
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "TEST_RESULTS.md"

DESCRIPTIONS = {
    "test_lexer": "Lexer: tokens, positions, lexical errors (L001-L004)",
    "test_parser": "Parser: clauses, precedence, round-trip printing, syntax errors (P001-P004)",
    "test_semantic": "Semantic analyzer: resolution, types, aggregates, GROUP BY / HAVING / ORDER BY rules",
    "test_query_plan": "Planner: operator order, step numbering, star expansion",
    "test_optimizer": "Optimizer: constant folding, predicate pushdown, projection removal",
    "test_executor": "Interpreter: differential tests vs SQLite, operators, expression semantics",
    "test_valid_queries": "Valid queries: one test per corpus query (compile + run + match SQLite)",
    "test_boundaries": "Boundary cases: empty input, formatting, deep nesting, long chains",
    "test_repair_loop": "Repair loop with a fake LLM: error feedback, history, attempt bound",
    "test_llm_client": "LLM client without network: stub questions, mode selection, prompts",
    "test_cli": "main.py end to end (subprocess, no API key): --sql, --demo, --list, -i",
    "test_visualize": "visualize.py: self-contained HTML with every section",
}


class CountingResult(unittest.TextTestResult):
    """Records the outcome of every test, keyed by its module."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = defaultdict(lambda: defaultdict(int))

    def _module(self, test):
        return type(test).__module__.split(".")[-1]

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes[self._module(test)]["passed"] += 1

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes[self._module(test)]["failed"] += 1

    def addError(self, test, err):
        super().addError(test, err)
        self.outcomes[self._module(test)]["errors"] += 1

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcomes[self._module(test)]["skipped"] += 1


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    runner = unittest.TextTestRunner(resultclass=CountingResult, verbosity=1)
    start = time.perf_counter()
    result = runner.run(suite)
    seconds = time.perf_counter() - start

    totals = defaultdict(int)
    rows = []
    for module in sorted(result.outcomes, key=lambda m: list(DESCRIPTIONS).index(m)
                         if m in DESCRIPTIONS else 99):
        counts = result.outcomes[module]
        total = sum(counts.values())
        for key in ("passed", "failed", "errors", "skipped"):
            totals[key] += counts[key]
        rows.append(f"| `{module}.py` | {DESCRIPTIONS.get(module, '')} | {total} | "
                    f"{counts['passed']} | {counts['failed'] + counts['errors']} |")
    grand_total = sum(totals.values())
    status = "ALL PASSED" if result.wasSuccessful() else "FAILURES PRESENT"

    lines = [
        "# Test results",
        "",
        "Generated automatically by `python -m tests.run_all` - do not edit by hand.",
        "",
        f"* Date: {datetime.date.today().isoformat()}",
        f"* Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}, "
        f"{platform.system()} {platform.release()}",
        "* Framework: `unittest` (standard library; no extra install)",
        "* No API key and no network access were used",
        f"* Run time: {seconds:.2f} s",
        "",
        f"## Summary: {status}",
        "",
        f"**{grand_total} tests: {totals['passed']} passed, {totals['failed']} failed, "
        f"{totals['errors']} errors, {totals['skipped']} skipped.**",
        "",
        "Some tests loop over several inputs using `subTest`; each such loop counts as "
        "one test above.",
        "",
        "## Per module",
        "",
        "| Module | What it covers | Tests | Passed | Failed/Errors |",
        "|--------|----------------|------:|-------:|--------------:|",
        *rows,
        "",
        "## How to run",
        "",
        "```",
        "python -m unittest            # run all tests",
        "python -m unittest -v         # with one line per test",
        "python -m tests.run_all       # run all tests and regenerate this file",
        "```",
        "",
    ]
    if not result.wasSuccessful():
        lines += ["## Failures", ""]
        for test, trace in result.failures + result.errors:
            lines += [f"### {test.id()}", "", "```", trace.strip(), "```", ""]

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {OUTPUT}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

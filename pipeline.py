"""
pipeline.py

Drives the compiler stages for one SQL string and times each stage.

    front_end(sql)    lexer -> parser -> semantic analyzer
    compile_sql(sql)  front_end, then the planner (intermediate code) and
                      the optimizer
    run_compiled(result, conn)
                      execute the optimized plan with our interpreter, then
                      cross-check against SQLite running the original SQL

Every stage reports problems as errors.CompileError objects (lexical,
syntax or semantic), so callers - main.py and the repair loop - handle all
three kinds the same way.

Safety net: the parser already rejects over-deep expressions (P005), but if
any later stage still ran out of Python recursion depth, the RecursionError
is turned into the same structured P005 error instead of a crash.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from errors import CompileError, LexError, ParseError
from executor import CrossCheck, ExecutionResult, cross_check, execute
from lexer import tokenize
from optimizer import OptimizationResult, optimize
from parser import parse
from query_plan import QueryPlan, build_plan
from semantic_analyzer import analyze


@dataclass
class CompileResult:
    sql: str
    tokens: Optional[list] = None
    ast: Optional[object] = None
    errors: List[CompileError] = field(default_factory=list)
    failed_stage: Optional[str] = None          # "lexical" / "syntax" / "semantic"
    plan: Optional[QueryPlan] = None            # set by compile_sql when valid
    optimization: Optional[OptimizationResult] = None
    execution: Optional[ExecutionResult] = None    # set by run_compiled
    check: Optional[CrossCheck] = None             # set by run_compiled
    timings: Dict[str, float] = field(default_factory=dict)   # stage -> milliseconds

    @property
    def ok(self):
        return not self.errors

    @property
    def optimized_plan(self):
        return self.optimization.after if self.optimization else None


class _Timer:
    """with _Timer(result.timings, "lex"): ...  records elapsed milliseconds."""

    def __init__(self, timings, stage):
        self.timings, self.stage = timings, stage

    def __enter__(self):
        self.start = time.perf_counter()

    def __exit__(self, *exc):
        self.timings[self.stage] = (time.perf_counter() - self.start) * 1000.0
        return False


def too_complex_error():
    return ParseError("P005", "The query is too deeply nested to process; simplify it")


def front_end(sql: str) -> CompileResult:
    """Run lexer, parser and semantic analyzer. Never raises a CompileError:
    the first lexical/syntax error, or all semantic errors, are returned in
    result.errors."""
    result = CompileResult(sql=sql)
    try:
        return _front_end(result)
    except RecursionError:
        result.errors, result.failed_stage = [too_complex_error()], "syntax"
        return result


def _front_end(result):
    sql = result.sql
    try:
        with _Timer(result.timings, "lex"):
            result.tokens = tokenize(sql)
        with _Timer(result.timings, "parse"):
            result.ast = parse(result.tokens)
    except (LexError, ParseError) as e:
        result.errors = [e]
        result.failed_stage = e.stage
        return result

    with _Timer(result.timings, "semantic"):
        result.errors = analyze(result.ast)
    if result.errors:
        result.failed_stage = "semantic"
    return result


def compile_sql(sql: str) -> CompileResult:
    """front_end() plus plan generation and optimization for valid queries."""
    result = front_end(sql)
    if result.ok:
        try:
            with _Timer(result.timings, "plan"):
                result.plan = build_plan(result.ast)
            with _Timer(result.timings, "optimize"):
                result.optimization = optimize(result.plan)
        except RecursionError:
            result.plan = result.optimization = None
            result.errors, result.failed_stage = [too_complex_error()], "syntax"
    return result


def run_compiled(result: CompileResult, conn) -> CompileResult:
    """Execute a successfully compiled query and cross-check it with SQLite."""
    with _Timer(result.timings, "execute"):
        result.execution = execute(result.optimized_plan, conn)
    with _Timer(result.timings, "sqlite_check"):
        result.check = cross_check(result.sql, result.execution, conn)
    return result

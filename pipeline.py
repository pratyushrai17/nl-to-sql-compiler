"""
pipeline.py

Drives the compiler stages for one SQL string and times each stage.

    front_end(sql)   lexer -> parser -> semantic analyzer

Every stage reports problems as errors.CompileError objects (lexical,
syntax or semantic), so callers - main.py and the repair loop - handle all
three kinds the same way.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from errors import CompileError, LexError, ParseError
from lexer import tokenize
from parser import parse
from semantic_analyzer import analyze


@dataclass
class CompileResult:
    sql: str
    tokens: Optional[list] = None
    ast: Optional[object] = None
    errors: List[CompileError] = field(default_factory=list)
    failed_stage: Optional[str] = None          # "lexical" / "syntax" / "semantic"
    timings: Dict[str, float] = field(default_factory=dict)   # stage -> milliseconds

    @property
    def ok(self):
        return not self.errors


class _Timer:
    """with _Timer(result.timings, "lex"): ...  records elapsed milliseconds."""

    def __init__(self, timings, stage):
        self.timings, self.stage = timings, stage

    def __enter__(self):
        self.start = time.perf_counter()

    def __exit__(self, *exc):
        self.timings[self.stage] = (time.perf_counter() - self.start) * 1000.0
        return False


def front_end(sql: str) -> CompileResult:
    """Run lexer, parser and semantic analyzer. Never raises a CompileError:
    the first lexical/syntax error, or all semantic errors, are returned in
    result.errors."""
    result = CompileResult(sql=sql)
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

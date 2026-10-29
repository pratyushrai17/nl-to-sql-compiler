"""
repair_loop.py

Orchestrates generate -> compile -> (on error) repair for one question:

    LLM generates SQL -> lexer -> parser -> semantic analyzer
        -> if any stage reports an error, the structured errors (code,
           message, line/column) are sent back to the LLM, which returns a
           corrected query -> compile again ... (bounded)

Lexical, syntax and semantic errors all take the same path: each compiler
stage reports a CompileError, and the loop does not care which stage it
came from.

`generate` is the LLM call, passed in so tests can substitute a fake. It is
called as generate(question, schema, previous_sql=None, errors=None).

`seed_sql` lets a caller supply attempt #1 directly instead of asking the
LLM, so the "fails validation, then gets repaired" path can be demonstrated
reliably.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from errors import CompileError
from pipeline import CompileResult, front_end
from symbol_table import schema_description


@dataclass
class Attempt:
    number: int
    sql: str
    source: str            # where the SQL came from, e.g. "llm" or "llm (repair)"
    compiled: CompileResult

    @property
    def errors(self) -> List[CompileError]:
        return self.compiled.errors

    @property
    def tokens(self):
        return self.compiled.tokens

    @property
    def ast(self):
        return self.compiled.ast

    @property
    def valid(self) -> bool:
        return self.compiled.ok


@dataclass
class PipelineResult:
    question: str
    attempts: List[Attempt] = field(default_factory=list)
    success: bool = False

    @property
    def final(self) -> Attempt:
        return self.attempts[-1]


def run_pipeline(question: str, max_retries: int = 2, seed_sql: Optional[str] = None,
                 generate=None) -> PipelineResult:
    if generate is None:
        from llm_client import generate_sql as generate

    schema = schema_description()
    result = PipelineResult(question=question)

    sql = seed_sql
    source = "seed (simulated bad first attempt)" if seed_sql else "llm"

    for number in range(1, max_retries + 2):   # attempt 1 + up to max_retries repairs
        if sql is None:
            sql = generate(question, schema)

        compiled = front_end(sql)
        result.attempts.append(Attempt(number, sql, source, compiled))

        if compiled.ok:
            result.success = True
            return result
        if number == max_retries + 1:
            return result

        sql = generate(question, schema, previous_sql=sql, errors=compiled.errors)
        source = "llm (repair)"

    return result

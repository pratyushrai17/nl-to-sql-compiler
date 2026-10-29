"""
repair_loop.py

Orchestrates generate -> compile -> (on error) repair for one question:

    attempt 1:  LLM writes SQL  -> lexer -> parser -> semantic analyzer
    on error:   the structured errors (code, message, line/column) of every
                failed attempt so far are sent back to the LLM, which returns
                a corrected query -> compile again
    stop:       when a query compiles cleanly, or after MAX_ATTEMPTS attempts

Lexical, syntax and semantic errors all take the same path: every compiler
stage reports a CompileError, and the loop does not care which stage it
came from.

The LLM is passed in as an object with generate(question, history), so the
same loop runs with the offline StubLLM, the LiveLLM, or a fake in tests.

history is a list with one dict per failed attempt:
    {"attempt": 1, "sql": "SELECT ...", "errors": [CompileError.to_dict(), ...]}
"""

from dataclasses import dataclass, field
from typing import List

from errors import CompileError
from pipeline import CompileResult, compile_sql

MAX_ATTEMPTS = 3


@dataclass
class Attempt:
    number: int
    sql: str
    compiled: CompileResult

    @property
    def errors(self) -> List[CompileError]:
        return self.compiled.errors

    @property
    def valid(self) -> bool:
        return self.compiled.ok

    def history_entry(self):
        return {"attempt": self.number, "sql": self.sql,
                "errors": [e.to_dict() for e in self.errors]}


@dataclass
class RepairResult:
    question: str
    attempts: List[Attempt] = field(default_factory=list)
    success: bool = False

    @property
    def final(self) -> Attempt:
        return self.attempts[-1]

    @property
    def history(self):
        return [a.history_entry() for a in self.attempts if not a.valid]


def run_repair_loop(question, llm, max_attempts=MAX_ATTEMPTS) -> RepairResult:
    """Ask `llm` for SQL and repair it until it compiles (or attempts run out).
    LLMError from the LLM propagates to the caller."""
    result = RepairResult(question=question)
    for number in range(1, max_attempts + 1):
        sql = llm.generate(question, result.history)
        compiled = compile_sql(sql)
        result.attempts.append(Attempt(number, sql, compiled))
        if compiled.ok:
            result.success = True
            break
    return result

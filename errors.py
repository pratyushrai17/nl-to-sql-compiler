"""
errors.py

Structured compiler errors shared by every stage of the front-end.

Every error carries:
  - code     a stable identifier, e.g. "L001", "P001", "S003"
             (L = lexical, P = parse/syntax, S = semantic)
  - message  a human-readable, *actionable* description - the repair loop
             sends this text straight back to the LLM, so it should say
             what to fix, not just what went wrong
  - line/col 1-based position in the SQL source (None if not applicable)

The lexer and parser stop at the first error (they raise). The semantic
analyzer collects every error it finds and returns them as a list, because
semantic errors are independent of each other and it is more useful to the
LLM to see all of them at once.

The full table of error codes lives in docs/GRAMMAR.md ("Error codes").
"""


class CompileError(Exception):
    """Base class for all lexical, syntax and semantic errors."""

    stage = "compile"  # overridden by subclasses

    def __init__(self, code, message, line=None, col=None):
        self.code = code
        self.message = message
        self.line = line
        self.col = col
        super().__init__(self.format())

    def location(self):
        if self.line is None:
            return ""
        return f" at line {self.line}, col {self.col}"

    def format(self):
        """One-line form used in CLI output and in the LLM repair prompt,
        e.g. "[S002] semantic error at line 1, col 8: Column 'email' ..." """
        return f"[{self.code}] {self.stage} error{self.location()}: {self.message}"

    def to_dict(self):
        """Plain-data form used in the repair history and in tests."""
        return {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "line": self.line,
            "col": self.col,
        }

    def __repr__(self):
        return f"{type(self).__name__}({self.code!r}, line={self.line}, col={self.col})"


class LexError(CompileError):
    stage = "lexical"


class ParseError(CompileError):
    stage = "syntax"


class SemanticError(CompileError):
    stage = "semantic"


def caret_line(source, line, col):
    """Return two lines: the offending source line and a '^' under `col`.
    Used by main.py to point at the error position in the SQL text."""
    if line is None:
        return ""
    lines = source.split("\n")
    if not 1 <= line <= len(lines):
        return ""
    text = lines[line - 1]
    return f"{text}\n{' ' * (col - 1)}^"

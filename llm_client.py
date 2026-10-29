"""
llm_client.py

The "front end of the front end": turns an English question into candidate
SQL. Two interchangeable implementations share one interface:

    llm.generate(question, history) -> sql string

`history` is the repair loop's list of earlier failed attempts, each a dict
{"attempt": n, "sql": "...", "errors": [{"code", "message", "line", "col", ...}]}.
An empty history means "first attempt".

  StubLLM  - offline, no API key. Answers only the built-in questions in
             examples.BUILTIN_QUESTIONS with pre-written SQL. Two of them
             return invalid SQL first, so the repair path runs offline too.
  LiveLLM  - calls the Anthropic API. Used only when ANTHROPIC_API_KEY is set.

get_llm() picks one: live if a key is present (or --live), otherwise stub,
and returns a notice to print so stub mode is always visible.

Key handling: the API key is read only from the ANTHROPIC_API_KEY
environment variable, by the Anthropic SDK itself. This module never reads
the key's value into a variable, never prints or logs it, and never stores
it anywhere.
"""

import os
import re

from examples import BUILTIN_QUESTIONS
from symbol_table import schema_description

DEFAULT_MODEL = "claude-sonnet-5-5"
MODEL = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

# Approximate list prices in USD per million tokens (input, output), used
# only for the cost *estimate* in evaluate.py. Check current pricing before
# relying on these numbers.
PRICE_PER_MTOK = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# Models that accept the server-side refusal fallback ("fallbacks": "default").
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}

STUB_NOTICE = (
    "*** STUB MODE: the LLM is simulated. English questions are answered with\n"
    "*** pre-written SQL for the built-in examples (python main.py --list).\n"
    "*** Every compiler stage after that is real. Set ANTHROPIC_API_KEY to use a live LLM."
)


class LLMError(Exception):
    """The LLM could not produce SQL (API failure, unknown stub question, ...)."""


class UnknownQuestionError(LLMError):
    """Stub mode was asked a question it has no pre-written answer for."""


def api_key_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def normalize_question(text):
    """Lower-case and drop punctuation, so small typing differences still match."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


# =============================================================================
# Stub LLM (offline)
# =============================================================================

class StubLLM:
    mode = "stub"

    def __init__(self, questions=BUILTIN_QUESTIONS):
        self.by_question = {normalize_question(q["question"]): q for q in questions}
        self.calls = 0

    def knows(self, question):
        return normalize_question(question) in self.by_question

    def generate(self, question, history):
        self.calls += 1
        entry = self.by_question.get(normalize_question(question))
        if entry is None:
            raise UnknownQuestionError(
                "The offline stub LLM only knows the 10 built-in questions "
                "(see: python main.py --list).\n"
                "To compile your own query without an API key, use:\n"
                '    python main.py --sql "SELECT ..."\n'
                "or, in interactive mode, type:  sql> SELECT ...\n"
                "To ask arbitrary English questions, set ANTHROPIC_API_KEY (live LLM)."
            )
        responses = entry["responses"]
        # Attempt k gets response k; if the stub runs out it repeats its last answer.
        return responses[min(len(history), len(responses) - 1)]


# =============================================================================
# Live LLM (Anthropic API)
# =============================================================================

SYSTEM_PROMPT = """You translate questions about a library database into SQLite SELECT \
statements. Output exactly one SELECT statement and nothing else: no markdown fences, \
no explanation, no comments.

The SQL is checked by a strict compiler that supports only this subset:
- SELECT [DISTINCT] ... FROM ... [INNER] JOIN ... ON ... WHERE ... GROUP BY ... HAVING ...
  ORDER BY ... LIMIT n
- operators: AND OR NOT, = != <> < > <= >=, + - * /, LIKE, IN (list), BETWEEN
- aggregates: COUNT(*), COUNT([DISTINCT] col), SUM, AVG, MIN, MAX
- no subqueries, no LEFT/RIGHT/OUTER joins, no other functions, no NULL
- every non-aggregated SELECT column must appear in GROUP BY
- qualify columns that exist in more than one joined table (e.g. Books.id)
- text values in single quotes

Schema (use only these tables and columns):
{schema}"""


def extract_sql(text):
    """Strip markdown code fences and a trailing semicolon if the model adds them."""
    text = text.strip()
    fence = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return text.rstrip(";").strip()


def build_user_message(question, history):
    """First attempt: just the question. Repair attempts: the question plus
    every earlier attempt with its structured compiler errors."""
    if not history:
        return f"Question: {question}"
    lines = [f"Question: {question}", "",
             "Your earlier SQL failed the compiler's checks. Fix it."]
    for entry in history:
        lines.append(f"\nAttempt {entry['attempt']}:\n{entry['sql']}\nErrors:")
        for err in entry["errors"]:
            where = f" (line {err['line']}, col {err['col']})" if err.get("line") else ""
            lines.append(f"- [{err['code']}] {err['stage']} error{where}: {err['message']}")
    lines.append("\nReturn only the corrected SELECT statement.")
    return "\n".join(lines)


class LiveLLM:
    mode = "live"

    def __init__(self, model=None):
        import anthropic   # imported here so stub mode never needs the SDK
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()   # the SDK reads ANTHROPIC_API_KEY itself
        self.model = model or MODEL
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def generate(self, question, history):
        anthropic = self.anthropic
        request = dict(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT.format(schema=schema_description()),
            messages=[{"role": "user", "content": build_user_message(question, history)}],
            output_config={"effort": "low"},   # short, well-specified task
        )
        try:
            if self.model in FALLBACK_MODELS:
                # If the model declines, the API retries on a fallback model in the same call.
                response = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
            else:
                response = self.client.messages.create(**request)
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic API rejected the credentials - check ANTHROPIC_API_KEY "
                           f"(status {e.status_code}).") from None
        except anthropic.NotFoundError:
            raise LLMError(f"Model '{self.model}' was not found - check ANTHROPIC_MODEL.") from None
        except anthropic.RateLimitError:
            raise LLMError("Anthropic API rate limit reached; try again shortly.") from None
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error (status {e.status_code}).") from None
        except anthropic.APIConnectionError:
            raise LLMError("Could not reach the Anthropic API (network problem).") from None

        self.calls += 1
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens

        if response.stop_reason == "refusal":
            raise LLMError("The model declined to answer this question.")
        text = "".join(block.text for block in response.content if block.type == "text")
        sql = extract_sql(text)
        if not sql:
            raise LLMError("The model returned an empty response instead of SQL.")
        return sql

    def estimated_cost(self):
        price_in, price_out = PRICE_PER_MTOK.get(self.model, (None, None))
        if price_in is None:
            return None
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000


# =============================================================================
# Choosing an implementation
# =============================================================================

def get_llm(force=None):
    """Return (llm, notice). `force` is None (auto), "stub" or "live".
    Without an API key, "live" falls back to the stub with an explanation."""
    if force == "stub":
        return StubLLM(), STUB_NOTICE
    if api_key_available():
        try:
            return LiveLLM(), f"LIVE MODE: using model {MODEL} via the Anthropic API."
        except ImportError:
            return StubLLM(), ("The 'anthropic' package is not installed "
                               "(pip install -r requirements.txt).\n" + STUB_NOTICE)
    if force == "live":
        return StubLLM(), ("--live was requested but ANTHROPIC_API_KEY is not set; "
                           "falling back.\n" + STUB_NOTICE)
    return StubLLM(), STUB_NOTICE

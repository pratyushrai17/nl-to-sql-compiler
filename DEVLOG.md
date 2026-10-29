# Development log

Dates for Phase 1 are taken from the git history. All Phase 2 + 3 work was
done on 2026-10-29, one commit per stage (see `git log`).

## 2026-09-16: Phase 1 prototype
* First end-to-end pipeline: regex-based lexer, small recursive-descent
  parser (SELECT / WHERE / one JOIN / GROUP BY / ORDER BY), semantic checks
  for unknown tables/columns, ambiguity, and one GROUP BY misuse rule.
* LLM generation and a 2-retry repair loop; three hard-coded demo examples.

## 2026-09-17: Stub mode
* Without `ANTHROPIC_API_KEY`, `llm_client.py` returned pre-written SQL for
  the three demo questions so the demo could run offline.

## 2026-09-18: Phase 1 review build
* Added `visualize.py` (HTML demo page) and the Phase 1 README.

## 2026-10-29: Phases 2 + 3

**Planning.** Updated CLAUDE.md: Phases 2+3 in progress, and the system must
be fully demoable offline. Priority order: grammar, errors, IR, optimizer,
interpreter, CLI modes, LLM, visualization, tests, evaluation, docs, security.

**Stage 1: grammar.** Rewrote the lexer as a character-by-character scanner
with line/column tracking (easier to explain than a regex, and gives
positions for free). Rewrote the parser with layered precedence (OR < AND <
NOT < predicates < + - < * / < unary), parentheses, DISTINCT, LIMIT,
optional AS aliases, HAVING, LIKE / IN / BETWEEN (with NOT), arithmetic, and
COUNT(DISTINCT). Wrote the BNF in `docs/GRAMMAR.md`.
* *Bug fixed from Phase 1:* the old parser accepted `LEFT JOIN` but executed
  it as an inner join. It is now rejected with P003, because silently
  changing the meaning of a query is worse than refusing it.
* Non-SELECT statements are rejected with P002, including `SELECT ...;
  DROP ...`.

**Stage 2: error handling.** Added `CompileError` (code, message, line,
col). The semantic analyzer became one recursive type-checking walk and
gained type mismatch, operand type, aggregate-in-WHERE, nested aggregate,
HAVING, boolean-condition, ORDER BY and alias rules (S006-S016). It
collects every error. The repair loop now receives structured errors from
all three stages.

**Stage 3: intermediate code.** `query_plan.py`: a tree of relational
operators linearized into numbered steps. Decision: SORT runs before PROJECT
so ORDER BY can use non-selected columns.

**Stage 4: optimizer.** Constant folding, predicate pushdown (including
merging two-table conjuncts into the JOIN's ON), and redundant projection
removal. Moved expression evaluation into `expr_eval.py`, so folding and
execution share one definition of SQL semantics.

**Stage 5: interpreter.** `executor.py` executes plan steps and
cross-checks against SQLite. The first cross-check design compared ordered
results exactly, but that is wrong when rows tie on the ORDER BY key, since
SQL leaves their order undefined. The final version only enforces order
between different sort keys. Added sample rows so joins, HAVING and empty
groups are exercised. Verified that the check catches injected interpreter
bugs.

**Stages 6+7: CLI and LLM.** Committed together because the CLI depends on
the new LLM interface. `--sql`, `-i`, `--list`, `--demo N`, run-all,
`--stub/--live/--show-plan/--verbose`. 10 stub questions, two of which fail
first (S010, S003) and are repaired. The live client reads the model from
`ANTHROPIC_MODEL` (default `claude-sonnet-5-5`), lets the SDK read the key
from the environment, never prints it, and falls back to stub mode without
a key. The repair loop has a maximum of 3 attempts and sends the full
structured history.

**Stage 8: visualization.** Rebuilt `visualize.py`: one self-contained HTML
file with repair attempts side by side, plans before and after
optimization, passes fired, trace, results, cross-check and timing bars.
Checked the rendering with a headless browser screenshot.

**Stage 9: tests.** 183 unittest tests at the end of this stage.
* *Bug found by boundary testing:* a 400-term AND chain, 300 nested
  parentheses or 500 NOTs crashed with Python's `RecursionError`. Fixed with
  a depth limit in the parser (P005: max 40 nested parentheses/function
  calls and 150 operator levels) and a safety net in `pipeline.py`.
* `tests/run_all.py` generates `docs/TEST_RESULTS.md` from the actual run.

**Stage 10: evaluation.** `evaluate.py`: 14 mutation operators over the
corpus, stage timings, and an optional live evaluation on 20 gold
questions. The first run reported one undetected mutant. Investigation
showed it was a bug in the *mutation operator* (a case-sensitive text
replace that did nothing on a lower-case query), not in the compiler. After
fixing the operator, all 544 mutants are rejected with the expected code.
The live evaluation was not run (no API key); its scoring logic is tested
with fake LLMs.

**Stage 11: documentation.** README, ARCHITECTURE (Mermaid), VIVA_NOTES,
this log, `.env.example`, updated `.gitignore` and `requirements.txt`.

**Stage 12: security check.** See the entry below.

### Security check (Stage 12), 2026-10-29
Scanned for API keys and secrets (Anthropic `sk-ant-...`, generic `sk-...`,
AWS, GitHub, Slack, Google keys, private-key blocks, and
`api_key/secret/password/token = "<long value>"` assignments) in:
* the working tree, including git-ignored files: no `.env` file exists
  (only `.env.example`, whose key value is empty);
* every commit on every ref and in the reflog (14 commits), plus
  unreachable/dangling objects and stashes (none).

Result: **no secret found.** The only pattern matches are the deliberate
placeholder `"placeholder-not-a-real-key"` in `tests/test_llm_client.py` and
the documentation placeholder `sk-ant-...` in the Phase 1 README. No file
that looks like a secret (`.env`, `*.pem`, `*.key`, ...) was ever
committed. In code, the key's value is only checked for presence
(`llm_client.api_key_available`) and is otherwise handled by the SDK.

### Not verified
* The live Anthropic API path (`LiveLLM`, live evaluation) has not been run
  against the real API: no key was available. Its prompt construction, mode
  selection and scoring logic are unit-tested offline.

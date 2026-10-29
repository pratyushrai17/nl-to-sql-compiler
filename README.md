# NL -> SQL Compiler

Translates English questions about a small library database into SQL, and
**checks that SQL with a hand-written compiler** before anything runs:

```
English question
  -> LLM writes candidate SQL                 (llm_client.py - live API or offline stub)
  -> lexer: characters -> tokens               (lexer.py)
  -> parser: tokens -> AST                     (parser.py, ast_nodes.py)
  -> semantic analysis against the schema      (semantic_analyzer.py, symbol_table.py)
       on any error: structured errors go back to the LLM, max 3 attempts (repair_loop.py)
  -> intermediate code: numbered query plan    (query_plan.py)
  -> optimizer: 3 passes                       (optimizer.py)
  -> interpreter runs the plan in Python       (executor.py, expr_eval.py)
  -> cross-check: SQLite runs the same SQL; results must match
```

The lexer, parser, AST, symbol table, semantic analyzer, planner, optimizer
and interpreter are all written from scratch. No parsing library (sqlparse,
sqlglot, ANTLR, PLY, Lark, ...) is used anywhere. The only third-party
package is the optional `anthropic` SDK for the live LLM.

**No API key is needed.** Every mode below works offline. Without a key the
LLM is replaced by a clearly labelled *stub* that knows 10 built-in
questions, and you can always type SQL directly with `--sql`.

## Setup

Requires Python 3 (developed and tested on Python 3.14.3, Windows 11; the `anthropic` SDK needs Python 3.10+).

```powershell
cd project2
python --version
# Optional, only for the live LLM mode:
pip install -r requirements.txt
```

Nothing else is needed: the database is an in-memory SQLite database
created on start-up (`db.py`).

## Running it (no API key)

### 1. Compile and run any SQL: `--sql` (the main demo)

```powershell
python main.py --sql "SELECT genre, COUNT(*) AS n FROM Books GROUP BY genre ORDER BY n DESC"
```

This prints every stage: [1] tokens, [2] the AST as a tree, [3] semantic
analysis, [4] the query plan, [5] which optimizer passes fired and the
optimized plan, [6] the interpreter's step-by-step row counts and result,
[7] the SQLite cross-check, then per-stage timings.

Broken SQL shows a structured error with a code, message and line/column,
and a `^` under the problem. The program does not crash:

```powershell
python main.py --sql "SELECT titel FROM Books"                     # S002 + "did you mean 'title'?"
python main.py --sql "SELECT title FROM Books WHERE title = 1984"  # S006 type mismatch
python main.py --sql "SELECT genre, author, COUNT(*) FROM Books GROUP BY genre"   # S010
python main.py --sql "SELECT title FROM Books WHERE genre = 'Fantasy"              # L002
python main.py --sql "DROP TABLE Books"                            # P002 only SELECT is supported
```

Exit code: 0 if the query is valid and the interpreter matches SQLite, 1 otherwise.

Some queries that show off the optimizer:

```powershell
python main.py --sql "SELECT title FROM Books WHERE 1 = 1 AND id > 2 * 3"
python main.py --sql "SELECT m.name, b.title FROM Members m JOIN Loans l ON m.id = l.member_id JOIN Books b ON l.book_id = b.id WHERE b.genre = 'Fantasy'"
python main.py --sql "SELECT * FROM Loans"
```

### 2. Built-in English questions (stub LLM)

```powershell
python main.py --list        # show the 10 built-in questions
python main.py --demo 3      # one question (3 and 4 fail validation first, then get repaired)
python main.py               # run all 10, then print a summary
```

Without a key a `*** STUB MODE ***` notice is always printed: the SQL for
English questions is pre-written, and every compiler stage after that is
real. A question outside the built-in set gets a message pointing to `--sql`.

### 3. Interactive mode

```powershell
python main.py -i
```

```
nl2sql> sql> SELECT title FROM Books WHERE id < 3       <- compiled directly
nl2sql> How many books are there in each genre?        <- sent to the (stub or live) LLM
nl2sql> :list   :demo 4   :help   :quit
```

### Options

| Option | Effect |
|--------|--------|
| `--stub` | force the offline stub LLM even if a key is set |
| `--live` | use the Anthropic API (falls back to the stub, with a notice, if no key is set) |
| `--show-plan` | for English questions, also show the unoptimized plan and every pass (always on for `--sql`) |
| `--verbose` | token line:column positions and per-step execution timings |

### Visualization

```powershell
python visualize.py          # writes demo_output.html and opens it
python visualize.py --no-open
```

`demo_output.html` is one self-contained file (no JavaScript, no external
files), generated offline. It shows the repair attempts side by side,
tokens, the AST tree, the plan before and after optimization, the passes
that fired, the interpreter trace, results, the cross-check and per-stage
timing bars.

### Tests and evaluation

```powershell
python -m unittest            # 188 tests, standard library only, no key needed
python -m tests.run_all       # same, and regenerates docs/TEST_RESULTS.md
python evaluate.py            # mutation test + stage timings -> docs/EVALUATION.md
```

## Optional: the live LLM

Set the key in the environment. The program reads it only from the
environment (never from a file), and never prints or logs it.

**Windows PowerShell (current window only):**

```powershell
$env:ANTHROPIC_API_KEY = "<your key>"
$env:ANTHROPIC_MODEL = "claude-sonnet-5-5"     # optional; this is the default
python main.py --live -i
```

To remove it again: `Remove-Item Env:ANTHROPIC_API_KEY`.
(To make it permanent for new windows: `setx ANTHROPIC_API_KEY "<your key>"`.)

**macOS / Linux:** `export ANTHROPIC_API_KEY=<your key>`

With a key, `python evaluate.py` also scores the live model on 20 questions
against gold SQL (validity, repairs, execution match, calls, estimated cost).
`.env.example` lists the variables. Do not put a real key in any file in
this repository.

## Schema and sample data

| Table   | Columns                          |
|---------|----------------------------------|
| Books   | id, title, author, genre         |
| Members | id, name, joined_on              |
| Loans   | id, book_id, member_id, due_date |

10 books, 5 members and 8 loans (`db.py`). The schema is defined once, in
`symbol_table.py`.

## Supported SQL

`SELECT [DISTINCT] ... FROM ... [INNER] JOIN ... ON ... WHERE ... GROUP BY ...
HAVING ... ORDER BY ... [ASC|DESC] LIMIT n` with `AND / OR / NOT` (correct
precedence), parentheses, `= != <> < > <= >=`, `+ - * /`, `[NOT] LIKE`,
`[NOT] IN (list)`, `[NOT] BETWEEN`, `COUNT/SUM/AVG/MIN/MAX` (including
`COUNT(DISTINCT x)`), and column and table aliases. Full BNF and all error
codes: [docs/GRAMMAR.md](docs/GRAMMAR.md).

Anything that is not a SELECT (`DROP`, `DELETE`, `UPDATE`, `INSERT`, ...) is
rejected by the parser and never executed. As a second safeguard, the SQLite
connection is read-only (`PRAGMA query_only`).

## Module map

| File | Responsibility |
|------|----------------|
| `lexer.py` | Character-by-character scanner -> tokens with line/column |
| `parser.py` | Recursive-descent parser -> AST; syntax errors |
| `ast_nodes.py` | AST node classes, tree helpers, AST -> SQL pretty-printer |
| `symbol_table.py` | Schema catalog, per-query scope, "did you mean" suggestions |
| `semantic_analyzer.py` | Name resolution, type checking, aggregate / GROUP BY / HAVING / ORDER BY rules |
| `errors.py` | Structured `CompileError` (code, message, line, col) |
| `query_plan.py` | Planner: AST -> numbered three-address-style plan |
| `optimizer.py` | Constant folding, predicate pushdown, redundant projection removal |
| `expr_eval.py` | Expression evaluation (three-valued logic, LIKE), shared by optimizer and interpreter |
| `executor.py` | Plan interpreter and SQLite cross-check |
| `pipeline.py` | Runs and times the stages for one SQL string |
| `llm_client.py` | Stub LLM, live Anthropic LLM, mode selection |
| `repair_loop.py` | Generate -> compile -> repair, max 3 attempts, structured history |
| `db.py` | In-memory read-only SQLite with sample data |
| `examples.py` | Built-in questions, valid-query corpus, gold NL->SQL pairs |
| `main.py` | CLI and live demo |
| `visualize.py` | Generates `demo_output.html` |
| `evaluate.py` | Mutation test, timings, optional live evaluation |
| `tests/` | `unittest` suite (`tests/run_all.py` writes the results file) |

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): diagram, data flow, module design
* [docs/GRAMMAR.md](docs/GRAMMAR.md): BNF, precedence, error codes, semantic rules
* [docs/VIVA_NOTES.md](docs/VIVA_NOTES.md): per-module explanations, algorithm walkthroughs, likely questions
* [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md): generated test results
* [docs/EVALUATION.md](docs/EVALUATION.md): generated evaluation results
* [DEVLOG.md](DEVLOG.md): development log

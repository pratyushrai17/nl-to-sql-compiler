# CLAUDE.md

Project-wide context for the NL→SQL compiler project ("project2").
This file is loaded automatically in every session, including future phases.

## Project Overview

This project builds a system that translates natural language questions into SQL,
and validates the generated SQL using classical compiler front-end techniques
(lexing, parsing, AST construction, semantic analysis) before execution — rather
than trusting LLM output directly.

**Pipeline:**

```
NL question
  → LLM generates candidate SQL
  → hand-written lexer tokenizes it
  → hand-written recursive-descent parser builds an AST
  → semantic analyzer validates the AST against a fixed schema (symbol table)
  → on error, the error is fed back to the LLM to regenerate (bounded retries)
  → once valid, the query runs against SQLite and results are shown
```

This is a **compiler design course project**. The compiler front-end is the point
of the project; the LLM is just the front-end's input source.

## HARD CONSTRAINT (all phases)

The lexer, parser, AST, symbol table, semantic analyzer, query planner,
optimizer and plan interpreter **must all be hand-written from scratch**.

Do **NOT** use `sqlparse`, `sqlglot`, ANTLR, PLY, Lark, or any other existing SQL
parsing / parser-generator library anywhere in this project. Doing so defeats the
purpose of a compiler design project and is not acceptable for grading.

The only third-party dependency permitted is the `anthropic` SDK (for the LLM
call). Everything else uses the Python standard library (`sqlite3`, etc.).

## Fixed Sample Schema — Library database

| Table   | Columns                                  |
|---------|------------------------------------------|
| Books   | id, title, author, genre                 |
| Members | id, name, joined_on                      |
| Loans   | id, book_id, member_id, due_date         |

The schema is fixed and hardcoded in `symbol_table.py`. It is the single source
of truth for semantic validation and for the schema description sent to the LLM.

## Scope

**In scope (across all phases):** `SELECT` statements only — `WHERE`, `INNER JOIN`,
`GROUP BY`, `HAVING`, `ORDER BY`, `DISTINCT`, `LIMIT`, `LIKE` / `IN` / `BETWEEN`,
aliases, and aggregates (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`). Full grammar:
`docs/GRAMMAR.md` (keep it in sync with `parser.py`).

**Out of scope entirely:** `INSERT`, `UPDATE`, `DELETE`, and all DDL.

## Phase Status

This project has 3 phases:

- **Phase 1 (DONE)** — Review 1: Problem Definition and System Design.
  Minimal working prototype demonstrating the pipeline end-to-end.
- **Phase 2** — Full core implementation of all modules.
- **Phase 3** — Complete integration, full testing, error handling, final polish.

**Current status: Phases 2+3 in progress** (being completed together).

The system **must be fully demoable offline (no API key)**: every compiler
stage, the `--sql` mode, the built-in stub examples (including the repair
path), the tests and the evaluation must all run without `ANTHROPIC_API_KEY`.
The live LLM path is optional.

## Project Structure

| File                   | Responsibility                                            |
|------------------------|-----------------------------------------------------------|
| `lexer.py`             | Hand-written scanner — SQL text → tokens with line/col    |
| `parser.py`            | Recursive-descent parser — tokens → AST                   |
| `ast_nodes.py`         | AST node classes, tree helpers, SQL pretty-printer        |
| `errors.py`            | Structured `CompileError` (code, message, line, col)      |
| `symbol_table.py`      | Hardcoded schema (tables / columns / types), query scope  |
| `semantic_analyzer.py` | Validates and annotates the AST against the symbol table  |
| `query_plan.py`        | Intermediate code: AST → numbered query plan              |
| `optimizer.py`         | Constant folding, predicate pushdown, projection removal  |
| `expr_eval.py`         | Shared expression semantics (3-valued logic, LIKE, ...)   |
| `executor.py`          | Plan interpreter + cross-check against SQLite             |
| `pipeline.py`          | Runs and times the stages for one SQL string              |
| `llm_client.py`        | Stub LLM (offline) and live Anthropic LLM                 |
| `repair_loop.py`       | Generate → compile → repair (max 3 attempts, history)     |
| `db.py`                | In-memory read-only SQLite with sample data               |
| `examples.py`          | Built-in questions, valid-query corpus, gold NL→SQL pairs |
| `main.py`              | CLI entry point; prints every pipeline stage clearly      |
| `visualize.py`         | Generates the self-contained `demo_output.html`           |
| `evaluate.py`          | Mutation test, timings, optional live evaluation          |
| `tests/`               | unittest suite; `tests/run_all.py` writes TEST_RESULTS.md |
| `docs/`                | GRAMMAR, ARCHITECTURE, VIVA_NOTES, TEST_RESULTS, EVALUATION |
| `README.md`            | How to run it, what each module does                      |

## Tech Details

- **Language:** Python (standard library only, plus the `anthropic` SDK).
- **LLM:** Anthropic API via the official `anthropic` Python SDK; the model is
  read from `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`). Optional: without a
  key the system falls back to the offline stub LLM automatically.
- **API key:** read from the `ANTHROPIC_API_KEY` environment variable. Never hardcode
  a key, and never commit one.
- **Database:** in-memory SQLite (`sqlite3`), seeded with sample rows at startup,
  then made read-only. SQLite is storage + a reference oracle; queries are
  executed by our own plan interpreter.
- **Tests:** `python -m unittest` (standard library; pytest is not installed).

## Academic Integrity

This is an **individual academic project**. All code must be written for this
project — no copied or downloaded implementations of lexers, parsers, or analyzers.

## Working Conventions

- Keep modules single-purpose and importable in isolation — each stage of the
  pipeline should be testable on its own.
- `main.py` must print each pipeline stage visibly (tokens, AST, validation
  result, final SQL, query results). This output *is* the live demo.
- Prefer clear, readable code over clever code; this is read by a grader.

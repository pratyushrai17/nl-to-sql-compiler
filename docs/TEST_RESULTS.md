# Test results

Generated automatically by `python -m tests.run_all` - do not edit by hand.

* Date: 2026-10-29
* Python 3.14.3, SQLite 3.50.4, Windows 11
* Framework: `unittest` (standard library; no extra install)
* No API key and no network access were used
* Run time: 1.29 s

## Summary: ALL PASSED

**183 tests: 183 passed, 0 failed, 0 errors, 0 skipped.**

Some tests loop over several inputs using `subTest`; each such loop counts as one test above.

## Per module

| Module | What it covers | Tests | Passed | Failed/Errors |
|--------|----------------|------:|-------:|--------------:|
| `test_lexer.py` | Lexer: tokens, positions, lexical errors (L001-L004) | 14 | 14 | 0 |
| `test_parser.py` | Parser: clauses, precedence, round-trip printing, syntax errors (P001-P004) | 24 | 24 | 0 |
| `test_semantic.py` | Semantic analyzer: resolution, types, aggregates, GROUP BY / HAVING / ORDER BY rules | 27 | 27 | 0 |
| `test_query_plan.py` | Planner: operator order, step numbering, star expansion | 6 | 6 | 0 |
| `test_optimizer.py` | Optimizer: constant folding, predicate pushdown, projection removal | 13 | 13 | 0 |
| `test_executor.py` | Interpreter: differential tests vs SQLite, operators, expression semantics | 13 | 13 | 0 |
| `test_valid_queries.py` | Valid queries: one test per corpus query (compile + run + match SQLite) | 52 | 52 | 0 |
| `test_boundaries.py` | Boundary cases: empty input, formatting, deep nesting, long chains | 11 | 11 | 0 |
| `test_repair_loop.py` | Repair loop with a fake LLM: error feedback, history, attempt bound | 4 | 4 | 0 |
| `test_llm_client.py` | LLM client without network: stub questions, mode selection, prompts | 9 | 9 | 0 |
| `test_cli.py` | main.py end to end (subprocess, no API key): --sql, --demo, --list, -i | 7 | 7 | 0 |
| `test_visualize.py` | visualize.py: self-contained HTML with every section | 3 | 3 | 0 |

## How to run

```
python -m unittest            # run all tests
python -m unittest -v         # with one line per test
python -m tests.run_all       # run all tests and regenerate this file
```

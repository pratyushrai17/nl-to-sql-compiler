# Evaluation

Generated automatically by `python evaluate.py` - every number below comes from that run. Re-run the script to refresh it.

* Date: 2026-10-29
* Python 3.14.3, SQLite 3.50.4, Windows 11
* Corpus: 52 valid queries (`examples.VALID_QUERIES`)

## 1. Mutation test (no API key needed)

Each operator injects one kind of fault into every corpus query it applies to. A mutant is *detected* if the compiler rejects it; *expected code* means the error reported includes the specific code the fault should trigger.

| Operator | Fault injected | Expected | Mutants | Detected | Rate | With expected code |
|----------|----------------|----------|--------:|---------:|-----:|-------------------:|
| `rename_column` | rename a column to one that does not exist | S002 | 48 | 48 | 100.0% | 48 (100.0%) |
| `rename_table` | rename the FROM table | S001 | 52 | 52 | 100.0% | 52 (100.0%) |
| `drop_group_by_column` | drop a selected column from GROUP BY | S010 | 8 | 8 | 100.0% | 8 (100.0%) |
| `aggregate_in_where` | add COUNT(*) > 0 to WHERE | S008 | 52 | 52 | 100.0% | 52 (100.0%) |
| `text_vs_number` | compare a TEXT column with 42 | S006 | 52 | 52 | 100.0% | 52 (100.0%) |
| `unqualify_join_column` | remove the qualifier of a column shared by joined tables | S003 | 8 | 8 | 100.0% | 8 (100.0%) |
| `having_without_group_by` | add HAVING to a query without GROUP BY | S011 | 44 | 44 | 100.0% | 44 (100.0%) |
| `sum_of_text` | add SUM(<text column>) | S007 | 52 | 52 | 100.0% | 52 (100.0%) |
| `alias_in_where` | use a SELECT alias in WHERE | S016 | 4 | 4 | 100.0% | 4 (100.0%) |
| `unknown_function` | call a function that is not supported | P004 | 52 | 52 | 100.0% | 52 (100.0%) |
| `drop_from_keyword` | delete the FROM keyword | P001 | 52 | 52 | 100.0% | 52 (100.0%) |
| `unterminated_string` | delete the closing quote of a string | L002 | 16 | 16 | 100.0% | 16 (100.0%) |
| `illegal_character` | insert a '#' character | L001 | 52 | 52 | 100.0% | 52 (100.0%) |
| `turn_into_delete` | replace the query with DELETE | P002 | 52 | 52 | 100.0% | 52 (100.0%) |
| **Total** | | | **544** | **544** | **100.0%** | **544 (100.0%)** |

No mutant was accepted by the compiler.

Limitation: every mutant here is invalid *by construction*, so this measures how reliably the checks fire, not whether the compiler can spot a query that is valid but answers the wrong question (that needs the live evaluation in section 3).

## 2. Stage timings (no API key needed)

Every corpus query was run through all stages 20 times (1040 runs per stage). Wall-clock milliseconds on this machine; sub-millisecond numbers are noisy.

| Stage | Runs | Average (ms) | Median (ms) | Max (ms) |
|-------|-----:|-------------:|------------:|---------:|
| Lexer | 1040 | 0.0348 | 0.0281 | 0.3756 |
| Parser | 1040 | 0.0376 | 0.0328 | 0.1679 |
| Semantic analysis | 1040 | 0.0200 | 0.0159 | 0.8372 |
| Planner | 1040 | 0.0170 | 0.0141 | 0.0934 |
| Optimizer | 1040 | 0.1362 | 0.1026 | 2.2835 |
| Interpreter | 1040 | 0.1009 | 0.0710 | 1.3460 |
| SQLite cross-check | 1040 | 0.0693 | 0.0556 | 2.1788 |

## 3. Live LLM evaluation

**Not run:** `ANTHROPIC_API_KEY` was not set when this report was generated, so no live LLM was called and no accuracy, call-count or cost numbers are reported. To run it, set the key and re-run `python evaluate.py`.

When run, it asks the live model the 20 questions in `examples.GOLD_QUESTIONS` and reports first-attempt validity, validity after repair, execution-match accuracy against the gold SQL, the number of LLM calls and an estimated cost.

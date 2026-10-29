# Viva notes

A study guide: what each module does, how the key algorithms work, step by
step, how to make common modifications, and likely questions with short
answers.

---

## 1. The big picture in one paragraph

An LLM turns an English question into SQL, but LLM output cannot be trusted.
So the SQL goes through a classical compiler front end: a **lexer** (characters
-> tokens), a **recursive-descent parser** (tokens -> abstract syntax tree)
and a **semantic analyzer** that checks the tree against a **symbol table**
(the schema). If any stage finds an error, the error, with its code and
line/column, goes back to the LLM to fix (up to 3 attempts). A valid query
is translated into **intermediate code** (a numbered query plan),
**optimized**, and **executed by our own interpreter**. Finally SQLite runs the
same SQL and we check that both answers agree.

---

## 2. Module by module

### lexer.py: lexical analysis
* **Job:** split the text into tokens: keywords, identifiers, numbers,
  strings, operators, punctuation. Each token records its line and column.
* **How:** a loop over characters (`Lexer.tokenize`). The first character
  decides the token class: a letter starts a word, a digit a number, `'` a
  string, and so on. This is a hand-coded finite automaton.
* **Details worth mentioning:** maximal munch for `<=`, `>=`, `<>`, `!=`;
  `''` inside a string is an escaped quote; `--` comments are skipped;
  keywords are case-insensitive (`select` -> token type `SELECT`).
* **Errors:** L001 unknown character, L002 unterminated string, L003
  unterminated quoted identifier, L004 malformed number (`12abc`).

### parser.py: syntax analysis
* **Job:** check the token sequence against the grammar
  ([GRAMMAR.md](GRAMMAR.md)) and build the AST.
* **How:** recursive descent, with one method per grammar rule. `expect(T)`
  consumes a token of type T or raises "Expected T but found Y" at that
  token's line and column.
* **Precedence** is encoded by which method calls which (section 3.1).
* **Errors:** P001 expected/found, P002 only SELECT is supported, P003
  unsupported feature (subquery, LEFT JOIN, UNION...), P004 unknown function,
  P005 nested too deeply.

### ast_nodes.py: the AST
* Dataclasses: expression nodes (`ColumnRef`, `Literal`, `BinaryOp`,
  `UnaryOp`, `LikeExpr`, `InExpr`, `BetweenExpr`, `AggregateCall`, `Star`)
  and clause nodes (`SelectStatement`, `SelectItem`, `TableRef`,
  `JoinClause`, `OrderByItem`).
* Positions (`line`, `col`) and analyzer annotations are excluded from `==`,
  so two parses of differently formatted SQL compare equal.
* `to_sql()` is a pretty-printer that adds parentheses only where precedence
  requires them. It is tested by a **round trip**: parse(print(parse(x)))
  == parse(x).

### symbol_table.py: symbol table
* **Global scope:** `SCHEMA`, a dictionary of table -> column -> type.
* **Query scope:** `Scope`, which maps the names a query uses for its tables
  (aliases) to real tables. `FROM Books AS b` binds `b` -> Books, and then
  `Books.title` is no longer visible (same rule as SQLite).
* `edit_distance` (Levenshtein, dynamic programming) powers "did you mean
  'title'?".

### semantic_analyzer.py: semantic analysis
* **Pass 1, scope:** every FROM/JOIN table must exist (S001); aliases must
  be unique (S005).
* **Pass 2, resolve + type-check:** one recursive function `check_expr`
  returns the type of each expression and checks the rules: unknown column
  (S002), ambiguous column (S003), unknown qualifier (S004), type mismatch
  (S006), wrong operand type (S007), aggregate in WHERE (S008), aggregate in
  ON or nested (S009), non-boolean condition (S013), `*` misuse (S015), alias
  outside ORDER BY (S016).
* **Pass 3, grouping rules:** the GROUP BY rule (S010), HAVING without
  GROUP BY (S011), HAVING with an ungrouped column (S012).
* **Pass 4, ORDER BY rules** (S014).
* It **annotates** the AST: each `ColumnRef` gets `qualifier` (which table)
  and `sql_type`.
* It **collects all errors** instead of stopping at the first.

### query_plan.py: intermediate code
* Translates the AST into a tree of relational operators. The tree is
  linearized into numbered steps (three-address style):
  ```
  t1 = SCAN Books
  t2 = FILTER t1 WHERE Books.genre = 'Dystopian'
  t3 = PROJECT t2 [Books.title]
  ```
* Operator order: SCAN/JOIN -> FILTER (WHERE) -> AGGREGATE (GROUP BY) ->
  HAVING -> SORT -> PROJECT -> DISTINCT -> LIMIT.

### optimizer.py: optimization
Three passes: constant folding, predicate pushdown, and redundant projection
removal (section 3.3). The output shows the plan before and after, and
which passes fired.

### expr_eval.py: expression semantics
SQL's three-valued logic (TRUE / FALSE / NULL), NULL propagation, SQLite's
integer division, IN-list rules, and a LIKE matcher. The interpreter and
constant folding both use it.

### executor.py: interpreter
Executes the numbered steps in order, keeping each step's result (a list of
row dictionaries) under its number, like registers for temporaries. It uses
a nested-loop join, hash grouping and a stable multi-key sort. Then
`cross_check` compares the result with SQLite.

### repair_loop.py and llm_client.py: the LLM side
`run_repair_loop(question, llm)`: generate SQL, compile it, and on error
send back the **history** (attempt number, SQL, error code, message,
line/col). It stops on success or after 3 attempts. `StubLLM` (offline,
pre-written answers) and `LiveLLM` (Anthropic API) share the same
interface.

---

## 3. Algorithm walkthroughs

### 3.1 Recursive descent and precedence: a worked example

Input: `WHERE a = 1 OR b = 2 AND c = 3`

The methods call each other from lowest to highest precedence:

```
parse_expr        handles OR      calls parse_and for each side
 parse_and        handles AND     calls parse_not
  parse_not       handles NOT     calls parse_predicate
   parse_predicate  handles = < LIKE IN BETWEEN   calls parse_additive
    parse_additive  handles + -   calls parse_term
     parse_term     handles * /   calls parse_unary
      parse_unary   handles -x    calls parse_primary
       parse_primary  literal / column / aggregate / ( expr )
```

Trace:
1. `parse_expr` calls `parse_and`, which calls down to `parse_predicate`.
   That parses `a = 1` and returns.
2. Back in `parse_and`: the next token is `OR`, not `AND`, so it returns
   `a = 1`.
3. `parse_expr` sees `OR`, consumes it, and calls `parse_and` again for the
   right side.
4. This `parse_and` parses `b = 2`, sees `AND`, parses `c = 3`, and builds
   `AND(b = 2, c = 3)`.
5. `parse_expr` builds `OR(a = 1, AND(b = 2, c = 3))`.

**Why it works:** AND is handled *deeper* in the recursion, so AND nodes
end up *lower* in the tree, and lower means "binds tighter". The `while`
loops make operators left-associative: `a - b - c` = `(a - b) - c`.
Parentheses work because `parse_primary` sees `(` and restarts at the
lowest level, `parse_expr`.

**BETWEEN subtlety:** `x BETWEEN 1 AND 5 AND y = 2`. The bounds are parsed
with `parse_additive`, which never consumes AND. So BETWEEN claims the first
AND, and the second AND is the logical one.

**Error example:** `SELECT title Books`. `parse_select_item` parses `title`
and then takes `Books` as an alias (AS is optional). `expect("FROM")` then
sees the end of input and raises *P001: Expected keyword FROM but found end
of input* at line 1, col 19.

### 3.2 The GROUP BY rule

**Rule:** in an *aggregated* query (one with GROUP BY, or any aggregate in
SELECT / HAVING / ORDER BY), every column used *outside* an aggregate must
be a GROUP BY column.

**Why:** after grouping, one output row stands for a whole group. A GROUP BY
column has one value per group. Any other column has many values per group,
so `SELECT genre, author, COUNT(*) ... GROUP BY genre` is meaningless: which
author should the Fantasy row show?

**Algorithm (`check_grouping`):**
1. Decide whether the query is aggregated.
2. `grouped` = the set of resolved keys of the GROUP BY columns, e.g.
   `{"Books.genre"}`. Resolved keys make `genre` and `b.genre` compare equal.
3. For each SELECT item, collect the column references *not inside an
   aggregate* (`column_refs(expr, inside_aggregates=False)`). Any column not
   in `grouped` is an S010 error.
4. Do the same for HAVING (S012) and ORDER BY (S010; ORDER BY may also use a
   SELECT alias).
5. `SELECT *` in an aggregated query is S010.

| Query | Result |
|-------|--------|
| `SELECT genre, COUNT(*) FROM Books GROUP BY genre` | valid |
| `SELECT genre, author, COUNT(*) FROM Books GROUP BY genre` | S010 on `author` |
| `SELECT genre, COUNT(*) FROM Books` | S010 (aggregate but no GROUP BY) |
| `SELECT genre, MAX(author) FROM Books GROUP BY genre` | valid (author is inside an aggregate) |
| `... GROUP BY genre HAVING author = 'x'` | S012 |
| `SELECT genre FROM Books WHERE COUNT(*) > 1 GROUP BY genre` | S008 (WHERE runs before grouping; use HAVING) |

### 3.3 The optimizer passes

**Pass 1: constant folding** (`fold`, a bottom-up rewrite)
* First fold the children, then the node itself.
* If a sub-expression has no columns (`is_constant`), evaluate it now with
  `expr_eval.evaluate` and replace it with a literal: `2 * 3` becomes `6`,
  and `1 = 1` becomes `TRUE`.
* Boolean simplification: `TRUE AND x` -> `x`, `FALSE AND x` -> `FALSE`,
  `x OR FALSE` -> `x`, `TRUE OR x` -> `TRUE`, `NOT NOT x` -> `x`. All of
  these are valid under three-valued logic.
* If a FILTER/HAVING condition folds to TRUE, the whole step is deleted.
* `1 / 0` is *not* folded. It evaluates to NULL, and the grammar has no NULL
  literal to replace it with.

Example: `WHERE 1 = 1 AND id > 2 * 3` becomes `WHERE id > 6`.

**Pass 2: predicate pushdown**
* Look for a FILTER directly above a JOIN.
* Split its condition into **conjuncts** (the parts joined by AND).
* For each conjunct, find which tables it uses (`tables_used`):
  * only tables from the left input -> push it into the left side, and so
    on recursively until it sits just above that table's SCAN;
  * only tables from the right input -> push it into the right side;
  * tables from both sides -> add it to that JOIN's ON condition.
* Conjuncts with no columns stay where they are. If every conjunct moved,
  the original FILTER disappears.
* **Benefit:** a nested-loop join costs |left| x |right| comparisons.
  Filtering first makes both inputs smaller.
* **Why it is correct here:** all joins are INNER joins, and for an inner
  join, filtering before or after joining gives the same rows. (For a LEFT
  JOIN, pushing a condition on the right-hand table would be wrong.)

Example:
```
before:                                   after:
t1 = SCAN Members AS m                    t1 = SCAN Members AS m
t2 = SCAN Loans AS l                      t2 = SCAN Loans AS l
t3 = JOIN t1, t2 ON m.id = l.member_id    t3 = JOIN t1, t2 ON m.id = l.member_id
t4 = SCAN Books AS b                      t4 = SCAN Books AS b
t5 = JOIN t3, t4 ON l.book_id = b.id      t5 = FILTER t4 WHERE b.genre = 'Fantasy'
t6 = FILTER t5 WHERE b.genre = 'Fantasy'  t6 = JOIN t3, t5 ON l.book_id = b.id
t7 = PROJECT t6 [m.name, b.title]         t7 = PROJECT t6 [m.name, b.title]
```

**Pass 3: redundant projection removal**
* A PROJECT that outputs exactly its input's columns, in the same order and
  with the same names, does nothing. The typical case is `SELECT *` from one
  table: `t2 = PROJECT t1 [all columns]` is removed.

**Is the optimizer correct?** Every test query runs both the unoptimized and
the optimized plan, and both must match SQLite
(`tests/test_executor.py`).

### 3.4 How the cross-check compares results
* No ORDER BY: SQL does not define row order, so the rows are compared as
  multisets (a `Counter` of rows).
* With ORDER BY: rows must come in the same order. However, rows that tie on
  every ORDER BY key may be in either order, because SQL does not define
  their order either. If LIMIT cuts through a group of tied rows, which of
  those rows are kept is also undefined, so that group is compared only by
  size.
* We checked that this is a real test: deliberately breaking the
  interpreter (wrong string sort, naive LIKE, float division) made the
  cross-check fail on 7, 4 and 1 corpus queries respectively.

---

## 4. How to make common modifications (practice these)

| Change | Where | Steps |
|--------|-------|-------|
| Add a keyword, e.g. `OFFSET` | `lexer.py` | Add it to `KEYWORDS` (remove it from `UNSUPPORTED_KEYWORDS`). Parse it in `parse_select_stmt` after LIMIT. Add a field to `SelectStatement`, a LIMIT step change in `query_plan.py`, and slicing in `executor.py`. Update GRAMMAR.md. |
| Add an operator, e.g. `%` (modulo) | `lexer.py`, `parser.py`, `expr_eval.py` | Add `"%": "PERCENT"` to `SINGLE_CHAR_TOKENS`. Add `"PERCENT"` to the token tuple in `parse_term`. Add `"%"` to `ARITHMETIC_OPS` and to `_BINARY_PREC` (at the `*` level) in `ast_nodes.py`. Handle it in `expr_eval.arithmetic`. |
| Add a semantic rule | `semantic_analyzer.py` | Add a check in `check_expr` (per expression) or a pass method called from `run()`. Report with `self.error("S017", message, node)`. Add the code to GRAMMAR.md and a test in `tests/test_semantic.py`. |
| Add a table or column | `symbol_table.py` + `db.py` | Add it to `SCHEMA`, then add a `CREATE TABLE` and sample rows in `db.py`. Everything else (validation, LLM prompt, `SELECT *`) picks it up automatically. |
| Add an optimizer pass | `optimizer.py` | Write `def my_pass(root): ... return root, notes` as a recursive rewrite, and append it to `PASSES`. The differential tests will catch any change in results. |
| Add a built-in demo question | `examples.py` | Append to `BUILTIN_QUESTIONS` with `responses` (a list; put an invalid first response to demo a repair). |
| Change the attempt limit | `repair_loop.py` | `MAX_ATTEMPTS`. |

After any change, run `python -m unittest`.

---

## 5. Likely viva questions and short answers

**Q: Why not just run the LLM's SQL directly?**
A: LLM output can reference columns that do not exist, mix up GROUP BY, or
even be a destructive statement. Our compiler rejects all of these before
execution, with precise errors the LLM can act on.

**Q: What is the difference between the lexer and the parser?**
A: The lexer recognizes *words* (a regular language, so a finite
automaton is enough). The parser recognizes *structure*, which needs a
context-free grammar because of nesting, such as parentheses.

**Q: Why recursive descent? Is the grammar LL(1)?**
A: It is simple, hand-writable and gives precise error messages. The grammar
is essentially LL(1): one token of lookahead decides each choice. There are
a few spots where we peek 2-3 tokens: `b.*` versus `b.col`, a function
call `NAME(` versus a column, and `NOT LIKE` versus a prefix `NOT`.

**Q: How do you avoid left recursion?**
A: Rules like `expr ::= expr OR term` would recurse forever in recursive
descent. We use iteration instead (`and_expr { OR and_expr }`): a `while`
loop that builds a left-leaning tree.

**Q: How is operator precedence implemented?**
A: Through the chain of parse methods (section 3.1). There is no
precedence table in the parser; `ast_nodes.py` has one only for printing.

**Q: What does the symbol table store?**
A: The catalog: tables, columns and types. Per query, it also stores the
scope: which aliases refer to which tables.

**Q: How do you detect an ambiguous column?**
A: We look up an unqualified name in every table in scope. Found in more
than one table means S003, and the message suggests the qualified names.

**Q: What type checks do you do?**
A: Every expression gets a type (INTEGER, REAL, TEXT or BOOLEAN). For
example, `title = 1984` is S006, `title + 1` is S007, `SUM(title)` is S007,
and `WHERE genre` (not a condition) is S013.

**Q: Why does the lexer stop at the first error but the semantic analyzer
collects all of them?**
A: After a lexical or syntax error, everything that follows is unreliable
(we would report garbage). Semantic errors are independent of each other,
so reporting all of them saves repair rounds.

**Q: What is your intermediate representation?**
A: A logical query plan, printed as numbered three-address-style steps
(`t3 = JOIN t1, t2 ON ...`). Each step has at most two inputs, and each
input is an earlier temporary.

**Q: Why does SORT come before PROJECT?**
A: So that ORDER BY can use columns that are not selected (`SELECT title
... ORDER BY id`). With DISTINCT this is still correct, because ORDER BY
terms must then be in the SELECT list, and removing duplicates from a
sorted list keeps it sorted.

**Q: Explain predicate pushdown. When would it be wrong?**
A: Move filters below joins so the joins process fewer rows (section 3.3).
It would be wrong for an outer join on the side that gets NULL-padded. We
only support INNER JOIN.

**Q: Can constant folding change a query's result?**
A: No. It uses exactly the evaluator the interpreter uses, and every test
runs both plans and compares them.

**Q: What is three-valued logic and where does NULL come from?**
A: Conditions can be TRUE, FALSE or NULL (unknown). A row is kept only if
its condition is TRUE. Our grammar has no NULL literal, but NULL appears at
run time, for example `MAX(id)` over no rows, or `x / 0`.

**Q: How do you know your interpreter is correct?**
A: Differential testing. SQLite runs the same SQL, and the results must
match. 52 corpus queries plus all demos are checked this way. Injected bugs
are caught.

**Q: How does the repair loop work and why limit it to 3 attempts?**
A: On failure, we send the LLM the question plus every failed attempt with
its error codes, messages and positions. The limit bounds cost and latency.
If the model cannot fix the query in 3 tries, more tries rarely help.

**Q: How does the demo work without an API key?**
A: `StubLLM` has pre-written SQL for 10 questions, two of which are wrong on
purpose, to show repairs. A STUB MODE notice is always printed. `--sql`
skips the LLM entirely, so any query can be typed.

**Q: How is the API key protected?**
A: It is read only from the environment, by the Anthropic SDK. Our code
never prints, logs or stores it, `.env` is git-ignored, and the git history
was checked for secrets.

**Q: How do you prevent destructive SQL?**
A: (1) The parser only accepts SELECT (P002). (2) Only validated SQL reaches
SQLite. (3) `run_query` refuses non-SELECT text. (4) The connection is
read-only (`PRAGMA query_only`).

**Q: What happens with a 1000-term WHERE clause or 300 nested parentheses?**
A: The parser tracks nesting depth and returns error P005 instead of
letting Python run out of stack. This was found by the boundary tests.

**Q: What are the limitations?**
A: No subqueries, outer joins, NULL literal / IS NULL, or scalar functions.
The joins are nested-loop (fine for small tables). The mutation test only
uses mutants that are invalid by construction, so it does not measure
whether a *valid* query answers the *right* question (only the live
evaluation, which needs a key, measures that).

**Q: What is the time complexity?**
A: Lexing and parsing are linear in the input length. Semantic analysis is
linear in AST size times the number of tables. Nested-loop JOIN is
O(|L| x |R|). Grouping is O(n) expected time with hashing. SORT is
O(n log n).

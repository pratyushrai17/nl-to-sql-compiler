# Grammar of the supported SQL subset

This is the grammar implemented by the hand-written recursive-descent parser
in `parser.py`. Each non-terminal below corresponds to one `parse_<rule>`
method. **Keep this file in sync with `parser.py`.**

Only `SELECT` is accepted. Any other statement (`DROP`, `DELETE`, `UPDATE`,
`INSERT`, `CREATE`, `ALTER`, ...) is rejected by the parser with error
**P002 "Only SELECT is supported"**, and is therefore never executed.

## Notation

`::=` defines a rule, `|` separates alternatives, `[ x ]` means optional,
`{ x }` means zero or more repetitions, `"x"` is a literal token, and
UPPER-CASE words are keywords (matched case-insensitively).

## Statement level

```bnf
query          ::= select_stmt [ ";" ] EOF

select_stmt    ::= SELECT [ DISTINCT ] select_list
                   FROM table_ref { join }
                   [ WHERE expr ]
                   [ GROUP BY column_ref { "," column_ref } ]
                   [ HAVING expr ]
                   [ ORDER BY order_item { "," order_item } ]
                   [ LIMIT INTEGER ]

select_list    ::= select_item { "," select_item }
select_item    ::= "*"
                 | IDENT "." "*"
                 | expr [ [ AS ] IDENT ]

table_ref      ::= IDENT [ [ AS ] IDENT ]
join           ::= [ INNER ] JOIN table_ref ON expr
order_item     ::= expr [ ASC | DESC ]
column_ref     ::= IDENT [ "." IDENT ]
```

## Expressions (lowest precedence first)

```bnf
expr           ::= and_expr { OR and_expr }
and_expr       ::= not_expr { AND not_expr }
not_expr       ::= NOT not_expr
                 | predicate
predicate      ::= additive [ comp_op additive
                            | [ NOT ] LIKE additive
                            | [ NOT ] IN "(" expr { "," expr } ")"
                            | [ NOT ] BETWEEN additive AND additive ]
comp_op        ::= "=" | "!=" | "<>" | "<" | ">" | "<=" | ">="
additive       ::= term { ( "+" | "-" ) term }
term           ::= unary { ( "*" | "/" ) unary }
unary          ::= "-" unary
                 | primary
primary        ::= NUMBER | STRING | TRUE | FALSE
                 | aggregate
                 | column_ref
                 | "(" expr ")"
aggregate      ::= agg_name "(" ( "*" | [ DISTINCT ] expr ) ")"
agg_name       ::= COUNT | SUM | AVG | MIN | MAX
```

### Precedence and associativity

| Level | Operators                                   | Associativity   |
|-------|---------------------------------------------|-----------------|
| 1 (lowest) | `OR`                                   | left            |
| 2     | `AND`                                       | left            |
| 3     | `NOT` (prefix)                              | right (nests)   |
| 4     | `= != <> < > <= >=`, `LIKE`, `IN`, `BETWEEN` | non-associative |
| 5     | `+ -`                                       | left            |
| 6     | `* /`                                       | left            |
| 7     | unary `-`                                   | right (nests)   |
| 8 (highest) | literals, columns, aggregates, `( expr )` | -             |

So `a OR b AND c` parses as `a OR (b AND c)`, and `NOT a = 1 AND b = 2`
parses as `(NOT (a = 1)) AND (b = 2)`. Parentheses override precedence.

**BETWEEN and AND:** the bounds of `BETWEEN` are parsed at the `additive`
level, so in `x BETWEEN 1 AND 5 AND y = 2` the first `AND` belongs to
`BETWEEN` and the second is the logical `AND`.

## Lexical rules (`lexer.py`)

```bnf
IDENT          ::= ( letter | "_" ) { letter | digit | "_" }    (not a keyword)
                 | '"' { any char except '"' } '"'               (quoted identifier)
NUMBER         ::= digit { digit } [ "." digit { digit } ]
STRING         ::= "'" { any char except "'" | "''" } "'"      ('' = escaped quote)
comment        ::= "--" { any char except newline }            (skipped)
```

Letters and digits are ASCII. Keywords and identifiers are
case-insensitive; table and column names are mapped to their schema
spelling by the semantic analyzer (`books.TITLE` -> `Books.title`).

## Deliberately unsupported

Each of these is recognized and rejected with a clear message (P003)
rather than a confusing generic error: subqueries, `LEFT`/`RIGHT`/`FULL`/
`CROSS`/`NATURAL` joins, comma-separated `FROM` lists, `UNION`/`INTERSECT`/
`EXCEPT`, `WITH`, `LIMIT ... OFFSET`. Function calls other than the five
aggregates give P004. `NULL` and `IS NULL` are not part of the grammar.

## Error codes

Every error has a code, a message and a 1-based line/column (see
`errors.py`).

| Code | Stage    | Meaning |
|------|----------|---------|
| L001 | lexical  | Unexpected / unknown character |
| L002 | lexical  | Unterminated string literal |
| L003 | lexical  | Unterminated quoted identifier |
| L004 | lexical  | Malformed number (digit followed by a letter, e.g. `12abc`) |
| P001 | syntax   | Expected X but found Y |
| P002 | syntax   | Only SELECT is supported (non-SELECT statement) |
| P003 | syntax   | Unsupported SQL feature (subquery, outer join, UNION, ...) |
| P004 | syntax   | Unknown function (only COUNT/SUM/AVG/MIN/MAX) |
| P005 | syntax   | Expression nested too deeply (over 40 nested parentheses/function calls, or 150 operator levels) |
| S001 | semantic | Undefined table |
| S002 | semantic | Undefined column (with a "did you mean" hint) |
| S003 | semantic | Ambiguous column (exists in more than one joined table) |
| S004 | semantic | Qualifier is not a table or alias in FROM/JOIN |
| S005 | semantic | Duplicate table name or alias |
| S006 | semantic | Type mismatch in a comparison, IN list or BETWEEN (e.g. TEXT vs INTEGER) |
| S007 | semantic | Wrong operand type: arithmetic on text, LIKE on a number, SUM/AVG of text |
| S008 | semantic | Aggregate function in WHERE (use HAVING) |
| S009 | semantic | Aggregate in JOIN ... ON, or an aggregate nested inside another |
| S010 | semantic | GROUP BY rule: column used outside an aggregate but not in GROUP BY (SELECT or ORDER BY), or SELECT * in an aggregated query |
| S011 | semantic | HAVING without GROUP BY |
| S012 | semantic | HAVING uses a column that is not in GROUP BY |
| S013 | semantic | A condition was expected (WHERE / ON / HAVING / AND / OR / NOT operand is not true/false) |
| S014 | semantic | ORDER BY misuse: position number, or with DISTINCT a term not in the SELECT list |
| S015 | semantic | `*` used anywhere except `SELECT *` / `COUNT(*)` |
| S016 | semantic | SELECT alias used in WHERE / GROUP BY / HAVING (aliases are visible only in ORDER BY) |

## Semantic rules in brief

* **Name resolution:** every column must exist in exactly one table in scope
  (or be qualified). Once a table has an alias, only the alias can qualify it.
* **Types:** INTEGER and REAL compare with each other; TEXT compares only
  with TEXT. Conditions (BOOLEAN) are produced by comparisons, LIKE, IN,
  BETWEEN, AND, OR, NOT, TRUE and FALSE.
* **GROUP BY rule:** a query is *aggregated* if it has GROUP BY or an
  aggregate in SELECT, HAVING or ORDER BY. In an aggregated query, every
  column outside an aggregate (in SELECT, HAVING and ORDER BY) must be a
  GROUP BY column.
* **Aliases:** a SELECT alias can be used in ORDER BY only.

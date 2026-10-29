"""
symbol_table.py

The symbol table has two levels, like scopes in a programming language:

  1. The *catalog* (global scope): the fixed Library schema - which tables
     exist, which columns each has, and each column's type. This is the
     single source of truth for semantic validation and for the schema text
     sent to the LLM.

  2. A *query scope* (class Scope): the tables one particular query brings
     into scope with FROM / JOIN, keyed by the name the query uses for them
     (its alias if it has one, otherwise the table name). Column references
     are resolved against this scope.

Identifiers are case-insensitive, as in SQLite: `books`, `BOOKS` and
`Books` all name the same table. Lookups return the canonical spelling.
"""

# table_name -> {column_name: sql_type}
SCHEMA = {
    "Books": {
        "id": "INTEGER",
        "title": "TEXT",
        "author": "TEXT",
        "genre": "TEXT",
    },
    "Members": {
        "id": "INTEGER",
        "name": "TEXT",
        "joined_on": "TEXT",
    },
    "Loans": {
        "id": "INTEGER",
        "book_id": "INTEGER",
        "member_id": "INTEGER",
        "due_date": "TEXT",
    },
}

# Aggregate functions recognized by the parser and the semantic analyzer.
AGGREGATE_FUNCTIONS = {"COUNT", "SUM", "AVG", "MIN", "MAX"}

NUMERIC_TYPES = {"INTEGER", "REAL"}


# -- catalog lookups ------------------------------------------------------------

def canonical_table(name):
    """The schema spelling of a table name (case-insensitive), or None."""
    for table in SCHEMA:
        if table.lower() == name.lower():
            return table
    return None


def canonical_column(table, name):
    """The schema spelling of a column of `table` (case-insensitive), or None."""
    for column in SCHEMA.get(table, {}):
        if column.lower() == name.lower():
            return column
    return None


def table_exists(name):
    return canonical_table(name) is not None


def column_exists(table, name):
    table = canonical_table(table)
    return table is not None and canonical_column(table, name) is not None


def get_columns(table):
    return list(SCHEMA.get(table, {}).keys())


def column_type(table, column):
    return SCHEMA[table][column]


def schema_description():
    """Schema text included in the LLM prompt, so it uses real names."""
    lines = []
    for table, columns in SCHEMA.items():
        col_list = ", ".join(f"{name} ({sql_type})" for name, sql_type in columns.items())
        lines.append(f"- {table}({col_list})")
    return "\n".join(lines)


# -- "did you mean" suggestions ------------------------------------------------------

def edit_distance(a, b):
    """Levenshtein distance (insertions, deletions, substitutions) between
    two strings, by dynamic programming over a (len(a)+1) x (len(b)+1) grid.
    Only the previous row is kept, so memory is O(len(b))."""
    a, b = a.lower(), b.lower()
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,                  # delete ca
                current[j - 1] + 1,               # insert cb
                previous[j - 1] + (ca != cb),     # substitute (free if equal)
            ))
        previous = current
    return previous[-1]


def closest_match(name, candidates, max_distance=2):
    """The candidate closest to `name` if it is within `max_distance`
    edits, else None. Used for "did you mean 'title'?" hints."""
    best, best_distance = None, max_distance + 1
    for candidate in candidates:
        distance = edit_distance(name, candidate)
        if distance < best_distance:
            best, best_distance = candidate, distance
    return best


# -- per-query scope --------------------------------------------------------------

class Scope:
    """The tables visible inside one SELECT statement.

    bindings: list of (binding_name, table_name) in FROM/JOIN order, e.g.
              [("b", "Books"), ("l", "Loans")]
    """

    def __init__(self):
        self.bindings = []

    def add(self, binding, table):
        self.bindings.append((binding, table))

    def lookup_binding(self, name):
        """Resolve a qualifier like `b` or `books` -> (binding, table), or None."""
        for binding, table in self.bindings:
            if binding.lower() == name.lower():
                return binding, table
        return None

    def tables_with_column(self, column):
        """Every (binding, table, canonical_column) in scope that has `column`."""
        found = []
        for binding, table in self.bindings:
            canonical = canonical_column(table, column)
            if canonical is not None:
                found.append((binding, table, canonical))
        return found

    def binding_names(self):
        return [binding for binding, _ in self.bindings]

    def __repr__(self):
        return "Scope(" + ", ".join(f"{b}->{t}" for b, t in self.bindings) + ")"

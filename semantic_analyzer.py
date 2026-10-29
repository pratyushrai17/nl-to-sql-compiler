"""
semantic_analyzer.py

Semantic analysis: checks that a syntactically valid SelectStatement also
*means* something against the Library schema, and decorates the AST with
what it learns (which table each column belongs to).

Passes, in order:
  1. Build the query scope from FROM / JOIN (symbol_table.Scope).
       S001 undefined table, S005 duplicate table alias
  2. Resolve every column reference against that scope.
       S002 undefined column, S003 ambiguous column, S004 unknown qualifier
  3. The GROUP BY rule (see check_grouping).
       S010 column used outside an aggregate but not listed in GROUP BY

analyze() returns a list of SemanticError (empty = valid). All errors are
collected rather than stopping at the first, so the LLM can fix them all in
one repair attempt.
"""

from ast_nodes import (
    AggregateCall, ColumnRef, SelectStatement, Star, column_refs, walk,
)
from errors import SemanticError
from symbol_table import (
    Scope, canonical_table, closest_match, column_type, get_columns, SCHEMA,
)


class Analyzer:
    def __init__(self, stmt: SelectStatement):
        self.stmt = stmt
        self.scope = Scope()
        self.errors = []

    def error(self, code, message, node=None):
        line = getattr(node, "line", None) or None
        col = getattr(node, "col", None) or None
        self.errors.append(SemanticError(code, message, line, col))

    # -- driver ----------------------------------------------------------------

    def run(self):
        self.build_scope()
        if self.errors:
            # If a table does not exist, resolving columns against it would only
            # produce a cascade of confusing follow-on errors - stop here.
            return self.errors

        stmt = self.stmt
        for item in stmt.select_items:
            self.check_select_item(item)
        for join in stmt.joins:
            self.resolve_all(join.on)
        if stmt.where is not None:
            self.resolve_all(stmt.where)
        for col in stmt.group_by:
            self.resolve_column(col)
        if stmt.having is not None:
            self.resolve_all(stmt.having)
        for item in stmt.order_by:
            self.resolve_all(item.expr)

        if not self.errors:
            self.check_grouping()
        return self.errors

    # -- pass 1: scope ------------------------------------------------------------

    def build_scope(self):
        for ref in self.stmt.tables():
            table = canonical_table(ref.name)
            if table is None:
                hint = closest_match(ref.name, SCHEMA.keys())
                suggestion = f" Did you mean '{hint}'?" if hint else ""
                self.error("S001",
                           f"Table '{ref.name}' is not defined in the schema "
                           f"(tables: {', '.join(SCHEMA)}).{suggestion}", ref)
                continue
            ref.name = table   # canonical spelling, e.g. books -> Books
            binding = ref.binding
            if self.scope.lookup_binding(binding):
                self.error("S005",
                           f"Table name or alias '{binding}' is used more than once in "
                           f"FROM/JOIN; give each table a distinct alias", ref)
                continue
            self.scope.add(binding, table)

    # -- pass 2: name resolution ------------------------------------------------------

    def check_select_item(self, item):
        if isinstance(item.expr, Star):
            star = item.expr
            if star.table is not None and self.scope.lookup_binding(star.table) is None:
                self.error("S004",
                           f"'{star.table}.*' refers to '{star.table}', which is not a "
                           f"table or alias in FROM/JOIN ({', '.join(self.scope.binding_names())})",
                           star)
            return
        self.resolve_all(item.expr)

    def resolve_all(self, expr):
        for node in walk(expr):
            if isinstance(node, ColumnRef):
                self.resolve_column(node)

    def resolve_column(self, col: ColumnRef):
        """Find the table a column belongs to and annotate the node with it."""
        if col.table is not None:
            found = self.scope.lookup_binding(col.table)
            if found is None:
                self.error("S004",
                           f"'{col.table}.{col.name}' uses '{col.table}', which is not a table "
                           f"or alias in FROM/JOIN ({', '.join(self.scope.binding_names())})",
                           col)
                return
            binding, table = found
            matches = [m for m in self.scope.tables_with_column(col.name) if m[0] == binding]
            if not matches:
                self.report_unknown_column(col, [table])
                return
        else:
            matches = self.scope.tables_with_column(col.name)
            if not matches:
                self.report_unknown_column(col, [t for _, t in self.scope.bindings])
                return
            if len(matches) > 1:
                options = " or ".join(f"'{b}.{c}'" for b, _, c in matches)
                self.error("S003",
                           f"Column '{col.name}' is ambiguous: it exists in more than one "
                           f"table in scope; qualify it as {options}", col)
                return

        binding, table, canonical = matches[0]
        col.name = canonical
        col.qualifier = binding
        col.sql_type = column_type(table, canonical)

    def report_unknown_column(self, col, tables):
        known = sorted({c for t in tables for c in get_columns(t)})
        hint = closest_match(col.name, known)
        suggestion = f" Did you mean '{hint}'?" if hint else ""
        where = f"table '{tables[0]}'" if len(tables) == 1 else \
            f"any table in scope ({', '.join(tables)})"
        self.error("S002",
                   f"Column '{col.name}' does not exist in {where}.{suggestion} "
                   f"(known columns: {', '.join(known)})", col)

    # -- pass 3: GROUP BY rule ---------------------------------------------------------

    def check_grouping(self):
        """In an aggregated query (one with GROUP BY or any aggregate in the
        SELECT list), each output row stands for a whole *group* of input
        rows. A column outside an aggregate has one value per group only if
        it is a GROUP BY column; any other column would be ambiguous."""
        stmt = self.stmt
        has_aggregate = any(isinstance(n, AggregateCall)
                            for item in stmt.select_items for n in walk(item.expr))
        if not stmt.group_by and not has_aggregate:
            return

        grouped = {c.key() for c in stmt.group_by}
        for item in stmt.select_items:
            if isinstance(item.expr, Star):
                self.error("S010", "SELECT * cannot be used in an aggregated query; list the "
                                   "GROUP BY columns and aggregates explicitly", item.expr)
                continue
            for col in column_refs(item.expr, inside_aggregates=False):
                if col.key() not in grouped:
                    self.error("S010",
                               f"Column '{col}' is selected alongside an aggregate but is not "
                               f"in GROUP BY; add it to GROUP BY or wrap it in an aggregate "
                               f"such as COUNT/MIN/MAX", col)


def analyze(stmt: SelectStatement):
    """Validate `stmt` against the schema. Returns a list of SemanticError
    (empty means the query is valid) and annotates the AST in place."""
    return Analyzer(stmt).run()

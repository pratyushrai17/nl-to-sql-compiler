"""
semantic_analyzer.py

Semantic analysis: checks that a syntactically valid SelectStatement also
*means* something against the Library schema, and decorates the AST with
what it learns (which table each column belongs to, and its type).

Passes, in order:

  1. Scope - build the query scope from FROM / JOIN (symbol_table.Scope).
       S001 undefined table, S005 duplicate table alias

  2. Resolution + type checking - one recursive walk over every expression
     (check_expr). For each node it resolves names, computes the node's
     type (INTEGER / REAL / TEXT / BOOLEAN) and checks the typing rules:
       S002 undefined column           S003 ambiguous column
       S004 unknown table qualifier    S016 SELECT alias used outside ORDER BY
       S006 type mismatch in a comparison / IN / BETWEEN
       S007 wrong operand type (arithmetic, LIKE, SUM/AVG of text, ...)
       S008 aggregate in WHERE         S009 aggregate in JOIN ON, or nested
       S013 WHERE / ON / HAVING / AND / OR / NOT operand is not a condition
       S015 '*' used anywhere except COUNT(*)

  3. Grouping rules (check_grouping) - only for aggregated queries:
       S010 column outside an aggregate that is not in GROUP BY
       S011 HAVING without GROUP BY
       S012 HAVING uses a column that is not in GROUP BY

  4. ORDER BY rules (check_order_by):
       S014 ORDER BY position number, or (with DISTINCT) an ORDER BY term
            that is not in the SELECT list

analyze() returns a list of SemanticError (empty = valid). All errors are
collected rather than stopping at the first, so the LLM can fix them all in
one repair attempt. A node whose type could not be determined (because an
error was already reported for it) gets type None, and checks involving a
None type are skipped - this avoids cascades of follow-on errors.
"""

from ast_nodes import (
    ARITHMETIC_OPS, COMPARISON_OPS, AggregateCall, BetweenExpr, BinaryOp,
    ColumnRef, InExpr, LikeExpr, Literal, SelectStatement, Star, UnaryOp,
    column_refs, contains_aggregate, to_sql,
)
from errors import SemanticError
from symbol_table import (
    NUMERIC_TYPES, SCHEMA, Scope, canonical_table, closest_match, column_type,
    get_columns,
)

BOOLEAN = "BOOLEAN"


def literal_type(value):
    if isinstance(value, bool):
        return BOOLEAN
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def comparable(t1, t2):
    """Can values of these two types be compared? Numbers compare with
    numbers, text with text, booleans with booleans."""
    if t1 in NUMERIC_TYPES and t2 in NUMERIC_TYPES:
        return True
    return t1 == t2


def describe_operand(node, sql_type):
    """e.g. "TEXT column 'title'" or "INTEGER value 1984" for error messages."""
    if isinstance(node, ColumnRef):
        return f"{sql_type} column '{to_sql(node)}'"
    if isinstance(node, Literal):
        return f"{sql_type} value {to_sql(node)}"
    return f"{sql_type} expression '{to_sql(node)}'"


class Analyzer:
    def __init__(self, stmt: SelectStatement):
        self.stmt = stmt
        self.scope = Scope()
        self.errors = []
        self.aliases = {}          # lower-case SELECT alias -> index in select_items
        self.select_types = []     # type of each SELECT item

    def error(self, code, message, node=None):
        line = getattr(node, "line", None) or None
        col = getattr(node, "col", None) or None
        self.errors.append(SemanticError(code, message, line, col))

    # -- driver ------------------------------------------------------------------

    def run(self):
        self.build_scope()
        if self.errors:
            # If a table does not exist, resolving columns against it would only
            # produce a cascade of confusing follow-on errors - stop here.
            return self.errors

        stmt = self.stmt
        for i, item in enumerate(stmt.select_items):
            if item.alias:
                self.aliases.setdefault(item.alias.lower(), i)
            self.select_types.append(self.check_select_item(item))

        for join in stmt.joins:
            self.require_condition(self.check_expr(join.on, "ON"), join.on, "JOIN ... ON")
        if stmt.where is not None:
            self.require_condition(self.check_expr(stmt.where, "WHERE"), stmt.where, "WHERE")
        for col in stmt.group_by:
            self.check_expr(col, "GROUP BY")
        if stmt.having is not None:
            self.require_condition(self.check_expr(stmt.having, "HAVING"), stmt.having, "HAVING")
        for item in stmt.order_by:
            self.check_expr(item.expr, "ORDER BY", top_level=True)

        if not self.errors:
            # The structural rules below assume every column was resolved.
            self.check_grouping()
            self.check_order_by()
        return self.errors

    # -- pass 1: scope -------------------------------------------------------------

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

    # -- pass 2: resolution and type checking ---------------------------------------------

    def check_select_item(self, item):
        if isinstance(item.expr, Star):
            star = item.expr
            if star.table is not None:
                found = self.scope.lookup_binding(star.table)
                if found is None:
                    self.error("S004",
                               f"'{star.table}.*' refers to '{star.table}', which is not a table "
                               f"or alias in FROM/JOIN ({', '.join(self.scope.binding_names())})",
                               star)
                else:
                    star.table = found[0]   # canonical binding name
            return None
        return self.check_expr(item.expr, "SELECT")

    def require_condition(self, sql_type, node, clause):
        if sql_type is not None and sql_type != BOOLEAN:
            self.error("S013",
                       f"The {clause} clause needs a condition (true/false), but "
                       f"'{to_sql(node)}' is a {sql_type} value; compare it with something, "
                       f"e.g. {to_sql(node)} = ...", node)

    def check_expr(self, node, clause, in_aggregate=False, top_level=False):
        """Resolve names in `node`, check typing rules, and return its type
        (or None if it could not be determined). `clause` says where the
        expression appears, because some rules depend on it (aggregates are
        not allowed in WHERE, SELECT aliases are visible only in ORDER BY)."""

        def sub(child):
            return self.check_expr(child, clause, in_aggregate)

        if isinstance(node, Literal):
            return literal_type(node.value)

        if isinstance(node, ColumnRef):
            return self.check_column(node, clause, top_level)

        if isinstance(node, Star):
            self.error("S015", "'*' can only be used as SELECT * or inside COUNT(*)", node)
            return None

        if isinstance(node, AggregateCall):
            return self.check_aggregate(node, clause, in_aggregate)

        if isinstance(node, BinaryOp):
            left, right = sub(node.left), sub(node.right)
            if node.op in ("AND", "OR"):
                for operand, t in ((node.left, left), (node.right, right)):
                    if t is not None and t != BOOLEAN:
                        self.error("S013",
                                   f"Both sides of {node.op} must be conditions, but "
                                   f"'{to_sql(operand)}' is a {t} value", operand)
                return BOOLEAN
            if node.op in COMPARISON_OPS:
                self.check_comparable(node.left, left, node.right, right, node,
                                      f"comparison '{node.op}'")
                return BOOLEAN
            if node.op in ARITHMETIC_OPS:
                for operand, t in ((node.left, left), (node.right, right)):
                    if t is not None and t not in NUMERIC_TYPES:
                        self.error("S007",
                                   f"Arithmetic operator '{node.op}' needs numbers, but got "
                                   f"{describe_operand(operand, t)}", node)
                if left is None or right is None:
                    return None
                return "INTEGER" if left == right == "INTEGER" else "REAL"

        if isinstance(node, UnaryOp):
            t = sub(node.operand)
            if node.op == "NOT":
                if t is not None and t != BOOLEAN:
                    self.error("S013", f"NOT must be applied to a condition, but "
                                       f"'{to_sql(node.operand)}' is a {t} value", node)
                return BOOLEAN
            if t is not None and t not in NUMERIC_TYPES:
                self.error("S007", f"Unary '-' needs a number, but got "
                                   f"{describe_operand(node.operand, t)}", node)
            return t

        if isinstance(node, LikeExpr):
            for operand in (node.expr, node.pattern):
                t = sub(operand)
                if t is not None and t != "TEXT":
                    self.error("S007", f"LIKE works on text, but got "
                                       f"{describe_operand(operand, t)}", operand)
            return BOOLEAN

        if isinstance(node, InExpr):
            t = sub(node.expr)
            for item in node.items:
                self.check_comparable(node.expr, t, item, sub(item), item, "IN list")
            return BOOLEAN

        if isinstance(node, BetweenExpr):
            t = sub(node.expr)
            for bound in (node.low, node.high):
                self.check_comparable(node.expr, t, bound, sub(bound), bound, "BETWEEN")
            return BOOLEAN

        raise TypeError(f"check_expr: unexpected node {node!r}")

    def check_comparable(self, left, lt, right, rt, at_node, context):
        if lt is None or rt is None or comparable(lt, rt):
            return
        hint = ""
        if isinstance(right, Literal) and lt == "TEXT":
            hint = f"; to match text, put the value in quotes: '{right.value}'"
        self.error("S006",
                   f"Type mismatch in {context}: cannot compare {describe_operand(left, lt)} "
                   f"with {describe_operand(right, rt)}{hint}", at_node)

    def check_column(self, col, clause, top_level):
        # In ORDER BY a bare name may refer to a SELECT alias: ORDER BY n DESC.
        # A whole ORDER BY term prefers the alias (as SQLite does); inside a
        # larger expression a real column wins and the alias is the fallback.
        alias_index = None
        if clause == "ORDER BY" and col.table is None:
            alias_index = self.aliases.get(col.name.lower())
            if alias_index is not None and (top_level or not self.scope.tables_with_column(col.name)):
                col.alias_index = alias_index
                return self.select_types[alias_index]

        # Elsewhere, an alias is not visible: SQL evaluates WHERE/GROUP BY/HAVING
        # before the SELECT list, so the alias does not exist yet.
        if col.table is None and col.name.lower() in self.aliases \
                and not self.scope.tables_with_column(col.name):
            self.error("S016",
                       f"'{col.name}' is a SELECT alias and cannot be used in {clause}; "
                       f"repeat the expression itself instead", col)
            return None

        return self.resolve_column(col)

    def resolve_column(self, col: ColumnRef):
        """Find the table a column belongs to and annotate the node with it."""
        if col.table is not None:
            found = self.scope.lookup_binding(col.table)
            if found is None:
                self.error("S004",
                           f"'{col.table}.{col.name}' uses '{col.table}', which is not a table "
                           f"or alias in FROM/JOIN ({', '.join(self.scope.binding_names())})",
                           col)
                return None
            binding, table = found
            matches = [m for m in self.scope.tables_with_column(col.name) if m[0] == binding]
            if not matches:
                self.report_unknown_column(col, [table])
                return None
        else:
            matches = self.scope.tables_with_column(col.name)
            if not matches:
                self.report_unknown_column(col, [t for _, t in self.scope.bindings])
                return None
            if len(matches) > 1:
                options = " or ".join(f"'{b}.{c}'" for b, _, c in matches)
                self.error("S003",
                           f"Column '{col.name}' is ambiguous: it exists in more than one "
                           f"table in scope; qualify it as {options}", col)
                return None

        binding, table, canonical = matches[0]
        col.name = canonical
        col.qualifier = binding
        col.sql_type = column_type(table, canonical)
        return col.sql_type

    def report_unknown_column(self, col, tables):
        known = sorted({c for t in tables for c in get_columns(t)})
        hint = closest_match(col.name, known)
        suggestion = f" Did you mean '{hint}'?" if hint else ""
        where = f"table '{tables[0]}'" if len(tables) == 1 else \
            f"any table in scope ({', '.join(tables)})"
        self.error("S002",
                   f"Column '{col.name}' does not exist in {where}.{suggestion} "
                   f"(known columns: {', '.join(known)})", col)

    def check_aggregate(self, agg, clause, in_aggregate):
        if clause == "WHERE":
            self.error("S008",
                       f"Aggregate {agg.func}() is not allowed in WHERE, because WHERE filters "
                       f"single rows before any grouping; move this condition to HAVING", agg)
        elif clause == "ON":
            self.error("S009", f"Aggregate {agg.func}() is not allowed in a JOIN ... ON "
                               f"condition", agg)
        elif in_aggregate:
            self.error("S009", f"Aggregate {agg.func}() cannot be nested inside another "
                               f"aggregate", agg)

        if isinstance(agg.arg, Star):
            if agg.func != "COUNT" or agg.distinct:
                self.error("S015", f"{agg.func}({'DISTINCT ' if agg.distinct else ''}*) is not "
                                   f"valid; only COUNT(*) accepts '*' - pass a column instead", agg)
            agg.sql_type = "INTEGER"
            return agg.sql_type

        arg_type = self.check_expr(agg.arg, clause, in_aggregate=True)
        if arg_type == BOOLEAN:
            self.error("S007", f"{agg.func}() cannot be applied to a condition "
                               f"('{to_sql(agg.arg)}')", agg)
            arg_type = None
        if agg.func in ("SUM", "AVG") and arg_type is not None and arg_type not in NUMERIC_TYPES:
            self.error("S007", f"{agg.func}() needs a numeric argument, but "
                               f"{describe_operand(agg.arg, arg_type)} is text; use COUNT, MIN "
                               f"or MAX for text columns", agg)
            arg_type = None

        if agg.func == "COUNT":
            agg.sql_type = "INTEGER"
        elif agg.func == "AVG":
            agg.sql_type = "REAL"
        else:                      # SUM, MIN, MAX keep the argument's type
            agg.sql_type = arg_type
        return agg.sql_type

    # -- pass 3: grouping rules -----------------------------------------------------------

    def is_aggregated(self):
        stmt = self.stmt
        exprs = [i.expr for i in stmt.select_items] + [o.expr for o in stmt.order_by]
        if stmt.having is not None:
            exprs.append(stmt.having)
        return bool(stmt.group_by) or any(contains_aggregate(e) for e in exprs)

    def check_grouping(self):
        """THE GROUP BY RULE. In an aggregated query (one with GROUP BY, or
        with an aggregate anywhere in SELECT / HAVING / ORDER BY), each output
        row stands for a whole *group* of input rows. A column used outside an
        aggregate has a single value per group only if it is a GROUP BY
        column; any other column would have many candidate values, so it is
        rejected."""
        stmt = self.stmt

        if stmt.having is not None and not stmt.group_by:
            self.error("S011", "HAVING requires a GROUP BY clause (HAVING filters groups); "
                               "use WHERE to filter individual rows, or add GROUP BY",
                       stmt.having)

        if not self.is_aggregated():
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

        if stmt.having is not None and stmt.group_by:
            for col in column_refs(stmt.having, inside_aggregates=False):
                if col.key() not in grouped:
                    self.error("S012",
                               f"HAVING uses column '{col}', which is not in GROUP BY; HAVING "
                               f"may only use GROUP BY columns and aggregates (move row "
                               f"conditions to WHERE)", col)

        for item in stmt.order_by:
            for col in column_refs(item.expr, inside_aggregates=False):
                if col.alias_index is None and col.key() not in grouped:
                    self.error("S010",
                               f"ORDER BY uses column '{col}', which is not in GROUP BY; in an "
                               f"aggregated query, order by a GROUP BY column, an aggregate, "
                               f"or a SELECT alias", col)

    # -- pass 4: ORDER BY rules -------------------------------------------------------------

    def check_order_by(self):
        stmt = self.stmt
        for item in stmt.order_by:
            expr = item.expr
            if isinstance(expr, Literal) and isinstance(expr.value, int) \
                    and not isinstance(expr.value, bool):
                self.error("S014", f"ORDER BY {expr.value}: ordering by column position is not "
                                   f"supported; use a column name or SELECT alias", expr)
                continue
            if stmt.distinct and not self.is_in_select_list(expr):
                self.error("S014",
                           f"With SELECT DISTINCT, ORDER BY term '{to_sql(expr)}' must also "
                           f"appear in the SELECT list", expr)

    def is_in_select_list(self, expr):
        if isinstance(expr, ColumnRef) and expr.alias_index is not None:
            return True
        target = to_sql(expr, resolved=True)
        for item in self.stmt.select_items:
            if isinstance(item.expr, Star):
                if isinstance(expr, ColumnRef) and item.expr.table in (None, expr.qualifier):
                    return True
            elif to_sql(item.expr, resolved=True) == target:
                return True
        return False


def analyze(stmt: SelectStatement):
    """Validate `stmt` against the schema. Returns a list of SemanticError
    (empty means the query is valid) and annotates the AST in place."""
    return Analyzer(stmt).run()

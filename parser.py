"""
parser.py

Hand-written recursive-descent parser: list of Tokens -> SelectStatement AST.

Each grammar rule in docs/GRAMMAR.md is one method below (parse_<rule>).
A method looks at the next token, decides which alternative applies,
consumes tokens with expect()/match(), and calls the methods for the
sub-rules. The call stack of these methods *is* the parse tree.

Operator precedence is encoded by the layering of the expression rules:
each level only calls the level above it, so tighter-binding operators are
parsed deeper in the recursion and end up lower in the AST.

    parse_expr          -> OR            (lowest precedence)
    parse_and           -> AND
    parse_not           -> NOT (prefix)
    parse_predicate     -> = != < > <= >=, [NOT] LIKE / IN / BETWEEN
    parse_additive      -> + -
    parse_term          -> * /
    parse_unary         -> unary -
    parse_primary       -> literals, columns, aggregates, ( expr )   (highest)

Loops (`while self.match("OR")`) make the binary operators left-associative:
a OR b OR c  ->  (a OR b) OR c.

The parser stops at the first syntax error and raises ParseError with the
line/column of the offending token. Error codes:
    P001  expected X but found Y
    P002  only SELECT is supported (DROP / DELETE / UPDATE / INSERT / ...)
    P003  unsupported SQL feature (subquery, LEFT JOIN, UNION, ...)
    P004  unknown function
    P005  expression nested too deeply (more than MAX_DEPTH levels)

Depth limit: every nested parenthesis, NOT, unary minus, and every
operator in a chain such as a OR b OR c ... adds one level to the
expression tree. Later stages walk that tree recursively, so a
pathological query (hundreds of nested parentheses) would exhaust Python's
recursion limit. The parser therefore rejects expressions deeper than
MAX_DEPTH with a clear error instead of crashing. Parentheses and function
calls have their own, smaller limit (MAX_PAREN_DEPTH), because each one
re-enters the parser through all eight precedence levels at once.
"""

from ast_nodes import (
    AggregateCall, BetweenExpr, BinaryOp, ColumnRef, InExpr, JoinClause,
    LikeExpr, Literal, OrderByItem, SelectItem, SelectStatement, Star,
    TableRef, UnaryOp,
)
from errors import ParseError
from lexer import describe
from symbol_table import AGGREGATE_FUNCTIONS

# token type -> operator spelling in the AST
COMPARISON_TOKENS = {"EQ": "=", "NEQ": "!=", "LT": "<", "GT": ">", "LE": "<=", "GE": ">="}

MAX_DEPTH = 150
MAX_PAREN_DEPTH = 40

NON_SELECT_STATEMENTS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "REPLACE", "ATTACH", "DETACH", "PRAGMA", "VACUUM",
}

# Human-readable names for token types used in "expected ..." messages.
TOKEN_NAMES = {
    "IDENT": "an identifier", "NUMBER": "a number", "STRING": "a string",
    "COMMA": "','", "DOT": "'.'", "LPAREN": "'('", "RPAREN": "')'",
    "STAR": "'*'", "EOF": "end of input",
}


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.depth = 0         # current expression nesting depth (see MAX_DEPTH)
        self.paren_depth = 0   # current ( ... ) / function-call nesting

    # -- token stream helpers -------------------------------------------------------

    def peek(self, offset=0):
        i = min(self.pos + offset, len(self.tokens) - 1)   # never run past EOF
        return self.tokens[i]

    def advance(self):
        tok = self.tokens[self.pos]
        if tok.type != "EOF":
            self.pos += 1
        return tok

    def check(self, *types):
        return self.peek().type in types

    def match(self, *types):
        """Consume and return the next token if it is one of `types`."""
        if self.check(*types):
            return self.advance()
        return None

    def expect(self, type_, what=None):
        """Consume a token of the given type or raise a P001 syntax error."""
        if self.check(type_):
            return self.advance()
        expected = what or TOKEN_NAMES.get(type_, f"keyword {type_}")
        self.error_expected(expected)

    def error_expected(self, expected):
        tok = self.peek()
        if tok.type in NON_SELECT_STATEMENTS:
            self.error_not_select(tok)
        raise ParseError("P001", f"Expected {expected} but found {describe(tok)}",
                         tok.line, tok.col)

    def error_not_select(self, tok):
        raise ParseError(
            "P002",
            f"Only SELECT is supported: '{tok.value}' statements are not allowed "
            f"(this tool never modifies the database)",
            tok.line, tok.col,
        )

    def deeper(self, levels=1):
        """Record `levels` more levels of expression nesting."""
        self.depth += levels
        if self.depth > MAX_DEPTH or self.paren_depth > MAX_PAREN_DEPTH:
            tok = self.peek()
            raise ParseError("P005", f"Expression is nested too deeply (limit: {MAX_PAREN_DEPTH} "
                                     f"nested parentheses/function calls, {MAX_DEPTH} levels of "
                                     f"operators); simplify the query",
                             tok.line, tok.col)

    def error_unsupported(self, tok, feature):
        raise ParseError("P003", f"{feature} is not supported by this SQL subset",
                         tok.line, tok.col)

    # -- entry point ------------------------------------------------------------------

    def parse(self) -> SelectStatement:
        """query := select_stmt [";"] EOF"""
        first = self.peek()
        if first.type in NON_SELECT_STATEMENTS:
            self.error_not_select(first)
        if first.type == "WITH":
            self.error_unsupported(first, "WITH (common table expressions)")

        stmt = self.parse_select_stmt()

        if self.check("UNION", "INTERSECT", "EXCEPT"):
            self.error_unsupported(self.peek(), self.peek().type)
        if self.match("SEMICOLON"):
            if self.check(*NON_SELECT_STATEMENTS):
                self.error_not_select(self.peek())
            if not self.check("EOF"):
                tok = self.peek()
                raise ParseError(
                    "P001",
                    f"Expected end of input after ';' but found {describe(tok)} "
                    f"(only one statement is allowed)",
                    tok.line, tok.col,
                )
        if not self.check("EOF"):
            self.error_expected("end of query (or a clause such as WHERE, GROUP BY, "
                                "ORDER BY, LIMIT)")
        return stmt

    # -- statement and clauses ------------------------------------------------------------

    def parse_select_stmt(self) -> SelectStatement:
        """select_stmt := SELECT [DISTINCT] select_list FROM table_ref join*
                          [WHERE expr] [GROUP BY column_list] [HAVING expr]
                          [ORDER BY order_list] [LIMIT NUMBER]"""
        self.expect("SELECT")
        distinct = self.match("DISTINCT") is not None
        select_items = self.parse_select_list()

        self.expect("FROM")
        from_table = self.parse_table_ref()
        if self.check("COMMA"):
            self.error_unsupported(self.peek(), "A comma-separated FROM list "
                                                "(use JOIN ... ON instead)")

        joins = []
        while True:
            if self.check("LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "NATURAL"):
                self.error_unsupported(self.peek(), f"{self.peek().type} JOIN (only INNER JOIN)")
            if not self.check("JOIN", "INNER"):
                break
            joins.append(self.parse_join())

        where = None
        if self.match("WHERE"):
            where = self.parse_expr()

        group_by = []
        if self.match("GROUP"):
            self.expect("BY")
            group_by.append(self.parse_column_ref())
            while self.match("COMMA"):
                group_by.append(self.parse_column_ref())

        having = None
        if self.match("HAVING"):
            having = self.parse_expr()

        order_by = []
        if self.match("ORDER"):
            self.expect("BY")
            order_by.append(self.parse_order_item())
            while self.match("COMMA"):
                order_by.append(self.parse_order_item())

        limit = None
        if self.match("LIMIT"):
            tok = self.peek()
            if tok.type != "NUMBER" or not isinstance(tok.value, int):
                self.error_expected("a non-negative whole number after LIMIT")
            limit = self.advance().value
            if self.check("OFFSET", "COMMA"):
                self.error_unsupported(self.peek(), "LIMIT with an offset")

        return SelectStatement(
            select_items=select_items, from_table=from_table, joins=joins,
            where=where, group_by=group_by, having=having, order_by=order_by,
            distinct=distinct, limit=limit,
        )

    def parse_select_list(self):
        """select_list := select_item {"," select_item}"""
        items = [self.parse_select_item()]
        while self.match("COMMA"):
            items.append(self.parse_select_item())
        return items

    def parse_select_item(self) -> SelectItem:
        """select_item := "*" | IDENT "." "*" | expr [[AS] IDENT]"""
        tok = self.peek()
        if self.match("STAR"):
            return SelectItem(Star(None, line=tok.line, col=tok.col))
        if tok.type == "IDENT" and self.peek(1).type == "DOT" and self.peek(2).type == "STAR":
            self.advance()
            self.advance()
            self.advance()
            return SelectItem(Star(tok.value, line=tok.line, col=tok.col))

        if self.check("FROM"):
            self.error_expected("a column or expression in the SELECT list")
        expr = self.parse_expr()
        return SelectItem(expr, self.parse_optional_alias())

    def parse_optional_alias(self):
        """[AS] IDENT - the AS keyword is optional, as in SQLite."""
        if self.match("AS"):
            return self.expect("IDENT", "an alias name after AS").value
        if self.check("IDENT"):
            return self.advance().value
        return None

    def parse_table_ref(self) -> TableRef:
        """table_ref := IDENT [[AS] IDENT]"""
        tok = self.peek()
        if tok.type == "LPAREN" and self.peek(1).type == "SELECT":
            self.error_unsupported(tok, "A subquery in FROM")
        name = self.expect("IDENT", "a table name").value
        return TableRef(name, self.parse_optional_alias(), line=tok.line, col=tok.col)

    def parse_join(self) -> JoinClause:
        """join := [INNER] JOIN table_ref ON expr"""
        self.match("INNER")
        self.expect("JOIN")
        table = self.parse_table_ref()
        self.expect("ON", "keyword ON (every JOIN needs an ON condition)")
        return JoinClause(table, self.parse_expr())

    def parse_order_item(self) -> OrderByItem:
        """order_item := expr [ASC | DESC]"""
        expr = self.parse_expr()
        direction = "ASC"
        if self.check("ASC", "DESC"):
            direction = self.advance().type
        return OrderByItem(expr, direction)

    def parse_column_ref(self) -> ColumnRef:
        """column_ref := IDENT ["." IDENT]"""
        tok = self.expect("IDENT", "a column name")
        if self.match("DOT"):
            name = self.expect("IDENT", "a column name after '.'").value
            return ColumnRef(tok.value, name, line=tok.line, col=tok.col)
        return ColumnRef(None, tok.value, line=tok.line, col=tok.col)

    # -- expressions, lowest precedence first -------------------------------------------------

    def parse_binary_chain(self, operand_rule, token_types):
        """Shared loop for left-associative operators:  operand {op operand}.
        Each operator makes the tree one level deeper, so it counts toward MAX_DEPTH."""
        start_depth = self.depth
        left = operand_rule()
        while self.check(*token_types):
            tok = self.advance()
            self.deeper()
            left = BinaryOp(tok.value.upper() if tok.type in ("AND", "OR") else tok.value,
                            left, operand_rule(), line=tok.line, col=tok.col)
        self.depth = start_depth
        return left

    def parse_expr(self):
        """expr := and_expr {OR and_expr}"""
        return self.parse_binary_chain(self.parse_and, ("OR",))

    def parse_and(self):
        """and_expr := not_expr {AND not_expr}"""
        return self.parse_binary_chain(self.parse_not, ("AND",))

    def parse_not(self):
        """not_expr := NOT not_expr | predicate"""
        tok = self.match("NOT")
        if tok:
            self.deeper()
            operand = self.parse_not()
            self.depth -= 1
            return UnaryOp("NOT", operand, line=tok.line, col=tok.col)
        return self.parse_predicate()

    def parse_predicate(self):
        """predicate := additive [ comp_op additive
                                 | [NOT] LIKE additive
                                 | [NOT] IN "(" expr {"," expr} ")"
                                 | [NOT] BETWEEN additive AND additive ]"""
        left = self.parse_additive()
        tok = self.peek()

        if tok.type in COMPARISON_TOKENS:
            self.advance()
            right = self.parse_additive()
            return BinaryOp(COMPARISON_TOKENS[tok.type], left, right, line=tok.line, col=tok.col)

        negated = False
        if tok.type == "NOT" and self.peek(1).type in ("LIKE", "IN", "BETWEEN"):
            self.advance()
            negated = True
        op_tok = self.peek()

        if self.match("LIKE"):
            return LikeExpr(left, self.parse_additive(), negated, line=op_tok.line, col=op_tok.col)

        if self.match("IN"):
            self.expect("LPAREN", "'(' after IN")
            if self.check("SELECT"):
                self.error_unsupported(self.peek(), "A subquery inside IN (...)")
            items = [self.parse_expr()]
            while self.match("COMMA"):
                items.append(self.parse_expr())
            self.expect("RPAREN", "')' to close the IN list")
            return InExpr(left, items, negated, line=op_tok.line, col=op_tok.col)

        if self.match("BETWEEN"):
            # Bounds are parsed at the additive level, so the AND here belongs
            # to BETWEEN and is not mistaken for a logical AND.
            low = self.parse_additive()
            self.expect("AND", "keyword AND between the BETWEEN bounds")
            high = self.parse_additive()
            return BetweenExpr(left, low, high, negated, line=op_tok.line, col=op_tok.col)

        return left

    def parse_additive(self):
        """additive := term {("+" | "-") term}"""
        return self.parse_binary_chain(self.parse_term, ("PLUS", "MINUS"))

    def parse_term(self):
        """term := unary {("*" | "/") unary}"""
        return self.parse_binary_chain(self.parse_unary, ("STAR", "SLASH"))

    def parse_unary(self):
        """unary := "-" unary | primary"""
        tok = self.match("MINUS")
        if tok:
            self.deeper()
            operand = self.parse_unary()
            self.depth -= 1
            # Fold "-5" into a negative literal right away; keep "-col" as an operator.
            if isinstance(operand, Literal) and isinstance(operand.value, (int, float)) \
                    and not isinstance(operand.value, bool):
                return Literal(-operand.value, line=tok.line, col=tok.col)
            return UnaryOp("-", operand, line=tok.line, col=tok.col)
        return self.parse_primary()

    def parse_primary(self):
        """primary := NUMBER | STRING | TRUE | FALSE | aggregate
                    | column_ref | "(" expr ")" """
        tok = self.peek()

        if tok.type in ("NUMBER", "STRING"):
            self.advance()
            return Literal(tok.value, line=tok.line, col=tok.col)
        if tok.type in ("TRUE", "FALSE"):
            self.advance()
            return Literal(tok.type == "TRUE", line=tok.line, col=tok.col)

        if tok.type == "LPAREN":
            if self.peek(1).type == "SELECT":
                self.error_unsupported(self.peek(1), "A subquery")
            self.advance()
            self.paren_depth += 1
            self.deeper()
            expr = self.parse_expr()
            self.depth -= 1
            self.paren_depth -= 1
            self.expect("RPAREN", "')' to close the parenthesis")
            return expr

        if tok.type == "IDENT" and self.peek(1).type == "LPAREN":
            return self.parse_function_call()

        if tok.type == "IDENT":
            return self.parse_column_ref()

        self.error_expected("an expression (a column, a literal, an aggregate or '(')")

    def parse_function_call(self):
        """aggregate := AGG_NAME "(" ("*" | [DISTINCT] expr) ")" """
        name_tok = self.advance()
        func = name_tok.value.upper()
        if func not in AGGREGATE_FUNCTIONS:
            raise ParseError(
                "P004",
                f"Unknown function '{name_tok.value}'; the supported functions are "
                f"{', '.join(sorted(AGGREGATE_FUNCTIONS))}",
                name_tok.line, name_tok.col,
            )
        self.expect("LPAREN")
        distinct = self.match("DISTINCT") is not None
        star_tok = self.match("STAR")
        if star_tok:
            arg = Star(None, line=star_tok.line, col=star_tok.col)
        else:
            self.paren_depth += 1
            self.deeper()
            arg = self.parse_expr()
            self.depth -= 1
            self.paren_depth -= 1
        self.expect("RPAREN", f"')' to close {func}(")
        return AggregateCall(func, arg, distinct, line=name_tok.line, col=name_tok.col)


def parse(tokens) -> SelectStatement:
    """Parse a token list (from lexer.tokenize) into a SelectStatement.
    Raises ParseError on the first syntax error."""
    return Parser(tokens).parse()

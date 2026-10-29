"""Tests for parser.py: AST shape, precedence, every clause, and syntax errors."""

import unittest

from ast_nodes import (
    AggregateCall, BetweenExpr, BinaryOp, ColumnRef, InExpr, LikeExpr, Literal,
    Star, UnaryOp, statement_to_sql,
)
from errors import ParseError
from lexer import tokenize
from parser import parse


def p(sql):
    return parse(tokenize(sql))


def where(sql_condition):
    return p(f"SELECT * FROM Books WHERE {sql_condition}").where


class ParserStructureTests(unittest.TestCase):

    def test_all_clauses(self):
        stmt = p("SELECT DISTINCT genre, COUNT(*) AS n FROM Books AS b "
                 "WHERE id > 1 GROUP BY genre HAVING COUNT(*) > 1 "
                 "ORDER BY n DESC, genre LIMIT 3;")
        self.assertTrue(stmt.distinct)
        self.assertEqual(len(stmt.select_items), 2)
        self.assertEqual(stmt.select_items[1].alias, "n")
        self.assertEqual((stmt.from_table.name, stmt.from_table.alias), ("Books", "b"))
        self.assertIsInstance(stmt.where, BinaryOp)
        self.assertEqual(stmt.group_by, [ColumnRef(None, "genre")])
        self.assertIsInstance(stmt.having, BinaryOp)
        self.assertEqual([o.direction for o in stmt.order_by], ["DESC", "ASC"])
        self.assertEqual(stmt.limit, 3)

    def test_aliases_with_and_without_as(self):
        stmt = p("SELECT title t, author AS a FROM Books bk")
        self.assertEqual([i.alias for i in stmt.select_items], ["t", "a"])
        self.assertEqual(stmt.from_table.alias, "bk")

    def test_two_joins(self):
        stmt = p("SELECT m.name FROM Members m JOIN Loans l ON m.id = l.member_id "
                 "INNER JOIN Books b ON b.id = l.book_id")
        self.assertEqual([j.table.name for j in stmt.joins], ["Loans", "Books"])

    def test_star_forms(self):
        stmt = p("SELECT *, b.* FROM Books b")
        self.assertEqual(stmt.select_items[0].expr, Star(None))
        self.assertEqual(stmt.select_items[1].expr, Star("b"))
        agg = p("SELECT COUNT(*) FROM Books").select_items[0].expr
        self.assertEqual(agg, AggregateCall("COUNT", Star(None)))

    def test_count_distinct(self):
        agg = p("SELECT COUNT(DISTINCT author) FROM Books").select_items[0].expr
        self.assertTrue(agg.distinct)
        self.assertEqual(agg.arg, ColumnRef(None, "author"))


class ParserPrecedenceTests(unittest.TestCase):

    def test_and_binds_tighter_than_or(self):
        expr = where("a = 1 OR b = 2 AND c = 3")
        self.assertEqual(expr.op, "OR")
        self.assertEqual(expr.right.op, "AND")

    def test_parentheses_override_precedence(self):
        expr = where("(a = 1 OR b = 2) AND c = 3")
        self.assertEqual(expr.op, "AND")
        self.assertEqual(expr.left.op, "OR")

    def test_not_binds_tighter_than_and(self):
        expr = where("NOT a = 1 AND b = 2")
        self.assertEqual(expr.op, "AND")
        self.assertIsInstance(expr.left, UnaryOp)

    def test_arithmetic_precedence_and_left_associativity(self):
        expr = where("id = 1 + 2 * 3 - 4")
        # right side is (1 + (2 * 3)) - 4
        rhs = expr.right
        self.assertEqual(rhs.op, "-")
        self.assertEqual(rhs.left.op, "+")
        self.assertEqual(rhs.left.right.op, "*")

    def test_negative_literal(self):
        self.assertEqual(where("id > -5").right, Literal(-5))

    def test_like_in_between_with_not(self):
        expr = where("title NOT LIKE 'The%' AND genre IN ('A', 'B') "
                     "AND id NOT BETWEEN 2 AND 4")
        like = expr.left.left
        in_expr = expr.left.right
        between = expr.right
        self.assertIsInstance(like, LikeExpr)
        self.assertTrue(like.negated)
        self.assertIsInstance(in_expr, InExpr)
        self.assertEqual(len(in_expr.items), 2)
        self.assertIsInstance(between, BetweenExpr)
        self.assertTrue(between.negated)

    def test_between_and_is_not_logical_and(self):
        expr = where("id BETWEEN 1 AND 5 AND genre = 'X'")
        self.assertEqual(expr.op, "AND")
        self.assertIsInstance(expr.left, BetweenExpr)

    def test_deeply_nested_parentheses(self):
        expr = where("((((id = 1))))")
        self.assertEqual(expr, BinaryOp("=", ColumnRef(None, "id"), Literal(1)))

    def test_long_and_or_chain(self):
        cond = " OR ".join(f"id = {i}" for i in range(30)) + " AND genre = 'X'"
        expr = where(cond)
        self.assertEqual(expr.op, "OR")              # OR is the root
        self.assertEqual(expr.right.op, "AND")       # last disjunct holds the AND

    def test_pretty_printer_round_trip(self):
        queries = [
            "SELECT title FROM Books WHERE (genre = 'A' OR genre = 'B') AND id > 2",
            "SELECT DISTINCT genre FROM Books ORDER BY genre DESC LIMIT 2",
            "SELECT a.name, COUNT(*) AS n FROM Members AS a JOIN Loans AS l ON a.id = l.member_id "
            "GROUP BY a.name HAVING COUNT(*) >= 1",
            "SELECT title FROM Books WHERE NOT (id = 1 OR id = 2) AND title LIKE 'it''s%'",
            "SELECT id - (2 - 1), id * (1 + 2) FROM Books",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                ast = p(sql)
                self.assertEqual(p(statement_to_sql(ast)), ast)


class ParserErrorTests(unittest.TestCase):

    def assertParseError(self, sql, code, line=None, col=None, text=None):
        with self.assertRaises(ParseError) as ctx:
            p(sql)
        err = ctx.exception
        self.assertEqual(err.code, code, err.format())
        if line is not None:
            self.assertEqual((err.line, err.col), (line, col), err.format())
        if text is not None:
            self.assertIn(text, err.message)
        return err

    def test_missing_from_reports_expected_and_found(self):
        self.assertParseError("SELECT title Books", "P001", 1, 19,
                              "Expected keyword FROM but found end of input")

    def test_error_position_on_second_line(self):
        self.assertParseError("SELECT title\nFROM Books\nWHERE genre = = 'x'", "P001", 3, 15)

    def test_unclosed_parenthesis(self):
        self.assertParseError("SELECT title FROM Books WHERE (id = 1", "P001",
                              text="')' to close the parenthesis")

    def test_trailing_comma_in_select_list(self):
        self.assertParseError("SELECT title, FROM Books", "P001", 1, 15)

    def test_non_select_statements_are_rejected(self):
        for sql in ["DROP TABLE Books", "DELETE FROM Books", "UPDATE Books SET title = 'x'",
                    "INSERT INTO Books VALUES (9, 'x', 'y', 'z')", "CREATE TABLE t (a INT)"]:
            with self.subTest(sql=sql):
                self.assertParseError(sql, "P002", 1, 1, "Only SELECT is supported")

    def test_second_statement_after_semicolon_is_rejected(self):
        self.assertParseError("SELECT * FROM Books; DROP TABLE Books", "P002", 1, 22)

    def test_unsupported_features(self):
        for sql in ["SELECT * FROM Books LEFT JOIN Loans ON Books.id = Loans.book_id",
                    "SELECT * FROM Books WHERE id IN (SELECT book_id FROM Loans)",
                    "SELECT * FROM Books, Loans",
                    "SELECT id FROM Books UNION SELECT id FROM Loans"]:
            with self.subTest(sql=sql):
                self.assertParseError(sql, "P003")

    def test_unknown_function(self):
        self.assertParseError("SELECT UPPER(title) FROM Books", "P004", 1, 8)

    def test_limit_requires_whole_number(self):
        self.assertParseError("SELECT * FROM Books LIMIT 'x'", "P001")
        self.assertParseError("SELECT * FROM Books LIMIT 2.5", "P001")


if __name__ == "__main__":
    unittest.main()

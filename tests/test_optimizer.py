"""Tests for optimizer.py: each pass fires when it should, and only then."""

import unittest

from ast_nodes import Literal
from optimizer import fold
from pipeline import compile_sql
from lexer import tokenize
from parser import parse


def optimized(sql):
    result = compile_sql(sql)
    assert result.ok, [e.format() for e in result.errors]
    return result.optimization


def fold_where(condition):
    stmt = parse(tokenize(f"SELECT * FROM Books WHERE {condition}"))
    return fold(stmt.where)


class ConstantFoldingTests(unittest.TestCase):

    def test_arithmetic_is_folded(self):
        opt = optimized("SELECT title FROM Books WHERE id > 2 * 3 - 1")
        self.assertIn("Books.id > 5", str(opt.after))
        self.assertEqual(opt.fired, ["constant_folding"])

    def test_boolean_identities(self):
        self.assertEqual(fold_where("1 = 1"), Literal(True))
        self.assertEqual(repr(fold_where("1 = 1 AND id = 3")), "id = 3")
        self.assertEqual(repr(fold_where("id = 3 OR 2 > 3")), "id = 3")
        self.assertEqual(fold_where("1 = 2 AND id = 3"), Literal(False))
        self.assertEqual(fold_where("id = 3 OR 'a' = 'a'"), Literal(True))
        self.assertEqual(repr(fold_where("NOT NOT id = 3")), "id = 3")

    def test_like_in_between_on_constants_are_folded(self):
        self.assertEqual(fold_where("'Hobbit' LIKE 'h%t'"), Literal(True))
        self.assertEqual(fold_where("3 IN (1, 2)"), Literal(False))
        self.assertEqual(fold_where("3 BETWEEN 1 AND 5"), Literal(True))

    def test_always_true_filter_is_removed(self):
        opt = optimized("SELECT title FROM Books WHERE 1 = 1")
        self.assertNotIn("FILTER", opt.after.ops())

    def test_division_by_zero_is_not_folded(self):
        self.assertEqual(repr(fold_where("id = 1 / 0")), "id = 1 / 0")

    def test_original_plan_is_not_modified(self):
        opt = optimized("SELECT title FROM Books WHERE 1 = 1")
        self.assertIn("FILTER", opt.before.ops())


class PredicatePushdownTests(unittest.TestCase):

    SQL = ("SELECT m.name, b.title FROM Members m JOIN Loans l ON m.id = l.member_id "
           "JOIN Books b ON l.book_id = b.id "
           "WHERE b.genre = 'Fantasy' AND m.id = 2 AND m.id + b.id > 2")

    def test_single_table_conjuncts_move_above_their_scans(self):
        steps = [str(s) for s in optimized(self.SQL).after.steps()]
        self.assertEqual(steps[0], "t1 = SCAN Members AS m")
        self.assertEqual(steps[1], "t2 = FILTER t1 WHERE m.id = 2")
        self.assertIn("t6 = FILTER t5 WHERE b.genre = 'Fantasy'", steps)

    def test_two_table_conjunct_joins_the_on_condition(self):
        steps = [str(s) for s in optimized(self.SQL).after.steps()]
        self.assertIn("t7 = JOIN t4, t6 ON l.book_id = b.id AND m.id + b.id > 2", steps)

    def test_top_filter_disappears_when_everything_moved(self):
        after = optimized(self.SQL).after
        self.assertNotEqual(after.steps()[-2].node.op, "FILTER")

    def test_no_pushdown_without_join(self):
        opt = optimized("SELECT title FROM Books WHERE genre = 'Fantasy'")
        self.assertEqual(opt.fired, [])
        self.assertEqual(str(opt.before), str(opt.after))


class RedundantProjectionTests(unittest.TestCase):

    def test_select_star_projection_is_removed(self):
        opt = optimized("SELECT * FROM Books")
        self.assertEqual(opt.after.ops(), ["SCAN"])
        self.assertEqual(opt.after.root.headers, ["id", "title", "author", "genre"])

    def test_reordered_or_renamed_projection_is_kept(self):
        self.assertIn("PROJECT", optimized("SELECT title, id FROM Books").after.ops())
        self.assertIn("PROJECT", optimized(
            "SELECT id, title AS t, author, genre FROM Books").after.ops())

    def test_removal_under_limit_keeps_columns_consistent(self):
        opt = optimized("SELECT * FROM Loans LIMIT 2")
        self.assertEqual(opt.after.ops(), ["SCAN", "LIMIT"])
        self.assertEqual(opt.after.root.columns[0], "Loans.id")


if __name__ == "__main__":
    unittest.main()

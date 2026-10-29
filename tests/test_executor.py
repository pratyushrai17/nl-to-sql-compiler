"""Tests for executor.py and expr_eval.py.

The differential tests run every query in examples.VALID_QUERIES three
ways - SQLite, our interpreter on the unoptimized plan, and our interpreter
on the optimized plan - and require all three to agree.
"""

import unittest

from db import get_connection
from examples import VALID_QUERIES
from executor import ExecutionResult, compare_results, cross_check, execute
from expr_eval import arithmetic, in_list, like_match, sql_and, sql_not, sql_or
from pipeline import compile_sql


class DifferentialTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.conn = get_connection()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_corpus_is_large_enough(self):
        self.assertGreaterEqual(len(VALID_QUERIES), 40)

    def test_optimized_plan_matches_sqlite(self):
        for sql in VALID_QUERIES:
            with self.subTest(sql=sql):
                compiled = compile_sql(sql)
                self.assertTrue(compiled.ok, [e.format() for e in compiled.errors])
                ours = execute(compiled.optimized_plan, self.conn)
                check = cross_check(sql, ours, self.conn)
                self.assertTrue(check.match, f"{check.detail}\nours={ours.rows}\n"
                                             f"sqlite={check.sqlite_rows}")

    def test_unoptimized_plan_matches_sqlite(self):
        for sql in VALID_QUERIES:
            with self.subTest(sql=sql):
                compiled = compile_sql(sql)
                ours = execute(compiled.plan, self.conn)
                self.assertTrue(cross_check(sql, ours, self.conn).match)


class ExecutorBehaviourTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.conn = get_connection()

    def run_sql(self, sql):
        return execute(compile_sql(sql).optimized_plan, self.conn)

    def test_trace_has_one_entry_per_step_with_row_counts(self):
        result = self.run_sql("SELECT title FROM Books WHERE genre = 'Dystopian'")
        self.assertEqual([t.rows_out for t in result.trace], [10, 2, 2])

    def test_empty_result(self):
        self.assertEqual(self.run_sql("SELECT title FROM Books WHERE id > 99").rows, [])

    def test_aggregate_without_group_by_over_no_rows_gives_one_row(self):
        self.assertEqual(self.run_sql("SELECT COUNT(*), MAX(id) FROM Books WHERE id > 99").rows,
                         [(0, None)])

    def test_order_by_desc_and_limit(self):
        self.assertEqual(self.run_sql("SELECT id FROM Books ORDER BY id DESC LIMIT 2").rows,
                         [(10,), (9,)])

    def test_mismatch_is_detected(self):
        fake = ExecutionResult(headers=["id"], rows=[(1,), (2,)])
        match, detail = compare_results(fake, [(1,), (3,)])
        self.assertFalse(match)
        match, _ = compare_results(fake, [(1,)])
        self.assertFalse(match)

    def test_writes_are_impossible_on_the_connection(self):
        with self.assertRaises(Exception):
            self.conn.execute("DELETE FROM Books")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Books").fetchone(), (10,))


class ExpressionSemanticsTests(unittest.TestCase):

    def test_three_valued_logic(self):
        self.assertIs(sql_and(False, lambda: None), False)
        self.assertIsNone(sql_and(True, lambda: None))
        self.assertIs(sql_or(True, lambda: None), True)
        self.assertIsNone(sql_or(False, lambda: None))
        self.assertIsNone(sql_not(None))

    def test_integer_division_truncates_toward_zero(self):
        self.assertEqual(arithmetic("/", 7, 2), 3)
        self.assertEqual(arithmetic("/", -7, 2), -3)
        self.assertIsNone(arithmetic("/", 1, 0))
        self.assertEqual(arithmetic("/", 7, 2.0), 3.5)

    def test_in_list_with_null(self):
        self.assertIs(in_list(1, [1, None]), True)
        self.assertIsNone(in_list(2, [1, None]))
        self.assertIs(in_list(2, [1, 3]), False)

    def test_like_matcher(self):
        self.assertTrue(like_match("The%", "The Hobbit"))
        self.assertTrue(like_match("%HOB%", "The Hobbit"))     # case-insensitive
        self.assertTrue(like_match("_une", "Dune"))
        self.assertTrue(like_match("%", ""))
        self.assertFalse(like_match("_", ""))
        self.assertFalse(like_match("The%", "Dune"))


if __name__ == "__main__":
    unittest.main()

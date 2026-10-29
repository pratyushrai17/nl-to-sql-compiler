"""Boundary cases: empty input, odd formatting, deep nesting, long chains.
The rule for every case: either a correct result or a structured error -
never a Python exception."""

import unittest

from db import get_connection
from parser import MAX_PAREN_DEPTH
from pipeline import compile_sql, run_compiled


class BoundaryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.conn = get_connection()

    def run_ok(self, sql):
        compiled = compile_sql(sql)
        self.assertTrue(compiled.ok, [e.format() for e in compiled.errors])
        run_compiled(compiled, self.conn)
        self.assertTrue(compiled.check.match, compiled.check.detail)
        return compiled.execution.rows

    def first_error(self, sql):
        compiled = compile_sql(sql)
        self.assertFalse(compiled.ok)
        return compiled.errors[0]

    def test_empty_and_blank_input(self):
        for sql in ["", "   \n\t ", "-- only a comment"]:
            with self.subTest(sql=sql):
                err = self.first_error(sql)
                self.assertEqual(err.code, "P001")
                self.assertIn("Expected keyword SELECT but found end of input", err.message)

    def test_empty_result(self):
        self.assertEqual(self.run_ok("SELECT title FROM Books WHERE genre = 'Poetry'"), [])

    def test_mixed_case_keywords_and_identifiers(self):
        self.assertEqual(self.run_ok("SeLeCt TiTlE fRoM bOoKs WhErE iD = 4"), [("The Hobbit",)])

    def test_extra_whitespace_tabs_newlines_and_semicolon(self):
        self.assertEqual(self.run_ok("\n\n  SELECT\ttitle\r\n FROM\n\n Books \t WHERE id=4 ;  \n"),
                         [("The Hobbit",)])

    def test_nested_parentheses_up_to_the_limit(self):
        depth = MAX_PAREN_DEPTH
        sql = "SELECT title FROM Books WHERE " + "(" * depth + "id = 4" + ")" * depth
        self.assertEqual(self.run_ok(sql), [("The Hobbit",)])

    def test_too_deep_nesting_is_an_error_not_a_crash(self):
        sql = "SELECT title FROM Books WHERE " + "(" * 500 + "id = 4" + ")" * 500
        self.assertEqual(self.first_error(sql).code, "P005")

    def test_long_and_or_chain(self):
        terms = " OR ".join(f"id = {i}" for i in range(1, 101))    # 100 terms
        self.assertEqual(len(self.run_ok(f"SELECT title FROM Books WHERE {terms}")), 10)
        mixed = " AND ".join(["id > 0", "id < 100"] * 40) + " OR id = 1"
        self.assertEqual(len(self.run_ok(f"SELECT title FROM Books WHERE {mixed}")), 10)

    def test_overlong_chain_is_an_error_not_a_crash(self):
        terms = " AND ".join("id > 0" for _ in range(1000))
        self.assertEqual(self.first_error(f"SELECT title FROM Books WHERE {terms}").code, "P005")

    def test_unicode_inside_a_string_literal(self):
        self.assertEqual(self.run_ok("SELECT title FROM Books WHERE author = 'Café'"), [])

    def test_unclosed_parenthesis_at_end_of_input(self):
        err = self.first_error("SELECT title FROM Books WHERE (id = 1 OR (id = 2)")
        self.assertEqual(err.code, "P001")
        self.assertIn("end of input", err.message)

    def test_limit_zero(self):
        self.assertEqual(self.run_ok("SELECT title FROM Books LIMIT 0"), [])


if __name__ == "__main__":
    unittest.main()

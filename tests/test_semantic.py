"""Tests for semantic_analyzer.py: name resolution, scope, GROUP BY rule."""

import unittest

from lexer import tokenize
from parser import parse
from semantic_analyzer import analyze


def errors_of(sql):
    return analyze(parse(tokenize(sql)))


def codes(sql):
    return [e.code for e in errors_of(sql)]


class SemanticValidTests(unittest.TestCase):

    def test_valid_queries_have_no_errors(self):
        for sql in [
            "SELECT title, author FROM Books WHERE genre = 'Fantasy'",
            "SELECT genre, COUNT(*) FROM Books GROUP BY genre",
            "SELECT m.name, b.title FROM Members m JOIN Loans l ON m.id = l.member_id "
            "JOIN Books b ON b.id = l.book_id",
            "select TITLE from books where GENRE = 'Fantasy'",   # identifiers are case-insensitive
        ]:
            with self.subTest(sql=sql):
                self.assertEqual(errors_of(sql), [])

    def test_columns_are_annotated_with_their_table(self):
        stmt = parse(tokenize("SELECT title FROM Books b JOIN Loans l ON b.id = l.book_id"))
        self.assertEqual(analyze(stmt), [])
        col = stmt.select_items[0].expr
        self.assertEqual((col.qualifier, col.sql_type), ("b", "TEXT"))


class SemanticNameErrorTests(unittest.TestCase):

    def test_undefined_table_with_suggestion(self):
        errs = errors_of("SELECT * FROM Book")
        self.assertEqual([e.code for e in errs], ["S001"])
        self.assertIn("Did you mean 'Books'", errs[0].message)
        self.assertEqual((errs[0].line, errs[0].col), (1, 15))

    def test_undefined_column_with_suggestion(self):
        errs = errors_of("SELECT titel FROM Books")
        self.assertEqual([e.code for e in errs], ["S002"])
        self.assertIn("Did you mean 'title'", errs[0].message)

    def test_ambiguous_column_across_join(self):
        errs = errors_of("SELECT id FROM Books JOIN Loans ON Books.id = Loans.book_id")
        self.assertEqual([e.code for e in errs], ["S003"])
        self.assertIn("'Books.id' or 'Loans.id'", errs[0].message)

    def test_unknown_qualifier(self):
        self.assertEqual(codes("SELECT x.title FROM Books b"), ["S004"])

    def test_table_name_hidden_by_alias(self):
        # Once Books is aliased as b, "Books.title" is no longer in scope (as in SQLite).
        self.assertEqual(codes("SELECT Books.title FROM Books b"), ["S004"])

    def test_duplicate_alias(self):
        self.assertEqual(codes("SELECT * FROM Books JOIN Books ON 1 = 1"), ["S005"])

    def test_multiple_errors_are_all_reported(self):
        self.assertEqual(codes("SELECT titel, writer FROM Books"), ["S002", "S002"])


class GroupByRuleTests(unittest.TestCase):

    def test_column_missing_from_group_by(self):
        errs = errors_of("SELECT genre, author, COUNT(*) FROM Books GROUP BY genre")
        self.assertEqual([e.code for e in errs], ["S010"])
        self.assertIn("'author'", errs[0].message)

    def test_aggregate_without_group_by(self):
        self.assertEqual(codes("SELECT genre, COUNT(*) FROM Books"), ["S010"])

    def test_qualified_and_unqualified_group_columns_match(self):
        self.assertEqual(codes("SELECT b.genre, COUNT(*) FROM Books b GROUP BY genre"), [])

    def test_star_in_aggregated_query(self):
        self.assertEqual(codes("SELECT * FROM Books GROUP BY genre"), ["S010"])


if __name__ == "__main__":
    unittest.main()

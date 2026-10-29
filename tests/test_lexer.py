"""Tests for lexer.py: token types, values, positions and lexical errors."""

import unittest

from errors import LexError
from lexer import tokenize


def types(sql):
    return [t.type for t in tokenize(sql)]


class LexerTokenTests(unittest.TestCase):

    def test_simple_select(self):
        self.assertEqual(
            types("SELECT title FROM Books WHERE genre = 'Fantasy'"),
            ["SELECT", "IDENT", "FROM", "IDENT", "WHERE", "IDENT", "EQ", "STRING", "EOF"],
        )

    def test_keywords_are_case_insensitive(self):
        tokens = tokenize("sElEcT DiStInCt title fRoM Books")
        self.assertEqual([t.type for t in tokens], ["SELECT", "DISTINCT", "IDENT", "FROM", "IDENT", "EOF"])
        self.assertEqual(tokens[0].value, "sElEcT")   # original spelling is kept

    def test_two_character_operators_use_maximal_munch(self):
        self.assertEqual(types("a <= b >= c <> d != e < f > g"),
                         ["IDENT", "LE", "IDENT", "GE", "IDENT", "NEQ", "IDENT", "NEQ",
                          "IDENT", "LT", "IDENT", "GT", "IDENT", "EOF"])

    def test_numbers_integer_and_decimal(self):
        tokens = tokenize("42 3.25")
        self.assertEqual((tokens[0].value, tokens[1].value), (42, 3.25))
        self.assertIsInstance(tokens[0].value, int)
        self.assertIsInstance(tokens[1].value, float)

    def test_string_with_escaped_quote(self):
        tokens = tokenize("'it''s'")
        self.assertEqual(tokens[0].type, "STRING")
        self.assertEqual(tokens[0].value, "it's")

    def test_quoted_identifier(self):
        tokens = tokenize('SELECT "title" FROM "Books"')
        self.assertEqual([(t.type, t.value) for t in tokens[1:4]],
                         [("IDENT", "title"), ("FROM", "FROM"), ("IDENT", "Books")])

    def test_line_and_column_positions(self):
        tokens = tokenize("SELECT title\n  FROM Books")
        from_tok = tokens[2]
        self.assertEqual((from_tok.type, from_tok.line, from_tok.col), ("FROM", 2, 3))

    def test_comments_and_extra_whitespace_are_skipped(self):
        sql = "  SELECT\t title  -- the title\n\n FROM   Books  "
        self.assertEqual(types(sql), ["SELECT", "IDENT", "FROM", "IDENT", "EOF"])

    def test_punctuation(self):
        self.assertEqual(types("COUNT(*), b.id;"),
                         ["IDENT", "LPAREN", "STAR", "RPAREN", "COMMA", "IDENT", "DOT",
                          "IDENT", "SEMICOLON", "EOF"])


class LexerErrorTests(unittest.TestCase):

    def assertLexError(self, sql, code, line, col):
        with self.assertRaises(LexError) as ctx:
            tokenize(sql)
        err = ctx.exception
        self.assertEqual((err.code, err.line, err.col), (code, line, col))
        return err

    def test_unknown_character(self):
        err = self.assertLexError("SELECT title FROM Books WHERE id @ 3", "L001", 1, 34)
        self.assertIn("'@'", err.message)

    def test_non_ascii_character_is_rejected_cleanly(self):
        self.assertLexError("SELECT id² FROM Books", "L001", 1, 10)

    def test_unterminated_string(self):
        err = self.assertLexError("SELECT title FROM Books\nWHERE genre = 'Fantasy", "L002", 2, 15)
        self.assertIn("closing single quote", err.message)

    def test_unterminated_quoted_identifier(self):
        self.assertLexError('SELECT "title FROM Books', "L003", 1, 8)

    def test_malformed_number(self):
        self.assertLexError("SELECT 12abc FROM Books", "L004", 1, 8)


if __name__ == "__main__":
    unittest.main()

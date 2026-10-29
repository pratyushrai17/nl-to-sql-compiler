"""Tests for repair_loop.py using a fake LLM (no API key, no network)."""

import unittest

from repair_loop import run_pipeline


class FakeLLM:
    """Returns pre-set SQL strings in order and records what it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, question, schema, previous_sql=None, errors=None):
        self.calls.append({"previous_sql": previous_sql, "errors": errors})
        return self.responses.pop(0)


GOOD_SQL = "SELECT title FROM Books WHERE genre = 'Fantasy'"


class RepairLoopErrorFeedTests(unittest.TestCase):
    """All three kinds of compile error must reach the LLM's repair call."""

    def check_repaired(self, bad_sql, expected_code):
        llm = FakeLLM(bad_sql, GOOD_SQL)
        result = run_pipeline("q", generate=llm)
        self.assertTrue(result.success)
        self.assertEqual(len(result.attempts), 2)
        sent = llm.calls[1]
        self.assertEqual(sent["previous_sql"], bad_sql)
        self.assertEqual(sent["errors"][0].code, expected_code)
        self.assertIsNotNone(sent["errors"][0].line)

    def test_lexical_error_is_fed_back(self):
        self.check_repaired("SELECT title FROM Books WHERE genre = 'Fantasy", "L002")

    def test_syntax_error_is_fed_back(self):
        self.check_repaired("SELECT title Books", "P001")

    def test_semantic_error_is_fed_back(self):
        self.check_repaired("SELECT titel FROM Books", "S002")


if __name__ == "__main__":
    unittest.main()

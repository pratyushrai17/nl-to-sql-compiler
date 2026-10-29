"""Tests for repair_loop.py using a fake LLM (no API key, no network)."""

import unittest

from repair_loop import MAX_ATTEMPTS, run_repair_loop


class FakeLLM:
    """Returns pre-set SQL strings in order and records the history it was given."""
    mode = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.histories = []

    def generate(self, question, history):
        self.histories.append([dict(h) for h in history])
        return self.responses[min(len(self.histories) - 1, len(self.responses) - 1)]


GOOD_SQL = "SELECT title FROM Books WHERE genre = 'Fantasy'"


class RepairLoopTests(unittest.TestCase):

    def test_valid_first_attempt_needs_one_call(self):
        llm = FakeLLM(GOOD_SQL)
        result = run_repair_loop("q", llm)
        self.assertTrue(result.success)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(llm.histories, [[]])

    def test_errors_of_all_three_stages_are_fed_back(self):
        for bad_sql, code in [("SELECT title FROM Books WHERE genre = 'Fantasy", "L002"),
                              ("SELECT title Books", "P001"),
                              ("SELECT titel FROM Books", "S002")]:
            with self.subTest(code=code):
                llm = FakeLLM(bad_sql, GOOD_SQL)
                result = run_repair_loop("q", llm)
                self.assertTrue(result.success)
                self.assertEqual(len(result.attempts), 2)
                entry = llm.histories[1][0]
                self.assertEqual((entry["attempt"], entry["sql"]), (1, bad_sql))
                error = entry["errors"][0]
                self.assertEqual(error["code"], code)
                self.assertIsNotNone(error["line"])
                self.assertIn("message", error)

    def test_history_accumulates_and_attempts_are_bounded(self):
        llm = FakeLLM("SELECT a FROM Books", "SELECT b FROM Books", "SELECT c FROM Books",
                      GOOD_SQL)
        result = run_repair_loop("q", llm)
        self.assertFalse(result.success)
        self.assertEqual(MAX_ATTEMPTS, 3)
        self.assertEqual(len(result.attempts), 3)          # the 4th response is never requested
        self.assertEqual([len(h) for h in llm.histories], [0, 1, 2])
        self.assertEqual([h["sql"] for h in llm.histories[2]],
                         ["SELECT a FROM Books", "SELECT b FROM Books"])

    def test_non_select_from_llm_is_rejected_not_executed(self):
        llm = FakeLLM("DELETE FROM Books", GOOD_SQL)
        result = run_repair_loop("q", llm)
        self.assertEqual(result.attempts[0].errors[0].code, "P002")
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()

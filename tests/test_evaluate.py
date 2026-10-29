"""Tests for evaluate.py's measurement logic (offline; the live part uses fakes)."""

import unittest

from evaluate import MUTATIONS, results_match, run_live_evaluation, run_mutation_test
from examples import GOLD_QUESTIONS
from llm_client import StubLLM


class MutationTestTests(unittest.TestCase):

    def test_every_mutant_is_detected_with_its_expected_code(self):
        stats, undetected = run_mutation_test()
        self.assertEqual(undetected, [])
        for name, *_ in MUTATIONS:
            with self.subTest(operator=name):
                self.assertGreater(stats[name]["applied"], 0)
                self.assertEqual(stats[name]["expected_code"], stats[name]["applied"])


class LiveEvaluationLogicTests(unittest.TestCase):
    """The scoring code is exercised with offline LLMs, so it is tested even
    though the real API is not called here."""

    def test_stub_answers_score_as_matching_the_gold_sql(self):
        rows = run_live_evaluation(StubLLM(), GOLD_QUESTIONS[:10])
        self.assertTrue(all(r["final_valid"] and r["match"] for r in rows))
        self.assertEqual(sum(not r["first_valid"] for r in rows), 2)   # the two repair demos

    def test_valid_but_wrong_sql_is_not_a_match(self):
        class WrongLLM:
            def generate(self, question, history):
                return "SELECT title FROM Books WHERE id = 1"
        rows = run_live_evaluation(WrongLLM(), GOLD_QUESTIONS[:1])
        self.assertTrue(rows[0]["final_valid"])
        self.assertFalse(rows[0]["match"])

    def test_unknown_questions_are_recorded_as_errors_not_crashes(self):
        rows = run_live_evaluation(StubLLM(), [("Unknown question?", "SELECT id FROM Books")])
        self.assertIsNotNone(rows[0]["error"])

    def test_results_match_ignores_row_order(self):
        self.assertTrue(results_match([(1,), (2,)], [(2,), (1,)]))
        self.assertFalse(results_match([(1,)], [(1,), (1,)]))


if __name__ == "__main__":
    unittest.main()

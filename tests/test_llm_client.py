"""Tests for llm_client.py that need no API key and make no network calls."""

import os
import unittest
from unittest import mock

import llm_client
from examples import BUILTIN_QUESTIONS
from llm_client import StubLLM, UnknownQuestionError, build_user_message, extract_sql, get_llm
from repair_loop import run_repair_loop


class StubLLMTests(unittest.TestCase):

    def test_ten_builtin_questions_two_of_which_need_repair(self):
        self.assertEqual(len(BUILTIN_QUESTIONS), 10)
        self.assertEqual(sum(1 for q in BUILTIN_QUESTIONS if q.get("repair")), 2)

    def test_every_builtin_question_succeeds(self):
        for q in BUILTIN_QUESTIONS:
            with self.subTest(question=q["question"]):
                result = run_repair_loop(q["question"], StubLLM())
                self.assertTrue(result.success)
                self.assertEqual(len(result.attempts), 2 if q.get("repair") else 1)

    def test_question_matching_ignores_case_and_punctuation(self):
        self.assertTrue(StubLLM().knows("how many books are there in each genre"))

    def test_unknown_question_points_to_sql_mode(self):
        with self.assertRaises(UnknownQuestionError) as ctx:
            StubLLM().generate("What is the weather?", [])
        self.assertIn("--sql", str(ctx.exception))


class ModeSelectionTests(unittest.TestCase):

    def test_no_key_means_stub_mode_with_notice(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            llm, notice = get_llm()
            self.assertEqual(llm.mode, "stub")
            self.assertIn("STUB MODE", notice)
            llm, notice = get_llm("live")
            self.assertEqual(llm.mode, "stub")
            self.assertIn("falling back", notice)

    def test_stub_can_be_forced_even_with_a_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "placeholder-not-a-real-key"}):
            llm, _ = get_llm("stub")
            self.assertEqual(llm.mode, "stub")

    def test_default_model(self):
        self.assertEqual(llm_client.DEFAULT_MODEL, "claude-sonnet-5-5")


class PromptTests(unittest.TestCase):

    def test_repair_prompt_contains_structured_history(self):
        history = [{"attempt": 1, "sql": "SELECT titel FROM Books", "errors": [
            {"stage": "semantic", "code": "S002", "message": "Column 'titel' ...",
             "line": 1, "col": 8}]}]
        text = build_user_message("Q?", history)
        self.assertIn("Attempt 1:", text)
        self.assertIn("[S002] semantic error (line 1, col 8)", text)
        self.assertEqual(build_user_message("Q?", []), "Question: Q?")

    def test_extract_sql_strips_fences(self):
        self.assertEqual(extract_sql("```sql\nSELECT 1 FROM Books;\n```"), "SELECT 1 FROM Books")


if __name__ == "__main__":
    unittest.main()

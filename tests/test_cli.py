"""End-to-end tests of main.py, run as a subprocess with no API key set."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_main(*args, stdin=None):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    proc = subprocess.run([sys.executable, "main.py", *args], cwd=ROOT, env=env, input=stdin,
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout + proc.stderr


class SqlModeTests(unittest.TestCase):

    def test_valid_query_prints_every_stage(self):
        code, out = run_main("--sql", "SELECT title FROM Books WHERE genre = 'Dystopian'")
        self.assertEqual(code, 0, out)
        for heading in ["[1] Lexical analysis", "[2] Syntax analysis", "[3] Semantic analysis",
                        "[4] Intermediate code", "[5] Optimization", "[6] Execution",
                        "[7] Cross-check vs SQLite"]:
            self.assertIn(heading, out)
        self.assertIn("Brave New World", out)
        self.assertIn("MATCH", out)
        self.assertNotIn("Traceback", out)

    def test_broken_queries_show_structured_errors_without_crashing(self):
        cases = [("SELECT title FROM Books WHERE id @ 1", "[L001]"),
                 ("SELECT title FROM Books WHERE", "[P001]"),
                 ("DELETE FROM Books", "[P002]"),
                 ("SELECT genre, author, COUNT(*) FROM Books GROUP BY genre", "[S010]")]
        for sql, code_text in cases:
            with self.subTest(sql=sql):
                code, out = run_main("--sql", sql)
                self.assertEqual(code, 1)
                self.assertIn(code_text, out)
                self.assertIn("line 1, col", out)
                self.assertIn("^", out)
                self.assertNotIn("Traceback", out)


class StubModeTests(unittest.TestCase):

    def test_list(self):
        code, out = run_main("--list")
        self.assertEqual(code, 0)
        self.assertIn("10. ", out)

    def test_demo_with_repair_shows_stub_notice(self):
        code, out = run_main("--demo", "3")
        self.assertEqual(code, 0, out)
        self.assertIn("STUB MODE", out)
        self.assertIn("Attempt 2", out)
        self.assertIn("[S010]", out)

    def test_demo_out_of_range(self):
        code, out = run_main("--demo", "42")
        self.assertEqual(code, 2)
        self.assertIn("choose 1-10", out)

    def test_interactive_mode(self):
        code, out = run_main("-i", stdin="sql> SELECT COUNT(*) FROM Books\n"
                                          "Tell me a joke\n:quit\n")
        self.assertEqual(code, 0)
        self.assertIn("[7] Cross-check vs SQLite", out)
        self.assertIn("only knows the 10 built-in questions", out)
        self.assertNotIn("Traceback", out)

    def test_no_arguments_runs_all_examples(self):
        code, out = run_main()
        self.assertEqual(code, 0, out[-2000:])
        self.assertIn("10 of 10 examples succeeded", out)


if __name__ == "__main__":
    unittest.main()

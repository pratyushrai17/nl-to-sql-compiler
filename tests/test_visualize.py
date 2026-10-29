"""Tests for visualize.py: the HTML is self-contained and contains every section."""

import unittest

from db import get_connection
from visualize import build_page


class VisualizeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        conn = get_connection()
        cls.page = build_page(conn)
        conn.close()

    def test_page_is_self_contained(self):
        self.assertNotIn("<script", self.page)
        self.assertNotIn("http://", self.page)
        self.assertNotIn("https://", self.page)

    def test_page_has_all_sections(self):
        for text in ["Example 10", "Query plan (before optimization)", "Optimized plan",
                     "Per-stage timings", "Attempt 2", "[S010]", "[S003]", "[L002]", "[P002]"]:
            self.assertIn(text, self.page)

    def test_every_cross_check_matches(self):
        self.assertEqual(self.page.count("MATCH</span>"), 13)
        self.assertNotIn("MISMATCH", self.page)


if __name__ == "__main__":
    unittest.main()

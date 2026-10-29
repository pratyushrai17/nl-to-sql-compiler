"""Tests for query_plan.py: operator order, step numbering, star expansion."""

import unittest

from pipeline import compile_sql


def plan_of(sql):
    result = compile_sql(sql)
    assert result.ok, [e.format() for e in result.errors]
    return result.plan


class PlannerTests(unittest.TestCase):

    def test_simple_filter_plan_text(self):
        plan = plan_of("SELECT title FROM Books WHERE genre = 'Dystopian'")
        self.assertEqual(str(plan).splitlines(), [
            "t1 = SCAN Books",
            "t2 = FILTER t1 WHERE Books.genre = 'Dystopian'",
            "t3 = PROJECT t2 [Books.title]",
        ])

    def test_every_operator_in_order(self):
        plan = plan_of("SELECT DISTINCT b.genre, COUNT(*) AS n FROM Books b "
                       "JOIN Loans l ON b.id = l.book_id WHERE l.id > 0 GROUP BY b.genre "
                       "HAVING COUNT(*) > 0 ORDER BY n DESC LIMIT 2")
        self.assertEqual(plan.ops(), ["SCAN", "SCAN", "JOIN", "FILTER", "AGGREGATE",
                                      "HAVING", "SORT", "PROJECT", "DISTINCT", "LIMIT"])

    def test_steps_only_reference_earlier_temporaries(self):
        plan = plan_of("SELECT m.name FROM Members m JOIN Loans l ON m.id = l.member_id "
                       "JOIN Books b ON b.id = l.book_id")
        for step in plan.steps():
            self.assertTrue(all(i < step.id for i in step.inputs), str(step))
        self.assertEqual(plan.ops(), ["SCAN", "SCAN", "JOIN", "SCAN", "JOIN", "PROJECT"])

    def test_order_by_alias_is_replaced_by_the_expression(self):
        plan = plan_of("SELECT genre, COUNT(*) AS n FROM Books GROUP BY genre ORDER BY n DESC")
        sort = [s for s in plan.steps() if s.node.op == "SORT"][0]
        self.assertIn("BY [COUNT(*) DESC]", str(sort))

    def test_aggregates_are_computed_once(self):
        plan = plan_of("SELECT genre, COUNT(*) FROM Books GROUP BY genre "
                       "HAVING COUNT(*) > 1 ORDER BY COUNT(*)")
        agg = [s.node for s in plan.steps() if s.node.op == "AGGREGATE"][0]
        self.assertEqual(len(agg.aggregates), 1)

    def test_star_expansion_and_headers(self):
        plan = plan_of("SELECT b.*, l.due_date FROM Books b JOIN Loans l ON b.id = l.book_id")
        self.assertEqual(plan.root.headers, ["id", "title", "author", "genre", "due_date"])


if __name__ == "__main__":
    unittest.main()

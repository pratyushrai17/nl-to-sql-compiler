"""One test per query in examples.VALID_QUERIES (generated below), so every
valid query is counted and reported individually.

Each test: the query must compile with no errors, the optimized plan must
run in our interpreter, and the result must match SQLite.
"""

import unittest

from db import get_connection
from examples import VALID_QUERIES
from pipeline import compile_sql, run_compiled


class ValidQueryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.conn = get_connection()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def check_query(self, sql):
        compiled = compile_sql(sql)
        self.assertTrue(compiled.ok, [e.format() for e in compiled.errors])
        run_compiled(compiled, self.conn)
        self.assertTrue(compiled.check.match,
                        f"{compiled.check.detail}\nours={compiled.execution.rows}\n"
                        f"sqlite={compiled.check.sqlite_rows}")


def _make_test(sql):
    def test(self):
        self.check_query(sql)
    test.__doc__ = " ".join(sql.split())[:100]
    return test


for _number, _sql in enumerate(VALID_QUERIES, start=1):
    setattr(ValidQueryTests, f"test_valid_query_{_number:02d}", _make_test(_sql))


if __name__ == "__main__":
    unittest.main()

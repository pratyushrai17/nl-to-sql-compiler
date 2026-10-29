"""
db.py

In-memory SQLite database seeded with sample data matching
symbol_table.SCHEMA.

SQLite plays two roles in this project:
  1. Storage: the plan interpreter (executor.py) reads base-table rows from
     it with load_table().
  2. Reference implementation: after our interpreter runs a query, the
     original SQL is also run by SQLite (run_query) and the two results are
     compared, so any interpreter bug shows up as a mismatch.

Safety: after seeding, the connection is switched to read-only with
PRAGMA query_only, and run_query() refuses any text that does not start
with SELECT. Only SQL that already passed our own validation reaches it.
"""

import sqlite3

from lexer import tokenize
from symbol_table import SCHEMA, get_columns

_BOOKS = [
    (1, "1984", "George Orwell", "Dystopian"),
    (2, "Animal Farm", "George Orwell", "Satire"),
    (3, "Brave New World", "Aldous Huxley", "Dystopian"),
    (4, "The Hobbit", "J.R.R. Tolkien", "Fantasy"),
    (5, "The Fellowship of the Ring", "J.R.R. Tolkien", "Fantasy"),
    (6, "Dune", "Frank Herbert", "Science Fiction"),
    (7, "Foundation", "Isaac Asimov", "Science Fiction"),
    (8, "Pride and Prejudice", "Jane Austen", "Romance"),
    (9, "Emma", "Jane Austen", "Romance"),
    (10, "The Silmarillion", "J.R.R. Tolkien", "Fantasy"),
]

_MEMBERS = [
    (1, "Alice Rao", "2023-01-10"),
    (2, "Ben Chen", "2023-03-22"),
    (3, "Carla Diaz", "2024-02-14"),
    (4, "Dev Patel", "2024-07-01"),
    (5, "Eva Novak", "2024-09-12"),     # has no loans
]

_LOANS = [
    (1, 1, 1, "2024-05-01"),
    (2, 4, 2, "2024-05-10"),
    (3, 6, 1, "2024-06-01"),
    (4, 8, 3, "2024-06-15"),
    (5, 6, 2, "2024-06-20"),
    (6, 3, 1, "2024-07-05"),
    (7, 10, 4, "2024-07-15"),
    (8, 1, 3, "2024-08-01"),
]


def get_connection() -> sqlite3.Connection:
    """Create a fresh in-memory DB, create the schema, seed it, and make the
    connection read-only."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE Books (id INTEGER PRIMARY KEY, title TEXT, author TEXT, genre TEXT)")
    cur.execute("CREATE TABLE Members (id INTEGER PRIMARY KEY, name TEXT, joined_on TEXT)")
    cur.execute("CREATE TABLE Loans (id INTEGER PRIMARY KEY, book_id INTEGER, "
                "member_id INTEGER, due_date TEXT)")
    cur.executemany("INSERT INTO Books VALUES (?, ?, ?, ?)", _BOOKS)
    cur.executemany("INSERT INTO Members VALUES (?, ?, ?)", _MEMBERS)
    cur.executemany("INSERT INTO Loans VALUES (?, ?, ?, ?)", _LOANS)
    conn.commit()
    cur.execute("PRAGMA query_only = ON")   # from here on, any write is refused by SQLite
    return conn


def load_table(conn: sqlite3.Connection, table: str):
    """All rows of a base table, as tuples in schema column order.
    `table` must be a schema table name (it is never user text)."""
    if table not in SCHEMA:
        raise ValueError(f"unknown table {table!r}")
    columns = ", ".join(get_columns(table))
    return conn.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall()


def run_query(conn: sqlite3.Connection, sql: str):
    """Run an already-validated SELECT in SQLite; returns (column_names, rows)."""
    if tokenize(sql)[0].type != "SELECT":
        raise ValueError("run_query only executes SELECT statements")
    cur = conn.execute(sql)
    columns = [d[0] for d in cur.description] if cur.description else []
    return columns, cur.fetchall()

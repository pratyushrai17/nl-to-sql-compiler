"""
examples.py

Data shared by the demo, the tests and the evaluation. No logic here.

VALID_QUERIES  a corpus of valid SQL covering every supported feature. Used by
               the interpreter-vs-SQLite differential tests and as the seed
               queries for the mutation test in evaluate.py.
"""

VALID_QUERIES = [
    # -- single table, WHERE, boolean logic ---------------------------------------
    "SELECT * FROM Books",
    "SELECT title, author FROM Books WHERE genre = 'Dystopian'",
    "SELECT title FROM Books WHERE genre = 'Fantasy' OR genre = 'Satire'",
    "SELECT title FROM Books WHERE (genre = 'Fantasy' OR genre = 'Romance') AND id > 4",
    "SELECT title FROM Books WHERE NOT genre = 'Fantasy' AND id <= 5",
    "SELECT title FROM Books WHERE id != 3 AND id <> 4",
    "SELECT title FROM Books WHERE id >= 2 AND id < 6",
    "SELECT title FROM Books WHERE genre = 'Poetry'",                       # empty result
    "select TITLE, Author from books where GENRE = 'Romance'",              # mixed case
    "SELECT   title\n  FROM Books\n WHERE   id  =  1  ",                    # extra whitespace
    "SELECT title FROM Books WHERE ((((id = 1)) OR ((id = 2))))",           # nested parentheses
    "SELECT title FROM Books WHERE id = 1 OR id = 2 OR id = 3 OR id = 4 OR id = 5 "
    "AND genre = 'Fantasy' OR id = 9",                                      # long AND/OR chain
    "SELECT title FROM Books WHERE 1 = 1 AND id > 2 * 3",                   # constant folding
    "SELECT title FROM Books WHERE TRUE",
    # -- LIKE, IN, BETWEEN ------------------------------------------------------------------
    "SELECT title FROM Books WHERE title LIKE 'The%'",
    "SELECT title FROM Books WHERE title LIKE '%of%'",
    "SELECT title FROM Books WHERE title LIKE 'the h_bbit'",               # LIKE is case-insensitive
    "SELECT title FROM Books WHERE title NOT LIKE '%e%'",
    "SELECT title, genre FROM Books WHERE genre IN ('Fantasy', 'Satire', 'Romance')",
    "SELECT title FROM Books WHERE id NOT IN (1, 2, 3)",
    "SELECT id, due_date FROM Loans WHERE due_date BETWEEN '2024-06-01' AND '2024-06-30'",
    "SELECT id FROM Loans WHERE id NOT BETWEEN 2 AND 6",
    # -- expressions, aliases, DISTINCT, ORDER BY, LIMIT ----------------------------------
    "SELECT id, id * 2 AS double_id, id - 1, -id FROM Books",
    "SELECT id / 2, id / 2.0 FROM Loans",                                   # integer vs real division
    "SELECT title AS t, author AS a FROM Books AS b WHERE b.genre = 'Fantasy'",
    "SELECT DISTINCT genre FROM Books",
    "SELECT DISTINCT author FROM Books ORDER BY author DESC",
    "SELECT title, id FROM Books ORDER BY genre, title DESC",
    "SELECT title FROM Books ORDER BY id DESC LIMIT 3",
    "SELECT title FROM Books ORDER BY title LIMIT 0",
    "SELECT * FROM Loans LIMIT 2",
    "SELECT genre FROM Books ORDER BY genre LIMIT 2",                       # LIMIT cuts tied rows
    # -- aggregates, GROUP BY, HAVING -----------------------------------------------------
    "SELECT COUNT(*) FROM Books",
    "SELECT COUNT(*), MIN(due_date), MAX(due_date) FROM Loans",
    "SELECT SUM(id), AVG(id) FROM Books",
    "SELECT COUNT(DISTINCT author), COUNT(author) FROM Books",
    "SELECT MAX(id), SUM(id), COUNT(*) FROM Books WHERE id > 100",          # aggregates over no rows
    "SELECT genre, COUNT(*) FROM Books GROUP BY genre",
    "SELECT genre, COUNT(*) AS n FROM Books GROUP BY genre ORDER BY n DESC, genre",
    "SELECT author, COUNT(*) FROM Books GROUP BY author HAVING COUNT(*) > 1",
    "SELECT genre, MIN(title) FROM Books GROUP BY genre HAVING genre <> 'Satire' ORDER BY genre",
    "SELECT genre, COUNT(*) FROM Books WHERE id > 100 GROUP BY genre",     # GROUP BY over no rows
    "SELECT genre, author, COUNT(*) FROM Books GROUP BY genre, author ORDER BY genre, author",
    "SELECT COUNT(*) * 10 + 1 FROM Loans",
    # -- joins --------------------------------------------------------------------------------
    "SELECT m.name, l.due_date FROM Members m JOIN Loans l ON m.id = l.member_id",
    "SELECT Members.name, Books.title FROM Members JOIN Loans ON Members.id = Loans.member_id "
    "JOIN Books ON Books.id = Loans.book_id ORDER BY Members.name, Books.title",
    "SELECT m.name, b.title FROM Members AS m JOIN Loans AS l ON m.id = l.member_id "
    "JOIN Books AS b ON l.book_id = b.id WHERE b.genre = 'Science Fiction' "
    "AND m.joined_on < '2024-01-01'",
    "SELECT m.name, COUNT(*) AS loans FROM Members m JOIN Loans l ON m.id = l.member_id "
    "GROUP BY m.name HAVING COUNT(*) >= 2 ORDER BY loans DESC",
    "SELECT b.genre, COUNT(DISTINCT l.member_id) FROM Books b JOIN Loans l ON b.id = l.book_id "
    "GROUP BY b.genre",
    "SELECT b.*, l.due_date FROM Books b JOIN Loans l ON b.id = l.book_id WHERE l.id < 3",
    "SELECT a.title, c.title FROM Books a JOIN Books c ON a.author = c.author AND a.id < c.id",
    "SELECT m.name FROM Members m JOIN Loans l ON m.id = l.member_id WHERE l.id > 100",  # empty join
]

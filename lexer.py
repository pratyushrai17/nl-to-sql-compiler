"""
lexer.py

Hand-written lexical analyzer (scanner): SQL source text -> list of Tokens.

How it works
------------
The scanner walks the input one character at a time, keeping track of the
current line and column. At each position it looks at the current character
and decides which kind of token starts there:

    whitespace        skipped (newlines advance the line counter)
    -- comment        skipped up to the end of the line
    letter or _       identifier or keyword (keywords are case-insensitive)
    digit             number literal: 42 or 3.14
    '                 string literal; '' inside a string is an escaped quote
    "                 quoted identifier, e.g. "Books"
    < > ! =           one- or two-character comparison operators
    , . ( ) * + - / ; single-character punctuation

Anything else is a lexical error. Each token records where it started
(line, col), which later stages use for error messages.

This is effectively a hand-coded finite automaton: each branch below is
one group of states, and "maximal munch" is applied for <=, >=, <>, !=.

Lexical errors (see errors.py / docs/GRAMMAR.md):
    L001  unexpected / unknown character
    L002  unterminated string literal
    L003  unterminated quoted identifier
    L004  malformed number (e.g. 12abc)
"""

from errors import LexError

# Words with a special meaning in our grammar. Matched case-insensitively;
# the token type is the upper-case word itself (e.g. "SELECT").
KEYWORDS = {
    "SELECT", "DISTINCT", "FROM", "WHERE", "GROUP", "BY", "HAVING", "ORDER",
    "ASC", "DESC", "LIMIT", "AS", "JOIN", "INNER", "ON",
    "AND", "OR", "NOT", "LIKE", "IN", "BETWEEN", "TRUE", "FALSE",
}

# Words we do NOT support, but recognize so the parser can give a clear
# message instead of a confusing "expected X" error.
UNSUPPORTED_KEYWORDS = {
    # other statement types - rejected with "only SELECT is supported"
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "REPLACE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "WITH",
    # SELECT features outside this project's grammar
    "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "NATURAL",
    "UNION", "INTERSECT", "EXCEPT", "OFFSET",
}

# Single-character tokens.
SINGLE_CHAR_TOKENS = {
    ",": "COMMA", ".": "DOT", "(": "LPAREN", ")": "RPAREN", "*": "STAR",
    "+": "PLUS", "-": "MINUS", "/": "SLASH", ";": "SEMICOLON", "=": "EQ",
}


def is_digit(ch):
    return "0" <= ch <= "9"


def is_ident_start(ch):
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch == "_"


def is_ident_char(ch):
    return is_ident_start(ch) or is_digit(ch)


class Token:
    """One lexical unit.

    type   token class: a keyword ("SELECT"), "IDENT", "NUMBER", "STRING",
           an operator ("LE", "NEQ", ...), punctuation ("COMMA") or "EOF"
    value  the identifier/keyword text as written, the number as int/float,
           or the string's contents with quotes removed
    line, col  1-based start position in the source
    """

    def __init__(self, type_, value, line, col):
        self.type = type_
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.type}, {self.value!r}, {self.line}:{self.col})"

    def __eq__(self, other):
        return isinstance(other, Token) and (self.type, self.value) == (other.type, other.value)


def describe(token):
    """How a token is named in error messages: "keyword FROM",
    "identifier 'Books'", "end of input", ..."""
    if token.type == "EOF":
        return "end of input"
    if token.type == "IDENT":
        return f"identifier '{token.value}'"
    if token.type == "STRING":
        return f"string '{token.value}'"
    if token.type == "NUMBER":
        return f"number {token.value}"
    if token.type in KEYWORDS or token.type in UNSUPPORTED_KEYWORDS:
        return f"keyword {token.type}"
    return f"'{token.value}'"


class Lexer:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0       # index into src
        self.line = 1
        self.col = 1

    # -- character helpers ------------------------------------------------------

    def peek(self, offset=0):
        """The character `offset` places ahead, or '' at end of input."""
        i = self.pos + offset
        return self.src[i] if i < len(self.src) else ""

    def advance(self):
        """Consume one character, keeping line/col up to date."""
        ch = self.src[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    # -- main loop ----------------------------------------------------------------

    def tokenize(self):
        tokens = []
        while True:
            self.skip_whitespace_and_comments()
            if self.pos >= len(self.src):
                tokens.append(Token("EOF", None, self.line, self.col))
                return tokens
            tokens.append(self.next_token())

    def skip_whitespace_and_comments(self):
        while self.pos < len(self.src):
            ch = self.peek()
            if ch in " \t\r\n":
                self.advance()
            elif ch == "-" and self.peek(1) == "-":
                while self.pos < len(self.src) and self.peek() != "\n":
                    self.advance()
            else:
                return

    def next_token(self):
        ch = self.peek()
        line, col = self.line, self.col

        if is_ident_start(ch):
            return self.read_word(line, col)
        if is_digit(ch):
            return self.read_number(line, col)
        if ch == "'":
            return self.read_string(line, col)
        if ch == '"':
            return self.read_quoted_identifier(line, col)

        # Two-character operators first ("maximal munch"): <= >= <> !=
        two = ch + self.peek(1)
        if two in ("<=", ">=", "<>", "!="):
            self.advance()
            self.advance()
            type_ = {"<=": "LE", ">=": "GE", "<>": "NEQ", "!=": "NEQ"}[two]
            return Token(type_, two, line, col)
        if ch == "<":
            self.advance()
            return Token("LT", ch, line, col)
        if ch == ">":
            self.advance()
            return Token("GT", ch, line, col)
        if ch in SINGLE_CHAR_TOKENS:
            self.advance()
            return Token(SINGLE_CHAR_TOKENS[ch], ch, line, col)

        raise LexError(
            "L001",
            f"Unexpected character {ch!r}; it is not part of the supported SQL syntax",
            line, col,
        )

    # -- token readers ----------------------------------------------------------------

    def read_word(self, line, col):
        start = self.pos
        while is_ident_char(self.peek()):
            self.advance()
        text = self.src[start:self.pos]
        upper = text.upper()
        if upper in KEYWORDS or upper in UNSUPPORTED_KEYWORDS:
            return Token(upper, text, line, col)
        return Token("IDENT", text, line, col)

    def read_number(self, line, col):
        start = self.pos
        while is_digit(self.peek()):
            self.advance()
        is_decimal = False
        if self.peek() == "." and is_digit(self.peek(1)):
            is_decimal = True
            self.advance()   # the '.'
            while is_digit(self.peek()):
                self.advance()
        if is_ident_start(self.peek()):
            raise LexError(
                "L004",
                f"Malformed number '{self.src[start:self.pos]}{self.peek()}...': a number "
                f"cannot be followed directly by a letter",
                line, col,
            )
        text = self.src[start:self.pos]
        return Token("NUMBER", float(text) if is_decimal else int(text), line, col)

    def read_string(self, line, col):
        self.advance()   # opening quote
        chars = []
        while True:
            if self.pos >= len(self.src):
                raise LexError(
                    "L002",
                    "Unterminated string literal: add the closing single quote (')",
                    line, col,
                )
            ch = self.advance()
            if ch == "'":
                if self.peek() == "'":       # '' is an escaped quote inside a string
                    self.advance()
                    chars.append("'")
                    continue
                return Token("STRING", "".join(chars), line, col)
            chars.append(ch)

    def read_quoted_identifier(self, line, col):
        self.advance()   # opening "
        start = self.pos
        while self.peek() != '"':
            if self.pos >= len(self.src):
                raise LexError(
                    "L003",
                    'Unterminated quoted identifier: add the closing double quote (")',
                    line, col,
                )
            self.advance()
        name = self.src[start:self.pos]
        self.advance()   # closing "
        return Token("IDENT", name, line, col)


def tokenize(source: str):
    """Turn a SQL string into a list of Tokens ending with an EOF token.
    Raises LexError on the first lexical error."""
    return Lexer(source).tokenize()

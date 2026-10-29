"""
visualize.py

Runs the offline examples through the real pipeline and renders everything
into ONE self-contained HTML file, demo_output.html (inline CSS, no
JavaScript, no external files - open it by double-clicking).

For each built-in English question (always with the stub LLM, so no API key
is needed) the page shows:
  * every repair attempt side by side, with its tokens and structured errors
  * the AST of the final query as a connected-box tree
  * the query plan before and after optimization, side by side, and the
    optimizer passes that fired
  * the interpreter's step-by-step row counts, the result table and the
    SQLite cross-check
  * per-stage timings as a small bar chart

A second section shows direct-SQL examples (the --sql mode), including one
lexical, one syntax and one semantic error.

Run with:  python visualize.py            (add --no-open to skip opening a browser)
"""

import html
import sys
import webbrowser
from pathlib import Path

from ast_nodes import statement_tree
from db import get_connection
from errors import caret_line
from examples import BUILTIN_QUESTIONS
from llm_client import StubLLM
from pipeline import compile_sql, run_compiled
from repair_loop import run_repair_loop

OUTPUT_PATH = Path(__file__).resolve().parent / "demo_output.html"

STAGE_LABELS = [("lex", "Lexer"), ("parse", "Parser"), ("semantic", "Semantic"),
                ("plan", "Planner"), ("optimize", "Optimizer"), ("execute", "Interpreter"),
                ("sqlite_check", "SQLite check")]

SHOWCASE_SQL = [
    ("Constant folding",
     "SELECT title FROM Books WHERE 1 = 1 AND id > 2 * 3"),
    ("Predicate pushdown across two joins",
     "SELECT m.name, b.title FROM Members m JOIN Loans l ON m.id = l.member_id "
     "JOIN Books b ON l.book_id = b.id WHERE b.genre = 'Fantasy' AND m.id + b.id > 2"),
    ("Redundant projection removal", "SELECT * FROM Loans LIMIT 3"),
    ("Lexical error", "SELECT title FROM Books WHERE genre = 'Fantasy"),
    ("Syntax error", "SELECT title, FROM Books"),
    ("Non-SELECT statement is rejected", "DROP TABLE Books"),
    ("Semantic errors", "SELECT titel, COUNT(*) FROM Books WHERE title = 1984"),
]


def esc(value):
    return html.escape(str(value), quote=True)


# -- small renderers -------------------------------------------------------------------

def render_tree(node):
    def li(n):
        kids = "".join(li(c) for c in n["children"])
        return f"<li><span class='tree-node'>{esc(n['label'])}</span>" + \
               (f"<ul>{kids}</ul>" if kids else "") + "</li>"
    return f"<div class='tree-wrap'><ul class='tree'>{li(node)}</ul></div>"


def render_tokens(tokens):
    if tokens is None:
        return "<p class='muted'>(the lexer stopped before producing tokens)</p>"
    boxes = "".join(
        f"<div class='token'><div class='t-type'>{esc(t.type)}</div>"
        f"<div class='t-val'>{esc(t.value)}</div></div>"
        for t in tokens if t.type != "EOF")
    return f"<div class='tokens'>{boxes}</div>"


def render_errors(compiled):
    if compiled.ok:
        return "<div class='status ok'>&#10003; Valid</div>"
    items = []
    for e in compiled.errors:
        marker = caret_line(compiled.sql, e.line, e.col)
        items.append(f"<li><b>[{esc(e.code)}]</b> {esc(e.stage)} error"
                     f"{esc(e.location())}: {esc(e.message)}"
                     + (f"<pre class='caret'>{esc(marker)}</pre>" if marker else "") + "</li>")
    return f"<div class='status fail'>&#10007; Invalid<ul>{''.join(items)}</ul></div>"


def render_plan(plan):
    return "<pre class='plan'>" + esc(plan) + "</pre>"


def render_table(headers, rows):
    if not rows:
        return "<p class='muted'>(no rows)</p>"
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc('NULL' if v is None else v)}</td>" for v in r)
                   + "</tr>" for r in rows)
    return f"<table class='results'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_timings(timings):
    present = [(key, label, timings[key]) for key, label in STAGE_LABELS if key in timings]
    longest = max((ms for _, _, ms in present), default=1) or 1
    rows = "".join(
        f"<div class='bar-row'><span class='bar-label'>{esc(label)}</span>"
        f"<span class='bar' style='width:{max(2, 100 * ms / longest):.0f}%'></span>"
        f"<span class='bar-ms'>{ms:.3f} ms</span></div>"
        for _, label, ms in present)
    return f"<div class='bars'>{rows}</div>"


def render_compiled(compiled):
    """AST, plans, passes, execution and cross-check for a valid query."""
    opt = compiled.optimization
    passes = "".join(
        f"<li><b>{esc(p.name)}</b>: " + ("<span class='fired'>fired</span>" if p.fired
                                          else "<span class='muted'>no change</span>")
        + "".join(f"<div class='note'>{esc(n)}</div>" for n in p.notes) + "</li>"
        for p in opt.passes)
    trace = "".join(f"<tr><td><code>{esc(s.step)}</code></td><td>{s.rows_out}</td></tr>"
                    for s in compiled.execution.trace)
    check = compiled.check
    badge = ("<span class='badge ok'>MATCH</span>" if check.match
             else "<span class='badge fail'>MISMATCH - BUG</span>")
    return f"""
      <div class='label'>Abstract syntax tree</div>
      {render_tree(statement_tree(compiled.ast))}
      <div class='two-col'>
        <div><div class='label'>Query plan (before optimization)</div>{render_plan(opt.before)}</div>
        <div><div class='label'>Optimized plan</div>{render_plan(opt.after)}</div>
      </div>
      <div class='label'>Optimizer passes</div><ul class='passes'>{passes}</ul>
      <div class='two-col'>
        <div><div class='label'>Interpreter trace</div>
          <table class='results trace'><thead><tr><th>step</th><th>rows out</th></tr></thead>
          <tbody>{trace}</tbody></table></div>
        <div><div class='label'>Per-stage timings</div>{render_timings(compiled.timings)}</div>
      </div>
      <div class='label'>Result (plan interpreter)</div>
      {render_table(compiled.execution.headers, compiled.execution.rows)}
      <p class='check'>Cross-check vs SQLite: {badge} <span class='muted'>{esc(check.detail)}</span></p>
    """


# -- sections -------------------------------------------------------------------------

def render_example(n, entry, conn):
    question = entry["question"]
    result = run_repair_loop(question, StubLLM())
    cards = "".join(f"""
      <div class='card'>
        <div class='card-head'>Attempt {a.number} <span class='muted'>(stub LLM, pre-written)</span></div>
        <pre class='sql'>{esc(a.sql)}</pre>
        <div class='label'>Tokens</div>{render_tokens(a.compiled.tokens)}
        <div class='label'>Validation</div>{render_errors(a.compiled)}
        {"<p class='muted'>&rarr; errors (code, message, line/col) sent back to the LLM</p>"
         if not a.valid else ""}
      </div>""" for a in result.attempts)

    body = "<p class='muted'>No valid SQL was produced.</p>"
    if result.success:
        compiled = run_compiled(compile_sql(result.final.sql), conn)
        body = render_compiled(compiled)
    status = "<span class='badge ok'>SUCCESS</span>" if result.success else \
        "<span class='badge fail'>FAILED</span>"
    repaired = f" <span class='badge warn'>repaired: {esc(entry['repair'])}</span>" \
        if entry.get("repair") else ""
    return f"""
    <section class='example' id='ex-{n}'>
      <h2>Example {n} {status}{repaired}</h2>
      <div class='question'>&ldquo;{esc(question)}&rdquo;</div>
      <div class='attempts{" multi" if len(result.attempts) > 1 else ""}'>{cards}</div>
      {body}
    </section>"""


def render_showcase(n, title, sql, conn):
    compiled = compile_sql(sql)
    if compiled.ok:
        run_compiled(compiled, conn)
        body = render_compiled(compiled)
    else:
        body = render_errors(compiled)
    return f"""
    <section class='example' id='sql-{n}'>
      <h2>{esc(title)}</h2>
      <pre class='sql'>python main.py --sql "{esc(sql)}"</pre>
      <div class='label'>Tokens</div>{render_tokens(compiled.tokens)}
      {body}
    </section>"""


CSS = """
:root {
  --bg: #f4f6f8; --card: #ffffff; --soft: #f9fafb; --border: #d7dce1; --text: #1f2937;
  --muted: #6b7280; --accent: #2563eb; --code-bg: #0f172a; --code-fg: #e2e8f0;
  --ok-bg: #e8f5e9; --ok-fg: #2e7d32; --fail-bg: #fdecea; --fail-fg: #c62828;
  --warn-bg: #fff4e5; --warn-fg: #9a5b00; --line: #9ca3af;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111418; --card: #1a1f25; --soft: #20262d; --border: #333b44; --text: #e5e7eb;
    --muted: #9ca3af; --accent: #60a5fa; --code-bg: #0b0f14; --code-fg: #e2e8f0;
    --ok-bg: #12301a; --ok-fg: #7ee08f; --fail-bg: #3a1515; --fail-fg: #ff8a80;
    --warn-bg: #3a2a10; --warn-fg: #ffc46b; --line: #6b7280;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 64px; background: var(--bg); color: var(--text);
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.5; }
h1 { text-align: center; margin: 0 0 4px; font-size: 28px; }
.subtitle { text-align: center; color: var(--muted); margin: 0 0 20px; }
h2.part { max-width: 1100px; margin: 40px auto 16px; font-size: 22px; }
nav { max-width: 1100px; margin: 0 auto 32px; display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
nav a { color: var(--accent); text-decoration: none; font-weight: 600; font-size: 14px;
  padding: 4px 12px; border: 1px solid var(--border); border-radius: 16px; background: var(--card); }
.example { max-width: 1100px; margin: 0 auto 40px; background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; padding: 20px 24px 28px; }
.example h2 { margin-top: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 20px; }
.question { font-size: 17px; font-style: italic; margin: 4px 0 20px; padding: 10px 14px;
  background: var(--soft); border-left: 4px solid var(--accent); border-radius: 4px; }
.badge { font-size: 12px; font-weight: 700; padding: 2px 10px; border-radius: 12px; }
.badge.ok { background: var(--ok-bg); color: var(--ok-fg); }
.badge.fail { background: var(--fail-bg); color: var(--fail-fg); }
.badge.warn { background: var(--warn-bg); color: var(--warn-fg); }
.attempts { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 8px; }
.attempts .card { flex: 1 1 100%; min-width: 0; }
.attempts.multi .card { flex: 1 1 420px; }
.card { border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; background: var(--soft); }
.card-head { font-weight: 700; margin-bottom: 8px; }
.label { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted);
  margin: 16px 0 6px; font-weight: 700; }
pre { margin: 0; font-family: Consolas, Menlo, monospace; font-size: 13px; }
pre.sql, pre.plan { background: var(--code-bg); color: var(--code-fg); padding: 10px 14px;
  border-radius: 6px; white-space: pre-wrap; word-break: break-word; overflow-x: auto; }
pre.caret { color: var(--fail-fg); margin-top: 4px; white-space: pre; overflow-x: auto; }
.tokens { display: flex; flex-wrap: wrap; gap: 5px; }
.token { border: 1px solid var(--border); border-radius: 5px; background: var(--card);
  padding: 2px 7px; text-align: center; min-width: 40px; }
.t-type { font-size: 10px; color: var(--accent); font-weight: 700; }
.t-val { font-family: monospace; font-size: 12px; word-break: break-word; }
.status { display: block; padding: 8px 12px; border-radius: 6px; font-weight: 600; }
.status.ok { background: var(--ok-bg); color: var(--ok-fg); }
.status.fail { background: var(--fail-bg); color: var(--fail-fg); }
.status ul { margin: 6px 0 0; padding-left: 18px; font-weight: 400; font-size: 13px; }
.two-col { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
.two-col > div { min-width: 0; }
ul.passes { margin: 0; padding-left: 20px; font-size: 14px; }
.fired { color: var(--ok-fg); font-weight: 700; }
.note { font-family: monospace; font-size: 12px; color: var(--muted); margin-left: 8px; }
table.results { border-collapse: collapse; width: 100%; font-size: 13px; display: block; overflow-x: auto; }
table.results th, table.results td { border: 1px solid var(--border); padding: 5px 9px; text-align: left; }
table.results th { background: var(--soft); }
table.trace code { font-size: 12px; }
.check { margin: 12px 0 0; }
.muted { color: var(--muted); font-style: italic; font-weight: 400; }
.bars { font-size: 12px; }
.bar-row { display: grid; grid-template-columns: 90px 1fr 80px; align-items: center; gap: 8px; margin: 3px 0; }
.bar { height: 10px; background: var(--accent); border-radius: 3px; display: block; }
.bar-ms { font-family: monospace; color: var(--muted); text-align: right; }
.tree-wrap { overflow-x: auto; padding: 6px 0; }
.tree, .tree ul, .tree li { list-style: none; margin: 0; padding: 0; position: relative; }
.tree { display: inline-flex; justify-content: center; min-width: 100%; }
.tree ul { display: flex; padding-top: 18px; position: relative; }
.tree li { display: flex; flex-direction: column; align-items: center; padding: 18px 6px 0; position: relative; }
.tree li::before, .tree li::after { content: ''; position: absolute; top: 0; right: 50%; width: 50%;
  height: 18px; border-top: 2px solid var(--line); }
.tree li::after { right: auto; left: 50%; border-left: 2px solid var(--line); }
.tree li:only-child::after, .tree li:only-child::before { display: none; }
.tree li:only-child { padding-top: 0; }
.tree li:first-child::before, .tree li:last-child::after { border: 0 none; }
.tree li:last-child::before { border-right: 2px solid var(--line); border-radius: 0 6px 0 0; }
.tree li:first-child::after { border-radius: 6px 0 0 0; }
.tree ul ul::before { content: ''; position: absolute; top: 0; left: 50%; border-left: 2px solid var(--line);
  width: 0; height: 18px; }
.tree-node { border: 1px solid var(--line); border-radius: 5px; background: var(--card); padding: 3px 8px;
  font-family: monospace; font-size: 12px; white-space: nowrap; }
"""


def build_page(conn):
    examples = "".join(render_example(n, e, conn) for n, e in enumerate(BUILTIN_QUESTIONS, 1))
    showcase = "".join(render_showcase(n, t, s, conn) for n, (t, s) in enumerate(SHOWCASE_SQL, 1))
    nav = "".join(f"<a href='#ex-{n}'>Example {n}</a>" for n in range(1, len(BUILTIN_QUESTIONS) + 1))
    nav += "".join(f"<a href='#sql-{n}'>{esc(t)}</a>" for n, (t, _) in enumerate(SHOWCASE_SQL, 1))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NL to SQL Compiler Demo</title>
<style>{CSS}</style>
</head>
<body>
<h1>NL &rarr; SQL Compiler Pipeline</h1>
<p class="subtitle">Generated offline by visualize.py (stub LLM, no API key). Lexer, parser,
semantic analyzer, planner, optimizer and interpreter are all hand-written.</p>
<nav>{nav}</nav>
<h2 class="part">Part 1 &mdash; English questions (stub LLM + repair loop)</h2>
{examples}
<h2 class="part">Part 2 &mdash; Direct SQL (python main.py --sql)</h2>
{showcase}
</body>
</html>
"""


def main(open_browser=True):
    conn = get_connection()
    try:
        page = build_page(conn)
    finally:
        conn.close()
    OUTPUT_PATH.write_text(page, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({len(page):,} bytes)")
    if open_browser:
        try:
            webbrowser.open(OUTPUT_PATH.as_uri())
        except Exception:
            pass   # no browser available - the file is still written
    return OUTPUT_PATH


if __name__ == "__main__":
    main(open_browser="--no-open" not in sys.argv)

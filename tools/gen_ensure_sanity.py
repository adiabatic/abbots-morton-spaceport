#!/usr/bin/env python3
"""Generate ensure-sanity.html with ·Tea·Tea no-double-half entries."""

from pathlib import Path

LETTERS = [
    ("Pea", 0xE650),
    ("Bay", 0xE651),
    ("Tea", 0xE652),
    ("Day", 0xE653),
    ("Key", 0xE654),
    ("Gay", 0xE655),
    ("Thaw", 0xE656),
    ("They", 0xE657),
    ("Fee", 0xE658),
    ("Vie", 0xE659),
    ("See", 0xE65A),
    ("Zoo", 0xE65B),
    ("She", 0xE65C),
    ("Jai", 0xE65D),
    ("Cheer", 0xE65E),
    ("Jay", 0xE65F),
    ("Ye", 0xE660),
    ("Way", 0xE661),
    ("He", 0xE662),
    ("Why", 0xE663),
    ("-ing", 0xE664),
    ("May", 0xE665),
    ("No", 0xE666),
    ("Low", 0xE667),
    ("Roe", 0xE668),
    ("Loch", 0xE669),
    ("Llan", 0xE66A),
    ("Excite", 0xE66B),
    ("Exam", 0xE66C),
    ("It", 0xE670),
    ("Eat", 0xE671),
    ("Et", 0xE672),
    ("Eight", 0xE673),
    ("At", 0xE674),
    ("I", 0xE675),
    ("Ah", 0xE676),
    ("Awe", 0xE677),
    ("Ox", 0xE678),
    ("Oy", 0xE679),
    ("Utter", 0xE67A),
    ("Out", 0xE67B),
    ("Owe", 0xE67C),
    ("Foot", 0xE67D),
    ("Ooze", 0xE67E),
]

TEA = 0xE652


def expect_tok(name):
    return f"·{name}"


def dt_name(name):
    return f"·{name}"


def entity(code):
    return f"&#x{code:04X};"


COLS = 3


def cell_pair(dt_text, expect, codes):
    dd_content = "".join(entity(c) for c in codes)
    return (
        f'            <td>{dt_text}</td>\n'
        f'            <td data-expect="{expect}" class="sample">{dd_content}</td>'
    )


def empty_pair():
    return "            <td></td>\n            <td></td>"


def table_wrap(cells):
    nrows = -(-len(cells) // COLS)  # ceil division
    rows = []
    for r in range(nrows):
        chunk = []
        for c in range(COLS):
            idx = c * nrows + r
            chunk.append(cells[idx] if idx < len(cells) else empty_pair())
        rows.append("          <tr>\n" + "\n".join(chunk) + "\n          </tr>")
    inner = "\n".join(rows)
    return (
        "      <table>\n"
        f"{inner}\n"
        "      </table>"
    )


def build_sections():
    tea_nhalf = "·Tea.!half"

    sections = []

    # --- Bare ---
    bare = cell_pair(
        "·Tea·Tea",
        f"{tea_nhalf} ? {tea_nhalf}",
        [TEA, TEA],
    )
    sections.append(("Bare", table_wrap([bare])))

    # --- X + Tea + Tea ---
    before_cells = []
    for name, code in LETTERS:
        tok = expect_tok(name)
        dt = f"{dt_name(name)}·Tea·Tea"
        expect = f"{tok} ? {tea_nhalf} ? {tea_nhalf}"
        before_cells.append(cell_pair(dt, expect, [code, TEA, TEA]))
    sections.append(("X·Tea·Tea", table_wrap(before_cells)))

    # --- Tea + Tea + Y ---
    after_cells = []
    for name, code in LETTERS:
        tok = expect_tok(name)
        dt = f"·Tea·Tea{dt_name(name)}"
        expect = f"{tea_nhalf} ? {tea_nhalf} ? {tok}"
        after_cells.append(cell_pair(dt, expect, [TEA, TEA, code]))
    sections.append(("·Tea·Tea·Y", table_wrap(after_cells)))

    # --- X + Tea + Tea + Y: one table per X ---
    for xname, xcode in LETTERS:
        xtok = expect_tok(xname)
        cells = []
        for yname, ycode in LETTERS:
            ytok = expect_tok(yname)
            dt = f"{dt_name(xname)}·Tea·Tea{dt_name(yname)}"
            expect = f"{xtok} ? {tea_nhalf} ? {tea_nhalf} ? {ytok}"
            cells.append(cell_pair(dt, expect, [xcode, TEA, TEA, ycode]))
        sections.append((f"{dt_name(xname)}·Tea·Tea·Y", table_wrap(cells)))

    return sections


def build_html():
    sections = build_sections()

    section_html = []
    for heading, content in sections:
        section_html.append(f"      <h3>{heading}</h3>\n{content}")

    body_sections = "\n\n".join(section_html)

    return f"""\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Ensure Sanity &mdash; Abbots Morton Spaceport</title>
    <link rel="stylesheet" href="shared.css" />
    <style>
      :root {{
        --font-stack: "Abbots Morton Spaceport Sans Senior", "Abbots Morton Spaceport Sans Junior", sans-serif;
        --font-size: 22px;
      }}

      body {{
        max-width: 80ch;
        font-size: var(--font-size);
        line-height: calc((14 + 2) / 11);
      }}

      table {{
        border-collapse: collapse;
        margin-bottom: 1rem;
      }}

      td {{
        padding: 0.15rem 0.5rem;
        vertical-align: baseline;
      }}

      td:first-child {{
        white-space: nowrap;
      }}
    </style>
    <script type="module">
      import {{ initToggles }} from "./shared.js";

      initToggles({{
        sizeToggle: "size-toggle",
        fontOrderToggle: "font-order-toggle",
        fontToggle: "font-toggle",
        levelToggle: "level-toggle",
        weightToggle: "weight-toggle",
      }});
    </script>
  </head>
  <body>
    <h1>Ensure Sanity</h1>

    <div class="toggle-buttons">
      <div id="size-toggle" class="size-control">
        <button class="size-down">&minus;</button>
        <span class="size-display">22px</span>
        <button class="size-up">+</button>
      </div>
      <button id="font-order-toggle">AMS first</button>
      <button id="font-toggle">Sans</button>
      <button id="level-toggle">Senior</button>
      <button id="weight-toggle">Weight 400</button>
    </div>

    <p>Joins that are visibly broken, found by reviewing the tables page.</p>

    <section class="panel">
      <h2>·Tea + ·Tea: no double halves</h2>
      <p>Neither ·Tea should be a half-height variant when two appear consecutively, regardless of surrounding context.</p>

{body_sections}
    </section>

    <footer>
      <a href="index.html">Font Test</a> | <a href="the-manual.html">The Manual</a> |
      <a href="extra-senior-words.html">Extra Senior Words</a> |
      <a href="tables.html">Tables</a> | Ensure Sanity |
      <a href="specimen.html">Specimen</a> | <a href="exotics.html">Exotics</a> |
      <a href="glyph-editor.html">Glyph Editor</a> |
      <a href="https://github.com/adiabatic/abbots-morton-spaceport">GitHub</a>
    </footer>
  </body>
</html>
"""


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    out = root / "test" / "ensure-sanity.html"
    out.write_text(build_html(), encoding="utf-8")
    print(f"Wrote {out}")

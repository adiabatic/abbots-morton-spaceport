#!/usr/bin/env python3
"""Generate ensure-sanity.html with ·Tea·Tea no-double-half entries."""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# noqa E402: "module import not at top of file" — must follow sys.path tweak above
from build_font import load_glyph_data  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

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
DAY = 0xE653
HE = 0xE662
CHEER = 0xE65E
COLS = 3

def _family_to_label(family: str) -> str:
    base = family.removeprefix("qs")
    return "-ing" if base == "Ing" else base


def _compute_ligature_pairs() -> set[tuple[str, str]]:
    data = load_glyph_data(ROOT / "glyph_data")
    pairs: set[tuple[str, str]] = set()
    for family in data["glyph_families"].values():
        seq = family.get("sequence")
        if isinstance(seq, list) and len(seq) == 2:
            first, second = seq
            pairs.add((_family_to_label(first), _family_to_label(second)))
    return pairs


LIGATURE_PAIRS = _compute_ligature_pairs()


def expect_tok(name: str) -> str:
    return f"·{name}"


def join_expect(names_and_tokens: list[tuple[str, str]]) -> str:
    """Join expect tokens, using +? for ligature pairs and ? otherwise.

    When a token is the second element of a +? ligature, its variant
    modifiers are stripped because data-expect applies modifiers to the
    whole ligature group, and they're dropped in the separated
    interpretation anyway.  When a token is the first element of a +?
    ligature, its modifiers are also stripped for the same reason.
    """
    # Pre-scan to find which indices are part of a ligature pair
    in_liga_first: set[int] = set()
    in_liga_second: set[int] = set()
    for i in range(1, len(names_and_tokens)):
        prev_name = names_and_tokens[i - 1][0]
        cur_name = names_and_tokens[i][0]
        if (prev_name, cur_name) in LIGATURE_PAIRS:
            in_liga_first.add(i - 1)
            in_liga_second.add(i)

    parts = []
    for i, (name, tok) in enumerate(names_and_tokens):
        # Strip modifiers from tokens participating in a ligature
        if i in in_liga_first or i in in_liga_second:
            tok = expect_tok(name)

        if i in in_liga_second:
            parts.append(f"+?{name}")
        elif i > 0:
            parts.append(f" ? {tok}")
        else:
            parts.append(tok)
    return "".join(parts)


def dt_name(name: str) -> str:
    return f"·{name}"


def entity(code: int) -> str:
    return f"&#x{code:04X};"


def cell_key(*names: str) -> str:
    return "|".join(names)


def cell_pair(dt_text: str, expect: str, codes: list[int], key: str, failed_keys: set[str]) -> str:
    dd_content = "".join(entity(c) for c in codes)
    highlight = key in failed_keys
    label_prefix = "◊ " if highlight else ""
    hl_attr = ' data-failed=""' if highlight else ""
    return (
        f"            <td>{label_prefix}{dt_text}</td>\n"
        f'            <td data-expect="{expect}" data-key="{key}"{hl_attr} class="sample">{dd_content}</td>'
    )


def empty_pair() -> str:
    return "            <td></td>\n            <td></td>"


def table_wrap(cells: list[str]) -> str:
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


def build_panels(failed_keys: set[str]) -> list[tuple[str, str, list[tuple[str, str]]]]:
    tea_nhalf = "·Tea.!half"
    tea_tok = expect_tok("Tea")
    cheer_tok = expect_tok("Cheer")

    panels: list[tuple[str, str, list[tuple[str, str]]]] = []

    tea_tea_sections: list[tuple[str, str]] = []

    # --- Bare ---
    key = cell_key("Tea", "Tea")
    expect = join_expect([("Tea", tea_nhalf), ("Tea", tea_nhalf)])
    bare = cell_pair("·Tea·Tea", expect, [TEA, TEA], key, failed_keys)
    tea_tea_sections.append(("Bare", table_wrap([bare])))

    # --- X + Tea + Tea ---
    before_cells = []
    for name, code in LETTERS:
        dt = f"{dt_name(name)}·Tea·Tea"
        expect = join_expect([(name, expect_tok(name)), ("Tea", tea_nhalf), ("Tea", tea_nhalf)])
        key = cell_key(name, "Tea", "Tea")
        before_cells.append(cell_pair(dt, expect, [code, TEA, TEA], key, failed_keys))
    tea_tea_sections.append(("X·Tea·Tea", table_wrap(before_cells)))

    # --- Tea + Tea + Y ---
    after_cells = []
    for name, code in LETTERS:
        dt = f"·Tea·Tea{dt_name(name)}"
        expect = join_expect([("Tea", tea_nhalf), ("Tea", tea_nhalf), (name, expect_tok(name))])
        key = cell_key("Tea", "Tea", name)
        after_cells.append(cell_pair(dt, expect, [TEA, TEA, code], key, failed_keys))
    tea_tea_sections.append(("·Tea·Tea·Y", table_wrap(after_cells)))

    panels.append(
        (
            "·Tea + ·Tea: no double halves",
            "Neither ·Tea should be a half-height variant when two appear consecutively, regardless of surrounding context.",
            tea_tea_sections,
        )
    )

    tea_cheer_sections: list[tuple[str, str]] = []

    # --- Bare ·Tea·Cheer ---
    key = cell_key("Tea", "Cheer")
    expect = f"{tea_nhalf} | {cheer_tok}"
    bare_tc = cell_pair("·Tea·Cheer", expect, [TEA, CHEER], key, failed_keys)
    tea_cheer_sections.append(("Bare", table_wrap([bare_tc])))

    # --- X + Tea + Cheer ---
    # Tea's half/!half trait varies with X (e.g., ·It forces .half via a prior
    # baseline/x-height join; ·See lands on .!half post-fix). Assert only the
    # strict break between Tea and Cheer; leave Tea's trait unasserted.
    # Skip X=Tea: ·Tea·Tea·Cheer is a content duplicate of the Y=Cheer row in
    # the ·Tea·Tea·Y grid above, which already carries a stricter assertion.
    x_tc_cells = []
    for name, code in LETTERS:
        if name == "Tea":
            continue
        dt = f"{dt_name(name)}·Tea·Cheer"
        if (name, "Tea") in LIGATURE_PAIRS:
            expect = f"{expect_tok(name)}+?Tea | {cheer_tok}"
        else:
            expect = f"{expect_tok(name)} ? {tea_tok} | {cheer_tok}"
        key = cell_key(name, "Tea", "Cheer")
        x_tc_cells.append(cell_pair(dt, expect, [code, TEA, CHEER], key, failed_keys))
    tea_cheer_sections.append(("X·Tea·Cheer", table_wrap(x_tc_cells)))

    # --- Tea + Cheer + Y ---
    tc_y_cells = []
    for name, code in LETTERS:
        dt = f"·Tea·Cheer{dt_name(name)}"
        expect = f"{tea_nhalf} | {cheer_tok} ? {expect_tok(name)}"
        key = cell_key("Tea", "Cheer", name)
        tc_y_cells.append(cell_pair(dt, expect, [TEA, CHEER, code], key, failed_keys))
    tea_cheer_sections.append(("·Tea·Cheer·Y", table_wrap(tc_y_cells)))

    panels.append(
        (
            "·Tea + ·Cheer: never joins",
            "·Tea must never join rightward onto ·Cheer, regardless of the preceding or following letter.",
            tea_cheer_sections,
        )
    )

    he_nhalf = "·He.!half"
    day_half = "·Day.half"
    he_tok = expect_tok("He")

    he_day_sections: list[tuple[str, str]] = []

    # --- Bare ·He·Day ---
    key = cell_key("He", "Day")
    expect = f"{he_nhalf} ~b~ {day_half}"
    bare_hd = cell_pair("·He·Day", expect, [HE, DAY], key, failed_keys)
    he_day_sections.append(("Bare", table_wrap([bare_hd])))

    # --- X + He + Day ---
    # qsHe has no entry anchor, so the X → He connection is context-dependent;
    # assert only that He lands on a non-half variant and Day lands on its half.
    x_hd_cells = []
    for name, code in LETTERS:
        dt = f"{dt_name(name)}·He·Day"
        expect = f"{expect_tok(name)} ? {he_nhalf} ~b~ {day_half}"
        key = cell_key(name, "He", "Day")
        x_hd_cells.append(cell_pair(dt, expect, [code, HE, DAY], key, failed_keys))
    he_day_sections.append(("X·He·Day", table_wrap(x_hd_cells)))

    # --- He + Day + Y ---
    # When (Day, Y) ligates, the Day.half trait rides on the ligature glyph
    # if that ligature has a half variant (qsDay_qsUtter.half exists, so the
    # baseline join survives). qsDay_qsEat has no half variant, so qsHe still
    # lands on its full form but the ligature enters at x-height — relax the
    # connection assertion there.
    hd_y_cells = []
    for name, code in LETTERS:
        dt = f"·He·Day{dt_name(name)}"
        if ("Day", name) in LIGATURE_PAIRS:
            if name == "Utter":
                expect = f"{he_nhalf} ~b~ ·Day+?{name}.half"
            else:
                expect = f"{he_nhalf} ? ·Day+?{name}"
        else:
            expect = f"{he_nhalf} ~b~ {day_half} ? {expect_tok(name)}"
        key = cell_key("He", "Day", name)
        hd_y_cells.append(cell_pair(dt, expect, [HE, DAY, code], key, failed_keys))
    he_day_sections.append(("·He·Day·Y", table_wrap(hd_y_cells)))

    panels.append(
        (
            "·He + ·Day: full He, half Day",
            "·He must be a full-height variant and ·Day must be its half form when they appear consecutively, regardless of surrounding context.",
            he_day_sections,
        )
    )

    return panels


def build_html(failed_keys: set[str]) -> str:
    panels = build_panels(failed_keys)

    panel_parts: list[str] = []
    for title, intro, sections in panels:
        section_html = [f"      <h3>{heading}</h3>\n{content}" for heading, content in sections]
        body = "\n\n".join(section_html)
        panel_parts.append(
            "    <section class=\"panel\">\n"
            f"      <h2>{title}</h2>\n"
            f"      <p>{intro}</p>\n\n"
            f"{body}\n"
            "    </section>"
        )
    body_sections = "\n\n".join(panel_parts)

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

      td[data-failed] {{
        background: light-dark(#f8d7da, #6b2020);
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
      <button id="weight-toggle">Regular</button>
    </div>

    <p>Joins that are visibly broken, found by reviewing the tables page.</p>

{body_sections}

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


def collect_failures(root: Path, out_path: Path) -> set[str]:
    html = out_path.read_text(encoding="utf-8")
    line_to_key: dict[int, str] = {}
    for i, line in enumerate(html.splitlines(), 1):
        m = re.search(r'data-expect="[^"]*"', line)
        if not m:
            continue
        km = re.search(r'data-key="([^"]*)"', line)
        if km:
            line_to_key[i] = km.group(1)

    result = subprocess.run(
        ["uv", "run", "pytest", str(out_path), "--tb=no", "-v"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    failed_keys = set()
    for line in result.stdout.splitlines():
        if "FAILED" not in line:
            continue
        m = re.search(r"::(\d+):", line)
        if m:
            lineno = int(m.group(1))
            if lineno in line_to_key:
                failed_keys.add(line_to_key[lineno])
    return failed_keys


if __name__ == "__main__":
    out = ROOT / "test" / "ensure-sanity.html"

    # First pass: generate without highlights
    out.write_text(build_html(set()), encoding="utf-8")

    if "--mark-failures" in sys.argv:
        print("Running tests to collect failures...")
        failed_keys = collect_failures(ROOT, out)
        print(f"Found {len(failed_keys)} failures")
        out.write_text(build_html(failed_keys), encoding="utf-8")

    print(f"Wrote {out}")

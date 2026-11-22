# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow>=11", "pymupdf>=1.24"]
# ///
"""Views, crops, and the review page of the Manual letter survey.

`reference/manual-letters/words/pNN.json` holds, per page of `reference/Quikscript Manual.pdf`, the handwritten words in reading order and where their letters sit on the scan (`tools/manual_letters_prefill.py` writes the words; the positions are filled in by eye). This script draws the views that work needs, records its results, and builds the crops and the review page. It declares its own dependencies, so run it as a script, from the repository root:

    uv run tools/manual_letters.py view 16 --y 300 620 --x 0 1150 -o tmp/v.png        # a ruled view for reading coordinates
    uv run tools/manual_letters.py view 16 --y 300 620 --x 0 1150 --boxes -o tmp/v.png # the same with the recorded letters drawn
    uv run tools/manual_letters.py check                                              # validate every word list
    uv run tools/manual_letters.py build                                              # crops, crops.json, index.html

    uv run tools/manual_letters.py line 16 2 --base 150 606 2000 606 --short 33        # record a handwriting line
    uv run tools/manual_letters.py set p16-w005 --line 2 --x 396 483 --cuts 424 452  # place a word's letters
    uv run tools/manual_letters.py slant 26 1 2 3 4                                   # mark lines as written at a slant
    uv run tools/manual_letters.py batch tmp/p16.txt                                  # many `line` and `set` commands from a file

`tools/manual_letters_words.py` documents the word-list format. A crop's frame hangs off the baseline, so every crop shows the same band above and below the writing line. `build` recompresses the crops with `oxipng -o max` when oxipng is installed, which is how the checked-in crops are stored.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pymupdf  # pyright: ignore[reportMissingImports]
from PIL import Image, ImageChops, ImageDraw, ImageFont  # pyright: ignore[reportMissingImports]

from manual_letters_words import (
    FORMS,
    JOIN_TEXT,
    ROOT,
    SURVEY,
    all_pages,
    check,
    family_order,
    load_page,
    set_line,
    set_slanted,
    set_word,
    split_form,
    unplaced,
)

PDF = ROOT / "reference" / "Quikscript Manual.pdf"
PAGES_DIR = ROOT / "tmp" / "manual-letters" / "pages"

# A crop's frame in Short heights: above and below the baseline, and beside the letter where no neighbor reaches further.
FRAME_ABOVE = 3.0
FRAME_BELOW = 2.2
FRAME_BESIDE = 2.0

DISPLAY = {"qsIng": "·-ing", "qsJai": "·J’ai"}


def letter_name(family: str) -> str:
    return DISPLAY.get(family, "·" + family.removeprefix("qs"))


def page_image(page: int) -> Any:
    """Return the page's embedded scan as an 8-bit image with black ink, extracting it from the PDF on first use."""
    path = PAGES_DIR / f"p{page:02d}.png"
    if not path.exists():
        PAGES_DIR.mkdir(parents=True, exist_ok=True)
        document = pymupdf.open(PDF)
        xref = document[page - 1].get_images(full=True)[0][0]
        image = Image.open(io.BytesIO(pymupdf.Pixmap(document, xref).tobytes("png"))).convert("L")
        histogram = image.histogram()
        if sum(histogram[:128]) > sum(histogram[128:]):
            image = ImageChops.invert(image)
        image.convert("1").save(path)
    return Image.open(path).convert("L")


def baseline_at(line: dict[str, Any], x: float) -> float:
    xa, ya, xb, yb = line["base"]
    return ya if xb == xa else ya + (yb - ya) * (x - xa) / (xb - xa)


def placed_letters(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one record per letter of every positioned word on a page: its box, its baseline, and its neighbors."""
    lines = {line["n"]: line for line in data["lines"]}
    out = []
    for word in data["words"]:
        if word["x"] is None or word["line"] is None:
            continue
        line = lines[word["line"]]
        edges = [word["x"][0], *word["cuts"], word["x"][1]]
        count = len(word["letters"])
        for i, letter in enumerate(word["letters"]):
            family, form = split_form(letter)
            x0, x1 = edges[i], edges[i + 1]
            base = word.get("base") or baseline_at(line, (x0 + x1) / 2)
            out.append(
                {
                    "page": data["page"],
                    "word": word,
                    "index": i,
                    "family": family,
                    "form": form,
                    "x0": x0,
                    "x1": x1,
                    "base": round(base),
                    "short": line["short"],
                    "slanted": bool(line.get("slanted")),
                    "left": word["joins"][i - 1] if i else "|",
                    "right": word["joins"][i] if i < count - 1 else "|",
                    "prev_x0": edges[i - 1] if i else None,
                    "next_x1": edges[i + 2] if i < count - 1 else None,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Ruled views
# ---------------------------------------------------------------------------

GRID = (150, 205, 255)
FAINT = (225, 240, 255)
TINTS = ((255, 236, 150), (190, 235, 190))


def _font(size: int) -> Any:
    return ImageFont.load_default(size=size)


def render_view(
    page: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    scale: float,
    boxes: bool,
    marks_x: list[int],
    marks_y: list[int],
) -> Any:
    """Draw a region of a page with a pixel ruler, and with `boxes` the recorded letters, baselines, and joins. Each mark is a red line at a proposed coordinate."""
    source = page_image(page)
    x1, y1 = min(x1, source.width), min(y1, source.height)
    left, top = 58, 64
    width, height = round((x1 - x0) * scale), round((y1 - y0) * scale)
    canvas = Image.new("RGB", (left + width + 6, top + height + 40), "white")
    draw = ImageDraw.Draw(canvas)
    small, label = _font(13), _font(15)

    def sx(x: float) -> float:
        return left + (x - x0) * scale

    def sy(y: float) -> float:
        return top + (y - y0) * scale

    shown = []
    if boxes:
        for record in placed_letters(load_page(page)):
            short, base = record["short"], record["base"]
            if record["x1"] < x0 or record["x0"] > x1 or base + short < y0 or base - 2 * short > y1:
                continue
            shown.append(record)
            cell = [sx(record["x0"]), sy(base - 2.2 * short), sx(record["x1"]), sy(base + 1.3 * short)]
            draw.rectangle(cell, fill=TINTS[record["index"] % 2])

    step = 100 if scale < 0.6 else 50 if scale < 2 else 20 if scale < 4 else 10
    for x in range(x0 - x0 % 10 + 10, x1, 10):
        major = x % step == 0
        draw.line([sx(x), top - (9 if major else 4), sx(x), top], fill="black")
        if major or scale >= 2:
            draw.line([sx(x), top, sx(x), top + height], fill=GRID if major else FAINT)
        if major:
            draw.text((sx(x), top - 11), str(x), font=small, fill=(200, 0, 0), anchor="mb")
    for y in range(y0 - y0 % 10 + 10, y1, 10):
        major = y % step == 0
        draw.line([left - (9 if major else 4), sy(y), left, sy(y)], fill="black")
        if major or scale >= 2:
            draw.line([left, sy(y), left + width, sy(y)], fill=GRID if major else FAINT)
        if major:
            draw.text((left - 11, sy(y)), str(y), font=small, fill=(200, 0, 0), anchor="rm")

    region = source.crop((x0, y0, x1, y1)).resize((width, height), Image.Resampling.LANCZOS)
    ink = Image.new("L", canvas.size, 255)
    ink.paste(region, (left, top))
    canvas = ImageChops.multiply(canvas, Image.merge("RGB", (ink, ink, ink)))
    draw = ImageDraw.Draw(canvas)

    for record in shown:
        short, base, word, i = record["short"], record["base"], record["word"], record["index"]
        draw.line([sx(record["x0"]), sy(base), sx(record["x1"]), sy(base)], fill=(0, 150, 0))
        draw.line(
            [sx(record["x0"]), sy(base - short), sx(record["x1"]), sy(base - short)], fill=(230, 120, 0)
        )
        name = record["family"].removeprefix("qs") + {"full": "", "half": ".h", "alt": ".a"}[record["form"]]
        middle = (sx(record["x0"]) + sx(record["x1"])) / 2
        draw.text(
            (middle, sy(base - 2.2 * short) - 2 - 15 * (i % 3)),
            name,
            font=label,
            fill=(0, 0, 200),
            anchor="mb",
        )
        below = sy(base + 1.3 * short) + 2
        if i == 0:
            draw.text(
                (sx(record["x0"]), below + 16), word["id"][-4:], font=small, fill=(120, 0, 120), anchor="la"
            )
        else:
            draw.text(
                (sx(record["x0"]), below), word["joins"][i - 1], font=label, fill=(200, 0, 0), anchor="ma"
            )

    for x in marks_x:
        draw.line([sx(x), top - 22, sx(x), top + height], fill=(230, 0, 0))
        draw.text((sx(x), top - 24), str(x), font=small, fill=(230, 0, 0), anchor="mb")
    for y in marks_y:
        draw.line([left, sy(y), left + width + 6, sy(y)], fill=(230, 0, 0))
        draw.text((left + width, sy(y) - 1), str(y), font=small, fill=(230, 0, 0), anchor="rb")
    return canvas


# ---------------------------------------------------------------------------
# Crops and the review page
# ---------------------------------------------------------------------------


def crop_frame(record: dict[str, Any], source_size: tuple[int, int]) -> tuple[int, int, int, int]:
    short = record["short"]
    beside = FRAME_BESIDE * short
    x0 = min(record["x0"] - beside, record["prev_x0"] if record["prev_x0"] is not None else record["x0"])
    x1 = max(record["x1"] + beside, record["next_x1"] if record["next_x1"] is not None else record["x1"])
    y0 = record["base"] - FRAME_ABOVE * short
    y1 = record["base"] + FRAME_BELOW * short
    return (
        max(0, round(x0)),
        max(0, round(y0)),
        min(source_size[0], round(x1)),
        min(source_size[1], round(y1)),
    )


def word_notation(word: dict[str, Any], targets: range) -> str:
    """Return a word in data-expect notation as HTML, with the target letters marked."""
    parts = []
    for i, letter in enumerate(word["letters"]):
        family, form = split_form(letter)
        text = html.escape(letter_name(family) + ("" if form == "full" else "." + form))
        parts.append(f"<mark>{text}</mark>" if i in targets else text)
        if i < len(word["joins"]):
            join = word["joins"][i]
            parts.append("+" if join == "+" else f" {JOIN_TEXT[join]} ")
    return "".join(parts)


def build() -> None:
    faults = check(all_pages())
    if faults:
        sys.exit("\n".join(faults))

    crops_dir = SURVEY / "crops"
    if crops_dir.exists():
        shutil.rmtree(crops_dir)
    records = []
    for data in all_pages():
        placed = placed_letters(data)
        if not placed:
            continue
        source = page_image(data["page"])

        def crop(group: list[dict[str, Any]], folder: Path) -> dict[str, Any]:
            """Save the crop that shows a run of neighboring letters of one word and return its record."""
            first, last = group[0], group[-1]
            frames = [crop_frame(record, source.size) for record in group]
            frame = (frames[0][0], min(f[1] for f in frames), frames[-1][2], max(f[3] for f in frames))
            word = first["word"]
            relative = Path("crops") / folder / f"{word['id']}-{first['index'] + 1}.png"
            (SURVEY / relative).parent.mkdir(parents=True, exist_ok=True)
            source.crop(frame).convert("1").save(SURVEY / relative, optimize=True)
            return {
                "file": relative.as_posix(),
                "family": "+".join(record["family"] for record in group),
                "form": first["form"],
                "left": first["left"],
                "right": last["right"],
                "page": first["page"],
                "word": word["id"],
                "index": first["index"],
                "gloss": word["gloss"],
                "letters": word["letters"],
                "joins": word["joins"],
                "src": word["src"],
                "note": word["note"],
                "size": [frame[2] - frame[0], frame[3] - frame[1]],
                "letter_x": [first["x0"] - frame[0], last["x1"] - frame[0]],
                "baseline_y": first["base"] - frame[1],
                "short": first["short"],
                "slanted": first["slanted"],
                "notation": word_notation(word, range(first["index"], last["index"] + 1)),
            }

        for record in placed:
            records.append(crop([record], Path(record["family"]) / record["form"]))
        for record, after in zip(placed, placed[1:]):
            if record["word"] is after["word"] and record["right"] == "+":
                records.append(
                    crop([record, after], Path("ligatures") / f"{record['family']}_{after['family']}")
                )

    if shutil.which("oxipng"):
        subprocess.run(["oxipng", "-o", "max", "-r", "-q", str(crops_dir)], check=True)
    else:
        print(
            "oxipng is not installed: the crops are larger than the checked-in ones and will all show as changed"
        )

    index = [{k: v for k, v in record.items() if k != "notation"} for record in records]
    (SURVEY / "crops.json").write_text(
        "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in index) + "\n]\n", encoding="utf-8"
    )
    (SURVEY / "index.html").write_text(render_html(records), encoding="utf-8")
    print(f"{len(records)} crops -> {SURVEY.relative_to(ROOT)}/index.html")


JOIN_RANK = {"|": 0, "b": 1, "x": 2, "6": 3, "t": 4, "+": 5}

PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Letters in The Manual</title>
<style>
:root {
  color-scheme: light;
  --zoom: 1;
  font-family: system-ui, sans-serif;
}
body {
  margin: 0 auto;
  max-width: 110rem;
  padding: 1rem 1.5rem 6rem;
  background: #f4f1ea;
  color: #1c1b19;
}
header {
  position: sticky;
  top: 0;
  z-index: 2;
  padding-block: 0.5rem;
  background: #f4f1eaf2;
  border-bottom: 1px solid #c9c2b3;

  nav {
    display: flex;
    flex-wrap: wrap;
    gap: 0.15rem 0.6rem;
    font-size: 0.85rem;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 1.2rem;
    align-items: center;
    margin-top: 0.4rem;
    font-size: 0.85rem;
  }
}
h1 {
  margin: 0 0 0.3rem;
  font-size: 1.2rem;
}
section.letter {
  margin-top: 2.5rem;

  h2 {
    border-bottom: 2px solid #1c1b19;
  }
  h3 {
    margin: 1.6rem 0 0.2rem;
    padding: 0.2rem 0.5rem;
    background: #dcd5c4;
  }
  h4 {
    margin: 1rem 0 0.3rem;
    font-weight: normal;
    font-size: 0.9rem;
  }
}
.count {
  color: #6b655a;
  font-weight: normal;
  font-size: 0.8em;
}
.crops {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  align-items: flex-start;
}
figure {
  margin: 0;
  max-width: calc(var(--w) * var(--zoom) * 1px + 2px);

  .frame {
    position: relative;
    width: calc(var(--w) * var(--zoom) * 1px);
    height: calc(var(--h) * var(--zoom) * 1px);
    border: 1px solid #c9c2b3;
    background: white;
    overflow: hidden;

    img {
      display: block;
      width: 100%;
      height: 100%;
      mix-blend-mode: multiply;
    }
    &::before {
      content: "";
      position: absolute;
      inset-block: 0;
      left: calc(var(--x0) * var(--zoom) * 1px);
      width: calc((var(--x1) - var(--x0)) * var(--zoom) * 1px);
      background: #ffe680;
    }
    &::after {
      content: "";
      position: absolute;
      inset-inline: 0;
      top: calc((var(--base) - var(--short)) * var(--zoom) * 1px);
      height: calc(var(--short) * var(--zoom) * 1px);
      border-block: 1px solid #2a8f3c;
      box-sizing: border-box;
      pointer-events: none;
    }
  }
  figcaption {
    margin-top: 0.15rem;
    font-size: 0.72rem;
    line-height: 1.3;
    overflow-wrap: anywhere;

    mark {
      background: #ffe680;
    }
    .where {
      color: #6b655a;
    }
    .note {
      display: block;
      color: #9a3412;
    }
  }
}
figure.slanted .frame {
  border-color: #1d4ed8;
  outline: 1px solid #1d4ed8;

  .lean {
    position: absolute;
    top: 0;
    right: 0;
    padding: 0 0.3rem;
    background: #1d4ed8;
    color: white;
    font-size: 0.65rem;
    font-style: italic;
  }
}
body:has(#show-letter:not(:checked)) figure .frame::before,
body:has(#show-lines:not(:checked)) figure .frame::after,
body:has(#hand option[value="upright"]:checked) figure.slanted,
body:has(#hand option[value="slanted"]:checked) figure:not(.slanted) {
  display: none;
}
</style>
</head>
<body>
"""


def render_groups(out: list[str], title: str, anchor: str, records: list[dict[str, Any]]) -> None:
    """Append one form's subsection: a heading, then a group of figures per pair of joins."""
    leaning = sum(r["slanted"] for r in records)
    count = f"{len(records)}, {leaning} slanted" if leaning else str(len(records))
    out.append(f'<h3 id="{anchor}">{html.escape(title)} <span class="count">{count}</span></h3>')
    contexts = sorted(
        {(r["left"], r["right"]) for r in records}, key=lambda c: (JOIN_RANK[c[0]], JOIN_RANK[c[1]])
    )
    for left, right in contexts:
        group = sorted(
            (r for r in records if (r["left"], r["right"]) == (left, right)), key=lambda r: r["slanted"]
        )
        heading = f"{JOIN_TEXT[left]} {title} {JOIN_TEXT[right]}"
        out.append(f'<h4><code>{html.escape(heading)}</code> <span class="count">{len(group)}</span></h4>')
        out.append('<div class="crops">')
        for r in group:
            style = f"--w:{r['size'][0]};--h:{r['size'][1]};--x0:{r['letter_x'][0]};--x1:{r['letter_x'][1]};--base:{r['baseline_y']};--short:{r['short']}"
            gloss = f"{html.escape(r['gloss'])} " if r["gloss"] else ""
            source = "" if r["src"] == "expect" else " · joins unattested"
            note = f'<span class="note">{html.escape(r["note"])}</span>' if r["note"] else ""
            lean = '<span class="lean">slanted</span>' if r["slanted"] else ""
            out.append(
                f'<figure{' class="slanted"' if r["slanted"] else ""} style="{style}">'
                f'<div class="frame"><img loading="lazy" src="{r["file"]}" alt="">{lean}</div>'
                f'<figcaption>{gloss}<code>{r["notation"]}</code> <span class="where">{r["word"]}{source}</span>{note}</figcaption></figure>'
            )
        out.append("</div>")


def render_html(records: list[dict[str, Any]]) -> str:
    order = family_order()
    present = [family for family in order if any(r["family"] == family for r in records)]
    pairs = {r["family"] for r in records if "+" in r["family"]}
    ligatures = sorted(pairs, key=lambda pair: [order.index(family) for family in pair.split("+")])
    out = [PAGE_HEAD, "<header>", "<h1>Letters in The Manual</h1>", "<nav>"]
    for family in present:
        out.append(f'<a href="#{family}">{html.escape(letter_name(family))}</a>')
    if ligatures:
        out.append('<a href="#ligatures">ligatures</a>')
    out.append("</nav>")
    out.append(
        '<div class="controls">'
        '<label><input type="checkbox" id="show-letter" checked> highlight the letter</label>'
        '<label><input type="checkbox" id="show-lines" checked> baseline and Short height</label>'
        '<label>hand <select id="hand"><option value="all">upright and slanted</option>'
        '<option value="upright">upright only</option><option value="slanted">slanted only</option></select></label>'
        '<label>zoom <input type="range" id="zoom" min="0.5" max="3" step="0.25" value="1"></label>'
        f'<span class="count">{len(records)} crops</span>'
        "</div>"
    )
    out.append("</header>")

    for family in present:
        mine = [r for r in records if r["family"] == family]
        out.append(f'<section class="letter" id="{family}">')
        out.append(f'<h2>{html.escape(letter_name(family))} <span class="count">{len(mine)}</span></h2>')
        for form in FORMS:
            of_form = [r for r in mine if r["form"] == form]
            if of_form:
                title = letter_name(family) + ("" if form == "full" else "." + form)
                render_groups(out, title, f"{family}-{form}", of_form)
        out.append("</section>")

    if ligatures:
        out.append('<section class="letter" id="ligatures">')
        out.append("<h2>Ligatures</h2>")
        for pair in ligatures:
            first, second = pair.split("+")
            title = f"{letter_name(first)}+{letter_name(second)[1:]}"
            render_groups(out, title, pair.replace("+", "_"), [r for r in records if r["family"] == pair])
        out.append("</section>")

    out.append(
        "<script>\n"
        'const zoom = document.getElementById("zoom");\n'
        'zoom.addEventListener("input", () => document.documentElement.style.setProperty("--zoom", zoom.value));\n'
        "</script>\n</body>\n</html>\n"
    )
    return "\n".join(out)


def run(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description="Views, crops, and the review page of the Manual letter survey."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view", help="draw a ruled region of a page")
    view.add_argument("page", type=int)
    view.add_argument("--x", type=int, nargs=2, metavar=("X0", "X1"))
    view.add_argument("--y", type=int, nargs=2, metavar=("Y0", "Y1"))
    view.add_argument(
        "--scale",
        type=float,
        help="output pixels per scan pixel; default fits the region into 1450 pixels, up to 4",
    )
    view.add_argument("--boxes", action="store_true", help="draw the recorded letters, baselines, and joins")
    view.add_argument("--mark-x", type=int, nargs="+", default=[], help="draw a red vertical line at each x")
    view.add_argument(
        "--mark-y", type=int, nargs="+", default=[], help="draw a red horizontal line at each y"
    )
    view.add_argument("-o", "--out", type=Path, required=True)
    line = commands.add_parser("line", help="record a handwriting line's baseline and Short height")
    line.add_argument("page", type=int)
    line.add_argument("n", type=int)
    line.add_argument(
        "--base", type=int, nargs=4, required=True, metavar=("X_LEFT", "Y_LEFT", "X_RIGHT", "Y_RIGHT")
    )
    line.add_argument("--short", type=int, required=True)
    place = commands.add_parser("set", help="overwrite fields of one word")
    place.add_argument("word")
    place.add_argument("--line", type=int)
    place.add_argument("--x", type=int, nargs=2)
    place.add_argument("--cuts", type=int, nargs="*")
    place.add_argument("--base", type=int, help="baseline y for this word alone; 0 clears it")
    place.add_argument(
        "--letters", nargs="+", help="every letter of the word, as qsPea, qsPea.half, qsNo.alt"
    )
    place.add_argument("--joins", nargs="*", help="the join after each letter but the last: b x t 6 + or |")
    place.add_argument("--gloss")
    place.add_argument("--note")
    slant = commands.add_parser("slant", help="mark lines as written at a deliberate slant")
    slant.add_argument("page", type=int)
    slant.add_argument("lines", type=int, nargs="+")
    slant.add_argument("--upright", action="store_true", help="clear the mark instead")
    batch = commands.add_parser(
        "batch", help="run the commands in a file, one per text line, `#` starting a comment"
    )
    batch.add_argument("file", type=Path)
    commands.add_parser("check", help="validate every word list")
    commands.add_parser("build", help="write the crops, crops.json, and index.html")
    args = parser.parse_args(argv)

    if args.command == "view":
        source = page_image(args.page)
        x0, x1 = args.x or (0, source.width)
        y0, y1 = args.y or (0, source.height)
        scale = args.scale or min(4, 1450 / (x1 - x0), 1450 / (y1 - y0))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        render_view(args.page, x0, y0, x1, y1, scale, args.boxes, args.mark_x, args.mark_y).save(args.out)
        print(f"{args.out}: page {args.page} x {x0}-{x1} y {y0}-{y1} at {scale:.2f}")
    elif args.command == "line":
        set_line(args.page, args.n, args.base, args.short)
    elif args.command == "set":
        names = ("line", "x", "cuts", "letters", "joins", "gloss", "note")
        fields = {k: v for k in names if (v := getattr(args, k)) is not None}
        if args.base is not None:
            fields["base"] = args.base or None
        set_word(args.word, **fields)
    elif args.command == "slant":
        set_slanted(args.page, args.lines, not args.upright)
    elif args.command == "batch":
        for number, text in enumerate(args.file.read_text(encoding="utf-8").splitlines(), 1):
            words = shlex.split(text, comments=True)
            if not words:
                continue
            try:
                run(words)
            except (SystemExit, StopIteration, KeyError) as error:
                sys.exit(f"{args.file}:{number}: {text.strip()!r} failed: {error!r}")
    elif args.command == "check":
        pages = all_pages()
        faults = check(pages)
        print("\n".join(faults) or "ok")
        print(f"{len(unplaced(pages))} words have no position and no note")
        sys.exit(1 if faults else 0)
    else:
        build()


if __name__ == "__main__":
    run(sys.argv[1:])

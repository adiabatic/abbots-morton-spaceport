"""Print the glyph variants HarfBuzz picks for each sequence in a fixture file.

Each fixture line holds whitespace-separated family names (`qsMay qsPea`), and `#` starts a comment. Each sequence is shaped with the Senior font given by `--font`, and each output glyph is printed with its entry and exit anchors. To check a YAML change, capture the output before the change and diff it against the output after.

The anchors come from compiling `glyph_data/`, not from the font, so a font built from older YAML can show glyph names with the wrong anchors or none.

Examples:

    uv run python tools/shape_sequences.py path/to/sequences.txt
    uv run python tools/shape_sequences.py path/to/sequences.txt --features ss03

Fixture line format:

    qsMay qsPea          # bare sequence
    qsSee qsPea qsKey    # multi-glyph sequence
    # full-line comment, ignored

Each output line looks like this:

    qsMay qsPea -> qsMay/exit=(5,5) | qsPea.en-y5.ex-y0/entry=(0,5)/exit=(4,0)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_ir import JoinGlyph

DEFAULT_FONT = ROOT / "test" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"


def _load_ps_names() -> dict[str, int]:
    with PS_NAMES_PATH.open() as f:
        return yaml.safe_load(f)


def _font(font_path: Path) -> hb.Font:
    blob = hb.Blob.from_file_path(str(font_path))
    return hb.Font(hb.Face(blob))


def _shape(font: hb.Font, text: str, features: dict[str, bool]) -> list[str]:
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, features)
    return [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]


def _format_anchor(anchors: tuple[tuple[int, int], ...]) -> str | None:
    if not anchors:
        return None
    return ",".join(f"({x},{y})" for x, y in anchors)


def _format_glyph(name: str, meta: JoinGlyph | None) -> str:
    if meta is None:
        return f"{name}/<no-meta>"
    parts = [name]
    entry = _format_anchor(meta.entry)
    if entry is not None:
        parts.append(f"entry={entry}")
    elif meta.entry_explicitly_none:
        parts.append("entry=null")
    exit_ = _format_anchor(meta.exit)
    if exit_ is not None:
        parts.append(f"exit={exit_}")
    return "/".join(parts)


def _parse_features(spec: str | None) -> dict[str, bool]:
    if not spec:
        return {}
    features: dict[str, bool] = {}
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            tag, value = token.split("=", 1)
            features[tag.strip()] = value.strip().lower() not in {"0", "off", "false"}
        else:
            features[token] = True
    return features


def _parse_fixture(path: Path) -> list[list[str]]:
    sequences: list[list[str]] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        sequences.append(line.split())
    return sequences


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="Path to sequence fixture file")
    parser.add_argument(
        "--features",
        help="Comma-separated OpenType feature spec, e.g. 'ss03' or 'ss03=on,ss05=off'",
    )
    parser.add_argument(
        "--font",
        type=Path,
        default=DEFAULT_FONT,
        help=f"Path to font file (default: {DEFAULT_FONT.relative_to(ROOT)})",
    )
    args = parser.parse_args()

    if not args.fixture.is_file():
        parser.error(f"fixture file not found: {args.fixture}")
    if not args.font.is_file():
        parser.error(f"font file not found: {args.font} (run `make` first?)")

    ps_names = _load_ps_names()
    data = load_glyph_data(ROOT / "glyph_data")
    meta_map = compile_glyph_set(data, "senior").glyph_meta
    font = _font(args.font)
    features = _parse_features(args.features)

    sequences = _parse_fixture(args.fixture)
    for families in sequences:
        unknown = [f for f in families if f not in ps_names]
        if unknown:
            print(f"{' '.join(families)} -> <unknown family: {', '.join(unknown)}>")
            continue
        text = "".join(chr(ps_names[f]) for f in families)
        shaped = _shape(font, text, features)
        rendered = " | ".join(_format_glyph(name, meta_map.get(name)) for name in shaped)
        print(f"{' '.join(families)} -> {rendered}")


if __name__ == "__main__":
    main()

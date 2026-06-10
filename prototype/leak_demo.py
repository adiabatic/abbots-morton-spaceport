"""Demonstrate that the prototype's ZWNJ coverage rows are load-bearing, not vacuous (PLAN.md deviation 13).

The conformance gate passing proves the defended encoding is correct; this script proves the defenses are doing work. It rebuilds the prototype font with the section 7 ZWNJ coverage surgically removed — the backtrack-slot identity guard on qsTea_qsOy and the explicit uni200C membership at the second lookahead slot of the two-slot rows — and shapes the witness sequences through HarfBuzz. Each witness must then diverge from the settlement oracle in exactly the predicted way: the shaper skips the ZWNJ during context matching and settles a cell across the break. If a witness does NOT diverge, the corresponding defense is dead weight and the script fails, because the prototype would no longer be exercising the leak class that _add_zwnj_guards_for_two_position_forward_rules and _strip_post_zwnj_from_ignore_contexts exist to fix in today's pipeline.

Run as: uv run python prototype/leak_demo.py

Writes prototype/out/Proto-noguards.otf (a deliberately broken font; never shipped, never used by the other harnesses) and prints a witness table.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

PROTOTYPE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROTOTYPE_DIR.parent
sys.path.insert(0, str(PROTOTYPE_DIR))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import uharfbuzz as hb
from build_font import build_font
from fontTools.ttLib import TTFont

from build import _glyph_data
from emit import emit_fea
from settle import settle
from table import build_table

OUT_PATH = PROTOTYPE_DIR / "out" / "Proto-noguards.otf"

BACKTRACK_GUARD_LINE = "    sub uni200C qsTea_qsOy' by qsTea_qsOy;"

IT, TEA, MAY, OY, ZWNJ = 0xE670, 0xE652, 0xE665, 0xE679, 0x200C

# (label, codepoints, position of the witness slot in the output glyph stream, the leaked glyph the unguarded font must produce there)
WITNESSES = (
    ("backtrack slot: It ZWNJ Tea Oy", (IT, ZWNJ, TEA, OY), 2, "qsTea_qsOy.after-it"),
    ("second lookahead slot: It May ZWNJ Oy", (IT, MAY, ZWNJ, OY), 0, "qsIt"),
    (
        "second lookahead slot behind a backtrack class: Tea It May ZWNJ Oy",
        (TEA, IT, MAY, ZWNJ, OY),
        1,
        "qsIt.en-y5.ex-noentry",
    ),
)


def _is_two_slot_boundary_rule(code: str, boundary_class: str) -> bool:
    tokens = code.split()
    if not tokens or tokens[0] != "sub" or boundary_class not in tokens:
        return False
    index = tokens.index(boundary_class)
    return (
        index + 2 < len(tokens)
        and tokens[index + 1] == "by"
        and not tokens[index - 1].endswith("'")
        and any(token.endswith("'") for token in tokens[1:index])
    )


def strip_guards(fea: str) -> str:
    lines = fea.splitlines()
    if BACKTRACK_GUARD_LINE not in lines:
        raise SystemExit("expected backtrack guard line not found; emit.py output changed shape")
    lines.remove(BACKTRACK_GUARD_LINE)
    boundary_class = next(
        (line.split(" = ")[0] for line in lines if line.endswith("= [space uni200C];")), None
    )
    if boundary_class is None:
        raise SystemExit("expected [space uni200C] boundary class not found")
    stripped = []
    swapped = 0
    for line in lines:
        # Only the two-slot boundary rows lose their uni200C: the class reference in the second lookahead slot is swapped for a space-only twin. One-slot boundary rows keep the shared class, so the chokepoint and the first-lookahead coverage stay intact and the demonstration isolates the second slot.
        code = line.split("#")[0].rstrip()
        if _is_two_slot_boundary_rule(code, boundary_class):
            stripped.append(line.replace(f" {boundary_class} by", " @no_zwnj_boundary by"))
            swapped += 1
        else:
            stripped.append(line)
    if swapped == 0:
        raise SystemExit("no two-slot boundary rows found to strip")
    class_block_end = max(index for index, line in enumerate(stripped) if line.startswith("@"))
    stripped.insert(class_block_end + 1, "@no_zwnj_boundary = [space];")
    print(f"stripped: backtrack guard removed, {swapped} two-slot boundary rows demoted to [space]")
    return "\n".join(stripped) + "\n"


def main() -> int:
    table = build_table()
    fea = strip_guards(emit_fea(table))
    with redirect_stdout(io.StringIO()):
        build_font(_glyph_data(table), OUT_PATH, variant="senior", senior_fea=fea)

    tt = TTFont(str(OUT_PATH))
    hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(str(OUT_PATH))))

    failures = 0
    for label, codepoints, position, leaked in WITNESSES:
        text = "".join(chr(cp) for cp in codepoints)
        buf = hb.Buffer()
        buf.cluster_level = hb.BufferClusterLevel.MONOTONE_CHARACTERS
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(hb_font, buf, {})
        actual = [tt.getGlyphName(info.codepoint) for info in buf.glyph_infos]
        expected = [item.glyph_name for item in settle(text)]
        leak_reproduced = actual[position] == leaked and expected[position] != leaked
        status = "LEAK REPRODUCED" if leak_reproduced else "NO LEAK (defense was not load-bearing!)"
        if not leak_reproduced:
            failures += 1
        print(f"{status}: {label}")
        print(f"    oracle:    {' | '.join(expected)}")
        print(f"    unguarded: {' | '.join(actual)}")
    if failures:
        print(f"\n{failures} witness(es) did not leak — the corresponding ZWNJ defense is vacuous")
        return 1
    print("\nall witnesses leak without the defenses; the guarded font's conformance pass is meaningful")
    return 0


if __name__ == "__main__":
    sys.exit(main())

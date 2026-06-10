"""CoreText-vs-HarfBuzz smoke driver for the de-risking prototype (PLAN.md sections 1 and 6b).

Compiles prototype/coretext_smoke.swift once per session (swiftc -O, the route recon C verified), shapes the curated sequence set through CoreText via the binary and through uharfbuzz directly, and diffs GID-for-GID and position-for-position. CoreText reports cumulative pen positions in points at 100 pt; HarfBuzz reports per-glyph advances and offsets in font units; positions are compared after converting CoreText points to font units (times upem / 100, rounded to integers). Glyph names in the report come from fontTools (TTFont.getGlyphName), never from HarfBuzz's truncating glyph_to_string.

Run as: uv run python prototype/coretext_smoke.py [--font PATH] [--sequences PATH|-] [--verbose]

Sequences default to prototype/smoke_sequences.txt (hex codepoints per line, "#" labels); pass --sequences - to read the same format from stdin. Every sequence runs under both feature configurations (default and ss03).

When run against the default font, writes prototype/out/coretext_summary.json (the persisted smoke log) and records the CoreText half of the K3 verdict in prototype/out/budget.json (the HarfBuzz half comes from prototype/conform.py).

ZWNJ slots (identified by string index/cluster) are never compared by GID — shapers may substitute any invisible glyph there — only by the structural contract: zero advance on both sides and no ink (prototype/recon/shapers.md section 4, assertion 4).
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROTOTYPE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROTOTYPE_DIR.parent
sys.path.insert(0, str(PROTOTYPE_DIR))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from conform import record_k3_half

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

DEFAULT_FONT = PROTOTYPE_DIR / "out" / "Proto.otf"
DEFAULT_SEQUENCES = PROTOTYPE_DIR / "smoke_sequences.txt"
SWIFT_SOURCE = PROTOTYPE_DIR / "coretext_smoke.swift"
BINARY = PROTOTYPE_DIR / "out" / "coretext_smoke"
POINT_SIZE = 100.0
ZWNJ_CODEPOINT = 0x200C
FEATURE_CONFIGURATIONS = (("default", ()), ("ss03", ("ss03",)))
POINT_TOLERANCE = 0.01


@dataclass
class HarfBuzzGlyph:
    gid: int
    name: str
    cluster: int
    x_advance: int
    x: int
    y: int


@dataclass
class CoreTextGlyph:
    gid: int
    string_index: int
    x_points: float
    y_points: float
    postscript_name: str


def parse_sequences(source) -> list[tuple[str, list[int]]]:
    sequences = []
    for line_number, raw_line in enumerate(source, start=1):
        line, _, comment = raw_line.partition("#")
        tokens = line.split()
        if not tokens:
            continue
        try:
            codepoints = [int(token, 16) for token in tokens]
        except ValueError as error:
            raise SystemExit(f"bad hex codepoint on line {line_number}: {error}")
        label = comment.strip() or " ".join(tokens)
        sequences.append((label, codepoints))
    return sequences


def compile_binary() -> Path:
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    if not BINARY.exists() or BINARY.stat().st_mtime < SWIFT_SOURCE.stat().st_mtime:
        subprocess.run(
            ["swiftc", "-O", str(SWIFT_SOURCE), "-o", str(BINARY)],
            check=True,
        )
    return BINARY


class HarfBuzzShaper:
    def __init__(self, font_path: Path):
        self.tt = TTFont(str(font_path))
        self.upem = self.tt["head"].unitsPerEm
        self.hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(str(font_path))))
        self.glyph_set = self.tt.getGlyphSet()
        self._ink_cache: dict[str, bool] = {}

    def shape(self, codepoints: list[int], feature_tags: tuple[str, ...]) -> list[HarfBuzzGlyph]:
        buf = hb.Buffer()
        buf.cluster_level = hb.BufferClusterLevel.MONOTONE_CHARACTERS
        buf.add_str("".join(chr(cp) for cp in codepoints))
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {tag: True for tag in feature_tags})
        glyphs = []
        pen = 0
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            glyphs.append(
                HarfBuzzGlyph(
                    gid=info.codepoint,
                    name=self.tt.getGlyphName(info.codepoint),
                    cluster=info.cluster,
                    x_advance=pos.x_advance,
                    x=pen + pos.x_offset,
                    y=pos.y_offset,
                )
            )
            pen += pos.x_advance
        return glyphs

    def has_ink(self, glyph_name: str) -> bool:
        cached = self._ink_cache.get(glyph_name)
        if cached is None:
            pen = BoundsPen(self.glyph_set)
            self.glyph_set[glyph_name].draw(pen)
            cached = pen.bounds is not None
            self._ink_cache[glyph_name] = cached
        return cached


def run_coretext(
    binary: Path, font_path: Path, codepoints: list[int], feature_tags: tuple[str, ...]
) -> tuple[float, list[CoreTextGlyph]]:
    command = [str(binary), str(font_path)]
    if feature_tags:
        command += ["--features", ",".join(feature_tags)]
    command += [f"{cp:04X}" for cp in codepoints]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    lines = completed.stdout.splitlines()
    if not lines or not lines[0].startswith("WIDTH "):
        raise SystemExit(f"unexpected harness output: {completed.stdout!r}")
    width = float(lines[0].split()[1])
    glyphs = []
    for line in lines[1:]:
        gid, string_index, x, y, postscript_name = line.split("\t")
        glyphs.append(
            CoreTextGlyph(
                gid=int(gid),
                string_index=int(string_index),
                x_points=float(x),
                y_points=float(y),
                postscript_name=postscript_name,
            )
        )
    return width, glyphs


def diff_glyph_streams(
    codepoints: list[int],
    shaper: HarfBuzzShaper,
    harfbuzz_glyphs: list[HarfBuzzGlyph],
    width: float,
    coretext_glyphs: list[CoreTextGlyph],
) -> list[str]:
    details: list[str] = []
    if len(harfbuzz_glyphs) != len(coretext_glyphs):
        details.append(f"glyph count: HarfBuzz {len(harfbuzz_glyphs)} vs CoreText {len(coretext_glyphs)}")
        return details

    scale = shaper.upem / POINT_SIZE
    for slot, (hb_glyph, ct_glyph) in enumerate(zip(harfbuzz_glyphs, coretext_glyphs)):
        slot_is_zwnj = (
            hb_glyph.cluster < len(codepoints)
            and codepoints[hb_glyph.cluster] == ZWNJ_CODEPOINT
            and ct_glyph.string_index < len(codepoints)
            and codepoints[ct_glyph.string_index] == ZWNJ_CODEPOINT
        )
        ct_name = shaper.tt.getGlyphName(ct_glyph.gid)
        if slot_is_zwnj:
            if hb_glyph.x_advance != 0:
                details.append(f"slot {slot} (ZWNJ): HarfBuzz x_advance {hb_glyph.x_advance}, want 0")
            if shaper.has_ink(hb_glyph.name) or shaper.has_ink(ct_name):
                details.append(
                    f"slot {slot} (ZWNJ): inked glyph at ZWNJ slot (hb {hb_glyph.name}, ct {ct_name})"
                )
            next_x = coretext_glyphs[slot + 1].x_points if slot + 1 < len(coretext_glyphs) else width
            if abs(next_x - ct_glyph.x_points) > POINT_TOLERANCE:
                details.append(
                    f"slot {slot} (ZWNJ): CoreText advance {next_x - ct_glyph.x_points:.4f} pt, want 0"
                )
            continue
        if hb_glyph.gid != ct_glyph.gid:
            details.append(
                f"slot {slot}: GID HarfBuzz {hb_glyph.gid} ({hb_glyph.name}) vs CoreText {ct_glyph.gid} ({ct_name})"
            )
            continue
        ct_x_units = round(ct_glyph.x_points * scale)
        ct_y_units = round(ct_glyph.y_points * scale)
        if (hb_glyph.x, hb_glyph.y) != (ct_x_units, ct_y_units):
            details.append(
                f"slot {slot} ({hb_glyph.name}): position HarfBuzz ({hb_glyph.x}, {hb_glyph.y}) vs CoreText ({ct_x_units}, {ct_y_units}) font units"
            )
    return details


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--font",
        type=Path,
        default=DEFAULT_FONT,
        help="font to shape through (default: prototype/out/Proto.otf)",
    )
    parser.add_argument(
        "--sequences",
        default=str(DEFAULT_SEQUENCES),
        help="sequence file (hex codepoints per line, # labels), or - for stdin (default: prototype/smoke_sequences.txt)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print glyph-name streams for passing sequences too"
    )
    args = parser.parse_args()

    if not args.font.exists():
        print(f"error: {args.font} does not exist", file=sys.stderr)
        return 2

    if args.sequences == "-":
        sequences = parse_sequences(sys.stdin)
    else:
        sequences = parse_sequences(Path(args.sequences).read_text().splitlines())
    if not sequences:
        print("error: no sequences to run", file=sys.stderr)
        return 2

    binary = compile_binary()
    shaper = HarfBuzzShaper(args.font)
    expected_postscript_name = shaper.tt["name"].getDebugName(6)

    failures = 0
    runs = 0
    results: list[dict] = []
    for label, codepoints in sequences:
        for config, feature_tags in FEATURE_CONFIGURATIONS:
            runs += 1
            width, coretext_glyphs = run_coretext(binary, args.font, codepoints, feature_tags)
            harfbuzz_glyphs = shaper.shape(codepoints, feature_tags)
            details = []
            for glyph in coretext_glyphs:
                if glyph.postscript_name != expected_postscript_name:
                    details.append(
                        f"CoreText fell back to {glyph.postscript_name} (expected {expected_postscript_name})"
                    )
                    break
            if not details:
                details = diff_glyph_streams(codepoints, shaper, harfbuzz_glyphs, width, coretext_glyphs)
            status = "PASS" if not details else "FAIL"
            if details:
                failures += 1
            results.append(
                {
                    "label": label,
                    "config": config,
                    "input": " ".join(f"{cp:04X}" for cp in codepoints),
                    "status": status,
                    "harfbuzz": [g.name for g in harfbuzz_glyphs],
                    "coretext": [shaper.tt.getGlyphName(g.gid) for g in coretext_glyphs],
                    "details": details,
                }
            )
            if details or args.verbose:
                print(f"{status}  [{config}] {label}")
                print(f"      input: {' '.join(f'{cp:04X}' for cp in codepoints)}")
                print(f"      harfbuzz: {' | '.join(g.name for g in harfbuzz_glyphs)}")
                print(f"      coretext: {' | '.join(shaper.tt.getGlyphName(g.gid) for g in coretext_glyphs)}")
                for detail in details:
                    print(f"      {detail}")
            else:
                print(f"{status}  [{config}] {label}")

    print()
    print(f"CoreText smoke: {runs - failures}/{runs} runs agree (font: {args.font})")
    if args.font.resolve() == DEFAULT_FONT.resolve():
        summary = {
            "font": str(args.font),
            "sequences": len(sequences),
            "runs": runs,
            "failures": failures,
            "pass": failures == 0,
            "results": results,
        }
        summary_path = PROTOTYPE_DIR / "out" / "coretext_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        print(f"summary written to {summary_path}")
        record_k3_half("coretext", {"pass": failures == 0, "runs": runs, "failures": failures})
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""Build the six shipped fonts, Mono, Junior and Senior in Regular and Bold, into one directory under their shipped file names (`uv run python -m rebuild.tools.build_fonts`, `make rebuild-fonts`). It writes nothing under site/.

Mono and Junior come from the rune files and the non-Quikscript glyph files (`build_family.build_mono_and_junior`), which need no settlement table, so they build while the tables are stale or absent. Senior is the rebuild's own build: the command copies rebuild/out/m1/M1.otf and M1-Bold.otf, with the glyph-facts sidecar the build writes beside them (`glyph_facts.FILE_NAME`) under the name the `data-expect` checker looks for beside the Regular (`check.glyph_facts_path`), and before it writes anything it refuses with `StaleSeniorError`, naming the build command, when either is missing or no longer the build of its inputs (`senior_staleness`). That decision reads run_m1's own records:

- the run_m1 green record (`cycle_paths.RUN_M1_GREEN`) must hold every line of the run_m1 skip key (`artifact_cycle.run_m1_skip_lines`) as it reads on disk, a comparison-side line aside (`artifact_cycle.comparison_side_label`), since no font reads one;
- the Stage A record must be present (`fingerprint.read_stage_a`), since a build deletes it before its first write under rebuild/out/m1 and writes it back only once its gates pass, so its absence means a build that wrote there stopped or failed since the green one.

The module lives under rebuild/tools/ because the check imports `artifact_cycle`, which reaches the comparison side, and no build-side module under rebuild/pipeline/ may (rebuild/test_build_code_closure.py).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Sequence

from rebuild.pipeline import build_family, fingerprint, glyph_facts, spec_load
from rebuild.pipeline.compile_font import FontSourceError
from rebuild.validation import check

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "rebuild" / "out" / "fonts"
FONTS = ("mono", "junior", "senior")
# The rebuild's Senior by style, as run_m1 writes it (`run_m1.OUT_DIR`, `run_m1.BOLD_FONT`).
M1_OUT = Path("rebuild") / "out" / "m1"
SENIOR_SOURCES = {"Regular": "M1.otf", "Bold": "M1-Bold.otf"}
RUN_M1_COMMAND = "uv run python -m rebuild.pipeline.run_m1"


class StaleSeniorError(Exception):
    pass


def senior_staleness(root: Path = REPO_ROOT) -> str | None:
    """Why the rebuild's Senior under `root`'s rebuild/out/m1 is not the build of the inputs on disk, or None when it is (the module docstring gives the test)."""
    from rebuild.tools import cycle_paths
    from rebuild.tools.artifact_cycle import (
        capped_labels,
        comparison_side_label,
        moved_input_labels,
        read_green_record,
        run_m1_skip_files,
    )

    root = Path(root)
    m1 = root / M1_OUT
    missing = [
        (M1_OUT / name).as_posix()
        for name in (*SENIOR_SOURCES.values(), glyph_facts.FILE_NAME)
        if not (m1 / name).is_file()
    ]
    if missing:
        return f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing"
    if fingerprint.read_stage_a(m1) is None:
        return f"{M1_OUT.as_posix()} holds no Stage A record, so a build that wrote there since the last green one stopped or failed"
    record = read_green_record(cycle_paths.RUN_M1_GREEN)
    if record is None or not isinstance(record.get("files"), dict):
        return "run_m1 has no green build on record"
    moved = moved_input_labels(record, run_m1_skip_files(root)) or []
    font_side = [label for label in moved if not comparison_side_label(label)]
    if font_side:
        return f"its inputs moved since run_m1's last green build: {capped_labels(font_side)}"
    return None


def require_current_senior(root: Path = REPO_ROOT) -> None:
    """Raise `StaleSeniorError`, naming the build command, while `senior_staleness` gives a reason."""
    reason = senior_staleness(root)
    if reason is not None:
        raise StaleSeniorError(
            f"the rebuild's Senior is not current: {reason}. Build it with `{RUN_M1_COMMAND}` (a `make artifact-cycle` pass runs it too)"
        )


def _copy_if_changed(source: Path, path: Path) -> None:
    data = source.read_bytes()
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)


def _copy_senior(out_dir: Path, root: Path) -> list[Path]:
    """Copy the rebuild's Senior, Regular and Bold, into `out_dir` under the shipped file names, and its glyph-facts sidecar beside them, leaving a file that already holds the same bytes alone, as the Mono and Junior builds do, and return the fonts' paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for style, source in SENIOR_SOURCES.items():
        path = out_dir / build_family.font_file_name("senior", style)
        _copy_if_changed(root / M1_OUT / source, path)
        written.append(path)
    regular = out_dir / build_family.font_file_name("senior", "Regular")
    _copy_if_changed(root / M1_OUT / glyph_facts.FILE_NAME, check.glyph_facts_path(regular))
    return written


def build(out_dir: Path, fonts: Sequence[str] = FONTS, root: Path = REPO_ROOT) -> list[Path]:
    """Write the fonts `fonts` names, each in Regular and Bold, into `out_dir` under their shipped file names, and return their paths. Senior's currency is checked before anything is written."""
    out_dir, root = Path(out_dir), Path(root)
    if "senior" in fonts:
        require_current_senior(root)
    written = build_family.build_mono_and_junior(out_dir, [font for font in fonts if font != "senior"], root)
    if "senior" in fonts:
        written += _copy_senior(out_dir, root)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rebuild.tools.build_fonts",
        description="Build the shipped fonts: Mono and Junior from the rune files and the glyph files, and Senior from the rebuild's build under rebuild/out/m1.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"the directory the fonts go to (default {DEFAULT_OUT_DIR.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--fonts",
        nargs="+",
        choices=FONTS,
        default=list(FONTS),
        help="the fonts to build, each in Regular and Bold (default all three)",
    )
    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        written = build(args.out, tuple(dict.fromkeys(args.fonts)))
    except (StaleSeniorError, build_family.FamilyBuildError, spec_load.SpecError, FontSourceError) as error:
        print(f"build_fonts: {error}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path}")
    print(f"{len(written)} fonts in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

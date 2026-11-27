"""Publish the six shipped fonts, Mono, Junior and Senior in Regular and Bold, into site/ under their shipped file names (`uv run python -m engine.tools.build_fonts`, which `make all` runs with `--build`). The font suite's session publishes through the same function before it collects, without `--build`.

Mono and Junior come from the rune files and the non-Quikscript glyph files (`build_family.build_mono_and_junior`), which need no settlement table, so they build while the tables are stale or absent. Senior is run_build's build: the command copies engine/out/build/Senior-Regular.otf and Senior-Bold.otf, and before it writes anything it refuses with `StaleSeniorError`, naming the build command, when either is missing or no longer the build of its inputs (`senior_staleness`). `--build` runs that build first (`RUN_BUILD_COMMAND`) when the Senior is not current, and refuses only when the build fails. The glyph-facts sidecar the `data-expect` checker reads stays beside the build under engine/out/build (`glyph_facts.FILE_NAME`), so site/, which the deploy uploads whole, holds only what the site serves. The staleness decision reads run_build's own records:

- the run_build green record (`cycle_paths.BUILD_GREEN`) must hold every line of the run_build skip key (`artifact_cycle.run_build_skip_lines`) as it reads on disk, a comparison-side line aside (`artifact_cycle.comparison_side_label`), since no font reads one;
- the Stage A record must be present (`fingerprint.read_stage_a`), since a build deletes it before its first write under engine/out/build and writes it back only once its gates pass, so its absence means a build that wrote there stopped or failed since the green one.

The module lives under engine/tools/ because the check imports `artifact_cycle`, which reaches the comparison side, and no build-side module under engine/pipeline/ may (engine/test_build_code_closure.py).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from engine.pipeline import build_family, fingerprint, glyph_facts, spec_load
from engine.pipeline.compile_font import FontSourceError

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_DIR = REPO_ROOT / "site"
FONTS = ("mono", "junior", "senior")
# The build's Senior by style, as run_build writes it (`run_build.OUT_DIR`, `run_build.BOLD_FONT`).
BUILD_OUT = Path("engine") / "out" / "build"
SENIOR_SOURCES = {"Regular": "Senior-Regular.otf", "Bold": "Senior-Bold.otf"}
RUN_BUILD_MODULE = "engine.pipeline.run_build"
RUN_BUILD_COMMAND = f"uv run python -m {RUN_BUILD_MODULE}"


class StaleSeniorError(Exception):
    pass


def senior_staleness(root: Path = REPO_ROOT) -> str | None:
    """Why the build's Senior under `root`'s engine/out/build is not the build of the inputs on disk, or None when it is (the module docstring gives the test)."""
    from engine.tools import cycle_paths
    from engine.tools.artifact_cycle import (
        capped_labels,
        comparison_side_label,
        moved_input_labels,
        read_green_record,
        run_build_skip_files,
    )

    root = Path(root)
    build_out = root / BUILD_OUT
    missing = [
        (BUILD_OUT / name).as_posix()
        for name in (*SENIOR_SOURCES.values(), glyph_facts.FILE_NAME)
        if not (build_out / name).is_file()
    ]
    if missing:
        return f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing"
    if fingerprint.read_stage_a(build_out) is None:
        return f"{BUILD_OUT.as_posix()} holds no Stage A record, so a build that wrote there since the last green one stopped or failed"
    record = read_green_record(cycle_paths.BUILD_GREEN)
    if record is None or not isinstance(record.get("files"), dict):
        return "run_build has no green build on record"
    moved = moved_input_labels(record, run_build_skip_files(root)) or []
    font_side = [label for label in moved if not comparison_side_label(label)]
    if font_side:
        return f"its inputs moved since run_build's last green build: {capped_labels(font_side)}"
    return None


def require_current_senior(root: Path = REPO_ROOT) -> None:
    """Raise `StaleSeniorError`, naming the build command, while `senior_staleness` gives a reason."""
    reason = senior_staleness(root)
    if reason is not None:
        raise StaleSeniorError(
            f"the build's Senior is not current: {reason}. Build it with `{RUN_BUILD_COMMAND}`, or with `make all`, which runs it and publishes the fonts (a `make artifact-cycle` pass runs it too)"
        )


def build_stale_senior(root: Path = REPO_ROOT) -> None:
    """Run the Senior's build (`RUN_BUILD_COMMAND`, in a child with the terminal as its output) when `senior_staleness` gives a reason, and raise `StaleSeniorError` when the build fails or leaves the Senior stale."""
    reason = senior_staleness(root)
    if reason is None:
        return
    print(
        f"build_fonts: the build's Senior is not current ({reason}); building it with `{RUN_BUILD_COMMAND}`"
    )
    sys.stdout.flush()
    returncode = subprocess.run([sys.executable, "-m", RUN_BUILD_MODULE], cwd=root, check=False).returncode
    if returncode != 0:
        raise StaleSeniorError(
            f"`{RUN_BUILD_COMMAND}` failed (exit {returncode}), so the Senior is not published"
        )
    require_current_senior(root)


def _copy_if_changed(source: Path, path: Path) -> None:
    data = source.read_bytes()
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)


def _copy_senior(out_dir: Path, root: Path) -> list[Path]:
    """Copy the build's Senior, Regular and Bold, into `out_dir` under the shipped file names, leaving a file that already holds the same bytes alone, as the Mono and Junior builds do, so a server's watch on the fonts sees only a real change, and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for style, source in SENIOR_SOURCES.items():
        path = out_dir / build_family.font_file_name("senior", style)
        _copy_if_changed(root / BUILD_OUT / source, path)
        written.append(path)
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
        prog="python -m engine.tools.build_fonts",
        description="Publish the shipped fonts into site/: Mono and Junior from the rune files and the glyph files, and Senior from run_build's build under engine/out/build.",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help=f"run the Senior's build (`{RUN_BUILD_COMMAND}`) first when it is not current, instead of refusing",
    )
    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        if args.build:
            build_stale_senior()
        written = build(SITE_DIR)
    except (StaleSeniorError, build_family.FamilyBuildError, spec_load.SpecError, FontSourceError) as error:
        print(f"build_fonts: {error}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}")
    print(f"{len(written)} fonts in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Regenerate the mini-M1 bundle beside this file from the live build output in `rebuild/out/m1`.

The bundle lets the contracts lane test the surface cache without reading `rebuild/out/`. `rebuild/test_unit_cache.py` checks that a warm store serves every unit, that an incremental rebuild is byte-identical to a from-scratch one, and that a corrupt store falls back to a full build. Those are properties of `unit_cache.py` and `build_m1`'s fan-out, not of any glyph, so a frozen workload tests them as well as the live one and takes seconds instead of minutes. The review surface's worked examples (which position the enricher judges, how the drafter words a policy record, what the ink comparator reports for a placed run, whether a witness re-settles to the row it was drawn from) also read the frozen windows, because none of them is a claim about the current corpus.

The bundle holds:

- `audit.tsv`, the live divergence audit filtered to the union of two sets: every window drawn from `LETTERS` and `BOUNDARIES`, and every window in `EXAMPLE_WINDOWS`, which the worked examples in `rebuild/test_review_enrich.py` and `rebuild/test_review_drafts.py` name by codepoint. Regeneration fails when a window in `EXAMPLE_WINDOWS` selects no row, so a lost example is found here and not in a test failure after a later rune edit.
- `baseline-<config>.subset.tsv.gz` for each of `conform.ACCEPTANCE_CONFIGS` and no other configuration, sliced to the same windows. The live build directory can also hold subset tables for other configurations, which nothing reads.
- `M1.otf`, a copy of the after font the slices were extracted against.
- `settlement-default.tsv` and `treaties-default.tsv`, which `rebuild/test_review_tablediff.py` and the table-diff build test use as real tables beside a real font.
- `pin.json`, the tree and blob shas of `pin.PINNED_PATHS` at the commit this ran on.

All of it must be regenerated together. A slice from one build beside a font from another, or a pin from a third, would make the enricher report glyph disagreements caused by the bundle and not by the code.

The pin replaces a checked-in copy of the spec. `build_m1` takes a `spec_root`, and the `mini_bundle` fixture in `rebuild/conftest.py` writes the pinned objects out of git into a session temp directory that every mini-bundle test passes as that root. So the enricher re-derives the settlement these rows were written under, a rune edit cannot make the frozen `new` cells stale, and there is no second copy of the runes in the tree to edit by mistake. Everything else in a mini build (the fingerprints, the git head, the manifest's relative paths, the corpus the pin drafts are validated against) comes from the repo root, because those describe the checkout and not the workload.

Two checks make HEAD safe to pin. The pinned paths must be clean, so HEAD's bytes are the working tree's. The live build's recorded `data` fingerprint must equal the one the tree hashes to now, so the frozen rows settled under that spec. If either fails, the pin could name a spec no build ran, and a content-addressed pin cannot detect that later.

Run it after `run_m1` has written a fresh `rebuild/out/m1`:

    uv run python rebuild/review/fixtures/mini/regenerate.py
"""

import gzip
import io
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from rebuild.pipeline import fingerprint  # noqa: E402
from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS  # noqa: E402
from rebuild.review.fixtures.mini import pin  # noqa: E402

LIVE = REPO_ROOT / "rebuild" / "out" / "m1"

LETTERS = {"E650", "E652", "E653", "E668"}
BOUNDARIES = {"0020", "200C", "00B7"}

# The windows the review surface's worked examples name by codepoint. Each test asks for a specific window, not for any unit of a class, so a member that selects no audit row means that window has left the audit, not that the filter is wrong.
EXAMPLE_WINDOWS = frozenset(
    {
        "0020:E650:E650",
        "200C:E652:E679",
        "200C:E665:E679:E650",
        "E650:E650:E67A",
        "E650:E650:200C:E67A",
        "E658:E666",
        "E650:200C:E650:E665",
        "E650:200C:E650:E670",
        "E650:E670:E65D",
        "E652:E670",
        "E652:E679",
        "E652:E653:E67A:E652",
        "E665:E666:E666",
        "E665:E670:E652:E679",
        "E670:E670",
        "E670:E67A:E670:E665",
    }
)


def selected_windows(audit: Path) -> tuple[str, list[str]]:
    """Return the audit's header and every row either filter keeps: a window drawn entirely from `LETTERS` and `BOUNDARIES` that contains at least one letter, or a window in `EXAMPLE_WINDOWS`. The example windows are added by name instead of by widening `LETTERS`, which would pull in every window of the letters they use."""
    lines = audit.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], lines[1:]
    kept = []
    for row in rows:
        window = row.split("\t")[1]
        parts = set(window.split(":"))
        if (parts <= (LETTERS | BOUNDARIES) and parts & LETTERS) or window in EXAMPLE_WINDOWS:
            kept.append(row)
    return header, kept


def main() -> int:
    if not (LIVE / "divergence-audit.tsv").exists():
        print(f"no live build output under {LIVE}; run run_m1 first", file=sys.stderr)
        return 1
    dirty = pin.dirty_paths()
    if dirty:
        print("the pin names committed objects; commit (or stash) these first:", file=sys.stderr)
        for line in dirty:
            print(line, file=sys.stderr)
        return 1
    recorded = fingerprint.read_stage_a(LIVE)
    if recorded is None or recorded["data"] != fingerprint.data_value(REPO_ROOT):
        print(
            "rebuild/out/m1 was not built from the spec as it stands on disk; run run_m1 first",
            file=sys.stderr,
        )
        return 1
    header, kept = selected_windows(LIVE / "divergence-audit.tsv")
    if len(kept) <= 200:
        print("the letter filter no longer selects a meaningful workload", file=sys.stderr)
        return 1
    windows = {row.split("\t")[1] for row in kept}
    dissolved = sorted(EXAMPLE_WINDOWS - windows)
    if dissolved:
        print("these worked-example windows select no audit row any more:", file=sys.stderr)
        for window in dissolved:
            print(f"  {window}", file=sys.stderr)
        print(
            "a dissolved exemplar wants a replacement window in EXAMPLE_WINDOWS and in the tests that "
            "name it, not a regenerated bundle without it",
            file=sys.stderr,
        )
        return 1
    (HERE / "audit.tsv").write_text("\n".join([header] + kept) + "\n", encoding="utf-8")

    tables = [LIVE / f"baseline-{config}.subset.tsv.gz" for config in ACCEPTANCE_CONFIGS]
    absent = [table.name for table in tables if not table.exists()]
    if absent:
        print(f"the live build has no {', '.join(absent)}; run run_m1 first", file=sys.stderr)
        return 1
    for table in tables:
        out = HERE / table.name
        with gzip.open(table, "rt", encoding="utf-8", newline="") as source:
            with open(out, "wb") as raw:
                # gzip writes the current time into its header unless told otherwise, which would make every regeneration a diff even when the rows are unchanged. mtime=0 and an empty filename make the file depend only on its content.
                with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0) as packed:
                    with io.TextIOWrapper(packed, encoding="utf-8", newline="") as sink:
                        for line in source:
                            if line.startswith("#") or line.split("\t", 1)[0] in windows:
                                sink.write(line)
    shutil.copyfile(LIVE / "M1.otf", HERE / "M1.otf")
    for table in ("settlement-default.tsv", "treaties-default.tsv"):
        shutil.copyfile(LIVE / table, HERE / table)
    record = pin.write_pin()
    print(
        f"{len(kept)} audit rows over {len(windows)} windows, spec pinned at {record['head'][:12]}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

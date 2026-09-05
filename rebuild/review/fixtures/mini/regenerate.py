"""Regenerate the hermetic mini-M1 bundle beside this file from the live build output.

The bundle is what lets `rebuild/test_unit_cache.py` prove the surface cache's contracts — a warm store serves every unit, an incremental rebuild lands byte-identical on a from-scratch one, a corrupt store degrades — in the contracts lane, at full xdist width, without any test reaching `rebuild/out/`. Those are properties of `unit_cache.py` and `build_m1`'s fan-out rather than of any glyph, so a frozen workload witnesses them as well as the live one and costs seconds instead of minutes. The same reasoning carries the review surface's worked examples: which position the enricher judges, how the drafter words a policy record, what the ink comparator makes of a placed run, whether a witness re-settles to the row it was drawn from — none of them is a claim about today's corpus, so all of them read the frozen windows here rather than the live audit.

What it holds: `audit.tsv`, the live divergence audit filtered twice over — every window drawn from the four letters below plus the boundary tokens, and every window named in `EXAMPLE_WINDOWS`, which is the set the worked examples in `rebuild/test_review_enrich.py` and `rebuild/test_review_drafts.py` name by codepoint. The second filter is what moved those examples off the live audit: each one wants a particular window rather than a particular corpus, so freezing the whole set here costs a megabyte and buys a lane. A window in that set that selects no row at all is a dissolved exemplar and refuses the regeneration, which is where a lost example should be found rather than in a red lane a rune edit later. Beside the audit: `baseline-<config>.subset.tsv.gz` for each of `conform.ACCEPTANCE_CONFIGS` and no other configuration, sliced to those same windows — the live build still carries subset tables from configurations an earlier matrix accepted, and a slice of one of those is a file nothing reads; `M1.otf`, a frozen copy of the after-font the slices were extracted against; the default settlement and treaty tables, which `rebuild/test_review_tablediff.py` and the table-diff build test want as a directory of real tables beside a real font rather than as anything about today's rules; and `pin.json`, the tree and blob shas of `glyph_data/runes`, `rebuild/schema`, `rebuild/script.yaml` and `rebuild/m1-divergences.yaml` at the commit this ran on (`pin.PINNED_PATHS` is the authority). All of it moves together, and only together — a slice from one build beside a font from another, or a pin from a third, would have the enricher reporting glyph disagreements that are the bundle's fault rather than the code's.

The pin is what makes the bundle hermetic, and it stands in for a checked-in copy of the spec. `build_m1` takes a `spec_root`; the `mini_bundle` fixture in `rebuild/conftest.py` materializes the pinned objects out of git into a session temp directory and every mini-bundle test hands that over, so the settlement the enricher re-derives is the one these rows were written under, a rune edit cannot leave the frozen `new` cells describing a rebuild that no longer happens, and no second copy of the runes sits in the tree waiting to be edited by mistake. Everything else in a mini build still comes from the repo root — the fingerprints, the git head, the relative paths in the manifest, the corpus the pin drafts validate against — because those are facts about this checkout rather than about the workload.

Two guards are what make HEAD trustworthy to pin against. The pinned paths must be clean, so HEAD's bytes are the working tree's bytes; and the live build's recorded `data` fingerprint must equal the one the tree hashes to now, so the rows being frozen really did settle under them. Fail either and the pin would name a spec no build ever ran, which is the one failure a content-addressed pin cannot detect later.

Run it after `run_m1` has left a fresh `rebuild/out/m1`:

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

# The windows the review surface's worked examples name, and the reason the bundle is filtered on two rules rather than one. Every test that reaches for one of these spells its codepoints out — none asks for "some unit of class X" — so the set is a list of exemplars rather than a sample, and a member selecting nothing means the exemplar dissolved rather than that the filter drifted.
EXAMPLE_WINDOWS = frozenset(
    {
        "0020:E650:E650",
        "200C:E652:E679",
        "200C:E665:E679:E650",
        "E650:E650:E670",
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
    """The audit's header plus every row either filter keeps: a window drawn entirely from `LETTERS` and `BOUNDARIES` and touching at least one letter, or a window `EXAMPLE_WINDOWS` names outright. The two are a union rather than a widened letter set on purpose — the exemplars reach letters the four-letter slice has no business dragging in whole."""
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
                # A gzip header stamps the wall clock unless it is told not to, which would leave every regeneration a diff even where the rows did not move. mtime=0 and an empty filename make the container a function of the content alone.
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

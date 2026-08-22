"""Regenerate the hermetic mini-M1 bundle beside this file from the live build output.

The bundle is what lets `rebuild/test_unit_cache.py` prove the surface cache's contracts — a warm store serves every unit, an incremental rebuild lands byte-identical on a from-scratch one, a corrupt store degrades — in the contracts lane, at full xdist width, without any test reaching `rebuild/out/`. Those are properties of `unit_cache.py` and `build_m1`'s fan-out rather than of any glyph, so a frozen workload witnesses them as well as the live one and costs seconds instead of minutes.

What it holds: `audit.tsv`, the live divergence audit filtered to windows drawn from the four letters below plus the boundary tokens; `baseline-*.subset.tsv.gz`, each live subset table sliced to those same windows; `M1.otf`, a frozen copy of the after-font the slices were extracted against; the default settlement and treaty tables, which `rebuild/test_review_tablediff.py` and the table-diff build test want as a directory of real tables beside a real font rather than as anything about today's rules; `m1-divergences.yaml`, the ledger whose class names the audit rows carry; and `spec/`, a copy of the spec those rows settled under — `glyph_data/runes/*.yaml`, `rebuild/script.yaml` and `rebuild/schema/*.json`, laid out at the paths `enrich.load_spec` expects under a root. All of it moves together, and only together — a slice from one build beside a font from another would have the enricher reporting glyph disagreements that are the bundle's fault rather than the code's.

The spec copy is what makes the bundle hermetic. `build_m1` takes a `spec_root`, and the mini-bundle tests pass this directory's `spec/`, so the settlement the enricher re-derives is the one these rows were written under and a rune edit cannot leave the frozen `new` cells describing a rebuild that no longer happens. Everything else in a mini build still comes from the repo root — the fingerprints, the git head, the relative paths in the manifest, the corpus the pin drafts validate against — because those are facts about this checkout rather than about the workload.

Run it after `run_m1` has left a fresh `rebuild/out/m1`:

    uv run python rebuild/review/fixtures/mini/regenerate.py
"""

import gzip
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
LIVE = REPO_ROOT / "rebuild" / "out" / "m1"

LETTERS = {"E650", "E652", "E653", "E668"}
BOUNDARIES = {"0020", "200C", "00B7"}
# The spec `enrich.load_spec` reads under a root, at the paths it reads them from, so `spec/` can be handed to build_m1 as a spec_root unchanged.
SPEC_TREES = ("glyph_data/runes/*.yaml", "rebuild/schema/*.json")
SPEC_FILES = ("rebuild/script.yaml",)


def selected_windows(audit: Path) -> tuple[str, list[str]]:
    lines = audit.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], lines[1:]
    kept = []
    for row in rows:
        parts = set(row.split("\t")[1].split(":"))
        if parts <= (LETTERS | BOUNDARIES) and parts & LETTERS:
            kept.append(row)
    return header, kept


def freeze_spec() -> int:
    """The spec the frozen rows settled under, copied to `spec/` at the same relative paths it lives at in the repo. Written fresh each time — a stale rune left behind would be a spec no build ever ran."""
    root = HERE / "spec"
    shutil.rmtree(root, ignore_errors=True)
    copied = 0
    for pattern in SPEC_TREES:
        for source in sorted(REPO_ROOT.glob(pattern)):
            target = root / source.relative_to(REPO_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied += 1
    for name in SPEC_FILES:
        source = REPO_ROOT / name
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied += 1
    return copied


def main() -> int:
    if not (LIVE / "divergence-audit.tsv").exists():
        print(f"no live build output under {LIVE}; run run_m1 first", file=sys.stderr)
        return 1
    header, kept = selected_windows(LIVE / "divergence-audit.tsv")
    if len(kept) <= 200:
        print("the letter filter no longer selects a meaningful workload", file=sys.stderr)
        return 1
    windows = {row.split("\t")[1] for row in kept}
    (HERE / "audit.tsv").write_text("\n".join([header] + kept) + "\n", encoding="utf-8")

    for table in sorted(LIVE.glob("baseline-*.subset.tsv.gz")):
        out = HERE / table.name
        with gzip.open(table, "rt", encoding="utf-8", newline="") as source:
            with gzip.open(out, "wt", encoding="utf-8", newline="", compresslevel=9) as sink:
                for line in source:
                    if line.startswith("#") or line.split("\t", 1)[0] in windows:
                        sink.write(line)
    shutil.copyfile(LIVE / "M1.otf", HERE / "M1.otf")
    for table in ("settlement-default.tsv", "treaties-default.tsv"):
        shutil.copyfile(LIVE / table, HERE / table)
    shutil.copyfile(REPO_ROOT / "rebuild" / "m1-divergences.yaml", HERE / "m1-divergences.yaml")
    frozen = freeze_spec()
    print(f"{len(kept)} audit rows over {len(windows)} windows, {frozen} spec files frozen", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

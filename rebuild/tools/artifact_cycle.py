"""The one-command driver for the commit-time artifact cycle.

It mechanizes the commit-time sequence: snapshot the current review surface (the only recovery copy, since everything under rebuild/out is gitignored), recompile M1.otf and vet it, rebuild the review surface in place, carry prior verdicts forward onto the fresh manifest, merge the carried file into the live autosave (rebuild.tools.merge_verdicts, so the app needs no manual import; --no-merge opts out), land echo-prefill verdicts onto the freshly restamped autosave (rebuild.tools.echo_verdicts writes fill records for the blanks in unanimously-judged echo groups, then a second merge_verdicts pass imports them, so cross-cycle echo blanks fill without a sitting-prep pass), land standing-approval verdicts the same way (rebuild.tools.standing_verdicts fills blanks matching the checked-in rules in rebuild/standing-approvals.yaml, so once-and-for-all decisions never queue again), re-baseline the census pins, and run the four gates — always printing a summary table at the end, even on failure.

The exit-code trap this driver exists to defuse: run_m1.main() SystemExits nonzero whenever any oracle rows are UNMATCHED, which is always true mid-migration. Its exit code is therefore not the gate; the four summary JSONs it writes are. The real gates are defect_errors, the boundary and Manual-pin passes, and multi_matched == 0.

The two artifact-independent gates (js, make-test) run from t=0 in a small thread pool while the build chain runs inline-serial in the main thread; gate:rebuild starts after the run_m1 gate passes, queued behind make-test by default so only one 12-way pytest pool is ever hot. gate:conform (the exhaustive font-vs-settle sweep, run_m1 --conform-only) also starts after the run_m1 gate passes and, by default, parks at the tail of the make-test -> rebuild chain, so only one heavy pool owns the box at a time. It used to launch co-resident with gate:rebuild's pytest pool on the theory that conform was short post-depth-4-pruning, but the 13-symbol alphabet grew the sweep back to ~6½ minutes and the two pools together oversubscribe the cores roughly 2:1 — measured, that contention roughly tripled gate:rebuild's wall time, a strictly worse critical path than running the same work in sequence. --rebuild-pool overlap restores full co-residency.

gate:make-test is auto-skipped when its input closure is provably unchanged since the last green run. The closure is every tracked or untracked-unignored file outside rebuild/, glyph_data/runes/, doc/, tmp/, .claude/, and Markdown — nothing `make test` executes (make all -> build_font over glyph_data/*.yaml non-recursively, typst, pyright over tools/ test/ conftest.py, pytest test/ site/) reads those trees, so a diff confined to them cannot move the gate's outcome and re-running its ~15 CPU-minutes would verify nothing. The last green fingerprint lives in rebuild/out/make-test-green.json, written by rebuild.tools.make_test_gate — the `make test` entry point — on every green run, so interactive greens and cycle greens share one record and `make test` itself self-skips on the same test. cycle_summary.json still records the fingerprint the cycle ran (or validly skipped) against, and prior_make_test_fingerprint falls back to it when the shared record is absent. The fingerprint sees file content only — a system-toolchain change (a typst upgrade, say; pyright and pytest are pinned through uv.lock, which is in the closure) is invisible to it. --force-make-test runs the gate regardless (as does `make test FORCE=1` inside the wrapper).

The same provably-unchanged principle guards every other heavy stage, each keyed by a content fingerprint over that stage's full input closure and a green record written only after that exact content passed live: run_m1 skips on rebuild/out/run-m1-green.json (the Stage A fingerprint components plus the oracle's subset tables and uv.lock) and re-evaluates its gate from the four summary JSONs already on disk; gate:conform skips on conform-green.json (the run_m1 key plus the M1.otf bytes and the sweep horizon); gate:rebuild skips on rebuild-gate-green.json (the suite's repo closure under rebuild/ and glyph_data/ plus the out/m1 artifacts, site fonts, baselines, conftest.py, pyproject.toml, and uv.lock); surface-build skips when the manifest's recorded inputs fingerprint already equals the one a build would stamp now (a rebuild would be byte-identical, mtime-floored generated_at included, so the autosave stays aligned); and the census check skips on census-green.json. The surface, conform, rebuild, and census skips engage only on cycles where run_m1 itself skipped, so a live M1 rebuild can never invalidate a key mid-cycle; green records are written only when the key still matches after the work ran, and a red result whose key matches its record deletes the record. --fresh runs everything regardless.

A green finish ends with a retention pass over the cycle's own disk piles, all of them regenerable or journal-covered: every tmp/review-pre-* snapshot except this cycle's is deleted (a snapshot is read once, by its own cycle's carry, and never again), root verdicts-carried-*.json files not stamped for the live surface are deleted (only the stamp-aligned frontier is ever read; the tracked copy under rebuild/evidence/ is never touched), verdicts-autosave-* stashes not referenced by a journal event at or after the last base event are deleted (the journal, not the stashes, is the sanctioned recovery path — and the reference index is the test because a stash's mtime predates the event that created it), and the journal itself is compacted to the newest base event older than RETENTION_WINDOW_DAYS, keeping at least that many days of --restore-as-of history. Failed, interrupted, first-run, and rehearsal cycles never prune; --keep-history opts out entirely; a retention error warns and never turns a green cycle red.

Run as: uv run python rebuild/tools/artifact_cycle.py — the carry source is auto-resolved from the autosave and the verdicts-*.json exports; pass --verdicts to name one explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REVIEW_OUT = ROOT / "rebuild" / "out" / "review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
M1_OUT = ROOT / "rebuild" / "out" / "m1"
CENSUS_PINS = ROOT / "rebuild" / "review-census-pins.json"
CARRY_TOOL = ROOT / "rebuild" / "tools" / "carry_verdicts.py"
ECHO_TOOL = ROOT / "rebuild" / "tools" / "echo_verdicts.py"
ECHO_FILL = ROOT / "verdicts-echo-fill.json"
STANDING_TOOL = ROOT / "rebuild" / "tools" / "standing_verdicts.py"
STANDING_FILL = ROOT / "verdicts-standing-fill.json"
CYCLE_SUMMARY = ROOT / "rebuild" / "out" / "cycle_summary.json"
MAKE_TEST_GREEN = ROOT / "rebuild" / "out" / "make-test-green.json"
RUN_M1_GREEN = ROOT / "rebuild" / "out" / "run-m1-green.json"
CONFORM_GREEN = ROOT / "rebuild" / "out" / "conform-green.json"
REBUILD_GATE_GREEN = ROOT / "rebuild" / "out" / "rebuild-gate-green.json"
CENSUS_GREEN = ROOT / "rebuild" / "out" / "census-green.json"
JSTEST_DIR = ROOT / "rebuild" / "review" / "jstests"
REVIEW_PORT = 7294

POOL_POLICIES = ("queue", "overlap")
REBUILD_POOL_POLICY_DEFAULT = "queue"
_GATE_POOL_WORKERS = 5
_CONFORM_JOBS_CAP = 8
CONFORM_HORIZON_DEFAULT = 5
RETENTION_WINDOW_DAYS = 7

M1_SUMMARY_FILES = {
    "pipeline": M1_OUT / "pipeline_summary.json",
    "boundary": M1_OUT / "boundary_equivalence_summary.json",
    "manual_pins": M1_OUT / "manual_pins_summary.json",
    "oracle": M1_OUT / "oracle_summary.json",
}
CONFORM_SUMMARY = M1_OUT / "conform_summary.json"

BASELINE_REBUILD_FAILURES = frozenset(
    {
        "rebuild/test_surface.py::test_real_cell_bindings_all_match",
        "rebuild/test_spec_load.py::test_loads_all_six_runes",
        "rebuild/test_spec_load.py::test_predicate_class_membership",
        "rebuild/test_spec_load.py::test_group_resolution",
    }
)

CENSUS_HINT_MODULES = frozenset(
    {
        "test_review_audit",
        "test_review_build",
        "test_review_families",
        "test_review_ink",
    }
)

MAKE_TEST_EXEMPT_PREFIXES = ("rebuild/", "glyph_data/runes/", "doc/", "tmp/", ".claude/")


def make_test_exempt(path: str) -> bool:
    """Whether a repo-relative path is provably outside gate:make-test's input closure. The exempt trees are safe because nothing the gate executes reads them: build_font globs glyph_data/*.yaml non-recursively (never glyph_data/runes/), and test/, site/, tools/, conftest.py contain no reference to rebuild/ or the rune files; Markdown is never an input to any gate."""
    return path.endswith(".md") or any(path.startswith(prefix) for prefix in MAKE_TEST_EXEMPT_PREFIXES)


def make_test_closure_files(root: Path) -> list[str] | None:
    """Every tracked or untracked-unignored file that could affect gate:make-test, repo-relative and sorted. None when git is unavailable, in which case the caller must run the gate unconditionally."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    paths = {entry for entry in result.stdout.split("\0") if entry}
    return sorted(path for path in paths if not make_test_exempt(path))


def make_test_closure_fingerprint(root: Path = ROOT) -> str | None:
    """Content hash of gate:make-test's input closure, read from the worktree (not the index) so uncommitted edits count. A deleted-but-tracked file hashes as absent, so deletions move the fingerprint too."""
    files = make_test_closure_files(root)
    if files is None:
        return None
    digest = hashlib.sha256()
    for rel in files:
        path = root / rel
        try:
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            file_hash = "absent"
        digest.update(f"{rel}\t{file_hash}\n".encode())
    return digest.hexdigest()


def read_green_record(path: Path) -> dict | None:
    """A gate's last-green record ({fingerprint, finished_at}); None when absent or malformed."""
    try:
        record = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    if isinstance(record, dict) and isinstance(record.get("fingerprint"), str):
        return record
    return None


def record_green(path: Path, fingerprint: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"format": f"ams-{path.stem}/1", "fingerprint": fingerprint, "finished_at": stamp})
        + "\n"
    )
    os.replace(tmp, path)


def clear_contradicted_green(path: Path, fingerprint: str | None) -> None:
    """A red result over content whose fingerprint still matches the recorded green contradicts the record; delete it so no later cycle can skip on a falsified green."""
    record = read_green_record(path)
    if fingerprint is not None and record is not None and record["fingerprint"] == fingerprint:
        path.unlink(missing_ok=True)


def read_make_test_green(path: Path | None = None) -> dict | None:
    """The shared last-green record for `make test`, written by rebuild.tools.make_test_gate on every green run — interactive or as gate:make-test."""
    return read_green_record(path if path is not None else MAKE_TEST_GREEN)


def record_make_test_green(fingerprint: str, path: Path | None = None) -> None:
    record_green(path if path is not None else MAKE_TEST_GREEN, fingerprint)


def prior_make_test_fingerprint(
    summary_path: Path | None = None, green_path: Path | None = None
) -> str | None:
    """The closure fingerprint of the last green `make test` run: the shared green record when present (always at least as fresh, since every green run rewrites it), else the fingerprint the previous cycle recorded green or validly carried forward."""
    record = read_make_test_green(green_path)
    if record is not None:
        return record["fingerprint"]
    try:
        summary = json.loads((summary_path if summary_path is not None else CYCLE_SUMMARY).read_text())
    except OSError, ValueError:
        return None
    value = summary.get("make_test_fingerprint") if isinstance(summary, dict) else None
    return value if isinstance(value, str) else None


M1_ARTIFACT_NAMES = ("M1.otf", "divergence-audit.tsv", "inputs_fingerprint.json")
REBUILD_GATE_EXEMPT_PREFIXES = ("rebuild/evidence/", "rebuild/review/jstests/")


def _sha256_path(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


def _digest_lines(lines: list[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode() + b"\n")
    return digest.hexdigest()


def _subset_tables(root: Path) -> list[Path]:
    return sorted((root / "rebuild" / "out" / "m1").glob("baseline-*.subset.tsv.gz"))


def run_m1_skip_fingerprint(root: Path = ROOT) -> str:
    """Content key over everything a full run_m1 reads: the Stage A fingerprint components (rune and config data, the full baselines, the pipeline code), the oracle's subset tables — which Stage A's `baselines` covers only by proxy — and uv.lock for the pinned toolchain. Matching the recorded green means a rerun would reproduce rebuild/out/m1 byte for byte."""
    from rebuild.pipeline import fingerprint

    lines = [f"{name}\t{value}" for name, value in sorted(fingerprint.stage_a(root).items())]
    lines += [f"{path.name}\t{_sha256_path(path)}" for path in _subset_tables(root)]
    lines.append(f"uv.lock\t{_sha256_path(root / 'uv.lock')}")
    return _digest_lines(lines)


def m1_artifacts_present(root: Path = ROOT) -> bool:
    """Whether rebuild/out/m1 still holds everything a skipped run_m1 must leave behind: the four gate summaries plus the artifacts the surface build consumes."""
    m1 = root / "rebuild" / "out" / "m1"
    names = [path.name for path in M1_SUMMARY_FILES.values()] + list(M1_ARTIFACT_NAMES)
    return all((m1 / name).exists() for name in names)


def conform_skip_fingerprint(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> str:
    """The run_m1 key plus the compiled font's bytes and the sweep horizon — exactly what gate:conform sweeps. The horizon is in the key so a green at a shallower horizon can never satisfy a deeper gate."""
    lines = [
        f"run-m1\t{run_m1_skip_fingerprint(root)}",
        f"M1.otf\t{_sha256_path(root / 'rebuild' / 'out' / 'm1' / 'M1.otf')}",
        f"horizon\t{horizon}",
    ]
    return _digest_lines(lines)


def rebuild_gate_closure_files(root: Path) -> list[str] | None:
    """Every tracked or untracked-unignored file the rebuild pytest suite can read from the repo: rebuild/ and glyph_data/ (minus Markdown, the carried-verdict evidence, and the JS-only jstests) plus the root conftest.py, pyproject.toml, and uv.lock. None when git is unavailable, in which case the caller must run the gate unconditionally."""
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "rebuild/",
                "glyph_data/",
                "conftest.py",
                "pyproject.toml",
                "uv.lock",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    paths = {entry for entry in result.stdout.split("\0") if entry}
    return sorted(
        path
        for path in paths
        if not path.endswith(".md")
        and not any(path.startswith(prefix) for prefix in REBUILD_GATE_EXEMPT_PREFIXES)
    )


def rebuild_gate_skip_fingerprint(root: Path = ROOT) -> str | None:
    """Content key over gate:rebuild's full input closure: the repo files from rebuild_gate_closure_files plus the out/m1 artifacts the suite reads and the site fonts and baselines it shapes against. The verdict store is deliberately absent — the suite exercises it only through fixtures — which is what lets verdict-only cycles skip the gate."""
    from rebuild.pipeline import fingerprint

    files = rebuild_gate_closure_files(root)
    if files is None:
        return None
    m1 = root / "rebuild" / "out" / "m1"
    lines = [f"{rel}\t{_sha256_path(root / rel)}" for rel in files]
    lines += [f"m1/{name}\t{_sha256_path(m1 / name)}" for name in M1_ARTIFACT_NAMES]
    lines += [f"m1/{path.name}\t{_sha256_path(path)}" for path in _subset_tables(root)]
    lines.append(f"fonts\t{fingerprint.hash_paths(root, fingerprint.font_paths(root))}")
    lines.append(f"baselines\t{fingerprint.baselines_value(root)}")
    return _digest_lines(lines)


def surface_build_skippable(root: Path = ROOT, review_out: Path | None = None) -> bool:
    """Whether rebuilding the review surface would reproduce its content byte for byte, so the build can be skipped with the autosave still aligned. True only when the manifest's recorded inputs fingerprint equals the one a build would stamp now (Stage A as recorded by run_m1, Stage B recomputed) and every shard the manifest names is still present. generated_at is mtime-derived, so a rebuild after pure mtime churn (git checkout, touch) could restamp it even with identical content — skipping deliberately keeps the existing stamp instead, which preserves the manifest-autosave alignment the stamp exists to key."""
    from rebuild.pipeline import fingerprint

    surface = review_out if review_out is not None else REVIEW_OUT
    try:
        manifest = json.loads((surface / "manifest.json").read_text())
    except OSError, ValueError:
        return False
    recorded = manifest.get("inputs_fingerprint")
    if not isinstance(recorded, dict):
        return False
    stage_a = fingerprint.read_stage_a(root / "rebuild" / "out" / "m1")
    if stage_a is None:
        return False
    before_font, junior_font = fingerprint.font_paths(root)
    expected = {**stage_a, **fingerprint.stage_b(root, before_font, junior_font)}
    if recorded != expected:
        return False
    try:
        shards = [meta["shard"] for meta in manifest["classes"] if meta.get("shard")]
    except KeyError, TypeError:
        return False
    return all((surface / shard).exists() for shard in shards)


def census_skip_fingerprint(root: Path = ROOT, surface: Path | None = None) -> str | None:
    """Content key over the census check's inputs: the surface identity (its recorded fingerprint and stamp), the checked-in pins, and the source artifacts the ink and family groups re-shape (the audit, the compiled font, and the subset tables; the site fonts and spec ride inside the manifest fingerprint). None when the surface has no fingerprinted manifest."""
    surface_dir = surface if surface is not None else REVIEW_OUT
    try:
        manifest = json.loads((surface_dir / "manifest.json").read_text())
    except OSError, ValueError:
        return None
    fp = manifest.get("inputs_fingerprint")
    if not isinstance(fp, dict):
        return None
    m1 = root / "rebuild" / "out" / "m1"
    lines = [
        f"manifest\t{json.dumps(fp, sort_keys=True)}",
        f"generated_at\t{manifest.get('generated_at')}",
        f"pins\t{_sha256_path(root / 'rebuild' / 'review-census-pins.json')}",
        f"M1.otf\t{_sha256_path(m1 / 'M1.otf')}",
        f"audit\t{_sha256_path(m1 / 'divergence-audit.tsv')}",
    ]
    lines += [f"m1/{path.name}\t{_sha256_path(path)}" for path in _subset_tables(root)]
    return _digest_lines(lines)


def snapshot_surface(src: Path, dst: Path) -> str:
    """Snapshot the surface as an APFS clone when possible (cp -c uses clonefile(2), sharing blocks copy-on-write, so the ~130MB recovery copy costs neither wall time nor real disk); shutil.copytree remains the portable fallback."""
    if sys.platform == "darwin":
        result = subprocess.run(["cp", "-Rc", str(src), str(dst)], capture_output=True, text=True)
        if result.returncode == 0:
            return "cloned"
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    return "copied"


@dataclass
class GateOutcome:
    ok: bool
    failures: list[str]
    unmatched: int | None
    multi_matched: int | None


def evaluate_run_m1_gate(pipeline: dict, boundary: dict, manual_pins: dict, oracle: dict) -> GateOutcome:
    """Decide whether the M1 build passed from its four summary JSONs. run_m1's own exit code is not usable — it fails on any UNMATCHED oracle rows, always present mid-migration — so this reads defect_errors, the boundary/Manual-pin passes, and multi_matched instead, and records UNMATCHED only as informational."""
    failures: list[str] = []

    defect_errors = pipeline.get("defect_errors") or []
    if defect_errors:
        failures.append(f"{len(defect_errors)} defect-gate error(s): {defect_errors[0]}")

    if not boundary.get("pass"):
        failures.append(f"boundary-equals-text-edge gate failed ({boundary.get('divergences')} divergences)")

    if not manual_pins.get("pass"):
        failures.append(
            f"Manual-pin gate failed ({len(manual_pins.get('disagreements') or [])} disagreements)"
        )

    multi_matched = oracle.get("multi_matched")
    if multi_matched is not None and multi_matched > 0:
        failures.append(f"oracle multi_matched = {multi_matched} (must be 0)")

    return GateOutcome(
        ok=not failures,
        failures=failures,
        unmatched=oracle.get("unmatched"),
        multi_matched=multi_matched,
    )


def evaluate_conform_gate(summary: dict | None) -> tuple[str, list[str]]:
    """Judge gate:conform from conform_summary.json's contents (None = the subprocess never wrote one). `pass` is the verdict; the detail lines name what broke — shaping divergences are compiler defects by definition, and nonzero uncovered counts mean dead generated rules or transitions."""
    if summary is None:
        return "FAILED (no conform_summary.json)", ["conform gate: run_m1 --conform-only wrote no summary"]
    failures: list[str] = []
    if summary.get("divergences"):
        failures.append(f"conform gate: {summary['divergences']} font-vs-settle divergence(s)")
    if summary.get("uncovered_rules"):
        failures.append(f"conform gate: {summary['uncovered_rules']} dead settlement rule(s)")
    if summary.get("uncovered_transitions"):
        failures.append(f"conform gate: {summary['uncovered_transitions']} dead decision-table transition(s)")
    if not summary.get("pass") and not failures:
        failures.append("conform gate: pass is false")
    if failures:
        return "FAILED", failures
    return "green", []


def conform_gate_argv(jobs: int, horizon: int = CONFORM_HORIZON_DEFAULT) -> list[str]:
    argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--conform-only"]
    if jobs > 1:
        argv += ["--jobs", str(jobs)]
    if horizon != CONFORM_HORIZON_DEFAULT:
        argv += ["--conform-horizon", str(horizon)]
    return argv


def classify_rebuild_failure(test_id: str, update_pins: bool) -> str:
    """Bucket a failing rebuild-suite test id: 'baseline' (the four documented batch-1 spec pins, always expected), 'census-hint' (a census-pinned review test, expected to go stale after a rune change until --update-pins), or 'hard' (anything unexplained — fails the cycle)."""
    if test_id in BASELINE_REBUILD_FAILURES:
        return "baseline"
    module = test_id.split("::", 1)[0]
    stem = Path(module).stem
    if stem in CENSUS_HINT_MODULES and not update_pins:
        return "census-hint"
    return "hard"


@dataclass
class Step:
    name: str
    argv: list[str] | None
    note: str = ""
    lane: str = ""


@dataclass
class Plan:
    short_id: str
    first_run: bool
    snapshot_dir: Path
    carry_out: Path | None
    verdicts: Path | None
    update_pins: bool
    skip_gates: bool
    do_merge: bool = False
    skip_conform: bool = False
    skip_make_test: bool = False
    make_test_note: str = ""
    make_test_fingerprint: str | None = None
    skip_run_m1: bool = False
    run_m1_note: str = ""
    run_m1_fingerprint: str | None = None
    skip_surface: bool = False
    surface_note: str = ""
    skip_rebuild_gate: bool = False
    rebuild_gate_note: str = ""
    conform_note: str = ""
    skip_census: bool = False
    census_skip_note: str = ""
    record_greens: bool = False
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT
    job_budget: int = 1
    conform_jobs: int = 1
    conform_horizon: int = CONFORM_HORIZON_DEFAULT
    review_out: Path | None = None
    census_surface: Path = REVIEW_OUT
    complaints_note: str = ""
    retention: bool = False
    steps: list[Step] = field(default_factory=list)


def jstest_argv() -> list[str]:
    """The JS suite argv. The *.test.js glob form is required — node v26 rejects the bare-directory form with 'Cannot find module' — and the glob is expanded in Python, never handed to a shell."""
    files = sorted(str(path.relative_to(ROOT)) for path in JSTEST_DIR.glob("*.test.js"))
    return ["node", "--test", *files]


def stage_job_budget(*, skip_gates: bool, skip_make_test: bool = False, ncores: int | None = None) -> int:
    """The --jobs budget the driver hands run_m1 and surface-build. Under a gated cycle a 12-way `make test` owns the box from t=0, so the build stages stay serial (1) — but make-test is the whole reason for that politeness, so the cores open up whenever it isn't actually going to run: --skip-gates, or the closure-unchanged auto-skip. gate:js still runs from t=0 in that case, but it's a single node process, not a pool."""
    n = ncores or (os.cpu_count() or 1)
    return n if skip_gates or skip_make_test else 1


def build_plan(
    *,
    verdicts: Path | None,
    no_carry: bool,
    carry_out: Path | None,
    snapshot_dir: Path | None,
    update_pins: bool,
    skip_gates: bool,
    first_run: bool,
    short_id: str,
    no_merge: bool = False,
    skip_conform: bool = False,
    skip_make_test: bool = False,
    make_test_note: str = "",
    make_test_fingerprint: str | None = None,
    conform_horizon: int = CONFORM_HORIZON_DEFAULT,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    review_out: Path | None = None,
    ncores: int | None = None,
    skip_run_m1: bool = False,
    run_m1_note: str = "",
    run_m1_fingerprint: str | None = None,
    skip_surface: bool = False,
    surface_note: str = "",
    skip_rebuild_gate: bool = False,
    rebuild_gate_note: str = "",
    conform_note: str = "",
    skip_census: bool = False,
    census_skip_note: str = "",
    record_greens: bool = False,
    keep_history: bool = False,
) -> Plan:
    resolved_snapshot = snapshot_dir if snapshot_dir is not None else ROOT / "tmp" / f"review-pre-{short_id}"
    do_carry = not no_carry and not first_run
    resolved_carry_out: Path | None = None
    if do_carry:
        resolved_carry_out = (
            carry_out if carry_out is not None else ROOT / f"verdicts-carried-{short_id}.json"
        )

    job_budget = stage_job_budget(skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores)
    conform_jobs = min(_CONFORM_JOBS_CAP, ncores or (os.cpu_count() or 1))
    census_surface = review_out if review_out is not None else REVIEW_OUT
    do_merge = do_carry and not no_merge and review_out is None
    do_retention = not keep_history and not first_run and review_out is None

    plan = Plan(
        short_id=short_id,
        first_run=first_run,
        snapshot_dir=resolved_snapshot,
        carry_out=resolved_carry_out,
        verdicts=verdicts,
        update_pins=update_pins,
        skip_gates=skip_gates,
        do_merge=do_merge,
        skip_conform=skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fingerprint,
        skip_run_m1=skip_run_m1,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fingerprint,
        skip_surface=skip_surface,
        surface_note=surface_note,
        skip_rebuild_gate=skip_rebuild_gate,
        rebuild_gate_note=rebuild_gate_note,
        conform_note=conform_note,
        skip_census=skip_census,
        census_skip_note=census_skip_note,
        record_greens=record_greens,
        retention=do_retention,
        pool_policy=pool_policy,
        job_budget=job_budget,
        conform_jobs=conform_jobs,
        conform_horizon=conform_horizon,
        review_out=review_out,
        census_surface=census_surface,
    )

    if first_run:
        plan.steps.append(
            Step("snapshot", None, "SKIPPED (first run: no existing surface to snapshot)", lane="build")
        )
    else:
        plan.steps.append(
            Step(
                "snapshot",
                None,
                f"snapshot {REVIEW_OUT} -> {resolved_snapshot} (APFS clone when supported)",
                lane="build",
            )
        )

    if skip_run_m1:
        plan.steps.append(
            Step(
                "run_m1",
                None,
                f"SKIPPED ({run_m1_note}); gate re-evaluated from the recorded summaries",
                lane="build",
            )
        )
    else:
        run_m1_argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
        if job_budget > 1:
            run_m1_argv += ["--jobs", str(job_budget)]
        plan.steps.append(Step("run_m1", run_m1_argv, lane="build"))

    if skip_surface:
        plan.steps.append(Step("surface-build", None, f"SKIPPED ({surface_note})", lane="build"))
    else:
        surface_argv = ["uv", "run", "python", "-m", "rebuild.review.build"]
        if job_budget > 1:
            surface_argv += ["--jobs", str(job_budget)]
        if review_out is not None:
            surface_argv += ["--out", str(review_out)]
        plan.steps.append(Step("surface-build", surface_argv, lane="build"))

    if do_carry:
        assert resolved_carry_out is not None
        carry_argv = [
            "uv",
            "run",
            "python",
            str(CARRY_TOOL),
            "--source",
            str(resolved_snapshot),
            str(verdicts),
            "--out",
            str(resolved_carry_out),
        ]
        if review_out is not None:
            carry_argv += ["--current-surface", str(review_out)]
        plan.steps.append(Step("carry", carry_argv, lane="build"))
    elif first_run:
        plan.steps.append(Step("carry", None, "SKIPPED (first run)", lane="build"))
    else:
        plan.steps.append(Step("carry", None, "SKIPPED (--no-carry)", lane="build"))

    if do_merge:
        assert resolved_carry_out is not None
        plan.steps.append(
            Step(
                "merge",
                ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(resolved_carry_out)],
                lane="build",
            )
        )
    elif do_carry and review_out is not None:
        plan.steps.append(
            Step("merge", None, "SKIPPED (rehearsal: the live autosave is never written)", lane="build")
        )
    elif do_carry:
        plan.steps.append(Step("merge", None, "SKIPPED (--no-merge)", lane="build"))
    elif first_run:
        plan.steps.append(Step("merge", None, "SKIPPED (first run)", lane="build"))
    else:
        plan.steps.append(Step("merge", None, "SKIPPED (--no-carry)", lane="build"))

    if do_merge:
        plan.steps.append(
            Step("echo-fill", ["uv", "run", "python", str(ECHO_TOOL), str(AUTOSAVE)], lane="build")
        )
        plan.steps.append(
            Step(
                "echo-merge",
                ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(ECHO_FILL)],
                lane="build",
            )
        )
        plan.steps.append(
            Step("standing-fill", ["uv", "run", "python", str(STANDING_TOOL), str(AUTOSAVE)], lane="build")
        )
        plan.steps.append(
            Step(
                "standing-merge",
                ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(STANDING_FILL)],
                lane="build",
            )
        )
    else:
        if do_carry and review_out is not None:
            echo_note = "SKIPPED (rehearsal: the live autosave is never written)"
        elif do_carry:
            echo_note = "SKIPPED (--no-merge)"
        elif first_run:
            echo_note = "SKIPPED (first run)"
        else:
            echo_note = "SKIPPED (--no-carry)"
        plan.steps.append(Step("echo-fill", None, echo_note, lane="build"))
        plan.steps.append(Step("echo-merge", None, echo_note, lane="build"))
        plan.steps.append(Step("standing-fill", None, echo_note, lane="build"))
        plan.steps.append(Step("standing-merge", None, echo_note, lane="build"))

    if skip_census:
        plan.steps.append(Step("census", None, f"SKIPPED ({census_skip_note})", lane="build"))
    else:
        census_mode = "--update" if update_pins else "--check"
        plan.steps.append(
            Step(
                "census",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "rebuild.review.census",
                    census_mode,
                    "--surface",
                    str(census_surface),
                ],
                (
                    "then `git diff -- rebuild/review-census-pins.json`, printed in full"
                    if update_pins
                    else "staleness reported informationally"
                ),
                lane="build",
            )
        )

    if review_out is not None:
        plan.complaints_note = "rehearsal: reads the live autosave"
    elif first_run:
        plan.complaints_note = "first run: no verdicts to cluster"
    elif not AUTOSAVE.exists():
        plan.complaints_note = "no verdicts store"
    if plan.complaints_note:
        plan.steps.append(Step("complaints", None, f"SKIPPED ({plan.complaints_note})", lane="build"))
    else:
        plan.steps.append(
            Step(
                "complaints",
                ["uv", "run", "python", "-m", "rebuild.tools.complaint_docket", str(AUTOSAVE)],
                "informational, non-gating",
                lane="build",
            )
        )

    if skip_gates:
        plan.steps.append(Step("gates", None, "SKIPPED (--skip-gates)"))
    else:
        plan.steps.append(Step("gate:js", jstest_argv(), lane="t0"))
        if skip_rebuild_gate:
            plan.steps.append(
                Step("gate:rebuild", None, f"SKIPPED ({rebuild_gate_note})", lane="rebuild")
            )
        else:
            plan.steps.append(
                Step(
                    "gate:rebuild",
                    [
                        "uv",
                        "run",
                        "pytest",
                        "rebuild/",
                        "-n",
                        "auto",
                        "--dist",
                        "worksteal",
                        "-q",
                        "--tb=no",
                        "-rfE",
                    ],
                    lane="rebuild",
                )
            )
        if skip_conform:
            plan.steps.append(
                Step("gate:conform", None, f"SKIPPED ({conform_note or '--skip-conform'})", lane="conform")
            )
        else:
            plan.steps.append(
                Step("gate:conform", conform_gate_argv(conform_jobs, conform_horizon), lane="conform")
            )
        if skip_make_test:
            plan.steps.append(Step("gate:make-test", None, f"SKIPPED ({make_test_note})", lane="t0"))
        else:
            plan.steps.append(Step("gate:make-test", ["make", "test"], lane="t0"))

    if do_retention:
        plan.steps.append(
            Step(
                "retention",
                None,
                f"on green finish: keep only this cycle's tmp/review-pre-* snapshot and the stamp-aligned verdicts-carried-*.json, drop verdicts-autosave-* stashes older than the journal's last base event, compact the journal to a {RETENTION_WINDOW_DAYS}-day restore floor; --keep-history skips",
            )
        )
    elif keep_history:
        plan.steps.append(Step("retention", None, "SKIPPED (--keep-history)"))
    elif first_run:
        plan.steps.append(Step("retention", None, "SKIPPED (first run: nothing accumulated yet)"))
    else:
        plan.steps.append(
            Step("retention", None, "SKIPPED (rehearsal: the live piles are not this cycle's to prune)")
        )

    return plan


def resolve_carry_source() -> dict | None:
    from rebuild.review import status

    try:
        stamp = json.loads((REVIEW_OUT / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        stamp = None
    return status.resolve_carry_source(ROOT, stamp, AUTOSAVE)


def describe_carry_source(resolved: dict, root: Path) -> str:
    try:
        shown = resolved["path"].relative_to(root)
    except ValueError:
        shown = resolved["path"]
    if resolved["aligned"]:
        provenance = "stamped for the served surface"
    else:
        provenance = (
            f"stamped {resolved['stamp']}, not the served surface; carry re-resolves by content and ink keys"
        )
    return (
        f"Auto-resolved carry source: {shown} ({resolved['count']} effective verdicts, {provenance}). "
        "Pass --verdicts to override."
    )


def resolve_short_id() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        head = result.stdout.strip()
        if head:
            return head
    except OSError, subprocess.SubprocessError:
        pass
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def server_listening(port: int = REVIEW_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _render_concurrency(plan: Plan) -> list[str]:
    if plan.skip_gates:
        return [
            "",
            "  Concurrency (--skip-gates):",
            f"    Lane build only; no gates; --jobs budget: {plan.job_budget}",
        ]
    t0_lane = "gate:js" if plan.skip_make_test else "gate:js, gate:make-test"
    lines = [
        "",
        f"  Concurrency (pool policy: {plan.pool_policy}):",
        f"    Lane t0   [from t=0, background]  : {t0_lane}",
        "    Lane build[serial, main thread]  : snapshot -> run_m1 -> surface-build -> carry -> merge -> census",
    ]
    if plan.skip_rebuild_gate:
        lines.append(
            "    Lane rebuild                     : SKIPPED (inputs unchanged since its last green run)"
        )
    else:
        lines.append("    Lane rebuild                     : starts when run_m1's four JSONs pass;")
        if plan.skip_make_test:
            lines.append(
                "                                       gate:make-test auto-skipped (closure unchanged), so no queueing"
            )
        elif plan.pool_policy == "overlap":
            lines.append(
                "                                       CO-RESIDENT with gate:make-test (overlap policy — two 12-way pytest pools)"
            )
        else:
            lines.append("                                       QUEUED behind gate:make-test  (queue policy)")
    if plan.skip_conform:
        lines.append("    Lane conform                     : SKIPPED (--skip-conform)")
    elif plan.pool_policy == "overlap":
        lines.append(
            f"    Lane conform                     : starts when run_m1's four JSONs pass; CO-RESIDENT with the pytest pools (--jobs {plan.conform_jobs})"
        )
    elif not plan.skip_rebuild_gate:
        lines.append(
            f"    Lane conform                     : starts when run_m1's four JSONs pass; QUEUED behind gate:rebuild's pool (queue policy — one heavy pool at a time) (--jobs {plan.conform_jobs})"
        )
    elif not plan.skip_make_test:
        lines.append(
            f"    Lane conform                     : starts when run_m1's four JSONs pass; QUEUED behind gate:make-test (queue policy; gate:rebuild skipped) (--jobs {plan.conform_jobs})"
        )
    else:
        lines.append(
            f"    Lane conform                     : starts when run_m1's four JSONs pass; both pytest gates skipped, so no queueing (--jobs {plan.conform_jobs})"
        )
    budget_reason = (
        "gate:make-test skipped, so the build stages fan out"
        if plan.skip_make_test
        else "a 12-way `make test` owns the cores"
    )
    lines.append(f"    build-stage --jobs budget        : {plan.job_budget}  ({budget_reason})")
    return lines


def render_plan(plan: Plan) -> str:
    lines = ["Artifact-cycle plan (resolved, nothing executed):", ""]
    lines.append(f"  git short id : {plan.short_id}")
    lines.append(f"  first run    : {plan.first_run}")
    lines.append(f"  snapshot dir : {plan.snapshot_dir}")
    lines.append(f"  verdicts     : {plan.verdicts if plan.verdicts is not None else '(none)'}")
    lines.append(f"  carry output : {plan.carry_out if plan.carry_out is not None else '(no carry)'}")
    if plan.review_out is not None:
        lines.append(
            f"  rehearsal    : surface writes redirected to {plan.review_out}; the live surface at rebuild/out/review is never written."
        )
    lines.append("")
    lines.append("  Steps:")
    for index, step in enumerate(plan.steps, start=1):
        if step.argv is not None:
            lines.append(f"    {index}. {step.name}: {' '.join(step.argv)}")
            if step.note:
                lines.append(f"       ({step.note})")
        else:
            lines.append(f"    {index}. {step.name}: {step.note}")
    lines.extend(_render_concurrency(plan))
    return "\n".join(lines)


@dataclass
class CycleReport:
    snapshot_dir: Path | None = None
    unmatched: int | None = None
    multi_matched: int | None = None
    boundary_pass: bool | None = None
    pins_pass: bool | None = None
    surface_units: int | None = None
    surface_rows: int | None = None
    surface_batches: int | None = None
    echo_groups: int | None = None
    carry_out: Path | None = None
    carry_lines: list[str] = field(default_factory=list)
    merge_status: str = "not run"
    merge_lines: list[str] = field(default_factory=list)
    echo_fill_status: str = "not run"
    echo_fill_lines: list[str] = field(default_factory=list)
    echo_merge_status: str = "not run"
    echo_merge_lines: list[str] = field(default_factory=list)
    standing_fill_status: str = "not run"
    standing_fill_lines: list[str] = field(default_factory=list)
    standing_merge_status: str = "not run"
    standing_merge_lines: list[str] = field(default_factory=list)
    census_status: str = "not run"
    complaints_status: str = "not run"
    gate_js: str = "not run"
    gate_rebuild: str = "not run"
    gate_conform: str = "not run"
    gate_make_test: str = "not run"
    rebuild_recordable: bool = False
    interrupted: bool = False


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text())


def _cmd_label(argv: list[str]) -> str:
    parts = [token for token in argv if token not in {"uv", "run", "python", "python3"}]
    if parts and parts[0] == "-m":
        parts = parts[1:]
    return " ".join(parts[:3])


class _Emitter:
    """Whole-line-atomic, lock-serialized stdout. Every write in the concurrent region routes through here so overlapping children never splice mid-line; cross-line interleave is expected and disambiguated by the [name] prefix."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def emit(self, text: str) -> None:
        with self._lock:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    def emit_block(self, lines: list[str]) -> None:
        with self._lock:
            for line in lines:
                sys.stdout.write(line + "\n")
            sys.stdout.flush()


class _ChildRegistry:
    """Thread-safe set of live subprocesses, so a KeyboardInterrupt can reap every child (no orphaned pytest army survives)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: set[subprocess.Popen] = set()
        self._closed = False
        self.killed_count = 0

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def add(self, proc: subprocess.Popen) -> bool:
        """Track a live child. Returns False once terminate_all has torn the registry down, so a worker that unblocks after a KeyboardInterrupt (the queue-mode gate tasks parked on an earlier gate's future — rebuild on make-test, conform on rebuild — are the case) never leaves a fresh subprocess untracked — the caller reaps it instead of spawning an orphaned pytest army."""
        with self._lock:
            if self._closed:
                return False
            self._children.add(proc)
            return True

    def remove(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._children.discard(proc)

    def terminate_all(self) -> None:
        with self._lock:
            self._closed = True
            children = list(self._children)
            self._children.clear()
        for proc in children:
            if proc.poll() is None:
                proc.terminate()
        for proc in children:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            self.killed_count += 1


@dataclass
class _StepResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    elapsed: float


def _terminate_child(proc: subprocess.Popen) -> None:
    """Terminate one child promptly (SIGTERM, 3s grace, then SIGKILL) and drain its pipes. Used only for the narrow race where the registry is torn down between a Popen and its registry.add."""
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def _run_step(
    name: str, argv: list[str], *, emit: _Emitter, registry: _ChildRegistry, stream: bool
) -> _StepResult:
    if registry.closed:
        return _StepResult(name, 130, "", "", 0.0)
    emit.emit(f"\n$ {' '.join(argv)}")
    start = time.perf_counter()
    proc = subprocess.Popen(
        argv, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1
    )
    if not registry.add(proc):
        _terminate_child(proc)
        return _StepResult(name, 130, "", "", 0.0)
    out_buf: list[str] = []
    err_buf: list[str] = []

    def pump(pipe, buf: list[str]) -> None:
        for line in pipe:
            line = line.rstrip("\r\n")
            buf.append(line)
            if stream:
                emit.emit(f"[{name}] {line}")
        pipe.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, out_buf)),
        threading.Thread(target=pump, args=(proc.stderr, err_buf)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    returncode = proc.wait()
    registry.remove(proc)
    elapsed = time.perf_counter() - start
    emit.emit(f"[t] {_cmd_label(argv)} {elapsed:.1f}s")
    return _StepResult(name, returncode, "\n".join(out_buf), "\n".join(err_buf), elapsed)


def _dump_captured(emit: _Emitter, result: _StepResult) -> None:
    lines: list[str] = []
    if result.stdout:
        lines.extend(result.stdout.splitlines())
    if result.stderr:
        lines.extend(result.stderr.splitlines())
    if lines:
        emit.emit_block(lines)


def _parse_surface_build(stderr: str) -> tuple[int, int, int] | None:
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped.startswith("Wrote ") and "units," in stripped and "batches)" in stripped:
            inner = stripped[stripped.index("(") + 1 : stripped.rindex(")")]
            numbers = []
            for chunk in inner.split(","):
                token = chunk.strip().split(" ", 1)[0]
                numbers.append(int(token))
            if len(numbers) == 3:
                return numbers[0], numbers[1], numbers[2]
    return None


@dataclass
class _RebuildOutcome:
    status: str
    failures: list[str]
    hard_ids: list[str]
    recordable: bool = False


_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _classify_rebuild(result: _StepResult, update_pins: bool) -> _RebuildOutcome:
    """Bucket the rebuild suite's FAILED/ERROR summary lines into baseline / census-hint / hard and turn them into a gate verdict. pytest emits ANSI color whenever FORCE_COLOR is set (as it is under the agent harness), wrapping each summary line in escape codes, so strip those first — otherwise no line begins with a literal "FAILED "/"ERROR ", the documented baseline can't be subtracted, and every colored run reads as an unexplained hard failure."""
    lines = [_ANSI_SGR.sub("", line) for line in result.stdout.splitlines()]
    failed_ids = [line.split(None, 2)[1] for line in lines if line.startswith("FAILED ")]
    error_ids = [line.split(None, 2)[1] for line in lines if line.startswith("ERROR ")]
    buckets: dict[str, list[str]] = {"baseline": [], "census-hint": [], "hard": []}
    for test_id in failed_ids:
        buckets[classify_rebuild_failure(test_id, update_pins)].append(test_id)
    buckets["hard"].extend(error_ids)
    if result.returncode != 0 and not failed_ids and not error_ids:
        buckets["hard"].append(f"pytest exited {result.returncode} with no parsed FAILED/ERROR lines")
    failures: list[str] = []
    if buckets["hard"]:
        status = f"FAILED ({len(buckets['hard'])} unexplained)"
        failures.append(f"rebuild suite: {len(buckets['hard'])} unexplained failure(s)")
    else:
        parts = []
        if buckets["baseline"]:
            parts.append(f"{len(buckets['baseline'])} documented baseline")
        if buckets["census-hint"]:
            parts.append(f"{len(buckets['census-hint'])} stale census pins? (re-run with --update-pins)")
        status = "green" if not parts else "green (" + ", ".join(parts) + ")"
    recordable = not buckets["hard"] and not buckets["census-hint"]
    return _RebuildOutcome(
        status=status, failures=failures, hard_ids=list(buckets["hard"]), recordable=recordable
    )


def _do_run_m1(
    report: CycleReport,
    *,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    budget: int,
    skip: bool = False,
    skip_note: str = "",
    record: bool = False,
    fingerprint: str | None = None,
) -> GateOutcome | None:
    """Run (or, when `skip` is set, reuse) the M1 build and judge its gate from the four summary JSONs. The skip path leaves rebuild/out/m1 untouched and re-evaluates the recorded summaries, which is sound because run_m1's outputs are deterministic and timestamp-free over the fingerprinted inputs. A live green records the fingerprint only if it still matches — an input edited mid-run means the tested content is no longer on disk — and a live red matching the record deletes it."""
    if skip:
        emit.emit(f"\nrun_m1: SKIPPED — {skip_note}; evaluating the gate from the recorded summaries.")
    else:
        for path in M1_SUMMARY_FILES.values():
            path.unlink(missing_ok=True)
        argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
        if budget > 1:
            argv += ["--jobs", str(budget)]
        spawn("run_m1", argv, emit=emit, registry=registry, stream=True)
    missing = [name for name, path in M1_SUMMARY_FILES.items() if not path.exists()]
    if missing:
        for name in missing:
            emit.emit(
                f"run_m1 gate failure: missing {name} summary ({M1_SUMMARY_FILES[name]}) — run_m1 did not complete"
            )
        return None
    summaries = {name: _load_summary(path) for name, path in M1_SUMMARY_FILES.items()}
    gate = evaluate_run_m1_gate(
        summaries["pipeline"], summaries["boundary"], summaries["manual_pins"], summaries["oracle"]
    )
    report.unmatched = gate.unmatched
    report.multi_matched = gate.multi_matched
    report.boundary_pass = bool(summaries["boundary"].get("pass"))
    report.pins_pass = bool(summaries["manual_pins"].get("pass"))
    if record and fingerprint is not None:
        if not gate.ok:
            clear_contradicted_green(RUN_M1_GREEN, fingerprint)
        elif not skip:
            if run_m1_skip_fingerprint(ROOT) == fingerprint:
                record_green(RUN_M1_GREEN, fingerprint)
            else:
                emit.emit("run_m1 green, but its inputs changed while it ran — green not recorded")
    return gate


def _run_m1_reasons(gate: GateOutcome | None) -> list[str]:
    if gate is None:
        return ["run_m1 did not write all four summary files"]
    return list(gate.failures)


def _do_surface_build(
    report: CycleReport,
    *,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    review_out: Path | None,
    budget: int,
    skip: bool = False,
    skip_note: str = "",
) -> bool:
    if skip:
        surface_dir = review_out if review_out is not None else REVIEW_OUT
        try:
            manifest = json.loads((surface_dir / "manifest.json").read_text())
        except OSError, ValueError:
            emit.emit("ERROR: surface-build skip: the manifest vanished mid-cycle; rerun with --fresh.")
            return False
        totals = manifest.get("totals") or {}
        report.surface_units = totals.get("units")
        report.surface_rows = totals.get("rows")
        report.surface_batches = totals.get("batches")
        report.echo_groups = totals.get("echo_groups")
        emit.emit(f"\nsurface-build: SKIPPED — {skip_note}.")
        return True
    argv = ["uv", "run", "python", "-m", "rebuild.review.build"]
    if budget > 1:
        argv += ["--jobs", str(budget)]
    if review_out is not None:
        argv += ["--out", str(review_out)]
    result = spawn("surface-build", argv, emit=emit, registry=registry, stream=True)
    parsed = _parse_surface_build(result.stderr) if result.returncode == 0 else None
    if result.returncode != 0 or parsed is None:
        emit.emit(
            "ERROR: review.build did not complete cleanly (no 'Wrote ... (N units, R rows, B batches)' line)."
        )
        return False
    report.surface_units, report.surface_rows, report.surface_batches = parsed
    surface_dir = review_out if review_out is not None else REVIEW_OUT
    manifest = json.loads((surface_dir / "manifest.json").read_text())
    report.echo_groups = manifest.get("totals", {}).get("echo_groups")
    return True


def _do_carry(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = [
        "uv",
        "run",
        "python",
        str(CARRY_TOOL),
        "--source",
        str(plan.snapshot_dir),
        str(plan.verdicts),
        "--out",
        str(plan.carry_out),
    ]
    if plan.review_out is not None:
        argv += ["--current-surface", str(plan.review_out)]
    result = spawn("carry", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    report.carry_out = plan.carry_out
    for line in result.stdout.splitlines():
        if any(word in line for word in ("carried", "kinds", "queue", "fallback")):
            report.carry_lines.append(line.strip())
    return result.returncode == 0


def _do_merge(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(plan.carry_out)]
    result = spawn("merge", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("merged ", "nothing changed", "stashed ")):
            report.merge_lines.append(stripped)
    report.merge_status = "merged" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_echo_fill(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = ["uv", "run", "python", str(ECHO_TOOL), str(AUTOSAVE)]
    result = spawn("echo-fill", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("wrote ") and "echo-fill verdicts" in stripped:
            report.echo_fill_lines.append(stripped)
    report.echo_fill_status = "filled" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_echo_merge(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(ECHO_FILL)]
    result = spawn("echo-merge", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("merged ", "nothing changed", "stashed ")):
            report.echo_merge_lines.append(stripped)
    report.echo_merge_status = "merged" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_standing_fill(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = ["uv", "run", "python", str(STANDING_TOOL), str(AUTOSAVE)]
    result = spawn("standing-fill", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("wrote ") and "standing-approval verdicts" in stripped:
            report.standing_fill_lines.append(stripped)
        elif stripped.endswith("held for review by except_left"):
            report.standing_fill_lines.append(stripped)
    report.standing_fill_status = "filled" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_standing_merge(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(STANDING_FILL)]
    result = spawn("standing-merge", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("merged ", "nothing changed", "stashed ")):
            report.standing_merge_lines.append(stripped)
    report.standing_merge_status = "merged" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_census(
    *,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    update_pins: bool,
    surface: Path,
    record: bool = False,
) -> str:
    """Check (or re-baseline) the census pins, and keep census-green.json honest: a clean check records the key it checked, --update records the key over the pins it just wrote (they are current by construction), and a stale check whose key matches the record deletes the falsified green. The key is computed before a --check spawn (the check mutates nothing) but after an --update (which rewrites the pins the key hashes)."""
    if update_pins:
        census = spawn(
            "census",
            ["uv", "run", "python", "-m", "rebuild.review.census", "--update", "--surface", str(surface)],
            emit=emit,
            registry=registry,
            stream=False,
        )
        _dump_captured(emit, census)
        diff = spawn(
            "git-diff",
            ["git", "diff", "--", "rebuild/review-census-pins.json"],
            emit=emit,
            registry=registry,
            stream=False,
        )
        _dump_captured(emit, diff)
        if census.returncode != 0:
            return "update FAILED"
        if record:
            key = census_skip_fingerprint(ROOT, surface)
            if key is not None:
                record_green(CENSUS_GREEN, key)
        if diff.stdout.strip():
            return "updated (diff shown above — review every moved number)"
        return "updated (no change)"
    key = census_skip_fingerprint(ROOT, surface) if record else None
    census = spawn(
        "census",
        ["uv", "run", "python", "-m", "rebuild.review.census", "--check", "--surface", str(surface)],
        emit=emit,
        registry=registry,
        stream=False,
    )
    _dump_captured(emit, census)
    if census.returncode == 0:
        if key is not None:
            record_green(CENSUS_GREEN, key)
        return "clean"
    if record:
        clear_contradicted_green(CENSUS_GREEN, key)
    return "STALE (informational — re-run with --update-pins or edit by hand)"


def _do_complaints(*, spawn, emit: _Emitter, registry: _ChildRegistry) -> str:
    result = spawn(
        "complaints",
        ["uv", "run", "python", "-m", "rebuild.tools.complaint_docket", str(AUTOSAVE)],
        emit=emit,
        registry=registry,
        stream=False,
    )
    _dump_captured(emit, result)
    if result.returncode != 0:
        return f"FAILED (exit {result.returncode}) — informational"
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped == "no open complaints":
            return stripped
        if stripped.startswith("wrote ") and ": " in stripped:
            return stripped.split(": ", 1)[1]
    return "done"


def _gate_js_task(spawn, emit: _Emitter, registry: _ChildRegistry) -> _StepResult:
    return spawn("gate:js", jstest_argv(), emit=emit, registry=registry, stream=False)


def _gate_make_test_task(spawn, emit: _Emitter, registry: _ChildRegistry) -> _StepResult:
    return spawn("gate:make-test", ["make", "test"], emit=emit, registry=registry, stream=True)


def _gate_conform_task(
    pool_policy: str,
    rebuild_fut: Future | None,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    argv: list[str],
) -> tuple[str, list[str]]:
    """gate:conform shapes the exhaustive font-vs-settle sweep against the fresh M1.otf via run_m1 --conform-only. Under the queue policy it parks at the tail of the make-test -> rebuild chain — behind gate:rebuild's future when that gate runs (which itself already waited out make-test), else directly behind gate:make-test — so its per-config process pool only spins up once the pytest pools have drained. Co-resident, the two heavy gates oversubscribe the box roughly 2:1, and measured that contention roughly tripled gate:rebuild's wall time — a worse critical path than the same work in sequence. The stale conform_summary.json is unlinked here, just before the sweep spawns, so the verdict can only come from this cycle's subprocess (an auto-skipped gate never runs this task and never reads the file)."""
    CONFORM_SUMMARY.unlink(missing_ok=True)
    if pool_policy == "queue":
        for fut in (rebuild_fut, make_fut):
            if fut is not None:
                try:
                    fut.result()
                except Exception:
                    pass
    result = spawn("gate:conform", argv, emit=emit, registry=registry, stream=False)
    summary = None
    if CONFORM_SUMMARY.exists():
        try:
            summary = json.loads(CONFORM_SUMMARY.read_text())
        except ValueError:
            summary = None
    status, failures = evaluate_conform_gate(summary)
    if result.returncode != 0 and not failures:
        status = f"FAILED (exit {result.returncode})"
        failures = [f"conform gate: exited {result.returncode} despite a passing summary"]
    return status, failures


def _gate_rebuild_task(
    pool_policy: str,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    update_pins: bool,
) -> _RebuildOutcome:
    if pool_policy == "queue" and make_fut is not None:
        try:
            make_fut.result()
        except Exception:
            pass
    result = spawn(
        "gate:rebuild",
        ["uv", "run", "pytest", "rebuild/", "-n", "auto", "--dist", "worksteal", "-q", "--tb=no", "-rfE"],
        emit=emit,
        registry=registry,
        stream=False,
    )
    return _classify_rebuild(result, update_pins)


def _gate_result(fut: Future, name: str, failures: list[str]):
    try:
        return fut.result()
    except Exception as exc:
        failures.append(f"{name} raised: {exc!r}")
        return None


def _join_gates(
    report: CycleReport,
    failures: list[str],
    js_fut: Future | None,
    rebuild_fut: Future | None,
    conform_fut: Future | None,
    make_fut: Future | None,
    update_pins: bool,
    emit: _Emitter,
) -> None:
    if js_fut is not None:
        js = _gate_result(js_fut, "gate:js", failures)
        if js is None:
            report.gate_js = "FAILED (exception)"
        else:
            report.gate_js = "green" if js.returncode == 0 else f"FAILED (exit {js.returncode})"
            if js.returncode != 0:
                failures.append("JS suite failed")
    if rebuild_fut is not None:
        outcome = _gate_result(rebuild_fut, "gate:rebuild", failures)
        if outcome is None:
            report.gate_rebuild = "FAILED (exception)"
        else:
            report.gate_rebuild = outcome.status
            report.rebuild_recordable = outcome.recordable
            for test_id in outcome.hard_ids:
                emit.emit(f"  hard rebuild failure: {test_id}")
            failures.extend(outcome.failures)
    if conform_fut is not None:
        conform = _gate_result(conform_fut, "gate:conform", failures)
        if conform is None:
            report.gate_conform = "FAILED (exception)"
        else:
            status, conform_failures = conform
            report.gate_conform = status
            failures.extend(conform_failures)
    if make_fut is not None:
        make = _gate_result(make_fut, "gate:make-test", failures)
        if make is None:
            report.gate_make_test = "FAILED (exception)"
        else:
            report.gate_make_test = "green" if make.returncode == 0 else f"FAILED (exit {make.returncode})"
            if make.returncode != 0:
                failures.append("make test failed")


def _record_gate_greens(report: CycleReport, plan: Plan, gate_keys: dict[str, str], emit: _Emitter) -> None:
    """Persist the concurrent gates' green records after they joined. Each key was snapshotted right after run_m1 finished (the artifacts it hashes are final from then on) and is recomputed here before recording, so a source file edited while the gates ran — content the gates never tested — can never be recorded green. A red gate whose key still matches its record deletes the falsified record."""
    key = gate_keys.get("conform")
    if key:
        if report.gate_conform == "green":
            if conform_skip_fingerprint(ROOT, plan.conform_horizon) == key:
                record_green(CONFORM_GREEN, key)
            else:
                emit.emit("gate:conform green, but its inputs changed while the cycle ran — green not recorded")
        elif report.gate_conform.startswith("FAILED"):
            clear_contradicted_green(CONFORM_GREEN, key)
    key = gate_keys.get("rebuild")
    if key:
        if report.gate_rebuild.startswith("green") and report.rebuild_recordable:
            if rebuild_gate_skip_fingerprint(ROOT) == key:
                record_green(REBUILD_GATE_GREEN, key)
            else:
                emit.emit(
                    "gate:rebuild green, but its input closure changed while the cycle ran — green not recorded"
                )
        elif report.gate_rebuild.startswith("FAILED"):
            clear_contradicted_green(REBUILD_GATE_GREEN, key)


def _run_cycle(
    plan: Plan, report: CycleReport, emit: _Emitter, registry: _ChildRegistry, spawn=_run_step
) -> int:
    pool = ThreadPoolExecutor(max_workers=_GATE_POOL_WORKERS)
    failures: list[str] = []
    try:
        js_fut = None if plan.skip_gates else pool.submit(_gate_js_task, spawn, emit, registry)
        make_fut = (
            None
            if plan.skip_gates or plan.skip_make_test
            else pool.submit(_gate_make_test_task, spawn, emit, registry)
        )
        rebuild_fut: Future | None = None
        conform_fut: Future | None = None
        gate_keys: dict[str, str] = {}
        if not plan.skip_gates and plan.skip_conform:
            report.gate_conform = f"skipped ({plan.conform_note or '--skip-conform'})"
        if not plan.skip_gates and plan.skip_rebuild_gate:
            report.gate_rebuild = f"skipped ({plan.rebuild_gate_note})"
        if not plan.skip_gates and plan.skip_make_test:
            report.gate_make_test = f"skipped ({plan.make_test_note})"

        gate = _do_run_m1(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            budget=plan.job_budget,
            skip=plan.skip_run_m1,
            skip_note=plan.run_m1_note,
            record=plan.record_greens,
            fingerprint=plan.run_m1_fingerprint,
        )
        if gate is None or not gate.ok:
            failures.extend(_run_m1_reasons(gate))
            if plan.skip_gates or not plan.skip_rebuild_gate:
                report.gate_rebuild = "not run (run_m1 gate failed)"
            if not plan.skip_gates and not plan.skip_conform:
                report.gate_conform = "not run (run_m1 gate failed)"
            _join_gates(report, failures, js_fut, None, None, make_fut, plan.update_pins, emit)
            return _finish(report, failures, plan)

        if plan.record_greens and not plan.skip_gates:
            if not plan.skip_conform:
                gate_keys["conform"] = conform_skip_fingerprint(ROOT, plan.conform_horizon)
            if not plan.skip_rebuild_gate:
                gate_keys["rebuild"] = rebuild_gate_skip_fingerprint(ROOT) or ""
        if not plan.skip_gates:
            if not plan.skip_rebuild_gate:
                rebuild_fut = pool.submit(
                    _gate_rebuild_task, plan.pool_policy, make_fut, spawn, emit, registry, plan.update_pins
                )
            if not plan.skip_conform:
                conform_fut = pool.submit(
                    _gate_conform_task,
                    plan.pool_policy,
                    rebuild_fut,
                    make_fut,
                    spawn,
                    emit,
                    registry,
                    conform_gate_argv(plan.conform_jobs, plan.conform_horizon),
                )

        if not _do_surface_build(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            review_out=plan.review_out,
            budget=plan.job_budget,
            skip=plan.skip_surface,
            skip_note=plan.surface_note,
        ):
            failures.append("surface rebuild failed")
            _join_gates(report, failures, js_fut, rebuild_fut, conform_fut, make_fut, plan.update_pins, emit)
            _record_gate_greens(report, plan, gate_keys, emit)
            return _finish(report, failures, plan)

        if plan.carry_out is not None:
            carried = _do_carry(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
            if not carried:
                failures.append("carry_verdicts failed")
            if plan.do_merge:
                if not carried:
                    report.merge_status = "not run (carry failed)"
                    report.echo_fill_status = "not run (carry failed)"
                    report.echo_merge_status = "not run (carry failed)"
                    report.standing_fill_status = "not run (carry failed)"
                    report.standing_merge_status = "not run (carry failed)"
                elif not _do_merge(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("verdict merge failed")
                    report.echo_fill_status = "not run (merge failed)"
                    report.echo_merge_status = "not run (merge failed)"
                    report.standing_fill_status = "not run (merge failed)"
                    report.standing_merge_status = "not run (merge failed)"
                elif not _do_echo_fill(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("echo-fill failed")
                    report.echo_merge_status = "not run (echo-fill failed)"
                    report.standing_fill_status = "not run (echo-fill failed)"
                    report.standing_merge_status = "not run (echo-fill failed)"
                elif not _do_echo_merge(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("echo-merge failed")
                    report.standing_fill_status = "not run (echo-merge failed)"
                    report.standing_merge_status = "not run (echo-merge failed)"
                elif not _do_standing_fill(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("standing-fill failed")
                    report.standing_merge_status = "not run (standing-fill failed)"
                elif not _do_standing_merge(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("standing-merge failed")
        if plan.skip_census:
            report.census_status = f"skipped ({plan.census_skip_note})"
        else:
            report.census_status = _do_census(
                spawn=spawn,
                emit=emit,
                registry=registry,
                update_pins=plan.update_pins,
                surface=plan.census_surface,
                record=plan.record_greens and plan.review_out is None,
            )
        if plan.complaints_note:
            report.complaints_status = f"skipped ({plan.complaints_note})"
        else:
            report.complaints_status = _do_complaints(spawn=spawn, emit=emit, registry=registry)

        _join_gates(report, failures, js_fut, rebuild_fut, conform_fut, make_fut, plan.update_pins, emit)
        _record_gate_greens(report, plan, gate_keys, emit)
        return _finish(report, failures, plan)
    except KeyboardInterrupt:
        registry.terminate_all()
        pool.shutdown(wait=False, cancel_futures=True)
        report.interrupted = True
        return _finish_interrupted(report, failures, registry.killed_count, plan)
    finally:
        pool.shutdown(wait=True)


def _print_summary(report: CycleReport) -> None:
    def show(value: object) -> str:
        return "—" if value is None else str(value)

    print("\n" + "=" * 68)
    print("ARTIFACT CYCLE SUMMARY")
    print("=" * 68)
    print(f"  snapshot dir       : {show(report.snapshot_dir)}")
    print(f"  oracle UNMATCHED   : {show(report.unmatched)} (informational)")
    print(f"  oracle multi_match : {show(report.multi_matched)}")
    print(f"  boundary gate      : {'pass' if report.boundary_pass else show(report.boundary_pass)}")
    print(f"  Manual-pin gate    : {'pass' if report.pins_pass else show(report.pins_pass)}")
    print(f"  surface units      : {show(report.surface_units)}")
    print(f"  surface rows       : {show(report.surface_rows)}")
    print(f"  surface batches    : {show(report.surface_batches)}")
    print(f"  echo groups        : {show(report.echo_groups)}")
    print(f"  carry output       : {show(report.carry_out)}")
    for line in report.carry_lines:
        print(f"      {line}")
    print(f"  merge -> autosave  : {report.merge_status}")
    for line in report.merge_lines:
        print(f"      {line}")
    print(f"  echo-fill          : {report.echo_fill_status}")
    for line in report.echo_fill_lines:
        print(f"      {line}")
    print(f"  echo-merge         : {report.echo_merge_status}")
    for line in report.echo_merge_lines:
        print(f"      {line}")
    print(f"  standing-fill      : {report.standing_fill_status}")
    for line in report.standing_fill_lines:
        print(f"      {line}")
    print(f"  standing-merge     : {report.standing_merge_status}")
    for line in report.standing_merge_lines:
        print(f"      {line}")
    print(f"  census pins        : {report.census_status}")
    print(f"  complaint groups   : {report.complaints_status}")
    print(f"  gate: JS suite     : {report.gate_js}")
    print(f"  gate: rebuild      : {report.gate_rebuild}")
    print(f"  gate: conform      : {report.gate_conform}")
    print(f"  gate: make test    : {report.gate_make_test}")
    print("  run_m1 summaries   :")
    for path in M1_SUMMARY_FILES.values():
        print(f"      {path}")
    print(f"      {CONFORM_SUMMARY}")
    print("=" * 68)


def _as_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _gate_entry(status: str) -> dict:
    return {"status": status, "green": status.startswith("green")}


def _surface_block(surface_dir: Path) -> dict:
    block: dict = {"dir": str(surface_dir), "generated_at": None, "inputs_fingerprint": None}
    try:
        manifest = json.loads((surface_dir / "manifest.json").read_text())
        block["generated_at"] = manifest.get("generated_at")
        block["inputs_fingerprint"] = manifest.get("inputs_fingerprint")
    except Exception:
        pass
    return block


def cycle_summary_payload(report: CycleReport, failures: list[str], plan: Plan, exit_kind: str) -> dict:
    return {
        "format": "ams-cycle-summary/1",
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "exit": exit_kind,
        "failures": list(failures),
        "gates": {
            "js": _gate_entry(report.gate_js),
            "rebuild": _gate_entry(report.gate_rebuild),
            "conform": _gate_entry(report.gate_conform),
            "make_test": _gate_entry(report.gate_make_test),
        },
        "make_test_fingerprint": (
            plan.make_test_fingerprint
            if report.gate_make_test.startswith("green") or plan.skip_make_test
            else None
        ),
        "unmatched": report.unmatched,
        "multi_matched": report.multi_matched,
        "boundary_pass": report.boundary_pass,
        "pins_pass": report.pins_pass,
        "surface_units": report.surface_units,
        "surface_rows": report.surface_rows,
        "surface_batches": report.surface_batches,
        "echo_groups": report.echo_groups,
        "carry_out": _as_str(report.carry_out),
        "carry_lines": list(report.carry_lines),
        "merge_status": report.merge_status,
        "merge_lines": list(report.merge_lines),
        "echo_fill_status": report.echo_fill_status,
        "echo_fill_lines": list(report.echo_fill_lines),
        "echo_merge_status": report.echo_merge_status,
        "echo_merge_lines": list(report.echo_merge_lines),
        "standing_fill_status": report.standing_fill_status,
        "standing_fill_lines": list(report.standing_fill_lines),
        "standing_merge_status": report.standing_merge_status,
        "standing_merge_lines": list(report.standing_merge_lines),
        "census_status": report.census_status,
        "complaints_status": report.complaints_status,
        "snapshot_dir": _as_str(report.snapshot_dir),
        "interrupted": report.interrupted,
        "plan": {
            "verdicts": _as_str(plan.verdicts),
            "carry_out": _as_str(plan.carry_out),
            "do_merge": plan.do_merge,
            "conform_horizon": plan.conform_horizon,
            "pool_policy": plan.pool_policy,
            "skip_gates": plan.skip_gates,
            "skip_conform": plan.skip_conform,
            "skip_run_m1": plan.skip_run_m1,
            "skip_surface": plan.skip_surface,
            "skip_rebuild_gate": plan.skip_rebuild_gate,
            "skip_census": plan.skip_census,
            "update_pins": plan.update_pins,
            "review_out": _as_str(plan.review_out),
            "first_run": plan.first_run,
            "short_id": plan.short_id,
        },
        "argv": list(sys.argv),
        "surface": _surface_block(plan.census_surface),
    }


def write_cycle_summary(payload: dict) -> None:
    target = CYCLE_SUMMARY
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, target)


def _emit_cycle_summary(report: CycleReport, failures: list[str], plan: Plan, exit_kind: str) -> None:
    try:
        write_cycle_summary(cycle_summary_payload(report, failures, plan, exit_kind))
    except Exception as exc:
        print(f"warning: failed to write {CYCLE_SUMMARY}: {exc!r}", file=sys.stderr)


def _preflight(args: argparse.Namespace) -> bool:
    if args.review_out is not None:
        print(
            f"Rehearsal mode: surface writes redirected to {args.review_out}; the live surface at rebuild/out/review is never written."
        )
        return True
    if not server_listening():
        return True
    if args.yes:
        print("=" * 68)
        print("WARNING: a review server is listening on 127.0.0.1:7294.")
        print("Proceeding with --yes. The in-place surface rebuild will restamp the")
        print("manifest and rewrite the shards under it, stranding the live verdicting")
        print("session. AFTER this cycle you MUST:")
        print("  1. restart the review server:  uv run python -m rebuild.review.serve")
        print("  2. reload the app (the carried verdicts are merged into the autosave automatically).")
        print("=" * 68)
        return True
    print("=" * 68)
    print("REFUSING TO RUN: a review server is listening on 127.0.0.1:7294.")
    print("The in-place surface rebuild would strand your live verdicting session")
    print("(livereload rewrites the shards and the manifest restamp orphans the")
    print("autosave). Before re-running:")
    print("  1. in the review app, export or confirm the autosave of your verdicts")
    print("  2. stop the review server")
    print("  3. re-run this command (or pass --yes to override at your own risk)")
    print("  (or pass --review-out <dir> to rehearse without touching the live surface)")
    print("=" * 68)
    return False


def prune_snapshots(tmp_dir: Path, keep: Path) -> list[Path]:
    removed: list[Path] = []
    for path in sorted(tmp_dir.glob("review-pre-*")):
        if not path.is_dir() or path.resolve() == keep.resolve():
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed


def prune_carried(root: Path, stamp: str | None, keep: Path | None) -> tuple[list[Path], list[Path]]:
    """Delete root-level carried files not stamped for the live surface. Only stamp-aligned files are ever read again (status.pick_frontier keys on manifest_generated_at, never on filename or mtime), and the tracked evidence copy lives under rebuild/evidence/, outside this glob. Unreadable files are kept and reported rather than deleted."""
    removed: list[Path] = []
    unreadable: list[Path] = []
    if stamp is None:
        return removed, unreadable
    for path in sorted(root.glob("verdicts-carried-*.json")):
        if keep is not None and path.resolve() == keep.resolve():
            continue
        try:
            data = json.loads(path.read_text())
        except OSError, ValueError:
            unreadable.append(path)
            continue
        if isinstance(data, dict) and data.get("manifest_generated_at") == stamp:
            continue
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed, unreadable


def prune_stashes(root: Path, journal_path: Path) -> list[Path] | None:
    """Delete verdicts-autosave-* stashes not referenced by a journal event at or after the last base event. The reference index, not mtime, is the test: os.replace preserves the displaced store's mtime, so the stash the latest base itself created predates that base on disk. Everything deleted is replayable via --restore-as-of. Returns None (nothing touched) when the journal holds no base to anchor on."""
    from rebuild.review import journal

    events = list(journal.iter_events(journal_path))
    last_base_at = None
    for event in events:
        if event.get("base"):
            last_base_at = event.get("at") or ""
    if last_base_at is None:
        return None
    keep_names = {
        event["stashed"]
        for event in events
        if event.get("stashed") and (event.get("at") or "") >= last_base_at
    }
    removed: list[Path] = []
    for path in sorted(root.glob("verdicts-autosave-*.json")):
        if path.name in keep_names:
            continue
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def retention_cutoff(now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_WINDOW_DAYS)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_retention(plan: Plan) -> None:
    from rebuild.review import journal

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    print("\nRetention (skip with --keep-history):")

    removed = prune_snapshots(ROOT / "tmp", plan.snapshot_dir)
    if removed:
        print(f"  snapshots : removed {len(removed)} ({', '.join(rel(path) for path in removed)}); kept {rel(plan.snapshot_dir)}")
    else:
        print(f"  snapshots : nothing to remove; kept {rel(plan.snapshot_dir)}")

    try:
        stamp = json.loads((REVIEW_OUT / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        stamp = None
    if stamp is None:
        print("  carried   : left intact (no surface manifest to align against)")
    else:
        removed, unreadable = prune_carried(ROOT, stamp, plan.carry_out)
        print(f"  carried   : removed {len(removed)} stale verdicts-carried-*.json; kept the stamp-aligned frontier")
        for path in unreadable:
            print(f"              kept {rel(path)} (unreadable, not pruning it)")

    journal_path = ROOT / journal.JOURNAL_NAME
    removed_stashes = prune_stashes(ROOT, journal_path)
    if removed_stashes is None:
        print("  stashes   : left intact (the journal holds no base event to anchor on)")
    else:
        print(f"  stashes   : removed {len(removed_stashes)} verdicts-autosave-* stashes older than the journal's last base")

    result = journal.compact(journal_path, cutoff=retention_cutoff())
    if result["compacted"]:
        total = result["dropped_lines"] + result["kept_lines"]
        print(f"  journal   : compacted {total} -> {result['kept_lines']} lines (restore floor now {result['floor_at']})")
    else:
        print(f"  journal   : left intact (no base event older than {RETENTION_WINDOW_DAYS} days)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the commit-time artifact cycle: snapshot, run_m1, surface rebuild, carry, census pins, gates."
    )
    parser.add_argument(
        "--verdicts",
        type=Path,
        help="prior verdicts master to carry forward (default: auto-resolve the best candidate among the autosave and the verdicts-*.json files at the repo root and under rebuild/evidence)",
    )
    parser.add_argument("--no-carry", action="store_true", help="skip the verdict carry-forward step")
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="leave verdicts-autosave.json untouched after the carry (skip the automatic merge into the live store)",
    )
    parser.add_argument(
        "--carry-out",
        type=Path,
        help="carried-forward output path (default: verdicts-carried-<short hash>.json at the repo root)",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        help="where to snapshot the current surface (default: tmp/review-pre-<short hash>)",
    )
    parser.add_argument(
        "--update-pins",
        action="store_true",
        help="re-baseline the census pins and print their git diff (default: check only, report staleness)",
    )
    parser.add_argument(
        "--skip-gates",
        action="store_true",
        help="skip the four post-build gates (JS suite, rebuild suite, conformance sweep, make test)",
    )
    parser.add_argument(
        "--skip-conform",
        action="store_true",
        help="skip gate:conform (the exhaustive font-vs-settle sweep) while keeping the other gates",
    )
    parser.add_argument(
        "--force-make-test",
        action="store_true",
        help="run gate:make-test even when its input closure is unchanged since its last green run (the auto-skip)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="run every stage and gate even when a green record proves its inputs unchanged since the last green run (disables all auto-skips, gate:make-test's included)",
    )
    parser.add_argument(
        "--conform-horizon",
        type=int,
        default=CONFORM_HORIZON_DEFAULT,
        help="exhaustive sweep length for gate:conform, passed through to run_m1 --conform-only; drop below 5 when the sweep becomes the cycle's long pole — witness top-ups keep rule/transition coverage exact at any horizon",
    )
    parser.add_argument(
        "--rebuild-pool",
        choices=POOL_POLICIES,
        default=REBUILD_POOL_POLICY_DEFAULT,
        help="how the heavy gates share cores: 'queue' (one pool at a time — make-test, then rebuild, then conform; default) or 'overlap' (co-resident)",
    )
    parser.add_argument(
        "--review-out",
        type=Path,
        default=None,
        help="rehearsal mode: redirect the surface write to this dir so the cycle can run while the live server is up",
    )
    parser.add_argument(
        "--keep-history",
        action="store_true",
        help="skip the green-finish retention pass (old snapshots, stale carried files and stashes, and the journal's pre-window history all stay on disk)",
    )
    parser.add_argument("--yes", action="store_true", help="override the running-review-server refusal")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved step plan and exit without executing anything",
    )
    args = parser.parse_args(argv)
    if args.fresh:
        args.force_make_test = True

    first_run = not (REVIEW_OUT / "manifest.json").exists()

    skip_make_test = False
    make_test_note = ""
    make_test_fp: str | None = None
    if not args.skip_gates:
        make_test_fp = make_test_closure_fingerprint(ROOT)
        if (
            not args.force_make_test
            and make_test_fp is not None
            and make_test_fp == prior_make_test_fingerprint()
        ):
            skip_make_test = True
            make_test_note = "closure unchanged since its last green run; --force-make-test overrides"
            print(f"gate:make-test auto-skipped: {make_test_note}")

    run_m1_fp = run_m1_skip_fingerprint(ROOT)
    skip_run_m1 = False
    run_m1_note = ""
    skip_surface = False
    surface_note = ""
    skip_rebuild_gate = False
    rebuild_gate_note = ""
    conform_note = ""
    auto_skip_conform = False
    skip_census = False
    census_skip_note = ""
    if not args.fresh:
        green = read_green_record(RUN_M1_GREEN)
        if green is not None and green["fingerprint"] == run_m1_fp and m1_artifacts_present(ROOT):
            skip_run_m1 = True
            run_m1_note = "build inputs unchanged since the last green M1 build; --fresh overrides"
            print(f"run_m1 auto-skipped: {run_m1_note}")
    if skip_run_m1:
        if args.review_out is None and not first_run and surface_build_skippable(ROOT):
            skip_surface = True
            surface_note = "the surface already reflects these inputs byte for byte, stamp included; --fresh overrides"
            print(f"surface-build auto-skipped: {surface_note}")
        if not args.skip_gates and not args.skip_conform:
            green = read_green_record(CONFORM_GREEN)
            if green is not None and green["fingerprint"] == conform_skip_fingerprint(
                ROOT, args.conform_horizon
            ):
                auto_skip_conform = True
                conform_note = "font and sweep inputs unchanged since its last green sweep; --fresh overrides"
                print(f"gate:conform auto-skipped: {conform_note}")
        if not args.skip_gates:
            rebuild_key = rebuild_gate_skip_fingerprint(ROOT)
            green = read_green_record(REBUILD_GATE_GREEN)
            if rebuild_key is not None and green is not None and green["fingerprint"] == rebuild_key:
                skip_rebuild_gate = True
                rebuild_gate_note = "input closure unchanged since its last green run; --fresh overrides"
                print(f"gate:rebuild auto-skipped: {rebuild_gate_note}")
        if skip_surface and not args.update_pins:
            census_key = census_skip_fingerprint(ROOT)
            green = read_green_record(CENSUS_GREEN)
            if census_key is not None and green is not None and green["fingerprint"] == census_key:
                skip_census = True
                census_skip_note = "surface, pins, and source inputs unchanged since the last clean check; --fresh overrides"
                print(f"census auto-skipped: {census_skip_note}")

    if not args.no_carry and args.verdicts is None and not first_run:
        resolved = resolve_carry_source()
        if resolved is None:
            args.no_carry = True
            print(
                "No carryable verdicts found (neither the autosave nor any verdicts-*.json at the repo root or under rebuild/evidence holds an effective verdict); proceeding without carry. Pass --verdicts to name a master explicitly."
            )
        else:
            args.verdicts = resolved["path"]
            print(describe_carry_source(resolved, ROOT))

    if args.dry_run:
        plan = build_plan(
            verdicts=args.verdicts,
            no_carry=args.no_carry,
            carry_out=args.carry_out,
            snapshot_dir=args.snapshot_dir,
            update_pins=args.update_pins,
            skip_gates=args.skip_gates,
            first_run=first_run,
            short_id=resolve_short_id(),
            no_merge=args.no_merge,
            skip_conform=args.skip_conform or auto_skip_conform,
            skip_make_test=skip_make_test,
            make_test_note=make_test_note,
            make_test_fingerprint=make_test_fp,
            conform_horizon=args.conform_horizon,
            pool_policy=args.rebuild_pool,
            review_out=args.review_out,
            skip_run_m1=skip_run_m1,
            run_m1_note=run_m1_note,
            run_m1_fingerprint=run_m1_fp,
            skip_surface=skip_surface,
            surface_note=surface_note,
            skip_rebuild_gate=skip_rebuild_gate,
            rebuild_gate_note=rebuild_gate_note,
            conform_note=conform_note,
            skip_census=skip_census,
            census_skip_note=census_skip_note,
            keep_history=args.keep_history,
        )
        print(render_plan(plan))
        return 0

    if not _preflight(args):
        return 2

    if first_run:
        print("First-run mode: no existing surface at rebuild/out/review — skipping snapshot and carry.")

    plan = build_plan(
        verdicts=args.verdicts,
        no_carry=args.no_carry,
        carry_out=args.carry_out,
        snapshot_dir=args.snapshot_dir,
        update_pins=args.update_pins,
        skip_gates=args.skip_gates,
        first_run=first_run,
        short_id=resolve_short_id(),
        no_merge=args.no_merge,
        skip_conform=args.skip_conform or auto_skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fp,
        conform_horizon=args.conform_horizon,
        pool_policy=args.rebuild_pool,
        review_out=args.review_out,
        skip_run_m1=skip_run_m1,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fp,
        skip_surface=skip_surface,
        surface_note=surface_note,
        skip_rebuild_gate=skip_rebuild_gate,
        rebuild_gate_note=rebuild_gate_note,
        conform_note=conform_note,
        skip_census=skip_census,
        census_skip_note=census_skip_note,
        record_greens=True,
        keep_history=args.keep_history,
    )

    report = CycleReport()

    if not first_run:
        if plan.snapshot_dir.exists():
            print(f"ERROR: snapshot dir already exists: {plan.snapshot_dir}")
            print("Refusing to overwrite the only recovery copy. Remove it or pass --snapshot-dir.")
            return 2
        how = snapshot_surface(REVIEW_OUT, plan.snapshot_dir)
        report.snapshot_dir = plan.snapshot_dir
        print(f"Snapshotted {REVIEW_OUT} -> {plan.snapshot_dir} ({how})")

    emit = _Emitter()
    registry = _ChildRegistry()
    return _run_cycle(plan, report, emit, registry)


def _finish(report: CycleReport, failures: list[str], plan: Plan) -> int:
    _print_summary(report)
    _emit_cycle_summary(report, failures, plan, "failed" if failures else "ok")
    if failures:
        print("\nCYCLE FAILED:")
        for reason in failures:
            print(f"  - {reason}")
        return 1
    if plan.retention and plan.record_greens:
        try:
            run_retention(plan)
        except Exception as exc:
            print(f"warning: retention pass failed: {exc!r}", file=sys.stderr)
    print("\nCycle complete.")
    return 0


def _finish_interrupted(report: CycleReport, failures: list[str], killed_count: int, plan: Plan) -> int:
    _print_summary(report)
    _emit_cycle_summary(report, failures, plan, "interrupted")
    print(f"\nCYCLE INTERRUPTED (SIGINT): terminated {killed_count} child process(es).")
    return 130


if __name__ == "__main__":
    sys.exit(main())

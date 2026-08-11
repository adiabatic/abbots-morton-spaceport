"""Which commits would arm an M1 rebuild, replayed against the repo's OWN skip closure.

  classify_commits.py [--window 80] [--ref HEAD] [--compare commits-80.json]

Prints one JSON object — a summary plus one row per commit, newest first — with the shape
`commits-80.json` carries.

The closure is `artifact_cycle.run_m1_skip_lines`: `fingerprint.data_lines` (rune files, the
schemas, script.yaml, punctuation.yaml, the three m1-*.yaml tables and the Senior kerning),
`fingerprint.pipeline_code_paths` (rebuild/pipeline and rebuild/validation), and uv.lock. The
baselines and the oracle's subset tables are in the closure too but live under rebuild/out,
which is gitignored, so no commit can move them.

Rune files are judged prose-blind, by `fingerprint.rune_file_digest` over BOTH blob versions —
the commit's and its parent's — rather than by whether the file changed. A commit that only
edits a ductus, a notes block, a non-refuse `why` or a YAML comment leaves the digest where it
was and arms nothing. Every other closure member is judged by whether the commit touched it,
which for a tracked file is the same question as whether its bytes moved.

The live modules are the authority, so a window is always classified under today's definition of
the closure, not under the one in force when its commits landed. Where those differ the
reclassification is the point: it says what that work would cost now.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))

from rebuild.pipeline import fingerprint  # noqa: E402
from rebuild.tools import artifact_cycle  # noqa: E402

DATA_GLOBS = (("glyph_data/runes/", ".yaml"), ("rebuild/schema/", ".json"))
CODE_GLOBS = (("rebuild/pipeline/", ".py"), ("rebuild/validation/", ".py"))
RUNE_DIR, RUNE_SUFFIX = DATA_GLOBS[0]
REVIEW_DIR = "rebuild/review/"
STATIC_DIR = "rebuild/review/static/"


def rel_of(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def in_globs(rel: str, globs: tuple[tuple[str, str], ...]) -> bool:
    return any(
        rel.startswith(directory) and rel.endswith(suffix) and "/" not in rel[len(directory) :]
        for directory, suffix in globs
    )


def named_members(paths: list[Path], globs: tuple[tuple[str, str], ...]) -> set[str]:
    return {rel for rel in map(rel_of, paths) if not in_globs(rel, globs)}


DATA_FILES = named_members(fingerprint.data_paths(ROOT), DATA_GLOBS)
CODE_FILES = named_members(fingerprint.pipeline_code_paths(ROOT), CODE_GLOBS) | {"uv.lock"}
FONT_FILES = {rel_of(path) for path in fingerprint.font_paths(ROOT)}


def is_rune(rel: str) -> bool:
    return rel.startswith(RUNE_DIR) and rel.endswith(RUNE_SUFFIX) and "/" not in rel[len(RUNE_DIR) :]


def is_data(rel: str) -> bool:
    return rel in DATA_FILES or in_globs(rel, DATA_GLOBS)


def is_code(rel: str) -> bool:
    return rel in CODE_FILES or in_globs(rel, CODE_GLOBS)


def is_surface(rel: str) -> bool:
    review_module = (
        rel.startswith(REVIEW_DIR)
        and rel.endswith(".py")
        and "/" not in rel[len(REVIEW_DIR) :]
        and rel != f"{REVIEW_DIR}serve.py"
    )
    return review_module or rel.startswith(STATIC_DIR) or rel in FONT_FILES


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def blob(sha: str, rel: str) -> bytes | None:
    result = subprocess.run(["git", "show", f"{sha}:{rel}"], cwd=ROOT, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def rune_digest_moved(scratch: Path, sha: str, rel: str) -> bool:
    """Whether the prose-blind digest differs between the commit's blob and its parent's. An added or deleted rune moves the closure by gaining or losing a line, so a missing side counts as moved."""
    scratch_file = scratch / "rune.yaml"
    digests = []
    for revision in (f"{sha}^", sha):
        payload = blob(revision, rel)
        if payload is None:
            return True
        scratch_file.write_bytes(payload)
        digests.append(fingerprint.rune_file_digest(scratch_file))
    return digests[0] != digests[1]


def classify(scratch: Path, sha: str, date: str, files: list[str]) -> dict:
    runes = [rel for rel in files if is_rune(rel)]
    moved_runes = [rel for rel in runes if rune_digest_moved(scratch, sha, rel)]
    prose_only = sorted(set(runes) - set(moved_runes))
    data_inputs = sorted(moved_runes + [rel for rel in files if is_data(rel) and not is_rune(rel)])
    code_inputs = sorted(rel for rel in files if is_code(rel))
    arming = data_inputs + code_inputs
    return {
        "sha": sha[:8],
        "date": date,
        "n_files": len(files),
        "arms_m1": bool(arming),
        "m1_kind": "data_or_rune" if data_inputs else "pipeline_code" if code_inputs else None,
        "touches_rune_semantics": bool(moved_runes),
        "rune_prose_only": prose_only,
        "arms_make_test": any(not artifact_cycle.make_test_exempt(rel) for rel in files),
        "arms_surface": any(is_surface(rel) for rel in files),
        "md_only": bool(files) and all(rel.endswith(".md") for rel in files),
        "m1_inputs": arming,
    }


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    dates = sorted({row["date"] for row in rows})
    counted = Counter(row["m1_kind"] for row in rows)
    inputs: Counter[str] = Counter()
    for row in rows:
        inputs.update(row["m1_inputs"])

    def pct(n: int) -> float:
        return round(100 * n / total, 1) if total else 0.0

    return {
        "commits": total,
        "active_days": len(dates),
        "date_span": [dates[0], dates[-1]] if dates else [],
        "commits_per_day": round(total / len(dates), 2) if dates else 0.0,
        "arms_m1": sum(row["arms_m1"] for row in rows),
        "arms_m1_pct": pct(sum(row["arms_m1"] for row in rows)),
        "m1_pipeline_code": counted["pipeline_code"],
        "m1_data_or_rune": counted["data_or_rune"],
        "touches_rune_semantics": sum(row["touches_rune_semantics"] for row in rows),
        "rune_prose_only_commits": sum(
            1 for row in rows if row["rune_prose_only"] and not row["touches_rune_semantics"]
        ),
        "arms_make_test": sum(row["arms_make_test"] for row in rows),
        "arms_make_test_pct": pct(sum(row["arms_make_test"] for row in rows)),
        "arms_surface": sum(row["arms_surface"] for row in rows),
        "arms_surface_pct": pct(sum(row["arms_surface"] for row in rows)),
        "md_only": sum(row["md_only"] for row in rows),
        "arming_inputs": dict(inputs.most_common()),
    }


def compare(rows: list[dict], reference: Path) -> dict:
    """Row-by-row agreement with an earlier run's JSON, over the keys that run recorded — so a schema that has since grown a field cannot register as a disagreement."""
    prior = json.loads(reference.read_text())
    mine = {row["sha"]: row for row in rows}
    disagreements = []
    for row in prior["rows"]:
        got = mine.get(row["sha"])
        if got is None:
            disagreements.append({"sha": row["sha"], "reason": "outside the classified window"})
            continue
        fields = {key: [value, got[key]] for key, value in row.items() if got[key] != value}
        if fields:
            disagreements.append({"sha": row["sha"], "date": row["date"], "fields": fields})
    summary = summarize(rows)
    return {
        "reference": rel_of(reference),
        "rows_compared": len(prior["rows"]),
        "rows_agreeing": len(prior["rows"]) - len(disagreements),
        "summary_disagreements": {
            key: [value, summary[key]] for key, value in prior["summary"].items() if summary[key] != value
        },
        "row_disagreements": disagreements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=int, default=80, help="how many commits to classify")
    parser.add_argument("--ref", default="HEAD", help="newest commit of the window")
    parser.add_argument("--compare", type=Path, help="an earlier run's JSON to check against")
    args = parser.parse_args()

    log = git("log", "--format=%H\t%ad", "--date=short", "-n", str(args.window), args.ref)
    commits = [line.split("\t") for line in log.splitlines()]
    rows = []
    with tempfile.TemporaryDirectory(dir=ROOT / "bench-the-rebuild") as scratch:
        for sha, date in commits:
            listing = git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", "-z", sha)
            files = [entry for entry in listing.split("\0") if entry]
            rows.append(classify(Path(scratch), sha, date, files))

    report = {"summary": summarize(rows), "rows": rows}
    if args.compare:
        report["reproduction"] = compare(rows, args.compare)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Compute the review corpus's readiness: whether the served corpus is the table-diff corpus (engine/REVIEW-PLAN.md §2.3), whether it matches the runes and code on disk (`fingerprint.TABLE_DIFF_COMPONENTS`, its after font is engine/out/build/Senior-Regular.otf byte for byte, and both app sidecars are stamped for its manifest, the check of a finished corpus the cycle shares, `app_index.outputs_not_current`), whether a green artifact cycle produced it, whether the snapshot it compares with is the one the tracked pointer names (`_snapshot_check`), whether the verdict store is stamped for it, how many of its human units read blank, and how many unaccepted changes it shows (`_diff_check`, which never fails). The serve.py /status handler and the verdict_ready CLI both render the dict `compute_status` returns, so a change to its shape changes theirs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from engine.pipeline import fingerprint
from engine.review import app_index, shards, store_lock
from engine.review.verdict_store import (
    MalformedStore,
    latest_by_unit,
    orphans_dir_for,
    read_store_document,
    summarize_orphans,
)

CORPUS_REMEDY = "make artifact-cycle"
# Where the snapshots and the tracked pointer are under the repo root (`engine.review.snapshot.DEFAULT_HOME`, `POINTER_PATH`), named here so this module imports the snapshot only at a call.
SNAPSHOT_HOME = Path("var") / "keep" / "review-snapshot"
POINTER = Path("engine") / "review-snapshot.json"
TABLE_DIFF_MODE = "table-diff"


def count_effective(records: Mapping[str, dict]) -> int:
    """Return how many of `records` are verdicts other than skips."""
    return sum(1 for record in records.values() if record.get("verdict") != "skip")


def _corpus_check(manifest) -> dict:
    """Check that a corpus is served and that it is the table-diff corpus the cycle builds."""
    if manifest is None:
        return {
            "level": "fail",
            "detail": "The review corpus has no readable manifest.json.",
            "remedy": CORPUS_REMEDY,
        }
    if manifest.get("mode") != TABLE_DIFF_MODE:
        return {
            "level": "fail",
            "detail": f"The review corpus's manifest names the mode {manifest.get('mode')!r}, not the table-diff corpus the cycle builds.",
            "remedy": CORPUS_REMEDY,
        }
    return {"level": "ok", "detail": "The review corpus manifest is present and readable.", "remedy": None}


def load_human_unit_ids(review_dir) -> frozenset[str]:
    manifest = json.loads((Path(review_dir) / "manifest.json").read_text())
    persisted = manifest["human_unit_ids"]
    if not isinstance(persisted, list) or not all(
        isinstance(unit, str) and unit.startswith("u-") for unit in persisted
    ):
        raise TypeError("manifest human_unit_ids must be a list of u- ids")
    if len(persisted) != len(set(persisted)):
        raise ValueError("manifest human_unit_ids must be unique")
    return frozenset(persisted)


def _load_json_dict(path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text())
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def _rel(repo_root, path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return str(path)


def table_diff_inputs(repo_root, snapshot_home=None, build_out=None) -> dict:
    """Return the table-diff components the sources and the current snapshot give now (`fingerprint.table_diff_values`): the record of the snapshot `current` names under `snapshot_home`, by default `var/keep/review-snapshot` under `repo_root`, and the tables' stamp the sources give (`run_build.tables_inputs`), or with `build_out` the stamp the windows heads of the build there carry (`snapshot.tables_stamp`), which is what a build over it would copy, the artifact cycle's question when it decides whether to skip the corpus build. The two modules are imported here, at the call, so the server loads them only when it first answers /status."""
    from engine.review import snapshot

    home = Path(snapshot_home) if snapshot_home is not None else Path(repo_root) / SNAPSHOT_HOME
    directory = snapshot.current(home)
    if build_out is not None:
        tables = snapshot.tables_stamp(Path(build_out))
    else:
        from engine.pipeline import run_build

        tables = run_build.tables_inputs()
    return fingerprint.table_diff_values(
        Path(repo_root),
        directory / snapshot.RECORD_NAME if directory is not None else None,
        tables,
    )


def _freshness_check(
    manifest, manifest_fp, repo_root, recompute, artifact_cycle_remedy, review_dir, build_out
) -> dict:
    """The freshness check: every `fingerprint.TABLE_DIFF_COMPONENTS` value the manifest records against `recompute(repo_root)`, a `snapshot` both leave null (no snapshot) counting as fresh, then the after font against engine/out/build/Senior-Regular.otf byte for byte, since the corpus's kern units show its `kern` lookup, then the files a finished build leaves (`app_index.outputs_not_current`); a change in the asset component alone warns, since the cycle refreshes the served copy in place."""
    names = fingerprint.TABLE_DIFF_COMPONENTS
    unknown = {name: "unknown" for name in names}
    if not isinstance(manifest_fp, dict):
        return {
            "level": "fail",
            "detail": "The corpus manifest records no input fingerprint, so its freshness cannot be verified.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    try:
        current = recompute(repo_root)
    except Exception:
        return {
            "level": "fail",
            "detail": "The current build inputs could not be recomputed, so the corpus freshness is unknown.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    components: dict[str, str] = {}
    for name in names:
        recorded = manifest_fp.get(name)
        if recorded == current.get(name) and (recorded is not None or name == "snapshot"):
            components[name] = "fresh"
        elif recorded is None:
            components[name] = "unknown"
        else:
            components[name] = "stale"
    hard = [name for name in names if name not in shards.ASSET_COMPONENTS and components[name] != "fresh"]
    if hard:
        stale = [name for name in hard if components[name] == "stale"]
        detail = (
            f"The build inputs changed since the corpus was generated: {', '.join(stale)}."
            if stale
            else f"These components cannot be verified until the next build records them: {', '.join(hard)}."
        )
        return {"level": "fail", "detail": detail, "remedy": artifact_cycle_remedy, "components": components}
    fonts = manifest.get("fonts")
    after_record = fonts.get("after") if isinstance(fonts, dict) else None
    recorded_sha = after_record.get("sha256") if isinstance(after_record, dict) else None
    try:
        after_sha = fingerprint.file_sha256(Path(build_out) / "Senior-Regular.otf")
    except OSError:
        after_sha = None
    if not isinstance(recorded_sha, str) or after_sha != recorded_sha:
        return {
            "level": "fail",
            "detail": "The corpus's after font is not the built font on disk: engine/out/build/Senior-Regular.otf is missing or has moved on since this corpus was built, so the review app is serving last build's font.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    missing = app_index.outputs_not_current(review_dir)
    if missing:
        return {
            "level": "fail",
            "detail": f"These files are missing or stamped for another manifest: {', '.join(missing)}. Either the build that wrote this corpus did not finish, or the manifest was rewritten without them.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    if any(components[name] != "fresh" for name in shards.ASSET_COMPONENTS if name in components):
        return {
            "level": "warn",
            "detail": "Only the review UI assets changed since the corpus was generated; the units are unchanged, and the cycle refreshes the served copy in place.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    return {
        "level": "ok",
        "detail": "The corpus reflects the current build inputs.",
        "remedy": None,
        "components": components,
    }


REMAKE_REMEDY = "make review-snapshot-remake"
RETAKE_REMEDY = "make review-accept ARGS='--apply --retake'"


def _snapshot_check(pointer_path, snapshot_home) -> dict:
    """Check that the snapshot the corpus compares with is the accepted one: the tracked pointer (`snapshot.read_pointer`) reads and names a snapshot in this checkout's format, a snapshot is current under `snapshot_home` and reads (`snapshot.load`), and its digest is the pointer's. A missing or mismatched snapshot is remade from the commit that last changed the pointer (`make review-snapshot-remake`); a pointer that does not read or names another format is replaced by a retake."""
    from engine.review import snapshot

    try:
        pointer = snapshot.read_pointer(Path(pointer_path))
    except snapshot.SnapshotError as error:
        return {
            "level": "fail",
            "detail": f"{error}, so no snapshot is the accepted one.",
            "remedy": RETAKE_REMEDY,
        }
    if pointer is None:
        return {
            "level": "fail",
            "detail": f"{Path(pointer_path).name} is missing, so no snapshot is the accepted one.",
            "remedy": RETAKE_REMEDY,
        }
    digest = str(pointer["digest"])
    if pointer.get("snapshot_format") != snapshot.SNAPSHOT_FORMAT:
        return {
            "level": "fail",
            "detail": f"The pointer names an {pointer.get('snapshot_format')!r} snapshot, and this checkout reads only {snapshot.SNAPSHOT_FORMAT}.",
            "remedy": RETAKE_REMEDY,
        }
    directory = snapshot.current(Path(snapshot_home))
    if directory is None:
        return {
            "level": "fail",
            "detail": f"No snapshot is current on this machine; the pointer names {digest[:12]}.",
            "remedy": REMAKE_REMEDY,
        }
    try:
        found = snapshot.load(directory)
    except snapshot.SnapshotError as error:
        return {
            "level": "fail",
            "detail": f"The current snapshot does not read: {error}.",
            "remedy": REMAKE_REMEDY,
        }
    if found.digest != digest:
        return {
            "level": "fail",
            "detail": f"The current snapshot, {found.digest[:12]}, is not the accepted one the pointer names, {digest[:12]}.",
            "remedy": REMAKE_REMEDY,
        }
    return {
        "level": "ok",
        "detail": f"The current snapshot is the accepted one the pointer names, {digest[:12]}, taken at {found.taken_at}.",
        "remedy": None,
    }


ACCEPT_REMEDY = "make review-accept ARGS=--apply"
REFUSED_VERDICTS = frozenset({"reject", "neither"})


def baseline_since(manifest, repo_root) -> str:
    """What the corpus's changes are since: the first twelve hex digits of the commit the snapshot stands for, read from git now (`snapshot.pointer_commit`) or else from the manifest's `baseline`, or when the accept that took it is not committed yet, when it was taken; `no snapshot` for a corpus built with none."""
    from engine.review import snapshot

    baseline = manifest.get("baseline") if isinstance(manifest, dict) else None
    if not isinstance(baseline, dict) or not baseline.get("present"):
        return "no snapshot"
    digest = baseline.get("digest")
    commit = (
        snapshot.pointer_commit(digest, Path(repo_root)) if isinstance(digest, str) else None
    ) or baseline.get("commit")
    if isinstance(commit, str) and commit:
        return commit[:12]
    return f"the snapshot taken at {baseline.get('taken_at')}"


def _diff_check(manifest, records: Mapping[str, dict] | None, human_ids, repo_root) -> dict:
    """Count the corpus's unaccepted changes: every unit (`count`), the human units that need a verdict (`human`), of which `blank` read blank or skipped and `rejected` read reject or neither, the machine-approved units (`machine`), and the units of the classes that take no verdict (`none`). It never fails. Once every human unit is verdicted and none is rejected, it warns with the accept as its remedy."""
    totals = manifest.get("totals")
    units = totals.get("units") if isinstance(totals, dict) else None
    count = units if isinstance(units, int) else 0
    human = frozenset(human_ids or ())
    none = sum(
        meta.get("unit_count", 0)
        for meta in manifest.get("classes") or ()
        if isinstance(meta, dict)
        and meta.get("no_verdict") is True
        and isinstance(meta.get("unit_count"), int)
    )
    machine = max(count - len(human) - none, 0)
    records = records or {}
    verdicts = [records.get(unit, {}).get("verdict") for unit in human]
    blank = sum(1 for verdict in verdicts if verdict is None or verdict == "skip")
    rejected = sum(1 for verdict in verdicts if verdict in REFUSED_VERDICTS)
    since = baseline_since(manifest, repo_root)
    detail = (
        f"{count:,} unaccepted change{'' if count == 1 else 's'} since {since}: {len(human):,} need a verdict "
        f"({blank:,} blank, {rejected:,} rejected), {machine:,} machine-approved, {none:,} need none."
    )
    due = count > 0 and blank == 0 and rejected == 0
    return {
        "level": "warn" if due else "ok",
        "detail": detail,
        "remedy": ACCEPT_REMEDY if due else None,
        "count": count,
        "human": len(human),
        "blank": blank,
        "rejected": rejected,
        "machine": machine,
        "none": none,
        "since": since,
    }


def _gates_check(summary, generated_at, manifest_fp, artifact_cycle_remedy) -> dict:
    """Check the recorded cycle's gates by each gate entry's `skip` field. A "proved" skip means a matching green record showed that this content already passed, so it counts toward readiness. Every other non-green gate blocks a review session: a "forced" skip or a gate that did not run is reported as unverified, and anything else as failing. The status string cannot make this distinction, because every skip kind's status starts with "skipped (", so reading it would let a `--skip-conform` pass report READY."""
    if summary is None:
        return {
            "level": "fail",
            "detail": "There is no recorded artifact cycle for this corpus.",
            "remedy": artifact_cycle_remedy,
        }
    corpus = summary.get("corpus") or {}
    if corpus.get("generated_at") != generated_at or corpus.get("inputs_fingerprint") != manifest_fp:
        return {
            "level": "fail",
            "detail": "The recorded cycle is for a different corpus than the one being served.",
            "remedy": artifact_cycle_remedy,
        }
    gates = summary.get("gates") or {}
    exit_val = summary.get("exit")
    non_green = [name for name in sorted(gates) if not (gates[name] or {}).get("green")]
    if exit_val in ("interrupted", "failed"):
        failed = [name for name in non_green if not (gates[name] or {}).get("skip")]
        if failed:
            detail = f"The last artifact cycle {exit_val} with failing gates: {', '.join(failed)}."
        else:
            detail = f"The last artifact cycle {exit_val}."
        return {"level": "fail", "detail": detail, "remedy": artifact_cycle_remedy}
    if not non_green:
        return {
            "level": "ok",
            "detail": f"The last artifact cycle finished green at {summary.get('finished_at')}.",
            "remedy": None,
        }
    entries = {name: (gates[name] or {}) for name in non_green}
    unproved = [name for name in non_green if entries[name].get("skip") != "proved"]
    if not unproved:
        return {
            "level": "ok",
            "detail": f"The last artifact cycle finished green at {summary.get('finished_at')}; every gate it skipped was already proved on this exact content.",
            "remedy": None,
        }
    unverified = [
        name
        for name in unproved
        if entries[name].get("skip")
        or str(entries[name].get("status", "")).startswith(("skipped", "not run"))
    ]
    failing = [name for name in unproved if name not in set(unverified)]
    if failing:
        return {
            "level": "fail",
            "detail": f"The last artifact cycle has failing gates: {', '.join(failing)}.",
            "remedy": artifact_cycle_remedy,
        }
    return {
        "level": "fail",
        "detail": f"The last artifact cycle left gates unverified: {', '.join(unverified)}.",
        "remedy": artifact_cycle_remedy,
    }


def _verdict_store_check(
    autosave_path, generated_at, artifact_cycle_remedy, autosave=None
) -> tuple[dict, dict[str, dict] | None]:
    """Check the store, and return the check with the store's latest record by unit when the store is stamped for the served corpus, else None. `autosave` is the parsed store when the caller already holds it, as the review server does; without it the store is read from `autosave_path`, and a store holding a record `verdict_store.well_formed` refuses fails the check, naming the record."""
    if autosave is None:
        path = Path(autosave_path)
        try:
            raw = path.read_bytes() if path.exists() else None
        except OSError:
            raw = None
        try:
            autosave = read_store_document(raw, f"The store {path.name}") if raw is not None else None
        except MalformedStore as malformed:
            return {
                "level": "fail",
                "detail": f"{malformed}, so the review server serves no verdict from it and refuses every save, and a pass refuses to land over it.",
                "remedy": "Replace the store with a readable one, or move it aside so the review server starts an empty one.",
            }, None
    if autosave is None:
        return {
            "level": "warn",
            "detail": "There is no autosave yet, so no in-progress verdicts are loaded.",
            "remedy": None,
        }, None
    if autosave["manifest_generated_at"] != generated_at:
        return {
            "level": "fail",
            "detail": (
                f"The autosave is stamped for a different corpus ({autosave['manifest_generated_at']}) "
                "than the one being served."
            ),
            "remedy": artifact_cycle_remedy,
        }, None
    records = latest_by_unit(autosave["verdicts"])
    return {
        "level": "ok",
        "detail": f"The autosave is aligned with this corpus and holds {count_effective(records):,} effective verdicts.",
        "remedy": None,
    }, records


def _blanks_check(records: Mapping[str, dict] | None, human_ids) -> dict:
    """Count the human units that hold no verdict but a skip."""
    if records is None:
        return {
            "level": "ok",
            "detail": "The blank count needs an autosave aligned with this corpus.",
            "count": None,
        }
    if human_ids is None:
        return {
            "level": "ok",
            "detail": "The corpus manifest's human unit ids could not be read, so the blank count is unavailable.",
            "count": None,
        }
    verdicted = {unit for unit, record in records.items() if record.get("verdict") != "skip"}
    remaining = len(set(human_ids) - verdicted)
    return {"level": "ok", "detail": f"{remaining:,} blanks remaining.", "count": remaining}


def _human_ids_or_none(review_dir) -> frozenset[str] | None:
    try:
        return load_human_unit_ids(review_dir)
    except OSError, ValueError, KeyError, TypeError:
        return None


BLOCKING = ("corpus", "freshness", "gates", "snapshot", "verdict_store")


def compute_status(
    repo_root,
    review_dir,
    build_out,
    autosave_path,
    cycle_summary_path,
    *,
    human_ids=None,
    autosave=None,
    recompute_table_diff=None,
    pointer_path=None,
    snapshot_home=None,
) -> dict:
    """Return the readiness dict: `ready` when every check in `BLOCKING` is there and passes. Beside the checks, `orphans` and `conflicts` count the saves the verdict store kept aside in its orphan documents (`verdict_store.summarize_orphans`), with the newest one's `recorded_at` and document and the counts per recent `recorded_at`, so the app can count those kept since it loaded. While an artifact-cycle pass holds its pass lock (var/cycle/pass.lock, `artifact_cycle.pass_lock`), the checks that would name the cycle as their remedy tell the reader to wait for that pass instead. `autosave` is the parsed store when the caller already holds it (serve.py passes its in-memory store, so that each /status request does not read the file again), and None to read `autosave_path`. `recompute_table_diff` gives the corpus's components now, `table_diff_inputs` by default; `pointer_path` is the tracked pointer, by default `engine/review-snapshot.json` under `repo_root`; and `snapshot_home` holds the snapshots, by default `var/keep/review-snapshot` under it. A corpus that is not a table-diff corpus gets no freshness or diff row, and fails the corpus row."""
    repo_root = Path(repo_root)
    review_dir = Path(review_dir)
    manifest = _load_json_dict(review_dir / "manifest.json")
    generated_at = manifest.get("generated_at") if manifest else None
    repo_head = manifest.get("repo_head") if manifest else None
    manifest_fp = manifest.get("inputs_fingerprint") if manifest else None
    if snapshot_home is None:
        snapshot_home = repo_root / SNAPSHOT_HOME
    if pointer_path is None:
        pointer_path = repo_root / POINTER
    running_pass = store_lock.holder(repo_root / "var" / "cycle" / "pass.lock")
    if running_pass is None:
        artifact_cycle_remedy = "make artifact-cycle"
    else:
        artifact_cycle_remedy = f"wait for the running pass (pid {running_pass}) to finish"
    summary = _load_json_dict(cycle_summary_path)
    if human_ids is None:
        human_ids = _human_ids_or_none(review_dir)
    corpus = _corpus_check(manifest)
    table_diff = manifest is not None and corpus["level"] == "ok"
    verdict_store, records = _verdict_store_check(
        autosave_path, generated_at, artifact_cycle_remedy, autosave
    )
    checks: dict[str, dict] = {"corpus": corpus}
    if table_diff:
        checks["freshness"] = _freshness_check(
            manifest,
            manifest_fp,
            repo_root,
            (
                recompute_table_diff
                if recompute_table_diff is not None
                else (lambda root: table_diff_inputs(root, snapshot_home))
            ),
            artifact_cycle_remedy,
            review_dir,
            build_out,
        )
    checks["gates"] = _gates_check(summary, generated_at, manifest_fp, artifact_cycle_remedy)
    checks["snapshot"] = _snapshot_check(pointer_path, snapshot_home)
    checks["verdict_store"] = verdict_store
    checks["blanks"] = _blanks_check(records, human_ids)
    if table_diff:
        checks["diff"] = _diff_check(manifest, records, human_ids, repo_root)
    ready = all(name in checks and checks[name]["level"] != "fail" for name in BLOCKING)
    kept_aside = summarize_orphans(orphans_dir_for(Path(autosave_path)))
    for entry in kept_aside.values():
        if entry["file"] is not None:
            entry["file"] = _rel(repo_root, entry["file"])
    return {
        "ready": ready,
        "corpus": {
            "dir": str(review_dir),
            "generated_at": generated_at,
            "repo_head": repo_head,
            "mode": manifest.get("mode") if manifest else None,
        },
        "checks": checks,
        "orphans": kept_aside["orphan"],
        "conflicts": kept_aside["conflict"],
    }

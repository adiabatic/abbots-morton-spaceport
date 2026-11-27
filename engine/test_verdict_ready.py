"""Tests for `engine.review.status.compute_status`, which builds the readiness dict that the review server's /status handler and the `verdict_ready` CLI render, and for the CLI's server row and checklist. Fixtures build a fake repo tree (a table-diff corpus's manifest and its sidecars, a snapshot and the tracked pointer that names it, the cycle summary and the verdict store) and stub the components' recompute, so no real build inputs are read."""

import gzip
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from engine.review import app_index, status, store_lock
from engine.review.status import compute_status, count_effective
from engine.tools import verdict_ready

STAMP = "2026-07-17T20:24:44Z"
OTHER_STAMP = "2026-07-10T00:00:00Z"
FP = {
    "snapshot": "s",
    "tables": "t",
    "font_inputs": "f",
    "review_code": "r",
    "static": "x",
    "accept_sweep": "a",
}
HUMAN = ["u-1", "u-2", "u-3"]
AFTER_FONT_BYTES = b"OTTO-after"
OTHER_FONT_BYTES = b"OTTO-newer"
GATE_NAMES = ("js", "engine_contracts", "conform", "make_test")


def recompute(_repo):
    return dict(FP)


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def verdict(unit, kind="approve", at="2026-07-17T21:00:00Z"):
    return {"unit": unit, "verdict": kind, "note": "", "at": at}


def verdicts_doc(stamp, records):
    return {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": list(records),
    }


def write_snapshot(repo: Path, *, current: bool = True) -> str:
    """Write a snapshot `snapshot.load` reads, with no configuration, under the repo's snapshot home, point `current` at it when asked, and return its digest."""
    from engine.review import snapshot

    home = repo / status.SNAPSHOT_HOME
    staging = home / "staging"
    staging.mkdir(parents=True)
    (staging / snapshot.FONT_NAME).write_bytes(b"OTTO-snapshot")
    (staging / snapshot.KERN_NAME).write_bytes(gzip.compress(b"# kern table\n", mtime=0))
    record = {
        "format": snapshot.SNAPSHOT_FORMAT,
        "digest": None,
        "taken_at": "2026-11-26T00:00:00Z",
        "tables_stamp": "t",
        "configs": [],
        "source": "engine/out/build",
        "files": {
            path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
            for path in sorted(staging.iterdir())
        },
    }
    record["digest"] = snapshot._digest(record)
    (staging / snapshot.RECORD_NAME).write_text(json.dumps(record), encoding="utf-8")
    final = home / record["digest"][: snapshot.DIGEST_NAME_LENGTH]
    staging.rename(final)
    if current:
        (home / snapshot.CURRENT_NAME).write_text(final.name + "\n", encoding="utf-8")
    return record["digest"]


def write_pointer(repo: Path, digest: str, snapshot_format: str | None = None) -> None:
    from engine.review import snapshot

    _write(
        repo / status.POINTER,
        {
            "format": snapshot.POINTER_FORMAT,
            "snapshot_format": snapshot_format or snapshot.SNAPSHOT_FORMAT,
            "digest": digest,
            "taken_at": "2026-11-26T00:00:00Z",
        },
    )


def write_corpus(
    repo: Path,
    digest: str | None,
    *,
    units: int = 7,
    human=HUMAN,
    mode: str = "table-diff",
    generated_at: str = STAMP,
    inputs_fp: str | Mapping[str, str | None] = "fresh",
    after_font: str = "present",
) -> None:
    """A table-diff corpus of `units` units: `human` in `joins`, two more machine-approved there, and the rest in `kerning`, which takes no verdict, with both app sidecars stamped for the manifest, as a finished build leaves them. `after_font` sets how the manifest's after-font record relates to the `Senior-Regular.otf` written beside it: "present" records that font's sha256, "moved" records the sha256 of other bytes (as after a `run_build` that ran after the corpus build), and "omit" writes no `fonts` block."""

    review_dir = repo / "engine" / "out" / "review"
    none = units - len(human) - 2 if units else 0
    classes = (
        [
            {
                "id": "joins",
                "shards": ["units/joins.json"],
                "status": None,
                "section": "open-questions",
                "no_verdict": False,
                "unit_count": len(human) + 2,
            },
            {
                "id": "kerning",
                "shards": ["units/kerning.json"],
                "status": None,
                "section": "no-review-needed",
                "no_verdict": True,
                "unit_count": none,
            },
        ]
        if units
        else []
    )
    manifest: dict[str, object] = {
        "format": "ams-review-manifest/2",
        "mode": mode,
        "generated_at": generated_at,
        "repo_head": "abc1234",
        "classes": classes,
        "human_unit_ids": list(human) if units else [],
        "totals": {"units": units, "rows": units, "batches": 1 if units else 0},
        "baseline": {
            "commit": None,
            "taken_at": "2026-11-26T00:00:00Z",
            "digest": digest,
            "present": digest is not None,
        },
    }
    if inputs_fp == "fresh":
        manifest["inputs_fingerprint"] = dict(FP)
    elif inputs_fp != "omit":
        manifest["inputs_fingerprint"] = inputs_fp
    if after_font != "omit":
        manifest["fonts"] = {
            "after": {"file": "fonts/after.otf", "sha256": hashlib.sha256(AFTER_FONT_BYTES).hexdigest()}
        }
    font = repo / "engine" / "out" / "build" / "Senior-Regular.otf"
    font.parent.mkdir(parents=True, exist_ok=True)
    font.write_bytes(AFTER_FONT_BYTES if after_font != "moved" else OTHER_FONT_BYTES)
    _write(review_dir / "manifest.json", manifest)
    for meta in classes:
        (review_dir / meta["shards"][0]).parent.mkdir(parents=True, exist_ok=True)
        (review_dir / meta["shards"][0]).write_text("[]")
    app_index.write_app_artifacts(review_dir, {}, {})


def write_summary(
    repo: Path,
    *,
    generated_at: str = STAMP,
    inputs_fp: str | Mapping[str, str | None] = "match",
    exit_: str = "ok",
    gates: str | Mapping[str, Mapping[str, object]] = "green",
) -> None:
    fp = dict(FP) if inputs_fp == "match" else inputs_fp
    gate_map = (
        {name: {"status": "passed", "green": True} for name in GATE_NAMES} if gates == "green" else gates
    )
    _write(
        repo / "engine" / "out" / "cycle_summary.json",
        {
            "format": "ams-cycle-summary/1",
            "finished_at": "2026-07-17T21:30:00Z",
            "exit": exit_,
            "gates": gate_map,
            "corpus": {"dir": "engine/out/review", "generated_at": generated_at, "inputs_fingerprint": fp},
        },
    )


def write_autosave(repo, *, stamp=STAMP, records=None):
    if records is None:
        records = [verdict(unit) for unit in HUMAN]
    _write(repo / "verdicts-autosave.json", verdicts_doc(stamp, records))


def setup(repo: Path, *, units: int = 7, records=None, **corpus) -> str:
    """A green checkout: a current snapshot the pointer names, a table-diff corpus built against it, a green cycle summary for that corpus, and a store stamped for it holding `records`, by default an approve on every human unit. Returns the snapshot's digest."""
    digest = write_snapshot(repo)
    write_pointer(repo, digest)
    write_corpus(repo, digest, units=units, **corpus)
    write_summary(repo)
    write_autosave(repo, records=records)
    return digest


def call(repo, **kwargs):
    kwargs.setdefault("recompute_table_diff", recompute)
    return compute_status(
        repo,
        repo / "engine" / "out" / "review",
        repo / "engine" / "out" / "build",
        repo / "verdicts-autosave.json",
        repo / "engine" / "out" / "cycle_summary.json",
        **kwargs,
    )


def _readiness(repo, **kwargs):
    kwargs.setdefault("other_root", lambda root: None)
    kwargs.setdefault("recompute_table_diff", recompute)
    return verdict_ready.readiness(
        repo_root=repo,
        review_dir=repo / "engine" / "out" / "review",
        build_out=repo / "engine" / "out" / "build",
        autosave_path=repo / "verdicts-autosave.json",
        cycle_summary_path=repo / "engine" / "out" / "cycle_summary.json",
        **kwargs,
    )


def _gate_map(**overrides):
    base = {name: {"status": "passed", "green": True, "skip": None} for name in GATE_NAMES}
    base.update(overrides)
    return base


# --- the corpus row ---------------------------------------------------------------------


def test_a_missing_manifest_fails_the_corpus_row_and_never_raises(tmp_path):
    (tmp_path / "engine" / "out" / "review").mkdir(parents=True)
    write_summary(tmp_path)
    result = call(tmp_path)
    assert result["ready"] is False
    assert result["checks"]["corpus"]["level"] == "fail"
    assert result["checks"]["corpus"]["remedy"] == "make artifact-cycle"
    assert "freshness" not in result["checks"] and "diff" not in result["checks"]
    assert result["checks"]["blanks"]["count"] is None


def test_a_corpus_that_is_not_a_table_diff_corpus_fails_and_gets_no_freshness_or_diff_row(tmp_path):
    setup(tmp_path, mode="another-mode")
    result = call(tmp_path)
    corpus = result["checks"]["corpus"]
    assert (
        corpus["level"] == "fail" and "the mode 'another-mode', not the table-diff corpus" in corpus["detail"]
    )
    assert corpus["remedy"] == "make artifact-cycle"
    assert "freshness" not in result["checks"] and "diff" not in result["checks"]
    assert result["ready"] is False


def test_ready_only_when_every_blocking_row_passes(tmp_path):
    setup(tmp_path)
    result = call(tmp_path)
    assert result["ready"] is True
    for name in status.BLOCKING:
        assert result["checks"][name]["level"] == "ok", name
    assert result["corpus"] == {
        "dir": str(tmp_path / "engine" / "out" / "review"),
        "generated_at": STAMP,
        "repo_head": "abc1234",
        "mode": "table-diff",
    }


# --- the freshness row ------------------------------------------------------------------


def test_a_manifest_without_an_input_fingerprint_fails_all_unknown(tmp_path):
    setup(tmp_path, inputs_fp="omit")
    freshness = call(tmp_path)["checks"]["freshness"]
    assert freshness["level"] == "fail"
    assert set(freshness["components"].values()) == {"unknown"}
    assert freshness["remedy"] == "make artifact-cycle"


def test_a_moved_component_fails_and_a_running_pass_replaces_the_remedy(tmp_path):
    setup(tmp_path)
    moved = lambda _repo: {**FP, "font_inputs": "kern edit"}  # noqa: E731
    freshness = call(tmp_path, recompute_table_diff=moved)["checks"]["freshness"]
    assert freshness["level"] == "fail" and freshness["components"]["font_inputs"] == "stale"
    assert "font_inputs" in freshness["detail"] and freshness["remedy"] == "make artifact-cycle"
    with store_lock.hold_flock(tmp_path / "var" / "cycle" / "pass.lock") as fd:
        os.pwrite(fd, b"4242\n", 0)
        freshness = call(tmp_path, recompute_table_diff=moved)["checks"]["freshness"]
    assert freshness["remedy"] == "wait for the running pass (pid 4242) to finish"


def test_a_corpus_with_no_snapshot_is_fresh_while_the_sources_give_none_either(tmp_path):
    setup(tmp_path, inputs_fp={**FP, "snapshot": None})
    freshness = call(tmp_path, recompute_table_diff=lambda _repo: {**FP, "snapshot": None})["checks"][
        "freshness"
    ]
    assert freshness["level"] == "ok" and freshness["components"]["snapshot"] == "fresh"


def test_static_only_stale_warns_and_points_at_the_cycle(tmp_path):
    """`static` is the only component whose staleness warns instead of failing: the cycle copies the new assets over the served corpus and keeps the server running."""
    setup(tmp_path)
    result = call(tmp_path, recompute_table_diff=lambda _repo: {**FP, "static": "new"})
    freshness = result["checks"]["freshness"]
    assert freshness["level"] == "warn" and freshness["components"]["static"] == "stale"
    assert freshness["remedy"] == "make artifact-cycle"
    assert result["ready"] is True


@pytest.mark.parametrize("after_font", ["moved", "omit"])
def test_freshness_fails_unless_the_after_font_is_the_built_font_on_disk(tmp_path, after_font):
    """The components do not hash Senior-Regular.otf itself, so a `run_build` after the corpus build leaves every component fresh while the corpus serves the previous build's letters; comparing the manifest's after-font sha256 with the font on disk catches that, and a corpus that records no sha256 cannot be checked."""
    setup(tmp_path, after_font=after_font)
    result = call(tmp_path)
    freshness = result["checks"]["freshness"]
    assert freshness["level"] == "fail" and "engine/out/build/Senior-Regular.otf" in freshness["detail"]
    assert set(freshness["components"].values()) == {"fresh"}
    assert result["ready"] is False


def test_freshness_fails_when_a_sidecar_is_missing(tmp_path):
    """Both app sidecars are written after the manifest and are not part of it, so a build killed between them, or a manifest rewritten without them, leaves a corpus whose fingerprint reads fresh while the app loads files that describe another corpus; the check names the file, as the cycle's `corpus_complete` does through the same `app_index.outputs_not_current`."""
    setup(tmp_path)
    app_index.artifact_path(tmp_path / "engine" / "out" / "review", app_index.APP_INDEX_NAME).unlink()
    result = call(tmp_path)
    freshness = result["checks"]["freshness"]
    assert freshness["level"] == "fail" and app_index.APP_INDEX_NAME in freshness["detail"]
    assert result["ready"] is False


# --- the gates row ----------------------------------------------------------------------


def test_the_gates_row_fails_without_a_summary_for_the_served_corpus(tmp_path):
    setup(tmp_path)
    (tmp_path / "engine" / "out" / "cycle_summary.json").unlink()
    assert "no recorded" in call(tmp_path)["checks"]["gates"]["detail"].lower()
    write_summary(tmp_path, generated_at=OTHER_STAMP)
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail" and "different corpus" in gates["detail"]
    write_summary(tmp_path, inputs_fp={**FP, "tables": "X"})
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail" and "different corpus" in gates["detail"]


def test_the_gates_row_names_the_failing_gates_of_a_failed_pass(tmp_path):
    setup(tmp_path)
    write_summary(
        tmp_path, exit_="failed", gates=_gate_map(engine_contracts={"status": "failed", "green": False})
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail" and "engine_contracts" in gates["detail"]


@pytest.mark.parametrize(
    "gates, level, words",
    [
        (_gate_map(), "ok", "finished green"),
        (
            _gate_map(
                conform={"status": "skipped (inputs unchanged)", "green": False, "skip": "proved"},
                make_test={"status": "skipped (closure unchanged)", "green": False, "skip": "proved"},
            ),
            "ok",
            "already proved",
        ),
        (
            _gate_map(conform={"status": "skipped (--skip-conform)", "green": False, "skip": "forced"}),
            "fail",
            "unverified: conform",
        ),
        (
            {name: {"status": "not run", "green": False, "skip": None} for name in GATE_NAMES},
            "fail",
            "unverified",
        ),
        (
            _gate_map(
                engine_contracts={"status": "FAILED (exit 1)", "green": False, "skip": None},
                conform={"status": "skipped (--skip-conform)", "green": False, "skip": "forced"},
            ),
            "fail",
            "failing gates: engine_contracts",
        ),
    ],
    ids=["green", "proved-skips", "forced-skip", "not-run", "failing-beside-forced"],
)
def test_the_gates_row_reads_each_gates_skip_field(tmp_path, gates, level, words):
    """A proved skip counts toward readiness; a forced skip or a gate that did not run blocks as unverified, and anything else as failing. The status string cannot make the distinction, since every skip kind's status starts with "skipped (", so reading it would let a `--skip-conform` pass report READY."""
    setup(tmp_path)
    write_summary(tmp_path, gates=gates)
    row = call(tmp_path)["checks"]["gates"]
    assert row["level"] == level and words in row["detail"]
    assert row["remedy"] == (None if level == "ok" else "make artifact-cycle")


# --- the snapshot row -------------------------------------------------------------------


@pytest.mark.parametrize("case", ["no-current", "another", "no-pointer", "other-format"])
def test_the_snapshot_row_fails_unless_the_current_snapshot_is_the_one_the_pointer_names(tmp_path, case):
    from engine.review import snapshot

    digest = setup(tmp_path)
    home = tmp_path / status.SNAPSHOT_HOME
    if case == "no-current":
        (home / snapshot.CURRENT_NAME).unlink()
    elif case == "another":
        write_pointer(tmp_path, "0" * 64)
    elif case == "no-pointer":
        (tmp_path / status.POINTER).unlink()
    else:
        write_pointer(tmp_path, digest, snapshot_format="ams-review-snapshot/1")
    result, ready = _readiness(tmp_path, listening=lambda: True)
    row = result["checks"]["snapshot"]
    assert row["level"] == "fail" and result["ready"] is False and ready is False
    expected = (
        "make review-snapshot-remake"
        if case in ("no-current", "another")
        else "make review-accept ARGS='--apply --retake'"
    )
    assert row["remedy"] == expected


# --- the verdict-store and blanks rows --------------------------------------------------


def test_a_missing_store_warns_with_no_remedy_and_no_blank_count(tmp_path):
    setup(tmp_path)
    (tmp_path / "verdicts-autosave.json").unlink()
    result = call(tmp_path)
    store = result["checks"]["verdict_store"]
    assert (store["level"], store["remedy"]) == ("warn", None) and "no autosave" in store["detail"].lower()
    assert result["checks"]["blanks"]["count"] is None and result["ready"] is True


def test_a_store_stamped_for_another_corpus_points_at_the_cycle(tmp_path):
    """The land carries the store onto the corpus it lands, so a store stamped for another corpus waits for the next pass."""
    setup(tmp_path)
    write_autosave(tmp_path, stamp=OTHER_STAMP)
    result = call(tmp_path)
    store = result["checks"]["verdict_store"]
    assert (store["level"], store["remedy"]) == ("fail", "make artifact-cycle")
    assert result["checks"]["blanks"]["count"] is None and result["ready"] is False


@pytest.mark.parametrize(
    "record, where",
    [
        ({**verdict("u-2"), "at": 5}, "on u-2"),
        ({"verdict": "approve", "at": "2026-07-17T21:00:00Z"}, "at index 1"),
    ],
    ids=["numeric-at", "no-unit"],
)
@pytest.mark.parametrize("stamp", [STAMP, OTHER_STAMP], ids=["aligned", "stamped-elsewhere"])
def test_a_store_holding_a_record_it_refuses_fails_naming_it(tmp_path, record, where, stamp):
    """The review server serves no verdict from such a store and refuses every save (`VerdictStore.reload`), so the readiness check fails on it, naming the record, instead of counting its verdicts, and writes nothing."""
    setup(tmp_path)
    write_autosave(tmp_path, stamp=stamp, records=[verdict("u-1"), record])
    stored = (tmp_path / "verdicts-autosave.json").read_bytes()
    result = call(tmp_path)
    store = result["checks"]["verdict_store"]
    assert store["level"] == "fail" and not result["ready"]
    assert f"verdicts-autosave.json holds a verdict record {where}" in store["detail"]
    assert "Replace the store with a readable one" in store["remedy"]
    assert result["checks"]["blanks"]["count"] is None
    assert (tmp_path / "verdicts-autosave.json").read_bytes() == stored


def test_the_store_row_counts_its_effective_verdicts_and_the_blanks_count_a_skip_as_blank(tmp_path):
    setup(tmp_path, records=[verdict("u-1"), verdict("u-2", "skip"), verdict("u-9")])
    result = call(tmp_path)
    assert result["checks"]["verdict_store"]["detail"] == (
        "The autosave is aligned with this corpus and holds 2 effective verdicts."
    )
    blanks = result["checks"]["blanks"]
    assert (blanks["level"], blanks["count"], blanks["detail"]) == ("ok", 2, "2 blanks remaining.")
    assert call(tmp_path, human_ids=frozenset({"u-1", "u-2"}))["checks"]["blanks"]["count"] == 1


def test_the_latest_record_per_unit_is_the_one_the_rows_read(tmp_path):
    setup(
        tmp_path,
        records=[
            verdict("u-1", "reject", at="2026-07-17T22:00:00Z"),
            verdict("u-1", "skip", at="2026-07-17T21:00:00Z"),
        ],
    )
    result = call(tmp_path)
    assert result["checks"]["blanks"]["count"] == 2 and result["checks"]["diff"]["rejected"] == 1


def test_count_effective_ignores_skip():
    records = {"u-1": verdict("u-1"), "u-2": verdict("u-2", "skip"), "u-3": verdict("u-3", "neither")}
    assert count_effective(records) == 2


def test_the_saves_the_store_kept_aside_are_counted_by_reason(tmp_path):
    from engine.review.verdict_store import append_orphans, orphans_dir_for

    setup(tmp_path)
    directory = orphans_dir_for(tmp_path / "verdicts-autosave.json")
    append_orphans(directory, OTHER_STAMP, [{**verdict("u-8"), "reason": "orphan"}])
    append_orphans(directory, STAMP, [{**verdict("u-1", "reject"), "reason": "conflict"}])
    result = call(tmp_path)
    assert (result["orphans"]["count"], result["conflicts"]["count"]) == (1, 1)
    assert result["orphans"]["file"].startswith("var/verdict-orphans/")


# --- the diff row and the checklist -----------------------------------------------------


def test_the_checklist_prints_the_rows_in_their_order(tmp_path):
    setup(tmp_path)
    result, ready = _readiness(tmp_path, listening=lambda: True)
    assert list(result["checks"]) == list(verdict_ready.CHECK_ORDER)
    lines = verdict_ready.checklist(result, ready)
    rows = [line[4:].split(":")[0] for line in lines if line[2:3] in ("✓", "!", "✗")]
    assert rows == list(verdict_ready.CHECK_ORDER)
    assert lines[-1] == f"READY - adjudicate at {verdict_ready.QUEUE_URL}"


def test_the_diff_row_counts_each_kind_of_change_and_names_the_accept_once_every_unit_is_accepted(tmp_path):
    setup(tmp_path, records=[verdict("u-1"), verdict("u-2", "reject"), verdict("u-3", "skip")])
    result, _ready = _readiness(tmp_path, listening=lambda: True)
    diff = result["checks"]["diff"]
    assert diff["detail"] == (
        "7 unaccepted changes since the snapshot taken at 2026-11-26T00:00:00Z: 3 need a verdict "
        "(1 blank, 1 rejected), 2 machine-approved, 2 need none."
    )
    assert (diff["level"], diff["remedy"]) == ("ok", None)
    assert verdict_ready.diff_summary_line(result) == (
        "review diff: 7 unaccepted changes since the snapshot taken at 2026-11-26T00:00:00Z (3 need a verdict)"
    )
    write_autosave(tmp_path, records=[verdict(unit, "either") for unit in HUMAN])
    result, ready = _readiness(tmp_path, listening=lambda: True)
    diff = result["checks"]["diff"]
    assert (diff["level"], diff["remedy"], diff["blank"], diff["rejected"]) == (
        "warn",
        "make review-accept ARGS=--apply",
        0,
        0,
    )
    assert ready is True
    assert "      remedy: make review-accept ARGS=--apply" in verdict_ready.checklist(result, ready)


def test_a_corpus_with_no_change_says_so_on_its_ready_line(tmp_path):
    setup(tmp_path, units=0, records=[])
    result, ready = _readiness(tmp_path, listening=lambda: True)
    assert ready is True
    assert result["checks"]["diff"]["count"] == 0 and result["checks"]["diff"]["level"] == "ok"
    assert verdict_ready.checklist(result, ready)[-1] == (
        "READY - no unaccepted changes since the snapshot taken at 2026-11-26T00:00:00Z"
    )


# --- the server row ---------------------------------------------------------------------


def test_readiness_adds_the_server_row_and_gates_ready_on_it(tmp_path):
    setup(tmp_path)
    result, ready = _readiness(tmp_path, listening=lambda: False)
    assert ready is False
    assert result["ready"] is True
    assert result["checks"]["server"]["level"] == "fail"
    assert result["checks"]["server"]["remedy"] == "make review-serve"
    lines = verdict_ready.checklist(result, ready)
    assert "  ✗ server: not listening on port 7294" in lines
    assert "      remedy: make review-serve" in lines
    assert lines[-1] == "NOT READY"

    result, ready = _readiness(tmp_path, listening=lambda: True)
    assert ready is True
    lines = verdict_ready.checklist(result, ready)
    assert "  ✓ server: listening on port 7294" in lines
    assert lines[-1] == f"READY - adjudicate at {verdict_ready.QUEUE_URL}"


def test_readiness_fails_the_server_row_for_a_server_that_serves_another_checkout(tmp_path):
    setup(tmp_path)
    result, ready = _readiness(tmp_path, listening=lambda: True, other_root=lambda root: "/elsewhere")
    assert ready is False
    assert result["checks"]["server"]["level"] == "fail"
    lines = verdict_ready.checklist(result, ready)
    assert "  ✗ server: a review server for another checkout (/elsewhere) holds port 7294" in lines
    assert "      remedy: stop that checkout's review server, then make review-serve" in lines
    assert lines[-1] == "NOT READY"


def test_readiness_without_the_server_row_answers_for_the_corpus_alone(tmp_path):
    """Under `make review-cycle` the Makefile recipe handles the server after the cycle, so the cycle calls `readiness` with `with_server=False`. There is no server row, and READY depends on the corpus alone."""
    setup(tmp_path)
    result, ready = _readiness(tmp_path, with_server=False, listening=lambda: False)
    assert ready is True
    assert "server" not in result["checks"]
    lines = verdict_ready.checklist(result, ready)
    assert not any("server" in line for line in lines)
    assert lines[-1] == f"READY - adjudicate at {verdict_ready.QUEUE_URL}"

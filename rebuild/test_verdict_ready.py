"""Tests for `rebuild.review.status.compute_status` and its helpers, which build the readiness dict that the review server's /status handler and the `verdict_ready` CLI render. Fixtures build a fake repo tree (surface manifest and small shards, cycle summary, autosave, repo-root verdicts files) and stub the fingerprint recompute, so no real build inputs are read."""

import hashlib
import json
import os
import time
import weakref
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import pytest

from rebuild.review import app_index, status, unit_index
from rebuild.review.status import (
    compute_status,
    count_effective,
    latest_verdicts,
    pick_frontier,
    resolve_carry_source,
)
from rebuild.review.verdict_store import parse_autosave_payload
from rebuild.tools import verdict_ready

STAMP = "2026-07-17T20:24:44Z"
OTHER_STAMP = "2026-07-10T00:00:00Z"
FP = {
    "data": "d",
    "baselines": "b",
    "pipeline_code": "p",
    "review_code": "r",
    "static": "s",
    "fonts": "f",
    "explain_prose": "e",
}

DEFAULT_CLASSES = [
    {"id": "class-a", "shards": ["units/class-a.json"], "status": "reviewed-approved"},
    {"id": "class-b", "shards": ["units/class-b.json"], "status": "intended"},
]
CLASS_A_UNITS = [{"id": "u-1", "batch": 1}, {"id": "u-2", "batch": 2}, {"id": "m-1", "batch": None}]
CLASS_B_UNITS = [{"id": "u-3", "batch": 1}]
HUMAN_ID_LIST = ["u-1", "u-2", "u-3"]
HUMAN_IDS = frozenset(HUMAN_ID_LIST)
AFTER_FONT_BYTES = b"OTTO-after"
OTHER_FONT_BYTES = b"OTTO-newer"


def recompute(_repo):
    return dict(FP)


@pytest.fixture(autouse=True)
def _empty_frontier_memo(monkeypatch):
    """`status._MEMO` is module state that persists across tests in an xdist worker, so each test starts with an empty memo and its parse counts are not affected by entries an earlier test left."""
    monkeypatch.setattr(status, "_MEMO", {})


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _write_raw(path, text, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))


def verdict(unit, kind="approve", at="2026-07-17T21:00:00Z"):
    return {"unit": unit, "verdict": kind, "note": "", "at": at}


def verdicts_doc(stamp, records):
    return {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": list(records),
    }


def write_surface(
    review_dir: Path,
    *,
    generated_at: str = STAMP,
    repo_head: str = "abc1234",
    inputs_fp: str | Mapping[str, str | None] = "fresh",
    shards: bool = True,
    human_ids: list[str] | None = HUMAN_ID_LIST,
    after_font: str = "present",
    sidecars: bool = True,
) -> None:
    """`after_font` sets how the manifest's after-font record relates to the `M1.otf` written beside it: "present" records that font's sha256, "moved" records the sha256 of different bytes (as after a `run_m1` that ran after the surface build), and "omit" writes no `fonts` block. `sidecars` writes the per-unit index and both app sidecars stamped for the manifest, as a finished build leaves them; a test that needs one missing deletes it."""
    manifest: dict[str, object] = {
        "format": "ams-review-manifest/2",
        "generated_at": generated_at,
        "repo_head": repo_head,
        "classes": DEFAULT_CLASSES,
    }
    if human_ids is not None:
        manifest["human_unit_ids"] = human_ids
    if inputs_fp == "fresh":
        manifest["inputs_fingerprint"] = dict(FP)
    elif inputs_fp != "omit":
        manifest["inputs_fingerprint"] = inputs_fp
    font = review_dir.parent / "m1" / "M1.otf"
    font.parent.mkdir(parents=True, exist_ok=True)
    font.write_bytes(AFTER_FONT_BYTES if after_font != "moved" else OTHER_FONT_BYTES)
    if after_font != "omit":
        manifest["fonts"] = {
            "after": {
                "file": "fonts/after.otf",
                "sha256": hashlib.sha256(AFTER_FONT_BYTES).hexdigest(),
            }
        }
    _write(review_dir / "manifest.json", manifest)
    if shards:
        _write(review_dir / "units" / "class-a.json", CLASS_A_UNITS)
        _write(review_dir / "units" / "class-b.json", CLASS_B_UNITS)
    if sidecars:
        unit_index.write_index(review_dir, [])
        app_index.write_app_artifacts(review_dir, {}, {})


def write_summary(
    repo: Path,
    *,
    generated_at: str = STAMP,
    inputs_fp: str | Mapping[str, str | None] = "match",
    exit_: str = "ok",
    gates: str | Mapping[str, Mapping[str, object]] = "green",
    carry_out: str = "rebuild/evidence/carried.json",
) -> None:
    fp = dict(FP) if inputs_fp == "match" else inputs_fp
    if gates == "green":
        gate_map = {
            "js": {"status": "passed", "green": True},
            "rebuild": {"status": "passed", "green": True},
            "conform": {"status": "passed", "green": True},
            "make_test": {"status": "passed", "green": True},
        }
    else:
        gate_map = gates
    _write(
        repo / "rebuild" / "out" / "cycle_summary.json",
        {
            "format": "ams-cycle-summary/1",
            "finished_at": "2026-07-17T21:30:00Z",
            "exit": exit_,
            "gates": gate_map,
            "carry_out": carry_out,
            "surface": {"dir": "rebuild/out/review", "generated_at": generated_at, "inputs_fingerprint": fp},
        },
    )


def write_autosave(repo, *, stamp=STAMP, records=None):
    if records is None:
        records = [verdict("u-1", "approve")]
    _write(repo / "verdicts-autosave.json", verdicts_doc(stamp, records))


def call(repo, **kwargs):
    kwargs.setdefault("recompute", recompute)
    return compute_status(
        repo,
        repo / "rebuild" / "out" / "review",
        repo / "rebuild" / "out" / "m1",
        repo / "verdicts-autosave.json",
        repo / "rebuild" / "out" / "cycle_summary.json",
        **kwargs,
    )


def setup_green(repo):
    write_surface(repo / "rebuild" / "out" / "review")
    write_summary(repo)
    write_autosave(repo)


def test_manifest_missing(tmp_path):
    write_summary(tmp_path)
    result = call(tmp_path)
    assert result["checks"]["surface"]["level"] == "fail"
    assert result["checks"]["surface"]["remedy"] == "uv run python -m rebuild.review.build"
    assert result["checks"]["freshness"]["level"] == "fail"
    assert set(result["checks"]["freshness"]["components"].values()) == {"unknown"}
    assert result["ready"] is False


def test_pre_fingerprint_manifest_fails_all_unknown(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review", inputs_fp="omit")
    write_summary(tmp_path)
    write_autosave(tmp_path)
    freshness = call(tmp_path)["checks"]["freshness"]
    assert freshness["level"] == "fail"
    assert set(freshness["components"].values()) == {"unknown"}
    assert freshness["remedy"] == "make artifact-cycle"


def test_data_stale_fails_with_artifact_cycle_remedy(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review", inputs_fp={**FP, "data": "OLD"})
    write_summary(tmp_path, inputs_fp={**FP, "data": "OLD"})
    write_autosave(tmp_path)
    freshness = call(tmp_path)["checks"]["freshness"]
    assert freshness["level"] == "fail"
    assert freshness["components"]["data"] == "stale"
    assert freshness["components"]["static"] == "fresh"
    assert freshness["remedy"] == "make artifact-cycle"
    assert "data" in freshness["detail"]


def test_explain_prose_stale_fails_like_any_other_hard_component(tmp_path):
    """A stale `explain_prose` fails, as every component except `static` does. The surface shows the refuse records' `why` in its explain text and the ledger classes' `why` as class rationales, so a surface stamped before a rewording shows text the runes or the ledger no longer contain."""
    write_surface(tmp_path / "rebuild" / "out" / "review", inputs_fp={**FP, "explain_prose": "OLD"})
    write_summary(tmp_path, inputs_fp={**FP, "explain_prose": "OLD"})
    write_autosave(tmp_path)
    freshness = call(tmp_path)["checks"]["freshness"]
    assert freshness["level"] == "fail"
    assert freshness["components"]["explain_prose"] == "stale"
    assert freshness["components"]["static"] == "fresh"
    assert freshness["remedy"] == "make artifact-cycle"
    assert "explain_prose" in freshness["detail"]


def test_static_only_stale_warns_and_points_at_the_cycle(tmp_path):
    """`static` is the only component whose staleness warns instead of failing. The remedy is the cycle, which copies the new assets over the served surface and keeps the server running; `make review-build` would rebuild every unit and leave the recorded cycle stamped for a surface that no longer exists."""
    write_surface(tmp_path / "rebuild" / "out" / "review", inputs_fp={**FP, "static": "OLD"})
    write_summary(tmp_path, inputs_fp={**FP, "static": "OLD"})
    write_autosave(tmp_path)
    freshness = call(tmp_path)["checks"]["freshness"]
    assert freshness["level"] == "warn"
    assert freshness["components"]["static"] == "stale"
    assert freshness["remedy"] == "make artifact-cycle"


def test_freshness_fails_when_the_after_font_moved(tmp_path):
    """The fingerprint hashes M1.otf's inputs and the two site fonts but not M1.otf itself, so a `run_m1` after the surface build leaves every component fresh while the surface serves the previous build's letters. The build checks the manifest's after-font sha256 against the bytes it copies, so comparing that sha256 with the font on disk catches the change."""
    setup_green(tmp_path)
    (tmp_path / "rebuild" / "out" / "m1" / "M1.otf").write_bytes(OTHER_FONT_BYTES)
    result = call(tmp_path)
    freshness = result["checks"]["freshness"]
    assert freshness["level"] == "fail"
    assert "M1.otf" in freshness["detail"]
    assert freshness["remedy"] == "make artifact-cycle"
    assert set(freshness["components"].values()) == {"fresh"}
    assert result["ready"] is False


def test_freshness_fails_when_a_sidecar_is_missing(tmp_path):
    """The per-unit index and both app sidecars are written after the manifest and are not part of it. A build killed between the two, or a manifest rewritten without them, leaves a surface whose fingerprint reads fresh while the app loads files that describe another surface."""
    setup_green(tmp_path)
    review_dir = tmp_path / "rebuild" / "out" / "review"
    unit_index.index_path(review_dir).unlink()
    result = call(tmp_path)
    freshness = result["checks"]["freshness"]
    assert freshness["level"] == "fail"
    assert unit_index.INDEX_NAME in freshness["detail"]
    assert freshness["remedy"] == "make artifact-cycle"
    assert result["ready"] is False


def test_freshness_fails_when_the_font_record_is_absent(tmp_path):
    """A surface that records no after-font sha256 cannot be checked, and a surface that cannot be checked is not ready."""
    write_surface(tmp_path / "rebuild" / "out" / "review", after_font="omit")
    write_summary(tmp_path)
    write_autosave(tmp_path)
    result = call(tmp_path)
    freshness = result["checks"]["freshness"]
    assert freshness["level"] == "fail"
    assert "rebuild/out/m1/M1.otf" in freshness["detail"]
    assert result["ready"] is False


def test_gates_summary_missing(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_autosave(tmp_path)
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "no recorded" in gates["detail"].lower()


def test_gates_stamp_mismatch(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(tmp_path, generated_at=OTHER_STAMP)
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "different surface" in gates["detail"]


def test_gates_fingerprint_mismatch(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(tmp_path, inputs_fp={**FP, "data": "X"})
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "different surface" in gates["detail"]


def test_gates_exit_failed(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(
        tmp_path,
        exit_="failed",
        gates={
            "js": {"status": "passed", "green": True},
            "rebuild": {"status": "failed", "green": False},
            "conform": {"status": "passed", "green": True},
            "make_test": {"status": "passed", "green": True},
        },
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "rebuild" in gates["detail"]


GATE_NAMES = ("js", "rebuild", "conform", "make_test")


def _gate_map(**overrides):
    base = {name: {"status": "passed", "green": True, "skip": None} for name in GATE_NAMES}
    base.update(overrides)
    return base


def test_gates_forced_skip_fails(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(
        tmp_path,
        gates=_gate_map(conform={"status": "skipped (--skip-conform)", "green": False, "skip": "forced"}),
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "unverified" in gates["detail"]
    assert "conform" in gates["detail"]


def test_gates_not_run_fails_as_unverified(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(
        tmp_path,
        gates={name: {"status": "not run", "green": False, "skip": None} for name in GATE_NAMES},
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "unverified" in gates["detail"]
    assert "failing gates" not in gates["detail"]


def test_gates_proved_skip_is_ready(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(
        tmp_path,
        gates=_gate_map(
            conform={"status": "skipped (inputs unchanged)", "green": False, "skip": "proved"},
            make_test={"status": "skipped (closure unchanged)", "green": False, "skip": "proved"},
        ),
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "ok"


def test_gates_real_failure_named_as_failing_not_unverified(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(
        tmp_path,
        gates=_gate_map(
            rebuild={"status": "FAILED (exit 1)", "green": False, "skip": None},
            conform={"status": "skipped (--skip-conform)", "green": False, "skip": "forced"},
        ),
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "failing gates: rebuild" in gates["detail"]


def test_gates_an_unverified_conform_blocks_readiness(tmp_path):
    """The readiness check reads each gate entry's fields without special-casing any gate, so conform is judged like the others: a skip that is not proved blocks as unverified, a failure blocks as failing, and only a pass or a proved skip is ready."""
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(
        tmp_path,
        gates=_gate_map(
            conform={"status": "skipped (--skip-conform)", "green": False, "skip": "forced"},
        ),
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "unverified: conform" in gates["detail"]
    assert gates["remedy"] == "make artifact-cycle"

    write_summary(
        tmp_path,
        gates=_gate_map(
            conform={"status": "FAILED", "green": False, "skip": None},
        ),
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "failing gates: conform" in gates["detail"]

    write_summary(
        tmp_path,
        gates=_gate_map(
            conform={
                "status": "skipped (font and sweep inputs unchanged)",
                "green": False,
                "skip": "proved",
            },
        ),
    )
    assert call(tmp_path)["checks"]["gates"]["level"] == "ok"


def test_gates_a_legacy_deferred_skip_reads_as_unverified(tmp_path):
    """Older cycle summaries can record a gate as `deferred`. A deferred gate did not run, so it reads as unverified like any other skip that is not proved, with `make artifact-cycle` as the remedy."""
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(
        tmp_path,
        gates=_gate_map(
            rebuild={
                "status": "deferred (rebuild waits for the next pass)",
                "green": False,
                "skip": "deferred",
            },
            make_test={"status": "skipped (closure unchanged)", "green": False, "skip": "proved"},
        ),
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "fail"
    assert "left gates unverified: rebuild" in gates["detail"]
    assert "failing gates" not in gates["detail"]
    assert gates["remedy"] == "make artifact-cycle"
    assert call(tmp_path)["ready"] is False


def test_gates_legacy_summary_without_skip_key_keeps_its_old_verdict(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(
        tmp_path,
        gates={
            "js": {"status": "passed", "green": True},
            "rebuild": {"status": "passed", "green": True},
            "conform": {"status": "skipped (--skip-conform)", "green": False},
            "make_test": {"status": "passed", "green": True},
        },
    )
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "warn"
    assert "predates" in gates["detail"]


def test_gates_all_green_ok(tmp_path):
    setup_green(tmp_path)
    gates = call(tmp_path)["checks"]["gates"]
    assert gates["level"] == "ok"
    assert "2026-07-17T21:30:00Z" in gates["detail"]


def test_verdict_store_missing_warns_naming_carry_out(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(tmp_path, carry_out="rebuild/evidence/carried.json")
    store = call(tmp_path)["checks"]["verdict_store"]
    assert store["level"] == "warn"
    assert "no autosave" in store["detail"].lower()
    assert "rebuild/evidence/carried.json" in store["remedy"]


def test_verdict_store_mismatch_fails_naming_carry_out(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(tmp_path, carry_out="rebuild/evidence/carried.json")
    write_autosave(tmp_path, stamp=OTHER_STAMP)
    store = call(tmp_path)["checks"]["verdict_store"]
    assert store["level"] == "fail"
    assert "rebuild/evidence/carried.json" in store["remedy"]


def test_verdict_store_aligned_ok_reports_count(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(tmp_path)
    write_autosave(tmp_path, records=[verdict("u-1"), verdict("u-2", "skip"), verdict("u-3")])
    store = call(tmp_path)["checks"]["verdict_store"]
    assert store["level"] == "ok"
    assert "2 effective" in store["detail"]


def test_frontier_none_warns(tmp_path):
    setup_green(tmp_path)
    frontier = call(tmp_path)["checks"]["frontier"]
    assert frontier["level"] == "warn"
    assert frontier["path"] is None
    assert frontier["count"] is None


def test_frontier_most_effective_wins(tmp_path):
    setup_green(tmp_path)
    _write(tmp_path / "verdicts-a.json", verdicts_doc(STAMP, [verdict("u-1"), verdict("u-2")]))
    _write(
        tmp_path / "verdicts-b.json", verdicts_doc(STAMP, [verdict("u-1"), verdict("u-2"), verdict("u-3")])
    )
    frontier = call(tmp_path)["checks"]["frontier"]
    assert frontier["level"] == "ok"
    assert frontier["path"] == "verdicts-b.json"
    assert frontier["count"] == 3


def test_frontier_excludes_the_autosave(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(tmp_path)
    write_autosave(tmp_path, records=[verdict("u-1"), verdict("u-2"), verdict("u-3")])
    _write(tmp_path / "verdicts-export.json", verdicts_doc(STAMP, [verdict("u-1")]))
    frontier = call(tmp_path)["checks"]["frontier"]
    assert frontier["path"] == "verdicts-export.json"
    assert frontier["count"] == 1


def test_frontier_filters_stale_stamped_file(tmp_path):
    setup_green(tmp_path)
    _write(tmp_path / "verdicts-cur.json", verdicts_doc(STAMP, [verdict("u-1")]))
    _write(
        tmp_path / "verdicts-old.json",
        verdicts_doc(OTHER_STAMP, [verdict("u-1"), verdict("u-2"), verdict("u-3")]),
    )
    frontier = call(tmp_path)["checks"]["frontier"]
    assert frontier["path"] == "verdicts-cur.json"
    assert frontier["count"] == 1


def test_blanks_skip_counts_as_blank(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(tmp_path)
    write_autosave(tmp_path, records=[verdict("u-1"), verdict("u-2", "skip"), verdict("u-3")])
    blanks = call(tmp_path, human_ids=HUMAN_IDS)["checks"]["blanks"]
    assert blanks["level"] == "ok"
    assert blanks["count"] == 1
    assert "1 blanks remaining" in blanks["detail"]


def test_blanks_aligned_happy_count_reads_manifest_without_shards(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review", shards=False)
    write_summary(tmp_path)
    write_autosave(tmp_path, records=[verdict("u-1")])
    blanks = call(tmp_path)["checks"]["blanks"]
    assert blanks["count"] == 2


def test_blanks_falls_back_to_shards_for_a_legacy_manifest(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review", human_ids=None)
    write_summary(tmp_path)
    write_autosave(tmp_path, records=[verdict("u-1")])
    blanks = call(tmp_path)["checks"]["blanks"]
    assert blanks["count"] == 2


def test_blanks_null_when_no_aligned_autosave(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(tmp_path)
    blanks = call(tmp_path)["checks"]["blanks"]
    assert blanks["level"] == "ok"
    assert blanks["count"] is None


def test_ready_only_in_full_green(tmp_path):
    setup_green(tmp_path)
    result = call(tmp_path)
    assert result["ready"] is True
    for name in ("surface", "freshness", "gates", "verdict_store"):
        assert result["checks"][name]["level"] == "ok"
    assert result["surface"]["repo_head"] == "abc1234"
    assert result["surface"]["generated_at"] == STAMP


def test_data_stale_makes_not_ready(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review", inputs_fp={**FP, "data": "OLD"})
    write_summary(tmp_path, inputs_fp={**FP, "data": "OLD"})
    write_autosave(tmp_path)
    assert call(tmp_path)["ready"] is False


def test_never_raises_on_empty_review_dir(tmp_path):
    (tmp_path / "rebuild" / "out" / "review").mkdir(parents=True)
    result = call(tmp_path)
    assert result["ready"] is False
    assert result["checks"]["surface"]["level"] == "fail"
    assert result["checks"]["blanks"]["count"] is None


def test_latest_verdicts_later_at_wins(tmp_path):
    path = tmp_path / "v.json"
    _write(
        path,
        verdicts_doc(
            STAMP,
            [
                verdict("u-1", "approve", at="2026-07-17T01:00:00Z"),
                verdict("u-1", "reject", at="2026-07-17T02:00:00Z"),
                verdict("u-2", "approve", at="2026-07-17T05:00:00Z"),
            ],
        ),
    )
    latest = latest_verdicts(path)
    assert latest["u-1"]["verdict"] == "reject"
    assert latest["u-2"]["verdict"] == "approve"


def test_count_effective_ignores_skip():
    records = {
        "u-1": {"verdict": "approve"},
        "u-2": {"verdict": "skip"},
        "u-3": {"verdict": "reject"},
        "u-4": {"verdict": "either"},
    }
    assert count_effective(records) == 3


def test_pick_frontier_includes_evidence_carried_masters(tmp_path):
    _write(
        tmp_path / "rebuild" / "evidence" / "verdicts-carried-abc1234.json",
        verdicts_doc(STAMP, [verdict("u-1"), verdict("u-2")]),
    )
    _write(tmp_path / "verdicts-echo-fill.json", verdicts_doc(STAMP, [verdict("u-3")]))
    hit = pick_frontier(tmp_path, STAMP)
    assert hit is not None
    assert hit[0].name == "verdicts-carried-abc1234.json"
    assert hit[1] == 2


def test_resolve_carry_source_prefers_aligned_max_count(tmp_path):
    write_autosave(tmp_path, records=[verdict("u-1")])
    _write(tmp_path / "verdicts-export.json", verdicts_doc(STAMP, [verdict("u-1"), verdict("u-2")]))
    _write(
        tmp_path / "verdicts-old.json",
        verdicts_doc(OTHER_STAMP, [verdict("u-1"), verdict("u-2"), verdict("u-3")]),
    )
    hit = resolve_carry_source(tmp_path, STAMP, tmp_path / "verdicts-autosave.json")
    assert hit is not None
    assert hit["path"].name == "verdicts-export.json"
    assert hit["count"] == 2
    assert hit["aligned"] is True


def test_resolve_carry_source_autosave_breaks_aligned_tie(tmp_path):
    write_autosave(tmp_path, records=[verdict("u-1"), verdict("u-2")])
    _write(tmp_path / "verdicts-export.json", verdicts_doc(STAMP, [verdict("u-1"), verdict("u-2")]))
    hit = resolve_carry_source(tmp_path, STAMP, tmp_path / "verdicts-autosave.json")
    assert hit is not None
    assert hit["path"].name == "verdicts-autosave.json"
    assert hit["aligned"] is True


def test_resolve_carry_source_falls_back_to_newest_stamp(tmp_path):
    write_autosave(tmp_path, stamp=OTHER_STAMP, records=[verdict("u-1")])
    _write(
        tmp_path / "rebuild" / "evidence" / "verdicts-carried-abc1234.json",
        verdicts_doc("2026-07-12T00:00:00Z", [verdict("u-1"), verdict("u-2")]),
    )
    hit = resolve_carry_source(tmp_path, STAMP, tmp_path / "verdicts-autosave.json")
    assert hit is not None
    assert hit["aligned"] is False
    assert hit["path"].name == "verdicts-carried-abc1234.json"
    assert hit["stamp"] == "2026-07-12T00:00:00Z"
    assert hit["count"] == 2


def test_resolve_carry_source_empty_aligned_autosave_yields_to_stale_master(tmp_path):
    write_autosave(tmp_path, records=[])
    _write(tmp_path / "verdicts-master.json", verdicts_doc(OTHER_STAMP, [verdict("u-1")]))
    hit = resolve_carry_source(tmp_path, STAMP, tmp_path / "verdicts-autosave.json")
    assert hit is not None
    assert hit["path"].name == "verdicts-master.json"
    assert hit["aligned"] is False


def test_resolve_carry_source_none_when_nothing_carryable(tmp_path):
    write_autosave(tmp_path, records=[verdict("u-1", "skip")])
    assert resolve_carry_source(tmp_path, STAMP, tmp_path / "verdicts-autosave.json") is None


def test_no_parsed_verdicts_file_outlives_the_read_of_the_next(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", 10**18)
    write_autosave(tmp_path, records=[verdict(unit) for unit in ("u-1", "u-2", "u-3", "u-4")])
    for name, units in (("a", ("u-1",)), ("b", ("u-1", "u-2")), ("c", ("u-1", "u-2", "u-3"))):
        _write(tmp_path / f"verdicts-{name}.json", verdicts_doc(STAMP, [verdict(unit) for unit in units]))

    class _Held(dict):
        pass

    class _HeldBytes(bytearray):
        pass

    real = status.parse_autosave_payload
    held = []
    overlaps = []

    def hold(data):
        raw = _HeldBytes(data)
        held.append(weakref.ref(raw))
        return raw

    def spy(raw):
        overlaps.append(
            sum(1 for reference in held if (alive := reference()) is not None and alive is not raw)
        )
        data = real(raw)
        if data is None:
            return None
        store = _Held(data)
        held.append(weakref.ref(store))
        return store

    _spy_on_reads(monkeypatch, wrap=hold)
    monkeypatch.setattr(status, "parse_autosave_payload", spy)
    assert pick_frontier(tmp_path, STAMP) == (tmp_path / "verdicts-c.json", 3)
    picked = len(overlaps)
    assert picked == 3
    hit = resolve_carry_source(tmp_path, STAMP, tmp_path / "verdicts-autosave.json")
    assert hit == {"path": tmp_path / "verdicts-autosave.json", "stamp": STAMP, "count": 4, "aligned": True}
    assert len(overlaps) == picked + 4
    assert overlaps == [0] * len(overlaps)


DEFAULT_HEAD_BYTES = status._HEAD_BYTES
ESCAPED_VALUE_STAMP = "2026-07-04T00:00:00Z"


def _records(count):
    return [verdict(f"u-{index}") for index in range(1, count + 1)]


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _spy_on_parses(monkeypatch):
    real = status.parse_autosave_payload
    parsed = []

    def spy(raw):
        parsed.append(hashlib.sha256(raw).hexdigest())
        return real(raw)

    monkeypatch.setattr(status, "parse_autosave_payload", spy)
    return parsed


def _spy_on_head_reads(monkeypatch):
    real = status._head_stamp
    heads = []

    def spy(head):
        heads.append(len(head))
        return real(head)

    monkeypatch.setattr(status, "_head_stamp", spy)
    return heads


def _spy_on_reads(monkeypatch, wrap=None):
    """Patch `Path.open` to count the bytes read through each handle, per path, and pass each read through `wrap` when one is given. `Path.read_bytes` opens through `Path.open`, so the autosave read is counted too."""
    real_open = Path.open
    tally: Counter[Path] = Counter()

    class Handle:
        def __init__(self, path, handle):
            self.path = path
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self.handle.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def read(self, *args):
            data = self.handle.read(*args)
            tally[self.path] += len(data)
            return data if wrap is None else wrap(data)

    def spy(self, *args, **kwargs):
        return Handle(self, real_open(self, *args, **kwargs))

    monkeypatch.setattr(Path, "open", spy)
    return tally


def _escaped_key_text(stamp, records):
    return (
        f'{{"format": "ams-review-verdicts/1", "manifest\\u005fgenerated_at": "{stamp}", "exported_at": "", '
        f'"verdicts": {json.dumps(records)}}}'
    )


def _escaped_value_text(records):
    escaped = ESCAPED_VALUE_STAMP.replace("-", "\\u002d")
    return (
        f'{{"format": "ams-review-verdicts/1", "manifest_generated_at": "{escaped}", "exported_at": "", '
        f'"verdicts": {json.dumps(records)}}}'
    )


def _reference_verdict_files(repo_root):
    root = Path(repo_root)
    candidates = sorted(root.glob("verdicts-*.json")) + sorted(
        (root / "rebuild" / "evidence").glob("verdicts-*.json")
    )
    for path in candidates:
        if path.name == "verdicts-autosave.json":
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        data = parse_autosave_payload(raw)
        if data is None:
            continue
        yield path, data


def _reference_effective_count(data):
    try:
        best = {}
        for record in data["verdicts"]:
            unit = record["unit"]
            if unit not in best or record["at"] > best[unit]["at"]:
                best[unit] = record
        return sum(1 for record in best.values() if record.get("verdict") != "skip")
    except KeyError, TypeError:
        return None


def _reference_pick_frontier(repo_root, manifest_stamp):
    best = None
    for path, data in _reference_verdict_files(repo_root):
        if data["manifest_generated_at"] != manifest_stamp:
            continue
        count = _reference_effective_count(data)
        if count is None:
            continue
        if best is None or count > best[1]:
            best = (path, count)
    return best


def _reference_resolve_carry_source(repo_root, manifest_stamp, autosave_path):
    entries = []
    autosave_path = Path(autosave_path)
    try:
        raw = autosave_path.read_bytes() if autosave_path.exists() else None
    except OSError:
        raw = None
    autosave = parse_autosave_payload(raw) if raw is not None else None
    if autosave is not None:
        count = _reference_effective_count(autosave)
        if count:
            entries.append((autosave_path, autosave["manifest_generated_at"], count, True))
    for path, data in _reference_verdict_files(repo_root):
        count = _reference_effective_count(data)
        if count:
            entries.append((path, data["manifest_generated_at"], count, False))
    if not entries:
        return None
    pool = [entry for entry in entries if manifest_stamp is not None and entry[1] == manifest_stamp]
    aligned = bool(pool)
    if not pool:
        latest = max(stamp for _, stamp, _, _ in entries)
        pool = [entry for entry in entries if entry[1] == latest]
    path, stamp, count, _ = max(pool, key=lambda entry: (entry[2], entry[3]))
    return {"path": path, "stamp": stamp, "count": count, "aligned": aligned}


def _write_corpus(root):
    """Write verdicts files covering the cases the head read must handle, and return every stamp they use. Each edge-case file has a stamp of its own, so wrongly skipping or wrongly keeping it changes the frontier for that stamp. The autosave under `rebuild/evidence/` has the newest stamp, so wrongly keeping it would also change `resolve_carry_source`'s fallback."""
    evidence = root / "rebuild" / "evidence"
    write_autosave(root, records=_records(4))
    _write(root / "verdicts-plain-a.json", verdicts_doc(STAMP, _records(2)))
    _write(root / "verdicts-plain-b.json", verdicts_doc(STAMP, _records(2)))
    _write(root / "verdicts-plain-c.json", verdicts_doc(STAMP, _records(1)))
    _write(evidence / "verdicts-carried-tied.json", verdicts_doc(STAMP, _records(2)))
    _write(evidence / "verdicts-autosave.json", verdicts_doc("2026-08-31T00:00:00Z", _records(5)))
    truncated = json.dumps(verdicts_doc("2026-07-01T00:00:01Z", _records(3)))
    _write_raw(root / "verdicts-truncated.json", truncated[: len(truncated) - 5])
    _write(
        root / "verdicts-wrong-format.json",
        {**verdicts_doc("2026-07-01T00:00:02Z", _records(3)), "format": "ams-review-verdicts/0"},
    )
    _write(
        root / "verdicts-records-in-a-dict.json",
        {
            **verdicts_doc("2026-07-01T00:00:03Z", []),
            "verdicts": {"u-1": verdict("u-1"), "u-2": verdict("u-2")},
        },
    )
    _write(
        root / "verdicts-bare-string-record.json", verdicts_doc("2026-07-01T00:00:04Z", [*_records(3), "u-4"])
    )
    foreign = json.dumps(verdicts_doc("2026-07-01T00:00:05Z", _records(200)))
    _write_raw(root / "verdicts-foreign-truncated.json", foreign[: len(foreign) // 2])
    _write(
        root / "verdicts-late-stamp.json",
        {
            "format": "ams-review-verdicts/1",
            "verdicts": _records(2),
            "exported_at": "x" * (DEFAULT_HEAD_BYTES + 100),
            "manifest_generated_at": "2026-07-02T00:00:00Z",
        },
    )
    _write(
        root / "verdicts-nested-stamp.json",
        {
            "meta": {"manifest_generated_at": "2026-07-03T00:00:00Z"},
            **verdicts_doc("2026-07-03T00:00:01Z", _records(2)),
        },
    )
    _write_raw(root / "verdicts-escaped-value.json", _escaped_value_text(_records(2)))
    _write_raw(root / "verdicts-escaped-key.json", _escaped_key_text("2026-07-05T00:00:00Z", _records(2)))
    _write_raw(
        root / "verdicts-bom.json",
        json.dumps(verdicts_doc("2026-07-06T00:00:00Z", _records(2))),
        encoding="utf-8-sig",
    )
    _write_raw(
        root / "verdicts-utf16.json",
        json.dumps(verdicts_doc("2026-07-07T00:00:00Z", _records(2))),
        encoding="utf-16",
    )
    _write(
        root / "verdicts-null-stamp.json", {**verdicts_doc(STAMP, _records(3)), "manifest_generated_at": None}
    )
    _write(
        root / "verdicts-no-stamp.json",
        {
            key: value
            for key, value in verdicts_doc(STAMP, _records(3)).items()
            if key != "manifest_generated_at"
        },
    )
    _write_raw(
        root / "verdicts-two-stamps.json",
        '{"format": "ams-review-verdicts/1", "manifest_generated_at": "2026-07-08T00:00:00Z", '
        f'"manifest_generated_at": "2026-07-08T00:00:01Z", "exported_at": "", "verdicts": {json.dumps(_records(2))}}}',
    )
    (root / "verdicts-dir.json").mkdir()
    return [
        STAMP,
        "2026-08-31T00:00:00Z",
        "2026-07-01T00:00:01Z",
        "2026-07-01T00:00:02Z",
        "2026-07-01T00:00:03Z",
        "2026-07-01T00:00:04Z",
        "2026-07-01T00:00:05Z",
        "2026-07-02T00:00:00Z",
        "2026-07-03T00:00:00Z",
        "2026-07-03T00:00:01Z",
        ESCAPED_VALUE_STAMP,
        "2026-07-05T00:00:00Z",
        "2026-07-06T00:00:00Z",
        "2026-07-07T00:00:00Z",
        "2026-07-08T00:00:00Z",
        "2026-07-08T00:00:01Z",
    ]


def test_pick_frontier_parses_no_candidate_stamped_for_another_surface(tmp_path, monkeypatch):
    aligned = tmp_path / "verdicts-a.json"
    stash = tmp_path / "verdicts-autosave-2026-07-10T00.00.00Z.json"
    _write(aligned, verdicts_doc(STAMP, _records(2)))
    _write(stash, verdicts_doc(OTHER_STAMP, _records(200)))
    _write_raw(tmp_path / "verdicts-escaped-key.json", _escaped_key_text(OTHER_STAMP, _records(3)))
    _write(
        tmp_path / "rebuild" / "evidence" / "verdicts-carried-abc1234.json",
        verdicts_doc(OTHER_STAMP, _records(3)),
    )
    assert stash.stat().st_size > status._HEAD_BYTES
    parsed = _spy_on_parses(monkeypatch)
    reads = _spy_on_reads(monkeypatch)
    assert pick_frontier(tmp_path, STAMP) == (aligned, 2)
    assert reads[stash] <= status._HEAD_BYTES
    assert parsed == [_digest(aligned)]
    reads.clear()
    assert pick_frontier(tmp_path, None) is None
    assert reads[stash] <= status._HEAD_BYTES
    assert parsed == [_digest(aligned)]


def test_a_candidate_stamped_for_another_surface_and_malformed_past_its_stamp_is_skipped(tmp_path):
    text = json.dumps(verdicts_doc(OTHER_STAMP, _records(3)))
    _write_raw(tmp_path / "verdicts-a-broken.json", text[: len(text) - 20])
    _write(tmp_path / "verdicts-b.json", verdicts_doc(STAMP, _records(1)))
    assert pick_frontier(tmp_path, STAMP) == (tmp_path / "verdicts-b.json", 1)
    assert pick_frontier(tmp_path, OTHER_STAMP) is None


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(json.dumps(verdicts_doc(STAMP, _records(3)))[:-5], id="truncated-tail"),
        pytest.param(
            json.dumps({**verdicts_doc(STAMP, _records(3)), "format": "ams-review-verdicts/0"}),
            id="wrong-format",
        ),
        pytest.param(
            json.dumps(
                {**verdicts_doc(STAMP, []), "verdicts": {record["unit"]: record for record in _records(3)}}
            ),
            id="verdicts-not-a-list",
        ),
    ],
)
def test_an_aligned_candidate_malformed_past_its_stamp_is_not_trusted(tmp_path, text):
    _write_raw(tmp_path / "verdicts-a-malformed.json", text)
    assert pick_frontier(tmp_path, STAMP) is None
    _write(tmp_path / "verdicts-b.json", verdicts_doc(STAMP, _records(1)))
    assert pick_frontier(tmp_path, STAMP) == (tmp_path / "verdicts-b.json", 1)


def test_a_head_stamp_overridden_after_the_records_is_not_the_frontier_under_the_head_stamp(tmp_path):
    text = (
        f'{{"format": "ams-review-verdicts/1", "manifest_generated_at": "{STAMP}", "exported_at": "{STAMP}", '
        f'"verdicts": {json.dumps(_records(3))}, "manifest_generated_at": "{OTHER_STAMP}"}}'
    )
    assert json.loads(text)["manifest_generated_at"] == OTHER_STAMP
    _write_raw(tmp_path / "verdicts-a-restamped.json", text)
    _write(tmp_path / "verdicts-b.json", verdicts_doc(STAMP, _records(1)))
    assert pick_frontier(tmp_path, STAMP) == (tmp_path / "verdicts-b.json", 1)


def test_a_stamp_far_from_the_head_of_its_file_is_still_read(tmp_path):
    path = tmp_path / "verdicts-late.json"
    _write(
        path,
        {
            "format": "ams-review-verdicts/1",
            "verdicts": _records(2),
            "exported_at": "x" * (status._HEAD_BYTES + 100),
            "manifest_generated_at": STAMP,
        },
    )
    assert pick_frontier(tmp_path, STAMP) == (path, 2)


def test_resolve_carry_source_falls_back_to_the_newest_stamp_on_a_tree_pick_frontier_read_by_head(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    newer_stamp = "2026-07-20T00:00:00Z"
    aligned = tmp_path / "verdicts-aligned.json"
    newer = tmp_path / "verdicts-newer.json"
    autosave = tmp_path / "verdicts-autosave.json"
    _write(aligned, verdicts_doc(STAMP, _records(1)))
    _write(newer, verdicts_doc(newer_stamp, _records(3)))
    _write(tmp_path / "verdicts-older.json", verdicts_doc(OTHER_STAMP, _records(2)))
    assert pick_frontier(tmp_path, STAMP) == (aligned, 1)
    assert status._MEMO[newer][1].parsed is False
    assert resolve_carry_source(tmp_path, "2026-07-01T00:00:00Z", autosave) == {
        "path": newer,
        "stamp": newer_stamp,
        "count": 3,
        "aligned": False,
    }
    assert resolve_carry_source(tmp_path, STAMP, autosave) == {
        "path": aligned,
        "stamp": STAMP,
        "count": 1,
        "aligned": True,
    }


@pytest.mark.parametrize("head_bytes", [1, 7, 64, DEFAULT_HEAD_BYTES])
def test_the_frontier_and_the_carry_source_answer_as_a_whole_parse_of_every_file_does(
    tmp_path, monkeypatch, head_bytes
):
    stamps = [*_write_corpus(tmp_path), None, "no-such-stamp", 0]
    autosave = tmp_path / "verdicts-autosave.json"
    monkeypatch.setattr(status, "_HEAD_BYTES", head_bytes)
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    for stamp in stamps:
        message = (stamp, status._HEAD_BYTES)
        expected_frontier = _reference_pick_frontier(tmp_path, stamp)
        expected_carry = _reference_resolve_carry_source(tmp_path, stamp, autosave)
        monkeypatch.setattr(status, "_MEMO", {})
        assert pick_frontier(tmp_path, stamp) == expected_frontier, message
        assert pick_frontier(tmp_path, stamp) == expected_frontier, message
        monkeypatch.setattr(status, "_MEMO", {})
        assert resolve_carry_source(tmp_path, stamp, autosave) == expected_carry, message
        assert resolve_carry_source(tmp_path, stamp, autosave) == expected_carry, message
        monkeypatch.setattr(status, "_MEMO", {})
        assert pick_frontier(tmp_path, stamp) == expected_frontier, message
        assert resolve_carry_source(tmp_path, stamp, autosave) == expected_carry, message
    monkeypatch.setattr(status, "_MEMO", {})
    for stamp in stamps:
        message = (stamp, status._HEAD_BYTES)
        assert pick_frontier(tmp_path, stamp) == _reference_pick_frontier(tmp_path, stamp), message
    for stamp in stamps:
        message = (stamp, status._HEAD_BYTES)
        assert resolve_carry_source(tmp_path, stamp, autosave) == _reference_resolve_carry_source(
            tmp_path, stamp, autosave
        ), message


def test_the_head_read_agrees_with_a_whole_parse_at_every_window_size(tmp_path, monkeypatch):
    compact = tmp_path / "verdicts-compact.json"
    indented = tmp_path / "verdicts-indented.json"
    escaped = tmp_path / "verdicts-escaped-value.json"
    _write(compact, verdicts_doc(STAMP, _records(1)))
    _write_raw(indented, json.dumps(verdicts_doc(OTHER_STAMP, _records(2)), indent=2))
    _write_raw(escaped, _escaped_value_text(_records(3)))
    for path in (compact, indented, escaped):
        assert path.read_bytes().index(b'"verdicts"') + len(b'"verdicts"') < 190
    autosave = tmp_path / "verdicts-autosave.json"
    for size in range(1, 200):
        monkeypatch.setattr(status, "_HEAD_BYTES", size)
        for stamp in (STAMP, OTHER_STAMP, ESCAPED_VALUE_STAMP, None, "no-such-stamp"):
            monkeypatch.setattr(status, "_MEMO", {})
            assert pick_frontier(tmp_path, stamp) == _reference_pick_frontier(tmp_path, stamp), (stamp, size)
            monkeypatch.setattr(status, "_MEMO", {})
            assert resolve_carry_source(tmp_path, stamp, autosave) == _reference_resolve_carry_source(
                tmp_path, stamp, autosave
            ), (stamp, size)


def test_pick_frontier_answers_an_unchanged_tree_from_the_memo(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    aligned = tmp_path / "verdicts-a.json"
    foreign = tmp_path / "verdicts-b.json"
    autosave = tmp_path / "verdicts-autosave.json"
    _write(aligned, verdicts_doc(STAMP, _records(2)))
    _write(foreign, verdicts_doc(OTHER_STAMP, _records(200)))
    write_autosave(tmp_path, records=_records(1))
    assert foreign.stat().st_size > status._HEAD_BYTES
    parsed = _spy_on_parses(monkeypatch)
    heads = _spy_on_head_reads(monkeypatch)
    assert pick_frontier(tmp_path, STAMP) == (aligned, 2)
    first = (len(parsed), len(heads))
    assert pick_frontier(tmp_path, STAMP) == (aligned, 2)
    assert (len(parsed), len(heads)) == first
    assert set(status._MEMO) == {aligned, foreign}
    assert pick_frontier(tmp_path, STAMP) == (aligned, 2)
    assert (len(parsed), len(heads)) == first
    carry = {"path": aligned, "stamp": STAMP, "count": 2, "aligned": True}
    assert resolve_carry_source(tmp_path, STAMP, autosave) == carry
    counted = len(parsed)
    assert resolve_carry_source(tmp_path, STAMP, autosave) == carry
    assert parsed[counted:] == [_digest(autosave)]


def test_a_same_size_rewrite_in_place_is_read_again(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    path = tmp_path / "verdicts-a.json"
    _write(path, verdicts_doc(STAMP, _records(2)))
    assert pick_frontier(tmp_path, STAMP) == (path, 2)
    original = path.stat()
    records = [verdict("u-1", "skip") | {"note": "abc"}, verdict("u-2")]
    _write(path, verdicts_doc(STAMP, records))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns - 1_000_000_000))
    assert path.stat().st_size == original.st_size
    assert pick_frontier(tmp_path, STAMP) == (path, 1)
    _write(path, verdicts_doc(OTHER_STAMP, records))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns - 2_000_000_000))
    assert path.stat().st_size == original.st_size
    assert pick_frontier(tmp_path, STAMP) is None
    assert pick_frontier(tmp_path, OTHER_STAMP) == (path, 1)


def test_a_same_size_rewrite_that_keeps_its_mtime_is_read_again(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    path = tmp_path / "verdicts-a.json"
    _write(path, verdicts_doc(STAMP, _records(2)))
    assert pick_frontier(tmp_path, STAMP) == (path, 2)
    recorded = path.stat()
    _write(path, verdicts_doc(OTHER_STAMP, _records(2)))
    os.utime(path, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))
    deadline = time.monotonic() + 1
    while path.stat().st_ctime_ns == recorded.st_ctime_ns and time.monotonic() < deadline:
        os.utime(path, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))
    rewritten = path.stat()
    assert (rewritten.st_ino, rewritten.st_size, rewritten.st_mtime_ns) == (
        recorded.st_ino,
        recorded.st_size,
        recorded.st_mtime_ns,
    )
    assert rewritten.st_ctime_ns != recorded.st_ctime_ns
    assert pick_frontier(tmp_path, STAMP) is None
    assert pick_frontier(tmp_path, OTHER_STAMP) == (path, 2)


def test_a_file_renamed_onto_a_candidate_is_read_again(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    path = tmp_path / "verdicts-a.json"
    replacement = tmp_path / "replacement.json"
    _write(path, verdicts_doc(STAMP, _records(2)))
    assert pick_frontier(tmp_path, STAMP) == (path, 2)
    recorded = path.stat()
    _write(replacement, verdicts_doc(OTHER_STAMP, _records(2)))
    os.utime(replacement, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))
    os.replace(replacement, path)
    replaced = path.stat()
    assert (replaced.st_size, replaced.st_mtime_ns) == (recorded.st_size, recorded.st_mtime_ns)
    assert pick_frontier(tmp_path, STAMP) is None
    assert pick_frontier(tmp_path, OTHER_STAMP) == (path, 2)


def test_a_corrupt_aligned_candidate_is_parsed_once_and_skipped_every_time(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    corrupt = tmp_path / "verdicts-a-corrupt.json"
    valid = tmp_path / "verdicts-b.json"
    _write_raw(corrupt, json.dumps(verdicts_doc(STAMP, _records(3)))[:-5])
    _write(valid, verdicts_doc(STAMP, _records(1)))
    parsed = _spy_on_parses(monkeypatch)
    assert pick_frontier(tmp_path, STAMP) == (valid, 1)
    assert pick_frontier(tmp_path, STAMP) == (valid, 1)
    assert parsed.count(_digest(corrupt)) == 1


def test_a_candidate_changed_inside_the_settle_window_is_read_again(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", 10**18)
    path = tmp_path / "verdicts-a.json"
    _write(path, verdicts_doc(STAMP, _records(2)))
    parsed = _spy_on_parses(monkeypatch)
    assert pick_frontier(tmp_path, STAMP) == (path, 2)
    assert pick_frontier(tmp_path, STAMP) == (path, 2)
    assert parsed == [_digest(path)] * 2


def test_resolve_carry_source_parses_only_what_pick_frontier_read_by_its_head(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    autosave = tmp_path / "verdicts-autosave.json"
    foreign = tmp_path / "verdicts-c.json"
    carried = tmp_path / "rebuild" / "evidence" / "verdicts-carried-abc1234.json"
    write_autosave(tmp_path, records=_records(1))
    _write(tmp_path / "verdicts-a.json", verdicts_doc(STAMP, _records(2)))
    _write(tmp_path / "verdicts-b.json", verdicts_doc(STAMP, _records(3)))
    _write(foreign, verdicts_doc(OTHER_STAMP, _records(200)))
    _write(carried, verdicts_doc("2026-07-20T00:00:00Z", _records(4)))
    assert pick_frontier(tmp_path, STAMP) == (tmp_path / "verdicts-b.json", 3)
    parsed = _spy_on_parses(monkeypatch)
    hit = resolve_carry_source(tmp_path, STAMP, autosave)
    assert parsed == [_digest(autosave), _digest(foreign), _digest(carried)]
    assert hit == _reference_resolve_carry_source(tmp_path, STAMP, autosave)


def test_a_deleted_candidate_drops_out_of_the_frontier(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    winner = tmp_path / "verdicts-a.json"
    runner_up = tmp_path / "verdicts-b.json"
    _write(winner, verdicts_doc(STAMP, _records(3)))
    _write(runner_up, verdicts_doc(STAMP, _records(2)))
    assert pick_frontier(tmp_path, STAMP) == (winner, 3)
    winner.unlink()
    assert pick_frontier(tmp_path, STAMP) == (runner_up, 2)
    assert set(status._MEMO) == {runner_up}
    runner_up.unlink()
    assert pick_frontier(tmp_path, STAMP) is None


def test_the_frontier_path_is_spelled_from_the_callers_root(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    _write(tmp_path / "verdicts-a.json", verdicts_doc(STAMP, _records(2)))
    monkeypatch.chdir(tmp_path)
    assert pick_frontier(Path("."), STAMP) == (Path("verdicts-a.json"), 2)
    assert pick_frontier(Path("."), STAMP) == (Path("verdicts-a.json"), 2)
    assert pick_frontier(tmp_path, STAMP) == (tmp_path / "verdicts-a.json", 2)


def test_the_memo_holds_only_the_last_walks_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_SETTLE_NS", -(10**18))
    first = tmp_path / "first"
    second = tmp_path / "second"
    carried = second / "rebuild" / "evidence" / "verdicts-carried-abc1234.json"
    _write(first / "verdicts-a.json", verdicts_doc(STAMP, _records(1)))
    _write(second / "verdicts-b.json", verdicts_doc(STAMP, _records(2)))
    _write(carried, verdicts_doc(OTHER_STAMP, _records(3)))
    assert pick_frontier(first, STAMP) == (first / "verdicts-a.json", 1)
    assert set(status._MEMO) == {first / "verdicts-a.json"}
    assert pick_frontier(second, STAMP) == (second / "verdicts-b.json", 2)
    assert set(status._MEMO) == {second / "verdicts-b.json", carried}


def test_mismatched_empty_autosave_remedy_names_the_frontier(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
    write_summary(tmp_path)
    write_autosave(tmp_path, stamp=OTHER_STAMP, records=[])
    _write(
        tmp_path / "rebuild" / "evidence" / "verdicts-carried-abc1234.json",
        verdicts_doc(STAMP, [verdict("u-1"), verdict("u-2")]),
    )
    store = call(tmp_path)["checks"]["verdict_store"]
    assert store["level"] == "fail"
    assert "rebuild/evidence/verdicts-carried-abc1234.json" in store["remedy"]
    assert "stashed automatically" in store["remedy"]


def test_partially_null_fingerprint_says_unverifiable_not_changed(tmp_path):
    write_surface(
        tmp_path / "rebuild" / "out" / "review",
        inputs_fp={**FP, "data": None, "baselines": None, "pipeline_code": None},
    )
    write_summary(tmp_path)
    write_autosave(tmp_path)
    freshness = call(tmp_path)["checks"]["freshness"]
    assert freshness["level"] == "fail"
    assert "cannot be verified" in freshness["detail"]
    assert freshness["components"]["data"] == "unknown"
    assert freshness["components"]["review_code"] == "fresh"


def test_aligned_empty_autosave_warns_toward_the_frontier(tmp_path):
    setup_green(tmp_path)
    write_autosave(tmp_path, records=[])
    _write(
        tmp_path / "rebuild" / "evidence" / "verdicts-carried-abc1234.json",
        verdicts_doc(STAMP, [verdict("u-1"), verdict("u-2")]),
    )
    result = call(tmp_path)
    store = result["checks"]["verdict_store"]
    assert store["level"] == "warn"
    assert "rebuild/evidence/verdicts-carried-abc1234.json" in store["remedy"]
    assert result["ready"] is True
    assert result["checks"]["blanks"]["count"] == 3


def _readiness(repo, **kwargs):
    return verdict_ready.readiness(
        repo_root=repo,
        review_dir=repo / "rebuild" / "out" / "review",
        m1_out=repo / "rebuild" / "out" / "m1",
        autosave_path=repo / "verdicts-autosave.json",
        cycle_summary_path=repo / "rebuild" / "out" / "cycle_summary.json",
        recompute=recompute,
        **kwargs,
    )


def test_readiness_adds_the_server_row_and_gates_ready_on_it(tmp_path):
    setup_green(tmp_path)
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
    assert lines[-1] == f"READY - adjudicate at {verdict_ready.DOCKET_URL}"


def test_readiness_without_the_server_row_answers_for_the_surface_alone(tmp_path):
    """Under `make review-cycle` the Makefile recipe handles the server after the cycle, so the cycle calls `readiness` with `with_server=False`. There is no server row, and READY depends on the surface alone."""
    setup_green(tmp_path)
    result, ready = _readiness(tmp_path, with_server=False, listening=lambda: False)
    assert ready is True
    assert "server" not in result["checks"]
    lines = verdict_ready.checklist(result, ready)
    assert not any("server" in line for line in lines)
    assert lines[-1] == f"READY - adjudicate at {verdict_ready.DOCKET_URL}"

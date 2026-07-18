"""Tests for rebuild.review.status.compute_status and its helpers: the readiness dict the /status handler and the verdict_ready CLI both render. Fixtures build a fake repo tree (surface manifest + tiny shards, cycle summary, autosave, repo-root verdicts files) and stub the fingerprint recompute so no real build inputs are touched."""

import json

from rebuild.review.status import compute_status, count_effective, latest_verdicts, pick_frontier

STAMP = "2026-07-17T20:24:44Z"
OTHER_STAMP = "2026-07-10T00:00:00Z"
FP = {"data": "d", "baselines": "b", "pipeline_code": "p", "review_code": "r", "static": "s", "fonts": "f"}

DEFAULT_CLASSES = [
    {"id": "class-a", "shard": "units/class-a.json", "status": "reviewed-approved"},
    {"id": "class-b", "shard": "units/class-b.json", "status": "intended"},
]
CLASS_A_UNITS = [{"id": "u-1", "batch": 1}, {"id": "u-2", "batch": 2}, {"id": "m-1", "batch": None}]
CLASS_B_UNITS = [{"id": "u-3", "batch": 1}]
HUMAN_IDS = frozenset({"u-1", "u-2", "u-3"})


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


def write_surface(review_dir, *, generated_at=STAMP, repo_head="abc1234", inputs_fp="fresh", shards=True):
    manifest = {
        "format": "ams-review-manifest/1",
        "generated_at": generated_at,
        "repo_head": repo_head,
        "classes": DEFAULT_CLASSES,
    }
    if inputs_fp == "fresh":
        manifest["inputs_fingerprint"] = dict(FP)
    elif inputs_fp != "omit":
        manifest["inputs_fingerprint"] = inputs_fp
    _write(review_dir / "manifest.json", manifest)
    if shards:
        _write(review_dir / "units" / "class-a.json", CLASS_A_UNITS)
        _write(review_dir / "units" / "class-b.json", CLASS_B_UNITS)


def write_summary(
    repo,
    *,
    generated_at=STAMP,
    inputs_fp="match",
    exit_="ok",
    gates="green",
    carry_out="rebuild/evidence/carried.json",
):
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
    assert freshness["remedy"].startswith("make artifact-cycle")


def test_data_stale_fails_with_artifact_cycle_remedy(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review", inputs_fp={**FP, "data": "OLD"})
    write_summary(tmp_path, inputs_fp={**FP, "data": "OLD"})
    write_autosave(tmp_path)
    freshness = call(tmp_path)["checks"]["freshness"]
    assert freshness["level"] == "fail"
    assert freshness["components"]["data"] == "stale"
    assert freshness["components"]["static"] == "fresh"
    assert freshness["remedy"].startswith("make artifact-cycle")
    assert "data" in freshness["detail"]


def test_static_only_stale_warns_with_review_build_remedy(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review", inputs_fp={**FP, "static": "OLD"})
    write_summary(tmp_path, inputs_fp={**FP, "static": "OLD"})
    write_autosave(tmp_path)
    freshness = call(tmp_path)["checks"]["freshness"]
    assert freshness["level"] == "warn"
    assert freshness["components"]["static"] == "stale"
    assert freshness["remedy"] == "make review-build"


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


def test_gates_skipped_conform_warns(tmp_path):
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
    assert "conform" in gates["detail"]


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


def test_blanks_aligned_happy_count_scans_shards(tmp_path):
    write_surface(tmp_path / "rebuild" / "out" / "review")
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

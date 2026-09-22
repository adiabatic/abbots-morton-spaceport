import json
from pathlib import Path

import pytest

from rebuild.tools import compressed_fold_experiment as experiment


def test_prepare_baselines_copies_the_names_and_count_sidecars(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "baseline-default.subset.tsv.gz").write_bytes(b"table")
    (source / experiment.baseline_subset.NAMES_NAME).write_text(
        json.dumps({"format": experiment.baseline_subset.NAMES_FORMAT, "names": {"default": []}})
    )
    (source / experiment.baseline_subset.STAMP_NAME).write_text(
        json.dumps({"format": experiment.baseline_subset.STAMP_FORMAT, "rows": {"default": 17}})
    )
    monkeypatch.setattr(experiment.run_m1, "OUT_DIR", source)
    monkeypatch.setattr(experiment.baseline_subset, "ensure_fresh", lambda _root: False)

    experiment.prepare_baselines(destination)

    assert (destination / "baseline-default.subset.tsv.gz").read_bytes() == b"table"
    assert experiment.baseline_subset.read_subset_names(destination) == {"default": []}
    assert experiment.baseline_subset.subset_row_counts(destination) == {"default": 17}


def test_artifact_snapshot_uses_uncompressed_windows_identity(tmp_path):
    import gzip

    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    with gzip.GzipFile(left / "windows-default.tsv.gz", "wb", mtime=1) as handle:
        handle.write(b"same\n")
    with gzip.GzipFile(right / "windows-default.tsv.gz", "wb", mtime=2) as handle:
        handle.write(b"same\n")

    report = experiment.compare_snapshots(
        experiment.artifact_snapshot(left), experiment.artifact_snapshot(right)
    )

    assert report["files"]["windows-default.tsv.gz"]["identical"]
    assert report["missing_required"]


def test_compare_reports_missing_and_changed_artifacts(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "settlement-default.tsv").write_text("left\n")
    (right / "settlement-default.tsv").write_text("right\n")
    (right / "treaties-default.tsv").write_text("new\n")

    report = experiment.compare_snapshots(
        experiment.artifact_snapshot(left), experiment.artifact_snapshot(right)
    )

    assert not report["identical"]
    assert not report["files"]["settlement-default.tsv"]["identical"]
    assert report["files"]["treaties-default.tsv"]["left"] is None


def test_empty_snapshots_never_compare_identical(tmp_path):
    snapshot = experiment.artifact_snapshot(tmp_path)

    report = experiment.compare_snapshots(snapshot, snapshot)

    assert not report["identical"]
    assert "M1.otf" in report["missing_required"]


def test_cold_run_refuses_a_nonempty_cache(tmp_path, monkeypatch):
    cache = tmp_path / "app-cache" / "taken"
    cache.mkdir(parents=True)
    (cache / "artifact").write_text("standing")
    monkeypatch.setattr(experiment, "require_experiment_source", lambda: None)
    monkeypatch.setattr(experiment, "require_experiment_root", lambda _path: None)
    monkeypatch.setattr(experiment.kernel_exec, "cargo_build", lambda: None)
    args = experiment.parser().parse_args(
        [
            "--experiment-root",
            str(tmp_path),
            "run",
            "--arm",
            "production",
            "--scenario",
            "cold",
            "--cache",
            "taken",
        ]
    )
    args.experiment_root = Path(args.experiment_root)

    with pytest.raises(SystemExit, match="is not empty"):
        experiment.command_run(args)


def test_production_child_clears_the_prototype_switch(monkeypatch):
    monkeypatch.setenv(experiment.PROTOTYPE_ENV, "1")

    assert experiment.PROTOTYPE_ENV not in experiment.child_environment("production")
    assert experiment.child_environment("prototype")[experiment.PROTOTYPE_ENV] == "1"


def test_timing_records_preserve_repeated_configuration_phases(tmp_path):
    log = tmp_path / "raw.log"
    log.write_text("[t] fold_expand_sort[default] 0.7s rows=17\n" "[t] fold_expand_sort[ss03] 0.6s rows=19\n")

    assert experiment.timing_records(log) == [
        {"label": "fold_expand_sort[default]", "elapsed_s": 0.7, "detail": "rows=17"},
        {"label": "fold_expand_sort[ss03]", "elapsed_s": 0.6, "detail": "rows=19"},
    ]


def test_controlled_edit_blocks_are_valid_yaml():
    old = yaml_block(experiment.PEA_EDIT_OLD)
    new = yaml_block(experiment.PEA_EDIT_NEW)

    assert old[0]["when"]["right"]["then"]["family"] == "qsEt"
    assert new[0]["when"]["right"]["then"]["family"] == "qsEight"


def yaml_block(text: str):
    import yaml

    return yaml.safe_load("prefer:\n" + "\n".join(f"  {line}" for line in text.splitlines()))["prefer"]


def test_cache_name_cannot_escape_the_experiment_root(tmp_path):
    with pytest.raises(SystemExit, match="one directory name"):
        experiment.cache_dir(tmp_path, "../outside")


def test_source_guard_accepts_only_the_nested_issue_clone(monkeypatch, tmp_path):
    root = tmp_path / "issue302" / "source"
    root.mkdir(parents=True)
    monkeypatch.setattr(experiment, "ROOT", root)
    (root / "site").mkdir()
    baseline = root / "rebuild" / "out" / "baseline-default.tsv.gz"
    baseline.parent.mkdir(parents=True)
    baseline.touch()

    experiment.require_experiment_source()

    monkeypatch.setattr(experiment, "ROOT", tmp_path / "ordinary-checkout")

    with pytest.raises(SystemExit, match="isolated issue302/source"):
        experiment.require_experiment_source()


def test_failed_ladder_cannot_archive_a_preexisting_report(tmp_path, monkeypatch):
    root = tmp_path / "source"
    rows = root / "rebuild" / "out" / "scaling-ladder.json"
    rows.parent.mkdir(parents=True)
    rows.write_text("stale\n")
    binary = tmp_path / "kernel"
    binary.write_bytes(b"binary")
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(experiment, "ROOT", root)
    monkeypatch.setattr(experiment.kernel_exec, "BINARY", binary)
    monkeypatch.setattr(experiment, "require_experiment_source", lambda: None)
    monkeypatch.setattr(experiment, "require_experiment_root", lambda _path: None)
    monkeypatch.setattr(experiment, "source_record", lambda: {})
    monkeypatch.setattr(experiment, "machine_record", lambda: {})

    def fail_without_output(_command, _log, _environment):
        assert not rows.exists()
        return 1, 23, 4.5

    monkeypatch.setattr(experiment, "measured_child", fail_without_output)
    args = experiment.parser().parse_args(
        ["--experiment-root", str(evidence), "ladder", "--arm", "production"]
    )
    args.experiment_root = Path(args.experiment_root)

    assert experiment.command_ladder(args) == 1
    run_dir = next((evidence / "ladder" / "production").iterdir())
    assert not (run_dir / "scaling-ladder.json").exists()
    assert not json.loads((run_dir / "run.json").read_text())["fresh_ladder"]


def test_child_refuses_an_outside_result_without_writing_it(tmp_path):
    experiment_root = tmp_path / "evidence"
    request_path = experiment_root / "runs" / "request.json"
    outside_result = tmp_path / "outside" / "child-result.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text(
        json.dumps(
            {
                "experiment_root": str(experiment_root),
                "result": str(outside_result),
            }
        )
    )
    args = experiment.parser().parse_args(["child", "--request", str(request_path)])

    with pytest.raises(SystemExit, match="must stay under"):
        experiment.command_child(args)

    assert not outside_result.exists()

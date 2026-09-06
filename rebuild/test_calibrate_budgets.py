"""What `make job-costs` has to keep true: a pool record measures one worker apiece and a named step measures one unit, a step whose tree holds more than the pool it names measures nothing here, an overrun is only ever an overrun of this host's own rows measured since the commit that seeded the constant and inside the recency window, and a constant this box has never tested says so in those words instead of passing quietly. `_main` states no seed stamps, so a fixture's date never collides with the live tree's commits; the tests about the bound state their own. Every fixture peak is computed against the real constants rather than written as a literal, because the assertions are about a relation and a literal would fossilize today's constant into a test that has to survive re-seeding it."""

import json

import pytest

from rebuild.tools import calibrate_budgets as cb
from rebuild.tools import make_test_gate as mtg
from rebuild.tools import rebuild_gate as rg

HOST = "this.local"
OTHER = "other.local"
CONSTANTS = cb.read_constants()


def _unit(name):
    return next(unit for unit in cb.UNITS if unit.name == name)


def _pool(unit, peaks, *, host=HOST, at="2026-08-20T12:00:00Z", controller=400_000_000):
    return {
        "format": "ams-cycle-timings/1",
        "kind": "pool",
        "host": host,
        "unit": unit,
        "finished_at": at,
        "width": len(peaks),
        "controller_peak_rss_bytes": controller,
        "worker_peak_rss_bytes": {f"gw{index}": peak for index, peak in enumerate(peaks)},
    }


def _step(name, peak, *, host=HOST, at="2026-08-20T12:00:00Z", run="r1"):
    return {
        "format": "ams-cycle-timings/1",
        "kind": "step",
        "run": run,
        "host": host,
        "name": name,
        "argv": ["uv", "run", "fake"],
        "rc": 0,
        "elapsed_s": 1.0,
        "finished_at": at,
        "peak_rss_bytes": peak,
    }


def _journal(tmp_path, entries):
    path = tmp_path / "j.ndjson"
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    return path


def _main(path, *args, seed_stamps=None):
    return cb.main(["--journal", str(path), *args], seed_stamps={} if seed_stamps is None else seed_stamps)


def _run(capsys, path, *args):
    code = _main(path, "--host", HOST, *args)
    return code, capsys.readouterr().out


def test_every_unit_with_a_constant_names_one_its_source_file_actually_defines():
    named = {unit.name for unit in cb.UNITS if unit.constant is not None}
    assert set(CONSTANTS) == named
    assert all(isinstance(value, int) and value > 0 for value in CONSTANTS.values())
    with pytest.raises(RuntimeError):
        cb._int_constant(cb.ROOT / "conftest.py", "NO_SUCH_WORKER_BYTES")


def test_every_pool_name_a_gate_wrapper_sets_is_one_the_registry_reads():
    """The unit name is the join between a measurement and a constant, and it is written down on both sides of a boundary no import crosses: the wrappers stamp it onto a child's environment, and this registry looks for it in the journal. Nothing else would notice a name drifting — a pool filed under a name no unit claims simply never turns up, and a unit that never turns up reads exactly like a lane this box has not run yet, which is the report's one legitimately quiet state."""
    known = {name for unit in cb.UNITS for name in unit.pool_units}
    assert {mtg.POOL_UNIT, *rg.POOL_UNIT_BY_LANE.values()} <= known


def test_a_pool_record_supplies_one_observation_per_worker():
    record = _pool("font-suite", [1_000_000, 2_000_000, 3_000_000])
    observed, dropped, _ = cb.observations(_unit("font-suite"), [record], {}, host=HOST, recent=20)
    assert [item.peak_bytes for item in observed] == [1_000_000, 2_000_000, 3_000_000]
    assert {item.source for item in observed} == {"pool"}
    assert dropped == 0


def test_the_controllers_own_peak_is_never_one_of_the_workers_observations():
    record = _pool("font-suite", [1_000_000], controller=9_000_000)
    observed, _, _ = cb.observations(_unit("font-suite"), [record], {}, host=HOST, recent=20)
    assert [item.peak_bytes for item in observed] == [1_000_000]


def test_a_surface_pool_record_prices_the_worker_constant():
    """The surface build files its own pool records rather than a pytest controller's, and they land on the divisor's row alone: the parent is measured by a step peak and would be nonsense as a worker observation, so the two surface rows never read each other's evidence."""
    record = _pool("surface", [6_000_000_000, 7_000_000_000])
    observed, _, _ = cb.observations(_unit("surface-worker"), [record], {}, host=HOST, recent=20)
    assert [item.peak_bytes for item in observed] == [6_000_000_000, 7_000_000_000]
    assert {item.source for item in observed} == {"pool"}
    parent, _, _ = cb.observations(_unit("surface-parent"), [record], {}, host=HOST, recent=20)
    assert parent == []


def test_a_named_step_peak_supplies_an_observation():
    steps = {"r1": [_step("run_m1", 9_000_000_000)]}
    observed, _, _ = cb.observations(_unit("kernel-config"), [], steps, host=HOST, recent=20)
    assert [(item.peak_bytes, item.source) for item in observed] == [(9_000_000_000, "step:run_m1")]


def test_a_make_test_step_peak_is_never_read_as_a_font_suite_worker(tmp_path, capsys):
    path = _journal(tmp_path, [_step("gate:make-test", CONSTANTS["font-suite"] * 50)])
    code, out = _run(capsys, path, "--check")
    assert code == 0
    assert "OVERRUN" not in out
    assert "no observations" in out


def test_check_trips_when_an_observed_peak_outruns_its_constant(tmp_path, capsys):
    peak = CONSTANTS["font-suite"] * 2
    path = _journal(tmp_path, [_pool("font-suite", [peak])])
    code, out = _run(capsys, path, "--check")
    assert code == 1
    assert "OVERRUN" in out
    assert "FONT_SUITE_WORKER_BYTES" in out


def test_check_is_green_when_every_observed_peak_fits(tmp_path, capsys):
    path = _journal(
        tmp_path,
        [
            _pool("font-suite", [CONSTANTS["font-suite"] // 2]),
            _step("run_m1", CONSTANTS["kernel-config"] // 2),
        ],
    )
    code, out = _run(capsys, path, "--check")
    assert code == 0
    assert "OVERRUN" not in out
    assert "job costs: green" in out


def test_a_unit_with_no_observations_is_informational_rather_than_a_failure(tmp_path, capsys):
    path = _journal(tmp_path, [_pool("font-suite", [CONSTANTS["font-suite"] // 2])])
    code, out = _run(capsys, path, "--check")
    assert code == 0
    assert "no observations" in out


def test_a_unit_with_no_rows_from_this_host_says_the_constant_is_unverified_here(tmp_path, capsys):
    path = _journal(tmp_path, [_pool("font-suite", [CONSTANTS["font-suite"] // 2], host=OTHER)])
    code, out = _run(capsys, path)
    assert code == 0
    assert "UNVERIFIED HERE" in out
    assert "never which box a constant was sized on" in out


def test_rows_from_other_hosts_are_reported_and_never_checked(tmp_path, capsys):
    peak = CONSTANTS["font-suite"] * 3
    path = _journal(
        tmp_path,
        [
            _pool("font-suite", [peak], host=OTHER),
            _pool("font-suite", [CONSTANTS["font-suite"] // 2]),
        ],
    )
    code, out = _run(capsys, path, "--check")
    assert code == 0
    assert "OVERRUN" not in out
    assert "1 record from other hosts was not checked" in out


def test_host_all_checks_every_machine(tmp_path, capsys):
    peak = CONSTANTS["font-suite"] * 3
    path = _journal(
        tmp_path,
        [
            _pool("font-suite", [peak], host=OTHER),
            _pool("font-suite", [CONSTANTS["font-suite"] // 2]),
        ],
    )
    assert _main(path, "--host", "all", "--check") == 1
    assert "OVERRUN" in capsys.readouterr().out


def test_the_recency_bound_ages_out_a_peak_a_memory_saver_retired(tmp_path, capsys):
    over = CONSTANTS["font-suite"] * 2
    under = CONSTANTS["font-suite"] // 2
    path = _journal(
        tmp_path,
        [_pool("font-suite", [over], at=f"2026-01-0{day}T12:00:00Z") for day in (1, 2, 3)]
        + [_pool("font-suite", [under], at=f"2026-08-0{day}T12:00:00Z") for day in (1, 2)],
    )
    assert _main(path, "--host", HOST, "--recent", "2", "--check") == 0
    capsys.readouterr()
    assert _main(path, "--host", HOST, "--recent", "0", "--check") == 1
    assert "OVERRUN" in capsys.readouterr().out


def test_a_record_older_than_the_constants_commit_is_never_held_against_it(tmp_path, capsys):
    over = CONSTANTS["kernel-config"] * 3
    under = CONSTANTS["kernel-config"] // 2
    path = _journal(
        tmp_path,
        [_step("run_m1", over, at="2026-09-04T08:00:00Z", run="old")]
        + [_step("run_m1", under, at="2026-09-06T08:00:00Z", run="new")],
    )
    seeded = {"kernel-config": "2026-09-05T20:18:22Z"}
    assert _main(path, "--host", HOST, "--check", seed_stamps=seeded) == 0
    out = capsys.readouterr().out
    assert (
        "since     : 2026-09-05T20:18:22Z, the commit that set CONFIG_PEAK_BYTES to its current value; 1 older record set aside"
        in out
    )
    assert _main(path, "--host", HOST, "--check") == 1
    assert "has no commit yet" in capsys.readouterr().out


def test_the_archaeology_pass_reads_past_the_constants_commit(tmp_path, capsys):
    over = CONSTANTS["kernel-config"] * 3
    path = _journal(tmp_path, [_step("run_m1", over, at="2026-09-04T08:00:00Z")])
    seeded = {"kernel-config": "2026-09-05T20:18:22Z"}
    assert _main(path, "--host", HOST, "--check", seed_stamps=seeded) == 0
    capsys.readouterr()
    assert _main(path, "--host", HOST, "--check", "--recent", "0", seed_stamps=seeded) == 1


def test_a_record_with_no_stamp_is_kept_under_the_seed_bound():
    record = _step("run_m1", 1)
    del record["finished_at"]
    observed, _, older = cb.observations(
        _unit("kernel-config"), [], {"r1": [record]}, host=HOST, recent=20, since="2026-09-05T00:00:00Z"
    )
    assert [item.peak_bytes for item in observed] == [1]
    assert older == 0


def test_the_seed_stamp_is_the_committer_time_of_the_constants_own_line(tmp_path):
    import subprocess

    tree = tmp_path / "tree"
    (tree / "rebuild" / "pipeline").mkdir(parents=True)
    source = tree / "rebuild" / "pipeline" / "kernel_exec.py"
    source.write_text("OTHER = 1\nCONFIG_PEAK_BYTES = 4_000_000_000\n", encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_DATE": "2026-09-05T20:18:22Z",
        "GIT_AUTHOR_DATE": "2026-09-05T20:18:22Z",
        "PATH": __import__("os").environ["PATH"],
    }
    git = lambda *args: subprocess.run(["git", *args], cwd=tree, env=env, check=True, capture_output=True)
    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "seed")
    assert cb.constant_seeded_at(source, "CONFIG_PEAK_BYTES", root=tree) == "2026-09-05T20:18:22Z"
    source.write_text("OTHER = 1\nCONFIG_PEAK_BYTES = 3_000_000_000\n", encoding="utf-8")
    assert cb.constant_seeded_at(source, "CONFIG_PEAK_BYTES", root=tree) is None
    env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = "2026-09-06T01:02:03Z"
    git("commit", "-q", "-am", "re-seed")
    assert cb.constant_seeded_at(source, "CONFIG_PEAK_BYTES", root=tree) == "2026-09-06T01:02:03Z"


def test_a_tree_that_is_not_a_checkout_has_no_bound(tmp_path):
    source = tmp_path / "kernel_exec.py"
    source.write_text("CONFIG_PEAK_BYTES = 4_000_000_000\n", encoding="utf-8")
    assert cb.constant_seeded_at(source, "CONFIG_PEAK_BYTES", root=tmp_path) is None


def test_the_live_tree_dates_its_constants_in_the_journals_own_stamp_shape():
    import re

    stamps = cb.read_seed_stamps()
    priced = {unit.name for unit in cb.UNITS if unit.constant is not None}
    assert set(stamps) <= priced
    for stamp in stamps.values():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)


def test_the_recency_window_is_one_machines_worth_under_host_all(tmp_path, capsys):
    """A fleet survey has to be a survey of the fleet. One busy box cycling all day would otherwise fill a global window by itself, and the quiet machine whose peak has actually run away — the one nobody is watching — would drop out of the report without the report saying so."""
    over = CONSTANTS["font-suite"] * 2
    under = CONSTANTS["font-suite"] // 2
    path = _journal(
        tmp_path,
        [_pool("font-suite", [under], at=f"2026-08-0{day}T12:00:00Z") for day in (1, 2, 3, 4)]
        + [_pool("font-suite", [over], at="2026-01-01T12:00:00Z", host=OTHER)],
    )
    assert _main(path, "--host", "all", "--recent", "2", "--check") == 1
    assert "OVERRUN" in capsys.readouterr().out


def test_a_fleet_survey_never_claims_a_constant_is_unverified_here(tmp_path, capsys):
    """A box is what "here" means, and --host all names none. With nothing measured anywhere the observed line has already said the whole of what is known, and a sentence about this host's missing rows would be answering a question nobody asked."""
    path = _journal(tmp_path, [_step("gate:make-test", CONSTANTS["font-suite"] * 50)])
    assert _main(path, "--host", "all", "--check") == 0
    out = capsys.readouterr().out
    assert "UNVERIFIED HERE" not in out
    assert "no observations in the journal" in out


def test_the_unmeasured_lane_is_reported_and_never_checked(tmp_path, capsys):
    path = _journal(tmp_path, [_pool("rebuild-contracts", [40_000_000_000])])
    code, out = _run(capsys, path, "--check")
    assert code == 0
    assert "no constant — deliberately unmeasured" in out
    assert _unit("rebuild-contracts").note in out
    assert "40.00 GB" in out


def test_tolerance_admits_a_peak_that_only_just_exceeds_the_constant(tmp_path, capsys):
    peak = int(CONSTANTS["font-suite"] * 1.05)
    path = _journal(tmp_path, [_pool("font-suite", [peak])])
    assert _main(path, "--host", HOST, "--check") == 1
    capsys.readouterr()
    assert _main(path, "--host", HOST, "--check", "--tolerance", "0.1") == 0


def test_a_missing_journal_reads_as_nothing_measured_rather_than_an_error(tmp_path, capsys):
    code, out = _run(capsys, tmp_path / "absent.ndjson", "--check")
    assert code == 0
    assert "nothing measured" in out


def test_the_report_states_the_width_each_constant_implies_here(tmp_path, capsys):
    path = _journal(tmp_path, [_pool("font-suite", [CONSTANTS["font-suite"] // 2])])
    _, out = _run(capsys, path)
    assert "width here" in out
    assert "GB each out of" in out


def test_the_width_clauses_answer_for_the_box_and_the_tree_they_are_given(tmp_path, capsys):
    """A report about a machine this suite is not running on, read out of a tree it is not checked out of: both are keywords precisely so the arithmetic can be asserted against a box and a cap someone stated rather than against whichever ones the runner happens to have."""
    tree = tmp_path / "tree"
    (tree / "rebuild" / "tools").mkdir(parents=True)
    (tree / "rebuild" / "tools" / "artifact_cycle.py").write_text(
        f"{cb.SURFACE_CAP_NAME} = 3\n{cb.SURFACE_PARENT_NAME} = 10_000_000_000\n{cb.SURFACE_WORKER_NAME} = 5_000_000_000\n",
        encoding="utf-8",
    )
    rows = cb.build_rows([], {}, constants=CONSTANTS, host=HOST, recent=20, tolerance=0.0, seeded_at={})
    out = "\n".join(cb.render_rows(rows, host=HOST, total_bytes=48_000_000_000, cores=12, root=tree))
    assert "48.00 GB total" in out
    assert "capped at 3" in out
    assert "the font suite takes the cores this process may run on (12), not the division" in out
    assert "the surface build's parent is subtracted from the box rather than divided into it" in out


def test_a_check_that_cannot_run_exits_apart_from_one_that_tripped(tmp_path, capsys, monkeypatch):
    """The cycle reads exit 1 as a measured overrun and diffs the constants on it. A renamed constant raises here on purpose — a calibration silently not performed is the failure this tool exists to prevent — so that raise must not arrive at the cycle wearing the verdict's exit code."""

    def boom(*args, **kwargs):
        raise RuntimeError("conftest.py defines no FONT_SUITE_WORKER_BYTES")

    monkeypatch.setattr(cb, "read_constants", boom)
    path = _journal(tmp_path, [_pool("font-suite", [CONSTANTS["font-suite"] // 2])])
    assert _main(path, "--host", HOST, "--check") == 2
    assert "FONT_SUITE_WORKER_BYTES" in capsys.readouterr().err


def test_an_overrun_that_only_just_clears_the_constant_never_reads_as_no_overrun(tmp_path, capsys):
    """The margin rounds to whole percent, and a peak a hair past its constant is the common trip — these constants are chosen with headroom, so reaching one at all is the event. A line reading "by 0%" beside a nonzero exit would argue against the exit."""
    peak = int(CONSTANTS["font-suite"] * 1.003) + 1
    path = _journal(tmp_path, [_pool("font-suite", [peak])])
    code, out = _run(capsys, path, "--check")
    assert code == 1
    assert "by less than 1%" in out
    assert "by 0%" not in out


def test_the_default_view_never_exits_nonzero(tmp_path, capsys):
    path = _journal(tmp_path, [_pool("font-suite", [CONSTANTS["font-suite"] * 2])])
    code, out = _run(capsys, path)
    assert code == 0
    assert "OVERRUN" in out

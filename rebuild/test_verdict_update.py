"""Tests for `rebuild/tools/verdict_update.py`: the sufficient duplicate, standing, duplicate schedule agrees with a bounded-loop reference over real fill, merge, carry, and complaint operations. The update reuses duplicate projections, streams full human records to their readers, preserves first proposals when clears refuse them, and announces completion only after every requested phase succeeds."""

import json
import pathlib

import pytest

from rebuild.tools import console, verdict_update as vu

STAMP = "S1"


def _payload():
    return {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": STAMP,
        "exported_at": STAMP,
        "verdicts": [],
    }


def _write_out(argv):
    pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(_payload()))
    return 0


def test_a_step_opens_a_phase_and_the_timing_that_follows_closes_it(capsys):
    """Each step prints a `[phase]` line naming it and then a `[t]` line with the same label and its duration, which `console.CycleConsole` matches by label. A failed step also prints a `[verdict-update] failed:` line; that prefix marks a result, and `verdict_update_sections` in `rebuild/tools/artifact_cycle.py` splits the verdict update's output on it."""
    assert vu._run("carry", lambda: 0) == 0
    assert vu._run("merge", lambda: 3) == 3
    lines = capsys.readouterr().out.splitlines()
    events = [console.parse_line(line) for line in lines]
    assert [event.name for event in events if isinstance(event, console.Phase)] == ["carry", "merge"]
    assert [event.label for event in events if isinstance(event, console.Timing)] == ["carry", "merge"]
    assert lines[-1] == f"{console.FAILED_LINE}merge (exit 3)"


IDS = frozenset({"u-1", "u-2", "u-machine"})


def _run_verdict_update(tmp_path, monkeypatch, extra=(), complaints=False):
    """Run `verdict_update.main` over a stub corpus with every step stubbed. Return the exit code, the human index records, the standing fill's calls, the fill's `--out` path, the complaint list's calls (empty unless `complaints` is set), and the units each duplicate fill received."""
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    master = tmp_path / "master.json"
    master.write_text(json.dumps(_payload()))
    index = [{"id": "u-1", "after": {"cells": [1]}}, {"id": "u-2"}]
    calls = []
    complaint_list_calls = []
    duplicate_calls = []

    def stream(_corpus, *, unit_ids=None):
        if unit_ids is not None:
            unit_ids.update(IDS)
        yield from (dict(unit) for unit in index)

    monkeypatch.setattr(vu.unit_index, "iter_human_units", stream)
    monkeypatch.setattr(vu.merge_verdicts, "main", lambda _argv: 0)

    def duplicate_fill(argv, units=None):
        duplicate_calls.append(units)
        return _write_out(argv)

    monkeypatch.setattr(vu.duplicate_verdicts, "main", duplicate_fill)

    def standing(argv, unit_source=None):
        assert unit_source is not None
        first, second = unit_source(), unit_source()
        assert first is not second
        assert list(first) == list(second) == index
        calls.append((argv, unit_source))
        return _write_out(argv)

    monkeypatch.setattr(vu.standing_verdicts, "main", standing)

    def fake_complaint_list(argv, units=None, unit_ids=None):
        assert units is not None
        complaint_list_calls.append((argv, list(units), unit_ids))
        return 0

    monkeypatch.setattr(vu.complaint_list, "main", fake_complaint_list)

    standing_out = tmp_path / "verdicts-standing-fill.json"
    code = vu.main(
        [
            "--corpus",
            str(corpus),
            "--merge-master",
            str(master),
            "--autosave",
            str(tmp_path / "verdicts-autosave.json"),
            "--journal",
            str(tmp_path / "verdicts-journal.ndjson"),
            "--duplicate-out",
            str(tmp_path / "verdicts-duplicate-fill.json"),
            "--standing-out",
            str(standing_out),
            "--rules",
            str(tmp_path / "standing-approvals.yaml"),
            *([] if complaints else ["--no-complaints"]),
            *extra,
        ]
    )
    return code, index, calls, standing_out, complaint_list_calls, duplicate_calls


def test_the_verdict_update_runs_the_standing_fill_in_its_open_only_form(tmp_path, monkeypatch):
    """The verdict update passes `--open-only --require-reach` and leaves the narrowing to the standing fill, so it still supplies fresh streams over all human records. The default memo sits beside the corpus directory, outside it, so a corpus rebuild does not delete it."""
    code, index, calls, standing_out, complaint_list_calls, _duplicate_calls = _run_verdict_update(
        tmp_path, monkeypatch
    )
    assert code == 0
    assert complaint_list_calls == []
    [(argv, units)] = calls
    assert "--open-only" in argv
    assert "--require-reach" in argv
    assert argv[argv.index("--out") + 1] == str(standing_out)
    assert argv[argv.index("--memo") + 1] == str(tmp_path / vu.standing_verdicts.MEMO_NAME)
    assert "--fresh-memo" not in argv
    assert argv[argv.index("--jobs") + 1] == "1"
    assert list(units()) == index


def test_the_duplicate_fill_takes_the_human_records_and_the_complaint_list_takes_the_id_set_beside_them(
    tmp_path, monkeypatch
):
    """Both duplicate fills get the same list of human duplicate records, because the duplicate fill reads nothing from machine records. The complaint list gets the human records and the set of every corpus id, because its absent-unit warning checks verdicts against machine units too."""
    code, index, _calls, _out, complaint_list_calls, duplicate_calls = _run_verdict_update(
        tmp_path, monkeypatch, complaints=True
    )
    assert code == 0
    assert len(duplicate_calls) == 2
    assert all(units is duplicate_calls[0] for units in duplicate_calls)
    assert duplicate_calls[0] == [vu.duplicate_verdicts.duplicate_record(unit) for unit in index]
    [(argv, units, unit_ids)] = complaint_list_calls
    assert argv[argv.index("--corpus") + 1] == str(tmp_path / "review")
    assert units == index
    assert unit_ids == IDS


def test_the_verdict_update_forwards_the_cycles_standing_fill_width(tmp_path, monkeypatch):
    """The verdict update passes `--standing-fill-jobs` to the standing fill as `--jobs` and computes no width of its own."""
    code, _index, calls, _out, _complaint_list_calls, _duplicate_calls = _run_verdict_update(
        tmp_path, monkeypatch, ("--standing-fill-jobs", "6")
    )
    assert code == 0
    [(argv, _units)] = calls
    assert argv[argv.index("--jobs") + 1] == "6"


def test_the_verdict_update_passes_a_named_memo_and_the_fresh_form_through(tmp_path, monkeypatch):
    """The verdict update passes `--standing-memo` to the fill as `--memo`, and `--fresh-standing-memo` (which the cycle sets under `--fresh`) as `--fresh-memo`."""
    memo = tmp_path / "elsewhere" / "memo.ndjson.gz"
    code, _index, calls, _out, _complaint_list_calls, _duplicate_calls = _run_verdict_update(
        tmp_path, monkeypatch, ("--standing-memo", str(memo), "--fresh-standing-memo")
    )
    assert code == 0
    [(argv, _units)] = calls
    assert argv[argv.index("--memo") + 1] == str(memo)
    assert "--fresh-memo" in argv


def _carrying_verdict_update(tmp_path, monkeypatch, extra=()):
    """Run the verdict update with `--verdicts` and `--carry-out`, with every step stubbed. Return the exit code, the human index records, the carry's calls, and the merge's calls."""
    corpus = tmp_path / "review"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({**_payload(), "manifest_generated_at": "S0"}))
    index = [{"id": "u-DdcTojn1hba"}]
    carries = []
    merges = []

    def stream(_corpus, *, unit_ids=None):
        if unit_ids is not None:
            unit_ids.update(IDS)
        yield from (dict(unit) for unit in index)

    monkeypatch.setattr(vu.unit_index, "iter_human_units", stream)
    monkeypatch.setattr(
        vu.carry_verdicts,
        "main",
        lambda argv, current_units=None, current_ids=None: carries.append((argv, current_units, current_ids))
        or 0,
    )
    monkeypatch.setattr(vu.merge_verdicts, "main", lambda argv: merges.append(argv) or 0)
    monkeypatch.setattr(vu.duplicate_verdicts, "main", lambda argv, units=None: _write_out(argv))
    monkeypatch.setattr(vu.standing_verdicts, "main", lambda argv, unit_source=None: _write_out(argv))
    code = vu.main(
        [
            "--corpus",
            str(corpus),
            "--verdicts",
            str(verdicts),
            "--carry-out",
            str(tmp_path / "carried.json"),
            "--autosave",
            str(tmp_path / "verdicts-autosave.json"),
            "--journal",
            str(tmp_path / "verdicts-journal.ndjson"),
            "--duplicate-out",
            str(tmp_path / "verdicts-duplicate-fill.json"),
            "--standing-out",
            str(tmp_path / "verdicts-standing-fill.json"),
            "--rules",
            str(tmp_path / "standing-approvals.yaml"),
            "--no-complaints",
            *extra,
        ]
    )
    return code, index, carries, merges


def test_the_carry_step_hands_the_verdicts_file_and_the_loaded_index_to_the_carry(tmp_path, monkeypatch):
    """The verdict update passes the carry the verdicts file, the human duplicate records, and every corpus id, because the orphaned count also checks machine ids. It names only the live corpus as `--current-corpus`, then merges the carried file."""
    code, index, carries, merges = _carrying_verdict_update(tmp_path, monkeypatch)
    assert code == 0
    [(argv, units, unit_ids)] = carries
    assert argv[: argv.index("--out")] == ["--verdicts", str(tmp_path / "verdicts.json")]
    assert argv[argv.index("--out") + 1] == str(tmp_path / "carried.json")
    assert argv[argv.index("--current-corpus") + 1] == str(tmp_path / "review")
    assert units == [vu.duplicate_verdicts.duplicate_record(unit) for unit in index]
    assert unit_ids == IDS
    assert merges[0][0] == str(tmp_path / "carried.json")


def test_the_staging_form_stops_after_the_carry(tmp_path, monkeypatch, capsys):
    """`--no-merge` never writes the live store: the carry runs and nothing after it does."""
    code, _index, carries, merges = _carrying_verdict_update(tmp_path, monkeypatch, ("--no-merge",))
    assert code == 0
    assert len(carries) == 1
    assert merges == []
    assert console.COMPLETE_LINE not in capsys.readouterr().out


SCHEDULE = [
    "duplicate-fill",
    "duplicate-merge",
    "standing-fill",
    "standing-merge",
    "duplicate-fill-2",
    "duplicate-merge-2",
]


@pytest.mark.parametrize("complaints", [False, True])
def test_the_complete_schedule_merges_even_empty_duplicate_fills(tmp_path, monkeypatch, capsys, complaints):
    code, *_ = _run_verdict_update(tmp_path, monkeypatch, complaints=complaints)
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert [line.removeprefix("[phase] ") for line in lines if line.startswith("[phase] ")] == [
        "merge",
        *SCHEDULE,
        *(["complaints"] if complaints else []),
    ]
    assert lines[-1] == console.COMPLETE_LINE + "duplicate, standing, duplicate"


@pytest.mark.parametrize("failed_phase", ["merge", *SCHEDULE, "complaints"])
def test_a_failed_phase_never_prints_completion(tmp_path, monkeypatch, capsys, failed_phase):
    run = vu._run

    def failing_run(name, call):
        return run(name, lambda: 7) if name == failed_phase else run(name, call)

    monkeypatch.setattr(vu, "_run", failing_run)
    code, *_ = _run_verdict_update(tmp_path, monkeypatch, complaints=True)
    lines = capsys.readouterr().out.splitlines()
    assert code == 7
    assert lines[-1] == console.FAILED_LINE + failed_phase + " (exit 7)"
    assert not any(line.startswith(console.COMPLETE_LINE) for line in lines)


def test_a_failed_carry_never_prints_completion(tmp_path, monkeypatch, capsys):
    run = vu._run
    monkeypatch.setattr(vu, "_run", lambda name, call: run(name, lambda: 7))
    code, *_ = _carrying_verdict_update(tmp_path, monkeypatch)
    assert code == 7
    assert console.COMPLETE_LINE not in capsys.readouterr().out


@pytest.mark.parametrize("exit_code, expected", [(3, 3), ("bad manifest", 1)])
def test_system_exit_from_a_phase_is_reported_as_failure(capsys, exit_code, expected):
    def exit_phase():
        raise SystemExit(exit_code)

    assert vu._run("duplicate-fill", exit_phase) == expected
    captured = capsys.readouterr()
    assert console.FAILED_LINE + f"duplicate-fill (exit {expected})" in captured.out
    if isinstance(exit_code, str):
        assert captured.err.strip() == exit_code


CORPUS_STAMP = "2026-07-10T00:00:00Z"
OLDER = "2026-07-08T00:00:00Z"
PREVIOUS = "2026-07-09T00:00:00Z"
NEWER = "2026-07-11T00:00:00Z"
MATCHING_DELTA = "d-111111111111"
OTHER_DELTA = "d-222222222222"
POINTER = "glyph_data/runes/qsPea.yaml:policies[0]"


def _record(unit, verdict="approve", at=PREVIOUS, note="human note"):
    return {"unit": unit, "verdict": verdict, "note": note, "at": at}


def _fixture_payload(records, stamp=CORPUS_STAMP):
    return {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": sorted(records, key=lambda record: record["unit"]),
    }


def _unit(unit, group=None, *, matching=False, excluded=False):
    return {
        "id": unit,
        "class": "fixture",
        "codepoints": [0xE650],
        "notation": unit,
        "duplicate_group": group,
        "configs": ["default"],
        "ink_deltas": {"default": MATCHING_DELTA if matching else OTHER_DELTA},
        "render_groups": [{}],
        "before": {"glyphs": ["qsOut" if excluded else "qsPea"], "junctions": []},
        "after": {"cells": ["qsPea/sole/None/None/"], "junctions": []},
        "provenance": [POINTER],
    }


def _integration_fixture(root, monkeypatch):
    """A synthetic corpus exercises both propagation sources, conflict and skip handling, standing guards, human precedence, and clears that reject a proposal or accept a newer standing proposal. The operations read the corpus's real index, fill files, store, journal, and complaint data; only the network probe and font-free memo environment are fixed."""
    root.mkdir()
    paths = {
        name: root / filename
        for name, filename in {
            "corpus": "review",
            "master": "master.json",
            "carry": "carried.json",
            "autosave": "verdicts-autosave.json",
            "journal": "journal.ndjson",
            "duplicate": "duplicate.json",
            "standing": "standing.json",
            "rules": "rules.yaml",
            "memo": "memo.ndjson.gz",
            "complaints": "complaints.json",
        }.items()
    }
    corpus = paths["corpus"]
    (corpus / "units").mkdir(parents=True)
    units = [
        _unit("u-mix-a", "mixed"),
        _unit("u-mix-b", "mixed"),
        _unit("u-mix-blank", "mixed"),
        _unit("u-reject", "rejected"),
        _unit("u-reject-blank", "rejected"),
        _unit("u-conflict-a", "conflict"),
        _unit("u-conflict-b", "conflict"),
        _unit("u-conflict-blank", "conflict"),
        _unit("u-standing", "standing", matching=True),
        _unit("u-standing-sibling", "standing"),
        _unit("u-skip", "skipped", matching=True),
        _unit("u-skip-source", "skipped"),
        _unit("u-skip-blank", "skipped"),
        _unit("u-exception", matching=True, excluded=True),
        _unit("u-human", matching=True),
        _unit("u-cleared-source", "cleared"),
        _unit("u-cleared-standing", "cleared", matching=True),
        _unit("u-cleared-newer", "cleared"),
        _unit("u-open"),
        _unit("u-machine"),
    ]
    manifest = {
        "generated_at": CORPUS_STAMP,
        "batch_size": 10,
        "human_unit_ids": [unit["id"] for unit in units if unit["id"] != "u-machine"],
        "classes": [{"id": "fixture", "shards": ["units/fixture.json"]}],
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    (corpus / "units" / "fixture.json").write_text(json.dumps(units))
    vu.unit_index.write_index(corpus, [("fixture", units)])
    records = [
        _record("u-mix-a", at=OLDER),
        _record("u-mix-b", "identical", note="latest mixed-group note"),
        _record("u-reject", "reject", note="[reviewed] shared complaint"),
        _record("u-conflict-a", at=OLDER),
        _record("u-conflict-b", "reject"),
        _record("u-skip", "skip", at=NEWER, note="deferred by human"),
        _record("u-skip-source"),
        _record("u-human", "reject", at=NEWER, note="human rejects the standing rule"),
        _record("u-cleared-source", at=OLDER, note="old source"),
        _record("u-machine", "approve"),
    ]
    payload = _fixture_payload(records)
    payload["cleared"] = [
        {"unit": "u-cleared-standing", "at": PREVIOUS},
        {"unit": "u-cleared-newer", "at": NEWER},
    ]
    paths["autosave"].write_text(json.dumps(payload))
    paths["master"].write_text(json.dumps(_fixture_payload(records)))
    paths["rules"].write_text(
        json.dumps(
            {
                "format": vu.standing_verdicts.FORMAT,
                "rules": [
                    {
                        "id": "fixture-ink-delta",
                        "verdict": "approve",
                        "note": "approved fixture ink delta",
                        "match": {"after": {"ink_deltas": [MATCHING_DELTA]}, "except_left": ["qsOut"]},
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(vu.merge_verdicts, "_server_listening", lambda: False)
    monkeypatch.setattr(vu.standing_verdicts, "memo_environment", lambda corpus: ("fixture", {}))
    return paths


def _standing_argv(paths):
    return [
        str(paths["autosave"]),
        "--corpus",
        str(paths["corpus"]),
        "--out",
        str(paths["standing"]),
        "--rules",
        str(paths["rules"]),
        "--memo",
        str(paths["memo"]),
        "--open-only",
        "--require-reach",
    ]


def _reference_update(paths, carrying=False):
    """The bounded-loop reference uses the real fill and merge operations, keeps the first proposal per unit, and stops when a later duplicate pass proposes no previously unseen unit. It includes the extra empty confirmation pass without depending on git history or the updater's schedule."""
    ids = set()
    units = [
        vu.duplicate_verdicts.duplicate_record(unit)
        for unit in vu.unit_index.iter_human_units(paths["corpus"], unit_ids=ids)
    ]

    def merge(path):
        assert (
            vu.merge_verdicts.main(
                [
                    str(path),
                    "--autosave",
                    str(paths["autosave"]),
                    "--corpus",
                    str(paths["corpus"]),
                    "--journal",
                    str(paths["journal"]),
                ]
            )
            == 0
        )

    if carrying:
        assert (
            vu.carry_verdicts.main(
                [
                    "--verdicts",
                    str(paths["master"]),
                    "--out",
                    str(paths["carry"]),
                    "--current-corpus",
                    str(paths["corpus"]),
                ],
                current_units=units,
                current_ids=ids,
            )
            == 0
        )
    merge(paths["carry"] if carrying else paths["master"])
    fills = {}
    duplicate_passes = 0
    for round_ in range(4):
        assert (
            vu.duplicate_verdicts.main(
                [
                    str(paths["autosave"]),
                    "--corpus",
                    str(paths["corpus"]),
                    "--out",
                    str(paths["duplicate"]),
                ],
                units=units,
            )
            == 0
        )
        duplicate_passes += 1
        proposed = json.loads(paths["duplicate"].read_text())["verdicts"]
        fresh = [record for record in proposed if record["unit"] not in fills]
        fills.update((record["unit"], record) for record in fresh)
        paths["duplicate"].write_text(json.dumps(_fixture_payload(list(fills.values()))))
        if round_ and not fresh:
            break
        merge(paths["duplicate"])
        if round_ == 0:
            assert (
                vu.standing_verdicts.main(
                    _standing_argv(paths), unit_source=lambda: vu.unit_index.iter_human_units(paths["corpus"])
                )
                == 0
            )
            merge(paths["standing"])
    assert (
        vu.complaint_list.main(
            [
                str(paths["autosave"]),
                "--corpus",
                str(paths["corpus"]),
                "--data-out",
                str(paths["complaints"]),
            ],
            units=vu.unit_index.iter_human_units(paths["corpus"]),
            unit_ids=ids,
        )
        == 0
    )
    return duplicate_passes


def _actual_update(paths, carrying=False, extra=()):
    return vu.main(
        [
            "--corpus",
            str(paths["corpus"]),
            "--autosave",
            str(paths["autosave"]),
            "--journal",
            str(paths["journal"]),
            "--duplicate-out",
            str(paths["duplicate"]),
            "--standing-out",
            str(paths["standing"]),
            "--rules",
            str(paths["rules"]),
            "--standing-memo",
            str(paths["memo"]),
            "--complaints-out",
            str(paths["complaints"]),
            *(
                ["--verdicts", str(paths["master"]), "--carry-out", str(paths["carry"])]
                if carrying
                else ["--merge-master", str(paths["master"])]
            ),
            *extra,
        ]
    )


def _records(path):
    return {record["unit"]: record for record in json.loads(path.read_text())["verdicts"]}


@pytest.mark.parametrize("carrying", [False, True])
def test_the_schedule_matches_the_bounded_reference_with_real_operations(
    tmp_path, monkeypatch, capsys, carrying
):
    reference = _integration_fixture(tmp_path / "reference", monkeypatch)
    current = _integration_fixture(tmp_path / "current", monkeypatch)
    if carrying:
        for paths in (reference, current):
            payload = json.loads(paths["master"].read_text())
            payload["manifest_generated_at"] = OLDER
            paths["master"].write_text(json.dumps(payload))
    assert _reference_update(reference, carrying) == 3
    capsys.readouterr()
    assert _actual_update(current, carrying) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.removeprefix("[phase] ") for line in lines if line.startswith("[phase] ")] == [
        *(["carry"] if carrying else []),
        "merge",
        *SCHEDULE,
        "complaints",
    ]
    assert lines[-1] == console.COMPLETE_LINE + "duplicate, standing, duplicate"
    assert "fixpoint" not in "\n".join(lines)
    for name in ("duplicate", "standing", "complaints"):
        assert json.loads(current[name].read_text()) == json.loads(reference[name].read_text())
    assert _records(current["autosave"]) == _records(reference["autosave"])
    assert json.loads(current["autosave"].read_text())["cleared"] == [
        {"unit": "u-cleared-newer", "at": NEWER},
    ]

    accepted = _records(current["autosave"])
    duplicate = _records(current["duplicate"])
    standing = _records(current["standing"])
    assert accepted["u-mix-blank"] == _record(
        "u-mix-blank", "identical", note="[duplicate-fill from u-mix-b] latest mixed-group note"
    )
    assert accepted["u-standing-sibling"] == _record(
        "u-standing-sibling",
        at=CORPUS_STAMP,
        note="[duplicate-fill from u-standing] " + standing["u-standing"]["note"],
    )
    assert accepted["u-cleared-standing"] == standing["u-cleared-standing"]
    assert duplicate["u-cleared-standing"] == _record(
        "u-cleared-standing", at=OLDER, note="[duplicate-fill from u-cleared-source] old source"
    )
    assert duplicate["u-cleared-newer"] == _record(
        "u-cleared-newer", at=OLDER, note="[duplicate-fill from u-cleared-source] old source"
    )
    assert "u-cleared-newer" not in accepted
    assert "u-exception" not in accepted
    assert "u-conflict-blank" not in accepted
    assert accepted["u-human"] == _record(
        "u-human", "reject", at=NEWER, note="human rejects the standing rule"
    )
    assert accepted["u-skip"] == _record("u-skip", "skip", at=NEWER, note="deferred by human")
    assert accepted["u-skip-blank"]["verdict"] == "approve"
    complaint = next(
        group
        for group in json.loads(current["complaints"].read_text())["groups"]
        if POINTER in group["pointers"]
    )
    assert "u-open" in complaint["defer_candidates"]["unit_ids"]
    assert any(entry["unit"] == "u-reject-blank" for part in complaint["rejects"].values() for entry in part)
    assert list(duplicate) == sorted(duplicate)

    repeated = current["duplicate"].with_name("refused-proposals.json")
    assert (
        vu.duplicate_verdicts.main(
            [
                str(current["autosave"]),
                "--corpus",
                str(current["corpus"]),
                "--out",
                str(repeated),
            ]
        )
        == 0
    )
    proposed_again = _records(repeated)["u-cleared-newer"]
    assert proposed_again["at"] == CORPUS_STAMP
    assert (
        proposed_again["note"]
        == "[duplicate-fill from u-cleared-standing] " + standing["u-cleared-standing"]["note"]
    )
    assert proposed_again != duplicate["u-cleared-newer"]

    before = current["autosave"].read_bytes()
    assert _actual_update(current, carrying) == 0
    assert current["autosave"].read_bytes() == before
    assert (
        capsys.readouterr().out.splitlines()[-1] == console.COMPLETE_LINE + "duplicate, standing, duplicate"
    )


@pytest.mark.parametrize("failure", ["manifest", "rules", "reach"])
def test_real_operation_refusals_leave_the_schedule_incomplete(tmp_path, monkeypatch, capsys, failure):
    paths = _integration_fixture(tmp_path / "current", monkeypatch)
    extra = ()
    if failure == "manifest":
        payload = json.loads(paths["autosave"].read_text())
        payload["manifest_generated_at"] = OLDER
        paths["autosave"].write_text(json.dumps(payload))
        extra = ("--merge-master", str(paths["autosave"]))
    else:
        payload = json.loads(paths["rules"].read_text())
        if failure == "rules":
            payload["rules"][0]["verdict"] = "reject"
        else:
            payload["rules"][0]["match"]["after"]["ink_deltas"] = ["d-333333333333"]
        paths["rules"].write_text(json.dumps(payload))
    assert _actual_update(paths, extra=extra) == 1
    captured = capsys.readouterr()
    assert console.FAILED_LINE in captured.out
    assert console.COMPLETE_LINE not in captured.out
    assert not paths["complaints"].exists()


def test_the_real_carry_only_form_writes_no_store_or_fills(tmp_path, monkeypatch, capsys):
    paths = _integration_fixture(tmp_path / "current", monkeypatch)
    before = paths["autosave"].read_bytes()
    assert _actual_update(paths, carrying=True, extra=("--no-merge",)) == 0
    assert paths["autosave"].read_bytes() == before
    assert paths["carry"].exists()
    assert not any(paths[name].exists() for name in ("duplicate", "standing", "complaints", "journal"))
    assert console.COMPLETE_LINE not in capsys.readouterr().out


def test_the_real_no_complaints_form_completes_the_fills(tmp_path, monkeypatch, capsys):
    paths = _integration_fixture(tmp_path / "current", monkeypatch)
    assert _actual_update(paths, extra=("--no-complaints",)) == 0
    assert "u-standing-sibling" in _records(paths["autosave"])
    assert not paths["complaints"].exists()
    assert (
        capsys.readouterr().out.splitlines()[-1] == console.COMPLETE_LINE + "duplicate, standing, duplicate"
    )

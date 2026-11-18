import io
from types import SimpleNamespace

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, kernel_io, spec_load, table
from rebuild.tools import scaling_sweep


def _row(runes, letters, windows, cpu, subtables=None):
    return {"runes": runes, "letters": letters, "windows": windows, "cpu": cpu, "settle_subtables": subtables}


def test_report_prints_na_for_a_pair_with_the_same_rune_count(capsys):
    scaling_sweep.report([_row(40, 30, 1000, 10.0), _row(41, 31, 1200, 12.0), _row(41, 31, 1200, 12.5)])
    lines = capsys.readouterr().out.splitlines()
    assert "41->41   windows   n/a   cpu   n/a   subtables   n/a" in lines
    assert any(line.startswith("whole-series fit over 3 sizes") for line in lines)


def test_report_fits_the_settlement_subtables_beside_the_windows_and_the_cpu(capsys):
    """N gets a pair exponent and a whole-series fit as the windows and the CPU time do. A size whose font was not built has no N, so its pair prints `n/a` and the fit leaves it out."""
    scaling_sweep.report(
        [_row(40, 30, 1000, 10.0, 2000), _row(42, 32, 1200, 12.0, 2400), _row(44, 34, 1400, 14.0, None)]
    )
    lines = capsys.readouterr().out.splitlines()
    assert "40->42   windows  3.74   cpu  3.74   subtables  3.74" in lines
    assert "42->44   windows  3.31   cpu  3.31   subtables   n/a" in lines
    assert any(line.startswith("  subtables ~ runes^3.74  letters^2.83") for line in lines)


def test_a_sizes_font_figures_come_from_a_whole_set_build_and_its_read_back(monkeypatch, tmp_path):
    """The shipped settlement lookup folds every settlement configuration, so the font is built over the whole set, not the timed child's `default` alone. N is the format-2 and format-3 subtable counts summed."""
    seen = {}

    def build_font(spec, out_dir, configs):
        seen["configs"] = configs
        return SimpleNamespace(
            readback={
                "checked": {
                    "gsub_budget": {"subtable_offset_headroom": 27_997, "largest_group_rule_bytes": 33_542},
                    "settle_subtable_formats": {"format2": 2_298, "format3": 927},
                }
            }
        )

    monkeypatch.setattr(scaling_sweep.scratch_build, "build_font", build_font)
    assert scaling_sweep.font_figures(fixtures.mini_spec(), tmp_path) == {
        "settle_subtables": 3_225,
        "subtable_offset_headroom": 27_997,
        "largest_group_rule_bytes": 33_542,
    }
    assert seen["configs"] == list(conform.SETTLEMENT_CONFIGS)


def _spec_path(tmp_path):
    """The mini fixture narrowed to ·Tea, ·May and ·It, dumped for the crate, which is built first."""
    spec_path = tmp_path / "spec.json"
    kernel_io.write_spec(spec_load.narrowed(fixtures.mini_spec(), ("qsTea", "qsMay", "qsIt")), spec_path)
    kernel_exec.ensure_built()
    return spec_path


def test_a_size_counts_the_same_through_a_pipe_as_from_the_kept_payload(monkeypatch, tmp_path):
    """A size counted through a pipe, as the sweep runs without `AMS_SCALING_DUMP`, gets the digest a kept run gets and the windows, rules and cells the payload that run wrote as a file holds, read back whole, and the crate's `windows_bytes` is that file's size. The piped run names the crate a `/dev/fd/` path for the payload, and nothing stands at the payload's path while the crate writes it or after, where the kept run leaves the file. The size is the mini fixture narrowed to ·Tea, ·May and ·It."""
    spec_path = _spec_path(tmp_path)
    piped = kernel_exec.windows_payload(tmp_path / "piped", "default")
    seen = {}
    scan, run_size = scaling_sweep.scan, scaling_sweep.run_size

    def scanning(source):
        source.peek(1)
        seen.setdefault("while writing", piped.exists() or piped.is_symlink())
        return scan(source)

    def running(binary, spec_path, out_dir, stamp, payloads=None):
        seen.setdefault("payload path", None if payloads is None else payloads.path("default"))
        return run_size(binary, spec_path, out_dir, stamp, payloads)

    monkeypatch.setattr(scaling_sweep, "scan", scanning)
    monkeypatch.setattr(scaling_sweep, "run_size", running)
    measured = {}
    for keep in (False, True):
        out_dir = tmp_path / ("kept" if keep else "piped")
        run, counts = scaling_sweep.measure(kernel_exec.BINARY, spec_path, out_dir, "sweep-stamp", keep)
        measured[keep] = (run["digest"], counts)
        if keep:
            assert run["windows_bytes"] == kernel_exec.windows_payload(out_dir, "default").stat().st_size
    assert measured[False] == measured[True]
    kept = kernel_exec.windows_payload(tmp_path / "kept", "default")
    with kept.open("rt", encoding="utf-8") as handle:
        _stamp, whole = table.read_windows(handle)
    assert measured[False][1] == (len(whole.transitions), len(whole.rules), len(whole.reachable_cells()))
    assert seen["payload path"].startswith("/dev/fd/")
    assert seen["while writing"] is False
    assert not piped.exists() and not piped.is_symlink()


@pytest.mark.parametrize("keep", [False, True], ids=["piped", "kept"])
def test_a_payload_whose_reader_lost_bytes_stops_the_size(monkeypatch, tmp_path, keep):
    """A payload whose reader receives fewer bytes than the crate answers it wrote, here one row another reader took, stops the run naming both counts instead of counting a short payload, through a pipe and from a kept file alike."""
    spec_path = _spec_path(tmp_path)
    scan = scaling_sweep.scan

    def losing(source):
        head, columns, _taken, rest = source.readline(), source.readline(), source.readline(), source.read()
        return scan(io.BytesIO(head + columns + rest))

    monkeypatch.setattr(scaling_sweep, "scan", losing)
    with pytest.raises(SystemExit, match=r"received \d+ bytes where the crate wrote \d+"):
        scaling_sweep.measure(kernel_exec.BINARY, spec_path, tmp_path / "out", "sweep-stamp", keep)


@pytest.mark.parametrize(
    "stdout",
    [
        b'{"config":"default","digest":"ab"}\n',
        b'{"config":"default","digest":"ab","windows_bytes":"12"}\n',
        b'{"config":"ss03","digest":"ab","windows_bytes":12}\n',
        b"not json\n",
    ],
    ids=["no byte count", "a byte count that is not a number", "another configuration", "not JSON"],
)
def test_an_answer_that_is_not_the_table_builds_stops_the_run(stdout):
    """Each `build-tables` answer line must be a `{config, digest, windows_bytes}` object with an integer byte count, for the configurations named and no others."""
    with pytest.raises(SystemExit, match="build-tables"):
        scaling_sweep.table_answers(stdout, ["default"])
    good = b'{"config":"default","digest":"ab","windows_bytes":12}\n'
    assert scaling_sweep.table_answers(good, ["default"]) == [
        {"config": "default", "digest": "ab", "windows_bytes": 12}
    ]

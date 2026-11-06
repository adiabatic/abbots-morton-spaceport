from types import SimpleNamespace

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


def test_a_size_counts_the_same_through_a_fifo_as_from_the_kept_payload(tmp_path):
    """A size counted through a FIFO, as the sweep runs without `AMS_SCALING_DUMP`, gets the digest a kept run gets and the windows, rules and cells the payload that run wrote as a file holds, read back whole, and leaves nothing at the payload's path, where the kept run leaves the file. The size is the mini fixture narrowed to ·Tea, ·May and ·It."""
    spec_path = tmp_path / "spec.json"
    kernel_io.write_spec(spec_load.narrowed(fixtures.mini_spec(), ("qsTea", "qsMay", "qsIt")), spec_path)
    kernel_exec.ensure_built()
    measured = {}
    for keep in (False, True):
        out_dir = tmp_path / ("kept" if keep else "fifo")
        run, counts = scaling_sweep.measure(kernel_exec.BINARY, spec_path, out_dir, "sweep-stamp", keep)
        measured[keep] = (run["digest"], counts)
    assert measured[False] == measured[True]
    kept = kernel_exec.windows_payload(tmp_path / "kept", "default")
    with kept.open("rt", encoding="utf-8") as handle:
        _stamp, whole = table.read_windows(handle)
    assert measured[False][1] == (len(whole.transitions), len(whole.rules), len(whole.reachable_cells()))
    assert not kernel_exec.windows_payload(tmp_path / "fifo", "default").exists()

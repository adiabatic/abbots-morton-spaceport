from types import SimpleNamespace

from rebuild.pipeline import conform, fixtures
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

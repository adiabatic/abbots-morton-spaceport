"""The hand probe reads new settlement from the same Rust-backed batch path as the explain CLI and review surface, and reads its baseline rows by scanning the subset table only as far as the last window it wants."""

import gzip
from pathlib import Path
from types import SimpleNamespace

import pytest

from rebuild.pipeline import fixtures
from rebuild.pipeline.model import CellId, Settled
from rebuild.tools import probe

MINI = Path(__file__).resolve().parent / "review" / "fixtures" / "mini"

SETTLED = (
    Settled(
        cell=CellId(rune="qsMay", stance="loop", entry=None, exit="x-height"),
        seam="x-height",
        extension=0,
    ),
    Settled(
        cell=CellId(rune="qsIt", stance="hapax", entry="x-height", exit=None),
        seam=None,
        extension=0,
    ),
)


def _settled_naming_the_request(codepoints, features):
    """A settlement that names the request it answers: one cell per codepoint, its rune the codepoint and its stance the active features, every seam at the x-height. A block rendered from another window's reports, or from another configuration's, reads differently."""
    stance = "+".join(sorted(features)) or "default"
    last = len(codepoints) - 1
    return tuple(
        Settled(
            cell=CellId(rune=f"cp{cp:04X}", stance=stance, entry=None, exit=None),
            seam=None if i == last else "x-height",
            extension=0,
        )
        for i, cp in enumerate(codepoints)
    )


def _baseline_naming_the_window(config, windows):
    """A baseline row for every wanted window whose glyphs field names the window and the configuration, so a block that shows another window's row, or another configuration's, reads differently."""
    return {w: [w, f"old-{w}-{config}", "0", "y0"] for w in windows}


def _stub_settlement(monkeypatch, configs, settle=_settled_naming_the_request):
    """Route the probe through a recording `explain_many` that answers each request with `settle(codepoints, features)`, under the given configurations and with no baseline table."""
    spec = fixtures.mini_spec()
    calls = []

    def explain_many(got_spec, requests):
        calls.append((got_spec, requests))
        return [SimpleNamespace(settled=settle(codepoints, features)) for codepoints, features in requests]

    monkeypatch.setattr(probe, "CONFIGS", configs)
    monkeypatch.setattr(probe, "load_default_spec", lambda: spec)
    monkeypatch.setattr(probe, "baseline_rows", lambda _config, _windows: {})
    monkeypatch.setattr(probe, "explain_many", explain_many)
    return spec, calls


def test_probe_routes_its_configs_through_explain_many(monkeypatch, capsys):
    """What the probe owns is the routing and the rendering, so the settlement it renders is a literal pair of cells here rather than a second trip through the kernel: one `explain_many` call carrying every configuration's window, and each report's cells and seams printed under their configuration."""
    spec, calls = _stub_settlement(monkeypatch, ["default"], settle=lambda _codepoints, _features: SETTLED)
    probe.main(["E665:E670"])
    assert len(calls) == 1
    assert calls[0][0] is spec
    assert calls[0][1] == [([0xE665, 0xE670], frozenset())]
    output = capsys.readouterr().out
    assert "=== window E665:E670 ===" in output
    assert "NEW cells : qsMay.loop/en=None/ex=x-height/ | qsIt.hapax/en=x-height/ex=None/" in output
    assert "NEW seams : y5" in output


def test_the_baseline_scan_stops_at_the_last_window_it_wants():
    """The scan is a pass with an early exit, so it is asserted on how far the source advanced rather than only on its answer: two wanted keys sit early in a synthetic table and the generator never yields the row after the second."""
    table = ["# header\n"] + [f"K{i}\tg{i}\t0\ty{i}\n" for i in range(20)]
    yielded = []

    def lines():
        for line in table:
            yielded.append(line)
            yield line

    found = probe._scan_rows(lines(), frozenset({"K2", "K5"}))
    assert found == {"K2": ["K2", "g2", "0", "y2"], "K5": ["K5", "g5", "0", "y5"]}
    assert yielded[-1] == "K5\tg5\t0\ty5\n"
    assert len(yielded) == 7


def test_the_baseline_scan_returns_what_a_whole_table_dict_holds(monkeypatch):
    """Over the mini bundle's checked-in default table, asking for the first data key, the last key, and an absent one in a single call returns exactly the rows a whole-table dict holds for the present keys; the oracle is built here over the same file so the assertion survives a regeneration of the bundle."""
    monkeypatch.setattr(probe, "OUT_DIR", MINI)
    whole = {}
    with gzip.open(MINI / "baseline-default.subset.tsv.gz", "rt") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            whole[parts[0]] = parts
    keys = list(whole)
    wanted = {keys[0], keys[-1], "0000:0000"}
    scan = probe.baseline_rows("default", wanted)
    assert scan == {k: whole[k] for k in wanted if k in whole}
    assert set(scan) == {keys[0], keys[-1]}


def test_no_wanted_window_opens_no_table(monkeypatch, tmp_path):
    """An empty wanted set returns an empty answer without opening a table, so a directory holding no such file raises nothing."""
    monkeypatch.setattr(probe, "OUT_DIR", tmp_path)
    assert probe.baseline_rows("default", set()) == {}


def test_every_window_and_configuration_rides_one_explain_many_call(monkeypatch):
    """Two windows under two configurations are one `explain_many` call whose requests are the window-major cross product in order, so the explainer's warm-up is paid once per process."""
    _spec, calls = _stub_settlement(monkeypatch, ["default", "ss03"])
    monkeypatch.setattr(probe.conform, "features_for_config", lambda config: frozenset({config}))
    probe.main(["E665:E670", "E652:E67A"])
    assert len(calls) == 1
    assert calls[0][1] == [
        ([0xE665, 0xE670], frozenset({"default"})),
        ([0xE665, 0xE670], frozenset({"ss03"})),
        ([0xE652, 0xE67A], frozenset({"default"})),
        ([0xE652, 0xE67A], frozenset({"ss03"})),
    ]


def test_each_block_carries_its_own_windows_settlement_and_baseline(monkeypatch, capsys):
    """Every rendered line names the window and configuration it came from, so the whole multi-window output is pinned as text: window blocks in argument order, each block's configurations in `CONFIGS` order, each line showing the settlement answered for that window under that configuration and the baseline row read for it. A slice taken from another window's reports, or a baseline looked up under another window's key, changes the text."""
    _stub_settlement(monkeypatch, ["default", "ss03"])
    monkeypatch.setattr(probe.conform, "features_for_config", lambda config: frozenset({config}))
    monkeypatch.setattr(probe, "baseline_rows", _baseline_naming_the_window)
    probe.main(["E665:E670", "E652:E67A:E650"])
    assert capsys.readouterr().out == (
        "=== window E665:E670 ===\n"
        "\n[default]\n"
        "  OLD glyphs: old-E665:E670-default\n"
        "  OLD seams : y0\n"
        "  NEW cells : cpE665.default/en=None/ex=None/ | cpE670.default/en=None/ex=None/\n"
        "  NEW seams : y5\n"
        "\n[ss03]\n"
        "  OLD glyphs: old-E665:E670-ss03\n"
        "  OLD seams : y0\n"
        "  NEW cells : cpE665.ss03/en=None/ex=None/ | cpE670.ss03/en=None/ex=None/\n"
        "  NEW seams : y5\n"
        "=== window E652:E67A:E650 ===\n"
        "\n[default]\n"
        "  OLD glyphs: old-E652:E67A:E650-default\n"
        "  OLD seams : y0\n"
        "  NEW cells : cpE652.default/en=None/ex=None/ | cpE67A.default/en=None/ex=None/ | cpE650.default/en=None/ex=None/\n"
        "  NEW seams : y5,y5\n"
        "\n[ss03]\n"
        "  OLD glyphs: old-E652:E67A:E650-ss03\n"
        "  OLD seams : y0\n"
        "  NEW cells : cpE652.ss03/en=None/ex=None/ | cpE67A.ss03/en=None/ex=None/ | cpE650.ss03/en=None/ex=None/\n"
        "  NEW seams : y5,y5\n"
    )


def test_the_multi_window_run_is_the_single_window_runs_concatenated(monkeypatch, capsys):
    """The skills diff probe output before and after a rune edit, so a run over several windows prints exactly the single-window runs joined in argument order. The stubbed settlement and baseline name their window, so the two single-window outputs differ and a block sliced from the wrong window's reports would not match."""
    _stub_settlement(monkeypatch, ["default", "ss03"])
    monkeypatch.setattr(probe, "baseline_rows", _baseline_naming_the_window)
    probe.main(["E665:E670"])
    first = capsys.readouterr().out
    probe.main(["E652:E67A:E650"])
    second = capsys.readouterr().out
    probe.main(["E665:E670", "E652:E67A:E650"])
    both = capsys.readouterr().out
    assert both == first + second
    assert first != second
    assert first.startswith("=== window E665:E670 ===\n\n[default]\n")
    assert "\n[ss03]\n" in first


def test_a_window_absent_from_the_subset_reads_as_not_in_subset(monkeypatch, capsys):
    """A baseline table that holds no row for the window prints the not-in-subset marker and an empty seams line."""
    _stub_settlement(monkeypatch, ["default"])
    probe.main(["E665:E670"])
    output = capsys.readouterr().out
    assert "  OLD glyphs: (not in subset)\n  OLD seams : \n" in output


def test_a_present_window_prints_its_baseline_row(monkeypatch, capsys):
    """A baseline row for the window fills the glyphs slot from its second field and the seams slot from its fourth."""
    _stub_settlement(monkeypatch, ["default"])
    monkeypatch.setattr(
        probe,
        "baseline_rows",
        lambda _config, _windows: {"E665:E670": ["E665:E670", "qsMay|qsIt", "0,1", "y5"]},
    )
    probe.main(["E665:E670"])
    output = capsys.readouterr().out
    assert "  OLD glyphs: qsMay|qsIt\n  OLD seams : y5\n" in output


def test_no_baseline_never_reads_a_subset_table(monkeypatch, capsys):
    """`--no-baseline` marks the baseline lines as not read, distinct from a genuine miss, and never asks for a table."""
    _stub_settlement(monkeypatch, ["default"])

    def refuse(_config, _windows):
        raise AssertionError("baseline table read under --no-baseline")

    monkeypatch.setattr(probe, "baseline_rows", refuse)
    probe.main(["--no-baseline", "E665:E670"])
    output = capsys.readouterr().out
    assert "=== window E665:E670 ===" in output
    assert "  OLD glyphs: (baseline not read)\n  OLD seams : \n" in output
    assert "NEW seams : y5" in output


@pytest.mark.parametrize("argv", [[], ["--no-baseline"], ["E665:zz"], ["E665:"], ["qsMay"]])
def test_an_empty_or_malformed_window_list_prints_the_usage_and_exits_2(monkeypatch, capsys, argv):
    """No window, or an entry that is not a colon-joined hex window, is refused with the usage line and exit status 2 before anything is loaded."""
    _spec, calls = _stub_settlement(monkeypatch, ["default"])
    with pytest.raises(SystemExit) as raised:
        probe.main(argv)
    assert raised.value.code == 2
    assert probe.USAGE in capsys.readouterr().err
    assert calls == []

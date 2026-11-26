"""Tests for rebuild/tools/probe.py, which settles windows through `explain_many` (the Rust-backed batch path the explain CLI and the review corpus use) and prints each window's settlement in every configuration."""

from types import SimpleNamespace

import pytest

from rebuild.pipeline import fixtures
from rebuild.pipeline.model import CellId, Settled
from rebuild.tools import probe

SETTLED = (
    Settled(
        cell=CellId(rune="qsMay", stance="loop", entry=None, exit="x-height"),
        junction="x-height",
        extension=0,
    ),
    Settled(
        cell=CellId(rune="qsIt", stance="sole", entry="x-height", exit=None),
        junction=None,
        extension=0,
    ),
)


def _settled_naming_the_request(codepoints, features):
    """Return one cell per codepoint, with the codepoint as its rune, the active features as its stance, and every junction at the x-height. A block rendered from another window's or another configuration's reports then prints different text."""
    stance = "+".join(sorted(features)) or "default"
    last = len(codepoints) - 1
    return tuple(
        Settled(
            cell=CellId(rune=f"cp{cp:04X}", stance=stance, entry=None, exit=None),
            junction=None if i == last else "x-height",
            extension=0,
        )
        for i, cp in enumerate(codepoints)
    )


def _stub_settlement(monkeypatch, configs, settle=_settled_naming_the_request):
    """Replace the probe's `explain_many` with one that records its calls and returns `settle(codepoints, features)` for each request, load the mini spec, and set `CONFIGS`."""
    spec = fixtures.mini_spec()
    calls = []

    def explain_many(got_spec, requests):
        calls.append((got_spec, requests))
        return [SimpleNamespace(settled=settle(codepoints, features)) for codepoints, features in requests]

    monkeypatch.setattr(probe, "CONFIGS", configs)
    monkeypatch.setattr(probe, "load_default_spec", lambda: spec)
    monkeypatch.setattr(probe, "explain_many", explain_many)
    return spec, calls


def test_probe_routes_its_configs_through_explain_many(monkeypatch, capsys):
    """The probe is responsible only for routing and rendering, so the settlement here is a fixed pair of cells instead of a kernel call. The probe makes one `explain_many` call and prints each report's cells and junctions under its configuration."""
    spec, calls = _stub_settlement(monkeypatch, ["default"], settle=lambda _codepoints, _features: SETTLED)
    probe.main(["E665:E670"])
    assert len(calls) == 1
    assert calls[0][0] is spec
    assert calls[0][1] == [([0xE665, 0xE670], frozenset())]
    output = capsys.readouterr().out
    assert "=== window E665:E670 ===" in output
    assert "  cells : qsMay.loop/en=None/ex=x-height/ | qsIt.sole/en=x-height/ex=None/" in output
    assert "  junctions : y5" in output


def test_every_window_and_configuration_goes_into_one_explain_many_call(monkeypatch):
    """Two windows under two configurations make one `explain_many` call whose requests are the cross product in window-major order, so the explainer's warm-up cost is paid once per process."""
    _spec, calls = _stub_settlement(monkeypatch, ["default", "ss03"])
    monkeypatch.setattr(probe, "features_for_config", lambda config: frozenset({config}))
    probe.main(["E665:E670", "E652:E67A"])
    assert len(calls) == 1
    assert calls[0][1] == [
        ([0xE665, 0xE670], frozenset({"default"})),
        ([0xE665, 0xE670], frozenset({"ss03"})),
        ([0xE652, 0xE67A], frozenset({"default"})),
        ([0xE652, 0xE67A], frozenset({"ss03"})),
    ]


def test_each_block_carries_its_own_windows_settlement(monkeypatch, capsys):
    """Checks the full multi-window output as text: window blocks in argument order, configurations in `CONFIGS` order within each block, and each line showing the settlement for its own window and configuration. The stub names the window and configuration in every line, so a block built from another window's reports prints different text."""
    _stub_settlement(monkeypatch, ["default", "ss03"])
    monkeypatch.setattr(probe, "features_for_config", lambda config: frozenset({config}))
    probe.main(["E665:E670", "E652:E67A:E650"])
    assert capsys.readouterr().out == (
        "=== window E665:E670 ===\n"
        "\n[default]\n"
        "  cells : cpE665.default/en=None/ex=None/ | cpE670.default/en=None/ex=None/\n"
        "  junctions : y5\n"
        "\n[ss03]\n"
        "  cells : cpE665.ss03/en=None/ex=None/ | cpE670.ss03/en=None/ex=None/\n"
        "  junctions : y5\n"
        "=== window E652:E67A:E650 ===\n"
        "\n[default]\n"
        "  cells : cpE652.default/en=None/ex=None/ | cpE67A.default/en=None/ex=None/ | cpE650.default/en=None/ex=None/\n"
        "  junctions : y5,y5\n"
        "\n[ss03]\n"
        "  cells : cpE652.ss03/en=None/ex=None/ | cpE67A.ss03/en=None/ex=None/ | cpE650.ss03/en=None/ex=None/\n"
        "  junctions : y5,y5\n"
    )


def test_the_multi_window_run_is_the_single_window_runs_concatenated(monkeypatch, capsys):
    """The skills diff probe output from before and after a rune edit, so a run over several windows must print the single-window runs joined in argument order. The stubs name their window, so the two single-window outputs differ and a block taken from the wrong window's reports would not match."""
    _stub_settlement(monkeypatch, ["default", "ss03"])
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


@pytest.mark.parametrize("argv", [[], ["E665:zz"], ["E665:"], ["qsMay"]])
def test_an_empty_or_malformed_window_list_prints_the_usage_and_exits_2(monkeypatch, capsys, argv):
    """With no window, or an entry that is not a colon-joined hex window, the probe prints the usage line and exits with status 2 before loading anything."""
    _spec, calls = _stub_settlement(monkeypatch, ["default"])
    with pytest.raises(SystemExit) as raised:
        probe.main(argv)
    assert raised.value.code == 2
    assert probe.USAGE in capsys.readouterr().err
    assert calls == []

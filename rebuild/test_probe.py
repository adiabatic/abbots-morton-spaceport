"""The hand probe reads new settlement from the same Rust-backed batch path as the explain CLI and review surface."""

from types import SimpleNamespace

from rebuild.pipeline import fixtures
from rebuild.pipeline.model import CellId, Settled
from rebuild.tools import probe

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


def test_probe_routes_its_configs_through_explain_many(monkeypatch, capsys):
    """What the probe owns is the routing and the rendering, so the settlement it renders is a literal pair of cells here rather than a second trip through the kernel: one `explain_many` call carrying every configuration's window, and each report's cells and seams printed under their configuration."""
    spec = fixtures.mini_spec()
    calls = []

    def explain_many(got_spec, requests):
        calls.append((got_spec, requests))
        return [SimpleNamespace(settled=SETTLED) for _codepoints, _features in requests]

    monkeypatch.setattr(probe, "CONFIGS", ["default"])
    monkeypatch.setattr(probe, "load_default_spec", lambda: spec)
    monkeypatch.setattr(probe, "load_subset", lambda _config: {})
    monkeypatch.setattr(probe, "explain_many", explain_many)
    probe.main(["E665:E670"])
    assert len(calls) == 1
    assert calls[0][0] is spec
    assert calls[0][1] == [([0xE665, 0xE670], frozenset())]
    output = capsys.readouterr().out
    assert "=== window E665:E670 ===" in output
    assert "NEW cells : qsMay.loop/en=None/ex=x-height/ | qsIt.hapax/en=x-height/ex=None/" in output
    assert "NEW seams : y5" in output

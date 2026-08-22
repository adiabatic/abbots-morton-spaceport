"""The hand probe reads new settlement from the same Rust-backed batch path as the explain CLI and review surface."""

from types import SimpleNamespace

from rebuild.pipeline import fixtures
from rebuild.pipeline.settle import settle
from rebuild.tools import probe


def test_probe_routes_its_configs_through_explain_many(monkeypatch, capsys):
    spec = fixtures.mini_spec()
    calls = []

    def explain_many(got_spec, requests):
        calls.append((got_spec, requests))
        return [
            SimpleNamespace(settled=tuple(settle(got_spec, codepoints, features)))
            for codepoints, features in requests
        ]

    monkeypatch.setattr(probe, "CONFIGS", ["default"])
    monkeypatch.setattr(probe, "load_default_spec", lambda: spec)
    monkeypatch.setattr(probe, "load_subset", lambda _config: {})
    monkeypatch.setattr(probe, "explain_many", explain_many)
    probe.main(["E665:E670"])
    assert len(calls) == 1
    assert calls[0][1] == [([0xE665, 0xE670], frozenset())]
    output = capsys.readouterr().out
    assert "=== window E665:E670 ===" in output
    assert "NEW cells" in output

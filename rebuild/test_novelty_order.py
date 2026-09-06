"""Tests for the novelty ordering: blank-rep selection (one rep per echo group, lowest blank id, skips counting as blank), the greedy max-min walk (consecutive reps change class where id order would repeat it, rare classes surface first, deterministic across runs), and the emitted worklist URL's `order=given` form with the manifest-stamp guard."""

import json

import pytest

from rebuild.tools import novelty_order as no

STAMP = "2026-07-10T00:00:00Z"


def unit(
    uid,
    cls="alpha",
    group="qsPea:qsTea",
    tokens=("·Pea", "·Tea"),
    echo=None,
    batch: int | None = 1,
    seams_before=("break",),
    seams_after=("y0",),
    cells=("qsPea/full", "qsTea/half"),
    configs=("default",),
    kinds=("seam",),
    provenance=("glyph_data/runes/qsPea.yaml:policy.prefer[0]",),
):
    return {
        "id": uid,
        "order": None if batch is None else int(uid.split("-")[1]),
        "batch": batch,
        "echo": echo,
        "class": cls,
        "group": group,
        "notation_tokens": list(tokens),
        "configs": list(configs),
        "kinds": list(kinds),
        "before": {"seams": list(seams_before)},
        "after": {"seams": list(seams_after), "cells": list(cells)},
        "provenance": list(provenance),
    }


def v(unit_id, verdict, at=STAMP):
    return {"unit": unit_id, "verdict": verdict, "note": "", "at": at}


def test_blank_reps_takes_the_lowest_blank_member_per_echo_group():
    units = [
        unit("u-0001", echo="e-1"),
        unit("u-0002", echo="e-1"),
        unit("u-0003", echo="e-2"),
        unit("u-0004", echo=None),
        unit("u-0005", batch=None),
    ]
    records = {"u-0001": v("u-0001", "approve")}
    reps, blank_count = no.blank_reps(units, records)
    assert [u["id"] for u in reps] == ["u-0002", "u-0003", "u-0004"]
    assert blank_count == 3


def test_blank_reps_counts_a_skip_as_blank():
    units = [unit("u-0001", echo="e-1")]
    reps, blank_count = no.blank_reps(units, {"u-0001": v("u-0001", "skip")})
    assert [u["id"] for u in reps] == ["u-0001"]
    assert blank_count == 1


def test_novelty_order_alternates_classes_where_id_order_repeats_them():
    reps = [
        unit("u-0001", cls="alpha", group="qsPea:qsTea", tokens=("·Pea", "·Tea")),
        unit("u-0002", cls="alpha", group="qsPea:qsDay", tokens=("·Pea", "·Day")),
        unit("u-0003", cls="beta", group="qsMay:qsNo", tokens=("·May", "·No")),
        unit("u-0004", cls="beta", group="qsMay:qsLow", tokens=("·May", "·Low")),
    ]
    order = no.novelty_order(reps)
    assert sorted(order) == ["u-0001", "u-0002", "u-0003", "u-0004"]
    classes = {u["id"]: u["class"] for u in reps}
    assert all(classes[a] != classes[b] for a, b in zip(order, order[1:]))
    assert order == no.novelty_order(reps)


def test_novelty_order_seeds_with_the_rarest_class():
    reps = [
        unit("u-0001", cls="common"),
        unit("u-0002", cls="common", group="qsPea:qsDay"),
        unit("u-0003", cls="rare", group="qsMay:qsNo", tokens=("·May", "·No")),
    ]
    assert no.novelty_order(reps)[0] == "u-0003"


def test_distance_is_zero_for_identical_features_and_grows_with_difference():
    a = no.features(unit("u-0001"))
    b = no.features(unit("u-0002"))
    c = no.features(unit("u-0003", cls="beta", group="qsMay:qsNo", tokens=("·May", "·No"), configs=("ss04",)))
    assert no.distance(a, b) == 0.0
    assert no.distance(a, c) > 0.5


@pytest.fixture
def repo(tmp_path):
    surface = tmp_path / "surface"
    (surface / "units").mkdir(parents=True)
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": STAMP, "classes": [{"id": "all", "shards": ["units/all.json"]}]})
    )
    return {"surface": surface, "verdicts": tmp_path / "verdicts.json"}


def write_surface(repo, units):
    (repo["surface"] / "units" / "all.json").write_text(json.dumps(units))


def write_verdicts(repo, verdicts, stamp=STAMP):
    repo["verdicts"].write_text(json.dumps({"manifest_generated_at": stamp, "verdicts": verdicts}))


def run_main(repo, monkeypatch, capsys, *extra):
    monkeypatch.setattr(
        "sys.argv",
        ["novelty_order.py", str(repo["verdicts"]), "--surface", str(repo["surface"]), *extra],
    )
    no.main(clipboard_write=lambda _url: None)
    return capsys.readouterr().out


def worklist_url(out):
    return next(line for line in out.splitlines() if line.startswith("http://localhost:"))


def test_main_prints_the_order_given_worklist_url(repo, monkeypatch, capsys):
    write_surface(
        repo,
        [
            unit("u-0001", cls="alpha"),
            unit("u-0002", cls="beta", group="qsMay:qsNo", tokens=("·May", "·No")),
        ],
    )
    write_verdicts(repo, [])
    out = run_main(repo, monkeypatch, capsys)
    assert "2 blank units collapse to 2 echo groups" in out
    url = worklist_url(out)
    assert url.startswith("http://localhost:")
    assert url.endswith("&order=given")
    assert {"u-0001", "u-0002"} == set(url.split("#units=")[1].split("&")[0].split(","))


def test_main_routes_the_worklist_url_through_the_clipboard_hook(repo, monkeypatch, capsys):
    write_surface(repo, [unit("u-0001")])
    write_verdicts(repo, [])
    copied = []
    monkeypatch.setattr(
        "sys.argv",
        ["novelty_order.py", str(repo["verdicts"]), "--surface", str(repo["surface"])],
    )
    no.main(clipboard_write=copied.append)
    out = capsys.readouterr().out
    assert copied == [worklist_url(out)]


def test_main_limit_emits_a_prefix(repo, monkeypatch, capsys):
    write_surface(
        repo,
        [
            unit("u-0001", cls="alpha"),
            unit("u-0002", cls="beta", group="qsMay:qsNo", tokens=("·May", "·No")),
            unit("u-0003", cls="gamma", group="qsTea:qsOy", tokens=("·Tea", "·Oy")),
        ],
    )
    write_verdicts(repo, [])
    out = run_main(repo, monkeypatch, capsys, "--limit", "2")
    assert "emitting the first 2 reps" in out
    assert len(worklist_url(out).split("#units=")[1].split("&")[0].split(",")) == 2


def test_main_defaults_to_a_forty_rep_sitting(repo, monkeypatch, capsys):
    write_surface(repo, [unit(f"u-{number:04d}") for number in range(1, 46)])
    write_verdicts(repo, [])
    out = run_main(repo, monkeypatch, capsys)
    assert "emitting the first 40 reps" in out
    assert len(worklist_url(out).split("#units=")[1].split("&")[0].split(",")) == 40


def test_main_limit_zero_emits_the_whole_queue(repo, monkeypatch, capsys):
    write_surface(repo, [unit(f"u-{number:04d}") for number in range(1, 46)])
    write_verdicts(repo, [])
    out = run_main(repo, monkeypatch, capsys, "--limit", "0")
    assert "echo-fills the rest" in out
    assert len(worklist_url(out).split("#units=")[1].split("&")[0].split(",")) == 45


def test_main_refuses_a_verdicts_file_for_another_manifest(repo, monkeypatch, capsys):
    write_surface(repo, [unit("u-0001")])
    write_verdicts(repo, [], stamp="2026-07-01T00:00:00Z")
    with pytest.raises(SystemExit, match="never be joined across manifests"):
        run_main(repo, monkeypatch, capsys)


def test_main_reports_an_empty_queue(repo, monkeypatch, capsys):
    write_surface(repo, [unit("u-0001")])
    write_verdicts(repo, [v("u-0001", "approve")])
    out = run_main(repo, monkeypatch, capsys)
    assert "nothing to order" in out
    assert "units=" not in out

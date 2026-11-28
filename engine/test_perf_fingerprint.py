import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.review.verdict_store import store_bytes, store_document
from engine.tools import perf_fingerprint as pf

SCRIPT = Path(pf.__file__)


def _corpus(root: Path, *, stamp: str, head: str, inputs: str, unit: str = '{"id": "u-1"}') -> Path:
    corpus = root / "review"
    corpus.mkdir(parents=True)
    manifest = {
        "generated_at": stamp,
        "repo_head": head,
        "inputs_fingerprint": inputs,
        "fonts": {"before": {"source": "site/AbbotsMortonSpaceportSansSenior-Regular.otf", "sha256": head}},
        "units": 1,
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    (corpus / "units").mkdir()
    (corpus / "units" / "shard-0.json").write_text(unit)
    return corpus


def _store(path: Path, *, stamp: str, at: str, verdict: str = "approve") -> Path:
    document = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": [
            {"unit": "u-2", "verdict": "reject", "note": "too close", "at": at},
            {"unit": "u-1", "verdict": verdict, "note": "fine", "at": at},
        ],
        "cleared": [{"unit": "u-3", "at": at}],
    }
    path.write_text(json.dumps(document))
    return path


def test_the_corpus_and_store_digests_ignore_every_stamp_and_see_a_change(tmp_path):
    def font(_path: Path) -> str:
        return "content digest"

    first = pf.corpus_digests(
        _corpus(tmp_path / "a", stamp="2026-10-28T01:00:00Z", head="aaa", inputs="i1"), font
    )
    restamped = pf.corpus_digests(
        _corpus(tmp_path / "b", stamp="2026-10-29T02:00:00Z", head="bbb", inputs="i2"), font
    )
    changed = pf.corpus_digests(
        _corpus(tmp_path / "c", stamp="2026-10-28T01:00:00Z", head="aaa", inputs="i1", unit='{"id": "u-9"}'),
        font,
    )
    assert restamped == first
    assert changed["digest"] != first["digest"]
    assert [name for name in first["files"] if first["files"][name] != changed["files"][name]] == [
        "units/shard-0.json"
    ]

    store = pf.store_digest(_store(tmp_path / "s1.json", stamp="t1", at="2026-10-28T01:00:00Z"))
    assert pf.store_digest(_store(tmp_path / "s2.json", stamp="t2", at="2026-10-29T02:00:00Z")) == store
    held = json.loads((tmp_path / "s1.json").read_text())
    records = {record["unit"]: record for record in held["verdicts"]}
    for name, data in (
        (
            "one-a-line.json",
            store_bytes(
                store_document("t1", records, {entry["unit"]: entry["at"] for entry in held["cleared"]})
            ),
        ),
        ("indented.json", json.dumps(json.loads((tmp_path / "s1.json").read_text()), indent=2).encode()),
    ):
        (tmp_path / name).write_bytes(data)
        assert pf.store_digest(tmp_path / name) == store, name
    assert (
        pf.store_digest(
            _store(tmp_path / "s3.json", stamp="t1", at="2026-10-28T01:00:00Z", verdict="neither")
        )
        != store
    )


def test_the_tables_are_digested_with_the_trees_own_code(tmp_path):
    root = tmp_path / "tree"
    pipeline = root / "engine" / "pipeline"
    pipeline.mkdir(parents=True)
    (pipeline / "__init__.py").write_text("")
    (pipeline / "table.py").write_text(
        "from types import SimpleNamespace\n\n"
        "TABLE_DIGEST_STAMP = 'table_digest'\n\n"
        "def windows_head(out_dir, config):\n    return SimpleNamespace(stamps={'table_digest': f'sentinel {config} joins'})\n\n"
        "def read_join_tsv(path):\n    return path.read_text().strip()\n\n"
        "def materialized_table_digest(out_dir, config, joins):\n    return f'sentinel {config} {joins}'\n"
    )
    (pipeline / "fingerprint.py").write_text(
        "def font_content_digest(path):\n    return 'the tree’s font digest'\n"
    )
    build_out = root / "engine" / "out" / "build"
    build_out.mkdir(parents=True)
    (build_out / "windows-default.tsv.gz.head").write_bytes(b"not read by the stub")
    (build_out / "joins-default.tsv").write_text("joins\n")
    (build_out / "Senior-Regular.otf").write_bytes(b"font")
    out = tmp_path / "fingerprint.json"
    subprocess.run(
        [sys.executable, "-P", str(SCRIPT), "--root", str(root), "--out", str(out)], check=True, cwd=root
    )
    document = json.loads(out.read_text())
    assert document["format"] == pf.FORMAT
    assert document["tables"] == {"default": "sentinel default joins"}
    assert document["fonts"]["engine/out/build/Senior-Regular.otf"] == "the tree’s font digest"
    assert document["fonts"]["site/AbbotsMortonSpaceportMono-Regular.otf"] is None
    assert "accepted" not in document


def test_windows_rows_held_as_a_base_and_segments_fingerprint_as_their_folded_form(
    tmp_path, monkeypatch, capsys
):
    """A tree whose windows rows are a base and segments fingerprints as the tree a from-scratch build of the same tables leaves, so the bar's `tables` block reads the same across any segment or fold. A configuration with a head is hashed as its materialized rows stream (`table.materialized_table_digest`), with no `Window` row built, to the `table_digest` of the table those rows read into. A head whose recorded digest is not the one its materialized rows give stops the fingerprint naming the configuration, and the script exits 1 without writing its document."""
    from engine.pipeline import conform, fingerprint, fixtures, run_build, spec_load
    from engine.pipeline import table as table_module

    spec = fixtures.mini_spec()
    narrow = spec_load.narrowed(spec, ("qsTea", "qsMay", "qsIt"))
    held, scratch = (tmp_path / name / "engine" / "out" / "build" for name in ("held", "scratch"))
    run_build.build_tables(narrow, held, inputs="fp")
    run_build.build_tables(spec, held, inputs="fp")
    run_build.build_tables(spec, scratch, inputs="fp")
    assert all(table_module.windows_head(held, config).segments for config in conform.SETTLEMENT_CONFIGS)
    with monkeypatch.context() as streaming:
        streaming.setattr(table_module, "read_materialized_windows", None)
        digests = pf.table_digests(held, table_module)
    assert sorted(digests) == sorted(conform.SETTLEMENT_CONFIGS)
    for config in conform.SETTLEMENT_CONFIGS:
        _, decision = table_module.read_materialized_windows(held, config, sha256=True)
        joins = table_module.read_join_tsv(held / f"joins-{config}.tsv")
        assert digests[config] == table_module.table_digest(decision, joins), config
    assert digests == pf.table_digests(scratch, table_module)
    path = table_module.windows_head_path(held, "ss04")
    document = json.loads(path.read_text())
    document["stamps"][table_module.TABLE_DIGEST_STAMP] = "0" * 64
    path.write_text(json.dumps(document))
    with pytest.raises(pf.DigestMismatch, match="^ss04: "):
        pf.table_digests(held, table_module)
    monkeypatch.setattr(pf, "tree_modules", lambda root: (table_module, fingerprint))
    out = tmp_path / "fingerprint.json"
    assert pf.main(["--root", str(tmp_path / "held"), "--out", str(out)]) == 1
    assert "perf_fingerprint: ss04: " in capsys.readouterr().err
    assert not out.exists()

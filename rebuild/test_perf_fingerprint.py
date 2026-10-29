import gzip
import json
import subprocess
import sys
from pathlib import Path

from rebuild.tools import perf_fingerprint as pf

SCRIPT = Path(pf.__file__)


def _gz(path: Path, header: dict | None, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        if header is not None:
            handle.write(json.dumps(header) + "\n")
        handle.write(body)


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
    facts = {
        "corpus": {"generated_at": stamp, "repo_head": head, "inputs_fingerprint": inputs},
        "classes": ["a"],
    }
    (corpus / "review-facts.json").write_text(json.dumps(facts))
    _gz(
        corpus / "units-index.ndjson.gz",
        {"format": "ams-units-index/1", "manifest_sha256": head},
        '{"id": "u-1"}\n',
    )
    _gz(
        corpus / "app-units.ndjson.gz",
        {"format": "ams-app-units/1", "manifest_sha256": head, "generated_at": stamp},
        "row\n",
    )
    _gz(corpus / "app-locator-rows.ndjson.gz", None, "locator\n")
    (corpus / "units").mkdir()
    (corpus / "units" / "shard-0.json").write_text(unit)
    _gz(
        corpus / "unit-cache.ndjson.gz",
        {"format": "ams-unit-cache/1", "environment": inputs, "manifest_sha256": head},
        "c\n",
    )
    _gz(
        corpus / "ink-signatures.tsv.gz",
        {"format": "ams-ink-signatures/1", "environment_lines": [inputs]},
        "s\n",
    )
    return corpus


def _store(path: Path, *, stamp: str, at: str, verdict: str = "approve") -> Path:
    document = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": [
            {"unit": "u-2", "verdict": "reject", "note": "too close", "at": at},
            {"unit": "u-1", "verdict": verdict, "note": "[standing: rule] fine", "at": at},
        ],
        "cleared": [{"unit": "u-3", "at": at}],
    }
    path.write_text(json.dumps(document))
    return path


def _complaints(path: Path, *, stamp: str, at: str, note: str = "too close") -> Path:
    group = {"id": "g-1", "rejects": {"new": [{"unit": "u-2", "note": note, "at": at}], "older": []}}
    path.write_text(
        json.dumps(
            {"manifest_generated_at": stamp, "since": stamp, "totals": {"groups": 1}, "groups": [group]}
        )
    )
    return path


def test_the_corpus_store_and_complaint_digests_ignore_every_stamp_and_see_a_change(tmp_path):
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
    assert set(first[1]) == set(pf.STORES) and "unit-cache.ndjson.gz" not in first[0]["files"]
    assert changed[0]["digest"] != first[0]["digest"]
    assert [name for name in first[0]["files"] if first[0]["files"][name] != changed[0]["files"][name]] == [
        "units/shard-0.json"
    ]

    store = pf.store_digest(_store(tmp_path / "s1.json", stamp="t1", at="2026-10-28T01:00:00Z"))
    assert pf.store_digest(_store(tmp_path / "s2.json", stamp="t2", at="2026-10-29T02:00:00Z")) == store
    assert (
        pf.store_digest(
            _store(tmp_path / "s3.json", stamp="t1", at="2026-10-28T01:00:00Z", verdict="neither")
        )
        != store
    )

    complaints = pf.complaints_digest(
        _complaints(tmp_path / "k1.json", stamp="t1", at="2026-10-28T01:00:00Z")
    )
    assert (
        pf.complaints_digest(_complaints(tmp_path / "k2.json", stamp="t2", at="2026-10-29T02:00:00Z"))
        == complaints
    )
    assert (
        pf.complaints_digest(_complaints(tmp_path / "k3.json", stamp="t1", at="t", note="touching"))
        != complaints
    )


def test_the_tables_are_digested_with_the_trees_own_code(tmp_path):
    root = tmp_path / "tree"
    pipeline = root / "rebuild" / "pipeline"
    pipeline.mkdir(parents=True)
    (pipeline / "__init__.py").write_text("")
    (pipeline / "table.py").write_text(
        "from pathlib import Path\n\n"
        "def windows_path(out_dir, config):\n    return Path(out_dir) / f'windows-{config}.tsv.gz'\n\n"
        "def read_windows(path):\n    return 'inputs', path.name\n\n"
        "def read_join_tsv(path):\n    return path.read_text().strip()\n\n"
        "def table_digest(decision, joins):\n    return f'sentinel {decision} {joins}'\n"
    )
    (pipeline / "fingerprint.py").write_text(
        "def font_content_digest(path):\n    return 'the tree’s font digest'\n"
    )
    m1 = root / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    (m1 / "windows-default.tsv.gz").write_bytes(b"not read by the stub")
    (m1 / "joins-default.tsv").write_text("joins\n")
    (m1 / "M1.otf").write_bytes(b"font")
    out = tmp_path / "fingerprint.json"
    subprocess.run(
        [sys.executable, "-P", str(SCRIPT), "--root", str(root), "--out", str(out)], check=True, cwd=root
    )
    document = json.loads(out.read_text())
    assert document["format"] == pf.FORMAT
    assert document["tables"] == {"default": "sentinel windows-default.tsv.gz joins"}
    assert document["fonts"]["rebuild/out/m1/M1.otf"] == "the tree’s font digest"
    assert document["fonts"]["site/AbbotsMortonSpaceportMono-Regular.otf"] is None

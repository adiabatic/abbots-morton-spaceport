import gzip
import hashlib
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
    listed = root / "rebuild" / "accepted-units.txt"
    listed.write_bytes(b"# ams-accepted-units/1 closed_at=2026-11-05T18:00:00Z\nu-1AAAAAAAAAA\n")
    out = tmp_path / "fingerprint.json"
    subprocess.run(
        [sys.executable, "-P", str(SCRIPT), "--root", str(root), "--out", str(out)], check=True, cwd=root
    )
    document = json.loads(out.read_text())
    assert document["format"] == pf.FORMAT
    assert document["tables"] == {"default": "sentinel windows-default.tsv.gz joins"}
    assert document["fonts"]["rebuild/out/m1/M1.otf"] == "the tree’s font digest"
    assert document["fonts"]["site/AbbotsMortonSpaceportMono-Regular.otf"] is None
    assert document["accepted"] == hashlib.sha256(listed.read_bytes()).hexdigest()


def test_a_corpus_appended_to_reads_as_its_materialized_form(mini_corpus, mini_bundle, tmp_path):
    """A build that appends changed fragments to overflow parts and writes its unit store as a base and a segment fingerprints as its materialized form (#618's decision 48): the corpus and stores blocks equal those of the files a from-scratch build of the same units writes (`build.materialize_corpus`), with the store's head, base and segment files left out of the per-file block and the store read under `unit-cache.ndjson.gz`, as a store written before the segment format was. A corpus no build has appended to, already laid out as its materialized form, reads the same way. A class that lost its last unit by id on a pass that appended nothing keeps its part as it was, every live slot where a from-scratch build puts it and the lost unit's slot dead at its end, so the corpus is not its materialized form and reads as that form, whose part ends at the last live slot."""
    import shutil

    from rebuild.pipeline import fingerprint
    from rebuild.review import build
    from rebuild.review import unit_index
    from rebuild.test_unit_cache import _build, _edited_audit, _rewrite_store, _store_lines

    unappended = tmp_path / "unappended"
    build.materialize_corpus(mini_corpus, unappended)
    assert pf.corpus_digests(mini_corpus, fingerprint.font_content_digest, build.MaterializedCorpus) == (
        pf.corpus_digests(unappended, fingerprint.font_content_digest)
    )
    corpus = tmp_path / "corpus"
    shutil.copytree(mini_corpus, corpus)
    _build(corpus, mini_bundle, audit_path=_edited_audit(tmp_path, 2), jobs=1)
    assert list((corpus / "units").glob("*.x000.json")) and list(
        corpus.glob("unit-cache.ndjson.gz.0.1.delta")
    )
    form = tmp_path / "form"
    build.materialize_corpus(corpus, form)
    read = pf.corpus_digests(corpus, fingerprint.font_content_digest, build.MaterializedCorpus)
    assert read == pf.corpus_digests(form, fingerprint.font_content_digest)
    assert not any(name.startswith("unit-cache.ndjson.gz") for name in read[0]["files"])
    assert read[1]["unit-cache.ndjson.gz"] is not None
    shrunk = tmp_path / "shrunk"
    shutil.copytree(mini_corpus, shrunk)
    with unit_index.LiveCorpus(shrunk) as live:
        last = next(
            slots[-1]
            for meta in live.ordered()
            if len(unit_index.class_shards(meta)) == 1 and len(slots := live.slots(meta)) > 1
        )
    _header, lines = _store_lines(shrunk)
    _rewrite_store(shrunk, lines=[line for line in lines if unit_index.record_id(line) != last.unit_id])
    with build.MaterializedCorpus(shrunk) as materialized:
        assert not materialized.identical
    shrunk_form = tmp_path / "shrunk-form"
    build.materialize_corpus(shrunk, shrunk_form)
    assert (shrunk_form / last.part).stat().st_size < (shrunk / last.part).stat().st_size
    assert pf.corpus_digests(shrunk, fingerprint.font_content_digest, build.MaterializedCorpus) == (
        pf.corpus_digests(shrunk_form, fingerprint.font_content_digest)
    )

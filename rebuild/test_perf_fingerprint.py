import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rebuild.review import landing
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
        {
            "format": "ams-ink-signatures/1",
            "environment_lines": [inputs],
            "audit_identity": [1, 2, len(stamp), len(inputs)],
            "family_keys_digest": inputs,
        },
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
    held = landing.read_store(tmp_path / "s1.json")
    assert held is not None
    for name, data in (
        ("one-a-line.json", landing.document_bytes(held)),
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


def _store_with_overrides(path: Path, *, own: list[dict], cleared: list, overrides: dict | None) -> Path:
    document = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": "t1",
        "exported_at": "t1",
        "accepted": {"sha256": "0" * 64, "closed_at": "2026-11-05T18:00:00Z"},
        "verdicts": own,
        "cleared": cleared,
    }
    if overrides is not None:
        document["overrides"] = overrides
    path.write_text(json.dumps(document))
    return path


def test_the_store_digest_streams_to_the_value_a_whole_parse_gives(tmp_path, monkeypatch):
    """`store_digest` hashes the projection as the store streams, through the tree's own `accepted` readers, and gives the value a whole parse gives (`_parsed_store_digest`): on a store with an override section, with `cleared` as dicts and as bare ids with a duplicate, and with two records on one unit. A store whose own records are out of order is parsed whole, for the same value. Both of the stream's reads read the file it opened, so a writer that renames another store over the path between them changes neither."""
    at = "2026-11-06T09:00:00Z"
    own = [
        {"unit": "u-1", "verdict": "approve", "note": "", "at": at},
        {"unit": "u-2", "verdict": "reject", "note": "too close", "at": at},
        {"unit": "u-2", "verdict": "reject", "note": "touching é", "at": at},
        {"unit": "u-4", "verdict": "either", "at": at},
    ]
    overrides = {
        "verdicts": [
            {"unit": "u-3", "verdict": "neither", "note": None, "at": at},
            {"unit": "u-0", "verdict": "reject", "note": "", "at": at},
        ],
        "cleared": [{"unit": "u-5", "at": at}, "u-6"],
    }
    stores = {
        "sorted": _store_with_overrides(
            tmp_path / "sorted.json",
            own=own,
            cleared=[{"unit": "u-7", "at": at}, "u-8", "u-8"],
            overrides=overrides,
        ),
        "no-section": _store_with_overrides(
            tmp_path / "plain.json", own=own, cleared=["u-8"], overrides=None
        ),
        "unsorted": _store_with_overrides(
            tmp_path / "unsorted.json", own=list(reversed(own)), cleared=[], overrides=overrides
        ),
    }
    parsed = pf._parsed_store_digest
    whole: list[Path] = []
    monkeypatch.setattr(pf, "_parsed_store_digest", lambda path: whole.append(path) or parsed(path))
    for name, path in stores.items():
        assert pf.store_digest(path) == parsed(path), name
    assert whole == [stores["unsorted"]]
    assert pf.store_digest(stores["unsorted"]) == pf.store_digest(
        _store_with_overrides(tmp_path / "resorted.json", own=own, cleared=[], overrides=overrides)
    )

    tree = pf.tree_accepted()
    assert tree is not None
    read_tail = tree.read_tail

    def renamed_over(path, each=None):
        tail = read_tail(path, each)
        os.replace(stores["no-section"], stores["sorted"])
        return tail

    expected, wholes = parsed(stores["sorted"]), len(whole)
    monkeypatch.setattr(tree, "read_tail", renamed_over)
    assert pf.store_digest(stores["sorted"]) == expected != parsed(stores["sorted"])
    assert len(whole) == wholes


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


def test_windows_rows_held_as_a_base_and_segments_fingerprint_as_their_folded_form(
    tmp_path, monkeypatch, capsys
):
    """A tree whose windows rows are a base and segments fingerprints as the tree a from-scratch build of the same tables leaves, and as the single file per configuration a tree from before the segment format holds, so the bar's `tables` block reads the same across the landing pass and across any later segment or fold. A configuration with a head is hashed as its materialized rows stream (`table.materialized_table_digest`), with no `Window` row built, to the `table_digest` of the table those rows read into. A head whose recorded digest is not the one its materialized rows give stops the fingerprint naming the configuration, and the script exits 1 without writing its document."""
    import io
    import shutil

    from rebuild.pipeline import conform, fingerprint, fixtures, run_m1, spec_load
    from rebuild.pipeline import table as table_module

    spec = fixtures.mini_spec()
    narrow = spec_load.narrowed(spec, ("qsTea", "qsMay", "qsIt"))
    held, scratch, single = (
        tmp_path / name / "rebuild" / "out" / "m1" for name in ("held", "scratch", "single")
    )
    run_m1.build_tables(narrow, held, inputs="fp")
    run_m1.build_tables(spec, held, inputs="fp")
    run_m1.build_tables(spec, scratch, inputs="fp")
    assert all(table_module.windows_head(held, config).segments for config in conform.SETTLEMENT_CONFIGS)
    single.mkdir(parents=True)
    for config in conform.SETTLEMENT_CONFIGS:
        shutil.copyfile(scratch / f"joins-{config}.tsv", single / f"joins-{config}.tsv")
        payload = io.BytesIO(b"".join(table_module.materialized_windows(held, config)))
        head = payload.readline()
        part = run_m1._pack_windows(payload, single, table_module.windows_output(config), head)
        assert part.name == table_module.windows_path(single, config).name
    with monkeypatch.context() as streaming:
        streaming.setattr(table_module, "read_materialized_windows", None)
        digests = pf.table_digests(held, table_module)
    assert sorted(digests) == sorted(conform.SETTLEMENT_CONFIGS)
    for config in conform.SETTLEMENT_CONFIGS:
        _, decision = table_module.read_materialized_windows(held, config, sha256=True)
        joins = table_module.read_join_tsv(held / f"joins-{config}.tsv")
        assert digests[config] == table_module.table_digest(decision, joins), config
    assert digests == pf.table_digests(scratch, table_module) == pf.table_digests(single, table_module)
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


@pytest.fixture(scope="module")
def deflated_corpus(build_once, mini_corpus, mini_bundle) -> tuple[Path, Path, Path]:
    from rebuild.test_unit_cache import deflated_corpus_of

    return deflated_corpus_of(build_once, mini_corpus, mini_bundle)


def test_a_corpus_appended_to_reads_as_its_materialized_form(
    mini_corpus, mini_bundle, deflated_corpus, tmp_path
):
    """A build that appends changed fragments to overflow parts, deflated (#618's change 35), and writes its unit store as a base and a segment fingerprints as its materialized form (#618's decision 48): the corpus and stores blocks equal those of the files a from-scratch build of the same units writes (`build.materialize_corpus`), every fragment plain, with the store's head, base and segment files left out of the per-file block and the store read under `unit-cache.ndjson.gz`, as a store written before the segment format was. A corpus whose classes were folded deflated beside a deflated append fingerprints as the plain from-scratch build of its units does. A corpus no build has appended to, already laid out as its materialized form, reads the same way. A class that lost its last unit by id on a pass that appended nothing keeps its part as it was, every live slot where a from-scratch build puts it and the lost unit's slot dead at its end, so the corpus is not its materialized form and reads as that form, whose part ends at the last live slot."""
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
    deflated, scratch, _audit = deflated_corpus
    with build.MaterializedCorpus(deflated) as materialized:
        assert not materialized.identical
    with build.MaterializedCorpus(scratch) as materialized:
        assert materialized.identical
    assert pf.corpus_digests(deflated, fingerprint.font_content_digest, build.MaterializedCorpus) == (
        pf.corpus_digests(scratch, fingerprint.font_content_digest, build.MaterializedCorpus)
    )


def _segmented_index(corpus: Path) -> None:
    """Hold the corpus's units index as a head over a base and two segments whose content is the base's (#618's change 32): one that changes a record's notation and one that changes it back. A single file with no head is linked as generation 0's base first."""
    from rebuild.pipeline import row_delta
    from rebuild.review import unit_index

    materialized = unit_index.materialized_index(corpus)
    assert materialized is not None
    lines = list(materialized[1])
    record = json.loads(lines[0])
    changed = (json.dumps({**record, "notation": "changed"}, ensure_ascii=False) + "\n").encode()
    assert unit_index.write_index_lines(corpus, [changed, *lines[1:]]).outcome == "segment"
    assert unit_index.write_index_lines(corpus, lines).outcome == "segment"
    head = row_delta.read_head(corpus, unit_index.INDEX_NAME)
    assert head is not None and len(head.segments) == 2
    assert not unit_index.index_path(corpus).exists()


def test_a_units_index_held_as_a_head_a_base_and_segments_reads_as_the_single_file_with_its_content(
    mini_corpus, tmp_path
):
    """A corpus whose units index is a head over a base and segments (#618's change 32) fingerprints as one whose index is the single file a build before the segment format wrote with the same content, through the tree's own reader: in the materialized branch, over a corpus whose unit store has a head, and in the plain one, over a corpus with none. Either way the index is read under `units-index.ndjson.gz` and its head, base and segment files are left out of the `corpus` block, and a part whose sha256 is not the one its head records fails the reading."""
    import shutil

    from rebuild.pipeline import fingerprint
    from rebuild.review import build, unit_index

    font = fingerprint.font_content_digest
    single = tmp_path / "single"
    build.materialize_corpus(mini_corpus, single)
    assert unit_index.index_path(single).is_file() and not (single / pf.HEAD).exists()
    held = tmp_path / "held"
    shutil.copytree(mini_corpus, held)
    _segmented_index(held)
    plain = tmp_path / "plain"
    shutil.copytree(single, plain)
    _segmented_index(plain)
    expected = pf.corpus_digests(single, font)
    for read in (pf.corpus_digests(held, font, build.MaterializedCorpus), pf.corpus_digests(plain, font)):
        assert read == expected
        assert [name for name in read[0]["files"] if name.startswith(unit_index.INDEX_NAME)] == [
            unit_index.INDEX_NAME
        ]
    for corpus, materialize in ((held, build.MaterializedCorpus), (plain, None)):
        segment = next(corpus.glob(f"{unit_index.INDEX_NAME}.0.2.delta"))
        damaged = bytearray(segment.read_bytes())
        damaged[-9] ^= 1
        segment.write_bytes(bytes(damaged))
        with pytest.raises(unit_index.IndexUnreadable, match="sha256"):
            pf.corpus_digests(corpus, font, materialize)

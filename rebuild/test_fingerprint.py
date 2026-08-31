"""Tests for the build-input fingerprint module: the streamed file digest and the sweep that keeps it the only way rebuild/ hashes a file, content sensitivity, order independence, missing-file tolerance, the stat-based baselines component, the Stage A record round trip, the serve.py exclusion, and the prose-blind rune digest.

The sweep is here rather than in prose because file_sha256 exists to stop a hash costing the file its size in RAM, and that claim only holds while every hash goes through it — which a roster of callers written into a docstring cannot keep, since nothing checks a roster and the next module to grow a file hash falsifies it in silence. The modules that cannot import fingerprint spell the same streamed read out inline instead; those are pinned against the helper by value here, so a copy cannot drift from the original unnoticed either.
"""

import ast
import hashlib
import json
import textwrap
from pathlib import Path

from rebuild.baseline import model
from rebuild.pipeline import fingerprint
from rebuild.tools import artifact_cycle

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "glyph_data" / "runes").mkdir(parents=True)
    (root / "rebuild" / "schema").mkdir(parents=True)
    (root / "rebuild" / "pipeline").mkdir(parents=True)
    (root / "rebuild" / "kernel-rs" / "src").mkdir(parents=True)
    (root / "rebuild" / "review" / "static").mkdir(parents=True)
    (root / "rebuild" / "out").mkdir(parents=True)
    (root / "site").mkdir(parents=True)
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text("family: qsPea\n")
    (root / "glyph_data" / "runes" / "qsBay.yaml").write_text("family: qsBay\n")
    (root / "glyph_data" / "punctuation.yaml").write_text("dots: []\n")
    (root / "glyph_data" / "senior_quikscript_kerning.yaml").write_text("pairs: []\n")
    (root / "rebuild" / "script.yaml").write_text("alphabet: []\n")
    (root / "rebuild" / "schema" / "rune.schema.json").write_text("{}\n")
    (root / "rebuild" / "m1-contact-allow.yaml").write_text("[]\n")
    (root / "rebuild" / "m1-aliases.yaml").write_text("[]\n")
    (root / "rebuild" / "m1-divergences.yaml").write_text("[]\n")
    (root / "rebuild" / "pipeline" / "table.py").write_text("TABLE = 1\n")
    (root / "rebuild" / "kernel-rs" / "Cargo.toml").write_text("[package]\nname = 'kernel'\n")
    (root / "rebuild" / "kernel-rs" / "Cargo.lock").write_text("lock\n")
    (root / "rebuild" / "kernel-rs" / "src" / "guard.rs").write_text("const GUARD: bool = true;\n")
    (root / "rebuild" / "validation").mkdir(parents=True)
    (root / "rebuild" / "validation" / "shaping.py").write_text("SENIOR_FONT = 1\n")
    (root / "rebuild" / "review" / "build.py").write_text("BUILD = 1\n")
    (root / "rebuild" / "review" / "serve.py").write_text("SERVE = 1\n")
    (root / "rebuild" / "review" / "static" / "app.js").write_text("export const app = 1;\n")
    (root / "rebuild" / "out" / "baseline-default.tsv.gz").write_bytes(b"x" * 64)
    (root / "rebuild" / "out" / "digests.tsv").write_text("default\tabc123\n")
    (root / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf").write_bytes(b"senior-font")
    (root / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf").write_bytes(b"junior-font")
    return root


def test_file_sha256_matches_the_read_whole_digest(tmp_path):
    payloads = {
        "empty.bin": b"",
        "small.yaml": b"family: qsPea\n",
        "multi-chunk.bin": bytes(range(256)) * 8192,
    }
    for name, payload in payloads.items():
        path = tmp_path / name
        path.write_bytes(payload)
        assert fingerprint.file_sha256(path) == hashlib.sha256(payload).hexdigest()


def _read_whole_hashes(path):
    """The line of every `hashlib.<algo>(<expr>.read_bytes())` in one module — the spelling that holds a whole file in memory to hash it."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func, first = node.func, node.args[0]
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "hashlib"
            and isinstance(first, ast.Call)
            and isinstance(first.func, ast.Attribute)
            and first.func.attr == "read_bytes"
        ):
            found.append(node.lineno)
    return found


def test_no_rebuild_module_hashes_a_file_it_read_whole():
    closure = artifact_cycle.rebuild_gate_closure_files(REPO_ROOT)
    assert closure is not None, "the rebuild closure needs git, and without it there is nothing to sweep"
    modules = [
        rel
        for rel in closure
        if rel.endswith(".py") and rel.startswith("rebuild/") and not Path(rel).name.startswith("test_")
    ]
    assert "rebuild/pipeline/fingerprint.py" in modules, "the sweep reached nothing; the closure moved"
    offenders = sorted(f"{rel}:{line}" for rel in modules for line in _read_whole_hashes(REPO_ROOT / rel))
    assert offenders == [], (
        "these hash a file they read whole, which costs its size in resident memory; call "
        f"fingerprint.file_sha256 instead: {', '.join(offenders)}"
    )


def test_the_inline_streamed_reads_answer_what_the_helper_does(tmp_path):
    sample = tmp_path / "sample.otf"
    sample.write_bytes(bytes(range(256)) * 4096)
    expected = fingerprint.file_sha256(sample)
    assert artifact_cycle._sha256_path(sample) == expected
    assert model.font_sha256(sample) == expected


def test_hash_paths_is_content_sensitive_and_stable(tmp_path):
    root = _fake_repo(tmp_path)
    before = fingerprint.hash_paths(root, fingerprint.data_paths(root))
    assert before == fingerprint.hash_paths(root, fingerprint.data_paths(root))
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text("family: qsPea\nedited: true\n")
    assert fingerprint.hash_paths(root, fingerprint.data_paths(root)) != before


def test_hash_paths_ignores_argument_order(tmp_path):
    root = _fake_repo(tmp_path)
    paths = fingerprint.data_paths(root)
    assert fingerprint.hash_paths(root, paths) == fingerprint.hash_paths(root, list(reversed(paths)))


def test_hash_paths_skips_missing_files(tmp_path):
    root = _fake_repo(tmp_path)
    paths = fingerprint.data_paths(root)
    with_ghost = paths + [root / "glyph_data" / "runes" / "qsGhost.yaml"]
    assert fingerprint.hash_paths(root, with_ghost) == fingerprint.hash_paths(root, paths)


def test_baselines_value_tracks_size_not_mtime(tmp_path):
    root = _fake_repo(tmp_path)
    before = fingerprint.baselines_value(root)
    (root / "rebuild" / "out" / "baseline-default.tsv.gz").write_bytes(b"x" * 64)
    assert fingerprint.baselines_value(root) == before
    (root / "rebuild" / "out" / "baseline-default.tsv.gz").write_bytes(b"x" * 65)
    assert fingerprint.baselines_value(root) != before


def test_baselines_value_tracks_digests_content(tmp_path):
    root = _fake_repo(tmp_path)
    before = fingerprint.baselines_value(root)
    (root / "rebuild" / "out" / "digests.tsv").write_text("default\tdef456\n")
    assert fingerprint.baselines_value(root) != before


def test_review_code_excludes_the_non_build_modules(tmp_path):
    """serve.py, status.py, journal.py, and export.py never run in the surface build, so editing one must not stale the surface, drop the per-unit caches, or fail the validators lane — the plumbing key hashes the ones the verdict chain runs."""
    root = _fake_repo(tmp_path)
    for name in sorted(fingerprint.REVIEW_NON_BUILD_MODULES):
        assert root / "rebuild" / "review" / name not in fingerprint.review_code_paths(root)
    before = fingerprint.hash_paths(root, fingerprint.review_code_paths(root))
    for name in sorted(fingerprint.REVIEW_NON_BUILD_MODULES):
        (root / "rebuild" / "review" / name).write_text(f"# edited {name}\n")
    assert fingerprint.hash_paths(root, fingerprint.review_code_paths(root)) == before


def test_stage_a_round_trip(tmp_path):
    root = _fake_repo(tmp_path)
    out_dir = root / "rebuild" / "out" / "m1"
    out_dir.mkdir(parents=True)
    record = fingerprint.write_stage_a(root, out_dir)
    assert record["format"] == fingerprint.FORMAT
    values = fingerprint.read_stage_a(out_dir)
    assert values == {key: record[key] for key in fingerprint.STAGE_A_COMPONENTS}


def test_read_stage_a_tolerates_missing_and_malformed(tmp_path):
    assert fingerprint.read_stage_a(tmp_path / "nowhere") is None
    (tmp_path / fingerprint.STAGE_A_FILENAME).write_text("not json")
    assert fingerprint.read_stage_a(tmp_path) is None
    (tmp_path / fingerprint.STAGE_A_FILENAME).write_text(json.dumps({"data": "x"}))
    assert fingerprint.read_stage_a(tmp_path) is None


def test_compute_all_covers_every_component_and_isolates_edits(tmp_path):
    root = _fake_repo(tmp_path)
    before = fingerprint.compute_all(root)
    assert set(before) == set(fingerprint.COMPONENTS)
    assert all(isinstance(value, str) for value in before.values())
    (root / "glyph_data" / "runes" / "qsBay.yaml").write_text("family: qsBay\nedited: true\n")
    after = fingerprint.compute_all(root)
    assert after["data"] != before["data"]
    assert {key: after[key] for key in fingerprint.COMPONENTS if key != "data"} == {
        key: before[key] for key in fingerprint.COMPONENTS if key != "data"
    }


def test_data_lines_carry_one_label_per_file_and_hash_to_data_value(tmp_path):
    root = _fake_repo(tmp_path)
    lines = fingerprint.data_lines(root)
    labels = [line.split("\t", 1)[0] for line in lines]
    assert "glyph_data/runes/qsPea.yaml" in labels
    assert len(labels) == len(set(labels))
    assert fingerprint.data_value(root) == hashlib.sha256("\n".join(lines).encode()).hexdigest()


def test_table_data_lines_drop_exactly_the_comparison_and_defect_inputs(tmp_path):
    root = _fake_repo(tmp_path)
    labels = {line.split("\t", 1)[0] for line in fingerprint.data_lines(root)}
    table_labels = {line.split("\t", 1)[0] for line in fingerprint.table_data_lines(root)}
    assert labels - table_labels == set(fingerprint.NON_TABLE_DATA_LABELS)
    assert (
        fingerprint.table_data_value(root)
        == hashlib.sha256("\n".join(fingerprint.table_data_lines(root)).encode()).hexdigest()
    )


def test_an_alias_ledger_or_allow_list_edit_moves_the_run_key_but_not_the_tables_stamp(tmp_path):
    """The whole point of the narrowing, and the line it must not cross. Those three files are read by gates that consume a decision table — the oracle's comparison and the defect gate's allow-list — so a serialized enumeration built before the edit still describes the sources on disk and `--gates-only` may re-adjudicate against it. They stay in `data_value` and so in the artifact cycle's run_m1 key, which is what decides whether the defect gate re-runs at all, so narrowing the stamp cannot skip a gate."""
    root = _fake_repo(tmp_path)
    for label in fingerprint.NON_TABLE_DATA_LABELS:
        before = (
            fingerprint.data_value(root),
            fingerprint.tables_value(root),
            artifact_cycle.run_m1_skip_fingerprint(root),
        )
        (root / label).write_text("# edited\n[]\n")
        assert fingerprint.data_value(root) != before[0]
        assert fingerprint.tables_value(root) == before[1]
        assert artifact_cycle.run_m1_skip_fingerprint(root) != before[2]


def test_the_tables_stamp_still_tracks_the_runes_and_the_pipeline_code(tmp_path):
    root = _fake_repo(tmp_path)
    before = fingerprint.tables_value(root)
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text("family: qsPea\nedited: true\n")
    moved_rune = fingerprint.tables_value(root)
    assert moved_rune != before
    (root / "rebuild" / "script.yaml").write_text("alphabet: [edited]\n")
    moved_data = fingerprint.tables_value(root)
    assert moved_data != moved_rune
    (root / "rebuild" / "pipeline" / "table.py").write_text("TABLE = 2\n")
    moved_code = fingerprint.tables_value(root)
    assert moved_code != moved_data
    (root / "rebuild" / "kernel-rs" / "src" / "guard.rs").write_text("const GUARD: bool = false;\n")
    assert fingerprint.tables_value(root) != moved_code


def test_rune_digests_key_by_family_name(tmp_path):
    root = _fake_repo(tmp_path)
    digests = fingerprint.rune_digests(root)
    assert set(digests) == {"qsPea", "qsBay"}
    assert digests["qsPea"] == fingerprint.rune_file_digest(root / "glyph_data" / "runes" / "qsPea.yaml")


def test_pipeline_code_covers_validation_and_the_kernel_and_isolates_edits(tmp_path):
    root = _fake_repo(tmp_path)
    assert root / "rebuild" / "validation" / "shaping.py" in fingerprint.pipeline_code_paths(root)
    assert root / "rebuild" / "kernel-rs" / "Cargo.toml" in fingerprint.pipeline_code_paths(root)
    assert root / "rebuild" / "kernel-rs" / "Cargo.lock" in fingerprint.pipeline_code_paths(root)
    assert root / "rebuild" / "kernel-rs" / "src" / "guard.rs" in fingerprint.pipeline_code_paths(root)
    before = fingerprint.compute_all(root)
    (root / "rebuild" / "validation" / "shaping.py").write_text("SENIOR_FONT = 2\n")
    after = fingerprint.compute_all(root)
    assert after["pipeline_code"] != before["pipeline_code"]
    assert {key: after[key] for key in fingerprint.COMPONENTS if key != "pipeline_code"} == {
        key: before[key] for key in fingerprint.COMPONENTS if key != "pipeline_code"
    }
    (root / "rebuild" / "kernel-rs" / "src" / "guard.rs").write_text("const GUARD: bool = false;\n")
    after_kernel = fingerprint.compute_all(root)
    assert after_kernel["pipeline_code"] != after["pipeline_code"]
    assert {key: after_kernel[key] for key in fingerprint.COMPONENTS if key != "pipeline_code"} == {
        key: after[key] for key in fingerprint.COMPONENTS if key != "pipeline_code"
    }


PROSE_RUNE = textwrap.dedent("""\
    rune: qsPea
    codepoint: 0xE650
    ductus:
      hapax: |
        A deep stroke, drawn downward.
    notes: |
      Cannot join at the x-height twice.
    stances:
      hapax:
        motion: hapax
        bitmap: ["#", "#"]
        surface:
          unlocks:
          - {feature: ss03, why: original unlock rationale}
    policy:
      refuse:
      - {exit: baseline, why: two verticals render thick}
      prefer:
      - {stance: hapax, why: nicer to write}
    """)


def _data_after(root, text):
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text(text)
    return fingerprint.data_value(root)


def test_data_value_ignores_comments_and_formatting(tmp_path):
    root = _fake_repo(tmp_path)
    before = _data_after(root, PROSE_RUNE)
    assert _data_after(root, PROSE_RUNE.replace("ductus:", "ductus: # DRAFT")) == before
    assert _data_after(root, PROSE_RUNE.replace('bitmap: ["#", "#"]', 'bitmap: [ "#",   "#" ]')) == before


def test_data_value_ignores_ductus_prose_but_not_motion_names(tmp_path):
    root = _fake_repo(tmp_path)
    before = _data_after(root, PROSE_RUNE)
    assert _data_after(root, PROSE_RUNE.replace("drawn downward", "drawn upward")) == before
    assert _data_after(root, PROSE_RUNE.replace("ductus:\n  hapax:", "ductus:\n  pole:")) != before


def test_data_value_ignores_notes_prose_but_not_notes_presence(tmp_path):
    root = _fake_repo(tmp_path)
    before = _data_after(root, PROSE_RUNE)
    assert _data_after(root, PROSE_RUNE.replace("Cannot join", "Must not join")) == before
    without_notes = PROSE_RUNE.replace("notes: |\n  Cannot join at the x-height twice.\n", "")
    assert _data_after(root, without_notes) != before


def test_data_value_ignores_unquoted_whys_but_not_refuse_why(tmp_path):
    root = _fake_repo(tmp_path)
    before = _data_after(root, PROSE_RUNE)
    assert _data_after(root, PROSE_RUNE.replace("nicer to write", "easier to write")) == before
    assert _data_after(root, PROSE_RUNE.replace("original unlock rationale", "reworded rationale")) == before
    assert _data_after(root, PROSE_RUNE.replace(", why: nicer to write}", "}")) != before
    assert _data_after(root, PROSE_RUNE.replace("render thick", "render thin")) != before


def test_data_value_tracks_semantic_edits(tmp_path):
    root = _fake_repo(tmp_path)
    before = _data_after(root, PROSE_RUNE)
    assert _data_after(root, PROSE_RUNE.replace('bitmap: ["#", "#"]', 'bitmap: ["#", "##"]')) != before


def test_data_value_falls_back_to_bytes_on_unparseable_rune(tmp_path):
    root = _fake_repo(tmp_path)
    before = _data_after(root, "rune: qsPea\n\t: [broken")
    assert _data_after(root, "rune: qsPea\n\t: [broken again") != before


def test_data_value_tracks_non_rune_data_bytes(tmp_path):
    root = _fake_repo(tmp_path)
    before = fingerprint.data_value(root)
    (root / "rebuild" / "m1-aliases.yaml").write_text("[] # commented\n")
    assert fingerprint.data_value(root) != before


def test_stage_a_data_component_is_the_prose_blind_value(tmp_path):
    root = _fake_repo(tmp_path)
    assert fingerprint.stage_a(root)["data"] == fingerprint.data_value(root)

"""Tests for rebuild/pipeline/fingerprint.py: the streamed file digest and the sweep that checks no rebuild/ module hashes a file read whole, the path hashes (content sensitivity, order independence, missing files), the baselines component, the Stage A record, the prose-blind rune, ledger, code, lock, and font digests, and the explain-aware rune digest and `explain_prose` component, and the gate closures that hash code raw.

`file_sha256` streams a file so that hashing it never holds the whole file in memory. That helps only if every hash in rebuild/ goes through it, so the read-whole sweep checks this in code; a list of callers in a docstring would go stale unnoticed. Modules that cannot import fingerprint copy the streamed read inline, and a test here checks that each copy returns the same value as `file_sha256`.
"""

import ast
import hashlib
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

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
    (root / "tools").mkdir(parents=True)
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
    (root / "rebuild" / "pipeline" / "conform.py").write_text("CONFORM = 1\n")
    (root / "rebuild" / "pipeline" / "oracle.py").write_text("ORACLE = 1\n")
    (root / "rebuild" / "pipeline" / "oracle_positions.py").write_text("POSITIONS = 1\n")
    (root / "rebuild" / "kernel-rs" / "Cargo.toml").write_text("[package]\nname = 'kernel'\n")
    (root / "rebuild" / "kernel-rs" / "Cargo.lock").write_text("lock\n")
    (root / "rebuild" / "kernel-rs" / "src" / "guard.rs").write_text("const GUARD: bool = true;\n")
    (root / "rebuild" / "validation").mkdir(parents=True)
    (root / "rebuild" / "validation" / "shaping.py").write_text("SENIOR_FONT = 1\n")
    (root / "rebuild" / "review" / "build.py").write_text("BUILD = 1\n")
    (root / "rebuild" / "review" / "serve.py").write_text("SERVE = 1\n")
    (root / "rebuild" / "review" / "static" / "app.js").write_text("export const app = 1;\n")
    (root / "tools" / "build_font.py").write_text("BUILD_FONT = 1\n")
    (root / "tools" / "glyph_compiler.py").write_text("GLYPH_COMPILER = 1\n")
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


READ_WHOLE_EXEMPT = {
    "rebuild/pipeline/fingerprint.py": frozenset({"code_file_digest"}),
    "rebuild/tools/lock_digest.py": frozenset({"lock_digest"}),
}
"""The functions the sweep lets read a file whole, by module: `code_file_digest` projects a source file before hashing it, and its dispatch on the suffix before the file is opened bounds what it reads to the `.py` and `.rs` sources, none of them large; `lock_digest` projects the lock's text, and the lock is tens of kilobytes."""


def _read_whole_hashes(path, exempt_functions=frozenset()):
    """Return the line of every `hashlib.<algo>(...)` call in a module whose arguments contain a `.read_bytes()` call, however it is wrapped, outside the functions named in `exempt_functions`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    exempt_spans = [
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in exempt_functions
    ]
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "hashlib"
        ):
            continue
        if any(start <= node.lineno <= end for start, end in exempt_spans):
            continue
        reads_whole = any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "read_bytes"
            for argument in node.args
            for inner in ast.walk(argument)
        )
        if reads_whole:
            found.append(node.lineno)
    return found


def test_the_read_whole_sweep_sees_a_wrapped_read(tmp_path):
    module = tmp_path / "module.py"
    module.write_text(
        "import hashlib\n"
        "def direct(path):\n"
        "    return hashlib.sha256(path.read_bytes()).hexdigest()\n"
        "def wrapped(path):\n"
        "    return hashlib.sha256(project(path.read_bytes()).encode()).hexdigest()\n"
        "def streamed(path):\n"
        "    return hashlib.sha256(b'').hexdigest()\n",
        encoding="utf-8",
    )
    assert _read_whole_hashes(module) == [3, 5]
    assert _read_whole_hashes(module, frozenset({"wrapped"})) == [3]


def test_no_rebuild_module_hashes_a_file_it_read_whole():
    closure = artifact_cycle.rebuild_gate_closure_files(REPO_ROOT)
    assert closure is not None, "the rebuild closure needs git, and without it there is nothing to sweep"
    modules = [
        rel
        for rel in closure
        if rel.endswith(".py") and rel.startswith("rebuild/") and not Path(rel).name.startswith("test_")
    ]
    assert "rebuild/pipeline/fingerprint.py" in modules, "the sweep reached nothing; the closure moved"
    offenders = sorted(
        f"{rel}:{line}"
        for rel in modules
        for line in _read_whole_hashes(REPO_ROOT / rel, READ_WHOLE_EXEMPT.get(rel, frozenset()))
    )
    assert offenders == [], (
        "these hash a file they read whole, which costs its size in resident memory; call "
        f"fingerprint.file_sha256 instead, or name the function in READ_WHOLE_EXEMPT with its bound: {', '.join(offenders)}"
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
    """The modules in `REVIEW_NON_BUILD_MODULES` never run in the surface build, so editing one must not change `review_code`."""
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


def test_table_data_lines_drop_exactly_the_comparison_side_inputs(tmp_path):
    """`table_data_lines` is `data_lines` without the labels in `NON_TABLE_DATA_LABELS`."""
    root = _fake_repo(tmp_path)
    labels = {line.split("\t", 1)[0] for line in fingerprint.data_lines(root)}
    table_labels = {line.split("\t", 1)[0] for line in fingerprint.table_data_lines(root)}
    assert labels - table_labels == set(fingerprint.NON_TABLE_DATA_LABELS)
    assert (
        fingerprint.table_data_value(root)
        == hashlib.sha256("\n".join(fingerprint.table_data_lines(root)).encode()).hexdigest()
    )


def test_a_comparison_side_data_edit_moves_the_run_key_but_not_the_tables_stamp(tmp_path):
    """Editing an input in `NON_TABLE_DATA_LABELS` moves `data_value` and the run_m1 skip key but not `tables_value`. The oracle reads the alias map and the divergence ledger to name and classify divergences, and only `oracle_positions.KernEvaluator` reads the kern sidecar (the font compile never opens it), so an enumeration built before the edit still matches its sources and `--gates-only` can re-run the comparison over it. The run_m1 key still moves, so the gates still re-run.

    The edit is structural because the divergence ledger hashes prose-blind, so a comment-only edit to it would move nothing.
    """
    root = _fake_repo(tmp_path)
    assert "glyph_data/senior_quikscript_kerning.yaml" in fingerprint.NON_TABLE_DATA_LABELS
    for label in fingerprint.NON_TABLE_DATA_LABELS:
        before = (
            fingerprint.data_value(root),
            fingerprint.tables_value(root),
            artifact_cycle.run_m1_skip_fingerprint(root),
        )
        (root / label).write_text("# edited\n- {id: added}\n")
        assert fingerprint.data_value(root) != before[0]
        assert fingerprint.tables_value(root) == before[1]
        assert artifact_cycle.run_m1_skip_fingerprint(root) != before[2]


ALLOW_LIST = textwrap.dedent("""\
    # Reviewed declared-OK signatures for the off-anchor-contact gate.
    - signature: contact:qsOy.hapax.ex-y0:qsIt.hapax.en-y0:y1
      why: the corner today's font already draws on a baseline-proven join
    """)


def _allow_after(root, text):
    (root / fingerprint.CONTACT_ALLOW_LABEL).write_text(text)
    return (
        fingerprint.data_value(root),
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    )


def test_blessing_a_contact_signature_moves_the_run_key_alone(tmp_path):
    """The contact allow-list is in no fingerprint component and only in the run_m1 skip key. The defect gate is its only reader, so a signature change must re-run that gate without restamping the surface or dropping the review unit cache or the oracle row cache, whose stamps (`unit_cache.environment_stamp`, `oracle_cache.stamped_data_paths`) are built from `data_paths`."""
    root = _fake_repo(tmp_path)
    before = _allow_after(root, ALLOW_LIST)
    assert root / fingerprint.CONTACT_ALLOW_LABEL not in fingerprint.data_paths(root)
    assert fingerprint.CONTACT_ALLOW_LABEL not in {
        line.split("\t", 1)[0] for line in fingerprint.data_lines(root)
    }
    after = _allow_after(root, ALLOW_LIST.replace(":y1", ":y2"))
    assert after[:2] == before[:2]
    assert after[2] != before[2]


def test_wording_a_bless_or_reformatting_the_allow_list_moves_nothing(tmp_path):
    """An allow-list entry's `why` reaches no gate, so rewording it moves none of the keys. The digest is taken over the parsed document, so adding a comment or a blank line moves none of them either."""
    root = _fake_repo(tmp_path)
    before = _allow_after(root, ALLOW_LIST)
    assert _allow_after(root, ALLOW_LIST.replace("already draws on", "has always drawn on")) == before
    assert _allow_after(root, ALLOW_LIST.replace("gate.\n", "gate.\n# and a second line.\n")) == before
    assert _allow_after(root, ALLOW_LIST.replace("- signature:", "\n- signature:")) == before


def test_contact_allow_digest_is_prose_blind_and_falls_back_to_bytes(tmp_path):
    """A signature change moves the digest and a `why` change does not. A malformed allow-list digests to its raw bytes, so two different broken drafts get different values; `defects.run_gates` rejects either one."""
    path = tmp_path / "m1-contact-allow.yaml"
    path.write_text(ALLOW_LIST)
    parsed = fingerprint.contact_allow_digest(path)
    path.write_text(ALLOW_LIST.replace("already draws on", "has always drawn on"))
    assert fingerprint.contact_allow_digest(path) == parsed
    path.write_text(ALLOW_LIST.replace(":y1", ":y2"))
    assert fingerprint.contact_allow_digest(path) != parsed
    broken = "- signature: [unclosed\n"
    path.write_text(broken)
    broken_digest = fingerprint.contact_allow_digest(path)
    assert broken_digest == hashlib.sha256(broken.encode()).hexdigest()
    path.write_text("- signature: [unclosed again\n")
    assert fingerprint.contact_allow_digest(path) not in (parsed, broken_digest)


LEDGER = textwrap.dedent("""\
    # The M1 divergence ledger: one entry per reviewed divergence class.
    - id: boundary-echo
      status: intended
      no_verdict: true
      match: {predicate: boundary_echo, configs: all}
      count: 12
      exemplars:
        - {config: default, codepoints: "0020:E650", baseline: "space|qsPea", new: "space|qsPea.half"}
      why: |
        A window holding a run-splitting boundary never needs its own verdict.
    - id: seam-moved
      status: drift-accepted
      match: {predicate: seam_moved, configs: all}
      count: 3
      exemplars:
        - {config: default, codepoints: "E67A:E665", baseline: "qsUtter|qsMay.en-y5", new: "qsUtter|qsMay.en-y0"}
      why: |
        The old shadow stance joined at the x-height where word-initial settlement lands at the baseline.
    """)


STANDING = textwrap.dedent("""\
    # Once-and-for-all pattern rules, so a blessed delta shape never reaches the docket again.
    format: ams-standing-approvals/1
    rules:
      - id: tea-oy-ligature-break
        verdict: approve
        note: "never going to have a different opinion unless the left letter is \u00b7Out"
        match:
          before: {pivot: qsTea.half, follower: qsOy}
          after: {ligature: qsTea_qsOy}
          except_left: [qsOut]
    """)

REWORDED_CLASS = LEDGER.replace("never needs its own verdict", "wants no verdict of its own")
REWORDED_RULE = STANDING.replace("never going to have a different opinion", "settled for good")


def _ledger_digest(path, text):
    path.write_text(text)
    return fingerprint.divergence_ledger_digest(path)


def test_divergence_ledger_digest_is_prose_blind_and_falls_back_to_bytes(tmp_path):
    """Every ledger field except `why` moves the digest, since `audit.load_ledger`, `oracle.classify_divergence`, and the census read them: changing a class's status, its `no_verdict` flag, its count, or an exemplar, or adding a class. Rewording a `why`, editing a comment, or reformatting moves nothing. A malformed ledger digests to its raw bytes, so two broken drafts get different values."""
    path = tmp_path / "m1-divergences.yaml"
    parsed = _ledger_digest(path, LEDGER)
    assert _ledger_digest(path, REWORDED_CLASS) == parsed
    assert _ledger_digest(path, LEDGER.replace("# The M1", "# Retitled: the M1")) == parsed
    reflowed = LEDGER.replace(
        "match: {predicate: seam_moved, configs: all}",
        "match:\n    predicate: seam_moved\n    configs: all",
    )
    assert _ledger_digest(path, reflowed) == parsed
    assert _ledger_digest(path, LEDGER.replace("no_verdict: true", "no_verdict: false")) != parsed
    assert (
        _ledger_digest(path, LEDGER.replace("status: drift-accepted", "status: reviewed-approved")) != parsed
    )
    assert _ledger_digest(path, LEDGER.replace("count: 12", "count: 13")) != parsed
    assert (
        _ledger_digest(path, LEDGER.replace('new: "space|qsPea.half"', 'new: "space|qsPea.full"')) != parsed
    )
    assert _ledger_digest(path, LEDGER + "- id: added\n  status: intended\n  count: 0\n") != parsed
    broken = "- id: boundary-echo\n  match: {predicate: unclosed\n"
    broken_digest = _ledger_digest(path, broken)
    assert broken_digest == hashlib.sha256(broken.encode()).hexdigest()
    assert _ledger_digest(path, broken.replace("unclosed", "still unclosed")) not in (parsed, broken_digest)


def _standing_digest(path, text):
    path.write_text(text)
    return fingerprint.standing_approvals_digest(path)


def test_standing_approvals_digest_is_note_blind_and_falls_back_to_bytes(tmp_path):
    """`verdict`, `match`, and `except_left` move the digest; a rule's `note` and the file's comments don't. Only the artifact cycle's rebuild-lane closure reads this digest. The plumbing key hashes the file raw, because the standing fill copies each `note` into the verdicts it writes. A malformed file digests to its raw bytes."""
    path = tmp_path / "standing-approvals.yaml"
    parsed = _standing_digest(path, STANDING)
    assert _standing_digest(path, REWORDED_RULE) == parsed
    assert _standing_digest(path, STANDING.replace("# Once-and", "# Edited: once-and")) == parsed
    assert _standing_digest(path, STANDING.replace("verdict: approve", "verdict: reject")) != parsed
    assert _standing_digest(path, STANDING.replace("follower: qsOy", "follower: qsI")) != parsed
    assert _standing_digest(path, STANDING.replace("except_left: [qsOut]", "except_left: []")) != parsed
    added = STANDING + "  - id: added\n    verdict: approve\n    match: {before: {pivot: qsMay}}\n"
    assert _standing_digest(path, added) != parsed
    broken = "format: ams-standing-approvals/1\nrules: [unclosed\n"
    broken_digest = _standing_digest(path, broken)
    assert broken_digest == hashlib.sha256(broken.encode()).hexdigest()
    assert _standing_digest(path, broken.replace("unclosed", "still unclosed")) not in (parsed, broken_digest)


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


def test_an_oracle_code_edit_moves_the_run_key_but_not_the_tables_stamp(tmp_path):
    """Editing oracle.py or oracle_positions.py moves `pipeline_code` and the run_m1 skip key but not `tables_value`, so `--gates-only` can re-run the comparison over the enumeration on disk while a full run still rebuilds everything. `pipeline_code_paths` globs all of rebuild/pipeline/ and `COMPARISON_CODE_MODULES` is the only subtraction, so each file is checked by name. conform.py produces what the oracle classifies and stays in the tables' stamp."""
    root = _fake_repo(tmp_path)
    for name in ("oracle.py", "oracle_positions.py"):
        assert root / "rebuild" / "pipeline" / name not in fingerprint.table_code_paths(root)
        assert root / "rebuild" / "pipeline" / name in fingerprint.pipeline_code_paths(root)
    assert root / "rebuild" / "pipeline" / "conform.py" in fingerprint.table_code_paths(root)
    assert root / "rebuild" / "validation" / "shaping.py" in fingerprint.table_code_paths(root)
    assert root / "rebuild" / "kernel-rs" / "src" / "guard.rs" in fingerprint.table_code_paths(root)
    before = (
        fingerprint.compute_all(root)["pipeline_code"],
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    )
    (root / "rebuild" / "pipeline" / "oracle.py").write_text("ORACLE = 2\n")
    assert fingerprint.compute_all(root)["pipeline_code"] != before[0]
    assert fingerprint.tables_value(root) == before[1]
    assert artifact_cycle.run_m1_skip_fingerprint(root) != before[2]
    moved_classifier = (
        fingerprint.compute_all(root)["pipeline_code"],
        artifact_cycle.run_m1_skip_fingerprint(root),
    )
    (root / "rebuild" / "pipeline" / "oracle_positions.py").write_text("POSITIONS = 2\n")
    assert fingerprint.compute_all(root)["pipeline_code"] != moved_classifier[0]
    assert fingerprint.tables_value(root) == before[1]
    assert artifact_cycle.run_m1_skip_fingerprint(root) != moved_classifier[1]
    (root / "rebuild" / "pipeline" / "conform.py").write_text("CONFORM = 2\n")
    assert fingerprint.tables_value(root) != before[1]


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
    assert root / "tools" / "build_font.py" in fingerprint.pipeline_code_paths(root)
    assert root / "tools" / "glyph_compiler.py" in fingerprint.pipeline_code_paths(root)
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


def test_a_font_compile_tool_edit_moves_the_run_key_and_the_tables_stamp(tmp_path):
    """compile_font passes the mini font to tools/build_font.py, which runs the glyph compiler, the IR, and the FEA emitter, so an edit to that tools/ closure changes M1.otf. It must move `pipeline_code` (and with it the Stage A record and the surface's stamp), the run_m1 skip key, and `tables_value` (and with it the conform sweep's check). The review unit cache's stamps use their own code lists (`unit_cache.surface_code_paths`, `unit_cache.signature_code_paths`), which leave the font compile out because neither the surface build nor the comparator runs it. The unit store's stamp still sees a tools/ edit through its draft-harness line, which hashes every tools/*.py."""
    root = _fake_repo(tmp_path)
    for name in ("build_font.py", "glyph_compiler.py"):
        assert root / "tools" / name in fingerprint.pipeline_code_paths(root)
        assert root / "tools" / name in fingerprint.table_code_paths(root)
    before = fingerprint.compute_all(root)
    before_tables = fingerprint.tables_value(root)
    before_run = artifact_cycle.run_m1_skip_fingerprint(root)
    (root / "tools" / "build_font.py").write_text("BUILD_FONT = 2\n")
    after = fingerprint.compute_all(root)
    assert after["pipeline_code"] != before["pipeline_code"]
    assert {key: after[key] for key in fingerprint.COMPONENTS if key != "pipeline_code"} == {
        key: before[key] for key in fingerprint.COMPONENTS if key != "pipeline_code"
    }
    assert fingerprint.tables_value(root) != before_tables
    assert artifact_cycle.run_m1_skip_fingerprint(root) != before_run


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


def test_data_value_ignores_every_why_but_not_why_presence(tmp_path):
    """No `why` in a rune changes `data_value` or `tables_value`, a refusal's included: the crate uses a refuse `why` only in explain ladders, which the fixpoint never requests. Removing a `why` still moves `data_value`, because the schema requires a `why` on an absolute prefer and a missing one is a load failure."""
    root = _fake_repo(tmp_path)
    before = _data_after(root, PROSE_RUNE)
    tables = fingerprint.tables_value(root)
    assert _data_after(root, PROSE_RUNE.replace("nicer to write", "easier to write")) == before
    assert _data_after(root, PROSE_RUNE.replace("original unlock rationale", "reworded rationale")) == before
    assert _data_after(root, PROSE_RUNE.replace("render thick", "render thin")) == before
    assert fingerprint.tables_value(root) == tables
    assert _data_after(root, PROSE_RUNE.replace(", why: nicer to write}", "}")) != before
    assert _data_after(root, PROSE_RUNE.replace(", why: two verticals render thick}", "}")) != before


def _explain_after(root, text):
    path = root / "glyph_data" / "runes" / "qsPea.yaml"
    path.write_text(text)
    return fingerprint.rune_explain_digest(path)


def test_rune_explain_digest_moves_with_the_refuse_why_alone(tmp_path):
    """`rune_explain_digest` moves when a refuse `why` is reworded and on no other prose, comment, or formatting edit. A rune whose refusals have no `why` gets the same value from `rune_explain_digests` as from `rune_digests`."""
    root = _fake_repo(tmp_path)
    before = _explain_after(root, PROSE_RUNE)
    assert _explain_after(root, PROSE_RUNE.replace("render thick", "render thin")) != before
    assert _explain_after(root, PROSE_RUNE.replace("nicer to write", "easier to write")) == before
    assert _explain_after(root, PROSE_RUNE.replace("original unlock rationale", "reworded")) == before
    assert _explain_after(root, PROSE_RUNE.replace("Cannot join", "Must not join")) == before
    assert _explain_after(root, PROSE_RUNE.replace("drawn downward", "drawn upward")) == before
    assert _explain_after(root, PROSE_RUNE.replace("ductus:", "ductus: # DRAFT")) == before
    assert _explain_after(root, PROSE_RUNE.replace('bitmap: ["#", "#"]', 'bitmap: [ "#",   "#" ]')) == before
    explain_digests = fingerprint.rune_explain_digests(root)
    assert set(explain_digests) == set(fingerprint.rune_digests(root)) == {"qsPea", "qsBay"}
    assert explain_digests["qsPea"] == before
    assert explain_digests["qsBay"] == fingerprint.rune_digests(root)["qsBay"]


def test_refuse_prose_lines_name_the_family_and_the_record(tmp_path):
    """One line per refusal with a `why`, indexed so two refusals in one file stay distinct. A rune that fails to parse contributes a raw-digest line, so a broken file reads as changed instead of as a rune with no refusals."""
    root = _fake_repo(tmp_path)
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text(PROSE_RUNE)
    assert fingerprint.refuse_prose_lines(root) == ["qsPea\t0\ttwo verticals render thick"]
    assert (
        fingerprint.explain_prose_value(root)
        == hashlib.sha256("\n".join(fingerprint.refuse_prose_lines(root)).encode()).hexdigest()
    )
    broken = "rune: qsPea\n\t: [broken"
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text(broken)
    assert fingerprint.refuse_prose_lines(root) == [
        f"qsPea\t-\t{hashlib.sha256(broken.encode()).hexdigest()}"
    ]


def test_explain_prose_is_the_one_component_a_refuse_why_moves(tmp_path):
    """Rewording a refusal moves only `explain_prose`, so it restamps the surface and leaves `data`, `baselines`, and `pipeline_code` unchanged, and with `data` the run_m1 skip key. Stage A never includes it, because run_m1 reads no refuse prose. A bitmap edit moves only `data`."""
    root = _fake_repo(tmp_path)
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text(PROSE_RUNE)
    before = fingerprint.compute_all(root)
    assert "explain_prose" in fingerprint.STAGE_B_COMPONENTS
    assert "explain_prose" not in fingerprint.stage_a(root)
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text(
        PROSE_RUNE.replace("render thick", "render thin")
    )
    after = fingerprint.compute_all(root)
    assert after["explain_prose"] != before["explain_prose"]
    assert {key: after[key] for key in fingerprint.COMPONENTS if key != "explain_prose"} == {
        key: before[key] for key in fingerprint.COMPONENTS if key != "explain_prose"
    }
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text(
        PROSE_RUNE.replace("render thick", "render thin").replace('bitmap: ["#", "#"]', 'bitmap: ["#", "##"]')
    )
    bitmap = fingerprint.compute_all(root)
    assert bitmap["data"] != after["data"]
    assert {key: bitmap[key] for key in fingerprint.COMPONENTS if key != "data"} == {
        key: after[key] for key in fingerprint.COMPONENTS if key != "data"
    }


def test_explain_prose_follows_the_spec_root_a_build_names(tmp_path):
    """A workload bundled with its own frozen spec serves that spec's rationales, so `explain_prose` is computed over the spec root when a build names one and over the checkout otherwise. The other Stage B components come from the checkout either way."""
    root = _fake_repo(tmp_path / "checkout")
    spec_root = _fake_repo(tmp_path / "spec")
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text(PROSE_RUNE)
    (spec_root / "glyph_data" / "runes" / "qsPea.yaml").write_text(
        PROSE_RUNE.replace("render thick", "render thin")
    )
    fonts = (root / "site" / "before.otf", root / "site" / "junior.otf")
    bundled = fingerprint.stage_b(root, *fonts, spec_root)
    checkout = fingerprint.stage_b(root, *fonts)
    assert bundled["explain_prose"] == fingerprint.explain_prose_value(spec_root)
    assert checkout["explain_prose"] == fingerprint.explain_prose_value(root)
    assert bundled["explain_prose"] != checkout["explain_prose"]
    assert {key: value for key, value in bundled.items() if key != "explain_prose"} == {
        key: value for key, value in checkout.items() if key != "explain_prose"
    }


def _ledger_components(root, text):
    (root / fingerprint.DIVERGENCE_LEDGER_LABEL).write_text(text)
    return fingerprint.compute_all(root)


def test_the_divergence_ledger_line_carries_the_prose_blind_digest(tmp_path):
    """The ledger's `data_lines` entry uses `divergence_ledger_digest`, and the ledger is in `NON_TABLE_DATA_LABELS`. `_data_digest` picks the digest by label, so this also checks that the ledger's path in `data_paths` matches `DIVERGENCE_LEDGER_LABEL`; a mismatch would hash the ledger raw."""
    root = _fake_repo(tmp_path)
    ledger = root / fingerprint.DIVERGENCE_LEDGER_LABEL
    ledger.write_text(LEDGER)
    digests = dict(line.split("\t", 1) for line in fingerprint.data_lines(root))
    assert digests[fingerprint.DIVERGENCE_LEDGER_LABEL] == fingerprint.divergence_ledger_digest(ledger)
    assert fingerprint.DIVERGENCE_LEDGER_LABEL in fingerprint.NON_TABLE_DATA_LABELS


def test_wording_a_divergence_class_moves_the_explain_component_alone(tmp_path):
    """The review build copies a class's `why` into the manifest's `classes[].why`, so the `why` is hashed into `explain_prose` alongside the refuse `why`s. Rewording a class moves only that component: `data`, `tables_value`, and the run_m1 skip key, which is built from `data_lines`, stay. The surface rebuild it causes is served from the unit cache, because no shard carries the text."""
    root = _fake_repo(tmp_path)
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text(PROSE_RUNE)
    before = _ledger_components(root, LEDGER)
    before_tables = fingerprint.tables_value(root)
    after = _ledger_components(root, REWORDED_CLASS)
    assert after["explain_prose"] != before["explain_prose"]
    assert {key: after[key] for key in fingerprint.COMPONENTS if key != "explain_prose"} == {
        key: before[key] for key in fingerprint.COMPONENTS if key != "explain_prose"
    }
    assert fingerprint.tables_value(root) == before_tables
    (root / "glyph_data" / "runes" / "qsPea.yaml").write_text(
        PROSE_RUNE.replace("render thick", "render thin")
    )
    both = fingerprint.compute_all(root)
    assert both["explain_prose"] not in (before["explain_prose"], after["explain_prose"])


def test_retriaging_a_divergence_class_moves_the_data_component_and_not_the_tables_stamp(tmp_path):
    """Flipping `no_verdict` changes which units the surface asks for a verdict on and which rows the audit counts as adjudicated, so it moves `data` and the run_m1 skip key. The ledger is in `NON_TABLE_DATA_LABELS`, so `tables_value` and `explain_prose` stay, and the cycle re-runs the comparison over the tables and font on disk."""
    root = _fake_repo(tmp_path)
    before = _ledger_components(root, LEDGER)
    before_tables = fingerprint.tables_value(root)
    before_run = artifact_cycle.run_m1_skip_fingerprint(root)
    after = _ledger_components(root, LEDGER.replace("no_verdict: true", "no_verdict: false"))
    assert after["data"] != before["data"]
    assert after["explain_prose"] == before["explain_prose"]
    assert fingerprint.tables_value(root) == before_tables
    assert artifact_cycle.run_m1_skip_fingerprint(root) != before_run


def test_ledger_prose_lines_name_the_class(tmp_path):
    """One line per class with a `why`, keyed by the class id the manifest uses, with `ledger` as the first field so it cannot collide with a refuse line's family name. A ledger that fails to parse contributes a raw-digest line, so a broken file reads as changed instead of as a ledger with no rationales."""
    root = _fake_repo(tmp_path)
    ledger = root / fingerprint.DIVERGENCE_LEDGER_LABEL
    ledger.write_text(LEDGER)
    assert fingerprint.ledger_prose_lines(root) == [
        "ledger\tboundary-echo\tA window holding a run-splitting boundary never needs its own verdict.\n",
        "ledger\tseam-moved\tThe old shadow stance joined at the x-height where word-initial settlement lands at the baseline.\n",
    ]
    combined = sorted(fingerprint.refuse_prose_lines(root) + fingerprint.ledger_prose_lines(root))
    assert fingerprint.explain_prose_value(root) == hashlib.sha256("\n".join(combined).encode()).hexdigest()
    broken = "- id: boundary-echo\n  match: {predicate: unclosed\n"
    ledger.write_text(broken)
    assert fingerprint.ledger_prose_lines(root) == [
        f"ledger\t-\t{hashlib.sha256(broken.encode()).hexdigest()}"
    ]


def test_the_standing_approvals_reach_no_fingerprint_component(tmp_path):
    """No component includes the standing approvals, so neither a reworded note nor a changed rule changes `compute_all`, and neither can restamp the surface or drop the review unit cache. A rule change moves `standing_approvals_digest`, which the rebuild-lane closure uses. The plumbing key hashes the file raw, so a changed note moves that key too."""
    root = _fake_repo(tmp_path)
    rules = root / fingerprint.STANDING_APPROVALS_LABEL
    rules.write_text(STANDING)
    assert rules not in fingerprint.data_paths(root)
    before = fingerprint.compute_all(root)
    before_digest = fingerprint.standing_approvals_digest(rules)
    rules.write_text(REWORDED_RULE)
    assert fingerprint.compute_all(root) == before
    assert fingerprint.standing_approvals_digest(rules) == before_digest
    rules.write_text(STANDING.replace("verdict: approve", "verdict: reject"))
    assert fingerprint.compute_all(root) == before
    assert fingerprint.standing_approvals_digest(rules) != before_digest


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


PYTHON_MODULE = textwrap.dedent('''\
    """The module's own account of itself."""

    import hashlib


    class Stamp:
        """A stamp over a roster."""

        def digest(self, lines: list[str], salt: str = "seed") -> str:
            """One digest for one roster."""
            return hashlib.sha256("\\n".join(lines).encode()).hexdigest() + salt


    def label(path):
        """The label a line carries."""
        if path is None:
            raise ValueError("a line needs a path")
        return str(path)
    ''')


def _code_digest(path, text):
    path.write_text(text, encoding="utf-8")
    return fingerprint.code_file_digest(path)


def test_code_digest_is_docstring_blind_for_python(tmp_path):
    """Rewording a module, class, method, or function docstring, adding a `#` comment, or changing blank lines leaves the digest unchanged. Changing an identifier, a non-docstring string (error text a matcher compares against), an annotation, a default, or a decorator moves it. Removing a docstring moves it too, because the projection keeps each docstring's presence, as the rune projection keeps each `why`'s."""
    path = tmp_path / "module.py"
    parsed = _code_digest(path, PYTHON_MODULE)
    assert _code_digest(path, PYTHON_MODULE.replace("own account of itself", "own story")) == parsed
    assert _code_digest(path, PYTHON_MODULE.replace("A stamp over a roster", "A roster's stamp")) == parsed
    assert _code_digest(path, PYTHON_MODULE.replace("One digest for one roster", "One digest")) == parsed
    assert _code_digest(path, PYTHON_MODULE.replace("The label a line carries", "The line's label")) == parsed
    assert (
        _code_digest(path, PYTHON_MODULE.replace("import hashlib", "# a comment\nimport hashlib")) == parsed
    )
    assert (
        _code_digest(path, PYTHON_MODULE.replace("    def digest", "    # trailing\n    def digest"))
        == parsed
    )
    assert _code_digest(path, PYTHON_MODULE.replace("\n\n\nclass", "\nclass")) == parsed
    assert _code_digest(path, PYTHON_MODULE.replace("\n\n\ndef label", "\n\n\n\n\ndef label")) == parsed
    assert _code_digest(path, PYTHON_MODULE.replace("def label(path)", "def name(path)")) != parsed
    assert _code_digest(path, PYTHON_MODULE.replace("a line needs a path", "a path is needed")) != parsed
    assert _code_digest(path, PYTHON_MODULE.replace("lines: list[str]", "lines: tuple[str]")) != parsed
    assert _code_digest(path, PYTHON_MODULE.replace('salt: str = "seed"', 'salt: str = "salt"')) != parsed
    assert (
        _code_digest(path, PYTHON_MODULE.replace("    def digest", "    @staticmethod\n    def digest"))
        != parsed
    )
    assert _code_digest(path, PYTHON_MODULE.replace('    """A stamp over a roster."""\n\n', "")) != parsed
    assert _code_digest(path, "") != _code_digest(path, '"""Only a docstring."""\n')


def test_code_digest_falls_back_to_bytes_on_unparseable_python(tmp_path):
    """A module that fails to parse or decode digests to its raw bytes, so two differently broken drafts get different values."""
    path = tmp_path / "module.py"
    parsed = _code_digest(path, PYTHON_MODULE)
    broken = "def label(path:\n"
    broken_digest = _code_digest(path, broken)
    assert broken_digest == fingerprint.file_sha256(path) == hashlib.sha256(broken.encode()).hexdigest()
    assert _code_digest(path, broken.replace("path", "path, salt")) not in (parsed, broken_digest)
    undecodable = tmp_path / "latin.py"
    undecodable.write_bytes(b"# -*- coding: utf-8 -*-\nNAME = '\xff'\n")
    assert fingerprint.code_file_digest(undecodable) == fingerprint.file_sha256(undecodable)


RUST_MODULE = textwrap.dedent("""\
    //! The crate's own account of itself.

    /// A guard over a transition.
    pub const GUARD: bool = true; // trailing note

    // Whether the wave claims heavy deltas first.
    pub fn heavy_first() -> bool {
        true
    }
    """)


def test_code_digest_drops_whole_line_rust_comments_only(tmp_path):
    """Rewording a `//`, `///`, or `//!` line leaves the digest unchanged, including an indented one. Changing a code line moves it, and so does changing the trailing comment on a code line, because the projection keeps the whole line. A non-UTF-8 file digests to its raw bytes."""
    path = tmp_path / "guard.rs"
    parsed = _code_digest(path, RUST_MODULE)
    assert _code_digest(path, RUST_MODULE.replace("own account of itself", "own story")) == parsed
    assert (
        _code_digest(path, RUST_MODULE.replace("A guard over a transition", "A transition's guard")) == parsed
    )
    assert (
        _code_digest(path, RUST_MODULE.replace("claims heavy deltas first", "claims the heavy deltas"))
        == parsed
    )
    assert _code_digest(path, RUST_MODULE.replace("// Whether", "    // Whether")) == parsed
    assert _code_digest(path, RUST_MODULE.replace("GUARD: bool = true", "GUARD: bool = false")) != parsed
    assert _code_digest(path, RUST_MODULE.replace("// trailing note", "// reworded note")) != parsed
    assert _code_digest(path, RUST_MODULE.replace("    true\n", "    false\n")) != parsed
    undecodable = tmp_path / "latin.rs"
    undecodable.write_bytes(b'pub const NAME: &str = "\xff";\n')
    assert fingerprint.code_file_digest(undecodable) == fingerprint.file_sha256(undecodable)


def test_path_lines_hash_everything_but_code_raw(tmp_path):
    """Only Python and Rust sources are projected. YAML, the app's JavaScript, the crate's manifest and lock, and fonts get `file_sha256`'s value in `path_lines`."""
    root = _fake_repo(tmp_path)
    raw = {
        root / "glyph_data" / "punctuation.yaml",
        root / "rebuild" / "review" / "static" / "app.js",
        root / "rebuild" / "kernel-rs" / "Cargo.toml",
        root / "rebuild" / "kernel-rs" / "Cargo.lock",
        root / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
    }
    projected = {
        root / "rebuild" / "pipeline" / "table.py",
        root / "rebuild" / "kernel-rs" / "src" / "guard.rs",
    }
    digests = dict(line.split("\t", 1) for line in fingerprint.path_lines(root, sorted(raw | projected)))
    assert set(digests) == {fingerprint._label(root, path) for path in raw | projected}
    for path in raw:
        assert digests[fingerprint._label(root, path)] == fingerprint.file_sha256(path)
        assert fingerprint.code_file_digest(path) == fingerprint.file_sha256(path)
    for path in projected:
        assert digests[fingerprint._label(root, path)] == fingerprint.code_file_digest(path)
        assert digests[fingerprint._label(root, path)] != fingerprint.file_sha256(path)


def test_moved_note_expands_a_changed_label_through_its_sub_diff():
    """A changed label with a note in `expand` is shown as `label: note`. A label whose note is None, an added or removed label, and a label not in `expand` are shown as they are without `expand`. `limit` counts labels."""
    recorded = {"code": "a", "data": "x", "old": "1"}
    current = {"code": "b", "data": "y", "new": "2"}
    assert (
        fingerprint.moved_note(recorded, current) == "code (changed), data (changed), new (new), old (gone)"
    )
    assert (
        fingerprint.moved_note(recorded, current, expand={"code": "rebuild/review/ink.py (changed)"})
        == "code: rebuild/review/ink.py (changed), data (changed), new (new), old (gone)"
    )
    assert fingerprint.moved_note(
        recorded, current, expand={"code": None, "new": "never", "old": "never"}
    ) == ("code (changed), data (changed), new (new), old (gone)")
    assert (
        fingerprint.moved_note(recorded, current, limit=2, expand={"code": "ink.py (changed)"})
        == "code: ink.py (changed), data (changed) and 2 more"
    )
    assert fingerprint.moved_note(recorded, recorded, expand={"code": "ink.py (changed)"}) is None


def test_an_environment_stamp_hashes_its_lines_and_carries_its_detail_outside_them():
    """`value` and `labels` depend only on `lines`, so a store that starts recording `detail` keeps its recorded value. `detail_labels` returns a label's detail map, or an empty map for a label without one."""
    lines = ("format\tf/1", "surface_code\tabc", "data\tddd")
    bare = fingerprint.EnvironmentStamp(lines=lines)
    detailed = fingerprint.EnvironmentStamp(
        lines=lines, detail=(("surface_code", ("rebuild/review/ink.py\t1", "rebuild/review/build.py\t2")),)
    )
    assert detailed.value == bare.value == hashlib.sha256("\n".join(lines).encode()).hexdigest()
    assert detailed.labels == bare.labels == {"format": "f/1", "surface_code": "abc", "data": "ddd"}
    assert detailed.detail_labels("surface_code") == {
        "rebuild/review/ink.py": "1",
        "rebuild/review/build.py": "2",
    }
    assert detailed.detail_labels("data") == {} == bare.detail_labels("surface_code")


def _code_keyed(root):
    return (
        fingerprint.compute_all(root)["pipeline_code"],
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    )


def test_a_docstring_edit_moves_no_code_keyed_component(tmp_path):
    """Adding and then rewording a docstring in a pipeline module and `//` lines in the crate leaves `pipeline_code`, `tables_value`, and the run_m1 skip key unchanged. A statement edit in either file moves all three."""
    root = _fake_repo(tmp_path)
    table = root / "rebuild" / "pipeline" / "table.py"
    guard = root / "rebuild" / "kernel-rs" / "src" / "guard.rs"
    table.write_text('"""The table."""\n\nTABLE = 1\n')
    guard.write_text("// The guard.\nconst GUARD: bool = true;\n")
    before = _code_keyed(root)
    table.write_text('"""The table, reworded."""\n\n\nTABLE = 1  # noted\n')
    guard.write_text("// The guard, reworded.\n// And a second line.\nconst GUARD: bool = true;\n")
    assert _code_keyed(root) == before
    table.write_text('"""The table, reworded."""\n\n\nTABLE = 2  # noted\n')
    moved = _code_keyed(root)
    assert all(after != earlier for after, earlier in zip(moved, before))
    guard.write_text("// The guard, reworded.\nconst GUARD: bool = false;\n")
    assert all(after != earlier for after, earlier in zip(_code_keyed(root), moved))


def test_the_gate_closures_stay_raw_through_a_docstring_edit(tmp_path):
    """The rebuild-lane closure and gate:make-test's closure hash files raw, because test fixtures and the closure tests read source text. So a docstring-only edit that moves no fingerprint component still moves each lane key and the make-test key. One edited file is in each closure: a pipeline module is in the lane closure but not in gate:make-test's (`make_test_exempt` excludes rebuild/), and a font-compile tools/ module is in gate:make-test's closure and in `pipeline_code`. The fake repo is a git repo because both closures are listed with `git ls-files`."""
    root = _fake_repo(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "Makefile").write_text("all:\n\t@true\ntest:\n\t@true\n")
    (root / "conftest.py").write_text("")
    (root / "pyproject.toml").write_text("[project]\nname = 'fake'\n")
    (root / "uv.lock").write_text("lock\n")
    table = root / "rebuild" / "pipeline" / "table.py"
    build_font = root / "tools" / "build_font.py"
    table.write_text('"""The table."""\n\nTABLE = 1\n')
    build_font.write_text('"""The compile."""\n\nBUILD_FONT = 1\n')
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    lanes = {
        lane: artifact_cycle.rebuild_lane_fingerprint(root, lane) for lane in artifact_cycle.REBUILD_LANES
    }
    make_test = artifact_cycle.make_test_closure_fingerprint(root)
    assert all(key is not None for key in lanes.values()) and make_test is not None
    components = fingerprint.compute_all(root)
    table.write_text('"""The table, reworded."""\n\nTABLE = 1\n')
    build_font.write_text('"""The compile, reworded."""\n\nBUILD_FONT = 1\n')
    assert fingerprint.compute_all(root) == components
    for lane in artifact_cycle.REBUILD_LANES:
        assert artifact_cycle.rebuild_lane_fingerprint(root, lane) != lanes[lane], lane
    assert artifact_cycle.make_test_closure_fingerprint(root) != make_test


def test_the_projection_is_memoized_and_reprojects_when_the_file_moves(tmp_path, monkeypatch):
    """Repeated `hash_paths` calls over the same paths parse each Python file once per distinct content. A file rewritten with different content in the same instant still gets a new digest, which a key on size and mtime could miss."""
    root = _fake_repo(tmp_path)
    table = root / "rebuild" / "pipeline" / "table.py"
    table.write_text(f'"""Unique to this run."""\n\nTOKEN = "{tmp_path.name}-a"\n')
    parsed: list[object] = []
    real_parse = ast.parse

    def counting_parse(source, *args, **kwargs):
        parsed.append(source)
        return real_parse(source, *args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting_parse)
    paths = fingerprint.table_code_paths(root)
    first = fingerprint.hash_paths(root, paths)
    assert parsed.count(table.read_bytes()) == 1
    cold = len(parsed)
    assert fingerprint.hash_paths(root, paths) == first
    assert fingerprint.hash_paths(root, paths) == first
    assert len(parsed) == cold
    table.write_text(f'"""Unique to this run."""\n\nTOKEN = "{tmp_path.name}-b"\n')
    assert fingerprint.hash_paths(root, paths) != first
    assert len(parsed) == cold + 1
    assert parsed.count(table.read_bytes()) == 1


MINI_FONT = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini" / "M1.otf"

LOCK = textwrap.dedent("""\
    version = 1
    revision = 3
    requires-python = ">=3.14"

    [[package]]
    name = "abbots-morton-spaceport"
    version = "16.0.0"
    source = { virtual = "." }
    dependencies = [
        { name = "fonttools" },
    ]

    [package.dev-dependencies]
    dev = [
        { name = "uharfbuzz" },
    ]

    [package.metadata]
    requires-dist = [
        { name = "fonttools", specifier = ">=4.61.1" },
    ]

    [[package]]
    name = "fonttools"
    version = "4.61.1"
    source = { registry = "https://pypi.org/simple" }

    [[package]]
    name = "uharfbuzz"
    version = "0.50.2"
    source = { registry = "https://pypi.org/simple" }
    """)
LOCK_BUMPED = LOCK.replace('version = "16.0.0"', 'version = "16.1.0"').replace(
    'name = "fonttools", specifier = ">=4.61.1"', 'name = "fonttools", specifier = ">=4.61.2"'
)
LOCK_PIN_MOVED = LOCK.replace('version = "4.61.1"', 'version = "4.62.0"')
LOCK_PACKAGE_ADDED = (
    LOCK
    + '\n[[package]]\nname = "pyyaml"\nversion = "6.0.3"\nsource = { registry = "https://pypi.org/simple" }\n'
)
LOCK_PACKAGE_REMOVED = LOCK[: LOCK.index('\n[[package]]\nname = "uharfbuzz"')] + "\n"


def _lock_digest(path, text):
    path.write_text(text, encoding="utf-8")
    return fingerprint.lock_digest(path)


def test_lock_digest_is_blind_to_the_projects_own_block_and_to_nothing_else(tmp_path):
    """A version bump rewrites the project's own `[[package]]` block, including the specifiers in its `[package.metadata]` sub-table, and the digest ignores that block. A changed dependency pin, an added package, and a removed package each move it, because each changes the environment the build and the tests run in."""
    path = tmp_path / "uv.lock"
    base = _lock_digest(path, LOCK)
    assert _lock_digest(path, LOCK_BUMPED) == base
    assert _lock_digest(path, LOCK_PIN_MOVED) != base
    assert _lock_digest(path, LOCK_PACKAGE_ADDED) != base
    assert _lock_digest(path, LOCK_PACKAGE_REMOVED) != base
    assert len({base, _lock_digest(path, LOCK_PIN_MOVED), _lock_digest(path, LOCK_PACKAGE_ADDED)}) == 3
    assert base != fingerprint.file_sha256(path)


def test_lock_digest_falls_back_to_the_raw_bytes_without_a_project_block(tmp_path):
    """A lock without a `source = { virtual = "." }` block, such as `lock-1` or `version = 1`, and a lock that cannot be decoded each digest to their raw bytes. The suite's fake repos depend on this: two different fake locks must get different values, so an edit from `lock-1` to `lock-2` still shows."""
    path = tmp_path / "uv.lock"
    for text in ("lock-1\n", "lock-2\n", "version = 1\n", ""):
        assert _lock_digest(path, text) == hashlib.sha256(text.encode()).hexdigest()
    assert _lock_digest(path, "lock-1\n") != _lock_digest(path, "lock-2\n")
    path.write_bytes(b"\xff\xfe not text")
    assert fingerprint.lock_digest(path) == fingerprint.file_sha256(path)
    assert fingerprint.lock_digest(path) != _lock_digest(path, "lock-1\n")


def _font(source, target, edit):
    from fontTools.ttLib import TTFont

    font = TTFont(str(source))
    edit(font)
    font.save(str(target))
    return target


def _bump_version(font):
    font["head"].fontRevision = (
        font["head"].fontRevision + 0.001
    )  # pyright: ignore[reportAttributeAccessIssue]
    for record in font["name"].names:  # pyright: ignore[reportAttributeAccessIssue]
        record.string = record.toUnicode() + " bumped"


def _widen_a_glyph(font):
    metrics = font["hmtx"].metrics  # pyright: ignore[reportAttributeAccessIssue]
    name = sorted(name for name in metrics if name.startswith("qs"))[0]
    advance, lsb = metrics[name]
    metrics[name] = (advance + 10, lsb)


def _move_an_anchor(font):
    for lookup in font["GPOS"].table.LookupList.Lookup:  # pyright: ignore[reportAttributeAccessIssue]
        for subtable in lookup.SubTable:
            if lookup.LookupType == 9:
                subtable = subtable.ExtSubTable
            if getattr(subtable, "LookupType", lookup.LookupType) != 3:
                continue
            for record in subtable.EntryExitRecord:
                anchor = record.ExitAnchor or record.EntryAnchor
                if anchor is not None:
                    anchor.XCoordinate += 1
                    return
    raise AssertionError("the mini font carries no cursive anchor to move")


def _drop_a_table(font):
    del font["gasp"]


def _add_a_table(font):
    from fontTools.ttLib.tables.DefaultTable import DefaultTable

    table = DefaultTable("ZZZZ")
    table.data = b"\0\0\0\1"
    font["ZZZZ"] = table


def test_font_content_digest_is_blind_to_head_and_name_and_to_nothing_else(tmp_path):
    """Rewriting `head.fontRevision` and every `name` record of the checked-in mini font, as `make all` does to both site fonts on a version bump (the save also updates `head.modified` and the checksum adjustment), leaves the digest unchanged. Changing a glyph's advance or a cursive anchor's coordinate, dropping a table, or adding one each moves it. Every case goes through a fontTools save, so the test uses fonts a shaper would read, not patched bytes."""
    base = fingerprint.font_content_digest(MINI_FONT)
    bumped = _font(MINI_FONT, tmp_path / "bumped.otf", _bump_version)
    assert fingerprint.file_sha256(bumped) != fingerprint.file_sha256(MINI_FONT)
    assert fingerprint.font_content_digest(bumped) == base
    moved = {
        name: fingerprint.font_content_digest(_font(MINI_FONT, tmp_path / f"{name}.otf", edit))
        for name, edit in (
            ("widened", _widen_a_glyph),
            ("anchor", _move_an_anchor),
            ("dropped", _drop_a_table),
            ("added", _add_a_table),
        )
    }
    assert all(digest != base for digest in moved.values()), moved
    assert len(set(moved.values())) == len(moved)


def _builder_font(target, units_per_em):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    builder = FontBuilder(units_per_em, isTTF=False)
    names = [".notdef", "square"]
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({0xE650: "square"})
    charstrings = {}
    for name in names:
        pen = T2CharStringPen(100, None)
        pen.moveTo((0, 0))
        pen.lineTo((0, 100))
        pen.lineTo((100, 100))
        pen.lineTo((100, 0))
        pen.closePath()
        charstrings[name] = pen.getCharString()
    builder.setupCFF(
        psName="Square-Regular",
        fontInfo={"FamilyName": "Square", "FullName": "Square Regular"},
        charStringsDict=charstrings,
        privateDict={},
    )
    builder.setupHorizontalMetrics({name: (100, 0) for name in names})
    builder.setupHorizontalHeader(ascent=450, descent=-100)
    builder.setupNameTable({"familyName": "Square", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.save(str(target))
    return target


def test_a_units_per_em_edit_moves_the_font_projection_through_the_cff_font_matrix(tmp_path):
    """tools/build_font.py passes `FontBuilder.setupCFF` a `fontInfo` with no FontMatrix, so fontTools writes the `CFF ` top dict's FontMatrix as 1/unitsPerEm. Two fonts built that way that differ only in unitsPerEm therefore differ in `CFF ` as well as `head`, and the projection, which keeps `CFF `, tells them apart. If a fontTools release stops deriving the matrix, this test fails before a unitsPerEm edit in glyph_data/metadata.yaml can go unseen by every font key."""
    from fontTools.ttLib import TTFont

    fonts = {upem: _builder_font(tmp_path / f"upem-{upem}.otf", upem) for upem in (550, 1100)}
    tables = {}
    for upem, path in fonts.items():
        font = TTFont(str(path))
        matrix = font["CFF "].cff.topDictIndex[0].FontMatrix  # pyright: ignore[reportAttributeAccessIssue]
        assert matrix == pytest.approx([1 / upem, 0, 0, 1 / upem, 0, 0])
        assert font.reader is not None
        tables[upem] = {tag: font.reader[tag] for tag in font.reader.keys()}
    narrow, wide = tables[550], tables[1100]
    assert narrow.keys() == wide.keys()
    assert {tag for tag in narrow if narrow[tag] != wide[tag]} == {"head", "CFF "}
    assert fingerprint.font_content_digest(fonts[550]) != fingerprint.font_content_digest(fonts[1100])


def test_font_content_digest_falls_back_to_the_raw_bytes_for_a_file_that_is_no_sfnt(tmp_path):
    """A fake font, an empty file, and a truncated real font each digest to their raw bytes instead of raising or sharing a value. rebuild/test_baseline_subset.py's fake site font depends on this."""
    fake = tmp_path / "fake.otf"
    fake.write_bytes(b"not really an otf")
    empty = tmp_path / "empty.otf"
    empty.write_bytes(b"")
    truncated = tmp_path / "truncated.otf"
    truncated.write_bytes(MINI_FONT.read_bytes()[:3000])
    for path in (fake, empty, truncated):
        assert fingerprint.font_content_digest(path) == fingerprint.file_sha256(path)
    assert len({fingerprint.font_content_digest(path) for path in (fake, empty, truncated)}) == 3
    assert fingerprint.font_content_digest(MINI_FONT) != fingerprint.file_sha256(MINI_FONT)


def test_the_font_projection_is_memoized_on_the_bytes_and_reprojects_when_they_move(tmp_path, monkeypatch):
    """The memo is keyed on the raw digest, as in `code_file_digest`: the same bytes are projected once however many times they are asked for, and a file rewritten with different bytes is projected again."""
    projected: list[Path] = []
    real = fingerprint._projected_font_lines

    def counting(path):
        projected.append(path)
        return real(path)

    monkeypatch.setattr(fingerprint, "_projected_font_lines", counting)
    monkeypatch.setattr(fingerprint, "_FONT_DIGESTS", {})
    font = tmp_path / "font.otf"
    font.write_bytes(MINI_FONT.read_bytes())
    first = fingerprint.font_content_digest(font)
    assert fingerprint.font_content_digest(font) == first
    assert fingerprint.font_content_digest(MINI_FONT) == first
    assert len(projected) == 1
    _font(MINI_FONT, font, _widen_a_glyph)
    assert fingerprint.font_content_digest(font) != first
    assert len(projected) == 2


def _version_bump_keys(root):
    before, junior = fingerprint.font_paths(root)
    _key, labels = artifact_cycle.rebuild_lane_closure(root, artifact_cycle.REBUILD_LANES[0])
    assert labels is not None, "the lane closure needs git"
    return (
        fingerprint.stage_b(root, before, junior)["fonts"],
        labels["fonts"],
        artifact_cycle.run_m1_skip_files(root)["uv.lock"],
    )


def test_a_version_bump_moves_no_key_while_a_glyph_an_anchor_and_a_pin_move_each(tmp_path):
    """Checks three keys together: the Stage B `fonts` component, the contracts lane's `fonts` label, and the run_m1 skip key's `uv.lock` line. Rewriting only `head` and `name` in both site fonts and only the project version in the lock moves none of them. A glyph edit and a GPOS change each move both font keys, and a dependency-pin change moves the lock line. The fake repo is a git repo because the lane closure is listed with `git ls-files`."""
    root = _fake_repo(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "conftest.py").write_text("")
    (root / "pyproject.toml").write_text("[project]\nname = 'fake'\n")
    lock = root / "uv.lock"
    lock.write_text(LOCK)
    senior, junior = fingerprint.font_paths(root)
    senior.write_bytes(MINI_FONT.read_bytes())
    junior.write_bytes(MINI_FONT.read_bytes())
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    base = _version_bump_keys(root)
    assert all(value is not None for value in base)

    _font(MINI_FONT, senior, _bump_version)
    _font(MINI_FONT, junior, _bump_version)
    lock.write_text(LOCK_BUMPED)
    assert _version_bump_keys(root) == base

    _font(MINI_FONT, senior, _widen_a_glyph)
    glyph = _version_bump_keys(root)
    assert glyph[0] != base[0] and glyph[1] != base[1] and glyph[2] == base[2]
    _font(MINI_FONT, senior, _move_an_anchor)
    anchor = _version_bump_keys(root)
    assert anchor[0] != base[0] and anchor[1] != base[1] and anchor[2] == base[2]
    assert anchor[:2] != glyph[:2]

    senior.write_bytes(MINI_FONT.read_bytes())
    lock.write_text(LOCK_PIN_MOVED)
    pin = _version_bump_keys(root)
    assert pin[:2] == base[:2] and pin[2] != base[2]

"""Tests for rebuild/pipeline/fingerprint.py: the streamed file digest and the sweep that checks no rebuild/ module hashes a file read whole, the path hashes (content sensitivity, order independence, missing files), the Stage A record, the prose-insensitive rune, allow-list, code, lock, and font digests, and the gate closures that hash code raw.

`file_sha256` streams a file so that hashing it never holds the whole file in memory. That helps only if every hash in rebuild/ goes through it, so the read-whole sweep checks this in code; a list of callers in a docstring would go stale unnoticed. Modules that cannot import fingerprint copy the streamed read inline, and a test here checks that each copy returns the same value as `file_sha256`.
"""

import ast
import hashlib
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from rebuild.pipeline import fingerprint, row_delta
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
    (root / "rebuild" / "script.yaml").write_text("alphabet: []\n")
    (root / "rebuild" / "schema" / "rune.schema.json").write_text("{}\n")
    (root / "rebuild" / "m1-contact-allow.yaml").write_text("[]\n")
    (root / "rebuild" / "pipeline" / "table.py").write_text("TABLE = 1\n")
    (root / "rebuild" / "pipeline" / "conform.py").write_text("CONFORM = 1\n")
    (root / "rebuild" / "kernel-rs" / "Cargo.toml").write_text("[package]\nname = 'kernel'\n")
    (root / "rebuild" / "kernel-rs" / "Cargo.lock").write_text("lock\n")
    (root / "rebuild" / "kernel-rs" / "src" / "guard.rs").write_text("const GUARD: bool = true;\n")
    (root / "rebuild" / "validation").mkdir(parents=True)
    (root / "rebuild" / "validation" / "shaping.py").write_text("SHAPER = 1\n")
    (root / "rebuild" / "review" / "build.py").write_text("BUILD = 1\n")
    (root / "rebuild" / "review" / "serve.py").write_text("SERVE = 1\n")
    (root / "rebuild" / "review" / "static" / "app.js").write_text("export const app = 1;\n")
    (root / "tools" / "build_font.py").write_text("BUILD_FONT = 1\n")
    (root / "tools" / "glyph_compiler.py").write_text("GLYPH_COMPILER = 1\n")
    (root / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf").write_bytes(b"senior-font")
    (root / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf").write_bytes(b"junior-font")
    return root


# Every component a tree's sources give without a build or a snapshot: the Stage A record's, and the table-diff corpus's `review_code`, `static` and `font_inputs`.
COMPONENTS = (*fingerprint.STAGE_A_COMPONENTS, "review_code", "static", "font_inputs")


def _components(root: Path) -> dict:
    return {
        **fingerprint.stage_a(root),
        "review_code": fingerprint.hash_paths(root, fingerprint.review_code_paths(root)),
        "static": fingerprint.hash_paths(root, fingerprint.static_paths(root)),
        "font_inputs": fingerprint.font_inputs_value(root),
    }


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
    assert row_delta.file_sha256(sample) == expected


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


def test_review_code_excludes_the_non_build_modules(tmp_path):
    """The modules in `REVIEW_NON_BUILD_MODULES` never run in the corpus build, so editing one must not change `review_code`."""
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
    record = fingerprint.write_stage_a(out_dir, fingerprint.stage_a(root))
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
    before = _components(root)
    assert set(before) == set(COMPONENTS)
    assert all(isinstance(value, str) for value in before.values())
    (root / "glyph_data" / "runes" / "qsBay.yaml").write_text("family: qsBay\nedited: true\n")
    after = _components(root)
    assert after["data"] != before["data"]
    assert {key: after[key] for key in COMPONENTS if key != "data"} == {
        key: before[key] for key in COMPONENTS if key != "data"
    }


def test_data_lines_carry_one_label_per_file_and_hash_to_data_value(tmp_path):
    root = _fake_repo(tmp_path)
    lines = fingerprint.data_lines(root)
    labels = [line.split("\t", 1)[0] for line in lines]
    assert "glyph_data/runes/qsPea.yaml" in labels
    assert len(labels) == len(set(labels))
    assert fingerprint.data_value(root) == hashlib.sha256("\n".join(lines).encode()).hexdigest()


ALLOW_LIST = textwrap.dedent("""\
    # Reviewed accepted signatures for the off-anchor-contact gate.
    - signature: contact:qsOy.sole.ex-y0:qsIt.sole.en-y0:y1
      why: the off-junction contact the old font already draws on a join it also makes
    """)


def _allow_after(root, text):
    (root / fingerprint.CONTACT_ALLOW_LABEL).write_text(text)
    return (
        fingerprint.data_value(root),
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    )


def test_blessing_a_contact_signature_moves_the_run_key_alone(tmp_path):
    """The contact allow-list is in no fingerprint component and only in the run_m1 skip key. The defect gate is its only reader, so a signature change must re-run that gate without moving the settle memo's stamp (`settle_memo_keys.stamped_data_paths`), which is built from `data_paths`."""
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


def test_contact_allow_digest_ignores_prose_and_falls_back_to_bytes(tmp_path):
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


KERNED_RUNE = textwrap.dedent("""\
    rune: qsPea
    stances: {}
    kern:
    - {right: {family: qsBay}, by: -2}
    """)


def test_a_kern_edit_moves_only_the_rune_s_kern_line_and_the_suite_closure(tmp_path):
    """A rune's `kern:` block reaches no table or settlement, so `rune_file_digest` leaves it out and every key built from it stays put: the tables' stamp, the `data` component and Stage A. It reaches the run_m1 skip key through the rune's kern line alone (`kern_lines`), which is the one label that moves, so the cycle reads the edit as a kern-only rebuild (`artifact_cycle.kern_only_rebuild`). The rebuild suite's closure hashes the whole document (`rune_document_digest`). Rewording a kern record's `why` moves nothing."""
    root = _fake_repo(tmp_path)
    rune = root / "glyph_data" / "runes" / "qsPea.yaml"
    rune.write_text(KERNED_RUNE)

    def keys():
        return (
            fingerprint.tables_value(root),
            fingerprint.data_value(root),
            fingerprint.rune_file_digest(rune),
            artifact_cycle.run_m1_skip_fingerprint(root),
            fingerprint.rune_kern_digest(rune),
            fingerprint.rune_document_digest(rune),
        )

    before = keys()
    lines_before = artifact_cycle.run_m1_skip_files(root)
    rune.write_text(KERNED_RUNE.replace("by: -2", "by: -1, why: A rationale"))
    edited = keys()
    assert edited[:3] == before[:3]
    assert all(edited[index] != before[index] for index in (3, 4, 5))
    moved = artifact_cycle.moved_input_labels({"files": lines_before}, artifact_cycle.run_m1_skip_files(root))
    assert moved == ["glyph_data/runes/qsPea.yaml#kern"]
    assert (
        artifact_cycle.kern_only_rebuild({"files": lines_before}, artifact_cycle.run_m1_skip_files(root))
        == moved
    )
    rune.write_text(KERNED_RUNE.replace("by: -2", "by: -1, why: Another rationale"))
    assert keys() == edited
    rune.write_text(KERNED_RUNE.replace("kern:\n- {right: {family: qsBay}, by: -2}\n", ""))
    assert fingerprint.tables_value(root) == before[0] and fingerprint.data_value(root) == before[1]
    rune.write_text(KERNED_RUNE.replace("stances: {}", "stances: {edited: {}}"))
    assert fingerprint.tables_value(root) != before[0]
    assert (
        artifact_cycle.kern_only_rebuild({"files": lines_before}, artifact_cycle.run_m1_skip_files(root))
        is None
    )


def test_a_font_data_edit_moves_only_its_font_line(tmp_path):
    """The full font compile reads the Latin, Shavian and symbol glyphs, the metadata, the Latin kern rules and the glyph-name map beside the runes (`font_data_paths`). No table depends on them, so an edit to one moves neither the tables' stamp nor any Stage A component, only its own font-data line in the run_m1 skip key, which the cycle reads as a kern-only rebuild. No compile reads glyph_data/quikscript.yaml, so an edit to the old engine's Quikscript families moves nothing, the angle parentheses' families included; glyph_data/punctuation.yaml, which holds the angle parentheses, keeps its table-side data line, because the namer dot's glyphs come from it."""
    root = _fake_repo(tmp_path)
    latin = root / "glyph_data" / "latin_letters.yaml"
    latin.write_text("glyphs:\n  a: {bitmap: ['#']}\n")
    (root / "postscript_glyph_names.yaml").write_text("a: 0x61\n")
    quikscript = root / "glyph_data" / "quikscript.yaml"
    quikscript.write_text(
        "glyph_families:\n  qsAngleParenLeft: {mono: {bitmap: ['#']}}\n  qsAngleParenRight: {mono: {bitmap: ['#']}}\n  qsPea: {mono: {bitmap: ['#']}}\n"
    )

    def keys():
        return (
            fingerprint.tables_value(root),
            fingerprint.stage_a(root),
            artifact_cycle.run_m1_skip_files(root),
        )

    before = keys()
    labels = [label for label in before[2] if label.endswith(fingerprint.FONT_LINE_SUFFIX)]
    assert labels == [
        "glyph_data/latin_letters.yaml#font",
        "postscript_glyph_names.yaml#font",
    ]
    assert fingerprint.font_data_paths(root) == [
        latin,
        root / "glyph_data" / "punctuation.yaml",
        root / "postscript_glyph_names.yaml",
    ]
    latin.write_text("glyphs:\n  a: {bitmap: ['##']}\n")
    edited = keys()
    assert edited[:2] == before[:2]
    moved = artifact_cycle.moved_input_labels({"files": before[2]}, edited[2])
    assert moved == ["glyph_data/latin_letters.yaml#font"]
    assert artifact_cycle.kern_only_rebuild({"files": before[2]}, edited[2]) == moved
    quikscript.write_text(
        quikscript.read_text().replace("qsPea: {mono: {bitmap: ['#']}}", "qsPea: {mono: {bitmap: ['##']}}")
    )
    assert keys() == edited
    quikscript.write_text(
        quikscript.read_text().replace(
            "qsAngleParenLeft: {mono: {bitmap: ['#']}}", "qsAngleParenLeft: {mono: {bitmap: ['##']}}"
        )
    )
    assert keys() == edited


def test_a_comparison_code_edit_moves_the_run_key_but_not_the_tables_stamp(tmp_path):
    """Editing rebuild/validation/shaping.py, the shaper the Manual-pin gate replays through, moves `pipeline_code` and the run_m1 skip key but not `tables_value`, so `--gates-only` can re-run the gates over the enumeration on disk while a full run still rebuilds everything. `pipeline_code_paths` globs rebuild/pipeline/ and rebuild/validation/, and `COMPARISON_CODE_MODULES` is the only subtraction `table_code_paths` makes, so each file is checked by path. conform.py and the crate stay in the tables' stamp."""
    root = _fake_repo(tmp_path)
    shaping = root / "rebuild" / "validation" / "shaping.py"
    assert shaping not in fingerprint.table_code_paths(root)
    assert shaping in fingerprint.pipeline_code_paths(root)
    assert root / "rebuild" / "pipeline" / "conform.py" in fingerprint.table_code_paths(root)
    assert root / "rebuild" / "kernel-rs" / "src" / "guard.rs" in fingerprint.table_code_paths(root)
    before = (
        _components(root)["pipeline_code"],
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    )
    shaping.write_text("SHAPER = 2\n")
    assert _components(root)["pipeline_code"] != before[0]
    assert fingerprint.tables_value(root) == before[1]
    assert artifact_cycle.run_m1_skip_fingerprint(root) != before[2]
    (root / "rebuild" / "pipeline" / "conform.py").write_text("CONFORM = 2\n")
    assert fingerprint.tables_value(root) != before[1]


def test_a_manual_pin_gate_edit_moves_the_run_key_but_not_the_tables_stamp(tmp_path):
    """Editing manual_pins.py, the Manual-pin gate, moves `pipeline_code` and the run_m1 skip key but not `tables_value`, so the cycle re-runs the gates over the enumeration on disk instead of rebuilding it."""
    root = _fake_repo(tmp_path)
    pins = root / "rebuild" / "pipeline" / "manual_pins.py"
    pins.write_text("PINS = 1\n")
    before = _code_keyed(root)
    pins.write_text("PINS = 2\n")
    after = _code_keyed(root)
    assert after[0] != before[0]
    assert after[1] == before[1]
    assert after[2] != before[2]


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
    before = _components(root)
    (root / "rebuild" / "validation" / "shaping.py").write_text("SENIOR_FONT = 2\n")
    after = _components(root)
    assert after["pipeline_code"] != before["pipeline_code"]
    assert {key: after[key] for key in COMPONENTS if key != "pipeline_code"} == {
        key: before[key] for key in COMPONENTS if key != "pipeline_code"
    }
    (root / "rebuild" / "kernel-rs" / "src" / "guard.rs").write_text("const GUARD: bool = false;\n")
    after_kernel = _components(root)
    assert after_kernel["pipeline_code"] != after["pipeline_code"]
    assert {key: after_kernel[key] for key in COMPONENTS if key != "pipeline_code"} == {
        key: after[key] for key in COMPONENTS if key != "pipeline_code"
    }


def test_a_font_compile_tool_edit_moves_the_run_key_and_the_tables_stamp(tmp_path):
    """compile_font passes the mini font to tools/build_font.py, which runs the glyph compiler, the IR, and the FEA emitter, so an edit to that tools/ closure changes M1.otf. It must move `pipeline_code` (and with it the Stage A record), the run_m1 skip key, and `tables_value` (and with it the conform sweep's check and the review corpus's `tables` component)."""
    root = _fake_repo(tmp_path)
    for name in ("build_font.py", "glyph_compiler.py"):
        assert root / "tools" / name in fingerprint.pipeline_code_paths(root)
        assert root / "tools" / name in fingerprint.table_code_paths(root)
    before = _components(root)
    before_tables = fingerprint.tables_value(root)
    before_run = artifact_cycle.run_m1_skip_fingerprint(root)
    (root / "tools" / "build_font.py").write_text("BUILD_FONT = 2\n")
    after = _components(root)
    moved = ("pipeline_code",)
    assert all(after[key] != before[key] for key in moved)
    assert {key: after[key] for key in COMPONENTS if key not in moved} == {
        key: before[key] for key in COMPONENTS if key not in moved
    }
    assert fingerprint.tables_value(root) != before_tables
    assert artifact_cycle.run_m1_skip_fingerprint(root) != before_run


PROSE_RUNE = textwrap.dedent("""\
    rune: qsPea
    codepoint: 0xE650
    ductus:
      sole: |
        A deep stroke, drawn downward.
    notes: |
      Cannot join at the x-height twice.
    stances:
      sole:
        motion: sole
        bitmap: ["#", "#"]
        surface:
          unlocks:
          - {feature: ss03, why: original unlock rationale}
    policy:
      refuse:
      - {exit: baseline, why: two verticals render thick}
      prefer:
      - {stance: sole, why: nicer to write}
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
    assert _data_after(root, PROSE_RUNE.replace("ductus:\n  sole:", "ductus:\n  pole:")) != before


def test_data_value_ignores_notes_prose_but_not_notes_presence(tmp_path):
    root = _fake_repo(tmp_path)
    before = _data_after(root, PROSE_RUNE)
    assert _data_after(root, PROSE_RUNE.replace("Cannot join", "Must not join")) == before
    without_notes = PROSE_RUNE.replace("notes: |\n  Cannot join at the x-height twice.\n", "")
    assert _data_after(root, without_notes) != before


def test_data_value_ignores_every_why_but_not_why_presence(tmp_path):
    """No `why` in a rune changes `data_value` or `tables_value`, a refusal's included: the crate uses a refuse `why` only in rankings, which the fixpoint never requests. Removing a `why` still moves `data_value`, because the schema requires a `why` on an absolute prefer and a missing one is a load failure."""
    root = _fake_repo(tmp_path)
    before = _data_after(root, PROSE_RUNE)
    tables = fingerprint.tables_value(root)
    assert _data_after(root, PROSE_RUNE.replace("nicer to write", "easier to write")) == before
    assert _data_after(root, PROSE_RUNE.replace("original unlock rationale", "reworded rationale")) == before
    assert _data_after(root, PROSE_RUNE.replace("render thick", "render thin")) == before
    assert fingerprint.tables_value(root) == tables
    assert _data_after(root, PROSE_RUNE.replace(", why: nicer to write}", "}")) != before
    assert _data_after(root, PROSE_RUNE.replace(", why: two verticals render thick}", "}")) != before


def test_data_value_tracks_semantic_edits(tmp_path):
    root = _fake_repo(tmp_path)
    before = _data_after(root, PROSE_RUNE)
    assert _data_after(root, PROSE_RUNE.replace('bitmap: ["#", "#"]', 'bitmap: ["#", "##"]')) != before


def test_data_value_falls_back_to_bytes_on_unparseable_rune(tmp_path):
    root = _fake_repo(tmp_path)
    before = _data_after(root, "rune: qsPea\n\t: [broken")
    assert _data_after(root, "rune: qsPea\n\t: [broken again") != before


SCHEMA = textwrap.dedent("""\
    {
      "title": "Rune file",
      "description": "One rune per file.",
      "type": "object",
      "properties": {
        "rune": {"type": "string", "description": "The family name."},
        "stances": {"type": "array", "items": {"$ref": "#/$defs/stance"}, "minItems": 1}
      },
      "anyOf": [{"required": ["rune"], "description": "A named rune."}],
      "$defs": {"stance": {"type": "object", "title": "Stance", "description": "One stance.", "minProperties": 0}}
    }
    """)
DATA_FILES = {
    "rebuild/schema/rune.schema.json": SCHEMA,
    "rebuild/script.yaml": "heights: {baseline: 0, x-height: 5}\n",
    "glyph_data/punctuation.yaml": "glyphs:\n  exclam.prop:\n    y_offset: -1\n  colon.prop:\n    y_offset: 0\n",
}


def _write_data(root, files):
    for label, text in files.items():
        (root / label).write_text(text)


def test_a_schema_annotation_or_a_data_file_comment_moves_no_data_key(tmp_path):
    """Rewording a schema's `title` or `description` wherever a schema sits (the root, under `properties`, inside `anyOf`, under `$defs`), or the comments and formatting of a YAML data input, leaves `data_value`, `tables_value` and the run_m1 skip key unchanged. Dropping the annotations is sound only while the shared adapter declares them as annotations and the standard validator's keyword dispatch does not evaluate them."""
    from rebuild.pipeline import spec_load

    annotations = set(fingerprint.SCHEMA_ANNOTATIONS)
    assert annotations <= spec_load._SchemaValidator._ANNOTATIONS
    assert annotations.isdisjoint(spec_load._SchemaValidator._KEYWORDS)
    assert annotations.isdisjoint(spec_load._SchemaValidator.VALIDATOR.VALIDATORS)
    schema = json.loads(SCHEMA)
    projected = fingerprint._projected_schema(schema)
    assert isinstance(projected, dict)
    validator = spec_load._SchemaValidator(schema, "rune.schema.json")
    without_annotations = spec_load._SchemaValidator(projected, "rune.schema.json")
    for value in ({"rune": "qsPea", "stances": [{}]}, {"rune": 1, "stances": []}):
        assert validator.check(value) == without_annotations.check(value)
    root = _fake_repo(tmp_path)
    _write_data(root, DATA_FILES)
    before = (
        fingerprint.data_value(root),
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    )
    _write_data(
        root,
        {
            "rebuild/schema/rune.schema.json": SCHEMA.replace("One rune per file.", "One rune to a file.")
            .replace("The family name.", "The PostScript family name.")
            .replace("A named rune.", "Named.")
            .replace('"title": "Stance"', '"title": "A stance"')
            .replace('"minItems": 1', '"minItems":   1'),
            "rebuild/script.yaml": "# Registries only.\nheights: {baseline: 0,   x-height: 5}  # in pixels\n",
            "glyph_data/punctuation.yaml": "# Hand-written.\nglyphs:\n  exclam.prop:  # ! U+0021\n    y_offset: -1\n\n  colon.prop: {y_offset: 0}\n",
        },
    )
    assert (
        fingerprint.data_value(root),
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    ) == before


@pytest.mark.parametrize(
    ("label", "old", "new"),
    [
        ("rebuild/schema/rune.schema.json", '"minItems": 1', '"minItems": 2'),
        ("rebuild/schema/rune.schema.json", '"minProperties": 0', '"maxProperties": 0'),
        (
            "rebuild/schema/rune.schema.json",
            '"type": "object", "title": "Stance", "description": "One stance.", "minProperties": 0',
            '"minProperties": 0, "type": "object", "title": "Stance", "description": "One stance."',
        ),
        ("rebuild/script.yaml", "x-height: 5", "x-height: 4"),
        ("glyph_data/punctuation.yaml", "y_offset: -1", "y_offset: -2"),
        (
            "glyph_data/punctuation.yaml",
            "  exclam.prop:\n    y_offset: -1\n  colon.prop:\n    y_offset: 0\n",
            "  colon.prop:\n    y_offset: 0\n  exclam.prop:\n    y_offset: -1\n",
        ),
    ],
)
def test_a_keyword_a_value_or_an_order_moves_the_data_value(tmp_path, label, old, new):
    """The data projections drop annotations, comments and formatting and nothing else: a changed or renamed schema keyword, a changed YAML value, and a reorder of keys each move `data_value`. Order is kept because no reader has been shown not to depend on it."""
    root = _fake_repo(tmp_path)
    _write_data(root, DATA_FILES)
    before = fingerprint.data_value(root)
    assert DATA_FILES[label].count(old) == 1
    (root / label).write_text(DATA_FILES[label].replace(old, new))
    assert fingerprint.data_value(root) != before


@pytest.mark.parametrize("name", fingerprint.SCHEMA_ANNOTATIONS)
def test_a_schema_member_named_like_an_annotation_stays_in_the_digest(tmp_path, name):
    """Under `properties` and `$defs`, `title` and `description` name members, so their schemas are hashed and a change to them moves the digest, while those schemas' own annotations are dropped. Keyword values are kept whole, even an annotation's name inside `const`, `enum`, or `required`."""
    path = tmp_path / "script.schema.json"

    def digest(schema):
        path.write_text(json.dumps(schema))
        return fingerprint.schema_file_digest(path)

    def feature(prop):
        return {"type": "object", "required": [name], "properties": {name: prop}}

    base = digest(feature({"type": "string", "description": "What the set does."}))
    assert digest(feature({"type": "string", "description": "What the stylistic set does."})) == base
    assert digest(feature({"type": "string"})) == base
    assert digest(feature({"type": "integer", "description": "What the set does."})) != base
    assert digest(feature({"type": "string", "description": "What the set does."})) != digest(
        {"type": "object", "required": [name], "properties": {}}
    )
    definitions = {"$defs": {name: {"type": "string", "description": "A definition."}}}
    assert digest(definitions) == digest({"$defs": {name: {"type": "string"}}})
    assert digest(definitions) != digest({"$defs": {name: {"type": "integer"}}})
    assert digest(definitions) != digest({"$defs": {}})
    assert digest({"const": {name: "one"}}) != digest({"const": {name: "two"}})
    assert digest({"enum": [{name: "one"}]}) != digest({"enum": [{name: "two"}]})
    assert digest({"required": [name]}) != digest({"required": []})


@pytest.mark.parametrize(
    ("schema", "value", "keyword", "changed"),
    [
        ({"type": "array", "minItems": 1}, ["qsPea"], "minItems", 2),
        ({"type": "string", "pattern": "^qs"}, "qsPea", "pattern", "^other$"),
        ({"type": "integer", "minimum": 0}, 0, "minimum", 1),
        ({"type": "object", "required": ["rune"]}, {"rune": "qsPea"}, "required", ["stances"]),
    ],
)
def test_an_evaluated_schema_keyword_moves_every_behavioral_data_key(
    tmp_path, schema, value, keyword, changed
):
    """A keyword in the shared adapter's vocabulary and the standard validator's dispatch changes validation and moves the data, tables, and run_m1 skip keys. This pins the distinction between hover annotations and constraints against the validator that loads rune and draft records."""
    from rebuild.pipeline import spec_load

    assert keyword in spec_load._SchemaValidator._KEYWORDS
    assert keyword in spec_load._SchemaValidator.VALIDATOR.VALIDATORS
    edited = {**schema, keyword: changed}
    assert spec_load._SchemaValidator(schema, "rune.schema.json").check(value) == []
    assert spec_load._SchemaValidator(edited, "rune.schema.json").check(value)
    root = _fake_repo(tmp_path)
    path = root / "rebuild" / "schema" / "rune.schema.json"
    path.write_text(json.dumps(schema))
    before = (
        fingerprint.data_value(root),
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    )
    path.write_text(json.dumps(edited))
    after = (
        fingerprint.data_value(root),
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    )
    assert all(new != old for old, new in zip(before, after, strict=True))


def test_an_unparseable_data_file_hashes_raw(tmp_path):
    """A schema that is not JSON, a schema whose parsed text cannot be encoded (a lone surrogate escape), and a YAML input that does not parse are hashed by their bytes, as an unparseable rune is, so the load failure stays visible and two broken drafts do not share a value."""
    root = _fake_repo(tmp_path)
    broken = {
        "rebuild/schema/rune.schema.json": '{"type": "object",',
        "rebuild/schema/script.schema.json": '{"type": "string", "pattern": "[\\ud800-\\udfff]"}',
        "rebuild/script.yaml": "heights: [unclosed\n",
    }
    _write_data(root, broken)
    digests = dict(line.split("\t", 1) for line in fingerprint.data_lines(root))
    for label in broken:
        assert digests[label] == fingerprint.file_sha256(root / label)


def test_stage_a_data_component_is_the_prose_insensitive_value(tmp_path):
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


def test_code_digest_ignores_docstrings_for_python(tmp_path):
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
    """`value` and `labels` depend only on `lines`, so a store that starts recording `detail` keeps its recorded value."""
    lines = ("format\tf/1", "corpus_code\tabc", "data\tddd")
    bare = fingerprint.EnvironmentStamp(lines=lines)
    detailed = fingerprint.EnvironmentStamp(
        lines=lines, detail=(("corpus_code", ("rebuild/review/ink.py\t1", "rebuild/review/build.py\t2")),)
    )
    assert detailed.value == bare.value == hashlib.sha256("\n".join(lines).encode()).hexdigest()
    assert detailed.labels == bare.labels == {"format": "f/1", "corpus_code": "abc", "data": "ddd"}


def _code_keyed(root):
    return (
        _components(root)["pipeline_code"],
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
    contracts = artifact_cycle.contracts_fingerprint(root)
    make_test = artifact_cycle.make_test_closure_fingerprint(root)
    assert contracts is not None and make_test is not None
    components = _components(root)
    table.write_text('"""The table, reworded."""\n\nTABLE = 1\n')
    build_font.write_text('"""The compile, reworded."""\n\nBUILD_FONT = 1\n')
    assert _components(root) == components
    assert artifact_cycle.contracts_fingerprint(root) != contracts
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
    """Rewriting `head.fontRevision` and every `name` record of the checked-in mini font, as a build does to every font on a version bump (the save also updates `head.modified` and the checksum adjustment), leaves the digest unchanged. Changing a glyph's advance or a cursive anchor's coordinate, dropping a table, or adding one each moves it. Every case goes through a fontTools save, so the test uses fonts a shaper would read, not patched bytes."""
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


def test_a_units_per_em_edit_moves_the_font_content_digest_through_the_cff_font_matrix(tmp_path):
    """tools/build_font.py passes `FontBuilder.setupCFF` a `fontInfo` with no FontMatrix, so fontTools writes the `CFF ` top dict's FontMatrix as 1/unitsPerEm. Two fonts built that way that differ only in unitsPerEm therefore differ in `CFF ` as well as `head`, and the content digest, which keeps `CFF `, tells them apart. If a fontTools release stops deriving the matrix, this test fails before a unitsPerEm edit in glyph_data/metadata.yaml can go unseen by every font key."""
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
    """A fake font, an empty file, and a truncated real font each digest to their raw bytes instead of raising or sharing a value."""
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


def test_the_font_content_digest_is_memoized_on_the_bytes_and_recomputed_when_they_move(
    tmp_path, monkeypatch
):
    """The memo is keyed on the raw digest, as in `code_file_digest`: the same bytes are digested once however many times they are asked for, and a file rewritten with different bytes is digested again."""
    digested: list[Path] = []
    real = fingerprint._font_content_lines

    def counting(path):
        digested.append(path)
        return real(path)

    monkeypatch.setattr(fingerprint, "_font_content_lines", counting)
    monkeypatch.setattr(fingerprint, "_FONT_DIGESTS", {})
    font = tmp_path / "font.otf"
    font.write_bytes(MINI_FONT.read_bytes())
    first = fingerprint.font_content_digest(font)
    assert fingerprint.font_content_digest(font) == first
    assert fingerprint.font_content_digest(MINI_FONT) == first
    assert len(digested) == 1
    _font(MINI_FONT, font, _widen_a_glyph)
    assert fingerprint.font_content_digest(font) != first
    assert len(digested) == 2


def _version_bump_keys(root, font):
    return (fingerprint.font_content_digest(font), artifact_cycle.run_m1_skip_files(root)["uv.lock"])


def test_a_version_bump_moves_no_key_while_a_glyph_an_anchor_and_a_pin_move_each(tmp_path):
    """Checks two keys together: a font's content digest (`font_content_digest`, which gate:make-test's key takes of the Senior) and the run_m1 skip key's `uv.lock` line. Rewriting only `head` and `name` in the font and only the project version in the lock moves neither. A glyph edit and a GPOS change each move the font's digest, and a dependency-pin change moves the lock line."""
    root = _fake_repo(tmp_path)
    lock = root / "uv.lock"
    lock.write_text(LOCK)
    senior = root / "rebuild" / "out" / "m1" / "M1.otf"
    senior.parent.mkdir(parents=True)
    senior.write_bytes(MINI_FONT.read_bytes())
    base = _version_bump_keys(root, senior)
    assert all(value is not None for value in base)

    _font(MINI_FONT, senior, _bump_version)
    lock.write_text(LOCK_BUMPED)
    assert _version_bump_keys(root, senior) == base

    _font(MINI_FONT, senior, _widen_a_glyph)
    glyph = _version_bump_keys(root, senior)
    assert glyph[0] != base[0] and glyph[1] == base[1]
    _font(MINI_FONT, senior, _move_an_anchor)
    anchor = _version_bump_keys(root, senior)
    assert anchor[0] != base[0] and anchor[1] == base[1]
    assert anchor[0] != glyph[0]

    senior.write_bytes(MINI_FONT.read_bytes())
    lock.write_text(LOCK_PIN_MOVED)
    pin = _version_bump_keys(root, senior)
    assert pin[0] == base[0] and pin[1] != base[1]

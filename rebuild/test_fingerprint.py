"""Tests for the build-input fingerprint module: the streamed file digest and the sweep that keeps it the only way rebuild/ hashes a file, content sensitivity, order independence, missing-file tolerance, the stat-based baselines component, the Stage A record round trip, the serve.py exclusion, the four prose-blind digests — the rune files' and the three human-reviewed ledgers' — the code projection that makes a Python docstring or a whole-line Rust comment invisible to every key `path_lines` builds while the artifact cycle's two gate closures stay raw, and the explain-aware rune digest and `explain_prose` component that are where a refuse record's `why` and a divergence class's `why` live instead of in any key a table build reads.

The sweep is here rather than in prose because file_sha256 exists to stop a hash costing the file its size in RAM, and that claim only holds while every hash goes through it — which a roster of callers written into a docstring cannot keep, since nothing checks a roster and the next module to grow a file hash falsifies it in silence. The modules that cannot import fingerprint spell the same streamed read out inline instead; those are pinned against the helper by value here, so a copy cannot drift from the original unnoticed either.
"""

import ast
import hashlib
import json
import subprocess
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


READ_WHOLE_EXEMPT = {"rebuild/pipeline/fingerprint.py": frozenset({"code_file_digest"})}
"""The functions the sweep lets read a file whole, by module: `code_file_digest` projects a source file before hashing it, and its dispatch on the suffix before the file is opened bounds what it reads to the `.py` and `.rs` sources, none of them large."""


def _read_whole_hashes(path, exempt_functions=frozenset()):
    """The line of every `hashlib.<algo>(...)` in one module whose argument reads a file whole through `.read_bytes()`, however that read is wrapped — the spelling that holds a whole file in memory to hash it — outside the functions named exempt."""
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
    """serve.py, status.py, journal.py, and export.py never run in the surface build, so editing one must not stale the surface or drop the per-unit caches — the plumbing key hashes the ones the verdict chain runs."""
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
    """The narrowing is by roster rather than by pattern, so the set it removes is worth pinning against the roster itself: whatever `NON_TABLE_DATA_LABELS` names leaves the tables' stamp, and nothing else does."""
    root = _fake_repo(tmp_path)
    labels = {line.split("\t", 1)[0] for line in fingerprint.data_lines(root)}
    table_labels = {line.split("\t", 1)[0] for line in fingerprint.table_data_lines(root)}
    assert labels - table_labels == set(fingerprint.NON_TABLE_DATA_LABELS)
    assert (
        fingerprint.table_data_value(root)
        == hashlib.sha256("\n".join(fingerprint.table_data_lines(root)).encode()).hexdigest()
    )


def test_a_comparison_side_data_edit_moves_the_run_key_but_not_the_tables_stamp(tmp_path):
    """The whole point of the narrowing, and the line it must not cross. All three files are read by gates that consume a decision table — the oracle's naming and classification from the alias map and the divergence ledger, its position channel from the kern sidecar — so a serialized enumeration built before the edit still describes the sources on disk and `--gates-only` may re-adjudicate against it. They stay in `data_value` and so in the artifact cycle's run_m1 key, which is what decides whether the comparison re-runs at all, so narrowing the stamp cannot skip a gate.

    The kern sidecar is the newest member and the one the roster has to keep honest: the font compile hands its builder an empty kerning map and never opens the file, so the only reader is `oracle.KernEvaluator`, and a sidecar edit that throws away an enumeration is spending a fixpoint on a table that would come back byte for byte.

    The edit has to be structural rather than a comment, because one of the three hashes prose-blind now: a comment above the divergence ledger's entries moves nothing at all, which is the neighboring bargain and not a hole in this one.
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
    """The allow-list's home after the narrowing: no fingerprint component at all, and one line in the artifact cycle's run_m1 key. The defect gate is its only reader, so a two-line bless has to re-run that gate and has no business re-stamping the surface or dropping the review unit cache — which is exactly what a place in `data_paths` cost it, since both `unit_cache.environment_stamp` and `oracle_cache.stamped_data_paths` derive from that list."""
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
    """`why:` on an allow-list entry is the reviewer's recorded rationale for blessing that corner and reaches no gate, so rewording one must not cost a re-adjudication — the same bargain the rune digest strikes with `ductus` and `notes`. Comments and blank lines go the same way, since the digest is taken over the parsed document rather than the bytes."""
    root = _fake_repo(tmp_path)
    before = _allow_after(root, ALLOW_LIST)
    assert _allow_after(root, ALLOW_LIST.replace("already draws on", "has always drawn on")) == before
    assert _allow_after(root, ALLOW_LIST.replace("gate.\n", "gate.\n# and a second line.\n")) == before
    assert _allow_after(root, ALLOW_LIST.replace("- signature:", "\n- signature:")) == before


def test_contact_allow_digest_is_prose_blind_and_falls_back_to_bytes(tmp_path):
    """The digest on its own, away from any key that carries it. Prose-blindness is the whole reason it is not `file_sha256`: a signature is what the gate reads and a `why` is what the reviewer wrote, so only the first may move it. The raw fallback is the other half — a malformed allow-list is a stopping change, `defects.run_gates` will refuse to read it, so two different broken drafts must not collapse onto one value the way a swallowed parse error would leave them."""
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
    """The ledger digest on its own, away from the components that carry it. What stays inside is what someone reads to decide something: `audit.load_ledger` takes the id and the `no_verdict` flag, `oracle.classify_divergence` takes the predicate and the configs, and the census takes the status and the ink flag, so retriaging a class, moving its counts or swapping an exemplar all move the digest. A class's `why` is the reviewer's recorded rationale for the triage and reaches no gate, so rewording one may not — the same bargain the rune digest strikes with `ductus` and `notes`, and comments and reformatting go with it since the digest is taken over the parsed document. The raw fallback is the other half: a malformed ledger stops the oracle outright, so two broken drafts must not collapse onto one value."""
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
    """The third ledger's digest, note-blind for the allow-list's reason: `standing_verdicts` matches on a rule's `verdict`, `match` and `except_left`, and the `note` beside them is the user's sentence about why the shape is blessed forever. Only the artifact cycle's rebuild-lane closures read this digest — the plumbing key keeps the file's raw bytes, because the fill quotes that sentence into every verdict it writes — so what it has to answer is whether a lane could tell the difference, and a reworded note is one no lane can see."""
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
    """The code-side twin of the ledger narrowing above, and the line it must not cross either. rebuild/pipeline/oracle.py runs against tables and a font already built, so an edit there leaves the enumeration on disk describing the sources it came from and `--gates-only` may re-adjudicate against it; the file stays in `pipeline_code_paths`, so the Stage A record and the artifact cycle's run_m1 key still move and a full run still re-derives everything. conform.py holds the producer of what the oracle classifies and stays on the tables' side."""
    root = _fake_repo(tmp_path)
    assert root / "rebuild" / "pipeline" / "oracle.py" not in fingerprint.table_code_paths(root)
    assert root / "rebuild" / "pipeline" / "oracle.py" in fingerprint.pipeline_code_paths(root)
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
    """The M1 font compile leaves rebuild/ behind: `compile_font` hands the mini font's glyph data and FEA to tools/build_font.py, which runs the glyph compiler, the IR and the FEA emitter beside it. That closure writes M1.otf's bytes, so an edit there has to move everything that decides whether the font on disk still answers for its sources — the Stage A record and with it the artifact cycle's run_m1 green, the stamp a serialized enumeration carries and so the conform sweep's key, and the surface's stamp, every one of them keyed on `pipeline_code_paths` or its build-side narrowing. Before the roster existed, all of them sat still while the font changed underneath. The review unit cache's stamps are keyed on their own rosters instead — the unit store on `unit_cache.surface_code_paths`, the ink-signature store on the narrower `unit_cache.signature_code_paths` — both of which leave the font compile out because neither the surface build nor the comparator runs it; the unit store's stamp still sees a tools/ edit, through the draft-harness line that hashes tools/*.py whole."""
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
    """Every rationale a rune carries is documentation as far as anything that builds a table is concerned, a refusal's included: the crate appends a refuse `why` to an elimination sentence only when it is asked for an explain ladder, which the fixpoint never asks for. Presence is the separate claim that stays inside, because the schema requires a `why` on an absolute prefer and dropping one is a load failure the digest has to see coming."""
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
    """The explain-aware digest's whole charter: the prose-blind projection plus the one sentence the review surface serves back, so a reworded refusal moves it and no other prose, comment, or reformatting does. A rune whose refusals carry no `why` hashes the same either way, which is what lets the review unit cache switch onto this digest without restamping a store."""
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
    """The component's input, spelled at the grain a reader can diff: one line per refusal that carries a rationale, indexed so two refusals in a file cannot collapse onto one line, and a raw-byte fallback for a rune that will not parse, so a broken file reads as changed rather than as carrying no refusals at all."""
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
    """Where the quoted prose lives now that it is out of `data`: a Stage B component of its own, so rewording a refusal re-stamps the surface — which is how the explain text served can be checked against the runes on disk — while `data`, `baselines` and `pipeline_code` stay put and with them run_m1's green, the conform sweep's key and both suite lanes. Stage A never carries it: run_m1 reads no refuse prose and could not record it honestly."""
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
    """A workload bundled with its own frozen spec serves that spec's rationales, so the component is taken over the spec root and the checkout's runes are never opened for it; a build that names no spec root reads the checkout, where the two are one tree. The other Stage B components stay on the checkout either way."""
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
    """Where the ledger sits among the data inputs: still in `data_lines`, hashed by its own digest rather than by raw bytes, and still exempt from the tables' stamp. The label is what routes it, so this is also what keeps `data_paths`' spelling of the path and `DIVERGENCE_LEDGER_LABEL` from drifting apart — a mismatch would silently hash the ledger raw again."""
    root = _fake_repo(tmp_path)
    ledger = root / fingerprint.DIVERGENCE_LEDGER_LABEL
    ledger.write_text(LEDGER)
    digests = dict(line.split("\t", 1) for line in fingerprint.data_lines(root))
    assert digests[fingerprint.DIVERGENCE_LEDGER_LABEL] == fingerprint.divergence_ledger_digest(ledger)
    assert fingerprint.DIVERGENCE_LEDGER_LABEL in fingerprint.NON_TABLE_DATA_LABELS


def test_wording_a_divergence_class_moves_the_explain_component_alone(tmp_path):
    """A class rationale reaches the surface — the review build copies it into the manifest's `classes[].why` — so it cannot simply leave the digests the way an allow-list `why` does; it moves to `explain_prose` instead, where a refuse record's rationale already lives. What that buys is the whole point of the change: rewording a class costs a surface rebuild, served from the unit cache because no shard carries the sentence, and leaves `data` alone and with it the artifact cycle's run_m1 green, the tables' stamp, the conform sweep's key and both suite lanes."""
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
    """The other side of the bargain, and the line it must not cross. Flipping `no_verdict` changes which units the surface offers a verdict on and which rows the audit calls adjudicated, so it has to move `data` and with it the run_m1 key — spent as a re-adjudication over the tables and font on disk, since the ledger is in `NON_TABLE_DATA_LABELS` — while leaving the enumeration's own stamp and the explain component alone."""
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
    """The component's other input, spelled at the grain a reader can diff: one line per class that carries a rationale, keyed by the class id the manifest serves it under, with the literal `ledger` first field keeping it from colliding with a refuse line's family name. A ledger that will not parse contributes a raw-byte line instead, so a broken file reads as changed rather than as carrying no rationales at all."""
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
    """The standing approvals are the contact allow-list's neighbor here: no component carries them, so neither a reworded note nor a rewritten rule can restamp the surface or drop the review unit cache. What a rule change does move is the two rebuild-lane closures, through `standing_approvals_digest`, and the plumbing key, through the raw bytes the chain's own key still keeps — both of them the artifact cycle's, and neither of them a fingerprint component."""
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
    """The whole soundness claim of the Python half. Every docstring — the module's, a class's, a method's, a function's — is prose nothing that builds anything reads, so rewording one leaves the digest still, and so do a `#` comment and a reflowed blank line, since the tree carries neither. Everything the interpreter can see still moves it: an identifier, a string constant that is not a docstring (the error text a matcher compares against), an annotation, a default, a decorator. The presence of a docstring is kept the way the rune projection keeps the presence of a `why`, so removing one is still an edit."""
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
    """A broken module is a stopping change wherever a code digest is read, so it digests to its own bytes and two differently broken drafts cannot collapse onto one value — the bargain `contact_allow_digest` strikes with a malformed allow-list."""
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
    """The Rust half, and the conservative line it draws: a `//`, `///` or `//!` line is dropped whole, so rewording any of them leaves the digest still, while a code line moves it — and so does the trailing comment on a code line, which rides along with the line rather than being parsed out of it. That last one is the decision the projection makes, recorded here so a later widening has to argue it."""
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
    """The projection reaches Python and Rust sources and nothing else, so the `static`, `fonts` and data rosters keep their byte digests: a YAML file, the app shell's JavaScript, the crate's manifest and a font all carry `file_sha256`'s value in the lines `path_lines` builds."""
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


def _code_keyed(root):
    return (
        fingerprint.compute_all(root)["pipeline_code"],
        fingerprint.tables_value(root),
        artifact_cycle.run_m1_skip_fingerprint(root),
    )


def test_a_docstring_edit_moves_no_code_keyed_component(tmp_path):
    """What the projection buys, end to end: adding a docstring to a pipeline module and a `//` line to the crate, then rewording both, leaves the Stage A `pipeline_code` component, the tables' stamp and the artifact cycle's run_m1 key where they stand — so the trace memo, the replay, the oracle row cache, the settle memo and the run_m1 green all survive a prose edit — while a statement edit in the same file still moves all three."""
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
    """The scope boundary: the rebuild suite's lane closures and gate:make-test's hash their files raw, because test fixtures and the closure tests read source text and a `# pyright: ignore` changes the answer pyright gives, so a docstring-only edit that moves no fingerprint component still moves every lane's key and the make-test key. Two files carry the edit, one per closure: a pipeline module is in every lane's closure and exempt from gate:make-test's (`make_test_exempt` leaves rebuild/ out wholesale), and the font compile's tools/ module is in gate:make-test's closure and in `pipeline_code`. A repo under git, since both closures are enumerated with `git ls-files`."""
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
    """The per-process memo, which is what keeps the projection from costing every stamp call a parse of the whole roster: over one roster, repeated `hash_paths` calls parse a Python file once, and a file whose content the process has already projected is not parsed again. The guard on its key is the other half — a file rewritten to the same size with different content, in the same instant, still digests differently, which is where a key on size and mtime would serve the stale projection."""
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

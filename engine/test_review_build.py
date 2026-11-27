"""Tests for the review-app build: the §7 contract checker over engine/review/fixtures/ (the checker the build runs over what it writes and `check_output_dir` runs over a written corpus), the manifest's class records, the config badge text, the app shell and its scripts, the assets refresh, the shard writer, `_write_json`, and the refusal to build into the served corpus while the review server listens.

No test here reads the live corpus. The build checks the per-unit and per-shard contracts over every unit it writes and fails on any violation, and what the manifest writer computes from its own inputs (the fingerprint and the feature descriptions) needs no separate check. A real table-diff build's corpus, its sidecars' byte addressing among it, is checked in `engine/test_review_diff_units.py` and `engine/test_app_index.py`, and the staging in `app_index.write_app_artifacts` and the currency check in `artifact_cycle.corpus_build_skippable` keep a shipped corpus from carrying a sidecar built for other shards.
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from engine.pipeline import fingerprint
from engine.review import app_index, diff_units, shards
from engine.review import build as review_build
from engine.review.build import (
    FEATURE_DESCRIPTIONS,
    STATIC_DIR,
    _prune_orphan_shards,
    _write_json,
    _write_shard,
    check_manifest,
    check_shards,
    check_unit,
    config_badge,
)
from engine.review.tablediff import SETTLED_CONFIGS

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "engine" / "review" / "fixtures"
MINI = FIXTURES / "mini"


def _load_fixture_units():
    units = []
    for shard in sorted((FIXTURES / "units").glob("*.json")):
        units.extend(json.loads(shard.read_text(encoding="utf-8")))
    return units


def test_fixture_manifest_passes_the_contract_checker():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert check_manifest(manifest) == []


def test_fixture_units_pass_the_contract_checker():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    units = _load_fixture_units()
    assert len(units) == manifest["totals"]["units"]
    for unit in units:
        assert (
            check_unit(unit) == []
        ), "when an error names the content key or the id, run `uv run python engine/review/fixtures/restamp.py`"


def test_fixture_units_exercise_the_contract_branches():
    units = _load_fixture_units()
    assert {unit["class"] for unit in units} == set(diff_units.CLASS_IDS)
    assert any(len(unit["configs"]) > 1 for unit in units)
    assert any("&#x200C;" in (unit["text_entities"] or "") for unit in units)
    assert any("&#x00B7;" in (unit["text_entities"] or "") for unit in units)
    assert any(len(unit["kinds"]) > 1 for unit in units)
    assert any(unit["pair"] is None for unit in units)
    assert any(unit["codepoints"] is None for unit in units), "a fixture unit must be shown by its row alone"
    assert any(unit["config_gate"] for unit in units)
    assert any(unit["config_gate"] is None and unit["config_note"] for unit in units)
    assert any(unit["ink_identical"] for unit in units)
    assert any(unit["no_verdict"] for unit in units)
    assert any(isinstance(unit["cluster"], str) for unit in units)
    assert any(unit["cluster"] is None for unit in units)


def _fixture_unit(*, ink_identical: bool) -> dict:
    """Return a deep copy of a human or ink-identical fixture unit that passes the contract checker, so a test can break one field and restore it."""
    return copy.deepcopy(
        next(
            unit
            for unit in _load_fixture_units()
            if unit["ink_identical"] is ink_identical and not unit["no_verdict"]
        )
    )


def test_check_unit_admits_one_machine_check_at_most():
    """The machine checks are tried in precedence order, each only where the earlier ones did not approve, so a unit approved by two checks comes from no build this checker knows."""
    unit = _fixture_unit(ink_identical=True)
    assert check_unit(unit) == []
    unit["picture_identical"] = True
    assert any("at most one machine check" in error for error in check_unit(unit))


def test_check_unit_takes_picture_identical_units_out_of_the_human_units():
    """A picture-identical unit stops being a human unit as an ink-identical one does: it carries no cluster, since no pixel it shows changes."""
    unit = _fixture_unit(ink_identical=False)
    assert check_unit(unit) == []
    unit["picture_identical"] = True
    assert any("cluster null" in error for error in check_unit(unit))
    unit["cluster"] = None
    assert check_unit(unit) == []


def test_check_manifest_requires_the_machine_checks():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert not any("machine checks" in error for error in check_manifest(manifest))
    del manifest["machine_approved"]["checks"]["picture_identical"]
    assert any("machine checks" in error for error in check_manifest(manifest))


def test_check_manifest_requires_each_class_s_copy_and_section():
    """A class needs a nonempty title and summary, the section `audit.class_section` gives its exemption and human units, and the classes must be listed by section. Each break below is made on a fresh copy of a manifest that passes."""
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert check_manifest(manifest) == []
    open_question = next(meta["id"] for meta in manifest["classes"] if meta["section"] == "open-questions")

    def class_of(edited: dict) -> dict:
        return next(meta for meta in edited["classes"] if meta["id"] == open_question)

    breaks = [
        (lambda edited: class_of(edited).pop("title"), "title must be a nonempty string"),
        (lambda edited: class_of(edited).update(summary=" "), "summary must be a nonempty string"),
        (lambda edited: class_of(edited).update(section="elsewhere"), "section must be one of"),
        (lambda edited: class_of(edited).update(section="no-review-needed"), "section must be the one"),
        (lambda edited: edited["classes"].reverse(), "classes must be listed by section"),
    ]
    for change, message in breaks:
        edited = copy.deepcopy(manifest)
        change(edited)
        assert any(message in error for error in check_manifest(edited)), message


def test_check_manifest_flags_a_malformed_inputs_fingerprint():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    manifest["inputs_fingerprint"] = {"snapshot": "x"}
    assert any("inputs_fingerprint" in error for error in check_manifest(manifest))
    manifest["inputs_fingerprint"] = {key: 7 for key in fingerprint.TABLE_DIFF_COMPONENTS}
    assert any("inputs_fingerprint" in error for error in check_manifest(manifest))


@pytest.mark.parametrize(
    "human_unit_ids",
    (
        "u-0000",
        ["u-BzEBRDeYbuP", ["u-MG7BYctpZgY"]],
        ["not-a-unit"],
        ["u-0000"],
        ["u-BzEBRDeYbuP", "u-BzEBRDeYbuP"],
    ),
)
def test_check_manifest_flags_malformed_human_unit_ids(human_unit_ids):
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    manifest["human_unit_ids"] = human_unit_ids
    assert any("human_unit_ids" in error for error in check_manifest(manifest))


def test_check_shards_flags_human_unit_ids_that_do_not_match_batches():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    by_class = {meta["id"]: list(shards.iter_class_fragments(FIXTURES, meta)) for meta in manifest["classes"]}
    manifest["human_unit_ids"].pop()
    assert any("human_unit_ids" in error for error in check_shards(manifest, by_class))


def _note(unit_configs, full_configs=SETTLED_CONFIGS):
    return config_badge(unit_configs, full_configs)[1]


def _gate(unit_configs, full_configs=SETTLED_CONFIGS):
    return config_badge(unit_configs, full_configs)[0]


def test_config_note_covers_the_general_gated_excluded_and_fallback_cases():
    full = SETTLED_CONFIGS
    assert _note(full) is None
    assert _note(("ss03", "ss03+ss05")) == "only when ss03 is on"
    assert _note(("default", "ss04", "ss05", "ss07", "ss08")) == "only when ss03 is off"
    assert _note(("ss08",)) == "only when ss08 is on"
    assert _note(("ss04", "ss08")) == "only under: ss04, ss08"


def test_a_config_outside_the_roster_reads_as_the_literal_set():
    """A glyph only the ss10 overlay reaches is a unit under ss10 alone (`diff_units.OVERLAY_CONFIG`), which the roster leaves out, so no conjunction selects it and the badge names it."""
    assert _gate(("ss10",)) is None
    assert _note(("ss10",)) == "only under: ss10"


def test_config_badge_caches_list_and_tuple_equivalents_together():
    full = SETTLED_CONFIGS
    from_lists = config_badge(["ss03"], list(full))
    from_tuples = config_badge(("ss03",), full)
    assert from_lists is from_tuples


def test_config_gate_pins_a_narrower_set_than_one_feature_can_describe():
    """A config set narrower than "every config with ss03 on" can still be described by a feature conjunction: a unit can differ under ss03 alone because turning ss05 on as well produces a different unit. Such a set resolves to the conjunction that selects it, so the badge names the features in their own colors instead of listing configs."""
    assert _note(("ss03",)) == "only when ss03 is on and ss05 is off"
    assert _note(("ss05",)) == "only when ss05 is on and ss03 is off"
    assert _note(("ss03+ss05",)) == "only when ss03 is on and ss05 is on"
    assert _note(("default", "ss04", "ss07", "ss08")) == "only when ss03 is off and ss05 is off"


def test_config_gate_leaves_the_literal_fallback_to_sets_no_conjunction_pins():
    """The literal fallback remains for a disjunction such as ss04 or ss08, which no conjunction selects, because every conjunction that admits both also admits default, and for a set that needs more constraints than `GATE_CONSTRAINT_CAP`, such as default alone, which only every set off selects."""
    assert _gate(("ss04", "ss08")) is None
    assert _note(("ss04", "ss08")) == "only under: ss04, ss08"
    assert _gate(("default",)) is None
    assert _note(("default",)) == "only under: default"
    assert _note(("default", "ss03")) == "only under: default, ss03"


def test_config_gate_clauses_carry_their_own_prose_and_the_note_is_their_join():
    """The clause `text` fields are the only copy of the badge's wording: the app renders each as one chip, and the note is their join. On-constraints come first, which puts the lit chip at the head of the badge."""
    gate = _gate(("ss03",))
    assert gate is not None
    assert gate == [
        {"feature": "ss03", "state": "on", "text": "only when ss03 is on"},
        {"feature": "ss05", "state": "off", "text": "and ss05 is off"},
    ]
    assert _note(("ss03",)) == " ".join(clause["text"] for clause in gate)


def test_feature_descriptions_keys_match_the_readme_stylistic_set_list():
    """`FEATURE_DESCRIPTIONS` copies README's "Stylistic sets" section by hand with shorter wording, so only the set of keys is compared. Adding or removing a stylistic set in the README fails this test until the build's map is updated."""
    import re

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Stylistic sets", 1)[1].split("\n## ", 1)[0]
    readme_sets = set(re.findall(r"^- `(ss\d+)`:", section, re.MULTILINE))
    assert readme_sets, "no `ssNN` bullets found under README's Stylistic sets heading"
    assert set(FEATURE_DESCRIPTIONS) == readme_sets
    assert all(isinstance(text, str) and text for text in FEATURE_DESCRIPTIONS.values())


class _HtmlSanity(HTMLParser):
    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.counts = {"main": 0, "h1": 0}
        self.references: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.counts:
            self.counts[tag] += 1
        attr_dict = dict(attrs)
        for key in ("href", "src"):
            value = attr_dict.get(key)
            if value:
                self.references.append(value)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"close </{tag}> with empty stack")
            return
        if self.stack[-1] != tag:
            self.errors.append(f"close </{tag}> but open is <{self.stack[-1]}>")
        else:
            self.stack.pop()


def test_index_html_sanity():
    """Checks the app shell at its source: `copy_static` copies the `fingerprint.static_files` listing of engine/review/static/ byte for byte, so the page a reviewer loads is this file, and its local references must resolve within that listing."""
    parser = _HtmlSanity()
    parser.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    assert parser.errors == []
    assert parser.stack == []
    assert parser.counts["main"] == 1
    assert parser.counts["h1"] == 1
    served = fingerprint.static_files(STATIC_DIR)
    for reference in parser.references:
        if "//" in reference or reference.startswith(("#", "mailto:", "data:")):
            continue
        target = STATIC_DIR / reference.split("#")[0].split("?")[0]
        assert target in served, f"dangling reference {reference}"


def test_node_check_passes_on_every_shipped_script():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed on this machine")
    scripts = [path for path in fingerprint.static_files(STATIC_DIR) if path.suffix == ".js"]
    assert scripts
    for script in scripts:
        result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def _seed_refreshable_corpus(
    corpus: Path, inputs_fingerprint: dict, root: Path, mode: str | None = None
) -> dict:
    """Build a corpus holding only the files `_check_output_files` requires: an empty manifest, of `mode` when one is given, both app sidecars stamped for that manifest, and the copied after font, whose bytes match both the sha the manifest records and the Senior-Regular.otf under `root`. `refresh_assets` writes index.html itself from the static tree it copies. The font record names no `source`, so the check compares the copy with the manifest only and resolves no path inside the fixture tree."""
    corpus.mkdir(parents=True, exist_ok=True)
    font_bytes = b"OTTO-fixture"
    (root / "engine" / "out" / "build").mkdir(parents=True, exist_ok=True)
    (root / "engine" / "out" / "build" / "Senior-Regular.otf").write_bytes(font_bytes)
    (corpus / "fonts").mkdir(parents=True, exist_ok=True)
    (corpus / "fonts" / "after.otf").write_bytes(font_bytes)
    manifest = {
        **({"format": review_build.MANIFEST_FORMAT, "mode": mode} if mode is not None else {}),
        "generated_at": "2026-01-01T00:00:00Z",
        "repo_head": "0" * 40,
        "inputs_fingerprint": dict(inputs_fingerprint),
        "classes": [],
        "fonts": {"after": {"file": "fonts/after.otf", "sha256": hashlib.sha256(font_bytes).hexdigest()}},
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    app_index.write_app_artifacts(corpus, {}, {})
    return manifest


def _fake_static_tree(root: Path) -> Path:
    static = root / "engine" / "review" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<!DOCTYPE html>\n<html><body><main></main></body></html>\n")
    (static / "app.js").write_text("export const app = 1;\n")
    return static


def test_refresh_assets_restamps_only_the_static_component(tmp_path):
    """Checks everything an assets refresh does: it copies the app shell onto the corpus, rewrites the `static` fingerprint component in place, and changes nothing else. `generated_at` stays, so an open review session keeps its verdict store. Both sidecars stay current, because the manifest's identity excludes this component. A table-diff corpus, the one the cycle builds, that `corpus_build_skippable` reported as stale before the refresh reports as current after it, so the next pass skips the build."""
    from engine.review import status
    from engine.tools.artifact_cycle import corpus_build_skippable

    root = tmp_path / "repo"
    static = _fake_static_tree(root)
    build_out = root / "engine" / "out" / "build"
    build_out.mkdir(parents=True)
    corpus = root / "engine" / "out" / "review"
    before = _seed_refreshable_corpus(
        corpus,
        {**status.table_diff_inputs(root, build_out=build_out), "static": "OLD"},
        root,
        mode="table-diff",
    )
    assert not corpus_build_skippable(root, corpus)

    copied = review_build.refresh_assets(corpus, root)

    assert sorted(copied) == ["app.js", "index.html"]
    assert (corpus / "app.js").read_text() == (static / "app.js").read_text()
    assert (corpus / "index.html").read_text() == (static / "index.html").read_text()
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    recorded = manifest["inputs_fingerprint"]
    assert recorded["static"] == fingerprint.hash_paths(root, fingerprint.static_paths(root))
    assert recorded["static"] != "OLD"
    assert {name: value for name, value in recorded.items() if name != "static"} == {
        name: value for name, value in before["inputs_fingerprint"].items() if name != "static"
    }
    assert manifest["generated_at"] == before["generated_at"]
    for name, fmt in app_index.ARTIFACTS:
        assert app_index.artifact_is_current(corpus, name, fmt)
    assert corpus_build_skippable(root, corpus)


def test_refresh_assets_refuses_a_corpus_it_cannot_restamp(tmp_path):
    """A corpus built before input fingerprinting has no component to rewrite, so the refresh exits instead of adding one; that corpus needs a full rebuild."""
    root = tmp_path / "repo"
    _fake_static_tree(root)
    corpus = root / "engine" / "out" / "review"
    corpus.mkdir(parents=True)
    with pytest.raises(SystemExit):
        review_build.refresh_assets(corpus, root)
    (corpus / "manifest.json").write_text(json.dumps({"generated_at": "x", "classes": []}))
    with pytest.raises(SystemExit):
        review_build.refresh_assets(corpus, root)


def test_refresh_assets_puts_the_manifest_back_when_the_corpus_is_broken(tmp_path):
    """The restamp makes the corpus read as current, so a failed contract check must undo it. A manifest left claiming freshness over sidecars that no longer match it would make the next cycle skip the rebuild, which is the only repair."""
    root = tmp_path / "repo"
    _fake_static_tree(root)
    corpus = root / "engine" / "out" / "review"
    _seed_refreshable_corpus(corpus, {"static": "OLD"}, root)
    app_index.artifact_path(corpus, app_index.APP_INDEX_NAME).unlink()
    with pytest.raises(SystemExit):
        review_build.refresh_assets(corpus, root)
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs_fingerprint"]["static"] == "OLD"


def test_the_refresh_assets_verb_copies_the_shipped_app(tmp_path):
    """Runs the `refresh-assets` subcommand the artifact cycle calls, over the real engine/review/static/: the shipped shell is copied onto the corpus, and the `static` component carries the value `fingerprint.stage_b` computes."""
    corpus = tmp_path / "corpus"
    _seed_refreshable_corpus(corpus, {"static": "OLD"}, tmp_path)
    review_build.main(["refresh-assets", "--out", str(corpus)])
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs_fingerprint"]["static"] == fingerprint.hash_paths(
        REPO_ROOT, fingerprint.static_paths(REPO_ROOT)
    )
    assert (corpus / "index.html").read_text(encoding="utf-8") == (STATIC_DIR / "index.html").read_text(
        encoding="utf-8"
    )
    for name, fmt in app_index.ARTIFACTS:
        assert app_index.artifact_is_current(corpus, name, fmt)


def test_the_app_copy_and_the_static_component_leave_out_dotfiles(tmp_path):
    """A `uv run` started in the app directory, or a `.DS_Store`, neither moves the `static` component nor reaches the served corpus, because `static_paths` and `copy_static` share one listing."""
    root = tmp_path / "repo"
    static = _fake_static_tree(root)
    clean = fingerprint.hash_paths(root, fingerprint.static_paths(root))
    (static / ".DS_Store").write_bytes(b"finder")
    (static / ".uv-cache" / "sdists-v9").mkdir(parents=True)
    (static / ".uv-cache" / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    (static / ".uv-cache" / "sdists-v9" / ".git").write_bytes(b"")

    assert [path.relative_to(static).as_posix() for path in fingerprint.static_paths(root)] == [
        "app.js",
        "index.html",
    ]
    assert fingerprint.hash_paths(root, fingerprint.static_paths(root)) == clean
    corpus = tmp_path / "corpus"
    assert sorted(review_build.copy_static(corpus, static)) == ["app.js", "index.html"]
    assert sorted(path.relative_to(corpus).as_posix() for path in corpus.rglob("*")) == [
        "app.js",
        "index.html",
    ]


def test_the_app_and_font_copies_never_leave_a_truncated_file_in_place(tmp_path, monkeypatch):
    """`copy_static` and `_copy_font` write the running app's files, which a tab can fetch mid-copy, so each copy goes to a `.partial` file that is renamed into place. A copy that fails halfway, or a font whose digest does not match, leaves the served file as it was and no staging file behind."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.js").write_text("new app\n")
    (static / "index.html").write_text("new index\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "app.js").write_text("old app\n")
    (corpus / "index.html").write_text("old index\n")
    real_copyfile = review_build.shutil.copyfile

    def copy_half_of_the_index(source, target):
        if Path(source).name != "index.html":
            return real_copyfile(source, target)
        Path(target).write_bytes(Path(source).read_bytes()[:3])
        raise OSError("disk full")

    monkeypatch.setattr(review_build.shutil, "copyfile", copy_half_of_the_index)
    with pytest.raises(OSError):
        review_build.copy_static(corpus, static)
    assert (corpus / "app.js").read_text() == "new app\n"
    assert (corpus / "index.html").read_text() == "old index\n"
    monkeypatch.setattr(review_build.shutil, "copyfile", real_copyfile)

    font = tmp_path / "Senior-Regular.otf"
    font.write_bytes(b"a font that moved since the build loaded it")
    (corpus / "fonts").mkdir()
    (corpus / "fonts" / "after.otf").write_bytes(b"the font the units describe")
    with pytest.raises(SystemExit):
        review_build._copy_font(font, corpus, "after.otf", "AMS Review After", tmp_path, "0" * 64)
    assert (corpus / "fonts" / "after.otf").read_bytes() == b"the font the units describe"
    assert not list(corpus.rglob("*.partial"))


def test_a_second_copy_into_the_same_corpus_leaves_the_unchanged_app_files_and_fonts_in_place(tmp_path):
    """`copy_static` and `_copy_font` leave a copy that already holds the bytes they would put there in place, so a corpus build over an unchanged app and unchanged fonts rewrites none of them, and each keeps its inode and modification time, here set long ago so that any write would move it. `_copy_font` compares the copy with the digest the build took at load, so the font it keeps is the one the units describe. A changed app file is copied into place, and the fallback index.html a tree without one gets is kept the same way."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.js").write_text("app\n")
    font_digest = hashlib.sha256((MINI / "Senior-Regular.otf").read_bytes()).hexdigest()
    corpus = tmp_path / "corpus"
    copies = [corpus / "app.js", corpus / "index.html", corpus / "fonts" / "after.otf"]

    def copy() -> dict:
        review_build.copy_static(corpus, static)
        return review_build._copy_font(
            MINI / "Senior-Regular.otf", corpus, "after.otf", "AMS Review After", REPO_ROOT, font_digest
        )

    first = copy()
    for path in copies:
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = {path: (path.stat().st_ino, path.stat().st_mtime_ns) for path in copies}
    assert copy() == first
    assert {path: (path.stat().st_ino, path.stat().st_mtime_ns) for path in copies} == before
    (static / "app.js").write_text("new\n")
    copy()
    assert (corpus / "app.js").read_text() == "new\n"
    assert (corpus / "app.js").stat().st_mtime_ns != 1_000_000_000


def _padded(count: int, filler: int = 0) -> list[dict]:
    return [{"id": f"u-{index:04d}", "pad": "x" * filler} for index in range(count)]


def test_write_shard_keeps_a_class_under_the_cap_in_one_bare_file(tmp_path):
    """A class under the size cap keeps one `units/<class-id>.json` file, the path the small classes, the checked-in fixtures, and archived corpora already use, and its bytes match one `json.dumps` call."""
    fragments = _padded(4, 40)
    assert _write_shard(tmp_path, "small", fragments)[0] == ["units/small.json"]
    path = tmp_path / "units" / "small.json"
    assert path.read_bytes() == (json.dumps(fragments, indent=1, ensure_ascii=True) + "\n").encode("utf-8")
    assert sorted(entry.name for entry in (tmp_path / "units").iterdir()) == ["small.json"]


def test_write_shard_splits_a_class_that_outgrows_the_cap(tmp_path, monkeypatch):
    """Past the cap, the class is written as parts numbered from `000` with no bare file beside them. Each part is within the cap, each part's bytes match one `json.dumps` of that part, and the parts concatenate to the class in order."""
    monkeypatch.setattr("engine.review.build.SHARD_PART_BYTES", 512)
    fragments = _padded(24, 60)
    parts, _spans = _write_shard(tmp_path, "big", fragments)
    assert len(parts) > 1
    assert parts == [f"units/big.{index:03d}.json" for index in range(len(parts))]
    assert not (tmp_path / "units" / "big.json").exists()
    seen: list[dict] = []
    for part in parts:
        raw = (tmp_path / part).read_bytes()
        assert len(raw) <= 512, part
        units = json.loads(raw)
        assert raw == (json.dumps(units, indent=1, ensure_ascii=True) + "\n").encode("utf-8"), part
        seen.extend(units)
    assert seen == fragments


def test_write_shard_gives_an_oversized_fragment_a_part_of_its_own(tmp_path, monkeypatch):
    """A single fragment larger than the cap is written in a part of its own, not split, and the fragments around it stay in their own parts."""
    monkeypatch.setattr("engine.review.build.SHARD_PART_BYTES", 128)
    fragments = [{"id": "u-0000"}, {"id": "u-0001", "pad": "x" * 400}, {"id": "u-0002"}]
    parts, _spans = _write_shard(tmp_path, "big", fragments)
    assert [json.loads((tmp_path / part).read_text(encoding="utf-8")) for part in parts] == [
        [fragments[0]],
        [fragments[1]],
        [fragments[2]],
    ]


def test_write_shard_writes_an_empty_class_as_one_empty_array(tmp_path):
    assert _write_shard(tmp_path, "empty", []) == (["units/empty.json"], [])
    assert (tmp_path / "units" / "empty.json").read_bytes() == b"[]\n"


def test_write_shard_leaves_no_staging_file_behind_when_serializing_fails(tmp_path):
    """Parts are staged and renamed for the same reason `_write_json` stages: an encode that fails partway must leave the previous build's units in place, with no truncated part or leftover `.partial` file."""
    with pytest.raises(TypeError):
        _write_shard(tmp_path, "big", [{"id": "u-0000"}, {"id": object()}])
    assert list((tmp_path / "units").iterdir()) == []


def test_prune_orphan_shards_removes_only_unreferenced_json(tmp_path):
    """Every part the manifest lists is kept, and both forms of an unlisted name are removed: the bare file a class left when it grew into parts, and a numbered part a shrinking class no longer fills."""
    units = tmp_path / "units"
    units.mkdir()
    for name in ("a.json", "b.json", "big.json", "big.000.json", "big.001.json", "big.002.json"):
        (units / name).write_text("[]", encoding="utf-8")
    (units / "stray.txt").write_text("keep me", encoding="utf-8")
    manifest = {
        "classes": [
            {"shards": ["units/a.json"]},
            {"shards": ["units/big.000.json", "units/big.001.json"]},
        ]
    }
    removed = _prune_orphan_shards(tmp_path, manifest)
    assert removed == ["b.json", "big.002.json", "big.json"]
    assert (units / "a.json").exists()
    assert (units / "big.000.json").exists()
    assert (units / "big.001.json").exists()
    assert (units / "stray.txt").exists()
    assert not (units / "b.json").exists()


def test_prune_orphan_shards_no_units_dir_is_noop(tmp_path):
    assert _prune_orphan_shards(tmp_path, {"classes": []}) == []


@pytest.mark.parametrize(
    "payload",
    (
        [],
        {},
        [{}, [], [{}]],
        [1, -2, 3.5, True, False, None, "x"],
        [{"a": {"b": [1, {"c": [[]]}]}}, {"a": []}],
        [{"letter": "·Pea", "raw": 'line\nbreak\ttab "quote" back\\slash \x1f'}],
        {"format": 3, "classes": [{"id": "a", "shards": ["units/a.json"]}], "why": None},
    ),
)
def test_write_json_matches_one_shot_dumps_byte_for_byte(tmp_path, payload):
    """`_write_json` writes a list one element at a time instead of building the whole string, so it assembles around per-element calls the framing and depth-1 indent that one `json.dumps` call would produce. The bytes must match because a shard's framing is the byte addressing the app's sidecars read (`engine/test_app_index.py`). This test compares against the one-call form over the shapes a shard or manifest can hold, including empty collections and escaped non-ASCII, where hand-built framing is most likely to differ."""
    target = tmp_path / "payload.json"
    _write_json(target, payload)
    assert target.read_bytes() == (json.dumps(payload, indent=1, ensure_ascii=True) + "\n").encode("utf-8")
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_write_json_reproduces_the_checked_in_fixture_shards(tmp_path):
    """The same comparison over the checked-in fixture shards and manifest: re-serializing them with `_write_json` reproduces them byte for byte."""
    sources = (FIXTURES / "manifest.json", *sorted((FIXTURES / "units").glob("*.json")))
    assert len(sources) > 1
    for source in sources:
        target = tmp_path / source.name
        _write_json(target, json.loads(source.read_text(encoding="utf-8")))
        assert target.read_bytes() == source.read_bytes(), source.name


def test_write_json_leaves_the_previous_file_alone_when_serializing_fails(tmp_path):
    """Because `_write_json` writes element by element, the encoder can fail after the file is started, so the write goes to a staging file that is renamed into place. Nothing downstream tolerates a half-written corpus file (`check_output_dir` and every manifest reader parse the files on disk, and the shards are written before the manifest that stamps them), so a failed write must leave the last good file in place and no staging file behind."""
    shard, manifest = tmp_path / "shard.json", tmp_path / "manifest.json"
    _write_json(shard, [{"id": "u-0000"}])
    _write_json(manifest, {"at": "old"})
    intact = {path: path.read_bytes() for path in (shard, manifest)}
    with pytest.raises(TypeError):
        _write_json(shard, [{"id": "u-0000"}, {"id": object()}])
    with pytest.raises(TypeError):
        _write_json(manifest, {"at": object()})
    assert {path: path.read_bytes() for path in (shard, manifest)} == intact
    assert sorted(path.name for path in tmp_path.iterdir()) == ["manifest.json", "shard.json"]


def test_a_build_into_the_served_corpus_refuses_while_the_review_server_listens(tmp_path, monkeypatch):
    """A build rewrites the files the open tabs read in place, so it refuses to write the served corpus while the review server's port answers, and says to run the cycle, which builds beside it. The refusal holds for a server that names this checkout as its root and for one that names none. A build elsewhere, one with no server up, one beside a server that names another checkout (a worktree's first pass beside the main checkout's server), and one into a served corpus with no manifest (the cycle's first run, which has nothing to land beside) go ahead. The port it checks is the server's."""
    from engine.review.serve import PORT

    assert review_build.REVIEW_SERVER_PORT == PORT
    served = tmp_path / "engine" / "out" / "review"
    served.mkdir(parents=True)
    monkeypatch.setattr(review_build, "DEFAULT_OUT", served)
    monkeypatch.setattr(review_build, "review_server_root", lambda port=PORT: None)
    monkeypatch.setattr(review_build, "review_server_listening", lambda port=PORT: True)
    review_build.refuse_the_served_corpus(served)
    (served / "manifest.json").write_text("{}")
    monkeypatch.setattr(
        review_build, "build_table_diff", lambda *a, **k: pytest.fail("built under a live server")
    )
    for root in (None, str(review_build.REPO_ROOT)):
        monkeypatch.setattr(review_build, "review_server_root", lambda port=PORT, root=root: root)
        with pytest.raises(SystemExit, match="REFUSING TO BUILD.*make artifact-cycle"):
            review_build.main([])
    monkeypatch.setattr(
        review_build, "review_server_root", lambda port=PORT: str(tmp_path / "other-checkout")
    )
    review_build.refuse_the_served_corpus(review_build.DEFAULT_OUT)
    review_build.refuse_the_served_corpus(tmp_path / "elsewhere")
    monkeypatch.setattr(review_build, "review_server_listening", lambda port=PORT: False)
    review_build.refuse_the_served_corpus(review_build.DEFAULT_OUT)

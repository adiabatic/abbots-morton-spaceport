"""Content fingerprints of the build inputs, split into named components so the readiness checker can say which component is stale and what rebuilds it.

Each component is a SHA-256 over sorted lines that hold no mtimes, so two builds of the same inputs produce the same value. The lines are `label\\tdigest` pairs over file contents. The corpus manifest's `generated_at` is mtime-based and identifies a corpus build, so that exported verdicts and sidecars can be matched to it. It cannot tell whether the corpus matches the sources on disk; these fingerprints can.

run_build records the Stage A components (`data`, `pipeline_code`) in `engine/out/build/inputs_fingerprint.json`, beside the alphabet token while the alphabet switch is set (`stage_a`). A build or a `--gates-only` pass deletes that record immediately before its first write there, and writes back the values it captured before reading its inputs only once its gates pass (`run_build_gates._settle_stage_a`), so a record matches the sources on disk only beside the artifacts of a passing run over them (`artifact_cycle.stage_a_current`). The review corpus carries `TABLE_DIFF_COMPONENTS` (`table_diff_values`), whose `tables` component the build copies from the windows heads of the build it compares.

`tables_value` is the source stamp a serialized window enumeration carries, so the conformance sweep can tell whether the tables on disk were built from the sources on disk. It covers only what the table fixpoint and the font compile read. Its data half, `data_value`, hashes each rune file without its `kern` block (`rune_file_digest`), which the kernel's spec dump leaves out too. Its code half, `table_code_paths`, leaves out the comparison-side code `COMPARISON_CODE_MODULES` names. That code runs only in the gates that check the tables and font a build has written, so editing it re-runs them over the enumeration on disk instead of discarding it. engine/test_build_code_closure.py checks that the build never reaches the comparison modules.

The human-reviewed contact allow-list has a prose-insensitive digest, like the rune files below. It is in no component, not even `data`. Only the defect gate reads it, so its digest (`contact_allow_digest`, under `CONTACT_ALLOW_LABEL`) is only in the artifact cycle's run_build skip key. Changing a signature re-runs that gate and nothing else. Each entry's `why` is left out of the digest.

`rune_file_digest` hashes a rune's parsed document instead of its bytes, with the prose removed: YAML comments and formatting, the ductus text, `notes`, and every `why`, refuse records' included. No build step reads any of these, so editing them should not make the corpus stale or re-run a cycle. It also drops the `kern` block whole. The digest keeps every geometric and policy field, the ductus keys (motion names, which the lints check), and the presence of every prose field, because the schema requires `why` on some records.

The full font compile reads the non-Quikscript part of the font beside the runes (`font_data_paths`: the Latin, numeral, punctuation, composite, symbol and Shavian glyphs, the metadata, the Latin kern rules and the glyph-name map). No table or settlement reads them, so they are in no Stage A component and not in the tables' stamp. They reach the run_build skip key alone, one line per file (`font_data_lines`, labeled `<label>#font`), and a moved font-data line, like a moved kern line, makes the cycle recompile the font over the tables on disk (`artifact_cycle.kern_only_rebuild`). glyph_data/punctuation.yaml is the exception: the namer dot's glyphs come from it, so it keeps its data line, and an edit to it, the angle parentheses and `space` among its records, rebuilds the tables. The review corpus's `font_inputs` component holds the font-data lines, so a Latin glyph edit rebuilds the corpus.

A rune's `kern` block reaches no table, no settlement and nothing the review corpus shows, so it is in no key built from `rune_file_digest`: not the `data` component, the tables' stamp, the settle memo's family keys (`rune_digests`), or the string replay's and deep replay's narrowing. It reaches the run_build skip key alone, through one line per rune (`kern_lines`, `rune_kern_digest`: the records less their `why`), so a kern edit rebuilds the font and nothing else (`artifact_cycle.kern_only_rebuild`). The engine suite's input closure hashes rune files whole, kern block included (`rune_document_digest`), because a test can read any part of a file.

The other data inputs have prose-insensitive digests too, chosen by `_data_digest`. A schema under engine/schema/ is hashed by `schema_file_digest`: its parsed JSON with key order kept and every `title` and `description` annotation removed from the positions where a schema sits. `spec_load._SchemaValidator._ANNOTATIONS` declares these as annotations, and its standard validator's `VALIDATORS` dispatch does not evaluate them. A property named `description` stays, because the projection removes keywords from schemas and never names from `properties`; engine/schema/script.schema.json declares one. The `$schema` and `$id` metadata stay too, because they identify the dialect and reference scope. The registry, glyph_data/script.yaml, and glyph_data/punctuation.yaml are hashed by `yaml_data_digest`, their parsed documents with key order kept, which drops comments and formatting and nothing else. Key order stays because no reader of these files has been shown not to depend on it, so a pure reorder still moves the digest. A file that fails to parse is hashed raw. `data_path_lines` hashes every data input this way wherever one reaches a key: the `data` component and the run_build skip key, `tables_value` and `run_build.locality_lines`, and the `data` line of the settle memo's stamp (`settle_memo_keys.stamped_data_paths`). Rewording a schema hover or a comment in one of these files therefore moves none of them.

`code_file_digest` projects code files the same way. A `.py` file is hashed as its syntax tree with every docstring's text set to None, and a `.rs` file with its whole-line `//` comments removed, so rewording either moves no key built by `path_lines` or `hash_paths`. Everything the interpreter or compiler sees stays in: every identifier, every non-docstring string constant (matchers compare against error text), every annotation, default, and decorator, and every Rust code line including its trailing comment. The presence of each docstring stays too. A file that fails to parse or decode is hashed as raw bytes, as `_projected_digest` does, so the failure stays visible. Every other file type that reaches `path_lines`, such as the app's static files and the crate's manifest and lock, is hashed raw by `file_sha256`.

Three closures outside this module hash code files raw. The engine suite's closure (`artifact_cycle._closure_digest`) and gate:make-test's closure (`artifact_cycle.make_test_closure_fingerprint`) do so because test fixtures and the closure tests read source text, so gate:engine-contracts and gate:make-test still run after a prose-only edit. The pyright gate's closure (`pyright_gate.closure_fingerprint`) does so because a `# pyright: ignore` comment changes pyright's result. The first two hash data inputs raw as well. The engine suite's closure hashes the schemas and the other YAML data inputs, so a schema hover or a data-file comment still re-runs the contracts tests whose closure holds the file. gate:make-test's closure hashes glyph_data/punctuation.yaml, which the Mono and Junior builds read, so a comment edit to it still runs `make test`. engine/test_fingerprint.py checks what the projection keeps and drops.

The fonts and `uv.lock`, which a version bump rewrites, have version-blind digests too, so neither moves a component or key that hashes it this way. The bump still reruns the engine suite and pyright, whose closures hash pyproject.toml raw, and gate:make-test, whose closure hashes glyph_data/metadata.yaml raw. A build writes the new version into each font's `name` table and `head.fontRevision`, and the bump-minor skill refreshes `uv.lock`, where only the project's own `[[package]]` block changes.

- `font_content_digest` hashes a font table by table from the bytes the `sfnt` reader returns: the sorted table tags, then each table's length and digest, leaving out `head` and `name` (`FONT_VERSION_TABLES`). `head` holds the revision, the checksum adjustment, and the modification time, and `name` holds the version strings. A shaper reads neither to position a glyph. `head` also holds `unitsPerEm`, but an edit to `units_per_em` in glyph_data/metadata.yaml still reaches the digest through `CFF `: engine/pipeline/font_assembly.py passes `FontBuilder.setupCFF` no FontMatrix, so fontTools writes the matrix as 1/unitsPerEm. engine/test_fingerprint.py checks that fontTools default. Only a hand edit to `head.unitsPerEm` alone would go unseen, and nothing in the tree makes one. Every table that can affect a shaped run stays (`CFF `, `hmtx`, `GPOS`, `GSUB`, `cmap`), and so does the list of tables. A file fontTools cannot open, or whose table it cannot read, is hashed as raw bytes.
- `lock_digest` (in `engine.tools.lock_digest`, a separate module because the pyright gate also hashes the lock and must not import engine.pipeline) removes the project's own block from the lock and hashes the rest, so a changed dependency pin or an added or removed package still moves it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml

from engine.pipeline import spec_load
from engine.tools.lock_digest import lock_digest  # re-exported: callers read `fingerprint.lock_digest`

FORMAT = "ams-inputs-fingerprint/2"
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
STAGE_A_COMPONENTS = ("data", "pipeline_code")
# The inputs fingerprint of a table-diff corpus (`table_diff_values`), which compares a snapshot of the accepted build with a build and reads no site font.
TABLE_DIFF_COMPONENTS = ("snapshot", "tables", "font_inputs", "review_code", "static")
STAGE_A_FILENAME = "inputs_fingerprint.json"


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes, streamed so the file is never held in memory whole. engine/test_fingerprint.py checks that no engine module hashes a file it read whole, and that the inline copies in modules that cannot import this one return the same value. Each caller handles a missing file itself."""
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def rune_paths(repo_root: Path) -> list[Path]:
    return sorted((Path(repo_root) / "glyph_data" / "runes").glob("*.yaml"))


def registry_path(repo_root: Path) -> Path:
    """Return the registry (`spec_load.DEFAULT_REGISTRY_PATH`) under `repo_root`. It sits in glyph_data/ beside the glyph files and holds no glyph records: it is a table-side input (`data_paths`), and `font_data_paths` leaves it out by name."""
    return Path(repo_root) / spec_load.DEFAULT_REGISTRY_PATH.relative_to(spec_load.REPO_ROOT)


def data_paths(repo_root: Path) -> list[Path]:
    root = Path(repo_root)
    paths = rune_paths(root)
    paths += sorted((root / "engine" / "schema").glob("*.json"))
    paths += [
        registry_path(root),
        root / "glyph_data" / "punctuation.yaml",
    ]
    return paths


FONT_LINE_SUFFIX = "#font"  # the suffix of a font-data line in the run_build skip key (`font_data_lines`)


def font_data_paths(repo_root: Path) -> list[Path]:
    """Return the files the full font compile reads beside the runes, in the order it merges them: every glyph_data/*.yaml but the registry (the Latin, numeral, punctuation, composite, symbol and Shavian glyphs, the metadata, and the Latin `kern` rules), then postscript_glyph_names.yaml, which maps the glyph names to code points. `compile_font.load_font_sources` reads exactly these, so the list is the one authority on what the compile reads, for Senior and for Mono and Junior (`build_family`). The registry (`registry_path`) is left out by name, because `font_assembly.load_glyph_data` refuses a document whose top-level keys are not a glyph file's."""
    root = Path(repo_root)
    registry = registry_path(root).name
    glyph_files = sorted(path for path in (root / "glyph_data").glob("*.yaml") if path.name != registry)
    return glyph_files + [root / "postscript_glyph_names.yaml"]


def font_data_lines(repo_root: Path) -> list[str]:
    """Return one `<label>#font\\t<digest>` line per font-compile input outside `data_paths`, sorted: each `font_data_paths` file by `_data_digest`. glyph_data/punctuation.yaml is in `data_paths` already, because the namer dot's glyphs come from it, so its line stays there. These lines are the run_build skip key's font-data lines (`artifact_cycle.run_build_skip_lines`). They are in no Stage A component and not in the tables' stamp, because no table depends on a Latin or Shavian glyph: a moved font-data line alone makes the cycle recompile the font over the tables on disk, as a moved kern line does (`artifact_cycle.kern_only_rebuild`)."""
    root = Path(repo_root)
    runes = set(rune_paths(root))
    table_side = set(data_paths(root))
    lines = [
        f"{_label(root, path)}{FONT_LINE_SUFFIX}\t{_data_digest(root, path, runes)}"
        for path in font_data_paths(root)
        if path not in table_side and path.is_file()
    ]
    return sorted(lines)


# The code of `pipeline_code_paths` that the table build and the font compile never run, as repo-relative paths: the gates that check the tables and font a build has written. These are the pin gate (manual_pins.py) and the engine/validation modules it checks the pins through (the grammar, expect.py; the corpus collector, corpus.py; the checker, check.py; classify.py and shaping.py), run_build's gate half (run_build_gates.py), and the shared memory segments the conformance sweep's pool hands its bytes home in (shared_segments.py). They stay in `pipeline_code_paths`, and `table_code_paths` leaves them out, so an edit to one re-runs the gates over the tables on disk instead of forcing a full build. A module of this kind that is missing from the set is hashed into the tables' stamp, so every edit to it forces a full build. engine/test_build_code_closure.py fails if the build reaches any module listed here.
COMPARISON_CODE_MODULES = frozenset(
    {
        "engine/pipeline/manual_pins.py",
        "engine/pipeline/run_build_gates.py",
        "engine/pipeline/shared_segments.py",
        "engine/validation/check.py",
        "engine/validation/classify.py",
        "engine/validation/corpus.py",
        "engine/validation/expect.py",
        "engine/validation/shaping.py",
    }
)


def pipeline_code_paths(repo_root: Path) -> list[Path]:
    """Return the code the `pipeline_code` component hashes: engine/pipeline, engine/validation, and the kernel crate's manifest, lock, and sources. engine/pipeline holds the generic font assembly the compile passes the font to (`font_assembly`, `glyph_records`, `departure_mono_import`), so an edit to the font assembly, the glyph records' compile or the Departure Mono import changes Senior-Regular.otf and moves this component, and `table_code_paths` keeps them as well. engine/validation holds the shaper, the junction classifier, and the `data-expect` grammar, collector and checker the pin gate runs. Whole trees are hashed instead of a list of imported modules, because such a list goes stale when an import or a Rust module is added, and over-invalidating is the safe error."""
    root = Path(repo_root)
    kernel = root / "engine" / "kernel-rs"
    return (
        sorted((root / "engine" / "pipeline").glob("*.py"))
        + sorted(path for path in (root / "engine" / "validation").glob("*.py"))
        + [kernel / "Cargo.toml", kernel / "Cargo.lock"]
        + sorted((kernel / "src").rglob("*.rs"))
    )


REVIEW_NON_BUILD_MODULES = frozenset(
    {
        "accept.py",
        "serve.py",
        "verdict_store.py",
        "status.py",
        "store_lock.py",
        "landing.py",
        "kern_routes.py",
        "kern_writer.py",
    }
)


def review_code_paths(repo_root: Path) -> list[Path]:
    """Return engine/review/*.py without `REVIEW_NON_BUILD_MODULES`, the modules the corpus build never imports, so an edit the build cannot execute does not make the corpus stale. serve.py is the dev server and verdict_store.py the store it keeps; status.py is the readiness check, store_lock.py the lock every writer of the verdict store holds, and landing.py moves a pass's corpus into place and carries the store onto it. accept.py accepts a table-diff corpus's changes by taking a new snapshot of the accepted build, and remakes a missing snapshot. kern_routes.py is the server's kerning routes and kern_writer.py the block writer and journal they write through, which read `engine/out/build` and the rune files, write the rune files' `kern:` blocks and `var/kern/`, and touch nothing of the corpus. engine/test_review_code_closure.py checks this set in both directions against the import graph of build.py."""
    return sorted(
        path
        for path in (Path(repo_root) / "engine" / "review").glob("*.py")
        if path.name not in REVIEW_NON_BUILD_MODULES
    )


def static_files(static_dir: Path) -> list[Path]:
    """Return the review app's files under `static_dir`, sorted, leaving out any path with a dot-prefixed part below that directory: the `.uv-cache/` a `uv run` started there leaves behind, or a `.DS_Store`. No tracked app asset is a dotfile. `table_diff_values` and `engine.review.build.refresh_assets` hash this list, through `static_paths`, into the manifest's `static` component, and `engine.review.build.copy_static` copies the same list, so the recorded value and the copied files name one set. Path parts are judged relative to `static_dir`, so a checkout under a dot directory keeps its files."""
    static_dir = Path(static_dir)
    return sorted(
        path
        for path in static_dir.rglob("*")
        if not any(part.startswith(".") for part in path.relative_to(static_dir).parts) and path.is_file()
    )


def static_paths(repo_root: Path) -> list[Path]:
    return static_files(Path(repo_root) / "engine" / "review" / "static")


def _label(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return path.name


def _without_docstrings(tree: ast.Module) -> ast.Module:
    """Set the text of every docstring in `tree` to None, in place, and return the tree. A docstring is the leading string expression of a module, class, or function body, the one `ast.get_docstring` reads. The expression itself stays, so adding or removing a docstring still changes the digest, and a module holding only a docstring does not hash like an empty one."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                first.value.value = None
    return tree


def _projected_python(raw: bytes) -> str:
    """Return the AST dump of a Python source with its docstrings blanked. The dump has no line positions, so comments, blank lines, and shifted line numbers don't change it; identifiers, constants, annotations, defaults, and decorators do."""
    return ast.dump(_without_docstrings(ast.parse(raw)))


def _projected_rust(raw: bytes) -> str:
    """Return a Rust source with every whole-line comment removed: each line whose left-stripped text starts with `//`, which covers `///` and `//!`. A trailing comment after code stays with its line."""
    return "\n".join(line for line in raw.decode().splitlines() if not line.lstrip().startswith("//"))


_CODE_PROJECTIONS: dict[str, Callable[[bytes], str]] = {".py": _projected_python, ".rs": _projected_rust}
_CODE_DIGESTS: dict[tuple[str, str], str] = {}


def code_file_digest(path: Path) -> str:
    """Return a prose-insensitive digest for a `.py` or `.rs` file (the module docstring says what the projection drops), and `file_sha256` for any other suffix. The suffix is checked before the file is opened, so a font reaching `path_lines` is streamed and never read whole. A file that fails to parse or decode digests to its raw bytes, so two broken drafts don't share a value.

    Results are memoized per process on the suffix and the raw content digest. The stamp functions ask for the same files many times in one cycle, and the projection costs far more than the raw hash. A key on size and mtime could return a stale projection for a file rewritten to the same size within one clock tick, which the tests do.
    """
    project = _CODE_PROJECTIONS.get(path.suffix)
    if project is None:
        return file_sha256(path)
    raw_digest = file_sha256(path)
    key = (path.suffix, raw_digest)
    digest = _CODE_DIGESTS.get(key)
    if digest is None:
        try:
            digest = hashlib.sha256(project(path.read_bytes()).encode()).hexdigest()
        except SyntaxError, ValueError, RecursionError:
            digest = raw_digest
        _CODE_DIGESTS[key] = digest
    return digest


def path_lines(repo_root: Path, paths: list[Path]) -> list[str]:
    """Return the sorted `label\\tdigest` lines that `hash_paths` hashes, one per existing file, each digest from `code_file_digest`. A green record can store them so that a skip miss names the file that changed."""
    return sorted(f"{_label(repo_root, path)}\t{code_file_digest(path)}" for path in paths if path.is_file())


def digest_lines(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def hash_paths(repo_root: Path, paths: list[Path]) -> str:
    return digest_lines(path_lines(repo_root, paths))


FONT_VERSION_TABLES = frozenset({"head", "name"})
_FONT_DIGESTS: dict[str, str] = {}


def _font_content_lines(path: Path) -> list[str] | None:
    """Return the lines `font_content_digest` hashes: the sorted table tags, then the length and digest of each table outside `FONT_VERSION_TABLES`. Each table is read from the reader as the bytes in the file, without decompiling, so fontTools' normalization on save cannot hide a difference. Returns None for a file that is not an `sfnt`, has no tables, or has a truncated table; the caller then hashes it raw."""
    from fontTools.ttLib import TTFont, TTLibError

    try:
        font = TTFont(str(path), lazy=True)
    except TTLibError, OSError, ValueError, struct.error:
        return None
    try:
        reader = font.reader
        tags = sorted(reader.keys()) if reader is not None else []
        if not tags or reader is None:
            return None
        lines = ["tables\t" + " ".join(tags)]
        for tag in tags:
            if tag in FONT_VERSION_TABLES:
                continue
            data = reader[tag]
            lines.append(f"{tag}\t{len(data)}\t{hashlib.sha256(data).hexdigest()}")
    except TTLibError, OSError, ValueError, AssertionError, struct.error:
        return None
    finally:
        font.close()
    return lines


def font_content_digest(path: Path) -> str:
    """Return a font's digest without its `head` and `name` tables (the module docstring says what is kept), or `file_sha256` for a file fontTools cannot read as an `sfnt`, so a fake fixture font and a truncated font each get a value of their own. Memoized per process on the raw content digest, for the same reason as `code_file_digest`."""
    raw_digest = file_sha256(path)
    digest = _FONT_DIGESTS.get(raw_digest)
    if digest is None:
        lines = _font_content_lines(path)
        digest = raw_digest if lines is None else digest_lines(lines)
        _FONT_DIGESTS[raw_digest] = digest
    return digest


KERN_FEATURE_TAG = "kern"


def _referenced_lookups(lookup: Any) -> list[int]:
    """The lookup indices a contextual lookup's rules invoke, in any of the three formats of a (chained) contextual GSUB or GPOS subtable."""
    found: list[int] = []
    for subtable in lookup.SubTable or []:
        subtable = getattr(subtable, "ExtSubTable", subtable)
        holders: list[Any] = [subtable]
        for set_name, rule_name in (
            ("ChainPosRuleSet", "ChainPosRule"),
            ("ChainPosClassSet", "ChainPosClassRule"),
            ("PosRuleSet", "PosRule"),
            ("PosClassSet", "PosClassRule"),
            ("ChainSubRuleSet", "ChainSubRule"),
            ("ChainSubClassSet", "ChainSubClassRule"),
            ("SubRuleSet", "SubRule"),
            ("SubClassSet", "SubClassRule"),
        ):
            for rule_set in getattr(subtable, set_name, None) or []:
                if rule_set is not None:
                    holders.extend(getattr(rule_set, rule_name, None) or [])
        for holder in holders:
            for records in (
                getattr(holder, "PosLookupRecord", None),
                getattr(holder, "SubstLookupRecord", None),
            ):
                found.extend(record.LookupListIndex for record in records or [])
    return found


def kern_only_lookups(table: Any) -> frozenset[int]:
    """The GPOS lookups only the `kern` feature reaches, directly or through the value lookups its contextual rules invoke: what a shaper with `kern` off never applies."""
    lookups: list[Any] = list(table.LookupList.Lookup if table.LookupList else ())
    records: list[Any] = list(table.FeatureList.FeatureRecord if table.FeatureList else ())

    def reached(roots: list[int]) -> set[int]:
        seen: set[int] = set()
        queue = list(roots)
        while queue:
            index = queue.pop()
            if index in seen or not 0 <= index < len(lookups):
                continue
            seen.add(index)
            queue.extend(_referenced_lookups(lookups[index]))
        return seen

    kern_roots = [
        i
        for record in records
        if record.FeatureTag == KERN_FEATURE_TAG
        for i in record.Feature.LookupListIndex
    ]
    other_roots = [
        i
        for record in records
        if record.FeatureTag != KERN_FEATURE_TAG
        for i in record.Feature.LookupListIndex
    ]
    return frozenset(reached(kern_roots) - reached(other_roots))


def _labels_of(lines: Iterable[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for line in lines:
        label, _, digest = line.partition("\t")
        labels[label] = digest
    return labels


@dataclass(frozen=True)
class EnvironmentStamp:
    """A whole-store stamp kept as `label\\tdigest` lines, so a store that records them can name the input that changed on a miss. `value` is the digest of `lines` alone. `detail` maps a label to the lines behind that label's digest (for a code closure, its `path_lines`), so a miss can name the file inside the closure. `detail` is not part of `value` or `labels`, so adding it leaves both unchanged.

    The settle memo's stamp takes this form (`settle_memo_keys.settle_memo_stamp`).
    """

    lines: tuple[str, ...]
    detail: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def value(self) -> str:
        return digest_lines(self.lines)

    @property
    def labels(self) -> dict[str, str]:
        """The same lines as a `label -> digest` map, which is the shape `moved_note` compares."""
        return _labels_of(self.lines)


def moved_note(
    recorded: Mapping[str, str],
    current: Mapping[str, str],
    limit: int = 8,
    expand: Mapping[str, str | None] | None = None,
) -> str | None:
    """Return a note naming the labels that differ between two `label -> digest` maps, or None when none differ. A changed label that `expand` maps to a note is shown as `label: note`; any other changed label is shown as `label (changed)`, and added or removed labels as `label (new)` or `label (gone)`. At most `limit` labels are listed, followed by a count of the rest. `artifact_cycle.moved_inputs_note` formats its notes the same way."""
    moved: list[str] = []
    for name in sorted(recorded.keys() & current.keys()):
        if recorded[name] == current[name]:
            continue
        inner = expand.get(name) if expand else None
        moved.append(f"{name}: {inner}" if inner else f"{name} (changed)")
    moved += [f"{name} (new)" for name in sorted(current.keys() - recorded.keys())]
    moved += [f"{name} (gone)" for name in sorted(recorded.keys() - current.keys())]
    if not moved:
        return None
    shown = ", ".join(moved[:limit])
    return f"{shown} and {len(moved) - limit} more" if len(moved) > limit else shown


def cursive_anchor_map(font) -> dict[str, list]:
    """Return, per glyph, one (lookup index, entry, exit) triple for each GPOS cursive-attachment record that names it, with anchors as [x, y] or None. Outside its charstring and advance, GPOS is the only table that changes how a compiled glyph renders, so the per-glyph digest includes it. The lookup index counts only the lookups outside the `kern` feature's reach (`kern_only_lookups`), so where the kern lookups sit in the lookup list moves no glyph's facts."""
    anchors: dict[str, list] = {}
    if "GPOS" not in font:
        return anchors
    table = font["GPOS"].table  # pyright: ignore[reportAttributeAccessIssue]
    lookup_list = table.LookupList
    if lookup_list is None:
        return anchors
    dropped = kern_only_lookups(table)
    kept = [
        (position, lookup) for position, lookup in enumerate(lookup_list.Lookup) if position not in dropped
    ]
    for index, (_position, lookup) in enumerate(kept):
        for subtable in lookup.SubTable:
            if lookup.LookupType == 9:
                subtable = subtable.ExtSubTable
            if getattr(subtable, "LookupType", lookup.LookupType) != 3:
                continue
            glyphs = subtable.Coverage.glyphs
            for name, record in zip(glyphs, subtable.EntryExitRecord):
                entry = record.EntryAnchor
                exit_anchor = record.ExitAnchor
                anchors.setdefault(name, []).append(
                    (
                        index,
                        None if entry is None else [entry.XCoordinate, entry.YCoordinate],
                        None if exit_anchor is None else [exit_anchor.XCoordinate, exit_anchor.YCoordinate],
                    )
                )
    return anchors


def glyph_facts(font, names: Iterable[str] | None = None) -> dict[str, tuple]:
    """Return, for each glyph `names` lists (every glyph of the font, sorted, when it is None), what decides how the compiled glyph renders apart from GSUB: its decomposed outline (`DecomposingRecordingPen`, so a change inside a subroutine still shows), its `hmtx` advance and left sidebearing, and its cursive-attachment records (`cursive_anchor_map`, None for a glyph none names), as one (outline, metrics, anchors) tuple. The review corpus's table-diff mode compares them between the accepted font and an edit's (`engine/review/diff_units.py`)."""
    from fontTools.pens.recordingPen import DecomposingRecordingPen

    glyph_set = font.getGlyphSet()
    metrics = font["hmtx"].metrics
    anchors = cursive_anchor_map(font)
    facts: dict[str, tuple] = {}
    for name in sorted(glyph_set.keys()) if names is None else names:
        pen = DecomposingRecordingPen(glyph_set)
        glyph_set[name].draw(pen)
        facts[name] = (tuple(pen.value), metrics.get(name), anchors.get(name))
    return facts


KERN_KEY = "kern"  # the rune file's kern block, which `rune_file_digest` leaves out and `rune_kern_digest` hashes alone
KERN_LINE_SUFFIX = "#kern"  # the suffix of a rune's kern line in the run_build skip key (`kern_lines`)

# Policy record kinds whose records carry an author `why`, which `rune_file_digest` drops.
POLICY_PROSE_KINDS = ("prefer", "extend", "contract", "pad_left", "resolve", "refuse")


def _without_prose(record: object, key: str) -> object:
    if isinstance(record, dict) and isinstance(record.get(key), str):
        return {**record, key: None}
    return record


def _without_why(record: object) -> object:
    return _without_prose(record, "why")


def _projected_stance(stance: object) -> object:
    if not isinstance(stance, dict):
        return stance
    surface = stance.get("surface")
    if not isinstance(surface, dict):
        return stance
    unlocks = surface.get("unlocks")
    if not isinstance(unlocks, list):
        return stance
    return {**stance, "surface": {**surface, "unlocks": [_without_why(unlock) for unlock in unlocks]}}


def _projected_rune(document: object, *, kern: bool = False) -> object:
    """Return the prose-insensitive view of a parsed rune document (the module docstring says what it drops). A value shaped in a way the schema rejects, such as a non-string prose value or a non-dict ductus, passes through unchanged, so the digest still moves and the load failure stays visible. The `kern` key is dropped whole unless `kern` is set, which keeps its records without their `why` (`rune_document_digest`)."""
    if not isinstance(document, dict):
        return document
    projected = {key: value for key, value in document.items() if kern or key != KERN_KEY}
    ductus = projected.get("ductus")
    if isinstance(ductus, dict):
        projected["ductus"] = {
            key: None if isinstance(value, str) else value for key, value in ductus.items()
        }
    if isinstance(projected.get("notes"), str):
        projected["notes"] = None
    policy = projected.get("policy")
    if isinstance(policy, dict):
        projected["policy"] = {
            kind: (
                [_without_why(record) for record in records]
                if kind in POLICY_PROSE_KINDS and isinstance(records, list)
                else records
            )
            for kind, records in policy.items()
        }
    stances = projected.get("stances")
    if isinstance(stances, dict):
        projected["stances"] = {name: _projected_stance(stance) for name, stance in stances.items()}
    records = projected.get(KERN_KEY)
    if isinstance(records, list):
        projected[KERN_KEY] = [_without_why(record) for record in records]
    return projected


def _load_yaml(text: str) -> object:
    return yaml.load(text, Loader=_SAFE_LOADER)


def _load_yaml_stream(text: str) -> object:
    return list(yaml.load_all(text, Loader=_SAFE_LOADER))


def _projected_digest(
    path: Path, project: Callable[[object], object], load: Callable[[str], object] = _load_yaml
) -> str:
    """Return the SHA-256 of a file's parsed content after `project`, so comments, formatting, and whatever `project` drops don't affect it. `load` parses the text, one YAML document by default. A file that fails to parse or serialize digests to its raw bytes, so a malformed file still changes the digest and two broken drafts don't share a value."""
    raw = path.read_bytes()
    try:
        payload = json.dumps(project(load(raw.decode())), ensure_ascii=False).encode()
    except yaml.YAMLError, UnicodeDecodeError, TypeError, ValueError:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(payload).hexdigest()


def rune_file_digest(path: Path) -> str:
    """One rune file's prose-insensitive content digest without its `kern` block (the module docstring holds the contract for what the projection drops)."""
    return _projected_digest(path, _projected_rune)


def _projected_rune_with_kern(document: object) -> object:
    return _projected_rune(document, kern=True)


def rune_document_digest(path: Path) -> str:
    """Return `rune_file_digest`'s projection with the rune's `kern` records kept, less their `why`: the whole document a reader of the file sees, which the engine suite's input closure hashes (`artifact_cycle._closure_digest`), since a test may read any part of a rune file."""
    return _projected_digest(path, _projected_rune_with_kern)


def _projected_kern(document: object) -> object:
    if not isinstance(document, dict):
        return document
    records = document.get(KERN_KEY)
    if isinstance(records, list):
        return [_without_why(record) for record in records]
    return records


def rune_kern_digest(path: Path) -> str:
    """Return the digest of a rune file's `kern` block alone, its records less their `why`: the one part of a rune file `rune_file_digest` leaves out, which `kern_lines` puts in the run_build skip key."""
    return _projected_digest(path, _projected_kern)


def kern_lines(repo_root: Path) -> list[str]:
    """Return one `<rune label>#kern\t<rune_kern_digest>` line per rune file, sorted. They are the run_build skip key's kern lines (`artifact_cycle.run_build_skip_lines`), the one place a rune's kern block reaches a key: a kern edit moves only its rune's line, which the cycle reads as a kern-only rebuild (`artifact_cycle.kern_only_rebuild`)."""
    root = Path(repo_root)
    return sorted(
        f"{_label(root, path)}{KERN_LINE_SUFFIX}\t{rune_kern_digest(path)}"
        for path in rune_paths(root)
        if path.is_file()
    )


CONTACT_ALLOW_LABEL = "engine/contact-allow.yaml"


def _projected_allow_list(document: object) -> object:
    """Return the parsed contact allow-list with each entry's `why` set to None; the signature stays. A document `defects.run_gates` would reject passes through unchanged, so its load failure stays visible in the digest."""
    if not isinstance(document, list):
        return document
    return [_without_why(entry) for entry in document]


def contact_allow_digest(path: Path) -> str:
    """Return the contact allow-list's prose-insensitive digest. Only the artifact cycle's run_build skip key uses it, under `CONTACT_ALLOW_LABEL`. Adding or changing a signature moves it; rewording a `why` does not."""
    return _projected_digest(path, _projected_allow_list)


SCHEMA_ANNOTATIONS = ("title", "description")
_SUBSCHEMA_KEYWORDS = frozenset({"items", "additionalProperties", "propertyNames", "not", "if", "then"})
_SUBSCHEMA_LIST_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf"})
_SUBSCHEMA_MAP_KEYWORDS = frozenset({"properties", "patternProperties", "$defs"})


def _projected_schema(schema: object) -> object:
    """Return a parsed JSON Schema with every string-valued `title` and `description` keyword removed, key order kept. It descends only through the keywords whose values are schemas: one schema, a list of them, or a map from a name to one. So the names under `properties` stay, a property named `description` among them, and a `const`, `enum`, or `required` value is kept whole. A keyword it does not descend into keeps its annotations, which can only make the digest move more often."""
    if not isinstance(schema, dict):
        return schema
    projected: dict[object, object] = {}
    for keyword, value in schema.items():
        if keyword in SCHEMA_ANNOTATIONS and isinstance(value, str):
            continue
        if keyword in _SUBSCHEMA_KEYWORDS:
            value = _projected_schema(value)
        elif keyword in _SUBSCHEMA_LIST_KEYWORDS and isinstance(value, list):
            value = [_projected_schema(item) for item in value]
        elif keyword in _SUBSCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            value = {name: _projected_schema(item) for name, item in value.items()}
        projected[keyword] = value
    return projected


def schema_file_digest(path: Path) -> str:
    """Return a schema's digest without its `title` and `description` annotations (`_projected_schema`). `spec_load._SchemaValidator._ANNOTATIONS` declares both as annotations, and its standard validator's `VALIDATORS` dispatch does not evaluate them. engine/test_fingerprint.py pins this authority without importing the loader into this module's code closure."""
    return _projected_digest(path, _projected_schema, json.loads)


def yaml_data_digest(path: Path) -> str:
    """Return the digest of a YAML file's parsed documents in order, key order kept, so only comments and formatting leave it. Each file is parsed as a stream, so a file of several documents hashes each in order."""
    return _projected_digest(path, lambda documents: documents, _load_yaml_stream)


def _data_digest(root: Path, path: Path, runes: set[Path]) -> str:
    """Return one data input's digest for `data_path_lines`: the prose-insensitive, kern-blind digest for rune files (`rune_file_digest`), `schema_file_digest` for a schema, and `yaml_data_digest` for the other YAML inputs."""
    if path in runes:
        return rune_file_digest(path)
    if path.suffix == ".json":
        return schema_file_digest(path)
    if path.suffix == ".yaml":
        return yaml_data_digest(path)
    return file_sha256(path)


def data_path_lines(repo_root: Path, paths: Iterable[Path]) -> list[str]:
    """Return the sorted `label\\tdigest` lines for `paths`, one per existing file, each hashed as `_data_digest` says. `data_lines` passes every data input; the settle memo's stamp passes the subset it covers (`settle_memo_keys.stamped_data_paths`)."""
    root = Path(repo_root)
    runes = set(rune_paths(root))
    return sorted(
        f"{_label(root, path)}\t{_data_digest(root, path, runes)}" for path in paths if path.is_file()
    )


def data_lines(repo_root: Path) -> list[str]:
    """Return the sorted `label\\tdigest` lines behind the `data` component, one per existing data input (`data_path_lines`). Exposed for the same reason as `path_lines`."""
    root = Path(repo_root)
    return data_path_lines(root, data_paths(root))


def data_value(repo_root: Path) -> str:
    """Return the `data` component: the hash of `data_lines`."""
    return hashlib.sha256("\n".join(data_lines(repo_root)).encode()).hexdigest()


def rune_digests(repo_root: Path) -> dict[str, str]:
    """Return every rune file's `rune_file_digest`, keyed by family name (the file stem, which spec_load checks against the `rune:` field). The settle memo invalidates per family on these (`settle_memo_keys.settle_family_keys`)."""
    return {path.stem: rune_file_digest(path) for path in rune_paths(Path(repo_root)) if path.is_file()}


def table_code_paths(repo_root: Path) -> list[Path]:
    """Return `pipeline_code_paths` without `COMPARISON_CODE_MODULES`: the code half of `tables_value`. Everything that can change a table or the font stays, including the crate, the spec loader, the emitters, the compiler, and the gates that run inside the build. A module leaves only when `COMPARISON_CODE_MODULES` names it, and engine/test_build_code_closure.py checks that the build never reaches one."""
    root = Path(repo_root)
    comparison = {root / relative for relative in COMPARISON_CODE_MODULES}
    return [path for path in pipeline_code_paths(root) if path not in comparison]


def tables_value(repo_root: Path) -> str:
    """Return the content key over everything the decision-table fixpoint and the font compile read: `data_value` and the hash of `table_code_paths`. A serialized window enumeration carries it, and the conformance sweep stops with an error when it no longer matches. It is narrower than the Stage A record: the code `COMPARISON_CODE_MODULES` names feeds no table, so editing it keeps the enumeration. The contact allow-list is in neither, and a rune's kern block is in no data line at all (`rune_file_digest` leaves it out): the kernel's spec dump leaves it out (`model.KERNEL_FIELD`), and only `engine/pipeline/kern.py`, which runs after the tables exist, reads it."""
    root = Path(repo_root)
    lines = (
        f"table_data\t{data_value(root)}",
        f"pipeline_code\t{hash_paths(root, table_code_paths(root))}",
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def stage_a(repo_root: Path) -> dict:
    """Return the Stage A components over the sources under `repo_root`, and, while the alphabet switch is set, its token under `spec_load.ALPHABET_LABEL` (`spec_load.alphabet_token`), so a record a small-alphabet run wrote never matches a whole-alphabet one, nor the reverse. With the switch unset the record holds the two components alone."""
    root = Path(repo_root)
    values = {
        "data": data_value(root),
        "pipeline_code": hash_paths(root, pipeline_code_paths(root)),
    }
    alphabet = spec_load.alphabet_token()
    if alphabet is not None:
        values[spec_load.ALPHABET_LABEL] = alphabet
    return values


def font_inputs_value(repo_root: Path) -> str:
    """Return the digest of what reaches the Regular font without moving the tables' stamp: the non-Quikscript font data (`font_data_lines`) and the runes' `kern` blocks (`kern_lines`), the two kinds of line a kern-only rebuild answers for."""
    root = Path(repo_root)
    return digest_lines(font_data_lines(root) + kern_lines(root))


def table_diff_values(repo_root: Path, snapshot_record: Path | None, tables: str | None) -> dict:
    """Return the table-diff corpus's inputs fingerprint (`TABLE_DIFF_COMPONENTS`): `snapshot`, the sha256 of the record of the snapshot it compares against (`engine/review/snapshot.py`), None with no snapshot; `tables`, the tables' stamp, which the build copies from the windows heads of the build it compares and the readiness check recomputes from the sources (`run_build.tables_inputs`); `font_inputs` (`font_inputs_value`); `review_code`, the review modules the build runs (`review_code_paths`); and `static`, the app's files (`static_paths`)."""
    root = Path(repo_root)
    record = Path(snapshot_record) if snapshot_record is not None else None
    return {
        "snapshot": file_sha256(record) if record is not None and record.is_file() else None,
        "tables": tables,
        "font_inputs": font_inputs_value(root),
        "review_code": hash_paths(root, review_code_paths(root)),
        "static": hash_paths(root, static_paths(root)),
    }


def write_stage_a(out_dir: Path, values: Mapping[str, str]) -> dict:
    """Write `values`, which `stage_a` returned, as the Stage A record under `out_dir`, and return the record. It takes the values instead of computing them, so a run writes the ones it captured before it read its inputs; `run_build_gates._settle_stage_a` says when a run writes them."""
    record = {"format": FORMAT, **values}
    (Path(out_dir) / STAGE_A_FILENAME).write_text(json.dumps(record, indent=2) + "\n")
    return record


def read_stage_a(out_dir: Path) -> dict | None:
    """Return the Stage A record under `out_dir` as `stage_a` returns its values, the alphabet token included when the record holds one, or None when it is missing, unreadable or incomplete."""
    try:
        record = json.loads((Path(out_dir) / STAGE_A_FILENAME).read_text())
    except OSError, ValueError:
        return None
    if not isinstance(record, dict):
        return None
    keys = STAGE_A_COMPONENTS + ((spec_load.ALPHABET_LABEL,) if spec_load.ALPHABET_LABEL in record else ())
    values = {key: record.get(key) for key in keys}
    if not all(isinstance(value, str) for value in values.values()):
        return None
    return values

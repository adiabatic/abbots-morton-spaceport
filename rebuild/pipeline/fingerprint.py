"""Content fingerprints of the build inputs, split into named components so the readiness checker can say which component is stale and what rebuilds it.

Each component is a SHA-256 over sorted lines that hold no mtimes, so two builds of the same inputs produce the same value. Most lines are `label\\tdigest` pairs over file contents. The baseline TSVs are the exception: they are large, and `rebuild/out/digests.tsv` already holds their content digests, so `baselines_value` hashes their sizes and that file. The surface manifest's `generated_at` is mtime-based and identifies a surface build, so that exported verdicts and sidecars can be matched to it. It cannot tell whether the surface matches the sources on disk; these fingerprints can.

run_m1 writes the Stage A components (`data`, `baselines`, `pipeline_code`) to `rebuild/out/m1/inputs_fingerprint.json` when it builds. The review build copies those recorded values into the manifest instead of recomputing them, so a surface rebuilt over stale `rebuild/out/m1` artifacts carries the stale values and the checker flags it. The review build computes the Stage B components (`review_code`, `static`, `fonts`, `explain_prose`) itself.

`tables_value` is the source stamp a serialized window enumeration carries, so the conformance sweep can tell whether the tables on disk were built from the sources on disk. It covers only what the table fixpoint and the font compile read. Its data half, `table_data_value`, leaves out `NON_TABLE_DATA_LABELS` (the alias map, the divergence ledger, and the kern sidecar). Its code half, `table_code_paths`, leaves out `COMPARISON_CODE_MODULES` (oracle.py and oracle_positions.py). Those inputs are read only by gates that run against tables and a font already built, so editing one re-runs those gates over the enumeration on disk instead of discarding it. rebuild/test_build_code_closure.py checks that the build never imports the comparison modules.

The three human-reviewed ledgers have prose-blind digests, like the rune files below:

- The contact allow-list is in no component, not even `data`. Only the defect gate reads it, so its digest (`contact_allow_digest`, under `CONTACT_ALLOW_LABEL`) is only in the artifact cycle's run_m1 skip key. Changing a signature re-runs that gate without restamping the surface or dropping the review unit cache. Each entry's `why` is left out of the digest.
- The divergence ledger is in `data` and left out of `tables_value`. `divergence_ledger_digest` hashes every field except each class's `why`. The audit, the classifier, and the census read those fields, so changing one moves `data` and the run_m1 skip key, and the cycle re-runs the comparison over the tables and font on disk. The review build copies each class's `why` into the manifest's `classes[].why`, which `check_manifest` requires, so the `why` is hashed into the Stage B `explain_prose` component through `ledger_prose_lines`. Rewording a class's `why` costs a surface rebuild served from the unit cache and nothing else.
- The standing approvals are in no component. `standing_approvals_digest` leaves out each rule's `note` and is used only by the artifact cycle's rebuild-lane closure. The plumbing key (`artifact_cycle.plumbing_skip_fingerprint`) hashes the file's raw bytes instead, because `standing_verdicts` copies a rule's `note` into every verdict it fills.

`rune_file_digest` hashes a rune's parsed document instead of its bytes, with the prose removed: YAML comments and formatting, the ductus text, `notes`, and every `why`, refuse records' included. No build step reads any of these (the refuse `why` has one reader, described next), so editing them should not make the surface stale or re-run a cycle. The digest keeps every geometric and policy field, the ductus keys (motion names, which the lints check), and the presence of every prose field, because the schema requires `why` on some records.

The crate appends a refuse record's `why` to that refusal's elimination message when it builds an explain ladder. The table fixpoint never requests a ladder; only the review surface's explain panel shows ladders. So the refuse `why` is hashed by `rune_explain_digest`, which the review unit cache's family keys are built from, and by the Stage B `explain_prose` component. Rewording one re-enriches the windows whose explain text quotes it and restamps the surface. No key built from `rune_file_digest` or `rune_digests` sees it.

`code_file_digest` projects code files the same way. A `.py` file is hashed as its syntax tree with every docstring's text set to None, and a `.rs` file with its whole-line `//` comments removed, so rewording either moves no key built by `path_lines` or `hash_paths`. Everything the interpreter or compiler sees stays in: every identifier, every non-docstring string constant (matchers compare against error text), every annotation, default, and decorator, and every Rust code line including its trailing comment. The presence of each docstring stays too. A file that fails to parse or decode is hashed as raw bytes, as `_projected_digest` does, so the failure stays visible. Every other file type that reaches `path_lines`, such as the app's static files and the crate's manifest and lock, is hashed raw by `file_sha256`. `baseline_subset.stamp_key` also uses `code_file_digest`.

Three closures outside this module hash code files raw. The rebuild-lane closure (`artifact_cycle._closure_digest`) and gate:make-test's closure (`artifact_cycle.make_test_closure_fingerprint`) do so because test fixtures and the closure tests read source text, so gate:rebuild-contracts and gate:make-test still run after a prose-only edit. The pyright gate's closure (`pyright_gate.closure_fingerprint`) does so because a `# pyright: ignore` comment changes pyright's result. rebuild/test_fingerprint.py checks what the projection keeps and drops.

The two files a version bump rewrites have projections too, so a bump moves no key. `make all` writes the new version into both site fonts' `name` table and `head.fontRevision`, and the bump-minor skill refreshes `uv.lock`, where only the project's own `[[package]]` block changes.

- `font_content_digest` hashes a font table by table from the bytes the `sfnt` reader returns: the sorted table tags, then each table's length and digest, leaving out `head` and `name` (`FONT_VERSION_TABLES`). `head` holds the revision, the checksum adjustment, and the modification time, and `name` holds the version strings. A shaper reads neither to position a glyph. `head` also holds `unitsPerEm`, but an edit to `units_per_em` in glyph_data/metadata.yaml still reaches the digest through `CFF `: tools/build_font.py passes `FontBuilder.setupCFF` no FontMatrix, so fontTools writes the matrix as 1/unitsPerEm. rebuild/test_fingerprint.py checks that fontTools default. Only a hand edit to `head.unitsPerEm` alone would go unseen, and nothing in the tree makes one. Every table that can affect a shaped run stays (`CFF `, `hmtx`, `GPOS`, `GSUB`, `cmap`), and so does the list of tables. A file fontTools cannot open, or whose table it cannot read, is hashed as raw bytes.
- `lock_digest` (in `rebuild.tools.lock_digest`, a separate module because the pyright gate also hashes the lock and must not import rebuild.pipeline) removes the project's own block from the lock and hashes the rest, so a changed dependency pin or an added or removed package still moves it.

`fonts_value` is `hash_paths` with `font_content_digest` per font. It is the Stage B `fonts` component and the contracts lane's `fonts` label.
"""

from __future__ import annotations

import ast
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

import yaml

from rebuild.tools.lock_digest import lock_digest
from rebuild.tools.site_fonts import font_paths

FORMAT = "ams-inputs-fingerprint/2"
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
STAGE_A_COMPONENTS = ("data", "baselines", "pipeline_code")
STAGE_B_COMPONENTS = ("review_code", "static", "fonts", "explain_prose")
COMPONENTS = STAGE_A_COMPONENTS + STAGE_B_COMPONENTS
STAGE_A_FILENAME = "inputs_fingerprint.json"


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes, streamed so the file is never held in memory whole. rebuild/test_fingerprint.py checks that no rebuild module hashes a file it read whole, and that the inline copies in modules that cannot import this one return the same value. Each caller handles a missing file itself."""
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def rune_paths(repo_root: Path) -> list[Path]:
    return sorted((Path(repo_root) / "glyph_data" / "runes").glob("*.yaml"))


def data_paths(repo_root: Path) -> list[Path]:
    root = Path(repo_root)
    paths = rune_paths(root)
    paths += sorted((root / "rebuild" / "schema").glob("*.json"))
    paths += [
        root / "rebuild" / "script.yaml",
        root / "glyph_data" / "punctuation.yaml",
        root / "rebuild" / "m1-aliases.yaml",
        root / "rebuild" / "m1-divergences.yaml",
        root / "glyph_data" / "senior_quikscript_kerning.yaml",
    ]
    return paths


# Pipeline modules that only run against tables and a font already built: the oracle's driver and classifier (oracle.py) and its position channel (oracle_positions.py, which the oracle row cache also stamps on its own as `oracle_cache.POSITION_CODE_PATHS`). They stay in `pipeline_code_paths`, and `table_code_paths` leaves them out, so an edit to one re-runs the gates over the tables on disk instead of forcing a full build. A module of this kind that is missing from the set is hashed into the tables' stamp, so every edit to it forces a full build. rebuild/test_build_code_closure.py fails if the build imports any module listed here.
COMPARISON_CODE_MODULES = frozenset({"oracle.py", "oracle_positions.py"})


FONT_COMPILE_TOOL_MODULES = frozenset(
    {
        "build_font.py",
        "departure_mono_import.py",
        "glyph_compiler.py",
        "quikscript_fea.py",
        "quikscript_ir.py",
        "quikscript_join_analysis.py",
    }
)


def font_compile_tool_paths(repo_root: Path) -> list[Path]:
    """Return the tools/ modules the M1 font compile runs: the import closure of tools/build_font.py within tools/, which rebuild/pipeline/compile_font.py calls to build the mini font. compile_font puts tools/ on sys.path, so these modules import each other by bare name. Most of tools/ is authoring and audit scripts that no build runs, so this is a fixed list instead of the whole directory, and rebuild/test_build_code_closure.py checks that it equals the walked import closure. Paths are returned whether or not they exist; `hash_paths` skips missing files."""
    return sorted(Path(repo_root) / "tools" / name for name in FONT_COMPILE_TOOL_MODULES)


def pipeline_code_paths(repo_root: Path) -> list[Path]:
    """Return the code the `pipeline_code` component hashes: rebuild/pipeline, rebuild/validation, the kernel crate's manifest, lock, and sources, and the font compile's tools/ modules (`font_compile_tool_paths`). rebuild/validation holds the shaper, row model, seam classifier, and Manual-pin replays, which are the before side of the M1 comparison. Whole trees are hashed instead of a list of imported modules, because such a list goes stale when an import or a Rust module is added, and over-invalidating is the safe error.

    The tools/ modules are included because compile_font passes the mini font to tools/build_font.py, so an edit to the glyph compiler, the IR, the FEA emitter, or the join analysis changes M1.otf. They are build-side, so `table_code_paths` keeps them as well.
    """
    root = Path(repo_root)
    kernel = root / "rebuild" / "kernel-rs"
    return (
        sorted((root / "rebuild" / "pipeline").glob("*.py"))
        + sorted((root / "rebuild" / "validation").glob("*.py"))
        + [kernel / "Cargo.toml", kernel / "Cargo.lock"]
        + sorted((kernel / "src").rglob("*.rs"))
        + font_compile_tool_paths(root)
    )


REVIEW_NON_BUILD_MODULES = frozenset({"serve.py", "verdict_store.py", "status.py", "journal.py", "export.py"})


def review_code_paths(repo_root: Path) -> list[Path]:
    """Return rebuild/review/*.py without `REVIEW_NON_BUILD_MODULES`, the modules the surface build never imports. Hashing one of those would force a full surface rebuild and drop the per-unit store for an edit the build cannot execute. serve.py is the dev server and verdict_store.py the store it keeps; status.py and journal.py belong to the verdict plumbing, and `artifact_cycle.plumbing_skip_fingerprint` hashes all four itself. export.py is a standalone CLI that turns exported verdicts into a triage YAML. rebuild/test_review_code_closure.py checks this set against build.py's import graph in both directions."""
    return sorted(
        path
        for path in (Path(repo_root) / "rebuild" / "review").glob("*.py")
        if path.name not in REVIEW_NON_BUILD_MODULES
    )


def static_paths(repo_root: Path) -> list[Path]:
    return sorted(
        path for path in (Path(repo_root) / "rebuild" / "review" / "static").rglob("*") if path.is_file()
    )


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
    """Return a prose-blind digest for a `.py` or `.rs` file (the module docstring says what the projection drops), and `file_sha256` for any other suffix. The suffix is checked before the file is opened, so a font or a baseline reaching `path_lines` is streamed and never read whole. A file that fails to parse or decode digests to its raw bytes, so two broken drafts don't share a value.

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


def _projected_font_lines(path: Path) -> list[str] | None:
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
        lines = _projected_font_lines(path)
        digest = raw_digest if lines is None else digest_lines(lines)
        _FONT_DIGESTS[raw_digest] = digest
    return digest


def fonts_value(repo_root: Path, paths: list[Path]) -> str:
    """Return `hash_paths` over the given fonts with `font_content_digest` in place of the raw digest, so the `make all` of a version bump leaves the Stage B `fonts` component and the contracts lane's `fonts` label unchanged."""
    return digest_lines(
        sorted(f"{_label(repo_root, path)}\t{font_content_digest(path)}" for path in paths if path.is_file())
    )


def _labels_of(lines: Iterable[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for line in lines:
        label, _, digest = line.partition("\t")
        labels[label] = digest
    return labels


@dataclass(frozen=True)
class EnvironmentStamp:
    """A whole-store stamp kept as `label\\tdigest` lines, so a store that records them can name the input that changed on a miss. `value` is the digest of `lines` alone. `detail` maps a label to the lines behind that label's digest (for a code closure, its `path_lines`), so a miss can name the file inside the closure. `detail` is not part of `value` or `labels`, so adding it leaves both unchanged.

    The class lives here because the review surface's stores and the oracle row cache both import this module and neither may import the other. `rebuild/pipeline/oracle_cache.py` is outside `unit_cache.surface_code_paths`, so importing it from the surface build would run code the surface's stamp does not hash (rebuild/test_review_code_closure.py checks that list), and the pipeline never imports `rebuild/review/`.
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

    def detail_labels(self, label: str) -> dict[str, str]:
        """One label's expansion as a `label -> digest` map, for the sub-diff `moved_note` renders through `expand`; empty for a label carrying no detail."""
        for name, lines in self.detail:
            if name == label:
                return _labels_of(lines)
        return {}


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
    """Return, per glyph, one (lookup index, entry, exit) triple for each GPOS cursive-attachment record that names it, with anchors as [x, y] or None. Outside its charstring and advance, GPOS is the only table that changes how a compiled glyph renders, so the per-glyph digest includes it."""
    anchors: dict[str, list] = {}
    if "GPOS" not in font:
        return anchors
    lookup_list = font["GPOS"].table.LookupList  # pyright: ignore[reportAttributeAccessIssue]
    if lookup_list is None:
        return anchors
    for index, lookup in enumerate(lookup_list.Lookup):
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


def after_font_glyph_digests(after_font: Path) -> tuple[dict[str, str], str]:
    """Return a digest per qs family over the after font's glyphs whose name stem is that family, and one digest over the rest of the font that a shaped run can depend on, apart from GSUB. A glyph's digest covers its decomposed outline (so a change inside a subroutine still shows), its advance and sidebearing, and its cursive anchors. The second digest covers the non-qs glyphs (boundary and marker helpers), the cmap, and the GPOS feature-to-lookup wiring. The function lives here because `rebuild/review/` imports `rebuild/pipeline/` and never the reverse.

    The GSUB wiring is left out. A rune edit changes the GSUB lookup list on nearly every cycle, so including it would drop every cache keyed on this digest after each rune edit. A window's glyph selection is covered by two other things. The caches' keys include the settled cells the window resolves to (the audit row's `new` column for a unit, the per-family rune keys for an oracle row). And `gate:conform` shapes the compiled font through HarfBuzz every cycle and checks that its selection matches settlement. So while conform passes, the glyph the font selects for a window is a function of the window's settled cells, which cannot change without the key changing. Every glyph a window can reach, in every variant of every family, is in the per-family digests. GPOS stays in because it can move a run without changing a glyph name or a cell.
    """
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.ttLib import TTFont

    font = TTFont(str(after_font))
    glyph_set = font.getGlyphSet()
    metrics = font["hmtx"].metrics  # pyright: ignore[reportAttributeAccessIssue]
    anchors = cursive_anchor_map(font)
    per_glyph: dict[str, str] = {}
    for name in sorted(glyph_set.keys()):
        pen = DecomposingRecordingPen(glyph_set)
        glyph_set[name].draw(pen)
        payload = repr((name, tuple(pen.value), metrics.get(name), anchors.get(name)))
        per_glyph[name] = hashlib.sha256(payload.encode()).hexdigest()

    families: dict[str, list[str]] = {}
    helper_lines: list[str] = []
    for name in sorted(per_glyph):
        stem = name.split(".")[0]
        if stem.startswith("qs"):
            families.setdefault(stem, []).append(f"{name}\t{per_glyph[name]}")
        else:
            helper_lines.append(f"{name}\t{per_glyph[name]}")

    family_digests = {
        stem: hashlib.sha256("\n".join(lines).encode()).hexdigest() for stem, lines in families.items()
    }

    wiring: list = []
    for tag in ("GPOS",):
        if tag not in font:
            continue
        table = font[tag].table  # pyright: ignore[reportAttributeAccessIssue]
        features = [
            (record.FeatureTag, list(record.Feature.LookupListIndex))
            for record in (table.FeatureList.FeatureRecord if table.FeatureList else ())
        ]
        types = [lookup.LookupType for lookup in (table.LookupList.Lookup if table.LookupList else ())]
        wiring.append((tag, features, types))
    helper_lines.append(
        "cmap\t" + hashlib.sha256(repr(sorted((font.getBestCmap() or {}).items())).encode()).hexdigest()
    )
    helper_lines.append("layout\t" + hashlib.sha256(repr(wiring).encode()).hexdigest())
    helpers = hashlib.sha256("\n".join(helper_lines).encode()).hexdigest()
    return family_digests, helpers


# Policy record kinds whose records carry an author `why`, and the kinds whose `why` something downstream reads. `rune_explain_digest` keeps the `why` of the second group and `rune_file_digest` drops it; that is the only difference between them.
POLICY_PROSE_KINDS = ("prefer", "extend", "contract", "resolve", "refuse")
QUOTED_POLICY_PROSE_KINDS = ("refuse",)


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


def _projected_rune(document: object, *, quoted_prose: bool = False) -> object:
    """Return the prose-blind view of a parsed rune document (the module docstring says what it drops). A value shaped in a way the schema rejects, such as a non-string prose value or a non-dict ductus, passes through unchanged, so the digest still moves and the load failure stays visible. With `quoted_prose`, the `why` of refuse records is kept."""
    if not isinstance(document, dict):
        return document
    projected = dict(document)
    ductus = projected.get("ductus")
    if isinstance(ductus, dict):
        projected["ductus"] = {
            key: None if isinstance(value, str) else value for key, value in ductus.items()
        }
    if isinstance(projected.get("notes"), str):
        projected["notes"] = None
    policy = projected.get("policy")
    if isinstance(policy, dict):
        kept = QUOTED_POLICY_PROSE_KINDS if quoted_prose else ()
        projected["policy"] = {
            kind: (
                [_without_why(record) for record in records]
                if kind in POLICY_PROSE_KINDS and kind not in kept and isinstance(records, list)
                else records
            )
            for kind, records in policy.items()
        }
    stances = projected.get("stances")
    if isinstance(stances, dict):
        projected["stances"] = {name: _projected_stance(stance) for name, stance in stances.items()}
    return projected


def _projected_digest(path: Path, project: Callable[[object], object]) -> str:
    """Return the SHA-256 of a YAML file's parsed content after `project`, so comments, formatting, and whatever `project` drops don't affect it. A file that fails to parse or serialize digests to its raw bytes, so a malformed file still changes the digest and two broken drafts don't share a value."""
    raw = path.read_bytes()
    try:
        payload = json.dumps(project(yaml.load(raw.decode(), Loader=_SAFE_LOADER)), ensure_ascii=False)
    except yaml.YAMLError, UnicodeDecodeError, TypeError, ValueError:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(payload.encode()).hexdigest()


def rune_file_digest(path: Path) -> str:
    """One rune file's prose-blind content digest (the module docstring holds the contract for what the projection drops)."""
    return _projected_digest(path, _projected_rune)


def _projected_rune_keeping_quoted_prose(document: object) -> object:
    return _projected_rune(document, quoted_prose=True)


def rune_explain_digest(path: Path) -> str:
    """Return `rune_file_digest`'s projection with `policy.refuse[].why` kept. The crate appends that text to a refusal's elimination message in an explain ladder, which the review surface shows as explain text. No table or font build asks for a ladder, so only the review side uses this digest: rewording a refusal invalidates the windows that quote it and nothing keyed on `rune_file_digest`."""
    return _projected_digest(path, _projected_rune_keeping_quoted_prose)


CONTACT_ALLOW_LABEL = "rebuild/m1-contact-allow.yaml"


def _projected_allow_list(document: object) -> object:
    """Return the parsed contact allow-list with each entry's `why` set to None; the signature stays. A document `defects.run_gates` would reject passes through unchanged, so its load failure stays visible in the digest."""
    if not isinstance(document, list):
        return document
    return [_without_why(entry) for entry in document]


def contact_allow_digest(path: Path) -> str:
    """Return the contact allow-list's prose-blind digest. Only the artifact cycle's run_m1 skip key uses it, under `CONTACT_ALLOW_LABEL`. Adding or changing a signature moves it; rewording a `why` does not."""
    return _projected_digest(path, _projected_allow_list)


DIVERGENCE_LEDGER_LABEL = "rebuild/m1-divergences.yaml"
STANDING_APPROVALS_LABEL = "rebuild/standing-approvals.yaml"


def _projected_ledger(document: object) -> object:
    """Return the parsed divergence ledger with each entry's `why` set to None. The other fields (`id`, `status`, `match`, `no_verdict`, `ink_identical`, `count`, `exemplars`) stay, because `audit.load_ledger`, `oracle.classify_divergence`, and the census read them. A document `audit.load_ledger` would reject passes through unchanged, so its load failure stays visible in the digest."""
    if not isinstance(document, list):
        return document
    return [_without_why(entry) for entry in document]


def divergence_ledger_digest(path: Path) -> str:
    """Return the divergence ledger's prose-blind digest, which `data_lines` uses. Changing a class's fields moves it. Rewording a class's `why` does not, because `ledger_prose_lines` hashes the `why` into `explain_prose` instead."""
    return _projected_digest(path, _projected_ledger)


def _projected_standing_rules(document: object) -> object:
    """Return the parsed standing approvals with each rule's `note` set to None; every other field stays. A document `standing_verdicts.load_rules` would reject passes through unchanged, so its load failure stays visible in the digest."""
    if not isinstance(document, dict):
        return document
    rules = document.get("rules")
    if not isinstance(rules, list):
        return document
    return {**document, "rules": [_without_prose(rule, "note") for rule in rules]}


def standing_approvals_digest(path: Path) -> str:
    """Return the standing approvals' digest without each rule's `note`. No component here includes it; only the artifact cycle's rebuild-lane closure uses it. The plumbing key hashes the file raw instead, because the standing fill copies each `note` into the verdicts it writes."""
    return _projected_digest(path, _projected_standing_rules)


def _data_digest(root: Path, path: Path, runes: set[Path]) -> str:
    """Return one data input's digest for `data_lines`: the prose-blind digest for rune files and the divergence ledger, and the raw bytes for everything else."""
    if path in runes:
        return rune_file_digest(path)
    if _label(root, path) == DIVERGENCE_LEDGER_LABEL:
        return divergence_ledger_digest(path)
    return file_sha256(path)


def data_lines(repo_root: Path) -> list[str]:
    """Return the sorted `label\\tdigest` lines behind the `data` component, one per existing data input, each hashed as `_data_digest` says. Exposed for the same reason as `path_lines`."""
    root = Path(repo_root)
    runes = set(rune_paths(root))
    return sorted(
        f"{_label(root, path)}\t{_data_digest(root, path, runes)}"
        for path in data_paths(root)
        if path.is_file()
    )


def data_value(repo_root: Path) -> str:
    """Return the `data` component: the hash of `data_lines`."""
    return hashlib.sha256("\n".join(data_lines(repo_root)).encode()).hexdigest()


NON_TABLE_DATA_LABELS = (
    "glyph_data/senior_quikscript_kerning.yaml",
    "rebuild/m1-aliases.yaml",
    DIVERGENCE_LEDGER_LABEL,
)


def table_data_lines(repo_root: Path) -> list[str]:
    """Return `data_lines` without `NON_TABLE_DATA_LABELS`, the data inputs no table stage reads. The oracle reads the alias map and the divergence ledger to name and classify divergences after the fixpoint has decided them. Only `oracle_positions.KernEvaluator` reads the kern sidecar, to add its kerns back before a position comparison; the font compile passes the builder an empty kerning map and never opens the file. None of the three reaches the kernel crate or any stage that builds a decision table, so including them in the tables' stamp would discard enumerations that would be rebuilt byte for byte.

    All three stay in `data_lines`, so an edit to one still moves the `data` component and the run_m1 skip key. The cycle then re-runs the comparison over the tables and font on disk (`artifact_cycle.comparison_side_label`) instead of the fixpoint.
    """
    excluded = set(NON_TABLE_DATA_LABELS)
    return [line for line in data_lines(repo_root) if line.split("\t", 1)[0] not in excluded]


def table_data_value(repo_root: Path) -> str:
    """Return the hash of `table_data_lines`, the data half of `tables_value`."""
    return hashlib.sha256("\n".join(table_data_lines(repo_root)).encode()).hexdigest()


def rune_digests(repo_root: Path) -> dict[str, str]:
    """Return every rune file's `rune_file_digest`, keyed by family name (the file stem, which spec_load checks against the `rune:` field). The oracle row cache invalidates per family on these (`oracle_cache.family_keys`). The review unit cache uses `rune_explain_digests` instead, because the explain text it caches quotes refuse `why`s."""
    return {path.stem: rune_file_digest(path) for path in rune_paths(Path(repo_root)) if path.is_file()}


def rune_explain_digests(repo_root: Path) -> dict[str, str]:
    """Return every rune file's `rune_explain_digest`, keyed by family name. The review unit cache's family keys (`unit_cache.family_content_keys`) are built from these. A rune whose refusals have no `why` gets the same value as in `rune_digests`."""
    return {path.stem: rune_explain_digest(path) for path in rune_paths(Path(repo_root)) if path.is_file()}


def refuse_prose_lines(repo_root: Path) -> list[str]:
    """Return a sorted `family\\tindex\\twhy` line for each refuse record with a `why`. This is all the rune prose anything downstream reads, and one of the two inputs to `explain_prose` (`ledger_prose_lines` is the other). A rune that fails to parse or decode contributes `family\\t-\\t<raw digest>`, so a broken file changes the value instead of reading as a rune with no refusals."""
    lines: list[str] = []
    for path in rune_paths(Path(repo_root)):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            document = yaml.load(raw.decode(), Loader=_SAFE_LOADER)
        except yaml.YAMLError, UnicodeDecodeError, TypeError, ValueError:
            lines.append(f"{path.stem}\t-\t{hashlib.sha256(raw).hexdigest()}")
            continue
        policy = document.get("policy") if isinstance(document, dict) else None
        records = policy.get("refuse") if isinstance(policy, dict) else None
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            why = record.get("why") if isinstance(record, dict) else None
            if isinstance(why, str):
                lines.append(f"{path.stem}\t{index}\t{why}")
    return sorted(lines)


def ledger_prose_lines(repo_root: Path) -> list[str]:
    """Return a sorted `ledger\\t<id>\\t<why>` line for each divergence-ledger entry with a `why`: the other input to `explain_prose`. The ledger's `why` leaves the `data` digest and is hashed here so the surface cannot serve a stale one: the review build copies it into the manifest's `classes[].why`, which `check_manifest` requires. No shard or sidecar carries it, so rewording a class restamps the surface and re-enriches no unit. A ledger that fails to parse or decode, or is not the list `audit.load_ledger` expects, contributes `ledger\\t-\\t<raw digest>`."""
    path = Path(repo_root) / DIVERGENCE_LEDGER_LABEL
    if not path.is_file():
        return []
    raw = path.read_bytes()
    try:
        document = yaml.load(raw.decode(), Loader=_SAFE_LOADER)
    except yaml.YAMLError, UnicodeDecodeError:
        document = None
    if not isinstance(document, list):
        return [f"ledger\t-\t{hashlib.sha256(raw).hexdigest()}"]
    lines: list[str] = []
    for index, entry in enumerate(document):
        why = entry.get("why") if isinstance(entry, dict) else None
        if not isinstance(why, str):
            continue
        identifier = entry.get("id")
        lines.append(f"ledger\t{identifier if isinstance(identifier, str) else index}\t{why}")
    return sorted(lines)


def explain_prose_value(repo_root: Path) -> str:
    """Return the `explain_prose` component: the hash of `refuse_prose_lines` and `ledger_prose_lines` sorted together, so the manifest records whether the explain text and class rationales it serves match the wording on disk. The two kinds of line cannot collide, because a ledger line's first field is `ledger` and a refuse line's is a family name. It is a Stage B component because no stage of the M1 build reads this prose, so run_m1 has nothing to record, and a stale value means the surface needs rebuilding, not the tables."""
    root = Path(repo_root)
    lines = sorted(refuse_prose_lines(root) + ledger_prose_lines(root))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def table_code_paths(repo_root: Path) -> list[Path]:
    """Return `pipeline_code_paths` without `COMPARISON_CODE_MODULES`: the code half of `tables_value`. Everything that can change a table or the font stays, including the crate, the spec loader, the emitters, the compiler, and the gates that run inside the build. A module leaves only when `COMPARISON_CODE_MODULES` names it, and rebuild/test_build_code_closure.py checks that the build never imports one."""
    root = Path(repo_root)
    pipeline = root / "rebuild" / "pipeline"
    return [
        path
        for path in pipeline_code_paths(root)
        if not (path.parent == pipeline and path.name in COMPARISON_CODE_MODULES)
    ]


def tables_value(repo_root: Path) -> str:
    """Return the content key over everything the decision-table fixpoint and the font compile read: `table_data_value` and the hash of `table_code_paths`. A serialized window enumeration carries it, and the conformance sweep stops with an error when it no longer matches. It is narrower than the Stage A record: the baselines, the alias map, the divergence ledger, the kern sidecar, and the oracle's own code feed no table, so editing one of them keeps the enumeration. The contact allow-list is in neither."""
    root = Path(repo_root)
    lines = (
        f"table_data\t{table_data_value(root)}",
        f"pipeline_code\t{hash_paths(root, table_code_paths(root))}",
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def baselines_value(repo_root: Path) -> str:
    out = Path(repo_root) / "rebuild" / "out"
    lines = sorted(
        f"{_label(repo_root, path)}\t{path.stat().st_size}"
        for path in out.glob("baseline-*.tsv.gz")
        if path.is_file()
    )
    digests = out / "digests.tsv"
    payload = "\n".join(lines).encode() + b"\n" + (digests.read_bytes() if digests.is_file() else b"")
    return hashlib.sha256(payload).hexdigest()


def stage_a(repo_root: Path) -> dict:
    root = Path(repo_root)
    return {
        "data": data_value(root),
        "baselines": baselines_value(root),
        "pipeline_code": hash_paths(root, pipeline_code_paths(root)),
    }


def stage_b(repo_root: Path, before_font: Path, junior_font: Path, spec_root: Path | None = None) -> dict:
    """Return the review-side components. `explain_prose` is computed over `spec_root` when the build names one, because a workload bundled with its own frozen spec serves that spec's rationales; otherwise it is computed over the checkout. `fonts` is `fonts_value` over the two site fonts, so the `make all` of a version bump leaves it unchanged."""
    root = Path(repo_root)
    return {
        "review_code": hash_paths(root, review_code_paths(root)),
        "static": hash_paths(root, static_paths(root)),
        "fonts": fonts_value(root, [Path(before_font), Path(junior_font)]),
        "explain_prose": explain_prose_value(Path(spec_root) if spec_root is not None else root),
    }


def compute_all(repo_root: Path) -> dict:
    root = Path(repo_root)
    before_font, junior_font = font_paths(root)
    return {**stage_a(root), **stage_b(root, before_font, junior_font)}


def write_stage_a(repo_root: Path, out_dir: Path) -> dict:
    record = {"format": FORMAT, **stage_a(repo_root)}
    (Path(out_dir) / STAGE_A_FILENAME).write_text(json.dumps(record, indent=2) + "\n")
    return record


def read_stage_a(out_dir: Path) -> dict | None:
    try:
        record = json.loads((Path(out_dir) / STAGE_A_FILENAME).read_text())
    except OSError, ValueError:
        return None
    if not isinstance(record, dict):
        return None
    values = {key: record.get(key) for key in STAGE_A_COMPONENTS}
    if not all(isinstance(value, str) for value in values.values()):
        return None
    return values

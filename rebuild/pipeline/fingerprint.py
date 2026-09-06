"""Content fingerprints for the build inputs, keyed by component so the readiness checker can name the remedy when a component goes stale.

The surface manifest's `generated_at` stamp is mtime-based and exists to key unit-id joinability; it cannot answer "does this surface reflect the sources on disk right now". These fingerprints do: pure content hashes (plus stat sizes for the 400MB baseline TSVs, whose content digests already live in digests.tsv), sorted and mtime-free so consecutive builds of the same inputs stay byte-identical.

Chain honesty: run_m1 persists the Stage A components (`data`, `baselines`, `pipeline_code`) into rebuild/out/m1/inputs_fingerprint.json at build time, and the review build copies those recorded values into the manifest instead of recomputing them — so a surface rebuilt over stale out/m1 artifacts carries the stale hashes and the checker flags it.

`tables_value` serves the same honesty for a build artifact rather than a manifest: the serialized decision tables carry it, so the conformance sweep can tell a table its own sources produced from one it must rebuild. It is keyed on `table_data_value` rather than `data_value` — the alias map, the divergence ledger, and the kern sidecar are read by gates that consume a built table and by nothing that builds one, so they belong to the whole-run record and not to this stamp. Its code half is `table_code_paths` rather than `pipeline_code_paths` for the same reason: the oracle's own module (`COMPARISON_CODE_MODULES`) runs against tables and a font already built, so an edit to the classifier or the ledger match re-adjudicates over the enumeration on disk instead of throwing it away, and rebuild/test_build_code_closure.py is what proves the build never reaches it.

The three human-reviewed ledgers hash prose-blind as well, for the rune files' reason below: what a reviewer wrote down is documentation, and only what a stage or a gate reads may move a key. The contact allow-list is the narrowest of them — in no component here at all, `data` included. The defect gate is the only stage that reads it, so a two-line bless has no business dropping the review unit cache and re-stamping the whole surface — which is what its old place in `data_paths` cost, through `unit_cache.environment_stamp` and `oracle_cache.stamped_data_paths` alike. It rides the artifact cycle's run_m1 skip key alone, under `CONTACT_ALLOW_LABEL`, hashed by `contact_allow_digest`: prose-blind in the same way a rune is, since a signature's `why:` is the reviewer's recorded rationale and can move no gate.

The divergence ledger keeps its place in `data` and its exemption from `tables_value`, hashed by `divergence_ledger_digest` under `DIVERGENCE_LEDGER_LABEL`: a class's predicate, status, `no_verdict` flag, count and exemplars are what the audit, the classifier and the census read, so those still move the Stage A record and the artifact cycle's run_m1 key — spent as a re-adjudication over the tables and font already on disk — while rewording a class's `why` moves neither. That one rationale is read after all, the way a refusal's is: the review build writes it into the surface manifest's `classes[].why` and `check_manifest` requires it to be there. So it rides the Stage B `explain_prose` component through `ledger_prose_lines`, and rewording a class costs a cache-served surface rebuild and nothing else — no fixpoint, no sweep, no suite lane.

The standing approvals are in no component here either; `standing_approvals_digest` exists for the artifact cycle's rebuild-lane closures alone, and it drops each rule's `note` because no lane reads one. The plumbing key deliberately does not use it: `standing_verdicts` quotes a rule's `note` verbatim into every verdict it fills, so a reword changes what the chain writes and `artifact_cycle.plumbing_skip_fingerprint` keeps the file's raw bytes.

Rune files are hashed by `rune_file_digest`, a prose-blind digest over the parsed document rather than the raw bytes: YAML comments and formatting, the ductus prose, the notes prose, and every `why` rationale — refuse records' included — are documentation no stage that builds anything consumes, so editing them must not stale the surface or re-run a cycle. What stays in the digest is exactly what can move an output or a gate: every geometric and policy field, the ductus *keys* (motion names, which the parity and naming lints enforce), and the *presence* of every prose field (the schema requires `why` on absolute prefers).

One rationale is read after all, and it has two homes of its own rather than a place in that digest. `policy.refuse[].why` is what the kernel crate's engine appends to a refusal's elimination sentence when it is asked for an explain ladder — a request the table fixpoint never makes and the review surface's explain panel is the whole audience for. So it rides `rune_explain_digest`, which the review unit cache's family keys are built from, and the Stage B `explain_prose` component, which the surface's manifest stamps: rewording one re-enriches the windows whose explain text quotes it and re-stamps the surface, and costs nothing else. The tables' stamp, the conformance sweep's key, the rebuild lanes' keys, the artifact cycle's run_m1 green, the oracle row cache's family keys, and `unit_cache.environment_stamp` all read `rune_file_digest` and cannot see it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

import yaml

FORMAT = "ams-inputs-fingerprint/2"
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
STAGE_A_COMPONENTS = ("data", "baselines", "pipeline_code")
STAGE_B_COMPONENTS = ("review_code", "static", "fonts", "explain_prose")
COMPONENTS = STAGE_A_COMPONENTS + STAGE_B_COMPONENTS
STAGE_A_FILENAME = "inputs_fingerprint.json"


def file_sha256(path: Path) -> str:
    """The shared file-content hash behind the build's fingerprints, stamps, and green records. Streamed through the digest rather than read whole, so hashing a file never costs its size in resident memory: the same value either way, and most of what passes through is small, but these inputs all grow with the migration and the one that forced the change is already hundreds of megabytes. A module that deliberately keeps rebuild.pipeline out of its import surface spells the same streamed read out inline instead and says why where it does; the roster is not written down here, because rebuild/test_fingerprint.py enforces it and a prose copy could only drift. Missing-file behavior stays with each caller, which is the one thing they disagree about."""
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


# The comparison side of rebuild/pipeline/: modules that run against tables and a font already built and build neither. They ride `pipeline_code_paths` — the whole-run record, the run_m1 green, the review surface's stamp — and `table_code_paths` leaves them out, so that a serialized window enumeration's stamp names what produced it and nothing more; the review unit cache's `unit_cache.surface_code_paths` leaves them out by its own roster, since the surface build never imports them either. rebuild/test_build_code_closure.py walks the import graph from every other pipeline module and from `run_m1.run`, and fails the moment either reaches one of these, so the roster cannot quietly admit a module the build actually runs.
COMPARISON_CODE_MODULES = frozenset({"oracle.py"})


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
    """The tools/ modules the M1 font compile runs: the import closure of tools/build_font.py inside tools/, which is what rebuild/pipeline/compile_font.py reaches when it hands the mini font's glyph data and FEA to `build_font` — the glyph compiler, the IR, the FEA emitter, the join analysis, and the Departure Mono import (compile_font puts tools/ on sys.path, so those modules import one another by bare name). A roster rather than the whole tree, because most of tools/ is authoring and audit scripts no build runs, and pinned to the walked closure by rebuild/test_build_code_closure.py so it can neither rot into an include-list nor miss a module the compile picked up. Returned unconditionally, the way the crate's paths are: `hash_paths` keeps whatever is a file."""
    return sorted(Path(repo_root) / "tools" / name for name in FONT_COMPILE_TOOL_MODULES)


def pipeline_code_paths(repo_root: Path) -> list[Path]:
    """rebuild/validation and the kernel crate ride in this component: the shaper, row model, seam classifier, and Manual-pin replays are the before side of the M1 comparison, while the crate emits the transition stream and formation guard the font is built from. Both fingerprinted Python trees and the crate's complete build-input surface are included rather than tracking current imports or modules piecemeal; those lists go wrong the next time an import or Rust module is added, and over-invalidation is the safe direction. The review unit cache's two store stamps are the one reader that does not take this component: `unit_cache.surface_code_paths` is the walked closure of what the surface build runs, a strict subset of this tree held to the import graph by rebuild/test_review_code_closure.py, so a pipeline or crate edit the surface never executes moves this record and the run_m1 green without dropping the store.

    The font compile's tools/ closure rides here too (`font_compile_tool_paths`), because compile_font hands the mini font to tools/build_font.py: an edit to the glyph compiler, the IR, the FEA emitter or the join analysis moves M1.otf's bytes, and until they were stamped it moved them under a run_m1 green, a table stamp, a conform key, a surface stamp and a unit-cache environment stamp that all stayed put. That tree is named by roster rather than swept whole, since nearly all of it is authoring and audit scripts the build never runs, and the roster is held to the walked closure by a test rather than tracked by hand — conservative in the same direction as the crate's build-input surface. Being pipeline code, it rides `table_code_paths` and so `tables_value` as well, which is right: the compile is on the build side, and only `COMPARISON_CODE_MODULES` leaves that stamp.
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


REVIEW_NON_BUILD_MODULES = frozenset({"serve.py", "status.py", "journal.py", "export.py"})


def review_code_paths(repo_root: Path) -> list[Path]:
    """The surface build's own code: rebuild/review/ minus the modules the build never imports, because a stamp component that moves on an edit the build cannot execute costs a full surface rebuild and drops both per-unit caches while proving nothing. serve.py is the dev server; status.py, journal.py, and export.py belong to the verdict plumbing, whose own key hashes what it runs (plumbing_skip_fingerprint). rebuild/test_review_code_closure.py walks build.py's import graph both ways so this exclusion list cannot drift from the real closure."""
    return sorted(
        path
        for path in (Path(repo_root) / "rebuild" / "review").glob("*.py")
        if path.name not in REVIEW_NON_BUILD_MODULES
    )


def static_paths(repo_root: Path) -> list[Path]:
    return sorted(
        path for path in (Path(repo_root) / "rebuild" / "review" / "static").rglob("*") if path.is_file()
    )


def font_paths(repo_root: Path) -> list[Path]:
    root = Path(repo_root)
    return [
        root / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
        root / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf",
    ]


def _label(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return path.name


def path_lines(repo_root: Path, paths: list[Path]) -> list[str]:
    """The per-file `label\\tdigest` lines a path-set hash is built from, sorted — exposed so a green record can store them and a skip miss can name exactly which input moved instead of reporting only that some 64-hex value did."""
    return sorted(f"{_label(repo_root, path)}\t{file_sha256(path)}" for path in paths if path.is_file())


def hash_paths(repo_root: Path, paths: list[Path]) -> str:
    return hashlib.sha256("\n".join(path_lines(repo_root, paths)).encode()).hexdigest()


def cursive_anchor_map(font) -> dict[str, list]:
    """Per glyph, the cursive-attachment geometry the after font positions it by: one (lookup index, entry, exit) triple per CursivePos record naming it, anchors as [x, y] or None. GPOS is the one channel a compiled glyph's rendering reads outside its charstring and advance, so it belongs in the per-glyph digest."""
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
    """Per qs family, a digest over the after font's compiled glyphs whose name stem belongs to it (decomposed outline operations, so subroutine plumbing can never hide a change; advance and sidebearing; cursive anchors), plus one environment digest over everything else a shaped run can touch regardless of family: the non-qs glyphs (boundary and marker helpers), the cmap, and the GPOS feature-to-lookup wiring. Two caches key on it — the review unit cache's family content keys (`unit_cache.family_content_keys`) and the oracle's per-row position store (`oracle_cache.position_family_keys`) — and it lives here because `rebuild/review/` imports `rebuild/pipeline/` and never the reverse.

    The GSUB wiring is deliberately not in the environment digest, and it is the one omission worth arguing. A rune edit moves the GSUB lookup list on essentially every cycle, so folding it in made both of the unit cache's whole-store stamps move on exactly the workflow the cache exists for — a store that never once served a unit. What covers a window's glyph selection instead is a pair of things already in the keys: the settled cells the window resolves to (the audit row's `new` column for a unit; the served row verdict's own per-family rune keys for an oracle row), and `gate:conform`, which re-shapes the compiled font through HarfBuzz every cycle and proves its selection is the settlement's. So within a cycle whose conform gate is green, which glyph the after font puts in a window is a function of that window's settled cells, and those cells cannot move without the key moving. Everything the selected glyph then contributes — outline, advance, cursive anchors — is in the per-family digests, for every variant of every family the window can reach rather than only the selected one, and the code that emits the GSUB at all is in the environment stamp's pipeline fingerprint. GPOS stays because its channel is positional: it can move a run without moving a name or a cell.
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


# Every policy record kind that carries an author `why`, and the one whose rationale something downstream reads. The difference is the whole of what separates `rune_file_digest` from `rune_explain_digest`.
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
    """The prose-blind view of a parsed rune document (see the module docstring for the contract). Anything shaped in a way the schema would reject — a non-string prose value, a non-dict ductus — passes through unprojected, so a type-breaking edit still moves the digest and the load failure it causes stays visible. `quoted_prose` spares the one rationale something downstream reads, `policy.refuse[].why`, which is the whole difference between the two rune digests here."""
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
    """Content digest of one YAML file over a prose-blind projection of what it parses to, so documentation edits, comments, and reformatting leave it unmoved. Falls back to the raw byte hash when the file does not parse or serialize — a malformed input is a stopping change wherever one of these digests is read, and the fallback keeps it visible rather than hashing a guess."""
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
    """One rune file's explain-aware content digest: `rune_file_digest`'s projection but for `policy.refuse[].why`, the sentence the crate's engine appends to a refusal's elimination when it is asked for an explain ladder and the review surface serves as explain text. Nothing that builds a table or a font asks for that ladder, so this digest is the review side's alone — it exists so rewording a refusal can invalidate the windows quoting it without touching anything keyed on the prose-blind digest."""
    return _projected_digest(path, _projected_rune_keeping_quoted_prose)


CONTACT_ALLOW_LABEL = "rebuild/m1-contact-allow.yaml"


def _projected_allow_list(document: object) -> object:
    """The prose-blind view of the parsed contact allow-list: every entry keeps its signature and loses its `why`, which is the reviewer's rationale for blessing that corner and reaches no gate. A document shaped in a way `defects.run_gates` would reject passes through unprojected, so the load failure it causes stays visible in the digest."""
    if not isinstance(document, list):
        return document
    return [_without_why(entry) for entry in document]


def contact_allow_digest(path: Path) -> str:
    """The contact allow-list's prose-blind content digest — the one line the allow list contributes to any key here, and it contributes it to the artifact cycle's run_m1 skip key alone (`CONTACT_ALLOW_LABEL`). Blessing a signature moves it; wording the bless does not."""
    return _projected_digest(path, _projected_allow_list)


DIVERGENCE_LEDGER_LABEL = "rebuild/m1-divergences.yaml"
STANDING_APPROVALS_LABEL = "rebuild/standing-approvals.yaml"


def _projected_ledger(document: object) -> object:
    """The prose-blind view of the parsed divergence ledger: every entry keeps its `id`, `status`, `match`, `no_verdict`, `ink_identical`, `count` and `exemplars` — which `audit.load_ledger`, `oracle.classify_divergence` and the census all read — and loses only its `why`, the reviewer's recorded rationale for the class. A document shaped in a way `audit.load_ledger` would refuse passes through unprojected, so the load failure it causes stays visible in the digest."""
    if not isinstance(document, list):
        return document
    return [_without_why(entry) for entry in document]


def divergence_ledger_digest(path: Path) -> str:
    """The divergence ledger's prose-blind content digest, which is how `data_lines` hashes it. Retriaging a class moves it; rewording one does not, because that wording rides `explain_prose` through `ledger_prose_lines` instead."""
    return _projected_digest(path, _projected_ledger)


def _projected_standing_rules(document: object) -> object:
    """The note-blind view of the parsed standing approvals: every rule keeps its `id`, `verdict`, `match` and `except_left`, and loses only its `note`. A document shaped in a way `standing_verdicts.load_rules` would refuse passes through unprojected, for the same reason the other two projections do."""
    if not isinstance(document, dict):
        return document
    rules = document.get("rules")
    if not isinstance(rules, list):
        return document
    return {**document, "rules": [_without_prose(rule, "note") for rule in rules]}


def standing_approvals_digest(path: Path) -> str:
    """The standing approvals' note-blind content digest. No component here carries it: the artifact cycle's rebuild-lane closures are its whole audience, since a rule's `note` is quoted into the verdicts the chain fills and so belongs in the plumbing key's raw bytes rather than here."""
    return _projected_digest(path, _projected_standing_rules)


def _data_digest(root: Path, path: Path, runes: set[Path]) -> str:
    """How one data input is hashed for `data_lines`: a prose-blind digest where the input has one — the rune files' and the divergence ledger's — and raw bytes otherwise."""
    if path in runes:
        return rune_file_digest(path)
    if _label(root, path) == DIVERGENCE_LEDGER_LABEL:
        return divergence_ledger_digest(path)
    return file_sha256(path)


def data_lines(repo_root: Path) -> list[str]:
    """The per-file `label\\tdigest` lines the `data` component is built from: rune files and the divergence ledger by their prose-blind digests, every other data input by raw bytes. Sorted, like `path_lines`, and exposed for the same reason."""
    root = Path(repo_root)
    runes = set(rune_paths(root))
    return sorted(
        f"{_label(root, path)}\t{_data_digest(root, path, runes)}"
        for path in data_paths(root)
        if path.is_file()
    )


def data_value(repo_root: Path) -> str:
    """The `data` component: rune files and the divergence ledger by their prose-blind digests, every other data input by raw bytes."""
    return hashlib.sha256("\n".join(data_lines(repo_root)).encode()).hexdigest()


NON_TABLE_DATA_LABELS = (
    "glyph_data/senior_quikscript_kerning.yaml",
    "rebuild/m1-aliases.yaml",
    DIVERGENCE_LEDGER_LABEL,
)


def table_data_lines(repo_root: Path) -> list[str]:
    """`data_lines` minus the three data inputs no table stage reads. `rebuild/m1-aliases.yaml` and `rebuild/m1-divergences.yaml` are the baseline oracle's, read to name and classify divergences the fixpoint has already decided; `glyph_data/senior_quikscript_kerning.yaml` is the position channel's, read by `oracle.KernEvaluator` alone to add the sidecar's kerns back before a baseline position diff — the font compile hands the builder its own empty kerning map and never opens the file. None of the three reaches the kernel crate or any stage that builds a decision table, so folding them into the tables' own stamp only made a ledger, classifier, or kern re-adjudication throw away an enumeration that would come back byte for byte.

    Narrower stamp, same coverage: all three stay in `data_lines`, which is what the artifact cycle's run_m1 green record and the Stage A `data` component are keyed on, so editing one still moves that key — and what the cycle spends against it is a re-adjudication over the tables and font already on disk (`artifact_cycle.comparison_side_label`), not a fixpoint.
    """
    excluded = set(NON_TABLE_DATA_LABELS)
    return [line for line in data_lines(repo_root) if line.split("\t", 1)[0] not in excluded]


def table_data_value(repo_root: Path) -> str:
    """The data half of the stamp a serialized window enumeration carries: `table_data_lines` hashed, which is `data_value` narrowed by exactly the comparison-side labels in `NON_TABLE_DATA_LABELS` and by nothing else."""
    return hashlib.sha256("\n".join(table_data_lines(repo_root)).encode()).hexdigest()


def rune_digests(repo_root: Path) -> dict[str, str]:
    """Every rune file's prose-blind digest, keyed by family name (the file stem, which spec_load lints to equal the `rune:` field). This is the per-rune grain the oracle row cache invalidates at (`oracle_cache.family_keys`), so a cached row survives a cycle exactly when every family it names still carries the digest recorded beside it. The review unit cache keys on `rune_explain_digests` instead, because among the products it caches is the explain text a refusal's `why` is quoted into."""
    return {path.stem: rune_file_digest(path) for path in rune_paths(Path(repo_root)) if path.is_file()}


def rune_explain_digests(repo_root: Path) -> dict[str, str]:
    """`rune_digests` at the same per-family grain and explain-aware, which is what the review unit cache's family content keys (`unit_cache.family_content_keys`) are built from: an entry moves when a record moves and when a refusal's `why` is reworded, and on nothing else. Byte-identical to `rune_digests` for a rune whose refusals carry no `why`."""
    return {path.stem: rune_explain_digest(path) for path in rune_paths(Path(repo_root)) if path.is_file()}


def refuse_prose_lines(repo_root: Path) -> list[str]:
    """The `family\\tindex\\twhy` line of every refuse record carrying a `why`, sorted — the whole of the rune prose anything downstream reads, and one of the two inputs to the `explain_prose` component, `ledger_prose_lines` being the other. A rune that will not parse or decode contributes `family\\t-\\t<raw digest>` instead, the way `_projected_digest` falls back, so a broken file moves the value rather than silently contributing no refusals at all."""
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
    """The `ledger\\t<id>\\t<why>` line of every divergence-ledger entry carrying a rationale, sorted — the `explain_prose` component's other input, and the reason the ledger's `why` can leave the `data` digest without the surface being able to serve a stale one. The review build copies that rationale into the manifest's `classes[].why`, which `check_manifest` requires, and nothing else downstream reads it: no shard and no sidecar carries it, so rewording a class re-stamps the surface and re-enriches nothing. A ledger that will not parse or decode, or that is not the list `audit.load_ledger` expects, contributes `ledger\\t-\\t<raw digest>` instead, the way `_projected_digest` falls back."""
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
    """The `explain_prose` component: `refuse_prose_lines` and `ledger_prose_lines` concatenated and then sorted as one list, hashed, so the surface's manifest can answer whether the explain text and the class rationales it serves are the wording on disk. The two kinds cannot collide because a ledger line's first field is the literal `ledger` and a refuse line's is a family name. Stage B rather than Stage A because no stage of the M1 build reads any of it — run_m1 could not record it honestly, and a stale value here is the surface's to fix rather than a rebuild's."""
    root = Path(repo_root)
    lines = sorted(refuse_prose_lines(root) + ledger_prose_lines(root))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def table_code_paths(repo_root: Path) -> list[Path]:
    """`pipeline_code_paths` minus `COMPARISON_CODE_MODULES`: the code half of the stamp a serialized window enumeration carries. Everything that can move a table or the font stays — the crate, the spec loader, the emitters, the compiler, the gates that run inside the build — and what leaves is only what runs against those artifacts afterward. Conservative in the same direction as `pipeline_code_paths`: a module the build never reaches is still stamped unless it is named in the roster, and the import-graph test is what earns a module its place there."""
    root = Path(repo_root)
    pipeline = root / "rebuild" / "pipeline"
    return [
        path
        for path in pipeline_code_paths(root)
        if not (path.parent == pipeline and path.name in COMPARISON_CODE_MODULES)
    ]


def tables_value(repo_root: Path) -> str:
    """The content key over everything the decision-table fixpoint and the font compile read: the rune and config data by `table_data_value`, plus the build side of the pipeline code by `table_code_paths`. A serialized window enumeration carries this value so it can prove it still describes the sources on disk, and the conformance sweep refuses the moment it does not. Deliberately narrower than the Stage A record at both ends — the oracle's baselines feed no table, so re-extracting them must not throw the windows away, and neither do the alias map, the divergence ledger, the kern sidecar, or the oracle's own code, so re-adjudicating one of those must not either. The contact allow-list is narrower still: it is in no component of the Stage A record either, and an edit to it moves this stamp exactly as little."""
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


def stage_b(repo_root: Path, before_font: Path, junior_font: Path) -> dict:
    root = Path(repo_root)
    return {
        "review_code": hash_paths(root, review_code_paths(root)),
        "static": hash_paths(root, static_paths(root)),
        "fonts": hash_paths(root, [Path(before_font), Path(junior_font)]),
        "explain_prose": explain_prose_value(root),
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

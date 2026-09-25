"""The Manual-pin gate: replays every data-expect pin from `MANDATING_CORPORA` whose text uses only migrated letters against the built M1 font, and reports each disagreement.

The mandating corpora are the repository's transcription of what The Manual requires letters to look like, and `test/test_shaping.py` checks them against the shipped Senior font. This gate replays each in-scope pin against M1.otf with the validation suite's shaper and GPOS seam classifier, and `run_m1` fails the build on any disagreement, when no pin is in scope, or when an in-scope pin was not replayed (`run_m1.manual_pin_gate_failure`). There is no waiver list. A disagreement means either the rune data breaks a Manual requirement (fix the runes) or the pin mistranscribes The Manual (fix the pin, which `test/test_shaping.py` then checks against the shipped font).

The third corpus `test/test_shaping.py` reads, site/extra-senior-words.html, is left out. It is a supplementary word list, not a transcription of The Manual, so a disagreement with it shows that M1 and the shipped font differ on that word but should not block a rune batch. `rebuild/validation/pins.py` still reads all three corpora, because it checks fidelity to the shipped font.

The checks follow `rebuild/validation/pins.py` with two differences. First, `.half` and `.alt` assertions look up the stance's declared traits: the shaped glyph name is split back into rune and stance, because M1 stance names (`flipped`, `alternate`) need not contain the trait name the way the old glyph names did. Second, the `.∅` exact-glyph assertion accepts either the bare cmap glyph or the label of the rune's isolated cell, both of which draw the letter with no contextual variant. Other variant assertions (`en-y0`, `noentry`, `extended`, and so on) are skipped and counted, as in the baseline replay, because the seam-height assertions already cover their design content and the old font's compat metadata has no M1 equivalent.

Pins that use letters outside the alphabet are counted per missing letter (`blocked_by`, `sole_blocker`), so the summary shows which letters' migration would bring the most pins into scope. A pin whose stylistic-set configuration is not in `conform.ACCEPTANCE_CONFIGS` is skipped and counted in `skipped_config`, because the M1 font does not implement that feature. `rebuild/validation/pins.py` still checks such a pin against the shipped font.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from rebuild.pipeline import conform, geometry
from rebuild.pipeline.labels import spec_alphabet
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.settle import cell_label
from rebuild.validation.classify import SeamClassifier
from rebuild.validation.pins import Disagreement, PinRun, ReplayReport, _import_test_shaping, collect_pin_runs
from rebuild.validation.rowmodel import Row, format_codepoints
from rebuild.validation.shaping import Shaper, last_glyph_covering, row_for

REPO_ROOT = Path(__file__).resolve().parents[2]
PS_NAMES_PATH = REPO_ROOT / "postscript_glyph_names.yaml"

# The module docstring says why site/extra-senior-words.html is left out.
MANDATING_CORPORA = ("site/index.html", "site/the-manual.html")

BOUNDARY_GLYPH_EQUIVALENTS = {"space": frozenset({"space", "uni200C"})}


@dataclass
class ManualPinReport:
    pins_eligible: int = 0
    pins_in_scope: int = 0
    skipped_config: int = 0
    replayed: int = 0
    seam_assertions: int = 0
    identity_assertions: int = 0
    trait_assertions: int = 0
    variant_assertions_skipped: int = 0
    disagreements: list[Disagreement] = field(default_factory=list)
    blocked_by: Counter = field(default_factory=Counter)
    sole_blocker: Counter = field(default_factory=Counter)

    @property
    def passed(self) -> bool:
        return not self.disagreements


def migrated_alphabet(spec: ResolvedSpec) -> frozenset[int]:
    return frozenset(ord(ch) for ch in spec_alphabet(spec))


def _codepoint_display_names() -> dict[int, str]:
    names = yaml.safe_load(PS_NAMES_PATH.read_text())
    return {cp: name for name, cp in names.items()}


def _stance_traits(spec: ResolvedSpec, glyph_name: str) -> frozenset[str]:
    parts = glyph_name.split(".")
    rune = spec.runes.get(parts[0])
    if rune is None or len(parts) < 2:
        return frozenset()
    stance = rune.stances.get(parts[1])
    if stance is None:
        return frozenset()
    return frozenset(stance.traits)


def _exact_glyph_names(spec: ResolvedSpec, base: str) -> frozenset[str]:
    names = {base}
    if base in spec.runes:
        names.add(cell_label(spec, geometry.isolated_cell(spec, base)))
    return frozenset(names)


def _expected_base(token: dict) -> str:
    if token["lig_base"]:
        return f"{token['base']}_{token['lig_base']}"
    return token["base"]


def _base_matches(expected: str, glyph_base: str) -> bool:
    if glyph_base == expected:
        return True
    return glyph_base in BOUNDARY_GLYPH_EQUIVALENTS.get(expected, frozenset())


def _check_interpretation(
    spec: ResolvedSpec,
    text: str,
    tokens: list[dict],
    connections: list[dict],
    row: Row,
) -> tuple[str | None, int, int, int, int]:
    """Checks one interpretation of a pin's optional ligatures against a shaped row. Returns (first failure or None, seam assertions, identity assertions, trait assertions, variant assertions skipped)."""
    ts = _import_test_shaping()
    spans = ts._token_char_spans(text, tokens)
    seam_checks = 0
    identity_checks = 0
    trait_checks = 0
    variant_skips = 0

    for i, token in enumerate(tokens):
        start, end = spans[i]
        glyph = row.glyphs[last_glyph_covering(row.clusters, start)]
        base = glyph.split(".")[0]
        expected = _expected_base(token)
        identity_checks += 1
        if not _base_matches(expected, base):
            return (
                f"token {i}: expected base {expected}, got {glyph!r}",
                seam_checks,
                identity_checks,
                trait_checks,
                variant_skips,
            )
        if token["exact_glyph"] and glyph not in _exact_glyph_names(spec, expected):
            return (
                f"token {i}: expected the isolated form of {expected}, got {glyph!r}",
                seam_checks,
                identity_checks,
                trait_checks,
                variant_skips,
            )
        traits = _stance_traits(spec, glyph)
        for v in token["variants"]:
            if v in ("half", "alt"):
                trait_checks += 1
                if v not in traits:
                    return (
                        f"token {i}: expected trait {v!r} on {glyph!r} (stance traits: {sorted(traits)})",
                        seam_checks,
                        identity_checks,
                        trait_checks,
                        variant_skips,
                    )
            else:
                variant_skips += 1
        for v in token.get("neg_variants", []):
            if v in ("half", "alt"):
                trait_checks += 1
                if v in traits:
                    return (
                        f"token {i}: trait {v!r} must not appear on {glyph!r}",
                        seam_checks,
                        identity_checks,
                        trait_checks,
                        variant_skips,
                    )
            else:
                variant_skips += 1
        for k in range(start, end - 1):
            seam_checks += 1
            if row.seams[k] != "lig":
                return (
                    f"token {i}: expected ligature seam at {k}, got {row.seams[k]!r}",
                    seam_checks,
                    identity_checks,
                    trait_checks,
                    variant_skips,
                )

    for i, conn in enumerate(connections):
        seam_index = spans[i + 1][0] - 1
        seam = row.seams[seam_index]
        kind = conn["kind"]
        if kind == "maybe":
            continue
        seam_checks += 1
        if kind == "height":
            if seam != f"y{conn['y']}":
                return (
                    f"connection {i}: expected y{conn['y']} at seam {seam_index}, got {seam!r}",
                    seam_checks,
                    identity_checks,
                    trait_checks,
                    variant_skips,
                )
        elif kind == "join":
            if not seam.startswith("y"):
                return (
                    f"connection {i}: expected a join at seam {seam_index}, got {seam!r}",
                    seam_checks,
                    identity_checks,
                    trait_checks,
                    variant_skips,
                )
        elif kind in ("break", "break_no_isolation"):
            if seam != "break":
                return (
                    f"connection {i}: expected break at seam {seam_index}, got {seam!r}",
                    seam_checks,
                    identity_checks,
                    trait_checks,
                    variant_skips,
                )
        else:
            raise ValueError(f"unknown connection kind {kind!r}")

    return (None, seam_checks, identity_checks, trait_checks, variant_skips)


def _check_pin(
    spec: ResolvedSpec,
    shaper: Shaper,
    classifier: SeamClassifier,
    pin: PinRun,
    report: ManualPinReport,
) -> None:
    ts = _import_test_shaping()
    row = row_for(shaper, classifier, pin.text, pin.features or None)
    interpretations = ts._expand_maybe_ligatures(list(pin.tokens), list(pin.connections))
    errors: list[str] = []
    for tokens, connections in interpretations:
        try:
            error, seam_checks, identity_checks, trait_checks, variant_skips = _check_interpretation(
                spec, pin.text, tokens, connections, row
            )
        except ValueError as exc:
            errors.append(f"structural: {exc}")
            continue
        if error is None:
            report.replayed += 1
            report.seam_assertions += seam_checks
            report.identity_assertions += identity_checks
            report.trait_assertions += trait_checks
            report.variant_assertions_skipped += variant_skips
            return
        errors.append(error)
    report.replayed += 1
    report.disagreements.append(
        Disagreement(
            kind="manual-pin",
            source=pin.source,
            config=pin.config_token,
            codepoints=format_codepoints(row.codepoints),
            expect=pin.expect,
            detail=f"shaped {'|'.join(row.glyphs)} seams {','.join(row.seams)}; " + " // ".join(errors),
        )
    )


def run_gate(font_path: Path, spec: ResolvedSpec) -> ManualPinReport:
    report = ManualPinReport()
    collection = ReplayReport()
    pins = [pin for pin in collect_pin_runs(collection) if pin.source.startswith(MANDATING_CORPORA)]
    report.pins_eligible = len(pins)
    alphabet = migrated_alphabet(spec)

    in_scope: list[PinRun] = []
    for pin in pins:
        if pin.config_token not in conform.ACCEPTANCE_CONFIGS:
            report.skipped_config += 1
            continue
        missing = {ord(ch) for ch in pin.text} - alphabet
        if missing:
            report.blocked_by.update(missing)
            if len(missing) == 1:
                report.sole_blocker.update(missing)
            continue
        in_scope.append(pin)
    report.pins_in_scope = len(in_scope)

    shaper = Shaper(font_path)
    classifier = SeamClassifier(font_path)
    for pin in in_scope:
        _check_pin(spec, shaper, classifier, pin, report)
    return report


def summarize(report: ManualPinReport, blocker_limit: int = 10) -> dict:
    names = _codepoint_display_names()
    blockers = [
        {
            "codepoint": f"U+{cp:04X}",
            "letter": names.get(cp, "?"),
            "blocks": count,
            "sole_blocker_of": report.sole_blocker.get(cp, 0),
        }
        for cp, count in report.blocked_by.most_common(blocker_limit)
    ]
    return {
        "pins_eligible": report.pins_eligible,
        "pins_in_scope": report.pins_in_scope,
        "skipped_config": report.skipped_config,
        "replayed": report.replayed,
        "seam_assertions": report.seam_assertions,
        "identity_assertions": report.identity_assertions,
        "trait_assertions": report.trait_assertions,
        "variant_assertions_skipped": report.variant_assertions_skipped,
        "disagreements": [f"{d.source} [{d.config}] {d.expect!r}: {d.detail}" for d in report.disagreements],
        "top_blocking_letters": blockers,
        "pass": report.passed,
    }

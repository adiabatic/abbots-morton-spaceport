"""HarfBuzz conformance gate for the de-risking prototype (PLAN.md sections 1 and 6a).

Enumerates every string of length 1-5 over the prototype alphabet (qsIt U+E670, qsTea U+E652, qsMay U+E665, qsOy U+E679, space, ZWNJ), shapes each through the prototype OTF with uharfbuzz under both feature configurations, and diffs the glyph-name sequence transition by transition against the pure-Python settlement oracle in prototype/settle.py. Glyph names are resolved through fontTools (TTFont.getGlyphName) because HarfBuzz's glyph_to_string truncates at 63 bytes. Length 5 (PLAN.md deviation 14): every rule window fits in four positions, but the adversarial ZWNJ placement against a backtracked two-slot rule (backtrack, input, first lookahead, ZWNJ, second lookahead) needs five.

On a clean full-gate run this writes prototype/out/conform_summary.json and records the HarfBuzz half of the K3 verdict in prototype/out/budget.json (the CoreText half comes from prototype/coretext_smoke.py).

Run as: uv run python prototype/conform.py [--font PATH] [--mechanics-only] [--max-length N]

When --font points at anything other than prototype/out/Proto.otf (for example a built font under site/), the oracle comparison is skipped automatically and only the harness mechanics run: enumeration, shaping, ZWNJ structural checks (zero advance, no ink), and split-buffer equivalence. Mechanics mode reports findings but always exits 0, because divergences against a non-prototype font are expected (the present-day ss03 ZWNJ leak, recon/families.md section 4 row 20, shows up here by design).
"""

import argparse
import itertools
import json
import sys
from dataclasses import dataclass
from pathlib import Path

PROTOTYPE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROTOTYPE_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(PROTOTYPE_DIR))

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

DEFAULT_FONT = PROTOTYPE_DIR / "out" / "Proto.otf"
ZWNJ = "\u200c"
ZWNJ_SENTINEL = "<zwnj>"
FALLBACK_ALPHABET = ("\ue670", "\ue652", "\ue665", "\ue679", " ", ZWNJ)
FALLBACK_FEATURE_MATRIX = {"default": frozenset(), "ss03": frozenset({"ss03"})}
MAX_PRINTED_DIVERGENCES = 100


@dataclass
class ShapedGlyph:
    name: str
    gid: int
    cluster: int
    x_advance: int
    x_offset: int
    y_offset: int


@dataclass
class Divergence:
    text: str
    config: str
    position: int
    expected: str
    got: str
    kind: str


def hex_codepoints(text: str) -> str:
    return " ".join(f"{ord(ch):04X}" for ch in text)


class Shaper:
    def __init__(self, font_path: Path):
        self.font_path = font_path
        self.tt = TTFont(str(font_path))
        self.hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(str(font_path))))
        self.glyph_set = self.tt.getGlyphSet()
        self._ink_cache: dict[str, bool] = {}
        self._outline_cache: dict[str, tuple] = {}

    def shape(self, text: str, features: frozenset[str]) -> list[ShapedGlyph]:
        buf = hb.Buffer()
        # MONOTONE_CHARACTERS keeps each input character in its own cluster; the default MONOTONE_GRAPHEMES merges ZWNJ (Grapheme_Cluster_Break=Extend) into the preceding cluster, which would hide which output slot is the ZWNJ slot.
        buf.cluster_level = hb.BufferClusterLevel.MONOTONE_CHARACTERS
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {tag: True for tag in features})
        return [
            ShapedGlyph(
                name=self.tt.getGlyphName(info.codepoint),
                gid=info.codepoint,
                cluster=info.cluster,
                x_advance=pos.x_advance,
                x_offset=pos.x_offset,
                y_offset=pos.y_offset,
            )
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions)
        ]

    def has_ink(self, glyph_name: str) -> bool:
        cached = self._ink_cache.get(glyph_name)
        if cached is None:
            pen = BoundsPen(self.glyph_set)
            self.glyph_set[glyph_name].draw(pen)
            cached = pen.bounds is not None
            self._ink_cache[glyph_name] = cached
        return cached

    def outline_signature(self, glyph_name: str) -> tuple:
        cached = self._outline_cache.get(glyph_name)
        if cached is None:
            pen = RecordingPen()
            self.glyph_set[glyph_name].draw(pen)
            cached = tuple(pen.value)
            self._outline_cache[glyph_name] = cached
        return cached


class OracleUnavailable(Exception):
    pass


class Oracle:
    """Adapter around prototype/settle.py. PLAN.md section 1 fixes the API as settle(sequence, features) -> list[Settled] but leaves the Settled-to-glyph-name mapping to the settlement module, so this adapter probes a small set of obvious conventions and fails loudly with the list of what it tried."""

    def __init__(self):
        try:
            import settle
        except ImportError as error:
            raise OracleUnavailable(f"cannot import prototype/settle.py: {error}")
        self._module = settle
        self._features_style: str | None = None

    def expected_glyph_names(self, text: str, features: frozenset[str]) -> list[str]:
        direct = getattr(self._module, "settled_glyph_names", None)
        if callable(direct):
            return [str(name) for name in self._call(direct, text, features)]
        settle_function = getattr(self._module, "settle", None)
        if not callable(settle_function):
            raise OracleUnavailable("prototype/settle.py exposes neither settled_glyph_names nor settle")
        settled = self._call(settle_function, text, features)
        return [self._name_of(item) for item in settled]

    def _call(self, function, text: str, features: frozenset[str]):
        if self._features_style == "set":
            return function(text, features)
        if self._features_style == "dict":
            return function(text, {tag: True for tag in features})
        try:
            result = function(text, features)
            self._features_style = "set"
            return result
        except TypeError:
            result = function(text, {tag: True for tag in features})
            self._features_style = "dict"
            return result

    def _name_of(self, item) -> str:
        if isinstance(item, str):
            return item
        for attribute in ("glyph_name", "name"):
            value = getattr(item, attribute, None)
            if isinstance(value, str):
                return value
            if callable(value):
                result = value()
                if isinstance(result, str):
                    return result
        for function_name in ("glyph_name", "glyph_name_for", "cell_glyph_name"):
            function = getattr(self._module, function_name, None)
            if callable(function):
                result = function(item)
                if isinstance(result, str):
                    return result
        raise OracleUnavailable(
            f"cannot turn settle output item {item!r} into a glyph name; tried item.glyph_name, item.name, and module-level glyph_name/glyph_name_for/cell_glyph_name"
        )


class RuleCoverage:
    """The PLAN.md section 6a coverage check: every emitted settlement rule must be exercised at least once across the enumeration, so dead rows are loud. For each sequence this replays the raw GSUB pipeline (formation, then the ss03 marker fold, then the ZWNJ chokepoint), matches every letter position against the ordered rule list under first-match-wins with the oracle's settled name in the backtrack slot and raw labels in the lookahead slots, marks the matched rule, and cross-checks the predicted outcome against the oracle."""

    def __init__(self):
        import spec as spec_module
        from table import EDGE_LABEL, NA_LABEL, build_table, raw_glyph_name

        self._spec = spec_module.SPEC
        self._edge_label = EDGE_LABEL
        self._na_label = NA_LABEL
        self._raw_glyph_name = raw_glyph_name
        self.rules = build_table(self._spec).rules
        self._rules_by_input: dict[str, list[tuple[int, object]]] = {}
        for index, rule in enumerate(self.rules):
            self._rules_by_input.setdefault(rule.input_glyph, []).append((index, rule))
        self._hit: set[int] = set()
        self._token_by_char = {chr(cp): token for cp, token in self._spec.codepoint_to_token.items()}
        self._formation = {(lead, trail): ligature for lead, trail, ligature in self._spec.formation}

    def _raw_labels(self, text: str, features: frozenset[str]) -> list[str]:
        tokens = [self._token_by_char[ch] for ch in text]
        formed: list[str] = []
        index = 0
        while index < len(tokens):
            pair = (tokens[index], tokens[index + 1]) if index + 1 < len(tokens) else None
            if pair in self._formation:
                formed.append(self._formation[pair])
                index += 2
            else:
                formed.append(tokens[index])
                index += 1
        labels: list[str] = []
        for position, token in enumerate(formed):
            if token == "space":
                labels.append("space")
            elif token == "zwnj":
                labels.append("uni200C")
            else:
                locked = (
                    position > 0
                    and formed[position - 1] == "zwnj"
                    and token in self._spec.entry_bearing_families
                )
                labels.append(self._raw_glyph_name(token, features, locked, self._spec))
        return labels

    def record(
        self,
        text: str,
        config: str,
        features: frozenset[str],
        expected: list[str],
        divergences: list[Divergence],
    ) -> None:
        labels = self._raw_labels(text, features)
        settled = normalize_expected(expected)
        if len(labels) != len(settled):
            return
        boundaries = {"space", "uni200C"}
        for index, label in enumerate(labels):
            if label in boundaries:
                continue
            if index == 0:
                left = self._edge_label
            elif labels[index - 1] in boundaries:
                left = labels[index - 1]
            else:
                left = settled[index - 1]
            right1 = labels[index + 1] if index + 1 < len(labels) else self._edge_label
            if right1 in boundaries or right1 == self._edge_label:
                right2 = self._na_label
            else:
                right2 = labels[index + 2] if index + 2 < len(labels) else self._edge_label
            predicted = label
            for rule_index, rule in self._rules_by_input.get(label, ()):
                if rule.backtrack is not None and left not in rule.backtrack:
                    continue
                if rule.look1 is not None and right1 not in rule.look1:
                    continue
                if rule.look2 is not None and right2 not in rule.look2:
                    continue
                self._hit.add(rule_index)
                predicted = rule.outcome
                break
            if predicted != settled[index]:
                divergences.append(Divergence(text, config, index, settled[index], predicted, "rule-replay"))
                return

    def uncovered(self) -> list:
        return [rule for index, rule in enumerate(self.rules) if index not in self._hit]


def load_alphabet() -> tuple[tuple[str, ...], str]:
    try:
        import spec
    except ImportError:
        return FALLBACK_ALPHABET, "fallback (prototype/spec.py not importable)"
    for attribute in ("ALPHABET", "alphabet"):
        value = getattr(spec, attribute, None)
        if callable(value):
            value = value()
        if value:
            return (
                tuple(chr(item) if isinstance(item, int) else str(item) for item in value),
                "prototype/spec.py",
            )
    return FALLBACK_ALPHABET, "fallback (prototype/spec.py has no ALPHABET)"


def load_anchor_lookup():
    """Best-effort hook for the gap-0 pen-position spot check. Expects spec (or settle) to expose anchors_in_font_units(glyph_name) or anchors_for_glyph(glyph_name) returning {"entry": (x, y) | None, "exit": (x, y) | None} in font units relative to the glyph origin. Returns None when no such accessor exists, in which case the check is skipped with a notice."""
    for module_name in ("spec", "settle"):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        for function_name in ("anchors_in_font_units", "anchors_for_glyph"):
            function = getattr(module, function_name, None)
            if callable(function):
                return function
    return None


def enumerate_sequences(alphabet: tuple[str, ...], max_length: int):
    for length in range(1, max_length + 1):
        for combination in itertools.product(alphabet, repeat=length):
            yield "".join(combination)


def zwnj_slots(text: str, shaped: list[ShapedGlyph]) -> set[int]:
    return {
        index
        for index, glyph in enumerate(shaped)
        if glyph.cluster < len(text) and text[glyph.cluster] == ZWNJ
    }


def normalize_actual(text: str, shaped: list[ShapedGlyph]) -> list[str]:
    slots = zwnj_slots(text, shaped)
    return [ZWNJ_SENTINEL if index in slots else glyph.name for index, glyph in enumerate(shaped)]


def normalize_expected(names: list[str]) -> list[str]:
    return [ZWNJ_SENTINEL if name in ("uni200C", "zwnj", ZWNJ) else name for name in names]


def check_oracle(
    text: str,
    config: str,
    shaped: list[ShapedGlyph],
    expected: list[str],
    divergences: list[Divergence],
    modes: set[str],
) -> None:
    actual = normalize_actual(text, shaped)
    expected = normalize_expected(expected)
    if len(actual) != len(expected):
        actual_dropped = [name for name in actual if name != ZWNJ_SENTINEL]
        expected_dropped = [name for name in expected if name != ZWNJ_SENTINEL]
        if len(actual_dropped) == len(expected_dropped):
            modes.add("oracle omits ZWNJ slots; comparing with ZWNJ slots dropped")
            actual, expected = actual_dropped, expected_dropped
        else:
            divergences.append(
                Divergence(text, config, -1, f"{len(expected)} glyphs", f"{len(actual)} glyphs", "length")
            )
            return
    for index, (want, got) in enumerate(zip(expected, actual)):
        if want != got:
            divergences.append(Divergence(text, config, index, want, got, "name"))
            return


def check_zwnj_structure(
    text: str, config: str, shaper: Shaper, shaped: list[ShapedGlyph], divergences: list[Divergence]
) -> None:
    for index in sorted(zwnj_slots(text, shaped)):
        glyph = shaped[index]
        if glyph.x_advance != 0:
            divergences.append(
                Divergence(
                    text,
                    config,
                    index,
                    "x_advance 0 at ZWNJ slot",
                    f"x_advance {glyph.x_advance} ({glyph.name})",
                    "zwnj-advance",
                )
            )
        if shaper.has_ink(glyph.name):
            divergences.append(
                Divergence(
                    text, config, index, "no ink at ZWNJ slot", f"inked glyph {glyph.name}", "zwnj-ink"
                )
            )


def slot_signature(shaper: Shaper, glyph: ShapedGlyph) -> tuple:
    return (shaper.outline_signature(glyph.name), glyph.x_advance, glyph.x_offset, glyph.y_offset)


def check_split_buffer(
    text: str,
    config: str,
    features: frozenset[str],
    shaper: Shaper,
    shaped: list[ShapedGlyph],
    divergences: list[Divergence],
) -> None:
    """ZWNJ split-buffer equivalence (the _isolation_glyphs_split pattern, test/test_shaping.py:710): the full shaping minus its ZWNJ slots must match the concatenation of the ZWNJ-delimited segments shaped in their own buffers. Comparison is per slot on (outline, x_advance, x_offset, y_offset) rather than glyph name, because the locked .noentry twins are bitmap-identical to the bare runes by design."""
    slots = zwnj_slots(text, shaped)
    full = [glyph for index, glyph in enumerate(shaped) if index not in slots]
    split: list[ShapedGlyph] = []
    for segment in text.split(ZWNJ):
        if segment:
            split.extend(shaper.shape(segment, features))
    if len(full) != len(split):
        divergences.append(
            Divergence(
                text, config, -1, f"{len(split)} glyphs (split)", f"{len(full)} glyphs (full)", "split-length"
            )
        )
        return
    for index, (full_glyph, split_glyph) in enumerate(zip(full, split)):
        if slot_signature(shaper, full_glyph) != slot_signature(shaper, split_glyph):
            expected = f"{split_glyph.name} (split halves)"
            got = f"{full_glyph.name} (full)"
            if full_glyph.name == split_glyph.name:
                expected += (
                    f" adv={split_glyph.x_advance} off=({split_glyph.x_offset},{split_glyph.y_offset})"
                )
                got += f" adv={full_glyph.x_advance} off=({full_glyph.x_offset},{full_glyph.y_offset})"
            divergences.append(Divergence(text, config, index, expected, got, "split"))
            return


def check_join_gaps(
    text: str,
    config: str,
    shaper: Shaper,
    shaped: list[ShapedGlyph],
    anchor_lookup,
    divergences: list[Divergence],
) -> None:
    pen = 0
    origins = []
    for glyph in shaped:
        origins.append((pen + glyph.x_offset, glyph.y_offset))
        pen += glyph.x_advance
    for index in range(len(shaped) - 1):
        left, right = shaped[index], shaped[index + 1]
        left_anchors = anchor_lookup(left.name) or {}
        right_anchors = anchor_lookup(right.name) or {}
        exit_anchor = left_anchors.get("exit")
        entry_anchor = right_anchors.get("entry")
        if exit_anchor is None or entry_anchor is None:
            continue
        exit_point = (origins[index][0] + exit_anchor[0], origins[index][1] + exit_anchor[1])
        entry_point = (origins[index + 1][0] + entry_anchor[0], origins[index + 1][1] + entry_anchor[1])
        if exit_point[1] == entry_point[1] and exit_point[0] != entry_point[0]:
            divergences.append(
                Divergence(
                    text,
                    config,
                    index,
                    f"gap 0 at seam (exit {exit_point})",
                    f"entry {entry_point} ({left.name} -> {right.name})",
                    "gap",
                )
            )
            return


def record_k3_half(half: str, payload: dict) -> None:
    """Merge one shaper's half of the K3 semantics verdict into prototype/out/budget.json (PLAN.md execution-order step 8 wants the consolidated verdict in the budget artifact, not in a scratch file). The verdict trips only when both halves are present and either failed."""
    budget_path = PROTOTYPE_DIR / "out" / "budget.json"
    if not budget_path.exists():
        return
    budget = json.loads(budget_path.read_text())
    k3 = budget.get("kill_criteria", {}).get("K3_semantics")
    if not isinstance(k3, dict):
        k3 = {
            "criterion": "any HarfBuzz-vs-settle or CoreText-vs-HarfBuzz divergence attributable to within-lookup sequential substitution or default-ignorable handling that cannot be fixed inside the section 7 encoding"
        }
    k3[half] = payload
    harfbuzz = k3.get("harfbuzz")
    coretext = k3.get("coretext")
    if isinstance(harfbuzz, dict) and isinstance(coretext, dict):
        k3["tripped"] = not (harfbuzz.get("pass") and coretext.get("pass"))
    budget.setdefault("kill_criteria", {})["K3_semantics"] = k3
    budget_path.write_text(json.dumps(budget, indent=2) + "\n")


def print_divergence_table(divergences: list[Divergence]) -> None:
    print()
    print(
        f"Divergence table (first divergent position per sequence and configuration; showing {min(len(divergences), MAX_PRINTED_DIVERGENCES)} of {len(divergences)}):"
    )
    header = f"{'input (hex)':<22} {'config':<8} {'pos':>3}  {'kind':<13} {'expected':<44} got"
    print(header)
    print("-" * len(header))
    for divergence in divergences[:MAX_PRINTED_DIVERGENCES]:
        print(
            f"{hex_codepoints(divergence.text):<22} {divergence.config:<8} {divergence.position:>3}  {divergence.kind:<13} {divergence.expected:<44} {divergence.got}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--font",
        type=Path,
        default=DEFAULT_FONT,
        help="font to shape through (default: prototype/out/Proto.otf)",
    )
    parser.add_argument(
        "--mechanics-only",
        action="store_true",
        help="skip the settle.py oracle comparison; just exercise enumeration, shaping, and the ZWNJ structural checks",
    )
    parser.add_argument(
        "--max-length", type=int, default=5, help="maximum sequence length to enumerate (default: 5)"
    )
    args = parser.parse_args()

    if not args.font.exists():
        if args.font == DEFAULT_FONT:
            print(
                f"error: {args.font} does not exist; build it first (uv run python prototype/build.py) or pass --font",
                file=sys.stderr,
            )
        else:
            print(f"error: {args.font} does not exist", file=sys.stderr)
        return 2

    mechanics_only = args.mechanics_only or args.font.resolve() != DEFAULT_FONT.resolve()
    oracle = None
    coverage = None
    if not mechanics_only:
        try:
            oracle = Oracle()
        except OracleUnavailable as error:
            print(f"error: oracle unavailable for the prototype font: {error}", file=sys.stderr)
            return 2
        try:
            coverage = RuleCoverage()
        except Exception as error:
            print(
                f"error: cannot build the settlement table for the rule-coverage check: {error}",
                file=sys.stderr,
            )
            return 2

    shaper = Shaper(args.font)
    alphabet, alphabet_source = load_alphabet()
    anchor_lookup = load_anchor_lookup() if oracle else None

    sequences = list(enumerate_sequences(alphabet, args.max_length))
    divergences: list[Divergence] = []
    comparison_modes: set[str] = set()
    oracle_errors: list[str] = []
    shaping_runs = 0

    for text in sequences:
        for config, features in FALLBACK_FEATURE_MATRIX.items():
            shaped = shaper.shape(text, features)
            shaping_runs += 1
            check_zwnj_structure(text, config, shaper, shaped, divergences)
            if ZWNJ in text:
                check_split_buffer(text, config, features, shaper, shaped, divergences)
            if oracle is not None:
                try:
                    expected = oracle.expected_glyph_names(text, features)
                except OracleUnavailable as error:
                    oracle_errors.append(str(error))
                    print(f"error: {error}", file=sys.stderr)
                    oracle = None
                else:
                    check_oracle(text, config, shaped, expected, divergences, comparison_modes)
                    if coverage is not None:
                        coverage.record(text, config, features, expected, divergences)
            if anchor_lookup is not None and oracle is not None:
                check_join_gaps(text, config, shaper, shaped, anchor_lookup, divergences)

    by_kind: dict[str, int] = {}
    for divergence in divergences:
        by_kind[divergence.kind] = by_kind.get(divergence.kind, 0) + 1

    print("HarfBuzz conformance gate")
    print(f"font: {args.font}")
    print(
        f"alphabet ({len(alphabet)} symbols, source: {alphabet_source}): {' '.join(f'{ord(ch):04X}' for ch in alphabet)}"
    )
    print(
        f"sequences: {len(sequences)} (length 1-{args.max_length}); configurations: {', '.join(FALLBACK_FEATURE_MATRIX)}; shaping runs: {shaping_runs}"
    )
    if mechanics_only:
        print(
            "mode: mechanics only (no oracle comparison; findings are informational and the exit code stays 0)"
        )
    else:
        print("mode: full gate against prototype/settle.py")
    if anchor_lookup is None and oracle is not None:
        print("gap check: skipped (no anchors_in_font_units/anchors_for_glyph accessor found on spec/settle)")
    elif anchor_lookup is not None:
        print("gap check: active (spec.anchors_in_font_units)")
    for mode in sorted(comparison_modes):
        print(f"note: {mode}")
    uncovered = coverage.uncovered() if coverage is not None else []
    if coverage is not None:
        if uncovered:
            print(
                f"rule coverage: {len(uncovered)} of {len(coverage.rules)} settlement rules never exercised:"
            )
            for rule in uncovered:
                print(
                    f"  {rule.input_glyph} | backtrack {rule.backtrack} | look1 {rule.look1} | look2 {rule.look2} -> {rule.outcome}"
                )
        else:
            print(f"rule coverage: all {len(coverage.rules)} settlement rules exercised")
    print()
    if divergences:
        print(f"RESULT: {len(divergences)} divergent sequence runs")
        for kind, count in sorted(by_kind.items()):
            print(f"  {kind}: {count}")
        print_divergence_table(divergences)
    else:
        print("RESULT: PASS — no divergences")

    if oracle_errors:
        return 2
    if mechanics_only:
        return 0

    passed = not divergences and not uncovered
    summary = {
        "font": str(args.font),
        "alphabet": [f"{ord(ch):04X}" for ch in alphabet],
        "max_length": args.max_length,
        "sequences": len(sequences),
        "shaping_runs": shaping_runs,
        "rules": len(coverage.rules) if coverage is not None else None,
        "uncovered_rules": len(uncovered),
        "divergences": len(divergences),
        "divergences_by_kind": by_kind,
        "pass": passed,
    }
    summary_path = PROTOTYPE_DIR / "out" / "conform_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"summary written to {summary_path}")
    record_k3_half(
        "harfbuzz",
        {
            "pass": passed,
            "shaping_runs": shaping_runs,
            "max_length": args.max_length,
            "divergences": len(divergences),
            "rules_exercised": (len(coverage.rules) - len(uncovered)) if coverage is not None else None,
        },
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

"""The witness stage's rule replay: `check_rule_certificates` settles every certificate the crate wrote beside a configuration's rules through the shared settle walk and asserts the rule first-matches at some position of it, under the exact first-match-wins the emitted lookup compiles to — `_matched_windows` over `_first_matching_rule`, with the rules renamed into the configuration's marker-folded space by `_renamed_rules_by_input`. `run_m1.run_rule_witnesses` runs it over the tables the build just folded, and it is the realizability half of the dead-rule alarm; the crate's fold (`fold::assert_outcome_partition`) is the other half.

The replay reads the emitter's rule fold — `emit_gsub._renamed`, and `_FoldedRule` as the type a folded rule arrives as — and it lives in a module of its own so that conform.py, the module `oracle_cache.ORACLE_ROW_CODE_PATHS` is cut from, never names emit_gsub.py. The oracle row cache stamps its whole store against the comparison's import closure at module grain (rebuild/test_oracle_code_closure.py walks it from `_compare_row` and `_SettledWindowWalk`, `if TYPE_CHECKING:` imports included), so a module conform.py imports for a purpose the comparison never exercises still drops every stored row verdict when it moves; with the replay here, an edit to emit_gsub.py alone leaves the store standing. The comparison's two entry points stay in conform.py because `ORACLE_ENTRY_MODULES` walks from there, and what this module reads back out of conform.py — the raw-label formation, the window slots, the settle walk, the memo file and the report — is inside that closure either way. conform.py reaches `table` and `kernel_io` through `kernel_exec` rather than through anything here, so both stay stamped as the settlement inputs they are.

On the tables side this module needs no roster of its own: `fingerprint.COMPARISON_CODE_MODULES` names only oracle.py, so belt.py rides `table_code_paths` exactly as conform.py does, and the review surface's per-unit store leaves it out through `unit_cache.PIPELINE_NON_SURFACE_MODULES`, since the surface build never imports it. The belt sweep itself, `run_conformance` and its configuration workers, is conform.py's.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Iterable, Mapping

from rebuild.pipeline import conform, kernel_exec, settle
from rebuild.pipeline.emit_gsub import _FoldedRule, _renamed
from rebuild.pipeline.model import ResolvedSpec, raw_rename_map

if TYPE_CHECKING:
    from rebuild.pipeline.table import Rule


def _first_matching_rule(
    rules_by_input: Mapping[str, list[tuple[int, Rule | _FoldedRule]]],
    label: str,
    left: str,
    right1: str,
    right2: str,
    right3: str,
    right4: str,
    representatives: Mapping[str, str] | None = None,
) -> int | None:
    """First-match-wins over the config's renamed rules for one window — the exact semantics the emitted FEA compiles to. A deep slot holding a class token is tested through its renamed representative member (`representatives`, from the table's `conform._DeepTokenIndex`): exact, not heuristic, because the build asserts every emitted look class holds a token's members all-in or all-out."""
    if representatives:
        right3 = representatives.get(right3, right3)
        right4 = representatives.get(right4, right4)
    for rule_index, rule in rules_by_input.get(label, ()):
        if rule.backtrack is not None and left not in rule.backtrack:
            continue
        if rule.look1 is not None and right1 not in rule.look1:
            continue
        if rule.look2 is not None and right2 not in rule.look2:
            continue
        look3 = getattr(rule, "look3", None)
        if look3 is not None and right3 not in look3:
            continue
        look4 = getattr(rule, "look4", None)
        if look4 is not None and right4 not in look4:
            continue
        return rule_index
    return None


def _matched_windows(spec, text, features, guard_verdicts, expected, rules_by_input, deep_index=None):
    """Replay the settlement lookup's view of one string: yield (position, window key, first-matching rule index or None) per letter slot, with labels and rules in the config's renamed (marker-folded) space and the left slot read from the settled stream — the exact first-match-wins semantics the emitted FEA compiles to. The window slots are the raw ones `conform._window_rights` reads; nothing here consults which slots the table chose to split, because a rule that dropped one matches whatever stands at it. `deep_index` is the table's `conform._DeepTokenIndex`; token resolution is a separate step strictly after `conform._window_rights`, which reads raw labels, and needs the settled left this loop holds — with no index the deep slots stay raw labels, which on a class-grain table realize no row."""
    try:
        labels = conform.raw_labels(spec, text, features, guard_verdicts)
    except ValueError:
        return
    settled = conform.normalize_expected(list(expected))
    if len(labels) != len(settled):
        return
    for index, label in enumerate(labels):
        if label in conform._WINDOW_BOUNDARIES:
            continue
        if index == 0:
            left = conform._EDGE_LABEL
        elif labels[index - 1] in conform._WINDOW_BOUNDARIES:
            left = labels[index - 1]
        else:
            left = settled[index - 1]
        right1, right2, right3, right4 = conform._window_rights(labels, index)
        if deep_index is not None:
            right3, right4 = deep_index.resolve(label, left, right1, right2, right3, right4)
        matched = _first_matching_rule(
            rules_by_input,
            label,
            left,
            right1,
            right2,
            right3,
            right4,
            representatives=deep_index.representatives if deep_index is not None else None,
        )
        yield index, (label, left, right1, right2, right3, right4), matched


def _renamed_rules_by_input(spec, features, decision) -> dict[str, list[tuple[int, Rule | _FoldedRule]]]:
    renames = raw_rename_map(spec, frozenset(features))
    rules_by_input: dict[str, list[tuple[int, Rule | _FoldedRule]]] = {}
    for index, rule in enumerate(getattr(decision, "rules", ())):
        renamed = _renamed(rule, renames)
        rules_by_input.setdefault(renamed.input_glyph, []).append((index, renamed))
    return rules_by_input


def _token_text(spec: ResolvedSpec, tokens: Iterable[str]) -> str:
    """Render a certificate's token stream (rune family, ligature-rune, or boundary-label tokens) back to codepoints; ligature runes expand to their component sequence, so raw_labels' greedy formation re-folds them to the intended labels."""
    boundary_codepoints = {
        {"space": "space", "zwnj": "uni200C", "namer-dot": "periodcentered"}[name]: token.codepoint
        for name, token in spec.registry.boundary_tokens.items()
    }
    chars: list[str] = []
    for token in tokens:
        if token in boundary_codepoints:
            chars.append(chr(boundary_codepoints[token]))
            continue
        rune = spec.runes[token]
        for part in rune.sequence or (token,):
            codepoint = spec.runes[part].codepoint
            if codepoint is None:
                raise ValueError(
                    f"ligature rune {token} names {part}, which carries no codepoint — this expansion is one level deep only"
                )
            chars.append(chr(codepoint))
    return "".join(chars)


def rule_signature(rule) -> str:
    slots = ", ".join(
        f"{name}={list(value) if value is not None else 'any'}"
        for name, value in (
            ("backtrack", rule.backtrack),
            ("look1", rule.look1),
            ("look2", rule.look2),
            ("look3", getattr(rule, "look3", None)),
            ("look4", getattr(rule, "look4", None)),
        )
    )
    return f"{rule.input_glyph} [{slots}] -> {rule.outcome}"


def check_rule_certificates(
    spec, features, decision, guard_verdicts=None, memo: conform.SettleMemoFile | None = None
) -> conform.WitnessReport:
    """The realizability half of rule coverage, settled rather than searched: every rule the table carries arrived with a certificate — the token stream the crate closed off the shortest producer chain of a row the rule first-matches (`certificate.rs`) — and this settles each certificate's text through the crate and asserts the rule first-matches at some position of it, under the exact first-match-wins the emitted lookup compiles to (`_matched_windows`). The sibling claim, that no rule sits behind another and can never win a window, is the crate's fold: `fold::assert_outcome_partition` refuses a table with a never-first rule before any certificate is closed.

    What the settle proves is the pins. A certificate's prefix is the chain of rows whose outcomes put the rule's left state in place, and the fixpoint only ever pinned those rows' slots — a settled left is reachable alongside the right1 that was the producing window's right2, and the deeper slots ride the allowed-sets. Settling the text from the run edge re-derives that left from nothing, so a pin the worklist got wrong settles the certificate to some other left, which first-matches some other rule, and the rule is reported. A table whose certificates do not cover its rules — a count that differs from the rule count — fails every rule, since nothing vouches for them.

    O(rules) settles and no search: the texts prefill one `conform._SettledWindowWalk` in waves, and `memo` is the configuration's shared settle memo (`conform.settle_memo_files`), keyed per family the way the oracle row cache is, so a window the belt or the oracle has already settled since the runes it names last moved costs a dict probe and the windows this check settles are handed on to them. That key is where the window-locality theorem reaches the certificates: a certificate names a handful of families, its windows survive exactly as long as those families' keys do, and a rune edit re-settles only the certificates naming an edited family.
    """
    if guard_verdicts is None:
        guard_verdicts = kernel_exec.guard_sweep(spec)
    features = frozenset(features)
    report = conform.WitnessReport(config=decision.config, rules=len(decision.rules))
    certificates = tuple(getattr(decision, "certificates", ()))
    if len(certificates) != len(decision.rules):
        report.failures.append(
            f"{decision.config}: the table carries {len(certificates)} certificate(s) for {len(decision.rules)} rule(s), so nothing vouches for its rules — a build-tables run that folded the rules writes one certificate per rule beside them"
        )
        return report
    glyph_names = {cell: settle.cell_label(spec, cell) for cell in decision.reachable_cells()}
    rules_by_input = _renamed_rules_by_input(spec, features, decision)
    walker = conform._SettledWindowWalk(
        spec, features, glyph_names, guard_verdicts, on_error="drop", memo=memo
    )
    texts: list[str | None] = []
    for index, tokens in enumerate(certificates):
        try:
            texts.append(_token_text(spec, tokens))
        except (KeyError, ValueError) as error:
            texts.append(None)
            report.failures.append(
                f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): its certificate {list(tokens)} does not render as text ({error})"
            )
    with suppress(settle.SettleError):
        walker.prefill(sorted({text for text in texts if text}))
    for index, text in enumerate(texts):
        if text is None:
            continue
        try:
            _settled, names = walker.walk(text)
        except settle.SettleError as error:
            report.failures.append(
                f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): the crate refused its certificate {text!r} ({error})"
            )
            continue
        fired = [
            matched
            for _position, _window, matched in _matched_windows(
                spec, text, features, guard_verdicts, names, rules_by_input
            )
        ]
        if index in fired:
            report.witnessed[index] = text
        else:
            report.failures.append(
                f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): its certificate {text!r} fires rules {fired} and never this one"
            )
    if memo is not None:
        walker.save_memo()
    report.served = walker.memo_windows
    report.fresh = walker.fresh_windows
    return report

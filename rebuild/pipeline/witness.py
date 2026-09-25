"""The witness stage's rule replay. `check_rule_certificates` settles each certificate the crate wrote beside a configuration's rules and checks that its rule is the first to match at some position, under the first-match-wins semantics the emitted lookup compiles to (`_matched_windows` over `_first_matching_rule`, with the rules renamed into the configuration's marker-folded names by `_renamed_rules_by_input`). `run_m1.run_rule_witnesses` runs it over the tables the build just folded. Together with the crate's fold check (`fold::assert_outcome_partition`), it fails the build on a rule that can never fire.

The replay imports the emitter's rule renaming (`emit_gsub._renamed`, with `_FoldedRule` as the type of a renamed rule), so it lives here and not in conform.py. `oracle_cache.ORACLE_ROW_CODE_PATHS` must list every module reachable from conform.py, which defines the comparison's entry points `_compare_row` and `_SettledWindowWalk`. rebuild/test_oracle_code_closure.py checks this by walking imports at module grain from `ORACLE_ENTRY_MODULES`, `if TYPE_CHECKING:` imports included. An edit to any listed module drops every stored row verdict, so keeping the replay here keeps emit_gsub.py off that list. What this module uses from conform.py (raw-label formation, the window slots, the settle walk, the memo file, and the report) is inside that closure anyway.

`fingerprint.COMPARISON_CODE_MODULES` names only oracle.py and oracle_positions.py, so witness.py is part of `table_code_paths`, as conform.py is. `unit_cache.PIPELINE_NON_SURFACE_MODULES` leaves it out of the review unit cache's stamp, because the surface build never imports it. The belt, `run_conformance` and its configuration workers, is in conform.py.
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
    """Return the index of the first of the configuration's renamed rules that matches one window, as the emitted FEA does, or None. A deep slot holding a class token is tested through its renamed representative member (`representatives`, the map a `conform._DeepTokenIndex` holds). That is exact, and a raw member label matches the same rules, because `fold::assert_deep_class_unions` checks that every emitted look class holds all of a token's members or none."""
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
    """Replay the settlement lookup over one string: yield (position, window key, index of the first matching rule or None) for each letter position, as the emitted FEA matches. Labels and rules are in the configuration's renamed (marker-folded) names, and the left slot comes from the settled stream. Yields nothing when the text cannot be labeled or `expected` has a different length. The window slots are the raw ones `conform._window_rights` reads. Which slots the table split does not matter here, because a rule that dropped a slot matches whatever is there. `deep_index`, a `conform._DeepTokenIndex` built from the table, maps the deep slots to class tokens after `conform._window_rights` has read the raw labels, and it needs the settled left this loop holds. Without it the deep slots stay raw labels. The first matching rule is the same either way (see `_first_matching_rule`), so `check_rule_certificates` passes none. Only the yielded window key differs: where a class-grain table's row holds a class token, the key with the raw label does not match that row."""
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
    """Return the text for a certificate's token stream (rune, ligature-rune, or boundary-label tokens). A ligature rune expands to its components, and `raw_labels`' greedy formation forms them back into the intended labels."""
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
    """Check that every rule in `decision` fires on its certificate, and return the report. The crate writes one certificate per rule (`certificate.rs`): a token stream built from the shortest producer chain of a row the rule first-matches. This settles each certificate's text through the crate and checks that the rule is the first to match at some position (`_matched_windows`). The other half, that no rule is shadowed everywhere by earlier rules, is the crate's fold check: `fold::assert_outcome_partition` fails the table before any certificate is built when some rule is never the first to match.

    Settling also checks the fixpoint's pins. A certificate's prefix is the chain of rows whose outcomes produce the rule's left state, and the fixpoint pinned only those rows' slots: a settled left is reachable with a right1 equal to the producing window's right2, and the deeper slots are limited by the allowed sets. Settling the text from the run edge derives that left again, so a wrong pin settles the certificate to a different left, a different rule matches, and the rule is reported. When the number of certificates differs from the number of rules, the report gets one failure and no rule is witnessed.

    The cost is O(rules) settles and no search. The texts are prefilled in waves through one `conform._SettledWindowWalk`. `memo` is the configuration's shared settle memo (`conform.settle_memo_files`), keyed per family as the oracle row cache is, so a window another stage has settled since the runes it names last changed costs one lookup, and the windows settled here are kept for the other stages. By the window-locality theorem (doc/rebuild-design.md §10), a rune edit re-settles only the certificates that name an edited family. The certificates ask for a small part of the memo file, so the walk loads only the rows its texts can ask for (`load_only_asked_by`, with the ask set fixed from the certificate texts before the first wave). It writes the windows it settled as a part at the memo's `write_path`, and the caller merges the part into the file, so a memo passed here must name a `write_path`. `served` on the report counts the rows the load kept, including stale rows the walk never serves. A certificate text that fails to tokenize is reported against its own rule by the per-text walk below. `load_only_asked_by` and `prefill` share one `suppress` so that the error reaches that walk and not the caller. A walk whose restriction was cut short this way loads the whole file and skips the prefill, which costs time and memory but gives the same result.
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
    pile = sorted({text for text in texts if text})
    with suppress(settle.SettleError):
        if memo is not None:
            walker.load_only_asked_by(pile)
        walker.prefill(pile)
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
    report.unasked = walker.unasked_windows
    report.fresh = walker.fresh_windows
    return report

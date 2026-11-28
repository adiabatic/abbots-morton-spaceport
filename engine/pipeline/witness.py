"""The witness stage's rule replay. `check_rule_certificates` settles each certificate the crate wrote beside a configuration's rules and checks that its rule is the first to match at some position (a guard rule's certificate under the configuration that keeps its window live), under the first-match-wins semantics the emitted lookup compiles to (`_matched_windows` over `_first_matching_rule`, with the rules renamed into the configuration's marker-renamed names by `_renamed_rules_by_input`). `run_build.run_rule_witnesses` runs it over the tables the build just folded. Together with the crate's fold check (`fold::assert_outcome_partition`), it fails the build on a rule that can never fire.

The replay imports the emitter's rule renaming (`emit_gsub._renamed`), so it lives here and not in conform.py. `settle_memo_keys.SETTLE_MEMO_CODE_PATHS` must list every module reachable from conform.py, which defines the settle memo's walk `_SettledWindowWalk`, except the width and telemetry helpers in the closure test's stop-set (`WIDTH_AND_TELEMETRY_MODULES`). engine/test_settle_memo_code_closure.py checks this by walking imports at module grain from `WALK_ENTRY_MODULES`, `if TYPE_CHECKING:` imports included. An edit to any listed module drops every settle memo, so keeping the replay here keeps emit_gsub.py off that list. What this module uses from conform.py (raw-label formation, the window slots, the settle walk, the memo, and the report) is inside that closure anyway.

`fingerprint.COMPARISON_CODE_MODULES` does not name witness.py, so it is part of `table_code_paths`, as conform.py is. The conformance sweep, `run_conformance` and its configuration workers, is in conform.py.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Iterable, Mapping, NamedTuple

from engine.pipeline import conform, kernel_exec, settle
from engine.pipeline.emit_gsub import _config_features, _renamed
from engine.pipeline.model import CellId, ResolvedSpec, raw_rename_map

GUARD_MARKER = "#guard"
"""The first token of a guard rule's certificate (`certificate::GUARD_MARKER`). The second names the configuration the rest of the stream settles under."""

if TYPE_CHECKING:
    from engine.pipeline.table import DecisionTable


class _RuleSlots(NamedTuple):
    """One renamed rule's context slots as `_first_matching_rule` tests them: each slot's members as a set, so a membership test hashes the label instead of comparing it with every member, or None where the rule matches any label."""

    backtrack: frozenset[str] | None
    look1: frozenset[str] | None
    look2: frozenset[str] | None
    look3: frozenset[str] | None
    look4: frozenset[str] | None


def _first_matching_rule(
    rules_by_input: Mapping[str, list[tuple[int, _RuleSlots]]],
    label: str,
    left: str,
    right1: str,
    right2: str,
    right3: str,
    right4: str,
) -> int | None:
    """Return the index of the first of the configuration's renamed rules that matches one window, as the emitted FEA does, or None. The deep slots hold raw member labels, which match the rules a class-grain table folds, because `fold::assert_deep_class_unions` checks that every emitted lookahead class holds all of a deep class's members or none."""
    for rule_index, (backtrack, look1, look2, look3, look4) in rules_by_input.get(label, ()):
        if backtrack is not None and left not in backtrack:
            continue
        if look1 is not None and right1 not in look1:
            continue
        if look2 is not None and right2 not in look2:
            continue
        if look3 is not None and right3 not in look3:
            continue
        if look4 is not None and right4 not in look4:
            continue
        return rule_index
    return None


def _matched_windows(spec, text, features, guard_verdicts, expected, rules_by_input):
    """Replay the settlement lookup over one string: yield (position, window key, index of the first matching rule or None) for each letter position, as the emitted FEA matches. Labels and rules are in the configuration's marker-renamed names, and the left slot comes from the settled stream. Yields nothing when the text cannot be labeled or `expected` has a different length. The window slots are the raw ones `conform._window_rights` reads. Which slots the table split does not matter here, because a rule that dropped a slot matches whatever is there, and a deep slot holds its raw label, which matches the rules folded from a class-grain table (`_first_matching_rule`). So where a class-grain table's row holds a class token, the yielded key, which holds the raw label, does not match that row."""
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
        matched = _first_matching_rule(rules_by_input, label, left, right1, right2, right3, right4)
        yield index, (label, left, right1, right2, right3, right4), matched


def _renamed_rules_by_input(spec, features, decision) -> dict[str, list[tuple[int, _RuleSlots]]]:
    renames = raw_rename_map(spec, frozenset(features))
    rules_by_input: dict[str, list[tuple[int, _RuleSlots]]] = {}
    for index, rule in enumerate(getattr(decision, "rules", ())):
        renamed = _renamed(rule, renames)
        slots = _RuleSlots(
            *(
                frozenset(members) if members is not None else None
                for members in (
                    renamed.backtrack,
                    renamed.look1,
                    renamed.look2,
                    getattr(renamed, "look3", None),
                    getattr(renamed, "look4", None),
                )
            )
        )
        rules_by_input.setdefault(renamed.input_glyph, []).append((index, slots))
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


def guard_sources(tables: Mapping[str, DecisionTable | tuple]) -> dict[str, frozenset[CellId]]:
    """The `sources` `check_rule_certificates` takes: each configuration of `tables`, whose entries are decision tables alone or with their join tables, mapped to its decision table's reachable cells, the settled glyphs a guard certificate's walk under that configuration names."""
    return {
        config: (entry[0] if isinstance(entry, (tuple, list)) else entry).reachable_cells()
        for config, entry in tables.items()
    }


def check_rule_certificates(
    spec,
    features,
    decision,
    guard_verdicts=None,
    memo: conform.SettleMemoFile | None = None,
    sources: Mapping[str, frozenset[CellId]] | None = None,
) -> conform.WitnessReport:
    """Check that every rule in `decision` fires on its certificate, and return the report. The crate writes one certificate per rule (`certificate.rs`): a token stream built from the shortest row chain of a row the rule first-matches. This settles each certificate's text through the crate and checks that the rule is the first to match at some position (`_matched_windows`). The other half, that no rule is shadowed everywhere by earlier rules, is the crate's fold check: `fold::assert_outcome_partition` fails the table before any certificate is built when some rule is unreachable because no replayed row first-matches it.

    Settling also checks the fixpoint's slot restrictions. A certificate's prefix is the chain of rows whose outcomes produce the rule's left state, and the fixpoint restricted only those rows' slots: a settled left is reachable with a right1 equal to the producing window's right2, and the deeper slots are limited by the allowed sets. Settling the text from the run edge derives that left again, so a wrong slot restriction settles the certificate to a different left, a different rule matches, and the rule is reported. When the number of certificates differs from the number of rules, the report gets one failure and no rule is witnessed.

    A guard rule's certificate is `[GUARD_MARKER, source, tokens…]` (`certificate::guard_certificate`). The rule exists only because `source`, another configuration, keeps a window live that this configuration's rules would otherwise answer wrongly ahead of that configuration's own rule in the shipped order (`engine/kernel-rs/src/crossconfig.rs`). So its text is settled under `source`'s features and labeled in `source`'s marker copies, the stream that window occurs in, and this configuration's rules, spelled the same way, must fire the rule at some position. `sources` maps each configuration to its decision table's reachable cells (`guard_sources`), which name the settled glyphs of that walk, so a caller hands over every configuration's name without its whole table; a guard certificate naming a configuration missing from it fails. Guard texts are settled without the memo, since the memo belongs to this configuration, and `guards` on the report counts the guard rules witnessed.

    The cost is O(rules) settles and no search. The texts are prefilled in waves through one `conform._SettledWindowWalk`. `memo` is the configuration's shared settle memo (`conform.settle_memo_files`), keyed per family (`settle_memo_keys`), so a window another stage has settled since the runes it names last changed costs one lookup (the window locality rule, doc/engine-design.md §10). The certificates ask for a small part of the memo, so the walk loads only the rows its texts can ask for (`load_only_asked_by`, with the ask set fixed from the certificate texts before the first wave). Its memo must be read-only (`conform.SettleMemoFile.read_only`): the walk serves the rows the load kept, keeps the windows it settles for the rest of the check, and writes neither the file nor a part, so a window the file lacks is settled again on every check. `served` on the report counts the rows the load kept, including stale rows the walk never serves. A certificate text that fails to tokenize is reported against its own rule by the per-text walk below. `load_only_asked_by` and `prefill` share one `suppress` so that the error reaches that walk and not the caller. A walk whose restriction was cut short this way loads the whole file and skips the prefill, which costs time and memory but gives the same result.
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
    guards: dict[int, str] = {}
    source_cells: Mapping[str, frozenset[CellId]] = sources or {}
    for index, tokens in enumerate(certificates):
        if tokens and tokens[0] == GUARD_MARKER:
            if len(tokens) < 2 or tokens[1] not in source_cells:
                texts.append(None)
                report.failures.append(
                    f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): its guard certificate {list(tokens)} names no configuration this check was given"
                )
                continue
            guards[index] = tokens[1]
            tokens = tokens[2:]
        try:
            texts.append(_token_text(spec, tokens))
        except (KeyError, ValueError) as error:
            texts.append(None)
            report.failures.append(
                f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): its certificate {list(tokens)} does not render as text ({error})"
            )
    pile = sorted({text for index, text in enumerate(texts) if text and index not in guards})
    with suppress(settle.SettleError):
        if memo is not None:
            walker.load_only_asked_by(pile)
        walker.prefill(pile)
    source_walkers: dict[str, conform._SettledWindowWalk] = {}
    for source in sorted(set(guards.values())):
        source_walkers[source] = conform._SettledWindowWalk(
            spec,
            _config_features(source),
            {cell: settle.cell_label(spec, cell) for cell in source_cells[source]},
            guard_verdicts,
            on_error="drop",
        )
        with suppress(settle.SettleError):
            source_walkers[source].prefill(
                sorted({text for index, text in enumerate(texts) if text and guards.get(index) == source})
            )
    for index, text in enumerate(texts):
        if text is None:
            continue
        source = guards.get(index)
        walk_features = features if source is None else _config_features(source)
        try:
            _settled, names = (walker if source is None else source_walkers[source]).walk(text)
        except settle.SettleError as error:
            report.failures.append(
                f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): the crate refused its certificate {text!r} ({error})"
            )
            continue
        fired = [
            matched
            for _position, _window, matched in _matched_windows(
                spec, text, walk_features, guard_verdicts, names, rules_by_input
            )
        ]
        if index in fired:
            report.witnessed[index] = text
            if source is not None:
                report.guards += 1
        else:
            settled_under = "" if source is None else f" settled under {source}"
            report.failures.append(
                f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): its certificate {text!r}{settled_under} fires rules {fired} and never this one"
            )
    report.served = walker.memo_windows
    report.unasked = walker.unasked_windows
    report.fresh = walker.fresh_windows
    return report

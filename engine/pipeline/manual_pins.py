"""The pin gate: checks every `data-expect` pin of the pin pages (`corpus.PIN_PAGES`) against the built font through the checker the font suite uses (`engine/validation/check.py`), and reports each failure.

The pages are the repository's transcription of what The Manual requires letters to look like (`site/the-manual.html`, and `site/index.html`, which quotes it). The root conftest checks the same pins against the fonts it publishes under `site/` with the same function (`check.check_pin`), so the suite and this gate agree on every pin they both check. `run_build` fails the build on any failure, when no pin is in scope, or when a pin in scope was not checked (`run_build_gates.manual_pin_gate_failure`). There is no waiver list. A failure means either the rune data breaks a requirement (fix the runes) or the pin mistranscribes it (fix the pin).

A pin the engine disagrees with pending the author's ruling carries `corpus.PENDING_ATTRIBUTE`. The gate counts such a pin in its summary and does not fail on it, whether it fails as expected or passes; the suite fails on a pending pin that passes, so the mark cannot go stale. A pin the vocabulary or the grammar rejects (`check.UnknownModifier`, a token that does not fit the text) fails the gate whether or not it is pending.

What the gate leaves out, each counted in the summary with its reason (`skipped`):

- A Junior run (`force-junior`): the build compiles Senior only, and the suite checks Junior runs against the built Junior.
- A pin with a Quikscript letter the spec's alphabet leaves out, which only the alphabet switch (`spec_load.RUNES_ENV`) does. Such pins are counted per missing letter (`blocked_by`, `sole_blocker`), so a narrowed build's summary shows which letters keep which pins out.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from engine.pipeline.labels import spec_alphabet
from engine.pipeline.model import ResolvedSpec
from engine.validation import check, corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
PS_NAMES_PATH = REPO_ROOT / "postscript_glyph_names.yaml"
QUIKSCRIPT_CODEPOINTS = range(0xE650, 0xE680)
JUNIOR_SKIP_REASON = "the build compiles Senior only; make test checks Junior runs against the built Junior"


@dataclass
class PageCounts:
    pins: int = 0
    checked: int = 0
    passed: int = 0
    failed: int = 0
    pending: int = 0
    pending_passing: int = 0
    invalid: int = 0
    blocked: int = 0
    junior_runs_skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(vars(self))


@dataclass
class ManualPinReport:
    pins_in_scope: int = 0
    replayed: int = 0
    pages: dict[str, PageCounts] = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    pending_passing: list[str] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    blocked_by: Counter = field(default_factory=Counter)
    sole_blocker: Counter = field(default_factory=Counter)

    @property
    def passed(self) -> bool:
        return not self.disagreements


def spec_codepoints(spec: ResolvedSpec) -> frozenset[int]:
    """Return the code points of the spec's alphabet (`labels.spec_alphabet`): its letters' and its boundary tokens'."""
    return frozenset(ord(ch) for ch in spec_alphabet(spec))


def _codepoint_display_names() -> dict[int, str]:
    names = yaml.safe_load(PS_NAMES_PATH.read_text())
    return {cp: name for name, cp in names.items()}


def missing_letters(pin: corpus.Pin, alphabet: frozenset[int]) -> set[int]:
    """Return the Quikscript letters of the pin's text that `alphabet` lacks. Every other character is in the font whatever the alphabet."""
    return {ord(ch) for ch in pin.text if ord(ch) in QUIKSCRIPT_CODEPOINTS and ord(ch) not in alphabet}


def _line(pin: corpus.Pin, text: str) -> str:
    return f"{pin.source} {pin.expect!r}: {text}"


def run_gate(font_path: Path, spec: ResolvedSpec, root: Path = REPO_ROOT) -> ManualPinReport:
    """Check every pin of the pin pages under `root` against the font at `font_path` and its glyph-facts sidecar (`check.glyph_facts_path`), leaving out what the module docstring lists."""
    report = ManualPinReport()
    alphabet = spec_codepoints(spec)
    fonts: dict[str, check.CheckFont | None] = {
        "senior": check.CheckFont(font_path, glyph_facts=check.glyph_facts_path(font_path)),
        "junior": None,
    }
    for pin in corpus.read_pins(root):
        counts = report.pages.setdefault(pin.page, PageCounts())
        counts.pins += 1
        missing = missing_letters(pin, alphabet)
        if missing:
            counts.blocked += 1
            report.skipped["pins with a letter the alphabet switch leaves out"] += 1
            report.blocked_by.update(missing)
            if len(missing) == 1:
                report.sole_blocker.update(missing)
            continue
        outcome = check.check_pin(fonts, pin, {"junior": JUNIOR_SKIP_REASON})
        counts.junior_runs_skipped += len(outcome.skipped_runs)
        if outcome.skipped_runs:
            report.skipped[f"Junior runs: {JUNIOR_SKIP_REASON}"] += len(outcome.skipped_runs)
        if outcome.checked_runs == 0:
            continue
        counts.checked += 1
        report.pins_in_scope += 1
        report.replayed += 1
        status = outcome.status
        if status == "pass":
            counts.passed += 1
        elif status == "fail":
            counts.failed += 1
            report.disagreements.append(_line(pin, outcome.message()))
        elif status == "invalid":
            counts.invalid += 1
            report.disagreements.append(_line(pin, outcome.message()))
        elif status == "pending":
            counts.pending += 1
            report.pending.append(_line(pin, f"pending ({pin.pending})"))
        else:
            counts.pending_passing += 1
            report.pending_passing.append(
                _line(pin, f"passes, but carries {corpus.PENDING_ATTRIBUTE}={pin.pending!r}")
            )
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
        "pins_in_scope": report.pins_in_scope,
        "replayed": report.replayed,
        "pages": {page: counts.as_dict() for page, counts in report.pages.items()},
        "skipped": dict(sorted(report.skipped.items())),
        "disagreements": list(report.disagreements),
        "pending": list(report.pending),
        "pending_passing": list(report.pending_passing),
        "top_blocking_letters": blockers,
        "pass": report.passed,
    }

"""Tests of the corpus build interface, `rebuild/review/corpus_exec.py`, the Python side of the Rust build of the review corpus's parent (`rebuild/CORPUS-PARENT-PORT-PLAN.md`): the switch, and the vocabulary and the fields the build input carries, which the crate's reader names too."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rebuild.review import corpus_exec, units_exec

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_SOURCE = REPO_ROOT / "rebuild" / "review-rs" / "src" / "corpus" / "input.rs"


def test_the_switch_reads_the_environment_and_the_build_runs_only_python(monkeypatch):
    """`corpus_exec.engine()` reads `AMS_CORPUS_BUILD_ENGINE` at call time, `python` by default, and refuses a value the switch does not know; while Python is the engine of record, the build runs only the Python engine."""
    monkeypatch.delenv(corpus_exec.ENGINE_VARIABLE, raising=False)
    assert corpus_exec.engine() == "python"
    monkeypatch.setenv(corpus_exec.ENGINE_VARIABLE, "cobol")
    with pytest.raises(ValueError, match="cobol"):
        corpus_exec.engine()
    for value in corpus_exec.SWITCH_VALUES:
        monkeypatch.setenv(corpus_exec.ENGINE_VARIABLE, value)
        assert corpus_exec.engine() == value
    assert corpus_exec.SWITCH_VALUES == ("python", "rust")
    assert corpus_exec.ENGINES == ("python",)


def _rust_names(name: str) -> tuple[str, ...]:
    source = INPUT_SOURCE.read_text(encoding="utf-8")
    block = re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", source, re.S)
    assert block is not None, f"input.rs no longer declares {name}"
    return tuple(re.findall(r'"([^"]+)"', block.group(1)))


def test_the_vocabulary_resolves_and_the_build_reads_the_same_names_and_fields():
    """Every `VOCABULARY` name is a constant the typed encoding can carry, and the crate's reader refuses an input without the same names (`corpus::input::VOCABULARY`), reads the same fields in the same order (`INPUT_FIELDS`) and the same format."""
    values = corpus_exec.vocabulary()
    assert tuple(values) == corpus_exec.VOCABULARY
    for name, value in values.items():
        assert units_exec.decode(json.loads(units_exec.compact(units_exec.encode(value)))) is not None, name
    assert _rust_names("VOCABULARY") == corpus_exec.VOCABULARY
    assert _rust_names("INPUT_FIELDS") == corpus_exec.INPUT_FIELDS
    source = INPUT_SOURCE.read_text(encoding="utf-8")
    assert re.search(rf'pub const INPUT_FORMAT: &str = "{re.escape(corpus_exec.INPUT_FORMAT)}";', source)

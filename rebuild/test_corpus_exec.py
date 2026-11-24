"""Tests of the corpus build interface, `rebuild/review/corpus_exec.py`, the Python side of the review crate's corpus build (`ams-review build`): the vocabulary and the fields the build input carries, which the crate's reader names too, and what `run_build` files and the build reports over the mini bundle's corpus, built and then rebuilt warm."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from rebuild.review import build, corpus_exec, units_exec
from rebuild.tools import cycle_timings

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_SOURCE = REPO_ROOT / "rebuild" / "review-rs" / "src" / "corpus" / "input.rs"
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_AUDIT = MINI / "audit.tsv"
MINI_FONT = MINI / "M1.otf"
REUSED = re.compile(r"reused (\d[\d,]*) of (\d[\d,]*) units from the cache")


def _rust_names(name: str) -> tuple[str, ...]:
    source = INPUT_SOURCE.read_text(encoding="utf-8")
    block = re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", source, re.S)
    assert block is not None, f"input.rs does not declare {name}"
    return tuple(re.findall(r'"([^"]+)"', block.group(1)))


def test_the_vocabulary_resolves_and_the_build_reads_the_same_names_and_fields():
    """Every `VOCABULARY` name is a constant the typed encoding can carry, and the crate's reader refuses an input without the same names (`corpus::input::VOCABULARY`), reads the same fields in the same order (`INPUT_FIELDS`) and the same format."""
    values = corpus_exec.vocabulary()
    assert tuple(values) == corpus_exec.VOCABULARY
    for name, value in values.items():
        assert json.loads(units_exec.compact(units_exec.encode(value))) is not None, name
    assert _rust_names("VOCABULARY") == corpus_exec.VOCABULARY
    assert _rust_names("INPUT_FIELDS") == corpus_exec.INPUT_FIELDS
    source = INPUT_SOURCE.read_text(encoding="utf-8")
    assert re.search(rf'pub const INPUT_FORMAT: &str = "{re.escape(corpus_exec.INPUT_FORMAT)}";', source)


def _build_mini(out: Path, mini_bundle) -> dict:
    return build.build_m1(
        out,
        audit_path=MINI_AUDIT,
        ledger_path=mini_bundle.ledger,
        subset_dir=MINI,
        after_font=MINI_FONT,
        spec_root=mini_bundle.spec_root,
        subset_pack=mini_bundle.subset_pack,
        jobs=2,
    )


def test_a_build_files_one_pool_record_and_a_warm_rebuild_reuses_every_unit(mini_bundle, tmp_path, capfd):
    """`build_m1` returns the manifest the build process wrote, and `corpus_exec.run_build` files one `corpus-build-rs` pool record for each build that ends done, its width the build's threads and its worker's reading a footprint. A second build over the first's corpus with the same inputs reuses every unit from the cache."""
    first = tmp_path / "first"
    manifest = _build_mini(first, mini_bundle)
    assert manifest == json.loads((first / "manifest.json").read_bytes())
    capfd.readouterr()
    warm = tmp_path / "warm"
    shutil.copytree(first, warm)
    _build_mini(warm, mini_bundle)
    match = REUSED.search(capfd.readouterr().err)
    assert match, "the warm build did not report what it reused"
    done, total = (int(group.replace(",", "")) for group in match.groups())
    assert done == total > 0, (done, total)
    records = [json.loads(line) for line in cycle_timings.JOURNAL.read_text(encoding="utf-8").splitlines()]
    pools = [record for record in records if record["unit"] == "corpus-build-rs"]
    assert [(record["width"], record["worker_reading"]) for record in pools] == [(2, "footprint")] * 2, pools

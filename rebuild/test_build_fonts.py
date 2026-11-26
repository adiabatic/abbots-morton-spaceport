"""Tests for `rebuild/tools/build_fonts.py`, the six-font command: the Senior currency check and copy over a synthetic root whose run_m1 records each test writes, the glyph-facts sidecar copied beside the Senior, and the shipped file names, which `rebuild/tools/suite_fonts.py` restates for the font suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from rebuild.pipeline import build_family, fingerprint, glyph_facts, run_m1
from rebuild.tools import build_fonts, cycle_paths, suite_fonts
from rebuild.validation import check
from rebuild.tools.artifact_cycle import run_m1_skip_files, run_m1_skip_fingerprint
from rebuild.tools.build_fonts import StaleSeniorError
from rebuild.tools.green_record import record_green

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fake_build(root: Path) -> None:
    """Lay out a synthetic root that holds a passing run_m1 build: the two fonts, a Stage A record, and a green record over the root's run_m1 skip key."""
    m1 = root / build_fonts.M1_OUT
    m1.mkdir(parents=True)
    for style, name in build_fonts.SENIOR_SOURCES.items():
        (m1 / name).write_bytes(f"senior {style}".encode())
    (m1 / glyph_facts.FILE_NAME).write_text('{"format": "ams-glyph-facts/1", "glyphs": {}}\n')
    (root / "rebuild" / "script.yaml").write_text("heights: {baseline: 0}\n")
    (root / fingerprint.CONTACT_ALLOW_LABEL).write_text("[]\n")
    fingerprint.write_stage_a(m1, {"data": "d", "pipeline_code": "p"})
    record_green(cycle_paths.RUN_M1_GREEN, run_m1_skip_fingerprint(root), files=run_m1_skip_files(root))


class TestTheSenior:
    def test_the_sources_are_the_files_run_m1_writes(self):
        assert REPO_ROOT / build_fonts.M1_OUT == run_m1.OUT_DIR
        assert build_fonts.SENIOR_SOURCES["Bold"] == run_m1.BOLD_FONT

    def test_a_senior_its_green_build_covers_is_copied_under_the_shipped_names(self, tmp_path):
        root = tmp_path / "root"
        _fake_build(root)
        out = tmp_path / "fonts"
        written = build_fonts.build(out, ("senior",), root)
        assert [path.name for path in written] == [
            "AbbotsMortonSpaceportSansSenior-Regular.otf",
            "AbbotsMortonSpaceportSansSenior-Bold.otf",
        ]
        assert [path.read_bytes() for path in written] == [b"senior Regular", b"senior Bold"]
        assert (
            check.glyph_facts_path(written[0]).read_bytes()
            == (root / build_fonts.M1_OUT / glyph_facts.FILE_NAME).read_bytes()
        )

    def test_a_missing_glyph_facts_sidecar_is_named(self, tmp_path):
        root = tmp_path / "root"
        _fake_build(root)
        (root / build_fonts.M1_OUT / glyph_facts.FILE_NAME).unlink()
        assert build_fonts.senior_staleness(root) == f"rebuild/out/m1/{glyph_facts.FILE_NAME} is missing"

    def test_a_moved_comparison_side_input_leaves_it_current(self, tmp_path):
        root = tmp_path / "root"
        _fake_build(root)
        (root / fingerprint.CONTACT_ALLOW_LABEL).write_text("- signature: moved\n")
        assert build_fonts.senior_staleness(root) is None

    def test_a_moved_build_input_refuses_before_anything_is_written_and_names_the_build_command(
        self, tmp_path
    ):
        root = tmp_path / "root"
        _fake_build(root)
        (root / "rebuild" / "script.yaml").write_text("heights: {baseline: 0, x-height: 5}\n")
        out = tmp_path / "fonts"
        with pytest.raises(StaleSeniorError) as raised:
            build_fonts.build(out, build_fonts.FONTS, root)
        message = str(raised.value)
        assert "rebuild/script.yaml" in message
        assert build_fonts.RUN_M1_COMMAND in message
        assert not out.exists()

    def test_a_missing_font_is_named(self, tmp_path):
        root = tmp_path / "root"
        _fake_build(root)
        (root / build_fonts.M1_OUT / run_m1.BOLD_FONT).unlink()
        assert build_fonts.senior_staleness(root) == "rebuild/out/m1/M1-Bold.otf is missing"

    def test_a_build_that_stopped_after_the_green_one_leaves_it_stale(self, tmp_path):
        root = tmp_path / "root"
        _fake_build(root)
        (root / build_fonts.M1_OUT / fingerprint.STAGE_A_FILENAME).unlink()
        assert "holds no Stage A record" in (build_fonts.senior_staleness(root) or "")

    def test_without_a_green_build_on_record_it_is_stale(self, tmp_path):
        root = tmp_path / "root"
        _fake_build(root)
        cycle_paths.RUN_M1_GREEN.unlink()
        assert build_fonts.senior_staleness(root) == "run_m1 has no green build on record"


def test_the_font_suite_names_the_fonts_and_the_sidecar_this_command_writes():
    """The root conftest and gate:make-test's key name the pin fonts through `suite_fonts`, which imports nothing; its names are the ones this command writes, and the sidecar's is the one the checker looks for beside the Senior and the one run_m1 writes under rebuild/out/m1."""
    assert suite_fonts.fonts_dir(REPO_ROOT) == build_fonts.DEFAULT_OUT_DIR
    for font, name in suite_fonts.FONT_FILES.items():
        assert name == build_family.font_file_name(font, "Regular")
    senior = suite_fonts.font_paths(REPO_ROOT)["senior"]
    assert suite_fonts.glyph_facts_path(REPO_ROOT) == check.glyph_facts_path(senior)
    assert check.glyph_facts_path(run_m1.OUT_DIR / "M1.otf").name == glyph_facts.FILE_NAME
    assert set(suite_fonts.BUILT_FONTS) <= set(build_fonts.FONTS)

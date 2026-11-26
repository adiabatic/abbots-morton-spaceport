"""Tests for `rebuild/tools/build_fonts.py`, the six-font publish: the Senior currency check and copy over a synthetic root whose run_m1 records each test writes, the build it runs first under `--build`, and the shipped file names and the glyph-facts sidecar's home, which `rebuild/tools/suite_fonts.py` restates for the font suite."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from rebuild.pipeline import build_family, fingerprint, glyph_facts, run_m1
from rebuild.tools import build_fonts, cycle_paths, suite_fonts
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

    def test_a_senior_its_green_build_covers_is_copied_under_the_shipped_names_without_its_sidecar(
        self, tmp_path
    ):
        """The fonts alone go to the published directory; the glyph-facts sidecar stays beside the build, where the font suite reads it."""
        root = tmp_path / "root"
        _fake_build(root)
        out = tmp_path / "fonts"
        written = build_fonts.build(out, ("senior",), root)
        assert [path.name for path in written] == [
            "AbbotsMortonSpaceportSansSenior-Regular.otf",
            "AbbotsMortonSpaceportSansSenior-Bold.otf",
        ]
        assert [path.read_bytes() for path in written] == [b"senior Regular", b"senior Bold"]
        assert sorted(path.name for path in out.iterdir()) == sorted(path.name for path in written)

    def test_build_runs_the_senior_build_only_while_the_senior_is_stale(self, tmp_path, monkeypatch):
        """`--build` runs the build command once when the Senior is stale and not at all when it is current, and refuses, naming the command, when the build fails."""
        root = tmp_path / "root"
        _fake_build(root)
        spawned: list[list[str]] = []
        returncodes = [0]

        def fake_run(argv, cwd, check):
            spawned.append(list(argv))
            return SimpleNamespace(returncode=returncodes[0])

        monkeypatch.setattr(build_fonts.subprocess, "run", fake_run)
        build_fonts.build_stale_senior(root)
        assert spawned == []
        staleness = ["its inputs moved"]
        monkeypatch.setattr(
            build_fonts, "senior_staleness", lambda at: staleness.pop(0) if staleness else None
        )
        build_fonts.build_stale_senior(root)
        assert [argv[-2:] for argv in spawned] == [["-m", build_fonts.RUN_M1_MODULE]]
        staleness[:] = ["its inputs moved"]
        returncodes[0] = 1
        with pytest.raises(StaleSeniorError, match="failed"):
            build_fonts.build_stale_senior(root)

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


def test_the_font_suite_names_the_fonts_this_command_writes_and_the_sidecar_run_m1_writes():
    """The root conftest and gate:make-test's key name the pin fonts through `suite_fonts`, which imports nothing; its names are the ones this command writes into site/, and its sidecar is the one run_m1 writes beside the Senior under rebuild/out/m1."""
    assert suite_fonts.fonts_dir(REPO_ROOT) == build_fonts.SITE_DIR
    for font, name in suite_fonts.FONT_FILES.items():
        assert name == build_family.font_file_name(font, "Regular")
    assert suite_fonts.glyph_facts_path(REPO_ROOT) == run_m1.OUT_DIR / glyph_facts.FILE_NAME

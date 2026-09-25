from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import derived_demote_oracle


def test_default_fea_is_the_senior_fea_the_build_writes_beside_its_font():
    senior_font = ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
    assert derived_demote_oracle.FEA_PATH == senior_font.with_suffix(".fea")


def test_fea_label_accepts_paths_inside_and_outside_the_repo(tmp_path: Path):
    outside = tmp_path / "Senior.fea"
    assert derived_demote_oracle._display_path(outside) == str(outside)
    assert derived_demote_oracle._display_path(ROOT / "site" / "Senior.fea") == "site/Senior.fea"

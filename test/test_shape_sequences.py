from pathlib import Path

import shape_sequences

ROOT = Path(__file__).resolve().parent.parent


def test_default_font_is_the_senior_font_the_build_writes_to_site():
    assert shape_sequences.DEFAULT_FONT == ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"

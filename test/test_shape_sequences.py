from quikscript_shaping_helpers import FONT_PATH

import shape_sequences


def test_default_font_is_the_senior_font_the_build_writes_to_site():
    assert shape_sequences.DEFAULT_FONT == FONT_PATH

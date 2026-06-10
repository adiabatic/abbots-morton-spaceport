"""Extractor unit tests for the pure pieces: basis enumeration, row serialization, header rendering, seam classification on corpus-known pairs, sampling stability, and small-run determinism. The broader validation suite (corpus replay, equivalence checks) lives in the parallel test module."""

import gzip

import pytest
import uharfbuzz as hb
from fontTools.ttLib import TTFont

from rebuild.baseline import alphabet, extract, model
from rebuild.baseline.classify import SeamClassifier
from rebuild.baseline.model import CONFIGS, FONT_PATH, Row, render_header, row_sort_key
from rebuild.baseline.shaper import Shaper


@pytest.fixture(scope="module")
def shaper() -> Shaper:
    return Shaper(FONT_PATH)


@pytest.fixture(scope="module")
def classifier() -> SeamClassifier:
    return SeamClassifier(FONT_PATH)


def test_alphabet_census():
    assert len(alphabet.ALPHABET) == 47
    assert len(alphabet.RUNE_CODEPOINTS) == 44
    assert list(alphabet.ALPHABET) == sorted(alphabet.ALPHABET)
    assert {0x0020, 0x00B7, 0x200C} < set(alphabet.ALPHABET)
    assert 0xE66E not in alphabet.ALPHABET and 0xE66F not in alphabet.ALPHABET
    names = alphabet.symbol_names()
    assert names[0xE650] == "qsPea"
    assert names[0xE67E] == "qsOoze"
    assert len(alphabet.alphabet_sha256()) == 64


def test_basis_size_arithmetic():
    assert alphabet.basis_size(1) == 47
    assert alphabet.basis_size(2) == 47 + 47**2
    assert alphabet.basis_size(4) == 4_985_760


def test_enumeration_is_canonical_order_and_matches_shards():
    enumerated = list(alphabet.enumerate_basis(2))
    assert len(enumerated) == alphabet.basis_size(2)
    assert enumerated == sorted(enumerated, key=lambda s: (len(s), s))
    concatenated = [
        string
        for length, first_index in alphabet.shard_keys(2)
        for string in alphabet.shard_strings(length, first_index)
    ]
    assert enumerated == concatenated


def test_row_round_trip():
    rows = [
        Row(
            codepoints=(0xE665,),
            glyphs=("qsMay",),
            clusters=(0,),
            seams=(),
            positions=((0, 0, 350),),
        ),
        Row(
            codepoints=(0xE665, 0x0020, 0xE652, 0x00B7),
            glyphs=("qsMay", "space", "qsTea.half", "periodcentered"),
            clusters=(0, 1, 2, 3),
            seams=("break", "break", "break"),
            positions=((0, 0, 350), (0, 0, 300), (0, 250, 250), (0, 0, 100)),
        ),
    ]
    for row in rows:
        assert Row.from_tsv(row.to_tsv()) == row
    assert row_sort_key(rows[0]) < row_sort_key(rows[1])


def test_header_rendering_and_parsing():
    header = render_header(
        "ss02+ss03",
        git_sha="ae9d08d",
        font_sha256="f" * 64,
        alphabet_sha256="a" * 64,
    )
    assert header[0] == f"# baseline-extract v{model.TOOL_VERSION}"
    assert header[1] == "# git_sha: ae9d08d"
    assert header[4] == "# config: ss02+ss03 (ss02=1 ss03=1)"
    assert header[-1] == "# columns: codepoints glyphs clusters seams positions"
    parsed = model.parse_header(header)
    assert parsed["config"] == "ss02+ss03"
    assert parsed["tool_version"] == model.TOOL_VERSION
    subset_header = render_header(
        "default",
        git_sha="ae9d08d",
        font_sha256="f" * 64,
        alphabet_sha256="a" * 64,
        subset="limit=100",
    )
    assert "# subset: limit=100" in subset_header
    assert subset_header[-1] == header[-1]


def test_configs_registry():
    assert list(CONFIGS) == [
        "default",
        "ss02",
        "ss03",
        "ss04",
        "ss05",
        "ss06",
        "ss07",
        "ss10",
        "ss02+ss03",
        "ss06+ss07",
        "ss02+ss03+ss05",
    ]
    assert CONFIGS["default"] == {}
    assert CONFIGS["ss02+ss03+ss05"] == {"ss02": True, "ss03": True, "ss05": True}


def test_classifier_heights(classifier):
    assert classifier.heights() == (0, 5, 6, 8)


HARFBUZZ_NAME_BYTE_LIMIT = 63

LONG_COMPILED_NAMES = (
    "qsExcite.en-y0.ex-y0.before-vertical.after-baseline-letter.noentry",
    "qsNo.alt.en-y0.ex-y0.after-it-and-vie.en-ext-1.ex-ext-1.ex-con-1",
    "qsUtter.alt.en-y5.ex-y0.reaches-way-back.en-ext-1.ex-ext-1.ex-con-2",
    "qsUtter.alt.en-y5.ex-y0.reaches-way-back.noentry.ex-ext-1.ex-con-2",
)


def test_font_has_compiled_names_past_the_harfbuzz_limit():
    tt_font = TTFont(str(FONT_PATH), lazy=True)
    long_names = tuple(
        sorted(
            name for name in tt_font.getGlyphOrder() if len(name.encode("utf-8")) > HARFBUZZ_NAME_BYTE_LIMIT
        )
    )
    assert long_names == LONG_COMPILED_NAMES


def test_harfbuzz_glyph_to_string_truncates_long_names():
    tt_font = TTFont(str(FONT_PATH), lazy=True)
    hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(str(FONT_PATH))))
    for name in LONG_COMPILED_NAMES:
        glyph_id = tt_font.getGlyphID(name)
        truncated = hb_font.glyph_to_string(glyph_id)
        assert truncated != name
        assert name.startswith(truncated)
        assert len(truncated.encode("utf-8")) == HARFBUZZ_NAME_BYTE_LIMIT


def test_shaper_name_recovery_survives_harfbuzz_truncation(shaper):
    tt_font = TTFont(str(FONT_PATH), lazy=True)
    for name in LONG_COMPILED_NAMES:
        assert shaper.glyph_name(tt_font.getGlyphID(name)) == name


def _seams(shaper, classifier, codepoints, features=None):
    row = extract.build_row(shaper, classifier, codepoints, features or {})
    return row


def test_it_no_joins_at_baseline(shaper, classifier):
    row = _seams(shaper, classifier, (0xE670, 0xE666))
    assert row.seams == ("y0",)
    assert row.glyphs[0].startswith("qsIt")


def test_way_thaw_never_joins(shaper, classifier):
    row = _seams(shaper, classifier, (0xE661, 0xE656))
    assert row.seams == ("break",)


def test_day_utter_ligates(shaper, classifier):
    row = _seams(shaper, classifier, (0xE653, 0xE67A))
    assert row.glyphs == ("qsDay_qsUtter",)
    assert row.clusters == (0,)
    assert row.seams == ("lig",)


def test_may_tea_xheight_join_is_ss03_gated(shaper, classifier):
    default_row = _seams(shaper, classifier, (0xE665, 0xE652))
    assert default_row.seams == ("break",)
    ss03_row = _seams(shaper, classifier, (0xE665, 0xE652), {"ss03": True})
    assert ss03_row.seams == ("y5",)


def test_split_shaping_concatenates_with_absolute_clusters(shaper):
    text = alphabet.string_text((0xE670, 0xE666))
    split = shaper.shape_split(text, (1,))
    left = shaper.shape(text[:1])
    right = shaper.shape(text[1:])
    assert split.names == left.names + right.names
    assert split.clusters == (0, 1)
    assert split.positions == left.positions + right.positions


def test_sample_predicate_is_stable():
    modulus = extract.sample_modulus(2000)
    selected = [string for string in alphabet.enumerate_basis(2) if extract.sample_includes(string, modulus)]
    again = [string for string in alphabet.enumerate_basis(2) if extract.sample_includes(string, modulus)]
    assert selected == again


def test_small_extraction_is_deterministic(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    digest_one = extract.extract_config("default", first_dir, workers=2, limit=150)
    digest_two = extract.extract_config("default", second_dir, workers=1, limit=150)
    assert digest_one.rows == digest_two.rows == 150
    assert digest_one.sha256_uncompressed == digest_two.sha256_uncompressed
    first_bytes = (first_dir / "baseline-default.tsv.gz").read_bytes()
    second_bytes = (second_dir / "baseline-default.tsv.gz").read_bytes()
    assert first_bytes == second_bytes
    text = gzip.decompress(first_bytes).decode("utf-8")
    lines = text.splitlines()
    header_lines = [line for line in lines if line.startswith("# ")]
    assert "# subset: limit=150" in header_lines
    data_lines = [line for line in lines if not line.startswith("# ")]
    assert len(data_lines) == 150
    parsed = [Row.from_tsv(line) for line in data_lines]
    assert parsed == sorted(parsed, key=row_sort_key)
    assert [row.codepoints for row in parsed[:47]] == [(cp,) for cp in alphabet.ALPHABET]
    assert (first_dir / "digests.tsv").exists()
    summary_path = extract.write_summary(first_dir)
    assert summary_path.read_text(encoding="utf-8").startswith("# Baseline extraction summary")

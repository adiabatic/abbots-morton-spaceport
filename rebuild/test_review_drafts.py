"""Tests for the site corpus index the review build hands the crate's pin drafter (`drafts.corpus_index_from_pages`), which sets a pin draft's `attribute` and `duplicate_of`: one entry a pinned cell's text and configuration, the first cell in page order winning, over a synthetic page and over the checked-in site pages."""

from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.review.drafts import corpus_index_from_pages

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_index_keeps_the_first_pinned_cell_of_each_text_and_configuration():
    """A cell is indexed under its text and the configuration its stylistic sets name, with its page and line and the expect attribute it carries. A later cell with the same text and configuration does not replace the first, a cell with no expect attribute is not indexed, and a page that does not exist contributes nothing."""
    page = (
        '<table><tr><td data-expect="·Tea ~b~ ·Day"></td>'
        '<td data-expect-noncanonically="·Tea" data-stylistic-set="3"></td></tr>\n'
        '<tr><td data-expect="·Tea ~x~ ·Day"></td><td></td></tr></table>\n'
    )
    index = corpus_index_from_pages({"site/a.html": page.encode(), "site/b.html": None})
    assert index == {
        ("", "default"): {"source": "site/a.html:1", "attribute": "data-expect"},
        ("", "ss03"): {"source": "site/a.html:1", "attribute": "data-expect-noncanonically"},
    }


def test_real_corpus_index_collects_manual_pins():
    index = corpus_index_from_pages(fingerprint.read_site_corpus(REPO_ROOT))
    assert len(index) > 500
    assert any(key[1] == "default" for key in index)
    assert any(record["attribute"] == "data-expect" for record in index.values())

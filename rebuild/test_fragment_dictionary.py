"""Tests for rebuild/tools/fragment_dictionary.py, which regenerates the preset dictionary the corpus build deflates each recomputed fragment against. No test reads the live corpus; the corpus here is written by the build's own shard writer."""

import json

from rebuild.review.build import _fragment_bytes, _write_shard
from rebuild.tools import fragment_dictionary


def test_the_dictionary_is_two_fragments_a_class_heaviest_class_last_cut_to_its_tail(tmp_path):
    """The recipe in the tool's docstring: the first two fragments of each class's first part, in the shard writer's framing, with the classes in ascending order of their shard bytes, joined and cut to the last 32 KiB. A class without units adds nothing. The heavy class's fragments are long enough that the first read of its part ends inside the second one, so the tool reads further."""
    heavy = [{"id": f"h-{index}", "text": "h" * 40_000} for index in range(3)]
    light = [{"id": f"l-{index}", "text": "l" * 10} for index in range(3)]
    classes = []
    for class_id, fragments in (("heavy", heavy), ("empty", []), ("light", light)):
        parts, _spans = _write_shard(tmp_path, class_id, fragments)
        classes.append({"id": class_id, "shards": parts})
    (tmp_path / "manifest.json").write_text(json.dumps({"classes": classes}), encoding="utf-8")
    sample = b"".join(map(_fragment_bytes, light[:2] + heavy[:2]))
    assert len(sample) > fragment_dictionary.DICTIONARY_BYTES
    assert fragment_dictionary.build_dictionary(tmp_path, size=len(sample)) == sample
    out = tmp_path / "dictionary.bin"
    assert fragment_dictionary.main(["--corpus", str(tmp_path), "--out", str(out)]) == 0
    assert out.read_bytes() == sample[-fragment_dictionary.DICTIONARY_BYTES :]

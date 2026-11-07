"""Regenerate the preset dictionary the review corpus build deflates each recomputed fragment against (`build.FRAGMENT_DICTIONARY`, read by `build._FragmentPacker` and `build._FragmentArena`) from a built corpus.

The recipe, #618's decision 71: the first two fragments of each class's first shard part, in the shard writer's framing (`build._fragment_bytes`), with the classes in ascending order of their shard bytes so that the heaviest class comes last, joined and cut to their last 32 KiB, zlib's window. Taking both slim and full fragments is what makes the dictionary work: one built from a single class's fragments deflates fragments of the other shape little better than no dictionary does.

Regenerate it when the units phase's line (`[t] review.build units`) shows the arena's ratio falling toward the one a fragment reaches without a dictionary, about 2.5 to 1, as it does after a change to the fragments' format. A regenerated dictionary changes no shard byte and no stamp, since nothing deflated with it outlives the build that deflated it. From the repository root, over the served corpus:

    uv run python -m rebuild.tools.fragment_dictionary
    uv run python -m rebuild.tools.fragment_dictionary --corpus rebuild/out/review --out rebuild/review/fragment-dictionary.bin

Then check the ratio on the next build's units line and commit the file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rebuild.review import unit_index
from rebuild.review.build import DEFAULT_OUT, FRAGMENT_DICTIONARY, _fragment_bytes

DICTIONARY_BYTES = 32 << 10
FRAGMENTS_PER_CLASS = 2


def _leading_fragments(part: Path, count: int) -> list[dict]:
    """Return the first `count` fragments of a shard part, parsed, reading the part from its start only as far as they reach."""
    decoder = json.JSONDecoder()
    size = 1 << 16
    while True:
        with part.open("rb") as handle:
            text = handle.read(size).decode("ascii")
        fragments: list[dict] = []
        index = text.index("[") + 1
        try:
            while len(fragments) < count:
                while text.startswith((" ", "\n", ","), index):
                    index += 1
                if text.startswith("]", index):
                    break
                fragment, index = decoder.raw_decode(text, index)
                fragments.append(fragment)
        except json.JSONDecodeError:
            if len(text) < size:
                raise
            size *= 4
            continue
        return fragments


def build_dictionary(corpus: Path, size: int = DICTIONARY_BYTES) -> bytes:
    """Return the dictionary the module docstring's recipe takes from the corpus at `corpus`."""
    manifest = json.loads((Path(corpus) / "manifest.json").read_bytes())
    classes = []
    for meta in manifest["classes"]:
        parts = [Path(corpus) / part for part in unit_index.class_shards(meta)]
        classes.append((sum(part.stat().st_size for part in parts), parts[0]))
    classes.sort(key=lambda entry: entry[0])
    sample = b"".join(
        _fragment_bytes(fragment)
        for _shard_bytes, first in classes
        for fragment in _leading_fragments(first, FRAGMENTS_PER_CLASS)
    )
    return sample[-size:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the corpus build's preset fragment dictionary.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_OUT, help="the built corpus to sample")
    parser.add_argument("--out", type=Path, default=FRAGMENT_DICTIONARY, help="where to write the dictionary")
    args = parser.parse_args(argv)
    dictionary = build_dictionary(args.corpus)
    args.out.write_bytes(dictionary)
    print(f"wrote {len(dictionary):,} bytes to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

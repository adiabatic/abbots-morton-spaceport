"""The review corpus's shards and their reader (doc/review-design.md §1.2, §7.2). Each manifest class lists its one shard, `units/<class-id>.json`, a JSON array of the class's fragments in id order (`class_shards`). `missing_shards` names the listed shards that are not on disk, which is the check of a finished corpus, and `iter_class_fragments` and `iter_shard_fragments` read the fragments back.

The build writes the manifest last, after every shard, both fonts and the static app files (`build.build_table_diff`), so a corpus whose manifest parses and names no missing shard is one a build finished.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

# The manifest's `inputs_fingerprint` components that fingerprint the copied review UI assets, which no shard or unit reads, so a corpus whose other components are fresh is refreshed in place when only these moved (`build.refresh_assets`), and the readiness check's freshness row warns instead of failing.
ASSET_COMPONENTS: tuple[str, ...] = ("static",)


def class_shards(meta: Mapping[str, Any]) -> list[str]:
    """Return the shard paths one manifest class entry lists, which is the class's one shard file."""
    return [str(part) for part in meta.get("shards") or ()]


def missing_shards(corpus: Path, manifest: Mapping[str, Any]) -> list[str]:
    """Name each shard `manifest` lists that is not a file under `corpus`. The build writes the manifest after the shards, so a corpus the build finished names none; this is the one check of a finished corpus, which the cycle's corpus skip and its promotion of a staged corpus (`artifact_cycle.corpus_complete`) and `make verdict-ready`'s freshness row read. A class list of the wrong shape raises TypeError or AttributeError."""
    return [
        part
        for meta in manifest.get("classes") or ()
        for part in class_shards(meta)
        if not (Path(corpus) / part).is_file()
    ]


def iter_class_fragments(corpus: Path, meta: Mapping[str, Any]) -> Iterator[dict]:
    """Yield one class's fragments in the order its shard holds them, which is id order. A shard that is missing raises OSError, and one that is not a JSON array ValueError."""
    for part in class_shards(meta):
        fragments = json.loads((Path(corpus) / part).read_text(encoding="ascii"))
        if not isinstance(fragments, list):
            raise ValueError(f"{part} is not a JSON array")
        yield from fragments


def iter_shard_fragments(corpus: Path) -> Iterator[dict]:
    """Yield every fragment of the corpus at `corpus`, class by class in manifest order."""
    manifest = json.loads((Path(corpus) / "manifest.json").read_text(encoding="utf-8"))
    for meta in manifest.get("classes") or ():
        yield from iter_class_fragments(corpus, meta)

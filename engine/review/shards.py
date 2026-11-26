"""The review corpus's shard layout and its reader of fragments (engine/REVIEW-PLAN.md §1.2, §7.2): the rule the build cuts a class's fragments into parts by (`PartLayout`, `base_part_names`), the order every walk of the corpus takes the classes in (`class_shard_key`), the manifest's identity that the app sidecars are stamped with (`manifest_sha256`), and the walk that reads every fragment back out of the parts (`iter_shard_fragments`).

A class's parts hold its fragments in id order, each a standalone ASCII JSON element between the part's `[`, commas and `PART_CLOSING`, so a part is a JSON array and a fragment's byte span in it parses on its own, which the app's Range reads rely on (`app_index`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

# The cap on one shard part's bytes on disk. The app reads each byte range it fetches, which never crosses a part, into one buffer and decodes each fragment in it as a JavaScript string, a byte a character in this ASCII-only JSON, and V8's `String::kMaxLength` on a 64-bit build is 2**29 - 24 characters. This is half that limit, and the other half is headroom.
SHARD_PART_BYTES = 1 << 28
PART_CLOSING = b"\n]\n"
# The manifest's `inputs_fingerprint` components that fingerprint the copied review UI assets, which no shard, sidecar or unit reads, so they are outside the corpus's identity (`manifest_sha256`) and every hard freshness check.
ASSET_COMPONENTS: tuple[str, ...] = ("static",)


def class_shard_key(class_id: str) -> str:
    """Return the key every walk of the corpus sorts classes by. A class written as numbered parts sorts where its bare form would, because the character after the class id is `.` in both forms."""
    return f"{class_id}.json"


def class_shards(meta: Mapping[str, Any]) -> list[str]:
    """Return one manifest class entry's shard parts, in part order."""
    return [str(part) for part in meta.get("shards") or ()]


def base_part_names(class_id: str, count: int) -> list[str]:
    """Return the names the build gives a class's `count` parts: the bare `units/<class>.json` for one, and `units/<class>.000.json` onward for more, never a bare name beside numbered ones. Both forms sort where `class_shard_key` puts the class, because the character after the class id is `.` in both."""
    if count == 1:
        return [f"units/{class_id}.json"]
    return [f"units/{class_id}.{index:03d}.json" for index in range(count)]


class PartLayout:
    """The rule the build cuts one class's fragments into parts by (`build._write_shard`). Fragments arrive in id order, each with its bytes on disk; `place` returns the part index, the byte start and whether the fragment opens its part (else a comma precedes it). A part is `[`, its fragments joined by commas, and `PART_CLOSING`, so a part equals `json.dumps(part, indent=1, ensure_ascii=True) + "\\n"` under the shard writer's framing, and a class with no fragment is one part, `[]\\n`. A part closes before the fragment that would take it past `SHARD_PART_BYTES`, so every part stays within the cap but one holding a single fragment that alone exceeds it. `sizes` closes the layout and returns each part's size."""

    def __init__(self, cap: int | None = None) -> None:
        self._cap = SHARD_PART_BYTES if cap is None else cap
        self._sizes: list[int] = []
        self._open = False

    def place(self, length: int) -> tuple[int, int, bool]:
        if self._open and self._sizes[-1] + 1 + length + len(PART_CLOSING) > self._cap:
            self._sizes[-1] += len(PART_CLOSING)
            self._open = False
        if not self._open:
            self._sizes.append(1 + length)
            self._open = True
            return len(self._sizes) - 1, 1, True
        start = self._sizes[-1] + 1
        self._sizes[-1] = start + length
        return len(self._sizes) - 1, start, False

    def sizes(self) -> list[int]:
        if self._open:
            self._sizes[-1] += len(PART_CLOSING)
            self._open = False
        if not self._sizes:
            self._sizes.append(len(b"[]\n"))
        return list(self._sizes)


def manifest_sha256(corpus: Path) -> str:
    """Return the identity of the manifest beside the corpus's shards (`manifest_identity`). A manifest that does not parse is hashed by its raw bytes, so a broken file mismatches instead of matching anything. A missing manifest raises OSError."""
    raw = (Path(corpus) / "manifest.json").read_bytes()
    try:
        document = json.loads(raw)
    except ValueError:
        return hashlib.sha256(raw).hexdigest()
    return manifest_identity(document)


def manifest_identity(document: Any) -> str:
    """Return the identity of a parsed manifest: the sha256 of its content with `ASSET_COMPONENTS` removed from `inputs_fingerprint`. Removing them lets an assets refresh rewrite that one field in place and leave the app sidecars stamped for the manifest they describe."""
    if isinstance(document, dict):
        recorded = document.get("inputs_fingerprint")
        if isinstance(recorded, dict):
            document = {
                **document,
                "inputs_fingerprint": {
                    name: value for name, value in recorded.items() if name not in ASSET_COMPONENTS
                },
            }
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\n\r":
        index += 1
    return index


def _walk_elements(text: str) -> Iterator[dict]:
    """Yield each element of one shard part. It reads the `[`, then calls `raw_decode` once per element, skipping the commas and whitespace between them, so each fragment is parsed and released before the next and the part is never held as objects all at once. A part that is not a JSON array raises ValueError."""
    decoder = json.JSONDecoder()
    index = _skip_whitespace(text, 0)
    if index >= len(text) or text[index] != "[":
        raise ValueError("a shard part is a JSON array")
    index = _skip_whitespace(text, index + 1)
    while index < len(text) and text[index] != "]":
        fragment, end = decoder.raw_decode(text, index)
        yield fragment
        index = _skip_whitespace(text, end)
        if index < len(text) and text[index] == ",":
            index = _skip_whitespace(text, index + 1)


def iter_class_fragments(corpus: Path, meta: Mapping[str, Any]) -> Iterator[dict]:
    """Yield one class's fragments, part by part in the order its manifest entry lists them, which is id order. A part that is missing raises OSError, and one that is not a JSON array ValueError."""
    for part in class_shards(meta):
        yield from _walk_elements((Path(corpus) / part).read_text(encoding="ascii"))


def iter_shard_fragments(corpus: Path) -> Iterator[dict]:
    """Yield every fragment of the corpus at `corpus`, one at a time, the classes in `class_shard_key` order."""
    manifest = json.loads((Path(corpus) / "manifest.json").read_text(encoding="utf-8"))
    for meta in sorted(
        manifest.get("classes") or (), key=lambda meta: class_shard_key(str(meta.get("id", "")))
    ):
        yield from iter_class_fragments(corpus, meta)

"""Black-box seam classification from the built font's GPOS: walk the curs feature to its cursive-attachment lookups (one per join height), record the per-height exit and entry glyph sets, and classify an adjacent output-glyph pair as joined-at-height or break. Equivalent to the test suite's anchor-Y intersection; per-height lookups are why cross-height attachment is structurally impossible."""

from __future__ import annotations

from pathlib import Path

from fontTools.ttLib import TTFont

FONT_UNITS_PER_PIXEL = 50


class SeamClassifier:
    def __init__(self, font_path: str | Path):
        tt_font = TTFont(str(font_path), lazy=True)
        gpos = tt_font["GPOS"].table
        lookup_indices: list[int] = []
        for record in gpos.FeatureList.FeatureRecord:
            if record.FeatureTag != "curs":
                continue
            for index in record.Feature.LookupListIndex:
                if index not in lookup_indices:
                    lookup_indices.append(index)
        if not lookup_indices:
            raise AssertionError("no curs feature found in GPOS")
        self._exit_sets: dict[int, frozenset[str]] = {}
        self._entry_sets: dict[int, frozenset[str]] = {}
        for index in sorted(lookup_indices):
            lookup = gpos.LookupList.Lookup[index]
            subtables = []
            for subtable in lookup.SubTable:
                if lookup.LookupType == 9:
                    subtable = subtable.ExtSubTable
                if subtable.LookupType != 3:
                    raise AssertionError(f"curs lookup {index} has non-cursive subtable")
                subtables.append(subtable)
            anchor_ys: set[int] = set()
            exits: set[str] = set()
            entries: set[str] = set()
            for subtable in subtables:
                for glyph, record in zip(subtable.Coverage.glyphs, subtable.EntryExitRecord):
                    if record.ExitAnchor is not None:
                        anchor_ys.add(record.ExitAnchor.YCoordinate)
                        exits.add(glyph)
                    if record.EntryAnchor is not None:
                        anchor_ys.add(record.EntryAnchor.YCoordinate)
                        entries.add(glyph)
            if len(anchor_ys) != 1:
                raise AssertionError(f"curs lookup {index} anchors are not uniform in Y: {sorted(anchor_ys)}")
            (anchor_y,) = anchor_ys
            if anchor_y % FONT_UNITS_PER_PIXEL != 0:
                raise AssertionError(f"curs lookup {index} anchor Y {anchor_y} is not a whole pixel")
            height = anchor_y // FONT_UNITS_PER_PIXEL
            if height in self._exit_sets:
                raise AssertionError(f"two curs lookups share height y{height}")
            self._exit_sets[height] = frozenset(exits)
            self._entry_sets[height] = frozenset(entries)

    def heights(self) -> tuple[int, ...]:
        return tuple(sorted(self._exit_sets))

    def classify(self, left_glyph: str, right_glyph: str) -> str:
        joined = [
            height
            for height in self.heights()
            if left_glyph in self._exit_sets[height] and right_glyph in self._entry_sets[height]
        ]
        if not joined:
            return "break"
        return "+".join(f"y{height}" for height in joined)

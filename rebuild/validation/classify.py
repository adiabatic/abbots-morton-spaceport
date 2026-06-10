"""Black-box seam classification per rebuild/BASELINE-PLAN.md §4: walk the built font's GPOS `curs` feature to its per-height cursive lookups and classify adjacent output-glyph pairs by exit/entry anchor membership."""

from __future__ import annotations

from pathlib import Path

from fontTools.ttLib import TTFont

PIXEL_SIZE = 50


class SeamClassifier:
    def __init__(self, font_path: Path | str) -> None:
        tt = TTFont(str(font_path))
        gpos = tt["GPOS"].table
        lookup_indices = sorted(
            {
                index
                for record in gpos.FeatureList.FeatureRecord
                if record.FeatureTag == "curs"
                for index in record.Feature.LookupListIndex
            }
        )
        if not lookup_indices:
            raise ValueError("font has no GPOS 'curs' feature")

        exit_sets: dict[int, frozenset[str]] = {}
        entry_sets: dict[int, frozenset[str]] = {}
        for index in lookup_indices:
            lookup = gpos.LookupList.Lookup[index]
            if lookup.LookupType == 9:
                subtables = [st.ExtSubTable for st in lookup.SubTable]
            else:
                subtables = list(lookup.SubTable)
            lookup_type = subtables[0].LookupType if lookup.LookupType == 9 else lookup.LookupType
            if lookup_type != 3:
                raise ValueError(f"'curs' lookup {index} is LookupType {lookup_type}, expected 3 (cursive)")

            anchor_ys: set[int] = set()
            exits: set[str] = set()
            entries: set[str] = set()
            for subtable in subtables:
                for glyph, record in zip(subtable.Coverage.glyphs, subtable.EntryExitRecord):
                    if record.EntryAnchor is not None:
                        anchor_ys.add(record.EntryAnchor.YCoordinate)
                        entries.add(glyph)
                    if record.ExitAnchor is not None:
                        anchor_ys.add(record.ExitAnchor.YCoordinate)
                        exits.add(glyph)
            if len(anchor_ys) != 1:
                raise ValueError(f"'curs' lookup {index} anchors are not uniform in Y: {sorted(anchor_ys)}")
            (y_units,) = anchor_ys
            if y_units % PIXEL_SIZE != 0:
                raise ValueError(f"'curs' lookup {index} anchor Y {y_units} is not a whole pixel")
            height = y_units // PIXEL_SIZE
            if height in exit_sets:
                raise ValueError(f"two 'curs' lookups share height {height}")
            exit_sets[height] = frozenset(exits)
            entry_sets[height] = frozenset(entries)

        if len(exit_sets) != 4:
            raise ValueError(f"expected exactly four 'curs' lookups, found {len(exit_sets)}")
        self._heights = tuple(sorted(exit_sets))
        self._exit_sets = exit_sets
        self._entry_sets = entry_sets

    def heights(self) -> tuple[int, ...]:
        return self._heights

    def classify(self, left_glyph: str, right_glyph: str) -> str:
        joined = [
            h
            for h in self._heights
            if left_glyph in self._exit_sets[h] and right_glyph in self._entry_sets[h]
        ]
        if not joined:
            return "break"
        return "+".join(f"y{h}" for h in joined)

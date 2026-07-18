"""The general table-vs-table treaty-diff mode (rebuild/REVIEW-PLAN.md §2.3, design §8): key-aligned diff of two settlement/treaty table directories, remove+add pairing into regrouped rows, provenance-only demotion, witness-string search through the settlement function, and the baseline snapshot writer."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DIFF_BUCKETS = ("added", "removed", "regrouped", "changed", "provenance-only")

BUCKET_WHY = {
    "added": "Settlement/treaty rows present only in the new tables: behavior the baseline never produced.",
    "removed": "Rows present only in the baseline tables: behavior the new tables no longer produce.",
    "regrouped": "A re-partitioned context: removals and additions sharing (config, input), rendered as one regrouped row.",
    "changed": "Rows whose key exists in both tables with a different outcome, junction, extension, or kern.",
    "provenance-only": "Settlement rows whose behavior is identical and only the provenance attribution moved — low priority.",
}


@dataclass(frozen=True)
class SettlementKey:
    config: str
    input: str
    backtrack: frozenset[str] | None
    look1: frozenset[str] | None
    look2: frozenset[str] | None
    look3: frozenset[str] | None = None
    look4: frozenset[str] | None = None

    def label(self) -> str:
        def part(value: frozenset[str] | None) -> str:
            return " ".join(sorted(value)) if value is not None else "-"

        label = f"{self.input} / {part(self.backtrack)} / {part(self.look1)} / {part(self.look2)}"
        if self.look3 is not None:
            label += f" / {part(self.look3)}"
        if self.look4 is not None:
            label += f" / {part(self.look4)}"
        return label


@dataclass(frozen=True)
class SettlementValue:
    outcome: str
    joint: bool
    provenance: str


@dataclass(frozen=True)
class TreatyKey:
    config: str
    left: str
    right: str

    def label(self) -> str:
        return f"{self.left} + {self.right}"


@dataclass(frozen=True)
class TreatyValue:
    junction: str
    extension: int
    kern: int


@dataclass
class DiffEntry:
    bucket: str
    table: str  # "settlement" | "treaty"
    config: str
    key: SettlementKey | TreatyKey
    old: SettlementValue | TreatyValue | None
    new: SettlementValue | TreatyValue | None
    paired: tuple["DiffEntry", ...] = ()
    witness: tuple[int, ...] | None = None


def _parse_set(token: str) -> frozenset[str] | None:
    return None if token == "-" else frozenset(token.split(" "))


def load_settlement(path: Path) -> dict[SettlementKey, SettlementValue]:
    rows: dict[SettlementKey, SettlementValue] = {}
    config = _config_from_path(path, "settlement-")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or line.startswith("input\t") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) == 7:
                # A pre-depth-3 snapshot: no lookahead3 column, every rule effectively unconstrained there.
                input_glyph, backtrack, look1, look2, outcome, joint, provenance = fields
                look3 = "-"
                look4 = "-"
            elif len(fields) == 8:
                input_glyph, backtrack, look1, look2, look3, outcome, joint, provenance = fields
                look4 = "-"
            else:
                input_glyph, backtrack, look1, look2, look3, look4, outcome, joint, provenance = fields
            key = SettlementKey(
                config,
                input_glyph,
                _parse_set(backtrack),
                _parse_set(look1),
                _parse_set(look2),
                _parse_set(look3),
                _parse_set(look4),
            )
            rows[key] = SettlementValue(outcome=outcome, joint=joint == "joint", provenance=provenance)
    return rows


def load_treaty(path: Path) -> dict[TreatyKey, TreatyValue]:
    rows: dict[TreatyKey, TreatyValue] = {}
    config = _config_from_path(path, "treaties-")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or line.startswith("left\t") or not line.strip():
                continue
            left, right, junction, extension, kern = line.rstrip("\n").split("\t")
            rows[TreatyKey(config, left, right)] = TreatyValue(junction, int(extension), int(kern))
    return rows


def _config_from_path(path: Path, prefix: str) -> str:
    return Path(path).stem.removeprefix(prefix)


def table_configs(directory: Path) -> list[str]:
    return sorted(path.stem.removeprefix("settlement-") for path in Path(directory).glob("settlement-*.tsv"))


def diff_settlement(
    old: dict[SettlementKey, SettlementValue],
    new: dict[SettlementKey, SettlementValue],
    config: str,
) -> list[DiffEntry]:
    entries: list[DiffEntry] = []
    removed: list[DiffEntry] = []
    added: list[DiffEntry] = []
    for key in old:
        if key in new:
            if old[key] == new[key]:
                continue
            behavior_same = (old[key].outcome, old[key].joint) == (new[key].outcome, new[key].joint)
            entries.append(
                DiffEntry(
                    bucket="provenance-only" if behavior_same else "changed",
                    table="settlement",
                    config=config,
                    key=key,
                    old=old[key],
                    new=new[key],
                )
            )
        else:
            removed.append(
                DiffEntry(
                    bucket="removed", table="settlement", config=config, key=key, old=old[key], new=None
                )
            )
    for key in new:
        if key not in old:
            added.append(
                DiffEntry(bucket="added", table="settlement", config=config, key=key, old=None, new=new[key])
            )

    by_input_removed: dict[str, list[DiffEntry]] = {}
    for entry in removed:
        by_input_removed.setdefault(entry.key.input, []).append(entry)  # type: ignore[union-attr]
    by_input_added: dict[str, list[DiffEntry]] = {}
    for entry in added:
        by_input_added.setdefault(entry.key.input, []).append(entry)  # type: ignore[union-attr]

    paired_inputs = sorted(set(by_input_removed) & set(by_input_added))
    for input_glyph in paired_inputs:
        members = tuple(by_input_removed[input_glyph] + by_input_added[input_glyph])
        entries.append(
            DiffEntry(
                bucket="regrouped",
                table="settlement",
                config=config,
                key=members[0].key,
                old=None,
                new=None,
                paired=members,
            )
        )
    for input_glyph, group in by_input_removed.items():
        if input_glyph not in paired_inputs:
            entries.extend(group)
    for input_glyph, group in by_input_added.items():
        if input_glyph not in paired_inputs:
            entries.extend(group)
    return entries


def diff_treaty(
    old: dict[TreatyKey, TreatyValue], new: dict[TreatyKey, TreatyValue], config: str
) -> list[DiffEntry]:
    entries: list[DiffEntry] = []
    for key in old:
        if key in new:
            if old[key] != new[key]:
                entries.append(DiffEntry("changed", "treaty", config, key, old[key], new[key]))
        else:
            entries.append(DiffEntry("removed", "treaty", config, key, old[key], None))
    for key in new:
        if key not in old:
            entries.append(DiffEntry("added", "treaty", config, key, None, new[key]))
    return entries


def diff_dirs(baseline_dir: Path, new_dir: Path, configs: list[str] | None = None) -> list[DiffEntry]:
    baseline_dir, new_dir = Path(baseline_dir), Path(new_dir)
    if configs is None:
        configs = sorted(set(table_configs(baseline_dir)) | set(table_configs(new_dir)))
    entries: list[DiffEntry] = []
    for config in configs:
        old_settlement = _load_if_exists(baseline_dir / f"settlement-{config}.tsv", load_settlement)
        new_settlement = _load_if_exists(new_dir / f"settlement-{config}.tsv", load_settlement)
        entries.extend(diff_settlement(old_settlement, new_settlement, config))
        old_treaty = _load_if_exists(baseline_dir / f"treaties-{config}.tsv", load_treaty)
        new_treaty = _load_if_exists(new_dir / f"treaties-{config}.tsv", load_treaty)
        entries.extend(diff_treaty(old_treaty, new_treaty, config))
    order = {bucket: index for index, bucket in enumerate(DIFF_BUCKETS)}
    entries.sort(key=lambda entry: (order[entry.bucket], entry.config, entry.table, entry.key.label()))
    return entries


def _load_if_exists(path: Path, loader):
    return loader(path) if path.exists() else {}


# --- witness search -------------------------------------------------------------


class WitnessIndex:
    """A per-config settlement sweep over every sequence of letters and boundaries up to `max_depth`, indexed two ways: per-position context tuples (input, settled left, raw right1, raw right2) for settlement-row witnesses, and adjacent settled-label pairs for treaty-row witnesses. Sequences enumerate shortest-first in codepoint order, so the recorded witness is always the first (shortest) one."""

    EDGE = "#EDGE"
    NA = "#NA"
    BOUNDARYISH = frozenset({"space", "uni200C", "periodcentered"})

    def __init__(self, spec, config: str, max_depth: int = 5):
        import itertools

        from rebuild.pipeline.conform import features_for_config, raw_labels, spec_alphabet
        from rebuild.pipeline.settle import Engine, cell_label, is_boundary_settled, settle_traces

        self.spec = spec
        self.config = config
        features = features_for_config(config)
        engine = Engine(spec, features)
        self.positions: dict[tuple[str, str, str, str, str, str], tuple[int, ...]] = {}
        self.pairs: dict[tuple[str, str], tuple[int, ...]] = {}
        alphabet = spec_alphabet(spec)
        for depth in range(1, max_depth + 1):
            for combo in itertools.product(alphabet, repeat=depth):
                text = "".join(combo)
                codepoints = tuple(ord(ch) for ch in text)
                try:
                    raw = raw_labels(spec, text, features)
                    traces = settle_traces(engine, codepoints)
                except Exception:
                    continue
                settled_labels = [cell_label(spec, trace.settled.cell) for trace in traces]
                letter_mask = [not is_boundary_settled(trace.settled) for trace in traces]
                if len(raw) != len(settled_labels):
                    continue
                for index, label in enumerate(raw):
                    if label in self.BOUNDARYISH:
                        continue
                    left = (
                        self.EDGE
                        if index == 0
                        else (
                            raw[index - 1]
                            if raw[index - 1] in self.BOUNDARYISH
                            else settled_labels[index - 1]
                        )
                    )
                    right1 = raw[index + 1] if index + 1 < len(raw) else self.EDGE
                    right2 = (
                        self.NA
                        if right1 in self.BOUNDARYISH or right1 == self.EDGE
                        else (raw[index + 2] if index + 2 < len(raw) else self.EDGE)
                    )
                    right3 = (
                        self.NA
                        if right2 in self.BOUNDARYISH or right2 in (self.EDGE, self.NA)
                        else (raw[index + 3] if index + 3 < len(raw) else self.EDGE)
                    )
                    right4 = (
                        self.NA
                        if right3 in self.BOUNDARYISH or right3 in (self.EDGE, self.NA)
                        else (raw[index + 4] if index + 4 < len(raw) else self.EDGE)
                    )
                    self.positions.setdefault((label, left, right1, right2, right3, right4), codepoints)
                    if index + 1 < len(settled_labels) and letter_mask[index] and letter_mask[index + 1]:
                        self.pairs.setdefault((settled_labels[index], settled_labels[index + 1]), codepoints)

    def witness_settlement(self, key: SettlementKey) -> tuple[int, ...] | None:
        best: tuple[int, ...] | None = None
        for (label, left, right1, right2, right3, right4), codepoints in self.positions.items():
            if label != key.input:
                continue
            if key.backtrack is not None and left not in key.backtrack:
                continue
            if key.look1 is not None and right1 not in key.look1:
                continue
            if key.look2 is not None and right2 not in key.look2:
                continue
            if key.look3 is not None and right3 not in key.look3:
                continue
            if key.look4 is not None and right4 not in key.look4:
                continue
            if best is None or (len(codepoints), codepoints) < (len(best), best):
                best = codepoints
        return best

    def witness_treaty(self, key: TreatyKey) -> tuple[int, ...] | None:
        return self.pairs.get((key.left, key.right))

    def attach(self, entries: list[DiffEntry]) -> None:
        for entry in entries:
            if entry.config != self.config or entry.witness is not None:
                continue
            if entry.table == "treaty":
                entry.witness = self.witness_treaty(entry.key)  # type: ignore[arg-type]
            else:
                keys = [member.key for member in entry.paired] or [entry.key]
                for key in keys:
                    witness = self.witness_settlement(key)  # type: ignore[arg-type]
                    if witness is not None:
                        entry.witness = witness
                        break


# --- the baseline snapshot ---------------------------------------------------------


def write_snapshot(tables_dir: Path, font: Path, to: Path, repo_root: Path | None = None) -> dict:
    """Copy the per-config settlement/treaty TSVs and the OTF they shipped with into an accepted-state directory the next migration diffs against, with sha256s, source paths, and the repo HEAD recorded in snapshot.json."""
    tables_dir, font, to = Path(tables_dir), Path(font), Path(to)
    to.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    for pattern in ("settlement-*.tsv", "treaties-*.tsv"):
        for source in sorted(tables_dir.glob(pattern)):
            target = to / source.name
            shutil.copyfile(source, target)
            files[source.name] = {"source": str(source), "sha256": _sha256(target)}
    font_target = to / font.name
    shutil.copyfile(font, font_target)
    files[font.name] = {"source": str(font), "sha256": _sha256(font_target)}
    head = "unknown"
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root or Path.cwd(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        pass
    snapshot = {"format": "ams-review-snapshot/1", "repo_head": head, "font": font.name, "files": files}
    (to / "snapshot.json").write_text(json.dumps(snapshot, indent=1) + "\n", encoding="utf-8")
    return snapshot


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

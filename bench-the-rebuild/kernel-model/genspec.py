"""Emit the k1-meso model spec as a flat integer text file all three implementations read.

The shape is taken from the real spec (rebuild/pipeline/spec_load.load_default_spec): 18 runes (15 letters + 3
ligatures), 30 stances, 4 heights, 4 features, 8 predicate classes, and the same per-rune policy-record census
(28 refuse / 34 prefer / 38 extend / 12 contract). Condition contents are generated from a fixed splitmix64
stream so the file is byte-stable, and the generator knobs are tuned so the model fixpoint lands near the real
kernel's measured window count and call counts.

Format: whitespace-separated integer tokens, one record per line, first token a keyword.
"""

import sys

MASK = (1 << 64) - 1


class Rng:
    def __init__(self, seed: int) -> None:
        self.state = seed & MASK

    def next(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        return self.next() % n

    def chance(self, num: int, den: int) -> bool:
        return self.below(den) < num


N_RUNES = 18
N_LETTERS = 15  # runes 0..14; 15,16,17 are the ligatures
LIGA_SEQS = {15: (2, 9), 16: (5, 7), 17: (1, 4)}
N_HEIGHTS = 4  # 0 baseline, 1 x-height, 2 y6, 3 top
N_FEATURES = 4  # 0 ss03, 1 ss04, 2 ss05, 3 ss10
N_STROKES = 3

# Per-rune stance counts, taken verbatim from the real spec (specstats.json).
STANCES = [1, 2, 2, 1, 2, 2, 1, 1, 2, 2, 1, 2, 1, 4, 1, 2, 1, 2]
# Per-rune policy-record counts, taken verbatim from the real spec.
REFUSE = [0, 1, 0, 0, 0, 0, 11, 0, 5, 0, 0, 2, 0, 1, 0, 6, 0, 2]
PREFER = [0, 6, 0, 0, 1, 2, 1, 0, 3, 6, 1, 2, 2, 1, 0, 1, 1, 7]
EXTEND = [2, 1, 5, 1, 3, 4, 4, 3, 3, 1, 0, 0, 4, 1, 3, 1, 0, 2]
CONTRACT = [1, 0, 0, 1, 0, 2, 0, 1, 0, 1, 0, 0, 2, 1, 0, 2, 0, 1]
# Predicate-class sizes, taken verbatim from the real spec.
CLASS_SIZES = [3, 13, 12, 12, 13, 4, 9, 2]


class Emitter:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.conds: list[str] = []
        self.whens: list[str] = []

    def cond(
        self,
        family_mask=0,
        klass=(),
        stance_mask=0,
        joined_at=-2,
        stroke=-1,
        is_token=-1,
        except_=(),
        then=-1,
    ) -> int:
        idx = len(self.conds)
        parts = [
            str(family_mask),
            str(len(klass)),
            *[str(k) for k in klass],
            str(stance_mask),
            str(joined_at),
            str(stroke),
            str(is_token),
            str(len(except_)),
            *[str(e) for e in except_],
            str(then),
        ]
        self.conds.append("cond " + str(idx) + " " + " ".join(parts))
        return idx

    def when(self, left=-1, right=-1, self_entry=-1, self_exit=-1, word=-1, feature=-1) -> int:
        idx = len(self.whens)
        self.whens.append(
            f"when {idx} {left} {right} {self_entry} {self_exit} {word} {feature}"
        )
        return idx


def main(seed: int, out_path: str) -> None:
    rng = Rng(seed)
    em = Emitter()
    lines: list[str] = []

    # --- predicate classes ---------------------------------------------------
    class_masks = []
    for size in CLASS_SIZES:
        picked: set[int] = set()
        while len(picked) < size:
            picked.add(rng.below(N_LETTERS))
        mask = 0
        for r in picked:
            mask |= 1 << r
        class_masks.append(mask)

    # --- stances -------------------------------------------------------------
    # Each stance gets an entry set, an ordered exit list, pairings, requires and unlocks. Densities match the
    # real spec's aggregate: 34 entry rows, 32 exit rows, 4 unlocks, 29 scoped rows over 30 stances.
    stance_lines: list[str] = []
    for rune in range(N_RUNES):
        for sidx in range(STANCES[rune]):
            n_entry = 1 if rng.chance(3, 4) else (2 if rng.chance(4, 5) else 0)
            entries = []
            picked_e: set[int] = set()
            while len(picked_e) < n_entry:
                picked_e.add(rng.below(N_HEIGHTS))
            for h in sorted(picked_e):
                selectable = 1
                n_scope = 1 if rng.chance(1, 3) else 0
                scope = []
                for _ in range(n_scope):
                    fam_mask = 0
                    for _ in range(3 + rng.below(6)):
                        fam_mask |= 1 << rng.below(N_LETTERS)
                    scope.append(
                        em.cond(
                            family_mask=fam_mask,
                            klass=(rng.below(len(CLASS_SIZES)),) if rng.chance(1, 3) else (),
                            joined_at=h if rng.chance(1, 4) else -2,
                        )
                    )
                entries.append((h, selectable, scope))

            n_exit = 1 if rng.chance(4, 5) else (2 if rng.chance(3, 4) else 0)
            exits = []
            picked_x: set[int] = set()
            while len(picked_x) < n_exit:
                picked_x.add(rng.below(N_HEIGHTS))
            for h in sorted(picked_x):
                n_scope = 1 if rng.chance(1, 4) else 0
                scope = []
                for _ in range(n_scope):
                    fam_mask = 0
                    for _ in range(4 + rng.below(8)):
                        fam_mask |= 1 << rng.below(N_LETTERS)
                    scope.append(em.cond(family_mask=fam_mask))
                exits.append((h, scope))

            # The (none, none) pairing always stays legal: it is the cell every stance falls back to when
            # nothing joins, and a stance that forbids it can leave a boundary-left window with no candidates
            # at all — a state the real fixpoint never reaches.
            never = []
            if rng.chance(1, 5):
                pair = (rng.below(N_HEIGHTS + 1) - 1, rng.below(N_HEIGHTS) )
                never.append(pair)
            only = []
            has_only = 1 if rng.chance(1, 15) else 0
            if has_only:
                only.append((-1, -1))
                for _ in range(6):
                    only.append((rng.below(N_HEIGHTS + 1) - 1, rng.below(N_HEIGHTS + 1) - 1))
            unlocks = []
            if rng.chance(2, 15):
                feat = rng.below(N_FEATURES - 1)
                kind = rng.below(3)
                w = -1
                if rng.chance(1, 2):
                    w = em.when(
                        right=em.cond(family_mask=(1 << (rng.below(N_LETTERS))) | (1 << rng.below(N_LETTERS)))
                    )
                if kind == 0:
                    unlocks.append((feat, rng.below(N_HEIGHTS), -1, 0, -1, -1, w))
                elif kind == 1:
                    unlocks.append((feat, -1, rng.below(N_HEIGHTS), 0, -1, -1, w))
                else:
                    unlocks.append(
                        (feat, -1, -1, 1, rng.below(N_HEIGHTS + 1) - 1, rng.below(N_HEIGHTS + 1) - 1, w)
                    )
            # A require: never lands on a rune's first stance, so every rune keeps at least one stance that
            # survives a boundary left; otherwise the fixpoint's seed windows have no candidates at all.
            req_entry = 1 if (sidx > 0 and entries and rng.chance(1, 6)) else 0
            req_exit = 1 if (sidx > 0 and exits and rng.chance(1, 8)) else 0

            parts = [str(rune), str(sidx), str(req_entry), str(req_exit), str(len(entries))]
            for h, sel, scope in entries:
                parts += [str(h), str(sel), str(len(scope)), *[str(c) for c in scope]]
            parts.append(str(len(exits)))
            for h, scope in exits:
                parts += [str(h), str(len(scope)), *[str(c) for c in scope]]
            parts.append(str(len(never)))
            for e, x in never:
                parts += [str(e), str(x)]
            parts.append(str(has_only))
            parts.append(str(len(only)))
            for e, x in only:
                parts += [str(e), str(x)]
            parts.append(str(len(unlocks)))
            for u in unlocks:
                parts += [str(v) for v in u]
            stance_lines.append("stance " + " ".join(parts))

    # --- policy records ------------------------------------------------------
    # Right-condition chain reach drives which runes carry live deep slots; the real spec's reach histogram over
    # prefer records with a right condition is {0: 11, 1: 11, 2: 4, 3: 3}.
    reach_plan = [0] * 11 + [1] * 11 + [2] * 4 + [3] * 3
    reach_slot = 0

    def right_chain(reach: int) -> int:
        fam = 0
        for _ in range(5 + rng.below(7)):
            fam |= 1 << rng.below(N_LETTERS)
        then = -1
        if reach > 0:
            then = right_chain(reach - 1)
        exc = ()
        if reach == 0 and rng.chance(1, 4):
            exc = (em.cond(family_mask=1 << rng.below(N_LETTERS)),)
        return em.cond(
            family_mask=fam,
            klass=(rng.below(len(CLASS_SIZES)),) if rng.chance(1, 4) else (),
            except_=exc,
            then=then,
        )

    def left_chain() -> int:
        fam = 0
        for _ in range(4 + rng.below(8)):
            fam |= 1 << rng.below(N_LETTERS)
        return em.cond(
            family_mask=fam,
            klass=(rng.below(len(CLASS_SIZES)),) if rng.chance(1, 4) else (),
            stance_mask=(1 << rng.below(4)) if rng.chance(1, 6) else 0,
            joined_at=(rng.below(N_HEIGHTS + 1) - 1) if rng.chance(1, 3) else -2,
            stroke=rng.below(N_STROKES) if rng.chance(1, 8) else -1,
        )

    record_lines: list[str] = []

    def emit_records(rune: int, kind: int, count: int) -> None:
        nonlocal reach_slot
        for _ in range(count):
            left = left_chain() if rng.chance(1, 2) else -1
            right = -1
            if kind == 1:  # prefer: reach plan drives the deep-slot census
                if reach_slot < len(reach_plan):
                    right = right_chain(reach_plan[reach_slot])
                    reach_slot += 1
                elif rng.chance(1, 2):
                    right = right_chain(0)
            elif rng.chance(3, 5):
                # Refuse/extend/contract right conditions stay reach-0: a deeper chain would let a concrete
                # right2 fire a refusal the UNKNOWN-optimistic lookahead closure could not see, and the fixpoint
                # would strand. The real spec has the same property.
                right = right_chain(0)
            w = em.when(
                left=left,
                right=right,
                self_entry=(rng.below(2)) if rng.chance(1, 6) else -1,
                self_exit=(rng.below(2)) if rng.chance(1, 6) else -1,
                word=rng.below(4) if rng.chance(1, 8) else -1,
                feature=rng.below(N_FEATURES) if rng.chance(1, 6) else -1,
            )
            stance = rng.below(STANCES[rune]) if rng.chance(1, 3) else -1
            entry = (rng.below(N_HEIGHTS + 1) - 1) if rng.chance(1, 3) else -2
            exit_ = (rng.below(N_HEIGHTS + 1) - 1) if rng.chance(1, 3) else -2
            if kind == 0 and exit_ == -2:
                # A refuse that names no exit height can still kill a joining candidate but must leave the
                # non-joining one alive, or the fixpoint strands where the closure admitted a seam.
                exit_ = rng.below(N_HEIGHTS)
            has_cell = 0
            ce = cx = -1
            has_over = 0
            oe = ox = -1
            if kind == 1:
                if rng.chance(2, 3):
                    has_cell = 1
                    ce = rng.below(N_HEIGHTS + 1) - 1
                    cx = rng.below(N_HEIGHTS + 1) - 1
                    if rng.chance(1, 3):
                        has_over = 1
                        oe = rng.below(N_HEIGHTS + 1) - 1
                        ox = rng.below(N_HEIGHTS + 1) - 1
                else:
                    stance = rng.below(STANCES[rune])
            absolute = 1 if (kind == 1 and rng.chance(1, 6)) else 0
            by = 1 + rng.below(2)
            record_lines.append(
                "record "
                + " ".join(
                    str(v)
                    for v in (
                        rune,
                        kind,
                        w,
                        stance,
                        entry,
                        exit_,
                        has_cell,
                        ce,
                        cx,
                        has_over,
                        oe,
                        ox,
                        absolute,
                        by,
                    )
                )
            )

    order_lines: list[str] = []
    for rune in range(N_RUNES):
        order = list(range(STANCES[rune]))
        for i in range(len(order) - 1, 0, -1):
            j = rng.below(i + 1)
            order[i], order[j] = order[j], order[i]
        order_lines.append("order " + str(rune) + " " + " ".join(str(o) for o in order))
        emit_records(rune, 0, REFUSE[rune])
        emit_records(rune, 1, PREFER[rune])
        emit_records(rune, 2, EXTEND[rune])
        emit_records(rune, 3, CONTRACT[rune])

    # --- entry strokes per rune (cond.stroke on the right side reads these) ---
    stroke_lines = []
    for rune in range(N_RUNES):
        mask = 0
        for _ in range(1 + rng.below(2)):
            mask |= 1 << rng.below(N_STROKES)
        stroke_lines.append(f"strokes {rune} {mask}")

    lines.append(f"header {N_RUNES} {N_LETTERS} {N_HEIGHTS} {N_FEATURES} {N_STROKES} {len(CLASS_SIZES)}")
    for i, m in enumerate(class_masks):
        lines.append(f"class {i} {m}")
    for rune in range(N_RUNES):
        a, b = LIGA_SEQS.get(rune, (-1, -1))
        lines.append(f"rune {rune} {1 if rune in LIGA_SEQS else 0} {a} {b} {STANCES[rune]}")
    lines.extend(order_lines)
    lines.extend(stroke_lines)
    lines.extend(em.conds)
    lines.extend(em.whens)
    lines.extend(stance_lines)
    lines.extend(record_lines)

    with open(out_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    sys.stderr.write(
        f"wrote {out_path}: {len(em.conds)} conditions, {len(em.whens)} whens, "
        f"{len(stance_lines)} stances, {len(record_lines)} records\n"
    )


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0x5EED1234, sys.argv[2] if len(sys.argv) > 2 else "spec.txt")

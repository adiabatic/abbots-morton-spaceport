"""Group the actionable "outright broken" emergent leaks into root-cause families.

Read-only. Reads the "broken" verdicts from doc/history/2026-06-03--leak-cleanup/leak-emergent-verdicts.txt and groups them by the changed-stance mechanism into the nine families the taxonomy in doc/history/2026-06-03--leak-cleanup/leak-triage.md analyzes. Forward (left-exit) families key on the mechanism; backward (right-entry) families additionally split by predecessor context (the left neighbor across the break).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERDICTS_PATH = ROOT / "doc" / "history" / "2026-06-03--leak-cleanup" / "leak-emergent-verdicts.txt"
BROKEN_VERDICT = "in context is outright broken"


def parse_row(line: str) -> tuple[str, list[str], int, dict[str, tuple[str, str]], list[str]]:
    label, _, diff = line.partition(" :: ")
    example, _, brk = label.partition(" [break ")
    families = example.split()
    break_idx = int(brk.rstrip("]"))
    sides: dict[str, tuple[str, str]] = {}
    starred: list[str] = []
    for clause in diff.split(" | "):
        raw = clause.strip()
        star = raw.startswith("*")
        body = raw.lstrip("*").strip()
        for tag in ("L", "R"):
            if body.startswith(tag + " ") and "->" in body:
                frm, _, to = body[2:].partition("->")
                sides[tag] = (frm.strip(), to.strip())
                if star:
                    starred.append(tag)
    return label.strip(), families, break_idx, sides, (starred or list(sides))


def classify_forward(follower: str, frm: str, to: str) -> str | None:
    base = frm.split(".")[0]
    if base == "qsThey" and "before-may" in to:
        return "F1_They_before_may_exit"
    if base == "qsMay" and ".ex-y0" in ("." + to):
        return "F2_May_baseline_exit_dangle"
    if base == "qsExcite":
        return "F3_Excite_vertical_exit"
    if base == "qsUtter" and "reaches-way-back" in to:
        return "F8_Utter_reaches_way_back"
    if follower in ("Tea", "Thaw") or "." not in to:
        return "F4_left_entry_revert_dangle"
    return None


def classify_backward(families: list[str], break_idx: int, sides: dict[str, tuple[str, str]]) -> str:
    predecessor = families[break_idx]
    prior = families[break_idx - 1] if break_idx >= 1 else ""
    if predecessor == "Zoo" or (prior == "They" and predecessor == "Zoo"):
        return "F5a_They_Zoo_predecessor"
    if predecessor == "Tea" and prior == "Out":
        return "F6_Out_Tea_predecessor"
    if predecessor in ("May", "No"):
        return "F7_May_No_predecessor_entry"
    if "R" in sides and "qsExcite" in sides["R"][1] and "noentry" in sides["R"][1]:
        return "F3_Excite_vertical_exit"
    return "F9_misc_backward"


def family_of(
    families: list[str], break_idx: int, sides: dict[str, tuple[str, str]], starred: list[str]
) -> str:
    if "L" in starred:
        frm, to = sides["L"]
        chosen = classify_forward(families[break_idx + 1], frm, to)
        if chosen is not None:
            return chosen
    return classify_backward(families, break_idx, sides)


def main() -> None:
    families: dict[str, list[str]] = defaultdict(list)
    for raw in VERDICTS_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        snap, _, verdict = line.partition(" => ")
        if verdict.strip() != BROKEN_VERDICT:
            continue
        snap = snap.strip()
        _label, fams, break_idx, sides, starred = parse_row(snap)
        families[family_of(fams, break_idx, sides, starred)].append(snap)

    ordered = sorted(families.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for name, rows in ordered:
        print(f"\n[{len(rows):2d}] {name}")
        for row in rows:
            print(f"    {row}")
    print(f"\n{len(ordered)} families, {sum(len(v) for v in families.values())} broken rows")


if __name__ == "__main__":
    main()

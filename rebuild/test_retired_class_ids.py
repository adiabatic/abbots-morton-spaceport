"""Guard against the review classes' retired ids. Every ledger class has a reviewer-facing id and the corpus lists unmatched windows under change groups, so an old id anywhere but the few places that still need it names a class or group no corpus has. The search is `git grep` over the working tree's tracked and untracked files, so it also covers the docs, skills and YAML comments no import reaches. A ledger `match.predicate` spells its oracle class with underscores, which this search never matches. Two kinds of file are left out (`EXCLUDED`): the review-facts pins, which every non-staging cycle pass rewrites from the corpus it built, and the carried verdict master under rebuild/evidence/, recorded verdict history whose notes keep the ids they were written with."""

import re
import subprocess
from pathlib import Path

from rebuild.review.fixtures.mini.pin import RENAMED_CLASS_IDS

REPO_ROOT = Path(__file__).resolve().parent.parent

RETIRED_GROUP_IDS = (
    "no-chain-gains",
    "tea-it-xheight",
    "oy-it-baseline",
    "may-utter-gains",
    "junction-loss-unjoined",
    "extension-non-summing",
    "unmatched-misc",
    "deferred-ss04",
    "deferred-ss10",
    "deferred-ss03",
)
RETIRED_IDS = tuple(
    sorted({old for old, new in RENAMED_CLASS_IDS.items() if old != new} | set(RETIRED_GROUP_IDS))
)

ALLOWED = {
    "rebuild/pipeline/oracle.py": "the oracle's internal class strings, which the oracle rename retires with its predicates",
    "rebuild/test_conform.py": "the oracle's tests, which name its internal class strings",
    "rebuild/review/fixtures/mini/pin.py": "the rename table that adapts the mini bundle's pinned ledger",
    "rebuild/test_retired_class_ids.py": "this guard's own list",
    "glyph_data/runes/qsTea_qsOy.yaml": "a rune `why:`, which only the author rewords (WHATNEXT.md)",
}
EXCLUDED = ("rebuild/review-facts-pins.json", "rebuild/evidence/")

_RETIRED = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, RETIRED_IDS)) + r")(?![\w-])")


def _hits() -> dict[str, list[str]]:
    """Return every line naming a retired id as a whole token, by path. `git grep` takes the ids as fixed strings and finds candidate lines; the token pattern then drops an id that is only part of a longer one."""
    command = ["git", "grep", "--untracked", "-I", "-n", "-F"]
    for identifier in RETIRED_IDS:
        command += ["-e", identifier]
    command += ["--", ".", *(f":(exclude){path}" for path in EXCLUDED)]
    found = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if found.returncode not in (0, 1):
        raise RuntimeError(f"git grep failed ({found.returncode}): {found.stderr.strip()}")
    hits: dict[str, list[str]] = {}
    for line in found.stdout.splitlines():
        path, number, text = line.split(":", 2)
        if _RETIRED.search(text):
            hits.setdefault(path, []).append(
                f"{path}:{number}: {', '.join(sorted(set(_RETIRED.findall(text))))}"
            )
    return hits


def test_no_retired_class_or_group_id_outside_the_allow_list():
    """Name the new id instead: `RENAMED_CLASS_IDS` in rebuild/review/fixtures/mini/pin.py maps each old ledger id to it, and `CHANGE_GROUP_ORDER` in rebuild/review/change_groups.py lists the change groups. Every allowed path must still name a retired id, so an entry leaves the list once its reason has gone."""
    hits = _hits()
    stray = [line for path, lines in sorted(hits.items()) if path not in ALLOWED for line in lines]
    assert not stray, "retired review class or group ids:\n" + "\n".join(stray)
    unused = sorted(set(ALLOWED) - set(hits))
    assert not unused, f"no retired id left in {unused}; drop them from ALLOWED"

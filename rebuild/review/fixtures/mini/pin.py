"""Pin the spec the frozen mini-M1 bundle settled under by git object ids, instead of keeping a copy of it in the tree.

The enricher re-settles every frozen window from the runes, so the bundle's rows are valid only under the spec they settled under. A checked-in copy of that spec would look like source and invite edits to the wrong file. `pin.json` records the tree and blob shas of `PINNED_PATHS` at the commit the bundle was regenerated on, and `materialize` writes those objects out of git on demand. A sha names content, so the pin survives any rebase that leaves those files' bytes unchanged. When the repository no longer holds a pinned object, `materialize` raises `MissingPinnedObjects` with the command that regenerates the bundle.
"""

import io
import json
import subprocess
import tarfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
PIN_PATH = HERE / "pin.json"

PINNED_PATHS: tuple[str, ...] = (
    "glyph_data/runes",
    "rebuild/schema",
    "rebuild/script.yaml",
    "rebuild/m1-divergences.yaml",
)
LEDGER_PATH = "rebuild/m1-divergences.yaml"

RENAMED_CLASS_IDS: dict[str, str] = {
    "boundary-window": "split-by-space-or-zwnj",
    "zwnj-word-initial-unification": "identical-unjoined-from-left",
    "post-marker-ligature-formation": "ss03-tea-oy-ligature-kept",
    "zwnj-follower-exit-restored": "joins-next-letter-after-zwnj",
    "zwnj-word-initial-junction-moved": "join-height-after-zwnj",
    "pre-ligature-cleanup-regularized": "new-joins-near-tea-oy",
    "regrouped-chain": "join-switches-sides",
    "ss03-chain-join-gains": "ss03-adds-joins-around-tea",
    "entered-it-baseline-join-gain": "it-joins-both-sides",
    "pea-chain-regularized": "pea-joins-next-letter",
    "halves-entry-extension-restored": "longer-entry-join",
    "same-junction-extension-non-summing": "one-extension-per-join",
    "may-baseline-entry-extension-dropped": "may-closer-at-baseline-join",
    "day-baseline-entry-extension-dropped": "half-day-closer-at-baseline-join",
    "vie-baseline-entry-extension-dropped": "vie-closer-at-baseline-join",
    "no-xheight-entry-extension-dropped": "halves-closer-to-no",
    "may-ligature-junction-loosened": "may-farther-after-day-utter",
    "dangling-anchor-dropped": "identical-unused-join-name",
    "bare-name-live-join": "identical-variant-name",
    "see-out-fusion-renamed": "identical-see-out-fusion",
    "may-jai-extension-consolidated": "may-jai-join-longer",
    "jai-entry-contraction-renamed": "identical-jai-jay-crown",
    "zoo-entry-contraction-renamed": "identical-zoo-crown",
    "roe-shortened-top-renamed": "identical-roe-short-top",
    "ss03-out-tea-ligature-kept": "ss03-out-tea-ligature-kept",
    "kern-out-of-scope": "spacing-at-kerned-pairs",
}

RENAMED_CLASS_STATUSES: dict[str, str] = {
    "split-by-space-or-zwnj": "by-design",
    "identical-unjoined-from-left": "by-design",
    "ss03-tea-oy-ligature-kept": "approved",
    "joins-next-letter-after-zwnj": "approved",
    "join-height-after-zwnj": "by-design",
    "new-joins-near-tea-oy": "per-unit",
    "join-switches-sides": "per-unit",
    "ss03-adds-joins-around-tea": "approved",
    "it-joins-both-sides": "per-unit",
    "pea-joins-next-letter": "approved",
    "longer-entry-join": "per-unit",
    "one-extension-per-join": "approved",
    "may-closer-at-baseline-join": "approved",
    "half-day-closer-at-baseline-join": "approved",
    "vie-closer-at-baseline-join": "approved",
    "halves-closer-to-no": "approved",
    "may-farther-after-day-utter": "approved",
    "identical-unused-join-name": "by-design",
    "identical-variant-name": "by-design",
    "identical-see-out-fusion": "by-design",
    "may-jai-join-longer": "per-unit",
    "identical-jai-jay-crown": "by-design",
    "identical-zoo-crown": "by-design",
    "identical-roe-short-top": "by-design",
    "ss03-out-tea-ligature-kept": "approved",
    "spacing-at-kerned-pairs": "per-unit",
}


class MissingPinnedObjects(RuntimeError):
    """Raised when the repository no longer holds an object the pin names."""


class StalePinnedLedger(RuntimeError):
    """Raised when `_adopt_reviewer_class_ids` cannot carry a pinned ledger entry to the working tree's ledger."""


def _git(args: list[str], repo_root: Path) -> bytes:
    return subprocess.run(["git", *args], cwd=repo_root, check=True, capture_output=True).stdout


def current_objects(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Return the sha each pinned path resolves to at HEAD, in `PINNED_PATHS` order: a tree for the first two, a blob for the last two."""
    return {rel: _git(["rev-parse", f"HEAD:{rel}"], repo_root).decode().strip() for rel in PINNED_PATHS}


def dirty_paths(repo_root: Path = REPO_ROOT) -> list[str]:
    """Return the porcelain status lines for the pinned paths, untracked files included. The list is empty only when the working tree matches HEAD there; otherwise a pin taken from HEAD would name a spec the build did not use."""
    status = _git(["status", "--porcelain", "--untracked-files=all", "--", *PINNED_PATHS], repo_root).decode()
    return [line for line in status.splitlines() if line.strip()]


def write_pin(repo_root: Path = REPO_ROOT, pin_path: Path = PIN_PATH) -> dict:
    """Write HEAD and the pinned objects to `pin_path` and return the record. `head` only records which commit the bundle was regenerated at; `materialize` reads `objects` alone, so rewritten history does not break the pin while the objects remain in the repository."""
    record = {
        "head": _git(["rev-parse", "HEAD"], repo_root).decode().strip(),
        "objects": current_objects(repo_root),
    }
    pin_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def read_pin(pin_path: Path = PIN_PATH) -> dict:
    return json.loads(pin_path.read_text(encoding="utf-8"))


def _preserve_authored_outgoing(dest: Path) -> None:
    """Adapt a pinned schema that has no `outgoing` stance property to the current loader. Such a schema means each ligature rune states its complete outgoing policy itself, so this adds an `outgoing` property that allows only a fixed exception and sets that exception on every stance of every rune with a `sequence`, including a ligature whose trailing component has one stance, which the current loader would otherwise inherit from without a declaration. It takes no policy or schema from the working tree. A pinned schema that already has `outgoing` is left unchanged."""
    schema_path = dest / "rebuild/schema/rune.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    properties = schema["$defs"]["stance"]["properties"]
    if "outgoing" in properties:
        return
    reason = "This frozen snapshot authors the complete outgoing contract on the ligature."
    properties["outgoing"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["exception"],
        "properties": {"exception": {"const": reason}},
    }
    for path in sorted((dest / "glyph_data/runes").glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not raw.get("sequence"):
            continue
        for stance in raw["stances"].values():
            stance["outgoing"] = {"exception": reason}
        path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    schema_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _adopt_reviewer_class_ids(dest: Path, repo_root: Path) -> None:
    """Adapt a pinned divergence ledger that predates the reviewer-facing class ids to the current loader. Such a ledger names its classes by their old ids and statuses and has no `title` or `summary`, so each entry takes its new id from `RENAMED_CLASS_IDS`, the status `RENAMED_CLASS_STATUSES` gives that id, and the `title` and `summary` of the working tree's ledger entry with that id. The bundle's `audit.tsv` names its classes by the new ids, as a run under the adapted ledger writes them. Every `match` stays, so the oracle classifies as the pinned ledger did, and the copied prose is all this takes from the working tree. A pinned ledger whose every entry has a `title` is current and is left unchanged. An entry that neither table nor the working tree's ledger can place raises `StalePinnedLedger`, because the bundle then needs regenerating."""
    ledger_path = dest / LEDGER_PATH
    entries = yaml.safe_load(ledger_path.read_text(encoding="utf-8")) or []
    if all("title" in entry for entry in entries):
        return
    live = {
        entry["id"]: entry for entry in yaml.safe_load((repo_root / LEDGER_PATH).read_text(encoding="utf-8"))
    }
    regenerate = "regenerate the bundle: uv run python rebuild/review/fixtures/mini/regenerate.py"
    for entry in entries:
        new_id = RENAMED_CLASS_IDS.get(entry["id"])
        if new_id is None:
            raise StalePinnedLedger(
                f"the pinned ledger's {entry['id']} has no reviewer-facing id; {regenerate}"
            )
        if new_id not in live:
            raise StalePinnedLedger(
                f"the working tree's ledger has no {new_id} (was {entry['id']}); {regenerate}"
            )
        entry["id"] = new_id
        entry["status"] = RENAMED_CLASS_STATUSES[new_id]
        entry["title"] = live[new_id]["title"]
        entry["summary"] = live[new_id]["summary"]
    ledger_path.write_text(yaml.safe_dump(entries, sort_keys=False, allow_unicode=True), encoding="utf-8")


def materialize(dest: Path, pin_path: Path = PIN_PATH, repo_root: Path = REPO_ROOT) -> Path:
    """Write the pinned objects under `dest` at their repository paths, the trees whole, apply `_preserve_authored_outgoing` and `_adopt_reviewer_class_ids`, and return `dest`. The result is a spec root that `rebuild.review.enrich.load_spec` and `rebuild.pipeline.fingerprint.rune_digests` can read. Every pinned object is checked before any is written, so a missing object leaves no partial spec root. Nothing is cached; the caller chooses where the files go and how long they stay."""
    dest = Path(dest)
    objects = read_pin(pin_path)["objects"]
    missing = [
        f"{rel} at {sha}"
        for rel, sha in objects.items()
        if subprocess.run(
            ["git", "cat-file", "-e", sha], cwd=repo_root, check=False, capture_output=True
        ).returncode
    ]
    if missing:
        raise MissingPinnedObjects(
            f"the mini bundle pins {', '.join(missing)}, which this repository does not hold; regenerate the "
            "bundle after a fresh run_m1: uv run python rebuild/review/fixtures/mini/regenerate.py"
        )
    for rel, sha in objects.items():
        kind = _git(["cat-file", "-t", sha], repo_root).decode().strip()
        target = dest / rel
        if kind == "tree":
            archive = _git(["archive", "--format=tar", sha], repo_root)
            target.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                bundle.extractall(target, filter="data")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_git(["cat-file", "blob", sha], repo_root))
    _preserve_authored_outgoing(dest)
    _adopt_reviewer_class_ids(dest, repo_root)
    return dest

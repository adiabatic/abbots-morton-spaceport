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


class MissingPinnedObjects(RuntimeError):
    """Raised when the repository no longer holds an object the pin names."""


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
    """Adapt a pinned schema that has no `outgoing` stance property to the current loader. Such a schema means each ligature rune states its complete outgoing policy itself, so this adds an `outgoing` property that allows only a fixed exception and sets that exception on every stance of every rune with a `sequence`, including single-stance trailing components. It takes no policy or schema from the working tree. A pinned schema that already has `outgoing` is left unchanged."""
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


def materialize(dest: Path, pin_path: Path = PIN_PATH, repo_root: Path = REPO_ROOT) -> Path:
    """Write the pinned objects under `dest` at their repository paths, the trees whole, apply `_preserve_authored_outgoing`, and return `dest`. The result is a spec root that `rebuild.review.enrich.load_spec` and `rebuild.pipeline.fingerprint.rune_digests` can read. Every pinned object is checked before any is written, so a missing object leaves no partial spec root. Nothing is cached; the caller chooses where the files go and how long they stay."""
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
    return dest

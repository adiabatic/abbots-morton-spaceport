"""The spec the frozen mini-M1 bundle settled under, pinned rather than copied. The enricher re-settles every frozen window from the runes, so the bundle's rows describe a rebuild that still happens only under the spec they settled under — which is why the bundle has to name one at all. Naming it as a second copy of the runes in the tree, though, invites editing the wrong file: the copy looks like source, reads like source, and nothing complains until a cycle much later.

Git already holds those bytes, content-addressed, so `pin.json` records the tree and blob shas of `PINNED_PATHS` at the commit the bundle was regenerated on and `materialize` writes them back out of the object store on demand. The pin survives any rebase that leaves those files' bytes alone, because a sha names content rather than history; a pin whose objects this repository no longer holds fails loudly, naming the command that regenerates the bundle, rather than silently settling under whatever the working tree happens to say today.
"""

import io
import json
import subprocess
import tarfile
from pathlib import Path

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
    """The sha each pinned path resolves to at HEAD, in `PINNED_PATHS` order — a tree for the first two, a blob for the last two."""
    return {rel: _git(["rev-parse", f"HEAD:{rel}"], repo_root).decode().strip() for rel in PINNED_PATHS}


def dirty_paths(repo_root: Path = REPO_ROOT) -> list[str]:
    """The porcelain status lines of the pinned paths — non-empty exactly when HEAD's bytes are not the working tree's, which is the condition that makes a pin taken from HEAD a lie."""
    status = _git(["status", "--porcelain", "--untracked-files=all", "--", *PINNED_PATHS], repo_root).decode()
    return [line for line in status.splitlines() if line.strip()]


def write_pin(repo_root: Path = REPO_ROOT, pin_path: Path = PIN_PATH) -> dict:
    """Record HEAD and the pinned objects. `head` is informational — which commit the bundle was regenerated at — and materialization resolves through `objects` alone, so a rewritten history costs the pin nothing as long as the content survives."""
    record = {
        "head": _git(["rev-parse", "HEAD"], repo_root).decode().strip(),
        "objects": current_objects(repo_root),
    }
    pin_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def read_pin(pin_path: Path = PIN_PATH) -> dict:
    return json.loads(pin_path.read_text(encoding="utf-8"))


def materialize(dest: Path, pin_path: Path = PIN_PATH, repo_root: Path = REPO_ROOT) -> Path:
    """Write the pinned objects under `dest` at the paths they live at in the repo — `<dest>/glyph_data/runes/`, `<dest>/rebuild/schema/`, `<dest>/rebuild/script.yaml`, `<dest>/rebuild/m1-divergences.yaml`, the two trees arriving whole rather than filtered down to the files a reader happens to want. That is the layout `rebuild.review.enrich.load_spec` and `rebuild.pipeline.fingerprint.rune_digests` read under a root, so `dest` is a spec root. Every pinned object is probed before any of them is written, so a repository missing one leaves no half-built spec root behind. Nothing is cached: the caller picks where the bytes land and how long they live."""
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
    return dest

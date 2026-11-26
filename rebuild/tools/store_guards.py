"""The guards a hand-run writer of the verdict store passes before it writes: the close-out (`rebuild.tools.close_out`), the re-key (`rebuild.tools.rekey_verdicts`) and the table-diff accept (`rebuild.review.accept`). Each takes the artifact cycle's pass lock beside the store without waiting and holds it to its end (`pass_lock`), so no pass that snapshotted the store before the write lands after it, refuses while a review server listens that may write the store (`serves`), and names the cause of either refusal (`pass_refusal`, `server_refusal`). A writer that reads the served corpus's human units first asks `make verdict-ready` and refuses while a blocking check fails (`readiness_refusal`)."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

from rebuild.review.store_lock import hold_flock, holder
from rebuild.tools.review_server import capabilities, server_listening

CYCLE = Path("var") / "cycle"
PASS_LOCK_NAME = "pass.lock"


def pass_lock_path_for(store: Path) -> Path:
    """Return the artifact cycle's pass lock beside the store, var/cycle/pass.lock, which for the live store is `artifact_cycle.pass_lock_path()`."""
    return Path(store).parent / CYCLE / PASS_LOCK_NAME


@contextlib.contextmanager
def pass_lock(store: Path) -> Iterator[None]:
    """Hold the artifact cycle's pass lock beside `store` without waiting, writing this process's pid into it as a pass does (`artifact_cycle.pass_lock`), so a pass that starts meanwhile names the holder while it waits. Raise `store_lock.LockBusy` while a pass holds it."""
    with hold_flock(pass_lock_path_for(store), blocking=False) as fd:
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        yield


def serves(store: Path) -> bool:
    """Return whether a review server listens that may write `store`: its /capabilities answer names that store, or names none, as a server that predates /capabilities or does not answer does. `rebuild.tools.rekey_verdicts` applies the same rule."""
    if not server_listening():
        return False
    answer = capabilities()
    served = answer.get("autosave") if isinstance(answer, dict) else None
    return not isinstance(served, str) or Path(served).resolve() == Path(store).resolve()


def pass_refusal(store: Path) -> str:
    """Return the refusal of a writer that found the pass lock beside `store` held (`pass_lock`), naming the holder's pid. A pass between its snapshot and its land would lay the saves made since over a prepared store from before the write."""
    who = holder(pass_lock_path_for(store)) or "unknown"
    return f"a pass holds {pass_lock_path_for(store)} (pid {who}); wait for it to finish"


def server_refusal(store: Path, task: str) -> str:
    """Return the refusal of `task`, a writer of `store`, while a review server listens that may write it (`serves`)."""
    return (
        f"a review server listening on port 7294 may write {Path(store).name}: its /capabilities answer names that store, or "
        f"names none. Stop it for {task} and restart it afterwards, and the open tabs fetch the store and the "
        "list again at the restart"
    )


def readiness_refusal(result: dict, ready: bool, consequence: str) -> str | None:
    """Return the refusal a writer makes when `make verdict-ready`'s blocking checks fail, naming each failing check with its remedy after `consequence`, what the writer cannot trust while they fail, or None when they pass."""
    if ready:
        return None
    failing = [
        f"{name}: {check['detail']}" + (f" (remedy: {check['remedy']})" if check.get("remedy") else "")
        for name, check in result["checks"].items()
        if check.get("level") == "fail"
    ]
    return f"`make verdict-ready`'s blocking checks fail, so {consequence}: {'; '.join(failing)}"

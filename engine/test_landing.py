"""Tests for the land (`engine/review/landing.py`), which moves a pass's corpus into place and carries the verdict store onto it under the store's lock beside a running review server.

On a stamp move the land carries the store by unit id: a verdict on a unit the new corpus still has stays, a skip there is dropped and leaves a tombstone, a verdict off the new corpus's human units goes to the orphan document of the stamp it was made on, and a tombstone stays on the new corpus's human units alone. A store already on the new stamp, and no store at all, are left as they are. A store holding a record `verdict_store.well_formed` refuses aborts the land before anything moves. A land killed between its swap and its store write leaves a store on the old stamp, which the next save stamped for the served corpus carries onto it. The land waits for a writer that holds the store's lock, and the review server's locked save path reads the served corpus only under the lock and answers a retryable 503 while a land holds it. The tree swap and its fallback where the filesystem refuses the swap call, the discard rename that never deletes, the discard a land leaves to its caller, and the copy-on-write clone. The code digest the server advertises on /capabilities covers exactly the protocol's modules, the modules its other routes run and the modules its /status judges freshness with.
"""

import ast
import errno
import json
import os
import threading
import time
from pathlib import Path

import pytest

from engine.review import landing
from engine.review.serve import DELTA_FORMAT, receive_autosave_locked
from engine.review.store_lock import holder, lock_path_for, store_lock
from engine.review.verdict_store import (
    ServedCorpus,
    VerdictStore,
    orphan_path,
    orphans_dir_for,
)


def record(unit, verdict="approve", note="", at="2026-09-01T00:00:00Z"):
    return {"unit": unit, "verdict": verdict, "note": note, "at": at}


def document(stamp, records, cleared=None):
    data = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": records,
    }
    if cleared:
        data["cleared"] = [{"unit": unit, "at": at} for unit, at in sorted(cleared.items())]
    return data


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".write")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def corpus(path: Path, stamp: str, ids, marker: str = "") -> Path:
    write(path / "manifest.json", {"generated_at": stamp, "human_unit_ids": sorted(ids)})
    (path / "units-000.json").write_text(marker or stamp)
    return path


def units(path: Path) -> dict[str, dict]:
    return {entry["unit"]: entry for entry in json.loads(path.read_text())["verdicts"]}


def tombstones(path: Path) -> dict[str, str]:
    return {entry["unit"]: entry["at"] for entry in json.loads(path.read_text()).get("cleared", [])}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    live = corpus(root / "engine" / "out" / "review", "A", ["u-1", "u-2", "u-3"])
    autosave = root / "verdicts-autosave.json"
    write(autosave, document("A", [record("u-1"), record("u-2")]))
    return {"root": root, "live": live, "next": live.with_name("review.next"), "autosave": autosave}


def land(repo, **overrides):
    options = dict(staged=repo["next"], live=repo["live"])
    options.update(overrides)
    return landing.land(repo["autosave"], options.pop("corpus", repo["next"]), **options)


def test_a_stamp_move_carries_the_store_by_unit_id_and_orphans_the_rest(repo):
    write(
        repo["autosave"],
        document(
            "A",
            [
                record("u-1", "reject", "too close"),
                record("u-2"),
                record("u-3", "skip", at="2026-09-03T00:00:00Z"),
                record("u-9", "skip"),
            ],
            {"u-4": "2026-09-02T00:00:00Z", "u-5": "2026-09-02T00:00:00Z"},
        ),
    )
    corpus(repo["next"], "B", ["u-1", "u-3", "u-4"], marker="new")
    result = land(repo)
    assert result.landed and result.carried and (result.old_stamp, result.new_stamp) == ("A", "B")
    assert (result.records, result.orphaned, result.skips_dropped) == (1, 1, 2)
    assert (repo["live"] / "units-000.json").read_text() == "new"
    assert json.loads(repo["autosave"].read_text())["manifest_generated_at"] == "B"
    assert units(repo["autosave"]) == {"u-1": record("u-1", "reject", "too close")}
    assert tombstones(repo["autosave"]) == {"u-3": "2026-09-03T00:00:00Z", "u-4": "2026-09-02T00:00:00Z"}
    orphans = json.loads(orphan_path(orphans_dir_for(repo["autosave"]), "A").read_text())
    assert orphans["manifest_generated_at"] == "A"
    assert [(entry["unit"], entry["reason"]) for entry in orphans["verdicts"]] == [("u-2", "orphan")]
    assert not repo["next"].exists() and not landing.discard_paths(repo["live"])


def test_a_store_already_on_the_new_stamp_and_a_missing_store_are_left_as_they_are(repo):
    before = repo["autosave"].read_bytes()
    corpus(repo["next"], "A", ["u-1"], marker="promoted")
    result = land(repo)
    assert result.landed and not result.carried and result.records == 2
    assert repo["autosave"].read_bytes() == before
    assert (repo["live"] / "units-000.json").read_text() == "promoted"
    repo["autosave"].unlink()
    corpus(repo["next"], "B", ["u-1"], marker="newer")
    result = land(repo)
    assert result.landed and not result.carried and result.old_stamp is None
    assert not repo["autosave"].exists()
    assert (repo["live"] / "units-000.json").read_text() == "newer"


def test_a_land_with_no_staged_tree_carries_the_store_onto_the_served_corpus(repo):
    corpus(repo["live"], "B", ["u-1"])
    result = land(repo, corpus=repo["live"], staged=None, live=None)
    assert result.landed and result.carried and result.records == 1
    assert json.loads(repo["autosave"].read_text())["manifest_generated_at"] == "B"


@pytest.mark.parametrize(
    "verdicts",
    [[{"unit": "u-1", "verdict": "approve"}], [record("u-1"), {**record("u-1", "reject"), "at": 5}]],
)
def test_the_land_aborts_and_moves_nothing_when_the_store_holds_a_record_it_refuses(repo, verdicts):
    write(repo["autosave"], document("A", verdicts))
    before = repo["autosave"].read_bytes()
    corpus(repo["next"], "B", ["u-1"], marker="new")
    result = land(repo)
    assert not result.landed and "holds a verdict record on u-1" in result.reason
    assert repo["autosave"].read_bytes() == before
    assert (repo["live"] / "units-000.json").read_text() == "A"
    assert (repo["next"] / "units-000.json").read_text() == "new"


def test_a_land_that_keeps_its_discard_leaves_the_old_tree_for_the_caller(repo):
    corpus(repo["next"], "B", ["u-1"], marker="new")
    result = land(repo, keep_discard=True)
    assert result.discard is not None and result.discard == str(landing.discard_path_for(repo["live"]))
    assert (Path(result.discard) / "units-000.json").read_text() == "A"


def test_the_command_line_writes_its_report_into_the_run_directory(repo, capsys):
    corpus(repo["next"], "B", ["u-1"], marker="new")
    run = repo["root"] / "var" / "cycle" / "run-1"
    argv = ["--autosave", str(repo["autosave"]), "--run-dir", str(run), "--corpus", str(repo["next"])]
    argv += ["--staged", str(repo["next"]), "--live", str(repo["live"])]
    assert landing.main(argv) == 0
    report = json.loads((run / landing.REPORT_NAME).read_text())
    assert report["landed"] and report["carried"] and report["new_stamp"] == "B"
    assert "carried the store onto B" in capsys.readouterr().out


def test_a_store_a_killed_land_left_on_the_old_stamp_is_carried_by_the_next_save(repo, monkeypatch):
    """A land killed between its swap and its store write leaves the new corpus served beside a store on the old stamp; the next save stamped for the served corpus carries the store onto it by unit id before it applies (rule 3 in `engine.review.verdict_store`)."""
    corpus(repo["next"], "B", ["u-1", "u-3"], marker="new")

    class Killed(BaseException):
        pass

    def killed(*_args, **_kwargs):
        raise Killed()

    monkeypatch.setattr(landing, "write_store", killed)
    with pytest.raises(Killed):
        land(repo)
    monkeypatch.undo()
    assert (repo["live"] / "units-000.json").read_text() == "new"
    assert json.loads(repo["autosave"].read_text())["manifest_generated_at"] == "A"
    store = VerdictStore(repo["autosave"])
    body = {
        "format": DELTA_FORMAT,
        "manifest_generated_at": "B",
        "sets": [record("u-3", "reject", at="2026-09-09T00:00:00Z")],
    }
    status, reply = store.receive(json.dumps(body).encode(), ServedCorpus("B", frozenset({"u-1", "u-3"})))
    assert status == 200 and reply["corpus_stamp"] == "B"
    assert set(units(repo["autosave"])) == {"u-1", "u-3"}
    orphans = json.loads(orphan_path(orphans_dir_for(repo["autosave"]), "A").read_text())
    assert [entry["unit"] for entry in orphans["verdicts"]] == ["u-2"]


def test_the_land_waits_for_a_writer_that_holds_the_lock_and_a_save_waits_for_the_land(repo):
    """The land takes the store's lock, waiting for another writer to let go of it, and the server's locked save path answers a retryable 503 while a land holds it and reads the served corpus only once it holds the lock itself."""
    corpus(repo["next"], "B", ["u-1"], marker="new")
    results: list[landing.LandResult] = []
    with store_lock(repo["autosave"]):
        worker = threading.Thread(target=lambda: results.append(land(repo)))
        worker.start()
        time.sleep(0.3)
        assert not results and (repo["live"] / "units-000.json").read_text() == "A"
        store = VerdictStore(repo["autosave"])
        body = json.dumps(
            {"format": DELTA_FORMAT, "manifest_generated_at": "A", "sets": [record("u-3")]}
        ).encode()
        status, reply = receive_autosave_locked(store, body)
        assert status == 503 and reply["retry"] is True
    worker.join(timeout=10)
    assert results and results[0].landed
    reads: list[str | None] = []

    def served() -> ServedCorpus:
        reads.append(holder(lock_path_for(repo["autosave"])))
        return ServedCorpus("B", frozenset({"u-1"}))

    store = VerdictStore(repo["autosave"])
    body = json.dumps(
        {
            "format": DELTA_FORMAT,
            "manifest_generated_at": "B",
            "sets": [record("u-1", "either", at="2026-09-09T00:00:00Z")],
        }
    ).encode()
    status, _reply = receive_autosave_locked(store, body, served)
    assert status == 200 and len(reads) == 1 and reads[0] is not None


def test_the_fallback_puts_the_live_tree_back_when_the_staged_one_will_not_move(tmp_path, monkeypatch):
    staged, live = tmp_path / "review.next", tmp_path / "review"
    corpus(staged, "B", [], marker="new")
    corpus(live, "A", [], marker="old")
    real_replace = os.replace
    calls: list[tuple] = []

    def failing_second(src, dst):
        calls.append((src, dst))
        if len(calls) == 2:
            raise OSError("simulated")
        return real_replace(src, dst)

    monkeypatch.setattr(landing.os, "replace", failing_second)
    with pytest.raises(OSError, match="simulated"):
        landing.exchange_dirs(staged, live, atomic=False)
    assert (live / "units-000.json").read_text() == "old"
    assert (staged / "units-000.json").read_text() == "new"


def test_a_filesystem_that_refuses_the_swap_call_falls_back_to_the_renames(tmp_path, monkeypatch):
    """A swap call the filesystem does not support (ENOTSUP, EINVAL, ENOSYS) takes the three-rename fallback; any other error is raised."""
    staged, live = tmp_path / "review.next", tmp_path / "review"
    corpus(staged, "B", [], marker="new")
    corpus(live, "A", [], marker="old")

    def unsupported(a, b):
        raise OSError(errno.ENOTSUP, os.strerror(errno.ENOTSUP), str(a))

    monkeypatch.setattr(landing, "_swap_call", unsupported)
    landing.exchange_dirs(staged, live)
    assert (live / "units-000.json").read_text() == "new"
    assert (staged / "units-000.json").read_text() == "old"

    def denied(a, b):
        raise OSError(errno.EACCES, os.strerror(errno.EACCES), str(a))

    monkeypatch.setattr(landing, "_swap_call", denied)
    with pytest.raises(PermissionError):
        landing.exchange_dirs(staged, live)
    assert (live / "units-000.json").read_text() == "new"


def test_a_tree_moved_to_discard_never_replaces_a_leftover_one(tmp_path):
    """Moving a tree to discard only renames, since it can run under the store's lock: a leftover discard tree keeps its name and the new one takes the next free numbered name, and the cycle finds both (`discard_paths`)."""
    live = corpus(tmp_path / "review", "A", [])
    leftover = corpus(landing.discard_path_for(live), "old", [], marker="leftover")
    tree = corpus(tmp_path / "review.next", "B", [], marker="swapped-out")
    moved = landing.move_to_discard(tree, live)
    assert moved == tmp_path / "review.discard-1"
    assert (moved / "units-000.json").read_text() == "swapped-out"
    assert (leftover / "units-000.json").read_text() == "leftover"
    assert landing.discard_paths(live) == [leftover, moved]


def test_clone_tree_writes_never_touch_the_source(tmp_path):
    source = corpus(tmp_path / "review", "A", [], marker="served")
    (source / "sub").mkdir()
    (source / "sub" / "shard.bin").write_bytes(b"x" * 4096)
    target = tmp_path / "review.next"
    assert landing.clone_tree(source, target) in ("clone", "copy")
    (target / "units-000.json").write_text("rebuilt")
    with (target / "sub" / "shard.bin").open("r+b") as handle:
        handle.write(b"y")
    assert (source / "units-000.json").read_text() == "served"
    assert (source / "sub" / "shard.bin").read_bytes() == b"x" * 4096


def test_the_code_digest_moves_with_a_protocol_served_or_freshness_module_only(tmp_path):
    """The digest the server advertises and the cycle compares covers exactly the land protocol's modules, the modules the server answers its other routes from with the pipeline and validation trees those run, and the modules its /status judges freshness with, so an edit to one of them, the kern stage the kerning routes run among them, makes the cycle restart a server started before it, and an edit to another review module or a tool does not. A missing module hashes as empty, so the freshness and served modules are checked to exist in the tree."""
    engine_dir = Path(landing.__file__).resolve().parent.parent
    assert all((engine_dir / name).is_file() for name in landing.FRESHNESS_MODULES)
    assert all((engine_dir / "review" / name).is_file() for name in landing.SERVED_MODULES)
    review = tmp_path / "review"
    watched = [review / name for name in landing.PROTOCOL_MODULES]
    watched += [review / name for name in landing.SERVED_MODULES]
    watched += [tmp_path / name for name in landing.FRESHNESS_MODULES]
    watched += [
        tmp_path / "pipeline" / "kern.py",
        tmp_path / "pipeline" / "spec_load.py",
        tmp_path / "validation" / "shaping.py",
    ]
    others = [review / "build.py", tmp_path / "tools" / "artifact_cycle.py"]
    for path in (*watched, *others):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
    base = landing.code_digest(review)
    for other in others:
        other.write_text("x = 2\n")
        assert landing.code_digest(review) == base, other
    for path in watched:
        path.write_text("x = 2\n")
        assert landing.code_digest(review) != base, path
        path.write_text("x = 1\n")


# The modules outside the digest's trees (`landing.served_paths`) that the kerning routes' trees import, each with why an edit to it cannot change a route's answer. `test_the_code_digest_covers_every_module_the_kerning_routes_run` walks the imports and keeps this list exact.
ROUTE_IMPORTS_OUTSIDE_THE_DIGEST = {
    "engine/review/__init__.py": "an empty package file",
    "engine/review/store_lock.py": "a land-protocol module, which the digest covers by `PROTOCOL_MODULES`",
    "engine/tools/artifact_cycle.py": "imported inside run_build's and run_build_gates' build and gate functions, which the routes never call",
    "engine/tools/console.py": "the build's terminal lines",
    "engine/tools/cycle_paths.py": "the cycle's own paths, read by build and gate functions the routes never call",
    "engine/tools/cycle_timings.py": "the timings journal",
    "engine/tools/green_record.py": "the cycle's green records, read by build and gate functions the routes never call",
    "engine/tools/lock_digest.py": "uv.lock's line of the run_build key, which the routes never compute",
    "engine/tools/memory_budget.py": "pool widths",
    "engine/tools/peak_rss.py": "memory telemetry",
}


def test_the_code_digest_covers_every_module_the_kerning_routes_run():
    """Walk the kerning routes' imports, inside functions too, through every module the digest covers, and check that what lies outside it is exactly `ROUTE_IMPORTS_OUTSIDE_THE_DIGEST`: a new import of a module whose edit could change an answer has to join the digest or this list, with a reason."""
    review = Path(landing.__file__).resolve().parent
    root = review.parent.parent
    served = {path.resolve() for path in landing.served_paths(review)}
    assert {
        review / "kern_routes.py",
        review / "kern_writer.py",
        root / "engine" / "pipeline" / "kern.py",
        root / "engine" / "pipeline" / "table.py",
    } <= served
    seen: set[Path] = set()
    outside: set[str] = set()
    queue = [review / name for name in landing.SERVED_MODULES]
    while queue:
        path = queue.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        if path not in served:
            outside.add(path.relative_to(root).as_posix())
            continue
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
            else:
                continue
            for name in names:
                if name.split(".")[0] != "engine":
                    continue
                stem = root.joinpath(*name.split("."))
                found = next(
                    (
                        candidate
                        for candidate in (stem.with_suffix(".py"), stem / "__init__.py")
                        if candidate.is_file()
                    ),
                    None,
                )
                if found is not None:
                    queue.append(found)
    assert outside == set(ROUTE_IMPORTS_OUTSIDE_THE_DIGEST)


def test_the_server_advertises_the_protocol_its_code_its_root_and_its_store():
    from engine.review import serve

    code = landing.code_digest(Path(serve.__file__).resolve().parent)
    assert serve.capabilities_payload(code) == {
        "land_protocol": landing.LAND_PROTOCOL,
        "code": code,
        "root": str(serve.REPO_ROOT),
        "autosave": str(serve.AUTOSAVE_PATH),
    }

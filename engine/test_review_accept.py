"""Tests for the table-diff accept and the remake (`engine/review/accept.py`): each refusal of the accept, the dry run, which writes nothing, an `--apply`, which takes the snapshot, writes the pointer, empties the store under its lock beside a listening review server and tells the open tabs, `--retake`, and the remake, over a stubbed export and build, in a git tree whose last commit to the pointer names the snapshot.

The build is a real table build of the pipeline's mini fixture spec with the frozen mini bundle's font and a small kern table beside it, as engine/test_review_snapshot.py builds one; the corpus is a manifest written by hand, since the accept reads only its stamp, its human units, its after font and its `tables` component. The readiness check and the reload sent to the open tabs are stubbed.
"""

import functools
import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.pipeline import fingerprint, fixtures, run_build
from engine.review import accept, snapshot
from engine.review.store_lock import hold_flock, holder, lock_path_for
from engine.review.verdict_store import AUTOSAVE_NAME
from engine.tools import store_guards

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI_FONT = REPO_ROOT / "engine" / "review" / "fixtures" / "mini" / "Senior-Regular.otf"
SPEC = fixtures.mini_spec()
STAMP = "2026-11-26T09:00:00Z"
HUMAN = ["u-1AAAAAAAAAA", "u-1BBBBBBBBBB"]
READY = ({"checks": {"corpus": {"level": "ok", "detail": "present"}}}, True)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("accept-build")
    run_build.build_tables(SPEC, out, inputs="fp-accept")
    shutil.copyfile(MINI_FONT, out / "Senior-Regular.otf")
    (out / "kern.tsv").write_text("# kern table\nleading\tleading_side\n", encoding="utf-8")
    return out


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def repo(tmp_path, built):
    """A checkout's files the accept reads and writes: the served table-diff corpus's manifest, the store with an approving verdict on every human unit, and the snapshot home and pointer it writes."""
    _write_json(
        tmp_path / "engine" / "out" / "review" / "manifest.json",
        {
            "mode": "table-diff",
            "generated_at": STAMP,
            "human_unit_ids": HUMAN,
            "totals": {"units": 3},
            "baseline": {"commit": None, "taken_at": None, "digest": None, "present": False},
            "fonts": {"after": {"sha256": fingerprint.file_sha256(built / "Senior-Regular.otf")}},
            "inputs_fingerprint": {"tables": "fp-accept"},
        },
    )
    _store(tmp_path, [(unit, "approve") for unit in HUMAN])
    return tmp_path


def _store(root: Path, verdicts) -> None:
    _write_json(
        root / AUTOSAVE_NAME,
        {
            "format": "ams-review-verdicts/1",
            "manifest_generated_at": STAMP,
            "exported_at": STAMP,
            "verdicts": [{"unit": unit, "verdict": kind, "note": "", "at": STAMP} for unit, kind in verdicts],
        },
    )


def _run(root: Path, built: Path, **kwargs) -> int:
    kwargs.setdefault("readiness", lambda: READY)
    kwargs.setdefault("tell", lambda _store, _digest: False)
    kwargs.setdefault("take", functools.partial(snapshot.take, spec=SPEC))
    return accept.run(
        store=root / AUTOSAVE_NAME,
        corpus=root / "engine" / "out" / "review",
        tables=built,
        home=root / "home",
        pointer=root / snapshot.POINTER_RELATIVE,
        **kwargs,
    )


def _untouched(root: Path, before: bytes) -> None:
    assert (root / AUTOSAVE_NAME).read_bytes() == before
    assert not (root / snapshot.POINTER_RELATIVE).exists()
    assert not (root / "home").exists() or snapshot.current(root / "home") is None


def test_the_accept_refuses_a_corpus_with_no_manifest(repo, built, capsys):
    """The accept refuses a corpus with no manifest that reads, dry run, apply and retake alike, and writes nothing."""
    manifest_path = repo / "engine" / "out" / "review" / "manifest.json"
    before = (repo / AUTOSAVE_NAME).read_bytes()
    manifest_path.unlink()
    for flags in ({}, {"apply": True}, {"apply": True, "retake": True}):
        assert _run(repo, built, **flags) == 1
        assert "has no manifest that reads" in capsys.readouterr().out
        _untouched(repo, before)


@pytest.mark.parametrize("case", ["not-ready", "other-font", "other-tables", "pass"])
def test_the_accept_refuses_while_the_corpus_is_not_ready_or_not_this_build_or_a_pass_runs(
    repo, built, capsys, case
):
    manifest_path = repo / "engine" / "out" / "review" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    kwargs: dict = {}
    if case == "not-ready":
        failing = {
            "checks": {
                "snapshot": {
                    "level": "fail",
                    "detail": "no snapshot",
                    "remedy": "make review-snapshot-remake",
                }
            }
        }
        kwargs["readiness"] = lambda: (failing, False)
    elif case == "other-font":
        _write_json(manifest_path, {**manifest, "fonts": {"after": {"sha256": "0" * 64}}})
    elif case == "other-tables":
        _write_json(manifest_path, {**manifest, "inputs_fingerprint": {"tables": "fp-other"}})
    before = (repo / AUTOSAVE_NAME).read_bytes()
    expected = {
        "not-ready": "blocking checks fail",
        "other-font": "after font is not",
        "other-tables": "built over other tables",
        "pass": "a pass holds",
    }[case]
    held = hold_flock(store_guards.pass_lock_path_for(repo / AUTOSAVE_NAME)) if case == "pass" else None
    try:
        if held is not None:
            held.__enter__()
        assert _run(repo, built, **kwargs) == 1
        assert expected in capsys.readouterr().out
        assert _run(repo, built, apply=True, **kwargs) == 1
        out = capsys.readouterr().out
        assert expected in out and "Nothing was written" in out
    finally:
        if held is not None:
            held.__exit__(None, None, None)
    _untouched(repo, before)


@pytest.mark.parametrize("verdict", [None, "skip", "reject", "neither"])
def test_the_accept_refuses_while_a_human_unit_is_blank_skipped_rejected_or_neither(
    repo, built, capsys, verdict
):
    _store(repo, [(HUMAN[0], "approve")] + ([(HUMAN[1], verdict)] if verdict is not None else []))
    before = (repo / AUTOSAVE_NAME).read_bytes()
    assert _run(repo, built, apply=True) == 1
    out = capsys.readouterr().out
    assert f"1 {verdict or 'blank'}" in out and "of the corpus's 2 human units" in out
    _untouched(repo, before)


def test_the_dry_run_prints_what_the_apply_writes_and_writes_nothing(repo, built, capsys):
    before = (repo / AUTOSAVE_NAME).read_bytes()
    assert _run(repo, built) == 0
    out = capsys.readouterr().out
    assert (
        "--apply would:" in out
        and "take a snapshot of" in out
        and "write an empty verdicts-autosave.json" in out
    )
    assert "dropping its 2 verdicts" in out and "ERROR" not in out
    _untouched(repo, before)


def test_the_apply_takes_the_snapshot_writes_the_pointer_and_empties_the_store(
    repo, built, capsys, monkeypatch
):
    """The apply runs beside a listening review server: it writes the empty store holding the store's lock, which the server's saves take without waiting, so a save made meanwhile gets a retryable 503, and then tells the open tabs to reload onto the empty store by the snapshot's digest."""
    store = repo / AUTOSAVE_NAME
    before = store.read_bytes()
    locked: list[bool] = []
    write_store = accept.write_store

    def write_locked(path, *args, **kwargs):
        locked.append(holder(lock_path_for(path)) is not None)
        return write_store(path, *args, **kwargs)

    monkeypatch.setattr(accept, "write_store", write_locked)
    told: list[tuple[Path, str, list]] = []

    def tell(path: Path, digest: str) -> bool:
        told.append((path, digest, json.loads(path.read_text(encoding="utf-8"))["verdicts"]))
        return True

    assert _run(repo, built, apply=True, tell=tell) == 0
    out = capsys.readouterr().out
    assert locked == [True]
    current = snapshot.current(repo / "home")
    assert current is not None
    taken = snapshot.load(current)
    assert snapshot.read_pointer(repo / snapshot.POINTER_RELATIVE) == snapshot.pointer_record(taken)
    document = json.loads(store.read_text(encoding="utf-8"))
    assert document["manifest_generated_at"] == STAMP and document["verdicts"] == []
    assert "accepted" not in document and "cleared" not in document
    assert gzip.decompress((repo / accept.KEEP / accept.KEPT_STORE).read_bytes()) == before
    assert f"the snapshot {taken.digest[:12]} is current" in out
    assert told == [(store, taken.digest[:12], [])] and "the open review tabs reload onto it" in out


def test_the_open_tabs_are_told_only_while_this_checkouts_server_listens(tmp_path, monkeypatch):
    """`tell_open_tabs` sends `ams:store/<digest>` to livereload's /forcereload only while a review server listens that may write the store (`store_guards.serves`), and returns whether the server took it."""
    from engine.tools import review_server

    sent: list[str] = []
    monkeypatch.setattr(review_server, "force_reload", lambda path: sent.append(path) or True)
    monkeypatch.setattr(store_guards, "serves", lambda _store: False)
    assert accept.tell_open_tabs(tmp_path / AUTOSAVE_NAME, "95eaed0f44db") is False and sent == []
    monkeypatch.setattr(store_guards, "serves", lambda _store: True)
    assert accept.tell_open_tabs(tmp_path / AUTOSAVE_NAME, "95eaed0f44db") is True
    assert sent == ["ams:store/95eaed0f44db"]


def test_a_retake_skips_the_verdicts_and_the_readiness_and_names_the_span_it_accepts_unreviewed(
    repo, built, capsys
):
    _store(repo, [(HUMAN[0], "reject")])
    failing = {"checks": {"snapshot": {"level": "fail", "detail": "another format", "remedy": "retake"}}}
    assert (
        _run(
            repo,
            built,
            apply=True,
            retake=True,
            readiness=lambda: (failing, False),
            stamped=lambda _tables: False,
        )
        == 1
    )
    assert "do not carry the stamp the sources on disk give" in capsys.readouterr().out
    assert (
        _run(
            repo,
            built,
            apply=True,
            retake=True,
            readiness=lambda: (failing, False),
            stamped=lambda _tables: True,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "--retake accepts unreviewed every change since no snapshot" in out
    assert "2 of them human units, 2 not accepted" in out
    assert snapshot.current(repo / "home") is not None


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def pointed(tmp_path, built):
    """A git tree whose last commit to the pointer names the snapshot of the mini build, taken in a home of its own."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    taken = snapshot.take(built, tmp_path / "original", spec=SPEC)
    (root / "engine").mkdir()
    snapshot.write_pointer(taken, root / snapshot.POINTER_RELATIVE)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "accept")
    return root, taken


def _remake(root: Path, home: Path, built: Path, source: Path | None = None) -> int:
    """Run the remake with an export that makes an empty tree and a build that takes a snapshot of `source`, the mini build unless another is named, as the tree's own build and snapshot would."""

    def export(_revision: str, tree: Path) -> None:
        tree.mkdir(parents=True)

    def build(_tree: Path, _out: Path, scratch_home: Path) -> None:
        snapshot.take(source or built, scratch_home, spec=SPEC)

    return accept.remake(
        home=home,
        work=root / "work",
        store=root / AUTOSAVE_NAME,
        repo_root=root,
        export=export,
        build=build,
    )


def test_the_remake_builds_the_pointers_commit_and_installs_the_snapshot_once_its_digest_matches(
    pointed, built, tmp_path, capsys
):
    root, taken = pointed
    home = tmp_path / "home"
    assert _remake(root, home, built) == 0
    assert "installed" in capsys.readouterr().out
    current = snapshot.current(home)
    assert current is not None and snapshot.load(current).digest == taken.digest
    assert not (root / "work").exists()
    assert _remake(root, home, built) == 0
    assert "already holds" in capsys.readouterr().out


def test_a_remake_whose_build_gives_another_digest_names_the_retake_and_installs_nothing(
    pointed, built, tmp_path, capsys
):
    root, _taken = pointed
    other = tmp_path / "other-build"
    shutil.copytree(built, other)
    (other / "kern.tsv").write_text("# another kern table\n", encoding="utf-8")
    home = tmp_path / "home"
    assert _remake(root, home, built, source=other) == 1
    out = capsys.readouterr().out
    assert "not" in out and "make review-accept ARGS='--apply --retake'" in out
    assert snapshot.current(home) is None and (root / "work").exists()


def test_the_remake_refuses_with_no_pointer_commit_or_another_snapshot_format(tmp_path, built, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    assert _remake(root, tmp_path / "home", built) == 1
    assert "no commit has changed" in capsys.readouterr().out
    (root / "engine").mkdir()
    _write_json(
        root / snapshot.POINTER_RELATIVE,
        {
            "format": snapshot.POINTER_FORMAT,
            "snapshot_format": "ams-review-snapshot/1",
            "digest": "0" * 64,
            "taken_at": None,
        },
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "old format")
    assert _remake(root, tmp_path / "home", built) == 1
    assert "reads only" in capsys.readouterr().out


def test_the_remake_environment_shares_this_checkouts_caches_without_its_virtual_environment(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/.venv")
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    environment = accept.remake_environment(tmp_path)
    assert "VIRTUAL_ENV" not in environment
    assert environment["UV_CACHE_DIR"] == str(tmp_path / ".uv-cache")
    assert environment["CARGO_TARGET_DIR"] == str(
        tmp_path / "engine" / "kernel-rs" / "target" / "snapshot-remake"
    )

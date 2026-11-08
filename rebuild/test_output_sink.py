"""Tests for `rebuild/pipeline/output_sink.py`: that each form of the writer helper leaves an output whose bytes come out the same in place and writes one whose bytes differ, that it writes a FIFO or a link to the null device at the path as the plain form does without blocking, and that its counts reach the pass's counts file only while the cycle names one, in the line format `cycle_timings` sums."""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from rebuild.pipeline import output_sink
from rebuild.tools import cycle_timings

LONG_AGO_NS = 1_000_000_000


def _from_source(target: Path, data: bytes, keep: bool) -> bool:
    source = target.with_name(target.name + ".source")
    source.write_bytes(data)
    return output_sink.replace_output(target, source=source, keep_if_equal=keep)


def _from_writer(target: Path, data: bytes, keep: bool) -> bool:
    return output_sink.replace_output(
        target,
        writer=lambda staging: staging.write_bytes(data),
        sha256=hashlib.sha256(data).hexdigest(),
        keep_if_equal=keep,
    )


FORMS: dict[str, Callable[[Path, bytes, bool], bool]] = {
    "write_output": lambda target, data, keep: output_sink.write_output(target, data, keep_if_equal=keep),
    "replace_output": lambda target, data, keep: output_sink.replace_output(target, data, keep_if_equal=keep),
    "replace_output from a source": _from_source,
    "replace_output from a writer": _from_writer,
}
IN_PLACE = {"write_output"}


@pytest.fixture
def counts(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "counts.tsv"
    path.write_bytes(b"")
    monkeypatch.setenv(output_sink.OUTPUT_COUNTS_ENV, str(path))
    return path


@pytest.mark.parametrize("keep", [True, False])
@pytest.mark.parametrize("form", FORMS)
def test_an_output_whose_bytes_come_out_the_same_keeps_its_inode_and_mtime(tmp_path, counts, form, keep):
    """A missing target is written. With `keep_if_equal` a second write of the same bytes writes nothing, so the file keeps its inode and its modification time, here set long ago so that any write would move it, and counts its bytes as kept; bytes of the same length that differ are written and counted as changed. Without it every call writes, as the plain write does, and counts as written."""
    write = FORMS[form]
    target = tmp_path / "summary.json"
    assert write(target, b"first\n", keep)
    os.utime(target, ns=(LONG_AGO_NS, LONG_AGO_NS))
    before = target.stat()
    assert write(target, b"first\n", keep) is not keep
    after = target.stat()
    assert ((after.st_ino, after.st_mtime_ns) == (before.st_ino, LONG_AGO_NS)) is keep
    assert write(target, b"fresh\n", keep)
    assert target.read_bytes() == b"fresh\n"
    assert not list(tmp_path.glob("*.partial"))
    expected = ["changed\t6", "kept\t6", "changed\t6"] if keep else ["written\t6"] * 3
    assert counts.read_text().splitlines() == expected


def _fifo(path: Path) -> Path:
    os.mkfifo(path)
    return path


def _null_link(path: Path) -> Path:
    path.symlink_to(os.devnull)
    return path


def _what_is_at(path: Path) -> str:
    if path.is_symlink():
        return f"link to {os.readlink(path)}"
    if path.is_fifo():
        return "fifo"
    return f"file holding {path.read_bytes()!r}"


def _run_beside(target: Path, call: Callable[[], object], *, drain: bool) -> list[bytes]:
    """Run `call` on a thread and fail unless it returns within the timeout, so a call that opens a FIFO no writer holds fails the test instead of hanging it. With `drain`, a second thread reads the FIFO at `target` to its end first, as the reader an in-place write to a FIFO needs, and the bytes it read are returned."""
    drained: list[bytes] = []
    reader = None
    if drain:

        def read() -> None:
            with target.open("rb") as handle:
                drained.append(handle.read())

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "the write blocked"
    if reader is not None:
        reader.join(10)
        assert not reader.is_alive(), "the FIFO's reader got no end of file"
    return drained


@pytest.mark.parametrize("make", [_fifo, _null_link], ids=["fifo", "null-device link"])
@pytest.mark.parametrize("form", FORMS)
def test_a_target_that_is_not_a_regular_file_is_written_as_the_plain_form_writes_it(
    tmp_path, counts, form, make
):
    """The compare never opens a FIFO, which blocks while no writer holds it, or the null device: `os.stat` finds that neither is a regular file, and the helper writes it as the plain form does. An in-place write goes through the FIFO, to its reader, or through the link, which stays; a replacing write renames a regular file over either. The call counts as written, since no compare ran."""
    seen = {}
    for keep in (False, True):
        directory = tmp_path / f"keep-{keep}"
        directory.mkdir()
        target = make(directory / "settlement-default.tsv")
        drained = _run_beside(
            target, lambda: FORMS[form](target, b"rows\n", keep), drain=make is _fifo and form in IN_PLACE
        )
        seen[keep] = (_what_is_at(target), drained)
    assert seen[True] == seen[False]
    if form in IN_PLACE:
        assert seen[True][0] == ("fifo" if make is _fifo else f"link to {os.devnull}")
    else:
        assert seen[True][0] == "file holding b'rows\\n'"
    assert counts.read_text().splitlines() == ["written\t5"] * 2


def test_the_counts_reach_the_file_only_while_the_cycle_names_one(tmp_path, monkeypatch):
    """A call counts nothing without `OUTPUT_COUNTS_ENV`, as a hand run, the fault kit, the memory series and the tests have none, and nothing when the file it names is gone, since a process that outlives its pass must not create the file again. While it names the pass's file, each call appends one line, which `cycle_timings.sum_output_counts` sums into the run line's `outputs` block, and an output held as a base and segments that a writer rewrote whole outside a fold, the share rule or a letter batch (`note_rewritten`) is named there under `rewritten`, once however often it was counted. The two modules spell the variable and the outcomes out, and they agree."""
    assert cycle_timings.OUTPUT_COUNTS_ENV == output_sink.OUTPUT_COUNTS_ENV
    assert cycle_timings.OUTPUT_OUTCOMES == (output_sink.CHANGED, output_sink.WRITTEN, output_sink.KEPT)
    assert cycle_timings.OUTPUT_REWRITTEN == output_sink.REWRITTEN
    counts = tmp_path / "counts.tsv"
    counts.write_bytes(b"")
    target = tmp_path / "pins.json"
    monkeypatch.delenv(output_sink.OUTPUT_COUNTS_ENV, raising=False)
    output_sink.write_output(target, "{}\n", keep_if_equal=True)
    monkeypatch.setenv(output_sink.OUTPUT_COUNTS_ENV, str(tmp_path / "ended-pass.tsv"))
    output_sink.write_output(target, "{}\n", keep_if_equal=True)
    assert counts.read_bytes() == b"" and not (tmp_path / "ended-pass.tsv").exists()
    monkeypatch.setenv(output_sink.OUTPUT_COUNTS_ENV, str(counts))
    output_sink.write_output(target, "{}\n", keep_if_equal=True)
    output_sink.write_output(target, '{"a": 1}\n', keep_if_equal=True)
    output_sink.replace_output(tmp_path / "scan.json", b"state", keep_if_equal=False)
    assert counts.read_text().splitlines() == ["kept\t3", "changed\t9", "written\t5"]
    assert cycle_timings.sum_output_counts(counts) == {
        "files": 2,
        "bytes": 14,
        "changed_bytes": 9,
        "kept_bytes": 3,
    }
    output_sink.note_rewritten("unit-cache.ndjson.gz")
    output_sink.note_rewritten("unit-cache.ndjson.gz")
    assert counts.read_text().splitlines()[-1] == "rewritten\tunit-cache.ndjson.gz"
    assert cycle_timings.sum_output_counts(counts) == {
        "files": 2,
        "bytes": 14,
        "changed_bytes": 9,
        "kept_bytes": 3,
        "rewritten": ["unit-cache.ndjson.gz"],
    }

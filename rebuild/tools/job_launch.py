"""Run a command as a one-shot launchd job. Launchd gives each job a resource coalition of its own, and every process the command starts joins it, so the write meter (`rebuild/tools/write_meter.py`) reads the job's writes in its own basis, exactly. It is the launcher for the fault kit and the memory series, neither of which launches through it yet: a kit run refuses until #618's change 0k (`doc/perf-check.md`), and the series has no harness. A cycle pass runs where it is started, and `doc/running-long-steps.md`'s `nohup caffeinate -i` recipe is how one is detached.

A launch writes `var/jobs/<label>.plist` under the checkout and bootstraps it into `gui/<uid>`, or into `user/<uid>` when that fails, as it does with no GUI login session. The label is `local.ams.<kind>.<checkout digest>.<stamp>`: the kind the caller names, the first eight hex digits of the SHA-256 of the checkout's path (`checkout_digest`), so two checkouts' jobs never share a label, and the UTC second the launch began, with `-2`, `-3` and so on after it when a launch of the same kind in that second holds the plain label (`write_job` creates the plist exclusively). The plist (`job_plist`) holds:

- `ProgramArguments`: `/usr/bin/caffeinate -i` ahead of the command, since a launchd job holds no power assertion of its own and idle sleep would suspend it. `caffeinate` execs the command and holds the assertion from a child it forks, so the job's pid is the command's and a signal sent to the job reaches the command.
- `WorkingDirectory`: the checkout.
- `EnvironmentVariables`: `HOME`, `USER`, `LOGNAME`, `LANG`, `TMPDIR`, `PATH` and `UV_CACHE_DIR` as the caller has them, and every `AMS_*` variable the caller set, and nothing else (`job_environment`), so no secret in the caller's environment is written into the plist. Launchd adds a few of its own, `XPC_SERVICE_NAME` among them.
- `RunAtLoad` true and `KeepAlive` false, so the job runs once, at the bootstrap.
- `AbandonProcessGroup` true, so launchd leaves the processes still in the job's process group running when the command exits.
- `ProcessType` `Interactive`, the one value under which launchd applies no resource limits; left unset, it throttles CPU and I/O (launchd.plist(5)).
- `LimitLoadToSessionType` `Aqua` and `Background`. A launch agent loads only into `Aqua` sessions by default, and `user/<uid>` is a `Background` session, where a bootstrap without the key fails with error 5.
- `StandardOutPath` and `StandardErrorPath`: the run's log, which the launcher empties first, as a shell's `>` would.

The launcher then follows the log to standard output until the job exits, and forwards SIGINT, SIGTERM and SIGHUP to the job with `launchctl kill`, every time one arrives; a signal ignored when the launcher started stays ignored, so a launcher under `nohup` passes no hangup on. It takes the job's exit status from `launchctl print`: the exit code, or 128 plus the signal's number for a job a signal ended, as a shell reports one. Then it boots the job out, deletes the plist and exits with that status. A launcher killed before then leaves the job running and its plist in `var/jobs/`, and `--attach` finishes it.

`--detach` returns once the job runs and prints its label and pid on standard output, `exited` in the pid's place for a job that has already ended. The job outlives the shell that launched it, since launchd runs it. `--attach LABEL` does the rest of a launch for a job under `var/jobs/`: it follows the log from its current end, forwards the stop signals, waits for the job to exit, boots it out, deletes the plist and exits with the job's status. A plist whose job neither domain holds, as after a logout or a restart, is deleted and reported.

A failure of the launcher's own (a bootstrap both domains refuse, a job that has not started `START_TIMEOUT_S` after its bootstrap, a job someone else booted out) prints the reason on standard error and exits 125, as `env` and `timeout` do for a failure of their own; a job it bootstrapped is booted out first. The module imports only the standard library.

Run as: uv run python -m rebuild.tools.job_launch --kind KIND --log FILE [--detach] -- COMMAND [ARG...]; or uv run python -m rebuild.tools.job_launch --attach LABEL.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import plistlib
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, NamedTuple

ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = Path("var") / "jobs"
CAFFEINATE = "/usr/bin/caffeinate"
LAUNCHCTL = "/bin/launchctl"
ENVIRONMENT_NAMES = frozenset({"HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "PATH", "UV_CACHE_DIR"})
ENVIRONMENT_PREFIX = "AMS_"
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
START_TIMEOUT_S = 30.0
POLL_S = 0.5
LAUNCHER_FAILURE = 125
READ_CHUNK_BYTES = 1 << 20
KIND = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_FIELD = re.compile(r"^\t(\w[\w ]*?) = (.*)$", re.MULTILINE)


class JobError(RuntimeError):
    """A failure of the launcher's own, as opposed to the job's exit status."""


@dataclass(frozen=True)
class Job:
    label: str
    plist: Path
    log: Path


class Service(NamedTuple):
    """What `launchctl print` says of a job: its pid while it runs, and its exit status once it has exited."""

    pid: int | None
    status: int | None


def checkout_digest(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:8]


def job_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in environ.items()
        if name in ENVIRONMENT_NAMES or name.startswith(ENVIRONMENT_PREFIX)
    }


def job_plist(
    label: str, command: Sequence[str], *, root: Path, log: Path, environ: Mapping[str, str]
) -> dict[str, object]:
    return {
        "Label": label,
        "ProgramArguments": [CAFFEINATE, "-i", *command],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": job_environment(environ),
        "RunAtLoad": True,
        "KeepAlive": False,
        "AbandonProcessGroup": True,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": ["Aqua", "Background"],
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def write_job(command: Sequence[str], *, kind: str, log: Path, root: Path, environ: Mapping[str, str]) -> Job:
    """Empty the job's log and create its plist under `root`'s `var/jobs/`, creating both directories as needed. The plist is created exclusively, so a label another launch holds is never reused, and a second launch of the kind in the same second takes the first free suffix."""
    if not command:
        raise ValueError("a job needs a command")
    if KIND.fullmatch(kind) is None:
        raise ValueError(f"a job's kind is lowercase letters and digits in hyphenated words, not {kind!r}")
    root = root.resolve()
    log = log.resolve()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"")
    jobs = root / JOBS_DIR
    jobs.mkdir(parents=True, exist_ok=True)
    stem = f"local.ams.{kind}.{checkout_digest(root)}.{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    number = 1
    while True:
        label = stem if number == 1 else f"{stem}-{number}"
        plist = jobs / f"{label}.plist"
        try:
            with plist.open("xb") as stream:
                plistlib.dump(job_plist(label, command, root=root, log=log, environ=environ), stream)
        except FileExistsError:
            number += 1
            continue
        return Job(label, plist, log)


def parse_service(text: str) -> Service:
    """Read `launchctl print`'s top-level fields, the lines one tab deep: `pid` while the job runs, then `last exit code` (`7`, or `78: EX_CONFIG`, and `(never exited)` before the first exit) or `last terminating signal` (`Terminated: 15`)."""
    fields = dict(_FIELD.findall(text))
    pid = int(fields["pid"]) if "pid" in fields else None
    status = None
    if fields.get("state") != "running":
        if "last terminating signal" in fields:
            status = 128 + int(fields["last terminating signal"].rsplit(":", 1)[1])
        elif (code := re.match(r"\d+", fields.get("last exit code", ""))) is not None:
            status = int(code.group())
    return Service(pid, status)


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    # A group of its own, so a Ctrl-C at the terminal reaches only the launcher, which forwards it to the job.
    return subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, process_group=0, check=False)


def _message(result: subprocess.CompletedProcess[str]) -> str:
    return " ".join(result.stderr.split()) or f"exit {result.returncode}"


def _domains() -> tuple[str, str]:
    return f"gui/{os.getuid()}", f"user/{os.getuid()}"


def bootstrap(plist: Path) -> str:
    """Bootstrap the plist into `gui/<uid>`, or `user/<uid>` when that fails, and return the domain that took it."""
    refusals = []
    for domain in _domains():
        result = _launchctl("bootstrap", domain, str(plist))
        if result.returncode == 0:
            return domain
        refusals.append(f"{domain}: {_message(result)}")
    raise JobError(f"launchctl bootstrap refused {plist.name} ({'; '.join(refusals)})")


def read_service(service: str) -> Service:
    result = _launchctl("print", service)
    if result.returncode != 0:
        raise JobError(f"launchctl print {service} failed: {_message(result)}")
    return parse_service(result.stdout)


def wait_started(service: str) -> Service:
    deadline = time.monotonic() + START_TIMEOUT_S
    while True:
        state = read_service(service)
        if state.pid is not None or state.status is not None:
            return state
        if time.monotonic() > deadline:
            raise JobError(f"{service} has not started {START_TIMEOUT_S:.0f} s after its bootstrap")
        time.sleep(POLL_S)


def finish(service: str, plist: Path) -> None:
    _launchctl("bootout", service)
    plist.unlink(missing_ok=True)


@contextlib.contextmanager
def _caught_signals() -> Iterator[list[int]]:
    """Queue SIGINT, SIGTERM and SIGHUP for forwarding instead of acting on them. A signal ignored on entry stays ignored, and off the main thread, where no handler can be installed, nothing is queued."""
    caught: list[int] = []
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in STOP_SIGNALS:
            handler = signal.getsignal(signum)
            if handler == signal.SIG_IGN:
                continue
            previous[signum] = signal.SIG_DFL if handler is None else handler
            signal.signal(signum, lambda received, _frame: caught.append(received))
    try:
        yield caught
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _copy(stream: BinaryIO, out: BinaryIO | None) -> BinaryIO | None:
    """Copy what the log gained to `out`, and return None in its place once writing to it fails, as it does after the terminal closes, so the launcher still reaches its cleanup."""
    while chunk := stream.read(READ_CHUNK_BYTES):
        if out is None:
            continue
        try:
            out.write(chunk)
            out.flush()
        except OSError:
            out = None
    return out


def follow(service: str, log: Path, *, offset: int, caught: list[int], out: BinaryIO | None) -> int:
    """Copy the log from `offset` to `out` and forward each queued signal until the job exits, and return its exit status. The state is read before the copy, so the copy after the state that shows the exit holds everything the job wrote."""
    with log.open("rb") as stream:
        stream.seek(offset)
        while True:
            while caught:
                _launchctl("kill", signal.Signals(caught.pop(0)).name, service)
            state = read_service(service)
            out = _copy(stream, out)
            if state.status is not None:
                return state.status
            time.sleep(POLL_S)


def launch(
    command: Sequence[str],
    *,
    kind: str,
    log: Path,
    detach: bool = False,
    root: Path = ROOT,
    environ: Mapping[str, str] | None = None,
    out: BinaryIO | None = None,
) -> int:
    """Run `command` as a one-shot launchd job and return its exit status, or with `detach` return 0 once it runs. `out` takes the followed log, standard output by default."""
    if sys.platform != "darwin":
        raise JobError("a launchd job needs macOS")
    environ = os.environ if environ is None else environ
    if detach:
        job, service = _bootstrapped(command, kind=kind, log=log, root=root, environ=environ)
        try:
            state = wait_started(service)
        except BaseException:
            finish(service, job.plist)
            raise
        print(f"{job.label} {'exited' if state.pid is None else state.pid}", flush=True)
        print(
            f"job_launch: finish it with `uv run python -m rebuild.tools.job_launch --attach {job.label}`",
            file=sys.stderr,
            flush=True,
        )
        return 0
    with _caught_signals() as caught:
        job, service = _bootstrapped(command, kind=kind, log=log, root=root, environ=environ)
        try:
            wait_started(service)
            return follow(
                service, job.log, offset=0, caught=caught, out=sys.stdout.buffer if out is None else out
            )
        finally:
            finish(service, job.plist)


def _bootstrapped(
    command: Sequence[str], *, kind: str, log: Path, root: Path, environ: Mapping[str, str]
) -> tuple[Job, str]:
    job = write_job(command, kind=kind, log=log, root=root, environ=environ)
    try:
        domain = bootstrap(job.plist)
    except BaseException:
        job.plist.unlink(missing_ok=True)
        raise
    print(f"job_launch: {job.label} runs in {domain}, logging to {job.log}", file=sys.stderr, flush=True)
    return job, f"{domain}/{job.label}"


def attach(label: str, *, root: Path = ROOT, out: BinaryIO | None = None) -> int:
    """Finish a job a detached launch left under `root`'s `var/jobs/`: follow its log from the current end until it exits, boot it out, delete its plist and return its exit status."""
    plist = root.resolve() / JOBS_DIR / f"{label}.plist"
    if not plist.is_file():
        raise JobError(f"no job {label} under {plist.parent}")
    log = Path(str(plistlib.loads(plist.read_bytes())["StandardOutPath"]))
    service = next(
        (
            f"{domain}/{label}"
            for domain in _domains()
            if _launchctl("print", f"{domain}/{label}").returncode == 0
        ),
        None,
    )
    if service is None:
        plist.unlink()
        raise JobError(f"neither {' nor '.join(_domains())} holds {label}; its plist is deleted")
    offset = log.stat().st_size
    with _caught_signals() as caught:
        try:
            return follow(
                service, log, offset=offset, caught=caught, out=sys.stdout.buffer if out is None else out
            )
        finally:
            finish(service, plist)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rebuild.tools.job_launch",
        description="Run a command as a one-shot launchd job, in a resource coalition of its own.",
    )
    parser.add_argument(
        "--kind", help="what the job is, such as fault-kit or memory-series; it goes into the label"
    )
    parser.add_argument(
        "--log", type=Path, help="the run's log, which takes the job's standard output and error"
    )
    parser.add_argument(
        "--detach", action="store_true", help="return once the job runs, printing its label and pid"
    )
    parser.add_argument(
        "--attach", metavar="LABEL", help="finish a detached job: follow it, clean up, exit with its status"
    )
    parser.add_argument("command", nargs="*", help="the command to run, after --")
    args = parser.parse_args(argv)
    try:
        if args.attach is not None:
            if args.kind is not None or args.log is not None or args.detach or args.command:
                parser.error("--attach takes no other option and no command")
            return attach(args.attach)
        if args.kind is None or args.log is None or not args.command:
            parser.error("a launch needs --kind, --log and a command")
        if KIND.fullmatch(args.kind) is None:
            parser.error("--kind is lowercase letters and digits in hyphenated words")
        return launch(args.command, kind=args.kind, log=args.log, detach=args.detach)
    except (JobError, OSError) as error:
        print(f"job_launch: {error}", file=sys.stderr)
        return LAUNCHER_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())

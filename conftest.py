import os
import re
import subprocess
import sys
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any, TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from engine.tools.pyright_gate import Check
    from engine.validation.check import CheckFont
    from engine.validation.corpus import Pin

ROOT = Path(__file__).resolve().parent

# The pages whose `data-expect` pins the session collects as items (`corpus.PIN_PAGES` names them with their directory).
PIN_PAGE_NAMES = ("index.html", "the-manual.html")

# The fonts the session checks: `_published` once the session has published the six fonts into site/ (`_publish_fonts`), in this process or, on a worker, in the controller, then the pin fonts by run font (`_pin_fonts`).
_font_cache: dict[str, Any] = {}

# The pyright check that pytest_configure starts on an engine-only run, for pytest_sessionfinish to join. It is popped, not read, so nothing is waited on twice.
_deferred_pyright: list["Check"] = []


def _join_deferred_pyright(interrupted: bool) -> int:
    if not _deferred_pyright:
        return 0
    check = _deferred_pyright.pop()
    if interrupted:
        check.abandon()
        return 0
    return check.wait()


def _make_env() -> dict[str, str]:
    # The children this file starts, pyright and the publish, run without the MAKEFLAGS and MFLAGS a parent make exports to its recipe. subprocess.run closes inherited fds by default, so a make one of them started would get the jobserver auth string without its fds and print "jobserver unavailable: using -j1".
    env = os.environ.copy()
    env.pop("MAKEFLAGS", None)
    env.pop("MFLAGS", None)
    return env


def _is_engine_only(config: pytest.Config) -> bool:
    engine = (ROOT / "engine").resolve()
    invocation_dir = Path(config.invocation_params.dir)
    targets = [(invocation_dir / arg.split("::", 1)[0]).resolve() for arg in config.args]
    return bool(targets) and all(target == engine or engine in target.parents for target in targets)


def _publish_fonts() -> None:
    """Publish the six fonts the font suite checks into site/ through engine/tools/build_fonts.py's command in a child, which writes nothing when the fonts on disk already hold the bytes. It is a child because that module reaches engine.pipeline, which no import of this file may (`TestTheConftestsStayLeaves` in engine/test_contracts_closure.py). A session never starts a table build, so the command runs without `--build`: when the build's Senior is stale it refuses, naming the build command, and the session fails with its message."""
    result = subprocess.run(
        [sys.executable, "-m", "engine.tools.build_fonts"],
        cwd=ROOT,
        check=False,
        env=_make_env(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise pytest.UsageError(f"the fonts the suite checks did not publish into site/: {message}")
    _font_cache["_published"] = True


def pytest_configure(config: pytest.Config) -> None:
    # Under xdist the controller runs no tests, so it never triggers the lazy publish in _pin_fonts. Publish once here, before the workers spawn, and mark the fonts published so each worker skips the publish it would otherwise run before its first pin.
    if hasattr(config, "workerinput"):
        _font_cache["_published"] = True
        return
    if config.getoption("dist", "no") == "no":
        return
    # An engine-only run reads no site font, so it publishes nothing, and its pyright check is deferred for pytest_sessionfinish to join, so it runs beside the xdist pool instead of before it. Any other run publishes the fonts with the check running beside the publish.
    from engine.tools import pyright_gate

    pyright = pyright_gate.begin(os.environ, ROOT, env=_make_env())
    if _is_engine_only(config):
        if pyright is not None:
            _deferred_pyright.append(pyright)
        return
    try:
        _publish_fonts()
    except pytest.UsageError:
        if pyright is not None:
            pyright.abandon()
        raise
    if pyright is not None and pyright.wait() != 0:
        raise pytest.UsageError("pyright type check failed (see output above)")


# What one font-suite worker holds at its peak. Nothing here divides by it, because the cores limit this pool before memory does (see the hook below). The artifact cycle reads it to estimate `make test`'s pool as a co-resident term when another step shares the machine. Its measurement set is the peak-RSS summary line below, which measured these workers at 0.11–0.33 GB each across runs; the constant is the highest reading rounded up to the tenth of a gigabyte, for the reason kernel_exec.DELTA_SLOT_BYTES errs high: a per-unit cost that errs low can put the machine into swap, while one that errs high only narrows a pool.
FONT_SUITE_WORKER_BYTES = 400_000_000


# Returns the machine's usable cores for `-n auto` everywhere in the repo: the cores this process may run on, counting the affinity mask and any cgroup CPU quota, which os.cpu_count() ignores. A font-suite worker costs only FONT_SUITE_WORKER_BYTES, so the cores limit that pool before memory does. No engine-suite worker reads a live build artifact (engine/conftest.py's audit guard checks this), so `uv run pytest engine/`, a single engine test file, a mixed `pytest engine/ test/`, and `pytest .` all get the same width. memory_budget is imported inside the hook because every pytest run loads this file but only runs that request `-n auto` call the hook. This firstresult hook overrides xdist's own, which is where PYTEST_XDIST_AUTO_NUM_WORKERS is normally read, so this hook reads the variable itself. The variable overrides the default in every collection mode.
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int:
    override = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if override:
        return max(1, int(override))
    from engine.tools.memory_budget import usable_cores

    return usable_cores()


# Peak RSS per xdist worker. Each worker reports its own peak at session finish through workeroutput, the controller collects them as nodes shut down, and the terminal summary prints one line, so every run measures what `-n auto` costs in memory. Figures are decimal GB, formatted by engine.tools.peak_rss.
_worker_peak_rss: dict[str, int] = {}


# tryfirst makes the controller's half the outermost wrapper, outside the terminal reporter's and xdist's, so the `pyright:` line prints below the pytest summary and the wait for pyright is not counted in the summary's time. test_pyright_gate checks the flag. The worker's half writes its peak before yielding, because xdist's wrapper, nested inside this one, sends workeroutput before this one resumes.
@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> Generator[None, object, object]:
    if hasattr(session.config, "workerinput"):
        from engine.tools.peak_rss import peak_rss_self_bytes

        workeroutput = session.config.workeroutput  # pyright: ignore[reportAttributeAccessIssue]
        workeroutput["peak_rss_bytes"] = peak_rss_self_bytes()
        return (yield)
    interrupted = True
    try:
        result = yield
        interrupted = exitstatus == pytest.ExitCode.INTERRUPTED
    finally:
        returncode = _join_deferred_pyright(interrupted)
    if returncode != 0:
        pytest.exit("pyright type check failed (see output above)", returncode=pytest.ExitCode.USAGE_ERROR)
    return result


def pytest_testnodedown(node, error) -> None:
    payload = getattr(node, "workeroutput", None) or {}
    peak = payload.get("peak_rss_bytes")
    if isinstance(peak, int):
        _worker_peak_rss[str(node.gateway.id)] = peak


def pytest_terminal_summary(terminalreporter, exitstatus, config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):
        return
    from engine.tools.cycle_timings import POOL_UNIT_ENV, gateway_order, record_pool
    from engine.tools.peak_rss import format_gb, peak_rss_self_bytes

    controller_peak = peak_rss_self_bytes()
    line = f"peak RSS (GB): controller {format_gb(controller_peak)}"
    if _worker_peak_rss:
        workers = ", ".join(
            f"{ident} {format_gb(peak)}"
            for ident, peak in sorted(_worker_peak_rss.items(), key=gateway_order)
        )
        line += f"; workers {workers}"
    terminalreporter.write_line(line)

    # Records the same measurement in the cycle-timings journal so `make job-costs` can compare it with the checked-in constants (FONT_SUITE_WORKER_BYTES above, the corpus build's constants in engine/tools/artifact_cycle.py). Only a pool whose caller set POOL_UNIT_ENV is recorded, so an unlabeled `uv run pytest` writes nothing. The width comes from the resolved numprocesses, not len(_worker_peak_rss): xdist resolves "auto" through the hook above during pytest_cmdline_main, so the option holds the width the pool ran at, while the peaks dict is one short whenever a node dies without returning its workeroutput. cycle_timings is imported inside the hook because this file is loaded by every pytest run and by tools that are not pytest, while only a controller reaches this line. Its `gateway_order` also sorts the printed line above, so both list the workers in the same order. POOL_UNIT_ENV is only ever set on a child's own environment dict, never on os.environ, so a nested pytest cannot inherit a stale unit name and record its pool under another name.
    unit = os.environ.get(POOL_UNIT_ENV, "").strip()
    width = getattr(config.option, "numprocesses", None)
    if unit and _worker_peak_rss and isinstance(width, int) and width >= 1:
        record_pool(
            unit,
            width=width,
            worker_peaks=dict(_worker_peak_rss),
            controller_peak_bytes=controller_peak,
        )


def _pin_fonts() -> dict[str, "CheckFont"]:
    """Return the Senior and Junior the pin items check against, publishing the fonts first when this process has not (a run without xdist), and loading each once. The Senior's glyph-facts sidecar is read beside the build it was copied from (`suite_fonts.glyph_facts_path`)."""
    if "fonts" not in _font_cache:
        if "_published" not in _font_cache:
            _publish_fonts()
        from engine.tools import suite_fonts
        from engine.validation.check import CheckFont

        paths = suite_fonts.font_paths(ROOT)
        _font_cache["fonts"] = {
            "senior": CheckFont(paths["senior"], glyph_facts=suite_fonts.glyph_facts_path(ROOT)),
            "junior": CheckFont(paths["junior"], cursive=False),
        }
    return _font_cache["fonts"]


# The Senior and Junior under site/, for the any-of regression checks in test/test_calt_expectations.py.
@pytest.fixture(scope="session")
def pin_fonts() -> dict[str, "CheckFont"]:
    return _pin_fonts()


def pytest_collect_file(parent: pytest.Collector, file_path: Path) -> "ShapingFile | None":
    if file_path.name in PIN_PAGE_NAMES and file_path.suffix == ".html":
        return ShapingFile.from_parent(parent, path=file_path)
    return None


class ShapingFile(pytest.File):
    """A pin page: one item per `data-expect` pin, read by the corpus collector (`engine/validation/corpus.py`)."""

    def collect(self) -> Iterator["ShapingItem"]:
        from engine.validation.corpus import collect_pins

        page = self.path.relative_to(ROOT).as_posix() if self.path.is_relative_to(ROOT) else self.path.name
        seen_ids: dict[str, int] = {}
        for pin in collect_pins(page, self.path.read_text(encoding="utf-8")):
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", pin.text).strip("_")[:40]
            if not slug:
                slug = re.sub(r"[^a-zA-Z0-9]+", "_", pin.expect).strip("_")[:40]
            slug = f"{pin.line}:{slug}"
            if slug in seen_ids:
                seen_ids[slug] += 1
                slug = f"{slug}_{seen_ids[slug]}"
            else:
                seen_ids[slug] = 0
            yield ShapingItem.from_parent(self, name=slug, pin=pin)


class ShapingItem(pytest.Item):
    """One pin, checked against the fonts under site/ by the checker the build's pin gate runs (`engine/validation/check.py`, `check_pin`). A pending pin (`corpus.PENDING_ATTRIBUTE`) is an expected failure, and fails once it passes, so its mark cannot outlive the disagreement it records."""

    def __init__(self, name: str, parent: pytest.Item, pin: "Pin") -> None:
        super().__init__(name, parent)
        self.pin = pin

    def setup(self) -> None:
        _pin_fonts()

    def runtest(self) -> None:
        from engine.validation.check import check_pin
        from engine.validation.corpus import PENDING_ATTRIBUTE

        outcome = check_pin(_pin_fonts(), self.pin)
        status = outcome.status
        if status == "pass":
            return
        if status == "pending":
            pytest.xfail(f"pending ({self.pin.pending}): {outcome.message()}")
        if status == "pending-passes":
            raise AssertionError(
                f"{self.pin.source}: {self.pin.expect!r} passes on the fonts but carries {PENDING_ATTRIBUTE}={self.pin.pending!r}; remove the attribute, and any mention of the pin in WHATNEXT.md"
            )
        raise AssertionError(outcome.message())

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, self.pin.line - 1, self.name

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException], style: str | None = None) -> str:
        return str(excinfo.value)

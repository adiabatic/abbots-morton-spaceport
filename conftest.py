import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from test_shaping import Run

ROOT = Path(__file__).resolve().parent

# Put `tools/` and `test/` on the path for the xdist controller too (not just the workers, which each insert their test module's `test/` dir on import). The controller imports this root conftest but no test module, yet it must import `quikscript_join_analysis` to deserialize a `NonJoiningNeighborSelectionWarning` ferried from a worker (raised in-process by `emit_quikscript_senior_features`'s Phase-1 join-contract pass), and it imports `test_shaping` when collecting the `site/` data-expect HTML corpora. Without this, xdist's warning unserialization or the corpus collection raises ModuleNotFoundError and aborts the session.
for _p in (str(ROOT / "tools"), str(ROOT / "test")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


_shaping_cache: dict[str, Any] = {}


def _make_env() -> dict[str, str]:
    # The outer `make test-and-review` runs with `-j2` and exports a jobserver pipe via MAKEFLAGS. Python's subprocess.run defaults to close_fds=True, so the inner `make all` would inherit the auth string but not the fds and emit "jobserver unavailable: using -j1". Drop MAKEFLAGS so it just runs standalone.
    env = os.environ.copy()
    env.pop("MAKEFLAGS", None)
    env.pop("MFLAGS", None)
    return env


def _is_rebuild_only(config: pytest.Config) -> bool:
    rebuild = (ROOT / "rebuild").resolve()
    invocation_dir = Path(config.invocation_params.dir)
    targets = [(invocation_dir / arg.split("::", 1)[0]).resolve() for arg in config.args]
    return bool(targets) and all(target == rebuild or rebuild in target.parents for target in targets)


def _rebuild_suite_fonts_present() -> bool:
    from rebuild.pipeline.fingerprint import font_paths

    return all(path.is_file() for path in font_paths(ROOT))


def pytest_configure(config: pytest.Config) -> None:
    # Under xdist, the controller dispatches but doesn't run tests, so the lazy build in _ensure_shaping_cache would never fire on it. Build here before workers spawn, and mark built so each worker skips the no-op `make all` it would otherwise spawn on first shaping test.
    if hasattr(config, "workerinput"):
        _shaping_cache["_built"] = True
        return
    if config.getoption("dist", "no") == "no":
        return
    # `make test` / `make test-slowly` / `make test-rebuild` set AMS_RUN_PYRIGHT so the pyright gate overlaps the ≈18s font build instead of running back-to-back as a serial prelude; both finish before the workers spawn, so a type error still fast-fails the whole run. A run that collects only under rebuild/ skips the font build — that suite shapes against the site fonts exactly as its input-closure fingerprint already hashed them, so rebuilding at suite head would either churn mtimes the review-surface fixture cache depends on or test bytes nobody fingerprinted — while pyright still starts here and still fast-fails before the workers spawn. The one exception: when the closure's fonts (fingerprint.font_paths) are absent, as after `make clean`, the build runs anyway, since a missing input the suite cannot shape against beats every mtime concern. Direct `uv run pytest -n …` invocations leave it unset and skip pyright, so iterating on a subset isn't aborted by an unrelated type error elsewhere in the tree. The argv carries no paths: `[tool.pyright] include` in pyproject.toml is the single authority for what gets checked, which is how rebuild/ gets covered from test-rebuild without a second path list to keep in sync.
    pyright = None
    if os.environ.get("AMS_RUN_PYRIGHT") == "1":
        pyright = subprocess.Popen(["uv", "run", "pyright"], cwd=ROOT, env=_make_env())
    if not _is_rebuild_only(config) or not _rebuild_suite_fonts_present():
        subprocess.run(["make", "all"], cwd=ROOT, check=True, env=_make_env())
        _shaping_cache["_built"] = True
    if pyright is not None and pyright.wait() != 0:
        raise pytest.UsageError("pyright type check failed (see output above)")


# What one font-suite worker holds at its peak. Nothing here divides by it — the branch below takes the core count, because a worker this small cannot bind a pool before the cores do — but it is what prices `make test` as a co-resident pool when something else wants the same box, so it is named rather than left in prose. Seeded from the peak-RSS summary line below (issue #51), which has these workers at 0.11–0.28 GB apiece across runs, and rounded up past the top of that range for the same reason kernel_exec.CONFIG_PEAK_BYTES rounds up past its own measurement: a per-unit cost that errs low is what puts a box into swap, while one that errs high only narrows a pool.
FONT_SUITE_WORKER_BYTES = 300_000_000


# What `-n auto` resolves to repo-wide: the box's usable cores — the ones this process may actually run on, affinity mask and cgroup CPU quota included, which os.cpu_count() reads straight past. The font suite's workers cost FONT_SUITE_WORKER_BYTES apiece, so the cores bind that pool before memory does; and no rebuild-suite worker reads a live build artifact (rebuild/conftest.py's audit guard is what holds that), so a bare `uv run pytest rebuild/`, a single rebuild test file, a mixed `pytest rebuild/ test/` and a `pytest .` all take the same answer, and there is no heavier worker to price a run at. memory_budget is imported inside the hook rather than at module scope because this file is loaded by every pytest run in the repo while this hook is called only by the ones that actually spell `-n auto`, so a run that states its own width never pays for the import at all — the same function-scope shape peak_rss and fingerprint are reached in here. Answering this firstresult hook shadows xdist's own, which is where PYTEST_XDIST_AUTO_NUM_WORKERS is normally read, so the variable is read here too and keeps overriding whichever default applies — including rebuild/conftest.py's, which returns None when it is set precisely so this line gets it — a run at a time.
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int:
    override = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if override:
        return max(1, int(override))
    from rebuild.tools.memory_budget import usable_cores

    return usable_cores()


# Peak RSS per xdist worker (issue #51): each worker reports its own high-water mark at session finish through workeroutput, the controller collects them as nodes shut down, and the terminal summary prints one line — so what `-n auto` actually costs in RAM is measured on every run instead of folklore. Figures are decimal GB via rebuild.tools.peak_rss, the repo-wide yardstick.
_worker_peak_rss: dict[str, int] = {}


def pytest_sessionfinish(session: pytest.Session) -> None:
    if hasattr(session.config, "workerinput"):
        from rebuild.tools.peak_rss import peak_rss_self_bytes

        workeroutput = session.config.workeroutput  # pyright: ignore[reportAttributeAccessIssue]
        workeroutput["peak_rss_bytes"] = peak_rss_self_bytes()


def pytest_testnodedown(node, error) -> None:
    payload = getattr(node, "workeroutput", None) or {}
    peak = payload.get("peak_rss_bytes")
    if isinstance(peak, int):
        _worker_peak_rss[str(node.gateway.id)] = peak


def pytest_terminal_summary(terminalreporter, exitstatus, config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):
        return
    from rebuild.tools.cycle_timings import POOL_UNIT_ENV, gateway_order, record_pool
    from rebuild.tools.peak_rss import format_gb, peak_rss_self_bytes

    controller_peak = peak_rss_self_bytes()
    line = f"peak RSS (GB): controller {format_gb(controller_peak)}"
    if _worker_peak_rss:
        workers = ", ".join(
            f"{ident} {format_gb(peak)}"
            for ident, peak in sorted(_worker_peak_rss.items(), key=gateway_order)
        )
        line += f"; workers {workers}"
    terminalreporter.write_line(line)

    # The line above is for whoever is watching this one run finish; the record below keeps the same measurement so it can be read against a checked-in constant later. Several widths in this tree are the box divided by a per-worker peak that was measured once and then written down — FONT_SUITE_WORKER_BYTES above, the surface build's constants in rebuild/tools/artifact_cycle.py — and until this record existed a memory-saver that moved one of those peaks left the constant quietly stale, with a box in swap as the first symptom rather than anything red. A pool only names itself when a caller told it what it is a pool of, so an unlabeled `uv run pytest` writes nothing. The width comes from the resolved numprocesses rather than from len(_worker_peak_rss): xdist resolves "auto" through the hook above during pytest_cmdline_main, long before this point, so the option holds the width the pool actually ran at, while the peaks dict is only the reporting width and is short by one whenever a node dies without handing back its workeroutput. cycle_timings is imported inside the hook rather than at module scope for the reason the hook above reaches memory_budget that way: this file is loaded by every pytest run in the repo and by things that are not pytest at all, while only a controller ever reaches this line, every worker having returned at the top of the hook. The same import supplies the sort the printed line above uses, so the workers a human just read and the workers the record keeps are ordered by one key rather than by two copies of it that can drift. The variable is only ever read here and is only ever set on a child's own environment dict, never on os.environ, so a nested pytest cannot inherit a stale unit name and file its pool under somebody else's.
    unit = os.environ.get(POOL_UNIT_ENV, "").strip()
    width = getattr(config.option, "numprocesses", None)
    if unit and _worker_peak_rss and isinstance(width, int) and width >= 1:
        record_pool(
            unit,
            width=width,
            worker_peaks=dict(_worker_peak_rss),
            controller_peak_bytes=controller_peak,
        )


def _ensure_shaping_cache() -> dict[str, Any]:
    if "fonts" not in _shaping_cache:
        if "_built" not in _shaping_cache:
            subprocess.run(["make", "all"], cwd=ROOT, check=True, env=_make_env())
            _shaping_cache["_built"] = True
        from test_shaping import load_font, build_anchor_map

        fonts = {}
        anchor_maps = {}
        potentials = {}
        for variant in ("senior", "junior"):
            fonts[variant] = load_font(variant)
            anchors, potential = build_anchor_map(variant)
            anchor_maps[variant] = anchors
            potentials[variant] = potential
        _shaping_cache["fonts"] = fonts
        _shaping_cache["anchor_maps"] = anchor_maps
        _shaping_cache["potentials"] = potentials
    return _shaping_cache


@pytest.fixture(scope="session")
def shaping_env() -> dict[str, Any]:
    return _ensure_shaping_cache()


def pytest_collect_file(parent: pytest.Collector, file_path: Path) -> "ShapingFile | None":
    if (
        file_path.name in ("index.html", "the-manual.html", "extra-senior-words.html")
        and file_path.suffix == ".html"
    ):
        return ShapingFile.from_parent(parent, path=file_path)
    return None


class ShapingFile(pytest.File):
    def collect(self) -> Iterator["ShapingItem"]:
        from test_shaping import _DataExpectCollector

        raw = self.path.read_text(encoding="utf-8")
        collector = _DataExpectCollector()
        collector.feed(raw)

        seen_ids: dict[str, int] = {}
        for text, expect, line, stylistic_set, runs in collector.cells:
            if not expect or not expect.strip():
                continue
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:40]
            if not slug:
                slug = re.sub(r"[^a-zA-Z0-9]+", "_", expect).strip("_")[:40]
            slug = f"{line}:{slug}"
            if slug in seen_ids:
                seen_ids[slug] += 1
                slug = f"{slug}_{seen_ids[slug]}"
            else:
                seen_ids[slug] = 0
            yield ShapingItem.from_parent(
                self,
                name=slug,
                text=text,
                expect_str=expect,
                html_line=line,
                stylistic_set=stylistic_set,
                runs=runs,
            )


class ShapingItem(pytest.Item):
    def __init__(
        self,
        name: str,
        parent: pytest.Item,
        text: str,
        expect_str: str,
        html_line: int,
        stylistic_set: str | None = None,
        runs: list[Run] | None = None,
    ) -> None:
        super().__init__(name, parent)
        self.text = text
        self.expect_str = expect_str
        self.html_line = html_line
        self.stylistic_set = stylistic_set
        self.runs = runs or [{"font": "senior", "text": text}]

    def setup(self) -> None:
        _ensure_shaping_cache()

    def runtest(self) -> None:
        from test_shaping import run_shaping_test_runs

        features = None
        if self.stylistic_set:
            features = {f"ss{ss.zfill(2)}": True for ss in self.stylistic_set.split()}

        run_shaping_test_runs(
            _shaping_cache["fonts"],
            _shaping_cache["anchor_maps"],
            self.runs,
            self.expect_str,
            base_potential_entries=_shaping_cache["potentials"],
            features=features,
        )

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, self.html_line - 1, self.name

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException], style: str | None = None) -> str:
        return str(excinfo.value)

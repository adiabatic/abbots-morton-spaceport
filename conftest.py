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


def _is_font_suite_only(config: pytest.Config) -> bool:
    """Whether every collected target lies under test/ or site/ — the font suite and its data-expect corpora, and nothing else."""
    invocation_dir = Path(config.invocation_params.dir)
    roots = [(ROOT / name).resolve() for name in ("test", "site")]
    targets = [(invocation_dir / arg.split("::", 1)[0]).resolve() for arg in config.args]
    return bool(targets) and all(
        any(target == root or root in target.parents for root in roots) for target in targets
    )


# What `-n auto` resolves to repo-wide, and the one place the answer is not simply the core count. Two is sized for the most RAM-constrained box that runs this repo (32 GB, where a full-width pool of the *rebuild* suite's live fixtures drove it deep into swap), and the per-worker peak-RSS summary below (issue #51) is the standing measurement that justifies it — every run reports what its workers actually cost. That measurement is also why the font suite is exempt: `make test`'s workers peak at 0.11–0.28 GB apiece, so the RAM argument never applied to them, and the whole of its 1,090 s at width 2 was the width. When every collected target is under test/ or site/ this answers the box's core count instead, which is the same rule rebuild/conftest.py applies to its own lanes. Answering this firstresult hook shadows xdist's own, which is where PYTEST_XDIST_AUTO_NUM_WORKERS is normally read, so the variable is read here too and keeps overriding whichever default applies, a run at a time.
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int:
    override = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if override:
        return max(1, int(override))
    return (os.cpu_count() or 2) if _is_font_suite_only(config) else 2


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
    from rebuild.tools.peak_rss import format_gb, peak_rss_self_bytes

    def _worker_order(item: tuple[str, int]) -> tuple[int, str]:
        digits = "".join(ch for ch in item[0] if ch.isdigit())
        return (int(digits) if digits else -1, item[0])

    line = f"peak RSS (GB): controller {format_gb(peak_rss_self_bytes())}"
    if _worker_peak_rss:
        workers = ", ".join(
            f"{ident} {format_gb(peak)}"
            for ident, peak in sorted(_worker_peak_rss.items(), key=_worker_order)
        )
        line += f"; workers {workers}"
    terminalreporter.write_line(line)


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

import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from test_shaping import Run

ROOT = Path(__file__).resolve().parent.parent


_shaping_cache: dict[str, Any] = {}


def _ensure_shaping_cache() -> dict[str, Any]:
    if "fonts" not in _shaping_cache:
        subprocess.run(["make", "all"], cwd=ROOT, check=True)
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


def pytest_collect_file(parent: pytest.Collector, file_path: Path) -> "ShapingFile | None":
    if file_path.name in ("index.html", "the-manual.html", "extra-senior-words.html") and file_path.suffix == ".html":
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
                self, name=slug, text=text, expect_str=expect,
                html_line=line, stylistic_set=stylistic_set,
                runs=runs,
            )


class ShapingItem(pytest.Item):
    def __init__(self, name: str, parent: pytest.Item, text: str,
                 expect_str: str, html_line: int,
                 stylistic_set: str | None = None,
                 runs: list[Run] | None = None) -> None:
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
            features = {f"ss{ss.zfill(2)}": True
                        for ss in self.stylistic_set.split()}

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

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException],
                     style: str | None = None) -> str:
        return str(excinfo.value)

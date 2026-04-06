import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

collect_ignore = ["test_shaping.py"]


def pytest_collect_file(parent, file_path):
    if file_path.name in ("index.html", "the-manual.html", "extra-senior-words.html", "ensure-sanity.html") and file_path.suffix == ".html":
        return ShapingFile.from_parent(parent, path=file_path)


class ShapingFile(pytest.File):
    def collect(self):
        from test_shaping import _DataExpectCollector

        import re

        raw = self.path.read_text(encoding="utf-8")
        collector = _DataExpectCollector()
        collector.feed(raw)

        seen_ids = {}
        for text, expect, line, stylistic_set, runs in collector.cells:
            if not expect.strip():
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
    def __init__(self, name, parent, text, expect_str, html_line,
                 stylistic_set=None, runs=None):
        super().__init__(name, parent)
        self.text = text
        self.expect_str = expect_str
        self.html_line = html_line
        self.stylistic_set = stylistic_set
        self.runs = runs or [{"font": "senior", "text": text}]

    def setup(self):
        if not hasattr(self.session, "_shaping_fonts"):
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
            self.session._shaping_fonts = fonts
            self.session._shaping_anchor_maps = anchor_maps
            self.session._shaping_potentials = potentials

    def runtest(self):
        from test_shaping import run_shaping_test_runs

        features = None
        if self.stylistic_set:
            features = {f"ss{ss.zfill(2)}": True
                        for ss in self.stylistic_set.split()}

        run_shaping_test_runs(
            self.session._shaping_fonts,
            self.session._shaping_anchor_maps,
            self.runs,
            self.expect_str,
            base_potential_entries=self.session._shaping_potentials,
            features=features,
        )

    def reportinfo(self):
        return self.path, self.html_line - 1, self.name

    def repr_failure(self, excinfo):
        return str(excinfo.value)

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

collect_ignore = ["test_shaping.py"]


def pytest_collect_file(parent, file_path):
    if file_path.name in ("index.html", "the-manual.html", "extra-senior-words.html") and file_path.suffix == ".html":
        return ShapingFile.from_parent(parent, path=file_path)


class ShapingFile(pytest.File):
    def collect(self):
        from test_shaping import _DataExpectCollector

        import re

        raw = self.path.read_text(encoding="utf-8")
        collector = _DataExpectCollector()
        collector.feed(raw)

        seen_ids = {}
        for text, expect, line, stylistic_set in collector.cells:
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
            )


class ShapingItem(pytest.Item):
    def __init__(self, name, parent, text, expect_str, html_line,
                 stylistic_set=None):
        super().__init__(name, parent)
        self.text = text
        self.expect_str = expect_str
        self.html_line = html_line
        self.stylistic_set = stylistic_set

    def setup(self):
        if not hasattr(self.session, "_shaping_font"):
            subprocess.run(["make", "all"], cwd=ROOT, check=True)
            from test_shaping import load_font, build_anchor_map

            self.session._shaping_font = load_font()
            anchors, potential = build_anchor_map()
            self.session._shaping_anchors = anchors
            self.session._shaping_potential_entries = potential

    def runtest(self):
        from test_shaping import run_shaping_test

        features = None
        if self.stylistic_set:
            features = {f"ss{ss.zfill(2)}": True
                        for ss in self.stylistic_set.split()}

        run_shaping_test(
            self.session._shaping_font,
            self.session._shaping_anchors,
            self.text,
            self.expect_str,
            base_potential_entries=self.session._shaping_potential_entries,
            features=features,
        )

    def reportinfo(self):
        return self.path, self.html_line - 1, self.name

    def repr_failure(self, excinfo):
        return str(excinfo.value)

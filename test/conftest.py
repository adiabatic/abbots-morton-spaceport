import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "test" / "index.html"


@pytest.fixture(scope="session", autouse=True)
def build_font():
    subprocess.run(["make", "all"], cwd=ROOT, check=True)


def pytest_collection_modifyitems(items):
    from test_shaping import CASE_LINES

    for item in items:
        if not hasattr(item, "callspec"):
            continue
        test_id = item.callspec.params.get("test_id")
        if test_id and test_id in CASE_LINES:
            line = CASE_LINES[test_id]
            item._location = (str(HTML_PATH), line - 1, item.name)

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def build_font():
    subprocess.run(["make", "all"], cwd=ROOT, check=True)

"""Two tests that share one session-scoped fixture, for `rebuild/test_contracts_closure.py` to run in a child pytest from the repository root, where pytest loads `rebuild/conftest.py` as the conftest of a directory below the rootdir. The file name matches no `python_files` pattern, so the real suite never collects it; the child names it on its command line.

The fixture's one setup reads a checked-in file, starts a child named as the kernel binary is, and starts an ordinary child, so each requester's recorded closure shows whether that setup was credited to it.
"""

import subprocess
from pathlib import Path

import pytest

from rebuild.tools.closure_record import KERNEL_BINARY

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def shared_setup(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    kernel = tmp_path_factory.mktemp("kernel") / KERNEL_BINARY
    kernel.write_text("#!/bin/sh\nexit 0\n")
    kernel.chmod(0o755)
    subprocess.run([str(kernel)], check=True)
    subprocess.run(["true"], check=True)
    return (REPO_ROOT / "rebuild" / "review" / "fixtures" / "manifest.json").read_bytes()


def test_first_requester(shared_setup: bytes) -> None:
    assert shared_setup


def test_second_requester(shared_setup: bytes) -> None:
    assert shared_setup

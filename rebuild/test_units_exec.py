"""Tests for the review crate under `rebuild/review-rs/`, the Rust engine of the review corpus build's unit recompute (`rebuild/CORPUS-UNITS-PORT-PLAN.md`).

The crate is a sibling of the kernel crate, not a member of a Cargo workspace, so it has its own lock, and in its build that lock, not the kernel's, picks the versions of the kernel's dependencies. The lock-parity test keeps the kernel library the review engine links the same code the table build runs.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_LOCK = REPO_ROOT / "rebuild" / "kernel-rs" / "Cargo.lock"
REVIEW_LOCK = REPO_ROOT / "rebuild" / "review-rs" / "Cargo.lock"


def _locked_versions(lock: Path) -> dict[str, set[str]]:
    with lock.open("rb") as handle:
        packages = tomllib.load(handle)["package"]
    versions: dict[str, set[str]] = {}
    for package in packages:
        versions.setdefault(package["name"], set()).add(package["version"])
    return versions


def test_every_package_both_locks_name_has_one_version():
    """A package the kernel's lock and the review crate's lock both name is locked at one version across the two, so a review-side dependency edit that moves a kernel dependency fails here instead of building the kernel library against code the table build never ran."""
    kernel = _locked_versions(KERNEL_LOCK)
    review = _locked_versions(REVIEW_LOCK)
    assert "ams-m1-kernel" in review, f"{REVIEW_LOCK.relative_to(REPO_ROOT)} does not lock the kernel crate"
    shared = sorted(kernel.keys() & review.keys())
    split = {
        name: sorted(kernel[name] | review[name]) for name in shared if len(kernel[name] | review[name]) != 1
    }
    assert split == {}, f"the two crates' locks disagree on these packages' versions: {split}"

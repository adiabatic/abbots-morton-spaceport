"""Determinism check (rebuild/BASELINE-PLAN.md §2 and §8).

Usage:
    uv run python rebuild/check_determinism.py [--config default] [--lengths 1,2]
    uv run python rebuild/check_determinism.py --command 'uv run python -m rebuild.baseline.cli extract --config default --out {out} --workers 10' --artifact baseline-default.tsv.gz

Default mode runs this script's own sample extraction (the full length-1 and length-2 basis through the shared shaping, classification, and row-serialization path) twice in fresh subprocesses with different PYTHONHASHSEED values and asserts byte-identical stdout — the §8 diff-stability property on the validation suite's own mechanics. The --command mode runs an arbitrary extractor command twice into fresh output directories ({out} is substituted) and compares the named artifact's bytes (uncompressed when it ends in .gz), so the same driver can gate the real extractor once it lands.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validation.classify import SeamClassifier
from validation.rowmodel import ALPHABET, CONFIGS
from validation.shaping import SENIOR_FONT, Shaper, row_for

REPO_ROOT = Path(__file__).resolve().parent.parent


def emit_sample(config_token: str, lengths: list[int], font: Path, out) -> None:
    features = CONFIGS[config_token]
    shaper = Shaper(font)
    classifier = SeamClassifier(font)
    out.write(f"# determinism-sample config: {config_token}\n")
    out.write(f"# lengths: {','.join(str(n) for n in lengths)}\n")
    for length in sorted(lengths):
        for codepoints in itertools.product(ALPHABET, repeat=length):
            text = "".join(chr(cp) for cp in codepoints)
            out.write(row_for(shaper, classifier, text, features or None).to_tsv() + "\n")


def _run_self_sample(config_token: str, lengths: str, font: Path, hash_seed: str) -> bytes:
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    result = subprocess.run(
        [
            sys.executable,
            __file__,
            "--emit",
            "--config",
            config_token,
            "--lengths",
            lengths,
            "--font",
            str(font),
        ],
        capture_output=True,
        env=env,
        cwd=REPO_ROOT,
        check=True,
    )
    return result.stdout


def _artifact_bytes(out_dir: Path, artifact: str) -> bytes:
    path = out_dir / artifact
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as fh:
            return fh.read()
    return path.read_bytes()


def _run_command(command: str, artifact: str) -> bytes:
    tmp_root = REPO_ROOT / "tmp"
    tmp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_root, prefix="determinism-") as out_dir:
        subprocess.run(command.format(out=out_dir), shell=True, cwd=REPO_ROOT, check=True)
        return _artifact_bytes(Path(out_dir), artifact)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="default", choices=sorted(CONFIGS))
    parser.add_argument("--lengths", default="1,2")
    parser.add_argument("--font", type=Path, default=SENIOR_FONT)
    parser.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--command", default=None)
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args(argv)

    if args.emit:
        emit_sample(args.config, [int(n) for n in args.lengths.split(",")], args.font, sys.stdout)
        return 0

    if args.command:
        if not args.artifact:
            parser.error("--command requires --artifact")
        first = _run_command(args.command, args.artifact)
        second = _run_command(args.command, args.artifact)
    else:
        first = _run_self_sample(args.config, args.lengths, args.font, "0")
        second = _run_self_sample(args.config, args.lengths, args.font, "1")

    digest_first = hashlib.sha256(first).hexdigest()
    digest_second = hashlib.sha256(second).hexdigest()
    print(f"run 1: {len(first)} bytes sha256 {digest_first}")
    print(f"run 2: {len(second)} bytes sha256 {digest_second}")
    if first != second:
        print("NOT deterministic: outputs differ")
        return 1
    print("deterministic: byte-identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

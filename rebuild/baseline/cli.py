"""Argparse front end for the baseline extraction: `extract` writes the per-configuration baseline tables, `summarize` digests them. The boundary-equivalence checks live in rebuild/validation/equivalence.py and the validation suite drives them directly."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import extract
from .extract import SHARD_WORKERS_DEFAULT
from .model import CONFIGS, DEFAULT_OUT_DIR


def _add_out(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)


def _config_tokens(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(CONFIGS)
    if args.config is None:
        sys.exit("pass --config <token> or --all")
    return [args.config]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="rebuild.baseline.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="extract baseline tables")
    extract_parser.add_argument("--config", choices=list(CONFIGS))
    extract_parser.add_argument("--all", action="store_true")
    _add_out(extract_parser)
    extract_parser.add_argument(
        "--workers",
        type=int,
        default=SHARD_WORKERS_DEFAULT,
        help="how many shard workers to fan out to; the default is the cores this process may actually run on (%(default)s here), and the extracted bytes are identical at every width",
    )
    subset_group = extract_parser.add_mutually_exclusive_group()
    subset_group.add_argument(
        "--limit", type=int, help="smoke run: only the first N basis strings in canonical order"
    )
    subset_group.add_argument(
        "--sample",
        type=int,
        help="smoke run: a deterministic hash-keyed sample of about N basis strings, identical across runs and configurations",
    )

    summarize_parser = subparsers.add_parser("summarize", help="write digests.tsv and SUMMARY.md")
    _add_out(summarize_parser)

    args = parser.parse_args(argv)

    if args.command == "extract":
        for token in _config_tokens(args):
            digest = extract.extract_config(
                token, args.out, args.workers, limit=args.limit, sample=args.sample
            )
            print(f"{token}: {digest.rows} rows, sha256 {digest.sha256_uncompressed}")
    elif args.command == "summarize":
        path = extract.write_summary(args.out)
        print(f"wrote {path} and {args.out / 'digests.tsv'}")


if __name__ == "__main__":
    main()

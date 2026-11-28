"""The accepted build that the review corpus's table-diff mode compares an edit against (doc/review-design.md §2.3). `take` copies one build's tables, rule certificates, Regular font and kern table out of `engine/out/build` into a directory named for its content under `var/keep/review-snapshot/`, and points the `current` file beside it at that directory. `load` reads a snapshot back and refuses one in another format, or with a file that is not the one its `snapshot.json` records.

A snapshot directory holds, for each configuration it covers (the settled configurations, `tablediff.SETTLED_CONFIGS`, for every snapshot `take` writes):

- `settlement-<config>.tsv.gz` and `joins-<config>.tsv.gz`, the build's two tables, gzipped with no name or time in the gzip header, so one table always gives the same bytes from one build of zlib, and other bytes from another, such as zlib-ng;
- `certificates-<config>.ndjson.gz`, one line per settlement rule in rule order, `{"rule": i, "text": [code points], "under": null}`: the text of the certificate the crate wrote beside the rule, read from the configuration's windows head through `table.read_windows_head`, the one reader of a table's head, and spelled out over the spec the build was loaded from (`certificate_text`). A guard rule's certificate settles under another configuration (`GUARD_MARKER`), which `under` names.

and once: `Senior-Regular.otf`, the Regular font the tables shipped in, `kern.tsv.gz`, the build's kern table, and `snapshot.json`. That record holds the format, the digest, when the snapshot was taken (`taken_at`), the tables' stamp (the `inputs` stamp every configuration's windows head records), the configurations, the directory it was taken from, and the sha256 and length of each file's content (`_content`): a gzipped file's unpacked bytes, and any other file's own. The packed bytes are recorded nowhere, because the zlib that packs them decides them.

The digest is the sha256 of the format, the tables' stamp, the configurations and each file's content sha256, written as canonical JSON (`_digest`). It leaves out when the snapshot was taken and where from, so the same build taken again, in this checkout, in a tree exported from a commit, or on a machine whose Python links another zlib, gives the same digest, and its first `DIGEST_NAME_LENGTH` hex digits name the directory. `take` keeps the snapshot it writes and the one `current` named before it, and deletes any older snapshot directory under the home; `install` does the same for a snapshot taken elsewhere, which the remake (`engine/review/accept.py`) takes in an exported tree.

The tracked pointer, `engine/review-snapshot.json` (`POINTER_PATH`), names the accepted snapshot: `{format, snapshot_format, digest, taken_at}` (`pointer_record`). It records no commit. The accept (`engine/review/accept.py`) writes it before the author commits, and the pointer's change goes into the commit with the edit it accepts, so the commit a snapshot stands for is the last commit that changed the pointer (`pointer_commit`), and a build of that commit remakes the snapshot.

`certificate_text` restates `witness._token_text`, because importing the witness stage would bring the conformance sweep into the corpus build's import closure; `engine/test_review_snapshot.py` holds the two to the same text over every certificate of a built table. The reader accepts only `SNAPSHOT_FORMAT`.

Usage:
    uv run python -m engine.review.snapshot take [--tables engine/out/build] [--home var/keep/review-snapshot]
    uv run python -m engine.review.snapshot show [--home var/keep/review-snapshot]
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
import zlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from engine.pipeline import table
from engine.pipeline.model import ResolvedSpec
from engine.review import tablediff
from engine.review.enrich import load_spec

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILD_DIR = REPO_ROOT / "engine" / "out" / "build"
DEFAULT_HOME = REPO_ROOT / "var" / "keep" / "review-snapshot"
POINTER_RELATIVE = "engine/review-snapshot.json"
POINTER_PATH = REPO_ROOT / POINTER_RELATIVE

SNAPSHOT_FORMAT = "ams-review-snapshot/5"
POINTER_FORMAT = "ams-review-snapshot-pointer/1"
RECORD_NAME = "snapshot.json"
# A one-line file beside the snapshot directories naming the current one; a plain file, written by rename, so a reader never sees half a name and the home copies or archives as ordinary files.
CURRENT_NAME = "current"
FONT_NAME = "Senior-Regular.otf"
KERN_SOURCE = "kern.tsv"
KERN_NAME = "kern.tsv.gz"
# The first token of a guard rule's certificate (`witness.GUARD_MARKER`, the crate's `certificate::GUARD_MARKER`); the second names the configuration the rest settles under.
GUARD_MARKER = "#guard"
DIGEST_NAME_LENGTH = 12
_SNAPSHOT_DIR = re.compile(rf"[0-9a-f]{{{DIGEST_NAME_LENGTH}}}")
_COPY_CHUNK = 1 << 20


class SnapshotError(ValueError):
    """A snapshot that cannot be taken from a build directory, or read back from its own."""


def settlement_name(config: str) -> str:
    return f"settlement-{config}.tsv.gz"


def joins_name(config: str) -> str:
    return f"joins-{config}.tsv.gz"


def certificates_name(config: str) -> str:
    return f"certificates-{config}.ndjson.gz"


@dataclass(frozen=True)
class Certificate:
    """One settlement rule's certificate: its rule's index, the text it spells (None for a rule whose certificate is empty), and the configuration a guard certificate settles under."""

    rule: int
    text: tuple[int, ...] | None
    under: str | None


def certificate_text(spec: ResolvedSpec, tokens: Iterable[str]) -> tuple[int, ...]:
    """Return the code points a certificate's token stream spells, as `witness._token_text` spells it: a boundary label is its boundary token's code point, and a rune or ligature rune is its letters' code points, a ligature expanded one level to its components. Raises `ValueError` for a component with no code point and `KeyError` for a token the spec does not know."""
    boundary = {
        table.BOUNDARY_LEFT_LABELS[name]: token.codepoint
        for name, token in spec.registry.boundary_tokens.items()
    }
    values: list[int] = []
    for token in tokens:
        if token in boundary:
            values.append(boundary[token])
            continue
        rune = spec.runes[token]
        for part in rune.sequence or (token,):
            codepoint = spec.runes[part].codepoint
            if codepoint is None:
                raise ValueError(f"ligature rune {token} names {part}, which carries no code point")
            values.append(codepoint)
    return tuple(values)


def certificates_of(spec: ResolvedSpec, decision: table.DecisionTable) -> list[Certificate]:
    """Return one `Certificate` for each rule of `decision`, in rule order, from the token streams its head carries. A certificate that does not spell out raises `SnapshotError`, since the witness stage of the build that wrote it settles every certificate and fails on one that does not tokenize."""
    certificates: list[Certificate] = []
    for index, tokens in enumerate(decision.certificates):
        under: str | None = None
        if tokens and tokens[0] == GUARD_MARKER:
            under = tokens[1] if len(tokens) > 1 else None
            tokens = tokens[2:]
        try:
            text = certificate_text(spec, tokens) if tokens else None
        except (KeyError, ValueError) as error:
            raise SnapshotError(
                f"{decision.config}: rule {index}'s certificate {list(tokens)} does not spell out ({error})"
            ) from None
        certificates.append(Certificate(index, text, under))
    return certificates


def _gzip_copy(source: Path, target: Path) -> None:
    """Gzip `source` into `target` with no file name and a zero time in the header, so the same bytes always give the same file."""
    with open(source, "rb") as reader, open(target, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as packed:
            shutil.copyfileobj(reader, packed, _COPY_CHUNK)


def _write_certificates(target: Path, certificates: Sequence[Certificate]) -> None:
    with open(target, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as packed:
            for certificate in certificates:
                record = {
                    "rule": certificate.rule,
                    "text": None if certificate.text is None else list(certificate.text),
                    "under": certificate.under,
                }
                packed.write((json.dumps(record, separators=(",", ":")) + "\n").encode())


def _rule_inputs(path: Path) -> list[str]:
    """The input label of each rule of a settlement table, in rule order, opened as `tablediff.load_settlement` opens it."""
    inputs: list[str] = []
    with tablediff.open_table(path) as handle:
        for line in handle:
            if line.startswith("#") or line.startswith("input\t") or not line.strip():
                continue
            inputs.append(line.split("\t", 1)[0])
    return inputs


def _content(path: Path) -> tuple[str, int]:
    """The sha256 and the length of a snapshot file's content: its unpacked bytes for a gzipped file, one whose name ends in `.gz`, and its own bytes for any other. A snapshot's record and digest rest on these, so they are the same whichever zlib packed the file. Raises what the read raises for a gzipped file that does not unpack."""
    digest = hashlib.sha256()
    length = 0
    with gzip.open(path, "rb") if path.name.endswith(".gz") else open(path, "rb") as reader:
        while chunk := reader.read(_COPY_CHUNK):
            digest.update(chunk)
            length += len(chunk)
    return digest.hexdigest(), length


def _digest(record: dict) -> str:
    """The snapshot's digest: the sha256 of its format, tables' stamp, configurations and each file's content sha256 (`_content`) as canonical JSON. When it was taken and from where are left out, and so is how each file was packed, so one build gives one digest wherever it is taken."""
    canonical = {
        "format": record.get("format"),
        "tables_stamp": record.get("tables_stamp"),
        "configs": record.get("configs"),
        "files": {name: entry.get("sha256") for name, entry in sorted((record.get("files") or {}).items())},
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def utc_now() -> str:
    return (
        datetime.datetime.now(tz=datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class Snapshot:
    """One snapshot directory that `load` has checked: every file the record names is there with the content it records, and the record's digest is its own."""

    directory: Path
    record: dict

    @property
    def digest(self) -> str:
        return str(self.record["digest"])

    @property
    def taken_at(self) -> str | None:
        return self.record.get("taken_at")

    @property
    def tables_stamp(self) -> str | None:
        return self.record.get("tables_stamp")

    @property
    def configs(self) -> tuple[str, ...]:
        return tuple(self.record.get("configs") or ())

    @property
    def font(self) -> Path:
        return self.directory / FONT_NAME

    @property
    def kern(self) -> Path:
        return self.directory / KERN_NAME

    def settlement_path(self, config: str) -> Path:
        return self.directory / settlement_name(config)

    def joins_path(self, config: str) -> Path:
        return self.directory / joins_name(config)

    def settlement(self, config: str) -> dict[tablediff.SettlementKey, tablediff.SettlementValue]:
        """The accepted settlement rows of `config`, in rule order, or none for a configuration the snapshot does not cover."""
        if config not in self.configs:
            return {}
        return tablediff.load_settlement(self.settlement_path(config), config)

    def joins(self, config: str) -> dict[tablediff.JoinKey, tablediff.JoinValue]:
        if config not in self.configs:
            return {}
        return tablediff.load_joins(self.joins_path(config), config)

    def outcomes(self, config: str) -> list[str]:
        """The cell each accepted settlement rule of `config` settles its input to, in rule order, the order `certificates` follows."""
        if config not in self.configs:
            return []
        return tablediff.load_outcomes(self.settlement_path(config))

    def certificates(self, config: str) -> list[Certificate]:
        """The accepted certificates of `config`, one per settlement rule in rule order."""
        if config not in self.configs:
            return []
        certificates: list[Certificate] = []
        with gzip.open(self.directory / certificates_name(config), "rt", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                text = record.get("text")
                certificates.append(
                    Certificate(
                        int(record["rule"]), None if text is None else tuple(text), record.get("under")
                    )
                )
        return certificates

    def baseline(self, commit: str | None = None) -> dict:
        """The manifest's `baseline` record for a corpus built against this snapshot: `commit` is the commit it stands for (`pointer_commit`), None while the accept that took it is not committed."""
        return {"commit": commit, "taken_at": self.taken_at, "digest": self.digest, "present": True}


ABSENT_BASELINE = {"commit": None, "taken_at": None, "digest": None, "present": False}


def load(directory: Path) -> Snapshot:
    """Read the snapshot in `directory` and check it: the record is in `SNAPSHOT_FORMAT`, every file it names is there and its content has the sha256 and length recorded (`_content`), every configuration it lists has its three files, the font and the kern table are there, and the digest is the record's own. Raises `SnapshotError` naming what is wrong."""
    directory = Path(directory)
    try:
        record = json.loads((directory / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SnapshotError(f"{directory / RECORD_NAME} does not read: {error}") from None
    if not isinstance(record, dict) or record.get("format") != SNAPSHOT_FORMAT:
        found = record.get("format") if isinstance(record, dict) else None
        raise SnapshotError(
            f"{directory} is a {found!r} snapshot, and this reader reads only {SNAPSHOT_FORMAT}; take a new one with "
            "`uv run python -m engine.review.snapshot take`"
        )
    files = record.get("files")
    if not isinstance(files, dict):
        raise SnapshotError(f"{directory / RECORD_NAME} names no files")
    required = {FONT_NAME, KERN_NAME} | {
        name(config)
        for config in record.get("configs") or ()
        for name in (settlement_name, joins_name, certificates_name)
    }
    if required - set(files):
        raise SnapshotError(
            f"{directory / RECORD_NAME} names none of {', '.join(sorted(required - set(files)))}"
        )
    for name, entry in sorted(files.items()):
        path = directory / name
        if not path.is_file():
            raise SnapshotError(f"{path} is missing")
        try:
            content = _content(path)
        except OSError, EOFError, zlib.error:
            content = None
        if content != (entry.get("sha256"), entry.get("bytes")):
            raise SnapshotError(f"{path} is not the file {RECORD_NAME} records")
    if record.get("digest") != _digest(record):
        raise SnapshotError(f"{directory / RECORD_NAME}'s digest is not the digest of what it records")
    return Snapshot(directory, record)


def current(home: Path = DEFAULT_HOME) -> Path | None:
    """The directory the `current` file under `home` names, or None when there is no such file or it names no snapshot directory there."""
    home = Path(home)
    try:
        name = (home / CURRENT_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not _SNAPSHOT_DIR.fullmatch(name) or not (home / name / RECORD_NAME).is_file():
        return None
    return home / name


def _point(home: Path, name: str) -> None:
    staging = home / f".{CURRENT_NAME}.partial"
    staging.write_text(name + "\n", encoding="utf-8")
    os.replace(staging, home / CURRENT_NAME)


def _prune(home: Path, keep: Iterable[str]) -> list[str]:
    """Delete every snapshot directory under `home` but those in `keep`, and return their names. Only directories named as `take` names them are touched."""
    kept = set(keep)
    removed: list[str] = []
    for child in sorted(home.iterdir()):
        if child.is_dir() and _SNAPSHOT_DIR.fullmatch(child.name) and child.name not in kept:
            shutil.rmtree(child)
            removed.append(child.name)
    return removed


def _load_spec(repo_root: Path) -> ResolvedSpec:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_spec(repo_root)


def take(
    tables_dir: Path = BUILD_DIR,
    home: Path = DEFAULT_HOME,
    *,
    spec: ResolvedSpec | None = None,
    repo_root: Path = REPO_ROOT,
    configs: Sequence[str] = tablediff.SETTLED_CONFIGS,
) -> Snapshot:
    """Take a snapshot of the build in `tables_dir` into `home` and make it the current one. Every configuration in `configs` must have its settlement and join tables and a windows head that reads (`table.read_windows_head`), every head must carry the same tables' stamp, and each head's rules must be the settlement table's rules, input for input; `Senior-Regular.otf` and `kern.tsv` must be there. A refusal raises `SnapshotError` and leaves `home` as it was. `spec` spells the certificates out and defaults to the checkout's spec, which must be the one the build was loaded from.

    The files are written into a staging directory under `home` and renamed into place under the digest's first `DIGEST_NAME_LENGTH` hex digits, so a reader never sees a half-written snapshot; a directory already holding that digest is left as it is. `current` is then pointed at it, and every other snapshot directory but the one `current` named before is deleted.
    """
    tables_dir, home = Path(tables_dir), Path(home)
    sources = {FONT_NAME: tables_dir / FONT_NAME, KERN_SOURCE: tables_dir / KERN_SOURCE}
    for config in configs:
        sources[f"settlement-{config}.tsv"] = tables_dir / f"settlement-{config}.tsv"
        sources[f"joins-{config}.tsv"] = tables_dir / f"joins-{config}.tsv"
    missing = sorted(name for name, path in sources.items() if not path.is_file())
    if missing:
        raise SnapshotError(
            f"{tables_dir} has no {', '.join(missing)}; run `uv run python -m engine.pipeline.run_build` first"
        )
    if spec is None:
        spec = _load_spec(repo_root)
    home.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".taking-", dir=home))
    try:
        stamps: set[str] = set()
        for config in configs:
            try:
                stamp, decision = table.read_windows_head(tables_dir, config)
            except (OSError, ValueError) as error:
                raise SnapshotError(
                    f"{tables_dir}'s {config} windows head does not read ({error}); run `uv run python -m engine.pipeline.run_build`"
                ) from None
            stamps.add(stamp)
            inputs = _rule_inputs(sources[f"settlement-{config}.tsv"])
            if inputs != [rule.input_glyph for rule in decision.rules]:
                raise SnapshotError(
                    f"{tables_dir}'s {config} settlement table holds {len(inputs)} rules and its windows head "
                    f"{len(decision.rules)}, not the same rules; run `uv run python -m engine.pipeline.run_build`"
                )
            _write_certificates(staging / certificates_name(config), certificates_of(spec, decision))
            del decision
            _gzip_copy(sources[f"settlement-{config}.tsv"], staging / settlement_name(config))
            _gzip_copy(sources[f"joins-{config}.tsv"], staging / joins_name(config))
        if len(stamps) != 1:
            raise SnapshotError(
                f"{tables_dir}'s windows heads carry {len(stamps)} tables' stamps, so they are not one build's"
            )
        shutil.copyfile(sources[FONT_NAME], staging / FONT_NAME)
        _gzip_copy(sources[KERN_SOURCE], staging / KERN_NAME)
        record: dict = {
            "format": SNAPSHOT_FORMAT,
            "digest": None,
            "taken_at": utc_now(),
            "tables_stamp": next(iter(stamps)),
            "configs": list(configs),
            "source": _relative(tables_dir, repo_root),
            "files": {
                path.name: dict(zip(("sha256", "bytes"), _content(path)))
                for path in sorted(staging.iterdir())
            },
        }
        record["digest"] = _digest(record)
        (staging / RECORD_NAME).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        return install(staging, home)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def install(directory: Path, home: Path = DEFAULT_HOME) -> Snapshot:
    """Move the snapshot in `directory`, which must read (`load`), into `home` under the first `DIGEST_NAME_LENGTH` hex digits of its digest by one rename, and make it current: `current` is pointed at it, and every other snapshot directory but the one `current` named before is deleted. A directory under `home` that already holds that digest is kept and `directory` is deleted; one under that name that does not read is replaced. `directory` must be on the file system that holds `home`."""
    staged = load(Path(directory))
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    final = home / staged.digest[:DIGEST_NAME_LENGTH]
    if final.exists():
        try:
            load(final)
        except SnapshotError:
            shutil.rmtree(final)
    if final.exists():
        shutil.rmtree(staged.directory)
    else:
        staged.directory.chmod(0o755)
        staged.directory.rename(final)
    previous = current(home)
    _point(home, final.name)
    _prune(home, {final.name, *([previous.name] if previous is not None else [])})
    return load(final)


def find(home: Path, digest: str) -> Snapshot | None:
    """The snapshot under `home` with the digest `digest`, kept there as the current or the previous one, or None when no directory there holds it."""
    directory = Path(home) / digest[:DIGEST_NAME_LENGTH]
    if not (directory / RECORD_NAME).is_file():
        return None
    try:
        found = load(directory)
    except SnapshotError:
        return None
    return found if found.digest == digest else None


def make_current(found: Snapshot, home: Path = DEFAULT_HOME) -> None:
    """Point `current` under `home` at `found`, a snapshot directory there, keeping the one `current` named before and deleting older ones, as `take` does."""
    home = Path(home)
    previous = current(home)
    _point(home, found.directory.name)
    _prune(home, {found.directory.name, *([previous.name] if previous is not None else [])})


def pointer_record(snapshot: Snapshot) -> dict:
    """The tracked pointer's record for `snapshot`: the pointer's format, the snapshot's format, its digest and when it was taken."""
    return {
        "format": POINTER_FORMAT,
        "snapshot_format": snapshot.record.get("format"),
        "digest": snapshot.digest,
        "taken_at": snapshot.taken_at,
    }


def write_pointer(snapshot: Snapshot, path: Path = POINTER_PATH) -> dict:
    """Write the pointer naming `snapshot` to `path` through a staging file renamed into place, and return its record."""
    record = pointer_record(snapshot)
    path = Path(path)
    staging = path.with_name(f".{path.name}.partial")
    staging.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, path)
    return record


def parse_pointer(text: str, name: str) -> dict:
    """Read a pointer's text, naming it `name` in a refusal: a record in `POINTER_FORMAT` whose `digest` is a sha256 hex digest. Raises `SnapshotError` for anything else."""
    try:
        record = json.loads(text)
    except ValueError as error:
        raise SnapshotError(f"{name} does not read: {error}") from None
    if not isinstance(record, dict) or record.get("format") != POINTER_FORMAT:
        raise SnapshotError(f"{name} is not an {POINTER_FORMAT} record")
    digest = record.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SnapshotError(f"{name} names no snapshot digest")
    return record


def read_pointer(path: Path = POINTER_PATH) -> dict | None:
    """The pointer at `path` (`parse_pointer`), or None when there is no such file. Raises `SnapshotError` for one that does not read."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SnapshotError(f"{path} does not read: {error}") from None
    return parse_pointer(text, str(path))


def _git(repo_root: Path, *arguments: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *arguments], cwd=repo_root, capture_output=True, text=True, check=True
        ).stdout
    except OSError, subprocess.CalledProcessError:
        return None


def last_pointer_commit(repo_root: Path = REPO_ROOT) -> str | None:
    """The full sha of the last commit that changed the pointer in `repo_root`, the commit the accepted snapshot stands for, or None outside a git tree or when no commit has changed it."""
    found = _git(repo_root, "log", "-1", "--format=%H", "--", POINTER_RELATIVE)
    if found is None:
        return None
    return found.strip() or None


def pointer_at(revision: str, repo_root: Path = REPO_ROOT) -> dict:
    """The pointer that `revision`, a commit or a tree, holds (`parse_pointer`). Raises `SnapshotError` when git cannot show it or it does not read."""
    shown = _git(repo_root, "show", f"{revision}:{POINTER_RELATIVE}")
    if shown is None:
        raise SnapshotError(f"{revision} holds no {POINTER_RELATIVE}")
    return parse_pointer(shown, f"{revision}:{POINTER_RELATIVE}")


def pointer_commit(digest: str, repo_root: Path = REPO_ROOT) -> str | None:
    """The commit the snapshot with `digest` stands for: the last commit that changed the pointer, when the pointer it holds names `digest`. None outside a git tree, when no commit has changed the pointer, and when the last one names another snapshot, as while the accept that wrote the pointer on disk is not committed."""
    commit = last_pointer_commit(repo_root)
    if commit is None:
        return None
    try:
        return commit if pointer_at(commit, repo_root).get("digest") == digest else None
    except SnapshotError:
        return None


def tables_stamp(tables_dir: Path, configs: Sequence[str] = tablediff.SETTLED_CONFIGS) -> str | None:
    """The tables' stamp of the build in `tables_dir`: the `inputs` stamp every configuration's windows head records (`table.windows_head`, which reads the head and not the head file), or None when a head does not read or two disagree. The table-diff manifest copies it as its `tables` component, and the accept compares it with the corpus's."""
    stamps: set[object] = set()
    for config in configs:
        try:
            stamps.add(table.windows_head(Path(tables_dir), config).stamps.get(table.INPUTS_STAMP))
        except OSError, ValueError:
            return None
    found = stamps.pop() if len(stamps) == 1 else None
    return found if isinstance(found, str) else None


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(repo_root).resolve()))
    except ValueError:
        return str(path)


def describe(snapshot: Snapshot) -> Iterator[str]:
    yield f"{snapshot.directory}"
    yield f"  digest     {snapshot.digest}"
    yield f"  taken_at   {snapshot.taken_at}"
    yield f"  configs    {', '.join(snapshot.configs)}"
    yield f"  tables     {snapshot.tables_stamp}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="engine.review.snapshot", description=(__doc__ or "").split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    taking = commands.add_parser("take", help="snapshot a build directory and make it the current snapshot")
    taking.add_argument("--tables", type=Path, default=BUILD_DIR, help="the build directory")
    taking.add_argument(
        "--home", type=Path, default=DEFAULT_HOME, help="where snapshots and the `current` file live"
    )
    showing = commands.add_parser("show", help="describe the current snapshot")
    showing.add_argument("--home", type=Path, default=DEFAULT_HOME)
    args = parser.parse_args(argv)
    try:
        if args.command == "take":
            snapshot = take(args.tables, args.home)
            print("\n".join(describe(snapshot)))
            return
        directory = current(args.home)
        if directory is None:
            raise SystemExit(
                f"{args.home} has no current snapshot; take one with `uv run python -m engine.review.snapshot take`"
            )
        print("\n".join(describe(load(directory))))
    except SnapshotError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main(sys.argv[1:])

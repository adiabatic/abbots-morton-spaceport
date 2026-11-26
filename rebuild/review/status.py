"""Compute the review corpus's readiness: whether the served corpus exists, whether it matches the runes and code on disk (its after font is the M1 font on disk, and its per-unit index, both app sidecars and its unit store are stamped for its manifest, the check of a finished corpus the cycle shares, `app_index.outputs_not_current`), whether a green artifact cycle produced it, and whether the verdict store (the autosave) is stamped for it. The serve.py /status handler and the verdict_ready CLI both render the dict `compute_status` returns, so a change to its shape changes theirs.

The checks follow the manifest's `mode`. An m1-audit corpus is checked against `fingerprint.COMPONENTS` and its after font outside the `kern` lookup, and names the fullest verdicts file. A table-diff corpus (rebuild/REVIEW-PLAN.md §2.3) is checked against `fingerprint.TABLE_DIFF_COMPONENTS` and its after font byte for byte (`_table_diff_freshness_check`), adds the `snapshot` check, which fails when no snapshot is current or the current one is not the one the tracked pointer names (`_snapshot_check`), and the `diff` check, which never fails and counts the unaccepted changes by what each needs (`_diff_check`), and names no fullest verdicts file, since its units are new to every store.

An effective verdict is a unit's latest verdict when that verdict is not skip, a store's override records on listed units (`rebuild.review.accepted.Overrides`) counted with its own. The store's count also takes the accepted list beside it (`rebuild.review.accepted`): each listed unit of the served corpus's human units that holds no override is an effective verdict of the store's, under its mark, so a store a close-out emptied counts the units it folded and no verdicts file at the root outranks it. A root file's count takes no listed unit, or a ten-record export would outrank the store. A list the readers cannot read, or a store that does not pair with it (`accepted.pair`), fails the verdict-store check, as the review server and every pass refuse it.

Two kinds of state are kept between calls. `_MEMO`, in the process, is keyed on each verdicts file's stat. It records the file's stamp and, once the file has been parsed, its effective count, so a long-lived server checks an unchanged file with one stat. The store record, `var/cycle/store-record.json` under the directory that holds the verdicts files (`store_record_path`), is kept between processes and keyed on each verdicts file's SHA-256 and on the code of its readers (`store_record_code`). For each of the `STORE_RECORD_KEEP` files it filled last, it holds two fields that fill independently: `digest`, the file's records digest (`records_digest`), which the verdict update's key reads (`artifact_cycle.verdicts_records_digest`), and `carry`, what the carry source and the fullest-verdicts pick read of the file (`_Carried`). A pass over an unchanged store then reads and hashes it instead of parsing it. A reader hashes the bytes it would parse, read once, and records a field only once it has computed the field from those bytes, never on a failed or refused read. The verdict-store check parses the store on every call, since the blank count needs its records, and records the carry field from that parse.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.review import accepted, app_index, store_lock, unit_index
from rebuild.review.accepted import Accepted, Overrides, View
from rebuild.review.serve import parse_autosave_payload
from rebuild.review.verdict_store import (
    MalformedStore,
    accepted_path_for,
    orphans_dir_for,
    read_store_document,
    summarize_orphans,
)

CORPUS_REMEDY = "uv run python -m rebuild.review.build"
MERGE_TOOL = "uv run python -m rebuild.tools.merge_verdicts"
# Where a table-diff corpus's snapshots and tracked pointer are under the repo root (`rebuild.review.snapshot.DEFAULT_HOME`, `POINTER_PATH`), named here so the readiness check of an m1-audit corpus imports nothing of the snapshot.
SNAPSHOT_HOME = Path("var") / "keep" / "review-snapshot"
POINTER = Path("rebuild") / "review-snapshot.json"


def _latest_from_list(verdicts) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for record in verdicts:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def latest_verdicts(path) -> dict[str, dict]:
    return _latest_from_list(json.loads(Path(path).read_text())["verdicts"])


def count_effective(records) -> int:
    return sum(1 for record in records.values() if record.get("verdict") != "skip")


def _verdict_paths(repo_root) -> list[Path]:
    """Return the verdicts-*.json files at the repo root, which holds exports, autosave stashes, restore previews, and the carried file a --no-merge or staging pass writes. The live autosave is excluded; callers that need it read it separately."""
    return [
        path
        for path in sorted(Path(repo_root).glob("verdicts-*.json"))
        if path.name != "verdicts-autosave.json"
    ]


def _effective_count(data) -> int | None:
    """Return how many records other than skips the verdicts document `data` holds, its override section's among them (`accepted.records_of`), or None when one cannot be counted."""
    try:
        return count_effective(_latest_from_list(accepted.records_of(data)))
    except KeyError, TypeError:
        return None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One verdicts file's stamp and effective count, as the fullest verdicts pick and the carry source need them. A stamp of None means the file is not a verdicts document. On a parsed file, a count of None means a record could not be counted. On an unparsed file only the head was read, so the stamp comes from the head and the count is unknown."""

    stamp: str | None
    count: int | None
    parsed: bool


STORE_RECORD = Path("var") / "cycle" / "store-record.json"
STORE_RECORD_KEEP = 16
_STORE_RECORD_CODE = ("status.py", "verdict_store.py", "accepted.py")


def store_record_path(directory) -> Path:
    """Return the store record of the verdicts files in `directory`: `STORE_RECORD` under it, so a store in a test's or a clone's directory has a record of its own."""
    return Path(directory) / STORE_RECORD


@functools.cache
def store_record_code() -> str:
    """Return the SHA-256 of the code that computes what the store record holds, the modules `_STORE_RECORD_CODE` names in this checkout's rebuild/review: this module, which holds the records digest (`records_digest`, `sorted_records_digest`) and the effective count (`_effective_count`), verdict_store.py, which holds `parse_autosave_payload`, and accepted.py, which holds the stream readers the digest reads through (`accepted.read_tail`, `accepted.iter_records`) and the override section (`accepted.records_of`, `accepted.Overrides`). A record written under another value is ignored, so a change to how a field is computed never answers from a record the old code wrote."""
    here = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in _STORE_RECORD_CODE:
        digest.update(f"{name}\0".encode() + (here / name).read_bytes() + b"\0")
    return digest.hexdigest()


def _store_record_files(path: Path) -> dict:
    """Return the store record at `path` as a dict from a file's SHA-256 to its fields, or an empty one when the record is missing, unreadable, not the record's shape, or written under another `store_record_code`."""
    try:
        with open(path, "rb") as handle:
            held = json.load(handle)
    except OSError, ValueError:
        return {}
    if not isinstance(held, dict) or held.get("code") != store_record_code():
        return {}
    files = held.get("files")
    return files if isinstance(files, dict) else {}


def recorded(path: Path, key: str, field: str) -> object:
    """Return the field `field` the store record at `path` holds for the verdicts file whose bytes hash to `key`, or None when it holds none for this code."""
    entry = _store_record_files(path).get(key)
    return entry.get(field) if isinstance(entry, dict) else None


def record_field(path: Path, key: str, field: str, value: object) -> None:
    """Set `field` to `value` in the store record at `path` for the verdicts file whose bytes hash to `key`, keeping the file's other field and the `STORE_RECORD_KEEP` files filled last. The record is written whole to a temporary name and renamed over `path`, so a reader never meets half of one; a record that cannot be written is left as it was, and two writers at once can lose one's field, which costs that field's next reader a parse."""
    files = _store_record_files(path)
    entry = files.pop(key, None)
    files[key] = {**(entry if isinstance(entry, dict) else {}), field: value}
    kept = dict(list(files.items())[-STORE_RECORD_KEEP:])
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({"code": store_record_code(), "files": kept}))
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)


def records_digest(handle: io.BufferedReader) -> str | None:
    """Return the records digest of the verdicts file open at `handle`, hashed as it streams, or None for a file the stream does not take, whose digest `sorted_records_digest` gives. The digest covers a document's content: every top-level field but `exported_at`, then its own records in (unit, canonical JSON) order, a line each, then the override section's records and tombstones (`accepted.Overrides`), each sorted by its canonical JSON apart from the store's own. Every write stamps the store with a new `exported_at`, the review server's idle write and a lock holder's catch-up among them, and the merge and the server lay records out differently, so neither a save that changes no record nor a rewrite in another layout moves it. A file that is not a JSON object with a `verdicts` list is hashed by its bytes.

    The file is read from its start as `accepted.read_tail` and `accepted.iter_records` read it, a block of about 1 MiB and one record at a time, never parsed whole. The stream does not take a file `read_tail` does not read as an ams-review-verdicts/1 store, one with a second `verdicts` list, or one whose records are not in (unit, line) order, which holds for every file the server, the merge and the land write, since each writes its records sorted by unit, one a unit. The members but the records are read first, then the records, hashed as they stream, both from the file `handle` opened: `read_tail` reads it through its descriptor's `/dev/fd` name, so a writer that renames another file over the path meanwhile changes neither read.
    """
    handle.seek(0)
    tail = accepted.read_tail(f"/dev/fd/{handle.fileno()}")
    if tail is None or tail.lists != 1:
        return None
    digest = hashlib.sha256(_records_digest_head(dict(tail.members)))
    last: tuple[str, str] | None = None
    try:
        for record in accepted.iter_records(handle):
            key = _record_order_key(record)
            if last is not None and key < last:
                return None
            digest.update(key[1].encode() + b"\n")
            last = key
    except ValueError:
        return None
    for line in _records_digest_overrides(tail.members.get(accepted.OVERRIDES)):
        digest.update(line)
    return digest.hexdigest()


def _record_order_key(record: object) -> tuple[str, str]:
    """Return the key `records_digest` orders a store's records by: the record's unit, "" for one that names no string unit, and its canonical JSON line."""
    unit = record.get("unit") if isinstance(record, dict) else None
    return (unit if isinstance(unit, str) else "", json.dumps(record, sort_keys=True, ensure_ascii=False))


def _records_digest_head(document: dict) -> bytes:
    """Return the line `records_digest` starts with: a document's members but its records, `exported_at` and its override section, as canonical JSON."""
    document.pop("exported_at", None)
    document.pop(accepted.OVERRIDES, None)
    return json.dumps(document, sort_keys=True, ensure_ascii=False).encode() + b"\n"


def _records_digest_overrides(section: object) -> Iterator[bytes]:
    """Yield the lines `records_digest` ends with: a document's override section, its records and then its tombstones each sorted by canonical JSON, and nothing for a document with no section."""
    if section is None:
        return
    yield b"overrides\n"
    held = section if isinstance(section, dict) else {}
    for name in ("verdicts", "cleared"):
        entries = held.get(name)
        for line in sorted(
            json.dumps(entry, sort_keys=True, ensure_ascii=False)
            for entry in (entries if isinstance(entries, list) else [])
        ):
            yield f"{name}\t{line}\n".encode()


def sorted_records_digest(handle: io.BufferedReader) -> str:
    """Return `records_digest`'s value for the file open at `handle`, read whole from its start, its records sorted by their (unit, line) key, for a file the streamed read does not take: one out of that order, one `accepted.read_tail` does not read as an ams-review-verdicts/1 store, or one with a second `verdicts` list, whose last list this reads as `json.load` does. A file that is not a JSON object with a `verdicts` list is hashed by its bytes, and one that cannot be read is "absent". The cycle runs it in a short child, so no whole parse of the store stays in its own process (`artifact_cycle._whole_records_digest`)."""
    try:
        handle.seek(0)
        document = json.load(handle)
    except OSError:
        return "absent"
    except ValueError:
        document = None
    if not isinstance(document, dict) or not isinstance(document.get("verdicts"), list):
        try:
            handle.seek(0)
            return hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError:
            return "absent"
    records = document.pop("verdicts")
    section = document.get(accepted.OVERRIDES)
    digest = hashlib.sha256(_records_digest_head(document))
    for _unit, line in sorted(_record_order_key(record) for record in records):
        digest.update(line.encode() + b"\n")
    for line in _records_digest_overrides(section):
        digest.update(line)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _Carried:
    """What the carry source and the fullest-verdicts pick read of a verdicts document, the store record's `carry` field: its stamp, its effective count (`_effective_count`, None when a record cannot be counted), the accepted list it names (its `accepted` field, None when it names none), and the units its override section names, which are all `_listed_count` reads of that section."""

    stamp: str
    count: int | None
    named: object
    overridden: frozenset[str]

    @classmethod
    def of(cls, data: dict, count: int | None, overrides: Overrides) -> _Carried:
        return cls(
            data["manifest_generated_at"],
            count,
            data.get("accepted"),
            frozenset(overrides.records) | frozenset(overrides.cleared),
        )

    @classmethod
    def parse(cls, raw: bytes) -> tuple[_Carried, dict] | None:
        """Return what the carry reads of the verdicts file whose bytes are `raw`, with the document it parsed, or None when `raw` is not an ams-review-verdicts/1 document."""
        data = parse_autosave_payload(raw)
        if data is None:
            return None
        return cls.of(data, _effective_count(data), Overrides.of(data)), data

    @classmethod
    def recorded(cls, record: Path, key: str) -> _Carried | None:
        """Return the `carry` field the store record at `record` holds for the file whose bytes hash to `key`, or None when it holds none of this shape for this code."""
        field = recorded(record, key, "carry")
        if not isinstance(field, dict):
            return None
        stamp, count, overridden = field.get("stamp"), field.get("count"), field.get("overridden")
        if (
            not isinstance(stamp, str)
            or not (count is None or (isinstance(count, int) and not isinstance(count, bool)))
            or not isinstance(overridden, list)
            or not all(isinstance(unit, str) for unit in overridden)
        ):
            return None
        return cls(stamp, count, field.get("accepted"), frozenset(overridden))

    def record(self, record: Path, key: str) -> None:
        record_field(
            record,
            key,
            "carry",
            {
                "stamp": self.stamp,
                "count": self.count,
                "accepted": self.named,
                "overridden": sorted(self.overridden),
            },
        )

    def overrides(self) -> Overrides:
        """Return an override section that names the units this one names, which is all `_listed_count` reads of it."""
        return Overrides({}, dict.fromkeys(self.overridden, ""))


def _summarize(raw: bytes, record: Path) -> _Candidate:
    """Return the stamp and effective count of the verdicts file whose bytes are `raw`: from the store record at `record` when it holds them for these bytes, and otherwise from a parse of `raw`, which then records them. Only the two scalars are returned, so the parsed store is freed before the caller reads the next file."""
    key = hashlib.sha256(raw).hexdigest()
    carried = _Carried.recorded(record, key)
    if carried is None:
        parsed = _Carried.parse(raw)
        if parsed is None:
            return _Candidate(None, None, True)
        carried = parsed[0]
        carried.record(record, key)
    return _Candidate(carried.stamp, carried.count, True)


_SETTLE_NS = 2_000_000_000
_StatKey = tuple[int, int, int, int, int]
_MEMO: dict[Path, tuple[_StatKey, _Candidate]] = {}


def _stat_key(stat: os.stat_result) -> _StatKey:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _read_candidate(
    path: Path, manifest_stamp, *, counts: bool, record: Path
) -> tuple[_StatKey, bool, _Candidate] | None:
    """Read one verdicts file and return its stat key from the open handle, whether its last change was at least _SETTLE_NS before the read began, and its candidate; or None when the file cannot be read. With `counts`, every file is read whole. Without it, a file whose head, its first `accepted.HEAD_BYTES` bytes, settles a stamp other than manifest_stamp (`accepted.head_stamp`) returns a head-only candidate, and every other file is read whole, so a candidate stamped manifest_stamp always carries the stamp a whole parse gives. A file read whole is summarized from those bytes (`_summarize`), through the store record at `record`. The handle is unbuffered so that the whole-file read after the head returns one bytes object, with no copy that joins a buffered head onto the rest. The head is the whole file only when the size fstat reported fits inside it.

    The head read judges a file with a second top-level manifest_generated_at after its `verdicts` array by the first, where a whole parse takes the later one. On such a file, pick_fullest_verdicts's result also depends on earlier calls: once resolve_carry_source has memoized the file's whole parse, pick_fullest_verdicts uses that parse's stamp, the later one. No repo writer makes such a file.
    """
    try:
        with path.open("rb", buffering=0) as handle:
            stat = os.fstat(handle.fileno())
            now = time.time_ns()
            key = _stat_key(stat)
            settled = now - max(stat.st_mtime_ns, stat.st_ctime_ns) >= _SETTLE_NS
            if counts:
                return key, settled, _summarize(handle.read(), record)
            head = handle.read(accepted.HEAD_BYTES)
            stamp = accepted.head_stamp(head)
            if stamp is not None and stamp != manifest_stamp:
                return key, settled, _Candidate(stamp, None, False)
            if stat.st_size <= len(head):
                return key, settled, _summarize(head, record)
            handle.seek(0)
            return key, settled, _summarize(handle.read(), record)
    except OSError:
        return None


def _listed_count(overrides: Overrides, listed: Accepted, human_ids) -> int:
    """Return how many units a store whose override section is `overrides` reads as accepted: listed, among `human_ids` (every listed id when they are None), and holding no override in it."""
    if not listed.verdicts:
        return 0
    return sum(1 for _ in View({}, {}, listed, overrides).from_list(human_ids))


def _read_autosave(
    autosave_path: Path, listed_path: Path | None, review_dir: Path, manifest_stamp
) -> _Candidate | None:
    """Read the store as a carry candidate: its stamp, and its effective count with its accepted units on the served corpus's human units (`_listed_count`; none when no corpus is served), after `accepted.pair` has checked that it pairs with the list at `listed_path`, which raises `accepted.Refused` for a list it cannot read or a store that does not pair with it. What the carry reads of the store comes from the store record beside it (`store_record_path`) when that holds it for the store's bytes, and otherwise from a parse of them, which then records it. A recorded store that names no list, beside a list, is parsed again for the pairing, since only its records, tombstones and override section say whether it pairs."""
    try:
        raw = autosave_path.read_bytes() if autosave_path.exists() else None
    except OSError:
        raw = None
    if raw is None:
        return None
    record = store_record_path(autosave_path.parent)
    key = hashlib.sha256(raw).hexdigest()
    carried = _Carried.recorded(record, key)
    data: dict | None = None
    if carried is None:
        parsed = _Carried.parse(raw)
        if parsed is None:
            return _Candidate(None, None, True)
        carried, data = parsed
        carried.record(record, key)
    candidate = _Candidate(carried.stamp, carried.count, True)
    if candidate.count is None or listed_path is None:
        return candidate
    listed = accepted.read(listed_path)
    if data is None and carried.named is None and listed.sha256 is not None:
        data = parse_autosave_payload(raw)
    accepted.pair(data if data is not None else {"accepted": carried.named}, listed, store=autosave_path)
    if manifest_stamp is None or not listed.verdicts:
        return candidate
    count = _listed_count(carried.overrides(), listed, load_human_unit_ids(review_dir))
    return _Candidate(candidate.stamp, candidate.count + count, True)


def _survey(repo_root, manifest_stamp, *, counts: bool) -> list[tuple[Path, _Candidate]]:
    """Return every verdicts file `_verdict_paths` finds, in its order and under the path it returns, paired with its candidate. A file that cannot be stat'ed or read is left out. A file is taken from _MEMO when its _stat_key is unchanged and the entry is enough for this call: a parsed entry always, and a head-only entry only when `counts` is false and its stamp is set and is not manifest_stamp. Any other file is read, through the store record of the repo root (`store_record_path`), and the read is memoized only when the file's last change was at least _SETTLE_NS before the read began, so a same-size rewrite within one timestamp tick is read again. _MEMO is then replaced by this call's entries, so it holds only files the latest glob found."""
    global _MEMO
    memo: dict[Path, tuple[_StatKey, _Candidate]] = {}
    surveyed: list[tuple[Path, _Candidate]] = []
    record = store_record_path(repo_root)
    for path in _verdict_paths(repo_root):
        try:
            key = _stat_key(path.stat())
        except OSError:
            continue
        memoized = _MEMO.get(path)
        if memoized is not None and memoized[0] == key:
            candidate = memoized[1]
            if candidate.parsed or (
                not counts and candidate.stamp is not None and candidate.stamp != manifest_stamp
            ):
                memo[path] = memoized
                surveyed.append((path, candidate))
                continue
        read = _read_candidate(path, manifest_stamp, counts=counts, record=record)
        if read is None:
            continue
        read_key, settled, candidate = read
        if settled:
            memo[path] = (read_key, candidate)
        surveyed.append((path, candidate))
    _MEMO = memo
    return surveyed


def pick_fullest_verdicts(repo_root, manifest_stamp) -> tuple[Path, int] | None:
    """Return the stamp-matching verdicts file at the repo root with the most effective verdicts: the verdicts file stamped manifest_stamp with the most, with that count, taking the first in glob order on a tie; or None when no file qualifies. The live autosave is not a candidate. `compute_status` compares this result with the store to name the fullest verdicts file, and merge_verdicts' auto-pick makes the same comparison. A file whose head settles a different stamp costs one bounded read and is not parsed. Any other file is parsed whole before its stamp and count are used. A file unchanged since a memoized read costs one stat."""
    best: tuple[Path, int] | None = None
    for path, candidate in _survey(repo_root, manifest_stamp, counts=False):
        if candidate.stamp is None or candidate.stamp != manifest_stamp:
            continue
        if candidate.count is None:
            continue
        if best is None or candidate.count > best[1]:
            best = (path, candidate.count)
    return best


def resolve_carry_source(
    repo_root, manifest_stamp, autosave_path, *, review_dir=None, accepted_path=None
) -> dict | None:
    """Return the verdicts file the artifact cycle carries forward when the caller names none, as a dict of `path`, `stamp`, `count`, and `aligned`, or None when no candidate has an effective verdict.

    The candidates are the live autosave and every verdicts-*.json at the repo root. The autosave's count takes its accepted units on the human units of the corpus at `review_dir` (the served corpus, rebuild/out/review under `repo_root`, by default), read from the list at `accepted_path` (by default the one beside the autosave, `verdict_store.accepted_path_for`), and a list it cannot read or an autosave that does not pair with it raises `accepted.Refused`. Among those stamped manifest_stamp, the one with the most effective verdicts wins, and the autosave wins a tie because it is the live store. When none is stamped manifest_stamp, the candidates with the newest stamp compete the same way and `aligned` is False. That happens when the served corpus was restamped outside a recorded cycle, or when a pass stopped between its corpus build and its carry. The cycle uses `aligned` in the line that names the master and to choose between the direct merge and the full carry. When the corpus build is skipped, an aligned master that is another file is merged straight into the store, or, when the snapshot of the store the verdict update reads is stamped for an older corpus or is not an ams-review-verdicts/1 document, into that snapshot carried onto the served corpus; an aligned master that is the store is merged from its snapshot, or, when that snapshot is either, carried onto the served corpus in its own place (`verdict_update.merge_sets_store_aside`; the carry refuses a snapshot it cannot read, naming it, and the merge refuses an ams-review-verdicts/1 snapshot stamped for a newer corpus, either of which fails the verdict update). An unaligned master is always carried by unit id. A verdict names its unit by content id, so a carried verdict reaches the unit with that id on the live corpus or none.

    Every export is counted whatever its stamp, through the memo shared with pick_fullest_verdicts, so a file pick_fullest_verdicts read by its head alone is read whole here. The autosave is read whole on every call and is not memoized by its stat. A file read whole is parsed only when the store record holds no `carry` field for its bytes (`_Carried`), so on an unchanged tree each file costs a read and a hash.
    """
    entries: list[tuple[Path, str, int, bool]] = []
    autosave_path = Path(autosave_path)
    review_dir = (
        Path(review_dir) if review_dir is not None else Path(repo_root) / "rebuild" / "out" / "review"
    )
    listed_path = Path(accepted_path) if accepted_path is not None else accepted_path_for(autosave_path)
    autosave = _read_autosave(autosave_path, listed_path, review_dir, manifest_stamp)
    if autosave is not None and autosave.stamp is not None and autosave.count:
        entries.append((autosave_path, autosave.stamp, autosave.count, True))
    for path, candidate in _survey(repo_root, manifest_stamp, counts=True):
        if candidate.stamp is not None and candidate.count:
            entries.append((path, candidate.stamp, candidate.count, False))
    if not entries:
        return None
    pool = [entry for entry in entries if manifest_stamp is not None and entry[1] == manifest_stamp]
    aligned = bool(pool)
    if not pool:
        latest = max(stamp for _, stamp, _, _ in entries)
        pool = [entry for entry in entries if entry[1] == latest]
    path, stamp, count, _ = max(pool, key=lambda entry: (entry[2], entry[3]))
    return {"path": path, "stamp": stamp, "count": count, "aligned": aligned}


def load_human_unit_ids(review_dir) -> frozenset[str]:
    manifest = json.loads((Path(review_dir) / "manifest.json").read_text())
    persisted = manifest["human_unit_ids"]
    if not isinstance(persisted, list) or not all(
        isinstance(unit, str) and unit.startswith("u-") for unit in persisted
    ):
        raise TypeError("manifest human_unit_ids must be a list of u- ids")
    if len(persisted) != len(set(persisted)):
        raise ValueError("manifest human_unit_ids must be unique")
    return frozenset(persisted)


def _load_json_dict(path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text())
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def _rel(repo_root, path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return str(path)


def _corpus_check(manifest) -> dict:
    if manifest is None:
        return {
            "level": "fail",
            "detail": "The review corpus has no readable manifest.json.",
            "remedy": CORPUS_REMEDY,
        }
    return {"level": "ok", "detail": "The review corpus manifest is present and readable.", "remedy": None}


def _freshness_check(
    manifest, manifest_fp, repo_root, recompute, artifact_cycle_remedy, review_dir, m1_out
) -> dict:
    unknown = {name: "unknown" for name in fingerprint.COMPONENTS}
    if manifest is None:
        return {
            "level": "fail",
            "detail": "The corpus manifest is missing, so its build inputs cannot be checked.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    if not isinstance(manifest_fp, dict):
        return {
            "level": "fail",
            "detail": "The corpus manifest records no input fingerprint, so its freshness cannot be verified.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    try:
        current = recompute(repo_root)
    except Exception:
        return {
            "level": "fail",
            "detail": "The current build inputs could not be recomputed, so the corpus freshness is unknown.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    components: dict[str, str] = {}
    for name in fingerprint.COMPONENTS:
        recorded = manifest_fp.get(name)
        if recorded is None:
            components[name] = "unknown"
        elif current.get(name) == recorded:
            components[name] = "fresh"
        else:
            components[name] = "stale"
    hard = [
        name
        for name in fingerprint.COMPONENTS
        if name not in unit_index.ASSET_COMPONENTS and components[name] != "fresh"
    ]
    if hard:
        stale = [name for name in hard if components[name] == "stale"]
        if stale:
            detail = f"The build inputs changed since the corpus was generated: {', '.join(stale)}."
        else:
            detail = f"These components cannot be verified until the next M1 build records them: {', '.join(hard)}."
        return {
            "level": "fail",
            "detail": detail,
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    fonts = manifest.get("fonts")
    after_record = fonts.get("after") if isinstance(fonts, dict) else None
    recorded_sha = after_record.get("sha256") if isinstance(after_record, dict) else None
    after = Path(m1_out) / "M1.otf"
    if not isinstance(recorded_sha, str):
        return {
            "level": "fail",
            "detail": "The corpus records no after-font hash, so it cannot be checked against rebuild/out/m1/M1.otf.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    if not fingerprint.after_font_current(Path(review_dir) / "fonts" / "after.otf", recorded_sha, after):
        return {
            "level": "fail",
            "detail": "The corpus's after font is not the M1 font on disk: rebuild/out/m1/M1.otf is missing or has moved on, outside its kern lookup, since this corpus was built, so the review app is serving last build's font.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    missing = app_index.outputs_not_current(review_dir)
    if missing:
        return {
            "level": "fail",
            "detail": f"These files are missing or stamped for another manifest: {', '.join(missing)}. Either the build that wrote this corpus did not finish, or the manifest was rewritten without them.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    if any(components[name] != "fresh" for name in unit_index.ASSET_COMPONENTS):
        return {
            "level": "warn",
            "detail": "Only the review UI assets changed since the corpus was generated; the units are unchanged, and the cycle refreshes the served copy in place.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    return {
        "level": "ok",
        "detail": "The corpus reflects the current build inputs.",
        "remedy": None,
        "components": components,
    }


def table_diff_inputs(repo_root, snapshot_home=None) -> dict:
    """Return the table-diff components the sources and the current snapshot give now (`fingerprint.table_diff_values`): the record of the snapshot `current` names under `snapshot_home`, by default `var/keep/review-snapshot` under `repo_root`, and the tables' stamp the sources give (`run_m1.tables_inputs`). The two modules are imported here, at the call, so a server serving an m1-audit corpus never loads them."""
    from rebuild.pipeline import run_m1
    from rebuild.review import snapshot

    home = Path(snapshot_home) if snapshot_home is not None else Path(repo_root) / SNAPSHOT_HOME
    directory = snapshot.current(home)
    return fingerprint.table_diff_values(
        Path(repo_root),
        directory / snapshot.RECORD_NAME if directory is not None else None,
        run_m1.tables_inputs(),
    )


def _table_diff_freshness_check(
    manifest, manifest_fp, repo_root, recompute, artifact_cycle_remedy, review_dir, m1_out
) -> dict:
    """The freshness check of a table-diff corpus: every `fingerprint.TABLE_DIFF_COMPONENTS` value the manifest records against `recompute(repo_root)`, a `snapshot` both leave null (no snapshot) counting as fresh, then the after font against rebuild/out/m1/M1.otf byte for byte, since the corpus's kern units show its `kern` lookup, then the files a finished build leaves (`app_index.outputs_not_current`); a change in the asset component alone warns, as for an m1-audit corpus."""
    names = fingerprint.TABLE_DIFF_COMPONENTS
    unknown = {name: "unknown" for name in names}
    if not isinstance(manifest_fp, dict):
        return {
            "level": "fail",
            "detail": "The corpus manifest records no input fingerprint, so its freshness cannot be verified.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    try:
        current = recompute(repo_root)
    except Exception:
        return {
            "level": "fail",
            "detail": "The current build inputs could not be recomputed, so the corpus freshness is unknown.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    components: dict[str, str] = {}
    for name in names:
        recorded = manifest_fp.get(name)
        if recorded == current.get(name) and (recorded is not None or name == "snapshot"):
            components[name] = "fresh"
        elif recorded is None:
            components[name] = "unknown"
        else:
            components[name] = "stale"
    hard = [name for name in names if name not in unit_index.ASSET_COMPONENTS and components[name] != "fresh"]
    if hard:
        stale = [name for name in hard if components[name] == "stale"]
        detail = (
            f"The build inputs changed since the corpus was generated: {', '.join(stale)}."
            if stale
            else f"These components cannot be verified until the next build records them: {', '.join(hard)}."
        )
        return {"level": "fail", "detail": detail, "remedy": artifact_cycle_remedy, "components": components}
    fonts = manifest.get("fonts")
    after_record = fonts.get("after") if isinstance(fonts, dict) else None
    recorded_sha = after_record.get("sha256") if isinstance(after_record, dict) else None
    try:
        after_sha = fingerprint.file_sha256(Path(m1_out) / "M1.otf")
    except OSError:
        after_sha = None
    if not isinstance(recorded_sha, str) or after_sha != recorded_sha:
        return {
            "level": "fail",
            "detail": "The corpus's after font is not the M1 font on disk: rebuild/out/m1/M1.otf is missing or has moved on since this corpus was built, so the review app is serving last build's font.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    missing = app_index.outputs_not_current(review_dir)
    if missing:
        return {
            "level": "fail",
            "detail": f"These files are missing or stamped for another manifest: {', '.join(missing)}. Either the build that wrote this corpus did not finish, or the manifest was rewritten without them.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    if any(components[name] != "fresh" for name in unit_index.ASSET_COMPONENTS if name in components):
        return {
            "level": "warn",
            "detail": "Only the review UI assets changed since the corpus was generated; the units are unchanged, and the cycle refreshes the served copy in place.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    return {
        "level": "ok",
        "detail": "The corpus reflects the current build inputs.",
        "remedy": None,
        "components": components,
    }


REMAKE_REMEDY = "make review-snapshot-remake"
RETAKE_REMEDY = "make review-accept ARGS='--apply --retake'"


def _snapshot_check(pointer_path, snapshot_home) -> dict:
    """Check that the snapshot the corpus compares with is the accepted one: the tracked pointer (`snapshot.read_pointer`) reads and names a snapshot in this checkout's format, a snapshot is current under `snapshot_home` and reads (`snapshot.load`), and its digest is the pointer's. A missing or mismatched snapshot is remade from the commit that last changed the pointer (`make review-snapshot-remake`); a pointer that does not read or names another format is replaced by a retake."""
    from rebuild.review import snapshot

    try:
        pointer = snapshot.read_pointer(Path(pointer_path))
    except snapshot.SnapshotError as error:
        return {
            "level": "fail",
            "detail": f"{error}, so no snapshot is the accepted one.",
            "remedy": RETAKE_REMEDY,
        }
    if pointer is None:
        return {
            "level": "fail",
            "detail": f"{Path(pointer_path).name} is missing, so no snapshot is the accepted one.",
            "remedy": RETAKE_REMEDY,
        }
    digest = str(pointer["digest"])
    if pointer.get("snapshot_format") != snapshot.SNAPSHOT_FORMAT:
        return {
            "level": "fail",
            "detail": f"The pointer names an {pointer.get('snapshot_format')!r} snapshot, and this checkout reads only {snapshot.SNAPSHOT_FORMAT}.",
            "remedy": RETAKE_REMEDY,
        }
    directory = snapshot.current(Path(snapshot_home))
    if directory is None:
        return {
            "level": "fail",
            "detail": f"No snapshot is current on this machine; the pointer names {digest[:12]}.",
            "remedy": REMAKE_REMEDY,
        }
    try:
        found = snapshot.load(directory)
    except snapshot.SnapshotError as error:
        return {
            "level": "fail",
            "detail": f"The current snapshot does not read: {error}.",
            "remedy": REMAKE_REMEDY,
        }
    if found.digest != digest:
        return {
            "level": "fail",
            "detail": f"The current snapshot, {found.digest[:12]}, is not the accepted one the pointer names, {digest[:12]}.",
            "remedy": REMAKE_REMEDY,
        }
    return {
        "level": "ok",
        "detail": f"The current snapshot is the accepted one the pointer names, {digest[:12]}, taken at {found.taken_at}.",
        "remedy": None,
    }


ACCEPT_REMEDY = "make review-accept ARGS=--apply"
REFUSED_VERDICTS = frozenset({"reject", "neither"})


def baseline_since(manifest, repo_root) -> str:
    """What the corpus's changes are since: the first twelve hex digits of the commit the snapshot stands for, read from git now (`snapshot.pointer_commit`) or else from the manifest's `baseline`, or when the accept that took it is not committed yet, when it was taken; `no snapshot` for a corpus built with none."""
    from rebuild.review import snapshot

    baseline = manifest.get("baseline") if isinstance(manifest, dict) else None
    if not isinstance(baseline, dict) or not baseline.get("present"):
        return "no snapshot"
    digest = baseline.get("digest")
    commit = (
        snapshot.pointer_commit(digest, Path(repo_root)) if isinstance(digest, str) else None
    ) or baseline.get("commit")
    if isinstance(commit, str) and commit:
        return commit[:12]
    return f"the snapshot taken at {baseline.get('taken_at')}"


def _diff_check(manifest, aligned: _Aligned | None, human_ids, repo_root) -> dict:
    """Count the corpus's unaccepted changes: every unit (`count`), the human units that need a verdict (`human`), of which `blank` read blank or skipped and `rejected` read reject or neither, the machine-approved units (`machine`), and the units of the classes that take no verdict (`none`). It never fails. Once every human unit is verdicted and none is rejected, it warns with the accept as its remedy."""
    totals = manifest.get("totals")
    units = totals.get("units") if isinstance(totals, dict) else None
    count = units if isinstance(units, int) else 0
    human = frozenset(human_ids or ())
    none = sum(
        meta.get("unit_count", 0)
        for meta in manifest.get("classes") or ()
        if isinstance(meta, dict)
        and meta.get("no_verdict") is True
        and isinstance(meta.get("unit_count"), int)
    )
    machine = max(count - len(human) - none, 0)
    records = aligned.records if aligned is not None else {}
    verdicts = [records.get(unit, {}).get("verdict") for unit in human]
    blank = sum(1 for verdict in verdicts if verdict is None or verdict == "skip")
    rejected = sum(1 for verdict in verdicts if verdict in REFUSED_VERDICTS)
    since = baseline_since(manifest, repo_root)
    detail = (
        f"{count:,} unaccepted change{'' if count == 1 else 's'} since {since}: {len(human):,} need a verdict "
        f"({blank:,} blank, {rejected:,} rejected), {machine:,} machine-approved, {none:,} need none."
    )
    due = count > 0 and blank == 0 and rejected == 0
    return {
        "level": "warn" if due else "ok",
        "detail": detail,
        "remedy": ACCEPT_REMEDY if due else None,
        "count": count,
        "human": len(human),
        "blank": blank,
        "rejected": rejected,
        "machine": machine,
        "none": none,
        "since": since,
    }


def _gates_check(summary, generated_at, manifest_fp, artifact_cycle_remedy) -> dict:
    """Check the recorded cycle's gates by each gate entry's `skip` field. A "proved" skip means a matching green record showed that this content already passed, so it counts toward readiness. Every other non-green gate blocks a review session: a "forced" skip or a gate that did not run is reported as unverified, and anything else as failing. The status string cannot make this distinction, because every skip kind's status starts with "skipped (", so reading it would let a `--skip-conform` pass report READY."""
    if summary is None:
        return {
            "level": "fail",
            "detail": "There is no recorded artifact cycle for this corpus.",
            "remedy": artifact_cycle_remedy,
        }
    corpus = summary.get("corpus") or {}
    if corpus.get("generated_at") != generated_at or corpus.get("inputs_fingerprint") != manifest_fp:
        return {
            "level": "fail",
            "detail": "The recorded cycle is for a different corpus than the one being served.",
            "remedy": artifact_cycle_remedy,
        }
    gates = summary.get("gates") or {}
    exit_val = summary.get("exit")
    non_green = [name for name in sorted(gates) if not (gates[name] or {}).get("green")]
    if exit_val in ("interrupted", "failed"):
        failed = [name for name in non_green if not (gates[name] or {}).get("skip")]
        if failed:
            detail = f"The last artifact cycle {exit_val} with failing gates: {', '.join(failed)}."
        else:
            detail = f"The last artifact cycle {exit_val}."
        return {"level": "fail", "detail": detail, "remedy": artifact_cycle_remedy}
    if not non_green:
        return {
            "level": "ok",
            "detail": f"The last artifact cycle finished green at {summary.get('finished_at')}.",
            "remedy": None,
        }
    entries = {name: (gates[name] or {}) for name in non_green}
    unproved = [name for name in non_green if entries[name].get("skip") != "proved"]
    if not unproved:
        return {
            "level": "ok",
            "detail": f"The last artifact cycle finished green at {summary.get('finished_at')}; every gate it skipped was already proved on this exact content.",
            "remedy": None,
        }
    unverified = [
        name
        for name in unproved
        if entries[name].get("skip")
        or str(entries[name].get("status", "")).startswith(("skipped", "not run"))
    ]
    failing = [name for name in unproved if name not in set(unverified)]
    if failing:
        return {
            "level": "fail",
            "detail": f"The last artifact cycle has failing gates: {', '.join(failing)}.",
            "remedy": artifact_cycle_remedy,
        }
    return {
        "level": "fail",
        "detail": f"The last artifact cycle left gates unverified: {', '.join(unverified)}.",
        "remedy": artifact_cycle_remedy,
    }


@dataclass(frozen=True, slots=True)
class _Aligned:
    """The store as the checks after the verdict-store check read it: its latest record by unit, its override records among them, and `listed`, how many accepted units of the corpus's human units it holds no override for, which count as its effective verdicts."""

    records: dict[str, dict]
    listed: int

    @property
    def effective(self) -> int:
        return count_effective(self.records) + self.listed


def _verdict_store_check(
    autosave_path,
    generated_at,
    artifact_cycle_remedy,
    fullest_hit,
    fullest_rel,
    autosave=None,
    *,
    listed: Accepted | None = None,
    listed_path: Path | None = None,
    human_ids_of=lambda: None,
) -> tuple[dict, _Aligned | None]:
    """Check the store. `autosave` and `listed` are the parsed store and the list the review server already paired; without them the store and the list at `listed_path` are read here and paired (`accepted.pair`), and a list that cannot be read or a store that does not pair with it fails the check. `human_ids_of` returns the corpus's human unit ids, or None when they cannot be read, for the store's accepted units. A store read here and paired records the carry field for its bytes in the store record beside it (`_Carried`, `store_record_path`), from the records the check reads."""
    recording: tuple[Path, str] | None = None
    if autosave is None:
        path = Path(autosave_path)
        try:
            raw = path.read_bytes() if path.exists() else None
        except OSError:
            raw = None
        try:
            autosave = read_store_document(raw, f"The store {path.name}") if raw is not None else None
            if autosave is not None and raw is not None:
                recording = (store_record_path(path.parent), hashlib.sha256(raw).hexdigest())
        except MalformedStore as malformed:
            return {
                "level": "fail",
                "detail": f"{malformed}, so the review server serves no verdict from it and refuses every save, and a pass refuses to land over it.",
                "remedy": f"Restore a readable store from its journal: {MERGE_TOOL} --list names the recorded times, and {MERGE_TOOL} --restore-as-of <time> --apply restores one.",
            }, None
        try:
            listed = accepted.read(listed_path) if listed_path is not None else None
            if listed is not None:
                accepted.pair(autosave, listed, store=path.name)
        except accepted.Refused as refused:
            return {
                "level": "fail",
                "detail": f"{refused}. Until then the review server serves no verdict and refuses every save, and a pass refuses to run its verdict update or land.",
                "remedy": "Pair the store and the accepted list again, as the detail says.",
            }, None
    if listed is None:
        listed = Accepted.empty(listed_path or accepted.RELATIVE_PATH)
    if autosave is None:
        return {
            "level": "warn",
            "detail": "There is no autosave yet, so no in-progress verdicts are loaded.",
            "remedy": f"If the store was lost, restore it from its journal: {MERGE_TOOL} --list names the recorded times, and {MERGE_TOOL} --restore-as-of <time> --apply restores one.",
        }, None
    try:
        records: dict[str, dict] | None = _latest_from_list(accepted.records_of(autosave))
    except KeyError, TypeError:
        records = None
    own = count_effective(records) if records is not None else None
    overrides = Overrides.of(autosave)
    if recording is not None:
        _Carried.of(autosave, own, overrides).record(*recording)
    listed_count = _listed_count(overrides, listed, human_ids_of()) if listed.verdicts else 0
    if autosave["manifest_generated_at"] != generated_at:
        stale_effective = (own or 0) + listed_count
        if stale_effective == 0 and fullest_hit and fullest_hit[1] > 0:
            remedy = f"Merge {fullest_rel} into the autosave ({MERGE_TOOL}); the stale autosave is empty and is stashed automatically when it is stamped for an older corpus, and the merge refuses one stamped for a newer corpus."
        else:
            remedy = artifact_cycle_remedy
        return {
            "level": "fail",
            "detail": (
                f"The autosave is stamped for a different corpus ({autosave['manifest_generated_at']}) "
                "than the one being served."
            ),
            "remedy": remedy,
        }, None
    aligned = _Aligned(records if records is not None else {}, listed_count)
    effective = aligned.effective
    if effective == 0 and fullest_hit and fullest_hit[1] > 0:
        return {
            "level": "warn",
            "detail": "The autosave is aligned with this corpus but empty; the verdicts in the fullest verdicts file are not yet merged in.",
            "remedy": f"Merge {fullest_rel} into the autosave ({MERGE_TOOL}).",
        }, aligned
    from_list = (
        f", {listed_count:,} of them accepted units from the list closed at {listed.closed_at}"
        if listed_count
        else ""
    )
    return {
        "level": "ok",
        "detail": f"The autosave is aligned with this corpus and holds {effective:,} effective verdicts{from_list}.",
        "remedy": None,
    }, aligned


def _fullest_verdicts_check(fullest_hit, fullest_rel, store_rel, aligned: _Aligned | None) -> dict:
    """Name the fullest verdicts file: the store when it is stamped for this corpus and holds at least as many effective verdicts as `fullest_hit`, its accepted units counted, the stamp-matching root file with the most, which otherwise wins. The store wins a tie, as in `resolve_carry_source`."""
    store_count = aligned.effective if aligned is not None else None
    if store_count is not None and (not fullest_hit or store_count >= fullest_hit[1]):
        return {
            "level": "ok",
            "detail": f"The fullest verdicts file is the store, {store_rel} ({store_count:,} effective verdicts); no stamp-matching verdicts file at the repo root holds more.",
            "remedy": None,
            "path": store_rel,
            "count": store_count,
        }
    if fullest_hit:
        count = fullest_hit[1]
        return {
            "level": "ok",
            "detail": f"The fullest verdicts file is {fullest_rel} ({count:,} effective verdicts).",
            "remedy": None,
            "path": fullest_rel,
            "count": count,
        }
    return {
        "level": "warn",
        "detail": "No verdicts file at the repo root is stamped for this corpus, and the store is missing, unreadable, holds a record the carry refuses, or is stamped for another corpus.",
        "remedy": None,
        "path": None,
        "count": None,
    }


def _blanks_check(aligned: _Aligned | None, human_ids) -> dict:
    """Count the human units that read blank: neither an effective record, an override record among them, nor an accepted unit the store holds no override for (`_Aligned.listed`, counted over the same `human_ids`)."""
    if aligned is None:
        return {
            "level": "ok",
            "detail": "The blank count needs an autosave aligned with this corpus.",
            "count": None,
        }
    if human_ids is None:
        return {
            "level": "ok",
            "detail": "The corpus manifest's human unit ids could not be read, so the blank count is unavailable.",
            "count": None,
        }
    effective_ids = {unit for unit, record in aligned.records.items() if record.get("verdict") != "skip"}
    remaining = len(set(human_ids) - effective_ids) - aligned.listed
    return {"level": "ok", "detail": f"{remaining:,} blanks remaining.", "count": remaining}


def _human_ids_or_none(review_dir) -> frozenset[str] | None:
    try:
        return load_human_unit_ids(review_dir)
    except OSError, ValueError, KeyError, TypeError:
        return None


def compute_status(
    repo_root,
    review_dir,
    m1_out,
    autosave_path,
    cycle_summary_path,
    *,
    human_ids=None,
    recompute=None,
    autosave=None,
    listed: Accepted | None = None,
    accepted_path=None,
    recompute_table_diff=None,
    pointer_path=None,
    snapshot_home=None,
) -> dict:
    """Return the readiness dict. Beside the checks, `orphans` and `conflicts` count the saves the verdict store kept aside in its orphan documents (`verdict_store.summarize_orphans`), with the newest one's `recorded_at` and document and the counts per recent `recorded_at`, so the app can count those kept since it loaded. While an artifact-cycle pass holds its pass lock (var/cycle/pass.lock, `artifact_cycle.pass_lock`), the checks that would name `make artifact-cycle` as their remedy tell the reader to wait for that pass instead. `autosave` is the parsed autosave when the caller already holds it (serve.py passes its in-memory store so that each /status request does not read and parse the file again), with `listed`, the accepted list it paired with, and None to read `autosave_path` and the list at `accepted_path`, by default the one beside it (`verdict_store.accepted_path_for`), and pair them. The store's accepted units count among its effective verdicts and leave the blanks. A store file that holds a record `verdict_store.well_formed` refuses fails the verdict-store check, naming the first such record (`verdict_store.read_store_document`), since the review server serves no verdict from it and refuses every save (`VerdictStore.reload`); serve.py passes None while its store is refused.

    A table-diff corpus is checked by its own rows (the module docstring): `recompute_table_diff` gives its components now, `table_diff_inputs` by default; `pointer_path` is the tracked pointer, by default `rebuild/review-snapshot.json` under `repo_root`; and `snapshot_home` holds the snapshots, by default `var/keep/review-snapshot` under it. The result's `corpus` names the mode.
    """
    if recompute is None:
        recompute = fingerprint.compute_all
    repo_root = Path(repo_root)
    review_dir = Path(review_dir)

    manifest = _load_json_dict(review_dir / "manifest.json")
    generated_at = manifest.get("generated_at") if manifest else None
    repo_head = manifest.get("repo_head") if manifest else None
    manifest_fp = manifest.get("inputs_fingerprint") if manifest else None
    mode = manifest.get("mode", "m1-audit") if manifest else None
    table_diff = mode == "table-diff"
    if snapshot_home is None:
        snapshot_home = repo_root / SNAPSHOT_HOME
    if pointer_path is None:
        pointer_path = repo_root / POINTER

    fullest_hit = None if table_diff else pick_fullest_verdicts(repo_root, generated_at)
    fullest_rel = _rel(repo_root, fullest_hit[0]) if fullest_hit else None
    running_pass = store_lock.holder(repo_root / "var" / "cycle" / "pass.lock")
    if running_pass is None:
        artifact_cycle_remedy = "make artifact-cycle"
    else:
        artifact_cycle_remedy = f"wait for the running pass (pid {running_pass}) to finish"

    summary = _load_json_dict(cycle_summary_path)

    loaded: list[frozenset[str] | None] = []

    def human_ids_of() -> frozenset[str] | None:
        if human_ids is not None:
            return human_ids
        if not loaded:
            loaded.append(_human_ids_or_none(review_dir))
        return loaded[0]

    verdict_store, aligned = _verdict_store_check(
        autosave_path,
        generated_at,
        artifact_cycle_remedy,
        fullest_hit,
        fullest_rel,
        autosave,
        listed=listed if autosave is not None else None,
        listed_path=(
            Path(accepted_path) if accepted_path is not None else accepted_path_for(Path(autosave_path))
        ),
        human_ids_of=human_ids_of,
    )
    if table_diff:
        assert manifest is not None
        checks = {
            "corpus": _corpus_check(manifest),
            "freshness": _table_diff_freshness_check(
                manifest,
                manifest_fp,
                repo_root,
                (
                    recompute_table_diff
                    if recompute_table_diff is not None
                    else (lambda root: table_diff_inputs(root, snapshot_home))
                ),
                artifact_cycle_remedy,
                review_dir,
                m1_out,
            ),
            "gates": _gates_check(summary, generated_at, manifest_fp, artifact_cycle_remedy),
            "snapshot": _snapshot_check(pointer_path, snapshot_home),
            "verdict_store": verdict_store,
            "blanks": _blanks_check(aligned, human_ids_of() if aligned is not None else None),
            "diff": _diff_check(manifest, aligned, human_ids_of(), repo_root),
        }
        blocking: tuple[str, ...] = ("corpus", "freshness", "gates", "snapshot", "verdict_store")
    else:
        checks = {
            "corpus": _corpus_check(manifest),
            "freshness": _freshness_check(
                manifest, manifest_fp, repo_root, recompute, artifact_cycle_remedy, review_dir, m1_out
            ),
            "gates": _gates_check(summary, generated_at, manifest_fp, artifact_cycle_remedy),
            "verdict_store": verdict_store,
            "fullest_verdicts": _fullest_verdicts_check(
                fullest_hit, fullest_rel, _rel(repo_root, autosave_path), aligned
            ),
            "blanks": _blanks_check(aligned, human_ids_of() if aligned is not None else None),
        }
        blocking = ("corpus", "freshness", "gates", "verdict_store")
    ready = all(checks[name]["level"] != "fail" for name in blocking)
    kept_aside = summarize_orphans(orphans_dir_for(Path(autosave_path)))
    for entry in kept_aside.values():
        if entry["file"] is not None:
            entry["file"] = _rel(repo_root, entry["file"])
    return {
        "ready": ready,
        "corpus": {
            "dir": str(review_dir),
            "generated_at": generated_at,
            "repo_head": repo_head,
            "mode": mode,
        },
        "checks": checks,
        "orphans": kept_aside["orphan"],
        "conflicts": kept_aside["conflict"],
    }

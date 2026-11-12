"""Dev server for the generated review app. It serves rebuild/out/review/ with livereload on port 7294, as tools/serve.py serves site/ on port 7293, so the two can run at the same time.

The app saves its verdicts to the server through /autosave, and the server keeps the store in memory (rebuild.review.verdict_store). At boot the app GETs the whole store with a sync token, which the server streams a chunk of about 1 MiB at a time from the record lines it holds, flushing each, so it never joins the whole body (`stream_full_store`). After that it GETs `?since=<token>` to fetch only what other tabs changed, and after each debounced change it POSTs a delta of the verdicts it set or cleared. POSTs accept only deltas; a whole-store export gets a 400 with recovery instructions and changes nothing. The store's file is verdicts-autosave.json at the repo root, which the `/verdicts-*.json` pattern in .gitignore covers. It is kept outside rebuild/out/review/, which a pass's land replaces as a whole. The store answers for each unit through the accepted list beside it, rebuild/accepted-units.txt (`rebuild.review.accepted`, the units a close-out folded out of the store): the full GET carries an `accepted` field after the token, the list's sha256 and `closed_at` and, by verdict, the listed units of the served corpus that hold no record or tombstone, which the tab shows as accepted; every applied save's reply names the list's sha256 too, so a tab that loaded another list moves. At boot, with no store file beside a list, the server writes an empty store stamped for the served corpus (`VerdictStore.stamp_empty_store`), so a fresh clone, a worktree or a machine whose store was deleted shows its accepted units. Each delta POST is applied with the served corpus (`served_corpus`: the manifest's stamp and human unit ids), so a delta from a tab loaded on another corpus is carried onto the store by unit id when the store is on the served corpus, and its conflicts and orphans are kept in var/verdict-orphans/ and reported back to the tab (`verdict_store` gives the rules). A delta stamped for the served corpus while the store is on another stamp carries the store onto the served corpus by unit id, keeping the old file as verdicts-autosave-<stamp>.json; a delta made on neither corpus gets a retryable 503. The sets and clears of every accepted save are appended to verdicts-journal.ndjson (rebuild.review.journal) before the reply, so every verdict change can be replayed, including clears, which the store file records only as tombstones, and the save leaves the store file as it was: the server writes the whole store once `IDLE_WRITE_SECONDS` pass with no save, so at most once a minute (`IdleWrite`), and every other holder of the store's lock first writes into the file the journal's events past the file's last write (`landing.locked_store`), so each holder reads every save whether or not the server still runs. Each POST is applied under the verdict store's lock (rebuild.review.store_lock), which every writer of the store and the journal holds, and reads the served corpus under it, because a land swaps the corpus under the same lock. The server never waits for it, because a POST runs on the IOLoop and waiting would stall every other request: while another writer holds it, a POST gets a 503 with `Retry-After: 1`, and the idle write, which takes it the same way, tries again `IDLE_RETRY_SECONDS` later, or `IDLE_WRITE_SECONDS` later after a write that failed. The app keeps every unconfirmed save in its outbox in the browser's storage and retries a 503 with backoff, but the save a closing tab sends (the pagehide beacon) is retried only when the app is next opened, so a hand-run merge or restore refuses to write the live store while a server listens, and a hand-run re-key refuses to rewrite its store while the listening server's /capabilities answer names that store or names none, unless --yes is passed on the merge_verdicts or rekey_verdicts command line; a hand-run verdict update's merges refuse as a merge does, with no override, so the server must be stopped first. An artifact-cycle pass holds the lock beside a running server only for its catch-up at its start, its store snapshot, its land and its retention's journal tail, a few seconds each.

The server keeps running through every artifact-cycle pass. A pass builds its corpus beside the served one and runs the verdict update on the pass's own names for the store's file, which every writer replaces by rename rather than writing through, then lands the corpus and the store together under the lock (rebuild.review.landing). A land killed inside that section leaves its intent file (`landing.intent_path_for`); before it answers /autosave or /status, the server finishes such a land when the lock is free, in its executor, and any request to either made while a land still holds the lock gets the retryable 503, so no tab reads the new corpus beside the old store. While that recovery refuses (`landing.LandUnrecoverable`), every such request gets the refusal under the reason it names, a GET of /autosave as a 404 and the others as a 422, which the app shows the reader, keeping its outbox (`orphaned_land_reply`). At boot the server waits for the lock, finishing an interrupted land and then catching the store up with the journal (`landing.locked_store`, `boot_catch_up`, which prints a line when it applied saves), so a server the cycle stopped with saves still unwritten loses none of them, before it loads the store, and a recovery that refuses stops it with the refusal. /capabilities names the land protocol the server speaks, the digest of its protocol and freshness modules as it loaded them (`landing.code_digest`), its repo root and its store (`capabilities_payload`); the cycle keeps the server running only when that root is its own checkout and that digest is the working tree's, and restarts a server whose code is older.

Livereload reloads no tab on a file change: the server registers one watch that ignores its path (`register_dormant_watch`). The cycle tells open tabs what changed through livereload's /forcereload with an `ams:` path, `ams:corpus/<generated_at>` after a pass's land moved the served corpus and `ams:assets/<static hash>` after an assets refresh, and the app's livereload plugin (static/reload-plugin.js) hands it to the app, which saves, waits while the reader types, and reloads at the same URL hash. A tab also moves when a save's reply or /status names a corpus other than the one it loaded.

Usage: uv run python -m rebuild.review.serve
"""

import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from rebuild.review import accepted, app_index, journal, landing
from rebuild.review.store_lock import LockBusy, store_lock
from rebuild.review.verdict_store import (
    DELTA_FORMAT,
    EXPORT_FORMAT,
    ServedCorpus,
    VerdictStore,
    file_signature,
    parse_autosave_payload,
    stash_path_for,
)

__all__ = [
    "DELTA_FORMAT",
    "EXPORT_FORMAT",
    "IdleWrite",
    "ServedCorpus",
    "WRITE_BUSY",
    "WRITE_FAILED",
    "capabilities_payload",
    "parse_autosave_payload",
    "receive_autosave",
    "receive_autosave_locked",
    "refresh_store",
    "stash_path_for",
    "stream_full_store",
    "try_pending_write",
    "write_pending_store",
]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW_DIR = REPO_ROOT / "rebuild" / "out" / "review"
M1_OUT = REPO_ROOT / "rebuild" / "out" / "m1"
CYCLE_SUMMARY_PATH = REPO_ROOT / "rebuild" / "out" / "cycle_summary.json"
AUTOSAVE_PATH = REPO_ROOT / "verdicts-autosave.json"
JOURNAL_PATH = REPO_ROOT / journal.JOURNAL_NAME
ACCEPTED_PATH = accepted.path_for(AUTOSAVE_PATH)
NDJSON_SUFFIX = ".ndjson.gz"
PORT = 7294
STATUS_TTL_S = 10.0
RETRY_AFTER_S = 1
IDLE_WRITE_SECONDS = 60
IDLE_RETRY_SECONDS = 5
LAND_RUNNING = {
    "ok": False,
    "retry": True,
    "reason": "store-locked",
    "error": "a pass is moving the corpus and the verdict store into place; ask again shortly",
}


def static_headers_for(path: str) -> dict[str, str]:
    """Return the headers for a static file. Every file gets `Cache-Control: no-store`, because a rebuild reuses every file name. The precompressed `.ndjson.gz` sidecars are sent as gzip-encoded `application/x-ndjson`, so the browser decompresses them. The unit shards and the locator's rows file (`app_index.LOCATOR_ROWS_NAME`) are sent as the bytes on disk with no content encoding, because the app reads them by byte range. The app fetches one gzip member of the rows file at a time and decompresses it itself, and Chrome refuses a partial response that declares a content encoding."""
    headers = {"Cache-Control": "no-store"}
    if path.endswith(NDJSON_SUFFIX) and not path.endswith(app_index.LOCATOR_ROWS_NAME):
        headers["Content-Type"] = "application/x-ndjson"
        headers["Content-Encoding"] = "gzip"
    return headers


def manifest_signature(review_dir: Path) -> tuple[int, int, int] | None:
    """Return the served manifest's `file_signature`. A rebuild or an assets refresh replaces the file, so the signature moves with every restamp."""
    return file_signature(review_dir / "manifest.json")


def register_dormant_watch(server, review_dir: Path) -> None:
    """Register the one path livereload watches, with a filter that ignores it, so the server never reloads a tab on a file change. With no path registered, livereload watches the working directory, and a registered path fires a reload whatever its `delay` says (the watcher drops `delay="forever"` because it keeps only float delays). A tab reloads only when it chooses to, for an `ams:` path the cycle sends to livereload's /forcereload (`rebuild/review/static/reload.js`)."""
    server.watch(str(review_dir / "manifest.json"), ignore=lambda _path: True)


def capabilities_payload(code: str) -> dict:
    """Return what /capabilities answers: the land protocol this server speaks (`landing.LAND_PROTOCOL`), `code`, the digest of the protocol's modules and the freshness modules its /status runs, as they were when the server booted (`landing.code_digest`), the repo root it serves from, and its store's path. The artifact cycle keeps a listening server running through a pass only when the root is its own checkout and the digest matches the working tree's, and restarts it when only the digest differs."""
    return {
        "land_protocol": landing.LAND_PROTOCOL,
        "code": code,
        "root": str(REPO_ROOT),
        "autosave": str(AUTOSAVE_PATH),
    }


def receive_autosave(
    raw: bytes, path: Path, journal_path: Path | None = None, served: ServedCorpus | None = None
) -> tuple[int, dict]:
    """Apply one autosave POST body to the file at `path` through a store loaded for this call, and return the HTTP status and response body. A store loaded for one call has no idle to wait for, so it writes a save it journaled first before it returns, as the server's idle write would. The server itself keeps one store for its lifetime."""
    store = VerdictStore(path, journal_path)
    reply = store.receive(raw, served)
    if store.pending:
        store.write()
    return reply


def receive_autosave_locked(
    store: VerdictStore, raw: bytes, served: Callable[[], ServedCorpus | None] | None = None
) -> tuple[int, dict]:
    """Apply one POST body to `store` under the verdict store's lock, and return the HTTP status and response body. `served` reads the served corpus, and is called only once the lock is held: a land swaps the corpus under the same lock, so a corpus read before the lock could name the corpus a land has just replaced, and a save stamped for that corpus would carry the landed store back onto it (`verdict_store`, rule 3). With no `served` the served corpus is unknown. When another writer holds the lock, or a land's intent file is there once the lock is taken (a land died after the request's own check, `landing.intent_path_for`), nothing is applied and the answer is a 503 whose body says to retry; the handler adds `Retry-After`, and the retry's check finishes that land first."""
    try:
        with store_lock(store.path, blocking=False):
            if landing.intent_path_for(store.path).exists():
                return 503, {
                    "ok": False,
                    "retry": True,
                    "reason": "land-interrupted",
                    "error": "a pass's land stopped halfway; the server finishes it before the next save",
                }
            return store.receive(raw, served() if served is not None else None)
    except LockBusy:
        return 503, {
            "ok": False,
            "retry": True,
            "reason": "store-locked",
            "error": "the verdict store is locked by another writer; save again shortly",
        }


WRITE_BUSY = "busy"
WRITE_FAILED = "failed"


def write_pending_store(store: VerdictStore) -> bool:
    """Write the saves `store` journaled and has not written to its file (`try_pending_write`), and return whether nothing is left to write."""
    return try_pending_write(store) is None


def try_pending_write(store: VerdictStore) -> str | None:
    """Write the saves `store` journaled and has not written (`VerdictStore.pending`) to its file under the store's lock, taken without waiting, as a POST takes it, and return None once nothing is left to write, or why it wrote nothing, leaving the store pending for the next attempt: `WRITE_BUSY` while another writer holds the lock or a land's intent file is there (a land died, which the next request finishes first), and `WRITE_FAILED` when the write fails. Under the lock it first reloads a file another writer replaced (`refresh_if_changed`), which already holds every journaled save, and writes only a store still pending, through `VerdictStore.write`, a chunk of about 1 MiB at a time. It never reloads after a failed write, since memory holds saves the file lacks and the journal holds them all."""
    try:
        with store_lock(store.path, blocking=False):
            if landing.intent_path_for(store.path).exists():
                return WRITE_BUSY
            try:
                store.refresh_if_changed(locked=True)
                if store.pending:
                    store.write()
            except OSError:
                return WRITE_FAILED
            return None
    except LockBusy:
        return WRITE_BUSY


def boot_catch_up(
    store: Path, journal_path: Path | None, accepted_path: Path | None
) -> landing.CatchUp | None:
    """Catch the store up with its journal at the server's boot (`landing.catch_up`), under the lock the boot holds, and print what it did when it applied saves a server stopped before its idle write had journaled, or left the store as it stands for the marker (`landing.CatchUp.line`)."""
    caught = landing.catch_up(store, journal_path, accepted_path)
    line = caught.line() if caught is not None else None
    if line is not None:
        print(line, flush=True)
    return caught


def refresh_store(store: VerdictStore) -> None:
    """Reload `store` when its file or the accepted list's moved (`VerdictStore.refresh_if_changed`), for a request that does not hold the store's lock. A move of the list alone while the store is pending waits on the pending store's write (`VerdictStore.reload_waits`), so the request writes it under the lock first (`write_pending_store`), and while another writer holds the lock it is answered from the store as memory holds it."""
    store.refresh_if_changed()
    if store.reload_waits():
        write_pending_store(store)


class Timers(Protocol):
    """What `IdleWrite` schedules its write on: a Tornado IOLoop's `call_later` and `remove_timeout`."""

    def call_later(self, delay: float, callback: Callable[[], None]) -> object: ...

    def remove_timeout(self, timeout: object) -> None: ...


class IdleWrite:
    """The review server's idle write. After each POST that leaves the store pending (`saved`), it schedules one write `delay` seconds on and cancels the one before, so the store is written once `delay` seconds pass with no save, at most once a minute with `IDLE_WRITE_SECONDS`, and a burst of saves writes it once. The write (`try_pending_write`) takes the store's lock without waiting: while another writer holds it, or a land's intent file is there, which writes nothing, it tries again `retry` seconds later, `IDLE_RETRY_SECONDS` by default, and after a write that fails, which can have written most of a store before it failed, `delay` seconds later, so failed writes come no more often than the idle writes themselves. `loop` gives the timers, the server's IOLoop."""

    def __init__(
        self,
        store: VerdictStore,
        loop: Callable[[], Timers],
        *,
        delay: float = IDLE_WRITE_SECONDS,
        retry: float = IDLE_RETRY_SECONDS,
    ) -> None:
        self.store = store
        self.delay = delay
        self.retry = retry
        self._loop = loop
        self._timeout: object | None = None

    def saved(self) -> None:
        """Schedule the write `delay` seconds from now, in place of the one scheduled before."""
        self._schedule(self.delay)

    def _schedule(self, delay: float) -> None:
        loop = self._loop()
        if self._timeout is not None:
            loop.remove_timeout(self._timeout)
        self._timeout = loop.call_later(delay, self._fire)

    def _fire(self) -> None:
        self._timeout = None
        unwritten = try_pending_write(self.store)
        if unwritten is not None:
            self._schedule(self.delay if unwritten == WRITE_FAILED else self.retry)


def orphaned_land_reply(path: Path, refused_status: int) -> tuple[int, dict] | None:
    """Finish a land whose holder died before a request is answered (`landing.recover_if_orphaned`), and return None when the request may go on, or the status and body to answer it with instead: the retryable 503 (`LAND_RUNNING`) while a land still holds the store's lock, and `refused_status` with the recovery's refusal under the reason it names (`landing.LandUnrecoverable`) while the recovery refuses, which it does until a person acts on the file the refusal names, as it says."""
    try:
        if landing.recover_if_orphaned(path):
            return None
    except landing.LandUnrecoverable as refused:
        return refused_status, {"ok": False, "reason": refused.reason, "error": str(refused)}
    return 503, LAND_RUNNING


def missing_autosave_reply(store: VerdictStore) -> dict:
    """Return the body of the 404 a GET of /autosave gets while `store` holds no stamped store: its refusal under its reason (`VerdictStore.refusal_reason`) while one stands, `store-malformed` for a file holding a record `verdict_store.well_formed` refuses and `accepted-malformed` or `accepted-mismatch` for an accepted list it cannot read or that does not pair with the store, so the app tells the reader why it shows no verdict, and "no autosave yet" otherwise."""
    if store.refusal is not None:
        return {"ok": False, "reason": store.refusal_reason, "error": store.refusal}
    return {"ok": False, "error": "no autosave yet"}


class ChunkedReply(Protocol):
    """What `stream_full_store` writes a reply through: a request handler's `write` and `flush`."""

    def write(self, chunk: bytes) -> None: ...

    def flush(self) -> Awaitable[None]: ...


async def stream_full_store(reply: ChunkedReply, store: VerdictStore, within: frozenset[str] | None) -> None:
    """Write the full GET of /autosave, `store.payload_bytes(token=True, within=within)`, to `reply` a chunk of about 1 MiB at a time (`VerdictStore.payload_chunks`), flushing each before the next is built, so the server never joins the whole body. The first chunk, the head with its token, is built before the first `await`, and a save that lands between two chunks is tolerated as `payload_chunks` says, since the tab fetches what moved after that token."""
    for chunk in store.payload_chunks(token=True, within=within):
        reply.write(chunk)
        await reply.flush()


def served_stamp(review_dir: Path) -> str | None:
    """Return the served manifest's `generated_at`, or None when it cannot be read."""
    try:
        stamp = json.loads((review_dir / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        return None
    return stamp if isinstance(stamp, str) else None


def main() -> None:
    if not (REVIEW_DIR / "manifest.json").exists():
        raise SystemExit(
            f"{REVIEW_DIR} has no manifest.json — build it first: uv run python -m rebuild.review.build"
        )

    from livereload import Server
    from tornado.ioloop import IOLoop
    from tornado.iostream import StreamClosedError
    from tornado.web import RequestHandler, StaticFileHandler

    from rebuild.review import status

    code = landing.code_digest(Path(__file__).resolve().parent)
    try:
        with landing.locked_store(
            AUTOSAVE_PATH,
            journal_path=JOURNAL_PATH,
            accepted_path=ACCEPTED_PATH,
            catch_up_with=boot_catch_up,
        ):
            store = VerdictStore(AUTOSAVE_PATH, JOURNAL_PATH, accepted_path=ACCEPTED_PATH)
            stamp = served_stamp(REVIEW_DIR)
            written = store.stamp_empty_store(stamp) if stamp is not None else None
    except landing.LandUnrecoverable as refused:
        raise SystemExit(f"The review server did not start: {refused}") from None
    if written is not None:
        print(f"wrote an empty store stamped {stamp} beside {ACCEPTED_PATH.name}", flush=True)
        if "journal_error" in written:
            print(f"the journal append failed: {written['journal_error']}", flush=True)

    async def answered_for_land(handler: RequestHandler, refused_status: int) -> bool:
        """Finish a land whose holder died before this request is answered, in the executor so the IOLoop keeps serving, and return whether the request was answered instead (`orphaned_land_reply`): with the retryable 503 while a land still holds the store's lock, since it may already have swapped the corpus in and not yet replaced the store, and with `refused_status` and the refusal while the recovery refuses."""
        if not landing.intent_path_for(AUTOSAVE_PATH).exists():
            return False
        reply = await IOLoop.current().run_in_executor(
            None, orphaned_land_reply, AUTOSAVE_PATH, refused_status
        )
        if reply is None:
            return False
        status_code, body = reply
        handler.set_status(status_code)
        if status_code == 503:
            handler.set_header("Retry-After", str(RETRY_AFTER_S))
        handler.finish(body)
        return True

    class ManifestCache:
        signature: tuple[int, int, int] | None = None
        stamp: str | None = None
        human_ids: frozenset[str] | None = None

    manifest_cache = ManifestCache()

    def cached_human_ids() -> frozenset[str] | None:
        """Return the served corpus's human unit ids, reading the manifest only when its mtime, size or inode moved, and the ids only when its stamp moved too, so an assets refresh that restamps only the manifest's `static` component reloads nothing."""
        signature = manifest_signature(REVIEW_DIR)
        if signature is not None and signature == manifest_cache.signature:
            return manifest_cache.human_ids
        try:
            stamp = json.loads((REVIEW_DIR / "manifest.json").read_text()).get("generated_at")
        except OSError, ValueError:
            return None
        if manifest_cache.signature is None or stamp != manifest_cache.stamp:
            try:
                manifest_cache.human_ids = status.load_human_unit_ids(REVIEW_DIR)
            except OSError, ValueError, KeyError, TypeError:
                manifest_cache.human_ids = None
        manifest_cache.signature = signature
        manifest_cache.stamp = stamp
        return manifest_cache.human_ids

    def served_corpus() -> ServedCorpus | None:
        """Return the served manifest's stamp and human unit ids, or None when either cannot be read, which makes a save on another stamp wait (`verdict_store`, rule 4)."""
        human_ids = cached_human_ids()
        stamp = manifest_cache.stamp
        if human_ids is None or not isinstance(stamp, str):
            return None
        return ServedCorpus(stamp, human_ids)

    class NoCacheStaticHandler(StaticFileHandler):
        def set_extra_headers(self, path: str) -> None:
            for name, value in static_headers_for(path).items():
                self.set_header(name, value)

        def compute_etag(self) -> str | None:
            """Return no etag. Tornado's default computes a sha512 of the whole file, which for a shard of a quarter gigabyte runs on the first Range request the explain panel makes, and caches it in a class dict that a rebuild never clears. With `Cache-Control: no-store`, no client would use the etag."""
            return None

        def should_return_304(self) -> bool:
            """Always send the body. Otherwise a Range request whose `If-Modified-Since` is satisfied would get a 304 with no body, and the app would have nothing to parse."""
            return False

    # /status recomputes the input fingerprints to check that the corpus is current, and picks the fullest verdicts file (status.pick_fullest_verdicts memoizes each verdicts file by its stat). The app requests it on every focus, visibility change, and hash change. It runs in the executor so that the shard Range requests a card waits on are not blocked. Concurrent requests over the same manifest share one computation, and a result is reused for STATUS_TTL_S seconds unless the manifest's signature has moved since it was computed, so a rebuilt corpus shows at once and any other change on disk can lag by up to that long.
    class StatusCache:
        at: float = 0.0
        key: tuple[int, int, int] | None = None
        result: dict | None = None
        future: Awaitable[dict] | None = None
        future_key: tuple[int, int, int] | None = None

    status_cache = StatusCache()

    idle = IdleWrite(store, IOLoop.current, delay=IDLE_WRITE_SECONDS, retry=IDLE_RETRY_SECONDS)

    def status_inputs() -> dict:
        refresh_store(store)
        return {
            "human_ids": cached_human_ids(),
            "autosave": store.payload_dict() if store.stamp is not None else None,
            "listed": store.accepted,
        }

    def compute_status_now(inputs: dict) -> dict:
        return status.compute_status(
            REPO_ROOT,
            REVIEW_DIR,
            M1_OUT,
            AUTOSAVE_PATH,
            CYCLE_SUMMARY_PATH,
            human_ids=inputs["human_ids"],
            autosave=inputs["autosave"],
            listed=inputs["listed"] if inputs["autosave"] is not None else None,
        )

    class StatusHandler(RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Cache-Control", "no-store")

        async def get(self) -> None:
            if await answered_for_land(self, 422):
                return
            key = manifest_signature(REVIEW_DIR)
            result = status_cache.result
            if (
                result is None
                or status_cache.key != key
                or time.monotonic() - status_cache.at >= STATUS_TTL_S
            ):
                future = status_cache.future
                if future is None or status_cache.future_key != key:
                    future = IOLoop.current().run_in_executor(None, compute_status_now, status_inputs())
                    status_cache.future = future
                    status_cache.future_key = key
                try:
                    result = await future
                except Exception as exc:
                    self.set_status(500)
                    self.finish({"error": str(exc)})
                    return
                finally:
                    if status_cache.future is future:
                        status_cache.future = None
                status_cache.result = result
                status_cache.key = key
                status_cache.at = time.monotonic()
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps(result))

    class AutosaveHandler(RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Cache-Control", "no-store")

        async def get(self) -> None:
            if await answered_for_land(self, 404):
                return
            refresh_store(store)
            if store.stamp is None:
                self.set_status(404)
                self.finish(missing_autosave_reply(store))
                return
            self.set_header("Content-Type", "application/json")
            since = self.get_query_argument("since", None)
            changes = store.changes_since(since) if since is not None else None
            if changes is not None:
                self.finish(json.dumps(changes, ensure_ascii=False))
                return
            try:
                await stream_full_store(self, store, cached_human_ids())
            except StreamClosedError:
                return
            self.finish()

        async def post(self) -> None:
            if await answered_for_land(self, 422):
                return
            status_code, body = receive_autosave_locked(store, self.request.body, served_corpus)
            if store.pending:
                idle.saved()
            self.set_status(status_code)
            if status_code == 503:
                self.set_header("Retry-After", str(RETRY_AFTER_S))
            self.finish(body)

    class CapabilitiesHandler(RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Cache-Control", "no-store")

        def get(self) -> None:
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps(capabilities_payload(code)))

    class ReviewServer(Server):
        def get_web_handlers(self, script):
            return [
                (r"/status", StatusHandler),
                (r"/autosave", AutosaveHandler),
                (r"/capabilities", CapabilitiesHandler),
            ] + super().get_web_handlers(script)

    server = ReviewServer()
    server.SFH = NoCacheStaticHandler  # pyright: ignore[reportAttributeAccessIssue]
    register_dormant_watch(server, REVIEW_DIR)
    server.serve(
        root=str(REVIEW_DIR),
        port=PORT,
        open_url_delay=None,
        live_css=False,
    )


if __name__ == "__main__":
    main()

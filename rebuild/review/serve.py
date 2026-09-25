"""Dev server for the generated review app. It serves rebuild/out/review/ with livereload on port 7294, as tools/serve.py serves site/ on port 7293, so the two can run at the same time.

The app saves its verdicts to the server through /autosave, and the server keeps the store in memory (rebuild.review.verdict_store). At boot the app GETs the whole store with a sync token. After that it GETs `?since=<token>` to fetch only what other tabs changed, and after each debounced change it POSTs a delta of the verdicts it set or cleared. A whole-store POST is also accepted. The store's file is verdicts-autosave.json at the repo root, which the `/verdicts-*.json` pattern in .gitignore covers. It is kept outside rebuild/out/review/ because livereload watches the JSON files there and would reload the page on every save. When a save carries a newer manifest stamp than the file on disk, the old file is moved aside to verdicts-autosave-<stamp>.json. That file may be the only copy of unexported verdicts from before a surface rebuild, and its unit ids must not be applied to the new surface. A save with an older stamp gets a 409, so that a tab left open from before a rebuild cannot overwrite the newly merged store on its next flush or pagehide beacon. The sets and clears of every accepted save are appended to verdicts-journal.ndjson (rebuild.review.journal), so every verdict change can be replayed, including clears, which the store file cannot record.

Usage: uv run python -m rebuild.review.serve
"""

import json
import time
from collections.abc import Awaitable
from pathlib import Path

from rebuild.review import app_index, journal
from rebuild.review.verdict_store import (
    DELTA_FORMAT,
    EXPORT_FORMAT,
    VerdictStore,
    parse_autosave_payload,
    stash_path_for,
)

__all__ = ["DELTA_FORMAT", "EXPORT_FORMAT", "parse_autosave_payload", "receive_autosave", "stash_path_for"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW_DIR = REPO_ROOT / "rebuild" / "out" / "review"
M1_OUT = REPO_ROOT / "rebuild" / "out" / "m1"
CYCLE_SUMMARY_PATH = REPO_ROOT / "rebuild" / "out" / "cycle_summary.json"
AUTOSAVE_PATH = REPO_ROOT / "verdicts-autosave.json"
JOURNAL_PATH = REPO_ROOT / journal.JOURNAL_NAME
NDJSON_SUFFIX = ".ndjson.gz"
PORT = 7294
STATUS_TTL_S = 10.0


def static_headers_for(path: str) -> dict[str, str]:
    """Return the headers for a static file. Every file gets `Cache-Control: no-store`, because a rebuild reuses every file name. The precompressed `.ndjson.gz` sidecars are sent as gzip-encoded `application/x-ndjson`, so the browser decompresses them. The unit shards and the locator's rows file (`app_index.LOCATOR_ROWS_NAME`) are sent as the bytes on disk with no content encoding, because the app reads them by byte range. The app fetches one gzip member of the rows file at a time and decompresses it itself, and Chrome refuses a partial response that declares a content encoding."""
    headers = {"Cache-Control": "no-store"}
    if path.endswith(NDJSON_SUFFIX) and not path.endswith(app_index.LOCATOR_ROWS_NAME):
        headers["Content-Type"] = "application/x-ndjson"
        headers["Content-Encoding"] = "gzip"
    return headers


def receive_autosave(raw: bytes, path: Path, journal_path: Path | None = None) -> tuple[int, dict]:
    """Apply one autosave POST body to the file at `path` through a store loaded for this call, and return the HTTP status and response body. The server itself keeps one store for its lifetime."""
    return VerdictStore(path, journal_path).receive(raw)


def main() -> None:
    if not (REVIEW_DIR / "manifest.json").exists():
        raise SystemExit(
            f"{REVIEW_DIR} has no manifest.json — build it first: uv run python -m rebuild.review.build"
        )

    from livereload import Server
    from tornado.ioloop import IOLoop
    from tornado.web import RequestHandler, StaticFileHandler

    from rebuild.review import status

    store = VerdictStore(AUTOSAVE_PATH, JOURNAL_PATH)
    human_ids_cache: dict[str | None, frozenset[str] | None] = {}

    def cached_human_ids() -> frozenset[str] | None:
        try:
            stamp = json.loads((REVIEW_DIR / "manifest.json").read_text()).get("generated_at")
        except OSError, ValueError:
            return None
        if stamp not in human_ids_cache:
            try:
                human_ids_cache[stamp] = status.load_human_unit_ids(REVIEW_DIR)
            except OSError, ValueError, KeyError, TypeError:
                human_ids_cache[stamp] = None
        return human_ids_cache[stamp]

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

    # /status recomputes the input fingerprints to check that the surface is current, and picks the frontier (status.pick_frontier memoizes each verdicts file by its stat). The app requests it on every focus, visibility change, and hash change. It runs in the executor so that the shard Range requests a card waits on are not blocked. Concurrent requests share one computation, and a result is reused for STATUS_TTL_S seconds, so the status can lag a change on disk by up to that long.
    class StatusCache:
        at: float = 0.0
        result: dict | None = None
        future: Awaitable[dict] | None = None

    status_cache = StatusCache()

    def status_inputs() -> dict:
        store.refresh_if_changed()
        return {
            "human_ids": cached_human_ids(),
            "autosave": store.payload_dict() if store.stamp is not None else None,
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
        )

    class StatusHandler(RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Cache-Control", "no-store")

        async def get(self) -> None:
            result = status_cache.result
            if result is None or time.monotonic() - status_cache.at >= STATUS_TTL_S:
                future = status_cache.future
                if future is None:
                    future = IOLoop.current().run_in_executor(None, compute_status_now, status_inputs())
                    status_cache.future = future
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
                status_cache.at = time.monotonic()
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps(result))

    class AutosaveHandler(RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Cache-Control", "no-store")

        def get(self) -> None:
            store.refresh_if_changed()
            if store.stamp is None:
                self.set_status(404)
                self.finish({"ok": False, "error": "no autosave yet"})
                return
            self.set_header("Content-Type", "application/json")
            since = self.get_query_argument("since", None)
            changes = store.changes_since(since) if since is not None else None
            if changes is not None:
                self.finish(json.dumps(changes, ensure_ascii=False))
                return
            self.finish(store.payload_bytes(token=True))

        def post(self) -> None:
            status_code, body = store.receive(self.request.body)
            self.set_status(status_code)
            self.finish(body)

    class ReviewServer(Server):
        def get_web_handlers(self, script):
            return [
                (r"/status", StatusHandler),
                (r"/autosave", AutosaveHandler),
            ] + super().get_web_handlers(script)

    server = ReviewServer()
    server.SFH = NoCacheStaticHandler  # pyright: ignore[reportAttributeAccessIssue]
    server.watch(str(REVIEW_DIR / "**/*.html"))
    server.watch(str(REVIEW_DIR / "**/*.css"))
    server.watch(str(REVIEW_DIR / "**/*.js"))
    server.watch(str(REVIEW_DIR / "**/*.otf"))
    server.watch(str(REVIEW_DIR / "**/*.json"))
    server.serve(
        root=str(REVIEW_DIR),
        port=PORT,
        open_url_delay=None,
        live_css=False,
    )


if __name__ == "__main__":
    main()

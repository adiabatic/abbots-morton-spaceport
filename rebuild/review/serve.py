"""Dev server for the generated review app — a sibling of tools/serve.py over rebuild/out/review/ on port 7294, so it runs alongside the site server on 7293.

The app keeps its verdicts on the server through /autosave, so a reload or crash never loses in-progress blessing work. The server holds the store resident (rebuild.review.verdict_store) and speaks it in changes: the app POSTs a delta of the verdicts it set or cleared after every mutation, GETs the whole store once at boot along with a sync token, and thereafter GETs `?since=<token>` to pick up only what other sessions changed. A whole-store POST is accepted too. The file behind it lives at the repo root (not under rebuild/out/review/, where livereload's JSON watch would turn every save into a page reload) as verdicts-autosave.json, next to the exported masters and covered by the same gitignore pattern. When an incoming save carries a newer manifest generation than the file on disk, the old file is stashed aside as verdicts-autosave-<stamp>.json instead of being overwritten — a stale-manifest autosave is the only copy of un-exported work from before a surface rebuild, and its unit ids must never be silently joined to the new surface. The reverse direction is refused outright with a 409: a tab still open from before a rebuild would otherwise clobber the freshly merged store with its pre-rebuild copy on its next flush or pagehide beacon. Every accepted save is appended to verdicts-journal.ndjson (rebuild.review.journal) as the verdicts it set and cleared, so any verdict change — including clears, which the store files cannot represent — can be replayed and recovered.

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
    """The headers every static file goes out with. `Cache-Control: no-store` on everything, because a rebuild reuses every name; and the precompressed NDJSON sidecars beside the manifest go out as gzip-encoded `application/x-ndjson`, so the browser decompresses them on arrival while the file on disk keeps an honest name. The shards are deliberately not in that set: the app addresses them by byte range, which only means anything while they are served identity-encoded. The locator's rows file is out of it for the same reason — the app fetches one gzip member of it at a time by the span the locator table names, and Chrome refuses a partial response that declares a content encoding — so that file goes out as the bytes on disk and the app decompresses the member itself."""
    headers = {"Cache-Control": "no-store"}
    if path.endswith(NDJSON_SUFFIX) and not path.endswith(app_index.LOCATOR_ROWS_NAME):
        headers["Content-Type"] = "application/x-ndjson"
        headers["Content-Encoding"] = "gzip"
    return headers


def receive_autosave(raw: bytes, path: Path, journal_path: Path | None = None) -> tuple[int, dict]:
    """One save against the file at `path`, through a store read for the call: the shape the tests and any one-shot caller use, where the server itself keeps one store for its lifetime."""
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
            """No etag at all. Tornado's default hashes the whole file to compute one — a quarter-gigabyte shard sha512'd on the first Range request the explain panel makes, memoized in a class dict that no rebuild invalidates — and `Cache-Control: no-store` already means nothing would use the answer."""
            return None

        def should_return_304(self) -> bool:
            """A conditional Range request that satisfied `If-Modified-Since` would otherwise come back 304 with no body, and the app would have nothing to parse."""
            return False

    # The status is the freshness fingerprint over the review code plus a scan of every verdicts file at the root, seconds of CPU the app asks for on every focus and every hash change. It is computed off the loop, so the shard Range requests a card is waiting on go out while it runs, and one answer serves every request inside STATUS_TTL_S — nothing it reads moves between a cycle and the next, and a cycle restarts this server.
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

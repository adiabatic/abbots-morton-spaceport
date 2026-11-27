"""Dev server for the generated review app. It serves engine/out/review/ with livereload on port 7294, as tools/serve.py serves site/ on port 7293, so the two can run at the same time.

The app saves its verdicts to the server through /autosave, and the server keeps the store in memory (engine.review.verdict_store). At boot the app GETs the whole store with a sync token; after that it GETs `?since=<token>` to fetch only what other tabs changed, and after each debounced change it POSTs a delta of the verdicts it set or cleared. POSTs accept only deltas; a whole-store export gets a 400 with recovery instructions and changes nothing. The store's file is verdicts-autosave.json at the repo root, which the `/verdicts-*.json` pattern in .gitignore covers, kept outside engine/out/review/, which a pass's land replaces as a whole. Each delta POST is applied under the store's lock, taken without waiting (`receive_autosave_locked`), with the served corpus (`served_corpus`: the manifest's stamp and human unit ids) read once the lock is held, and a save that changes the store writes the file before the reply. A delta from a tab loaded on another corpus is carried onto the store by unit id when the store is on the served corpus, and its conflicts and orphans are kept in var/verdict-orphans/ and reported back to the tab; a delta stamped for the served corpus while the store is on another stamp carries the store onto the served corpus by unit id first; a delta made on neither corpus gets a retryable 503 (`verdict_store` gives the rules). A POST made while another writer holds the lock, the land or the accept, gets a retryable 503 too, which the tab sends again from its outbox.

The server keeps running through every artifact-cycle pass. A pass builds its corpus beside the served one and lands it, carrying the store onto it by unit id, in one short section under the store's lock (engine.review.landing), after which it tells the open tabs to move; every request first reloads the store when its file moved (`VerdictStore.refresh_if_changed`). /capabilities names the land protocol the server speaks, the digest of its protocol and freshness modules as it loaded them (`landing.code_digest`), its repo root and its store (`capabilities_payload`); the cycle keeps the server running only when that root is its own checkout and that digest is the working tree's, and restarts a server whose code is older.

The kerning view (`#view=kern`) reads `GET /kern/inventory` and `GET /kern` and writes the rune files under `RUNES_DIR` through `POST /kern` and `POST /kern/undo`, the routes of `engine.review.kern_routes`, which this module imports only inside `main`, so no tool that imports it for its store helpers reaches the kern stage.

The app's fonts come from /fonts/browser/before.otf and /fonts/browser/after.otf, not from the corpus's fonts/ copies: the server serves each corpus font with a private-use stand-in for the namer dot mapped beside the dot (`namer_dot_stand_in_font`), and the app writes the stand-in where a sample has the dot, so Chromium shapes a sample as one run. The corpus copies stay byte-identical to the fonts the units were built from.

Livereload reloads no tab on a file change: the server registers one watch that ignores its path (`register_dormant_watch`). The cycle and the accept tell open tabs what changed through livereload's /forcereload with an `ams:` path: `ams:corpus/<generated_at>` after a pass's land moved the served corpus, `ams:assets/<static hash>` after an assets refresh, and `ams:store/<snapshot digest>` after an accept emptied the store. The app's livereload plugin (static/reload-plugin.js) hands the path to the app, which saves, waits while the reader types, and reloads at the same URL hash. A tab also moves when a save's reply or /status names a corpus other than the one it loaded.

Usage: uv run python -m engine.review.serve
"""

import io
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from engine.review import app_index, landing
from engine.review.store_lock import LockBusy, store_lock
from engine.review.verdict_store import (
    AUTOSAVE_NAME,
    DELTA_FORMAT,
    EXPORT_FORMAT,
    ServedCorpus,
    VerdictStore,
    file_signature,
    parse_autosave_payload,
)

__all__ = [
    "DELTA_FORMAT",
    "EXPORT_FORMAT",
    "ServedCorpus",
    "capabilities_payload",
    "missing_autosave_reply",
    "namer_dot_stand_in_font",
    "parse_autosave_payload",
    "receive_autosave",
    "receive_autosave_locked",
]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW_DIR = REPO_ROOT / "engine" / "out" / "review"
BUILD_OUT = REPO_ROOT / "engine" / "out" / "build"
RUNES_DIR = REPO_ROOT / "glyph_data" / "runes"
CYCLE_SUMMARY_PATH = REPO_ROOT / "engine" / "out" / "cycle_summary.json"
AUTOSAVE_PATH = REPO_ROOT / AUTOSAVE_NAME
NDJSON_SUFFIX = ".ndjson.gz"
PORT = 7294
STATUS_TTL_S = 10.0
RETRY_AFTER_S = 1
NAMER_DOT = 0x00B7
NAMER_DOT_STAND_IN = 0xF8FF
BROWSER_FONT_ROUTE = r"/fonts/browser/(before|after)\.otf"
STORE_LOCKED = {
    "ok": False,
    "retry": True,
    "reason": "store-locked",
    "error": "the verdict store is locked by another writer; save again shortly",
}


def namer_dot_stand_in_font(source: Path) -> bytes:
    """Return the bytes of the font at `source` with `NAMER_DOT_STAND_IN` mapped, in every Unicode cmap subtable, to the glyph the namer dot (U+00B7) maps to.

    Chromium ends a shaping run at U+00B7, because its Script_Extensions (Latn, Grek, Geor, Hani) leave out the Unknown script of the Quikscript letters, so the dot is shaped in a run of its own that does not see the letter after it. Senior lowers the dot before a Short letter by a rule that reads that letter (`emit_gsub.namer_dot_lookup`), so a split run draws the dot unlowered where HarfBuzz and CoreText lower it. The app writes the stand-in where a sample has the dot (`namerDotStandIn` in static/render.js), and the stand-in, a private-use code point like the letters, keeps the sample in one run. GSUB and GPOS act on glyphs, so the stand-in shapes as the dot does.

    The font is read with a glyph order of placeholder names, one per glyph id, so the cmap compiles back to the same glyph ids without the `CFF ` table being read, and every table but `cmap` and `head`, which holds the checksum adjustment, is written back as the bytes it was read from. Raises ValueError when the font maps no glyph to the namer dot, or already maps the stand-in to another glyph.

    `test_the_namer_dots_stand_in_keeps_the_dot_lowering_that_reads_the_next_letter` in engine/test_review_autosave.py fails once the dot's form no longer depends on the letter after it, when the stand-in has nothing left to keep.
    """
    from fontTools.ttLib import TTFont

    font = TTFont(source, recalcBBoxes=False, recalcTimestamp=False)
    glyph_count = font["maxp"].numGlyphs  # pyright: ignore[reportAttributeAccessIssue]
    font.setGlyphOrder([f"gid{gid}" for gid in range(glyph_count)])
    glyph = (font.getBestCmap() or {}).get(NAMER_DOT)
    if glyph is None:
        raise ValueError(f"{source} maps no glyph to U+{NAMER_DOT:04X}")
    for table in font["cmap"].tables:  # pyright: ignore[reportAttributeAccessIssue]
        if not table.isUnicode() or table.format == 14:
            continue
        held = table.cmap.get(NAMER_DOT_STAND_IN)
        if held is not None and held != glyph:
            raise ValueError(
                f"{source} already maps U+{NAMER_DOT_STAND_IN:04X}, the namer dot's stand-in, to another glyph"
            )
        table.cmap[NAMER_DOT_STAND_IN] = glyph
    buffer = io.BytesIO()
    font.save(buffer)
    return buffer.getvalue()


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
    """Register the one path livereload watches, with a filter that ignores it, so the server never reloads a tab on a file change. With no path registered, livereload watches the working directory, and a registered path fires a reload whatever its `delay` says (the watcher drops `delay="forever"` because it keeps only float delays). A tab reloads only when it chooses to, for an `ams:` path sent to livereload's /forcereload (`engine/review/static/reload.js`)."""
    server.watch(str(review_dir / "manifest.json"), ignore=lambda _path: True)


def capabilities_payload(code: str) -> dict:
    """Return what /capabilities answers: the land protocol this server speaks (`landing.LAND_PROTOCOL`), `code`, the digest of the protocol's modules and the freshness modules its /status runs, as they were when the server booted (`landing.code_digest`), the repo root it serves from, and its store's path. The artifact cycle keeps a listening server running through a pass only when the root is its own checkout and the digest matches the working tree's, and restarts it when only the digest differs."""
    return {
        "land_protocol": landing.LAND_PROTOCOL,
        "code": code,
        "root": str(REPO_ROOT),
        "autosave": str(AUTOSAVE_PATH),
    }


def receive_autosave(raw: bytes, path: Path, served: ServedCorpus | None = None) -> tuple[int, dict]:
    """Apply one autosave POST body to the file at `path` through a store loaded for this call, and return the HTTP status and response body. The server itself keeps one store for its lifetime."""
    return VerdictStore(path).receive(raw, served)


def receive_autosave_locked(
    store: VerdictStore, raw: bytes, served: Callable[[], ServedCorpus | None] | None = None
) -> tuple[int, dict]:
    """Apply one POST body to `store` under the verdict store's lock, taken without waiting, and return the HTTP status and response body. `served` reads the served corpus, and is called only once the lock is held: a land swaps the corpus under the same lock, so a corpus read before the lock could name the corpus a land has just replaced, and a save stamped for that corpus would carry the landed store back onto it (`verdict_store`, rule 3). With no `served` the served corpus is unknown. When another writer holds the lock, nothing is applied and the answer is a 503 whose body says to retry (`STORE_LOCKED`); the handler adds `Retry-After`."""
    try:
        with store_lock(store.path, blocking=False):
            return store.receive(raw, served() if served is not None else None)
    except LockBusy:
        return 503, dict(STORE_LOCKED)


def missing_autosave_reply(store: VerdictStore) -> dict:
    """Return the body of the 404 a GET of /autosave gets while `store` holds no stamped store: its refusal under `store-malformed` while the file holds a record `verdict_store.well_formed` refuses, so the app tells the reader why it shows no verdict, and "no autosave yet" otherwise."""
    if store.refusal is not None:
        return {"ok": False, "reason": store.refusal_reason, "error": store.refusal}
    return {"ok": False, "error": "no autosave yet"}


def main() -> None:
    if not (REVIEW_DIR / "manifest.json").exists():
        raise SystemExit(f"{REVIEW_DIR} has no manifest.json — build it first: make artifact-cycle")

    from livereload import Server
    from tornado.ioloop import IOLoop
    from tornado.web import HTTPError, RequestHandler, StaticFileHandler

    from engine.review import kern_routes, status

    code = landing.code_digest(Path(__file__).resolve().parent)
    store = VerdictStore(AUTOSAVE_PATH)

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
            """Return no etag. Tornado's default computes a sha512 of the whole file, which for a large shard runs on the first Range request the explain panel makes, and caches it in a class dict that a rebuild never clears. With `Cache-Control: no-store`, no client would use the etag."""
            return None

        def should_return_304(self) -> bool:
            """Always send the body. Otherwise a Range request whose `If-Modified-Since` is satisfied would get a 304 with no body, and the app would have nothing to parse."""
            return False

    # /status recomputes the input fingerprints to check that the corpus is current. The app requests it on every focus, visibility change, and hash change. It runs in the executor so that the shard Range requests a card waits on are not blocked. Concurrent requests over the same manifest share one computation, and a result is reused for STATUS_TTL_S seconds unless the manifest's signature has moved since it was computed, so a rebuilt corpus shows at once and any other change on disk can lag by up to that long.
    class StatusCache:
        at: float = 0.0
        key: tuple[int, int, int] | None = None
        result: dict | None = None
        future: Awaitable[dict] | None = None
        future_key: tuple[int, int, int] | None = None

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
            BUILD_OUT,
            AUTOSAVE_PATH,
            CYCLE_SUMMARY_PATH,
            human_ids=inputs["human_ids"],
            autosave=inputs["autosave"],
        )

    class StatusHandler(RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Cache-Control", "no-store")

        async def get(self) -> None:
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

        def get(self) -> None:
            store.refresh_if_changed()
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
            self.finish(store.payload_bytes(token=True))

        def post(self) -> None:
            status_code, body = receive_autosave_locked(store, self.request.body, served_corpus)
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

    browser_fonts: dict[str, tuple[tuple[int, int, int], bytes]] = {}
    kern = kern_routes.KernRoutes(REPO_ROOT, BUILD_OUT, runes_dir=RUNES_DIR)

    class BrowserFontHandler(RequestHandler):
        """Serve the corpus's fonts/<side>.otf with the namer dot's stand-in mapped (`namer_dot_stand_in_font`). The bytes are derived in the executor once per font file, keyed by its inode, mtime and size, so the font a land moves into place replaces the cached one."""

        def set_default_headers(self) -> None:
            self.set_header("Cache-Control", "no-store")

        async def get(self, side: str) -> None:
            source = REVIEW_DIR / "fonts" / f"{side}.otf"
            try:
                stat = source.stat()
            except FileNotFoundError:
                raise HTTPError(404) from None
            key = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            cached = browser_fonts.get(side)
            if cached is None or cached[0] != key:
                body = await IOLoop.current().run_in_executor(None, namer_dot_stand_in_font, source)
                cached = browser_fonts[side] = (key, body)
            self.set_header("Content-Type", "font/otf")
            self.finish(cached[1])

    class ReviewServer(Server):
        def get_web_handlers(self, script):
            return [
                (r"/status", StatusHandler),
                (r"/autosave", AutosaveHandler),
                (r"/capabilities", CapabilitiesHandler),
                *kern.handlers(),
                (BROWSER_FONT_ROUTE, BrowserFontHandler),
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

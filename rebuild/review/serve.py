"""Dev server for the generated review app — a sibling of tools/serve.py over rebuild/out/review/ on port 7294, so it runs alongside the site server on 7293.

The app POSTs its verdict store to /autosave after every mutation and restores it on load, so a reload or crash never loses in-progress blessing work. The autosave lives at the repo root (not under rebuild/out/review/, where livereload's JSON watch would turn every save into a page reload) as verdicts-autosave.json, next to the exported masters and covered by the same gitignore pattern. When an incoming save carries a different manifest generation than the file on disk, the old file is stashed aside as verdicts-autosave-<stamp>.json instead of being overwritten — a stale-manifest autosave is the only copy of un-exported work from before a surface rebuild, and its unit ids must never be silently joined to the new surface.

Usage: uv run python -m rebuild.review.serve
"""

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW_DIR = REPO_ROOT / "rebuild" / "out" / "review"
AUTOSAVE_PATH = REPO_ROOT / "verdicts-autosave.json"
EXPORT_FORMAT = "ams-review-verdicts/1"
PORT = 7294


def parse_autosave_payload(raw: bytes) -> dict | None:
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("format") != EXPORT_FORMAT:
        return None
    if not isinstance(data.get("manifest_generated_at"), str):
        return None
    if not isinstance(data.get("verdicts"), list):
        return None
    return data


def stash_path_for(path: Path, stamp: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ".-" else "." for c in stamp)
    return path.with_name(f"{path.stem}-{safe}{path.suffix}")


def receive_autosave(raw: bytes, path: Path) -> tuple[int, dict]:
    data = parse_autosave_payload(raw)
    if data is None:
        return 400, {"ok": False, "error": f"not an {EXPORT_FORMAT} document"}
    stashed = None
    if path.exists():
        existing = parse_autosave_payload(path.read_bytes())
        if existing is not None and existing["manifest_generated_at"] != data["manifest_generated_at"]:
            stash = stash_path_for(path, existing["manifest_generated_at"])
            os.replace(path, stash)
            stashed = stash.name
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)
    return 200, {"ok": True, "saved": len(data["verdicts"]), "stashed": stashed}


def main() -> None:
    if not (REVIEW_DIR / "manifest.json").exists():
        raise SystemExit(
            f"{REVIEW_DIR} has no manifest.json — build it first: uv run python -m rebuild.review.build"
        )

    from livereload import Server
    from tornado.web import RequestHandler, StaticFileHandler

    class NoCacheStaticHandler(StaticFileHandler):
        def set_extra_headers(self, path: str) -> None:
            self.set_header("Cache-Control", "no-store")

    class AutosaveHandler(RequestHandler):
        def set_default_headers(self) -> None:
            self.set_header("Cache-Control", "no-store")

        def get(self) -> None:
            if not AUTOSAVE_PATH.exists():
                self.set_status(404)
                self.finish({"ok": False, "error": "no autosave yet"})
                return
            self.set_header("Content-Type", "application/json")
            self.finish(AUTOSAVE_PATH.read_bytes())

        def post(self) -> None:
            status, body = receive_autosave(self.request.body, AUTOSAVE_PATH)
            self.set_status(status)
            self.finish(body)

    class ReviewServer(Server):
        def get_web_handlers(self, script):
            return [(r"/autosave", AutosaveHandler)] + super().get_web_handlers(script)

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

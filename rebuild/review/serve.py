"""Dev server for the generated review app — a sibling of tools/serve.py over rebuild/out/review/ on port 7294, so it runs alongside the site server on 7293.

Usage: uv run python -m rebuild.review.serve
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW_DIR = REPO_ROOT / "rebuild" / "out" / "review"
PORT = 7294


def main() -> None:
    if not (REVIEW_DIR / "manifest.json").exists():
        raise SystemExit(
            f"{REVIEW_DIR} has no manifest.json — build it first: uv run python -m rebuild.review.build"
        )

    from livereload import Server
    from tornado.web import StaticFileHandler

    class NoCacheStaticHandler(StaticFileHandler):
        def set_extra_headers(self, path: str) -> None:
            self.set_header("Cache-Control", "no-store")

    server = Server()
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

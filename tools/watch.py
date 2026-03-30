#!/usr/bin/env python3
"""Dev server with live reload — replaces browser-sync.

browser-sync's script injection corrupts multi-byte UTF-8 characters
when they straddle a 64 KB chunk boundary. livereload serves files
byte-for-byte and injects its reload script without this problem.
"""

from pathlib import Path

from livereload import Server
from tornado.web import StaticFileHandler

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"
PORT = 7293


class NoCacheStaticHandler(StaticFileHandler):
    def set_extra_headers(self, path):
        self.set_header("Cache-Control", "no-store")


server = Server()
server.watch(str(TEST_DIR / "*.html"))
server.watch(str(TEST_DIR / "*.css"))
server.watch(str(TEST_DIR / "*.otf"))
server.watch(str(TEST_DIR / "*.js"))
server.serve(
    root=str(TEST_DIR),
    port=PORT,
    open_url_delay=None,
    live_css=False,
    handler=NoCacheStaticHandler,
)

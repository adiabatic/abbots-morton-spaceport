"""Check whether the review server is listening on its port. The cycle driver checks before it rewrites the surface or stops the server, merge_verdicts checks before it writes the store, and verdict-ready reports it in its checklist. The check is a separate module because merge_verdicts runs inside the verdict chain, whose green key hashes the chain's import closure. Importing the check from the driver would put the driver, the timings journal, and the memory-budget and peak-RSS modules into that closure, so an edit to any of them, which cannot change a verdict, would re-run the whole chain. `PLUMBING_TOOL_MODULES` in rebuild/tools/artifact_cycle.py lists that closure, and rebuild/test_plumbing_closure.py checks the list against the import graph."""

from __future__ import annotations

import socket

from rebuild.review.serve import PORT as REVIEW_PORT


def server_listening(port: int = REVIEW_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0

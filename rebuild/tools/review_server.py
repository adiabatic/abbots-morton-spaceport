"""Whether the review server is up on its port: the cycle driver asks before it rewrites the surface or stops the server, merge_verdicts asks before it writes the store, and verdict-ready asks so its checklist can say the letters are actually on screen. It is its own module because merge_verdicts runs inside the verdict chain, whose green key hashes the chain's import closure — reaching this probe through the driver put the driver, the timings journal and the width yardsticks inside that closure, so a telemetry or width edit that can never move a verdict re-ran the whole chain. `PLUMBING_TOOL_MODULES` in rebuild/tools/artifact_cycle.py is that closure's roster and rebuild/test_plumbing_closure.py is what holds it to the walked import graph."""

from __future__ import annotations

import socket

REVIEW_PORT = 7294


def server_listening(port: int = REVIEW_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0

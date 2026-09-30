"""Check whether the review server is listening on its port, ask it what it is (`capabilities`), and ask it to tell its open tabs to reload. The cycle driver checks before a pass and reads /capabilities to decide whether the server can keep running through it, merge_verdicts checks before it writes the store, and verdict-ready reports it in its checklist. The cycle sends the reload (`force_reload`) after it changes what the server serves. The check is a separate module because merge_verdicts runs inside the verdict update, whose green key hashes the verdict update's import closure. Importing the check from the driver would put the driver, the timings journal, and the memory-budget and peak-RSS modules into that closure, so an edit to any of them, which cannot change a verdict, would re-run the whole verdict update. `VERDICT_UPDATE_TOOL_MODULES` in rebuild/tools/artifact_cycle.py lists that closure, and rebuild/test_verdict_update_closure.py checks the list against the import graph."""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from rebuild.review.serve import PORT as REVIEW_PORT

FORCE_RELOAD_TIMEOUT_S = 2.0
CAPABILITIES_TIMEOUT_S = 2.0
NO_CAPABILITIES = "no-capabilities"


def server_listening(port: int = REVIEW_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def force_reload(path: str, port: int = REVIEW_PORT) -> bool:
    """Send `path` to every tab connected to the review server's livereload socket, through livereload's /forcereload, and return whether the server took it. The app's plugin acts on `ams:corpus/<generated_at>` and `ams:assets/<static hash>` (rebuild/review/static/reload.js). No server listening, or any failure, returns False: the tabs then find the change on their next save or status check."""
    url = f"http://127.0.0.1:{port}/forcereload?{urllib.parse.urlencode({'path': path})}"
    try:
        with urllib.request.urlopen(url, timeout=FORCE_RELOAD_TIMEOUT_S) as response:
            return response.status == 200
    except OSError, ValueError, http.client.HTTPException:
        return False


def capabilities(port: int = REVIEW_PORT) -> dict | str | None:
    """Return the review server's /capabilities answer (`rebuild.review.serve.capabilities_payload`); `NO_CAPABILITIES` when the server answers 404 there, as a server that predates the land protocol does; or None when no answer came: no server, a timeout, another error status, or a body that is not a JSON object."""
    url = f"http://127.0.0.1:{port}/capabilities"
    try:
        with urllib.request.urlopen(url, timeout=CAPABILITIES_TIMEOUT_S) as response:
            answer = json.loads(response.read())
    except urllib.error.HTTPError as error:
        error.close()
        return NO_CAPABILITIES if error.code == 404 else None
    except OSError, ValueError, http.client.HTTPException:
        return None
    return answer if isinstance(answer, dict) else None

import re

_MARKER = re.compile(r"\s*(\[(?:carried|echo-fill|echo-harmonize|bulk)\b[^\]]*\])")


def cap_markers(note, keep=2):
    """Keep only the newest `keep` machine-provenance markers at the head of a note, dropping the older tail while preserving any human prose. Markers are the bracketed `[carried ...]` / `[echo-fill ...]` / `[echo-harmonize ...]` / `[bulk: ...]` segments the review producers prepend, newest first; a leading bracket that is not one of those kinds (a human note that happens to start with `[...]`) is left untouched. Idempotent."""
    markers = []
    pos = 0
    while (match := _MARKER.match(note, pos)) is not None:
        markers.append(match.group(1))
        pos = match.end()
    prose = note[pos:].strip()
    return " ".join(markers[:keep] + ([prose] if prose else []))

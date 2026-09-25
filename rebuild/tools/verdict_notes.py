import re

_MARKER = re.compile(r"\s*(\[(?:carried|echo-fill|echo-harmonize|bulk|parked|standing)\b[^\]]*\])")


def cap_markers(note, keep=2):
    """Return the note with only its first `keep` provenance markers and its human prose. A marker is a leading bracketed segment of a kind `_MARKER` lists. The tools that write verdicts prepend them, so the newest comes first. A leading bracket of any other kind is treated as prose. The function is idempotent."""
    markers = []
    pos = 0
    while (match := _MARKER.match(note, pos)) is not None:
        markers.append(match.group(1))
        pos = match.end()
    prose = note[pos:].strip()
    return " ".join(markers[:keep] + ([prose] if prose else []))


def strip_markers(note):
    """Return the note's human prose with every leading provenance marker removed, as the review app's `stripCarriedProvenance` in rebuild/review/static/verdicts.js does."""
    pos = 0
    while (match := _MARKER.match(note, pos)) is not None:
        pos = match.end()
    return note[pos:].strip()

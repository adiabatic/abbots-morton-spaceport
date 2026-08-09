"""pytest plugin: count hb.shape invocations (lru_cache misses) and lookups during a run, and print the tally at the end."""

import time

import quikscript_shaping_helpers as helpers

STATS = {"hb_calls": 0, "hb_s": 0.0, "lookups": 0}
_original_hb_shape = helpers.hb.shape


def _counting(*args, **kwargs):
    begin = time.perf_counter()
    out = _original_hb_shape(*args, **kwargs)
    STATS["hb_s"] += time.perf_counter() - begin
    STATS["hb_calls"] += 1
    return out


helpers.hb.shape = _counting

_original_shape = helpers._shape


def _counting_lookup(text):
    STATS["lookups"] += 1
    return _original_shape(text)


def pytest_configure(config):
    import test_calt_regressions as mod

    mod._shape = _counting_lookup


def pytest_sessionfinish(session, exitstatus):
    print(
        f"\n[shapecount] hb.shape calls={STATS['hb_calls']} hb_seconds={STATS['hb_s']:.3f} _shape lookups={STATS['lookups']}"
    )

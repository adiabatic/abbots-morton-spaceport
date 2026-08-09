"""Injected into every Python process (parent and ProcessPoolExecutor workers) via PYTHONPATH, so a lever can be A/B'd against the real command line without editing a tracked file. Controlled by AMS_LEVERS."""

import os

_levers = {x for x in os.environ.get("AMS_LEVERS", "").split(",") if x}

if "cyaml" in _levers:
    import yaml

    _loader = yaml.CSafeLoader
    yaml.safe_load = lambda stream: yaml.load(stream, _loader)
    yaml.safe_load_all = lambda stream: yaml.load_all(stream, _loader)
    yaml.SafeLoader = _loader

if "gcoff" in _levers:
    import gc

    gc.disable()

if "gcfreeze" in _levers:
    import gc

    gc.freeze()

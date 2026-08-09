"""pytest plugin: put a compiled kernel tree ahead of the repo before anything imports rebuild.pipeline.

Loaded as `-p preload_compiled` with AMS_COMPILED_TREE naming the tree. It imports the four kernel modules from that tree so every later import in the suite binds the compiled objects.
"""

import os
import sys

_tree = os.environ.get("AMS_COMPILED_TREE")
if _tree:
    sys.path.insert(0, _tree)
    import rebuild.pipeline.model  # noqa: F401
    import rebuild.pipeline.settle  # noqa: F401
    import rebuild.pipeline.specificity  # noqa: F401
    import rebuild.pipeline.table  # noqa: F401

    sys.stderr.write(
        "[preload] settle=%s table=%s model=%s\n"
        % (
            rebuild.pipeline.settle.__file__,
            rebuild.pipeline.table.__file__,
            rebuild.pipeline.model.__file__,
        )
    )

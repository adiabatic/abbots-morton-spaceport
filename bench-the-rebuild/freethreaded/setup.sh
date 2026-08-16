#!/bin/zsh
# Build the two interpreters this experiment compares, into this directory only.
#
# Nothing here touches the repo's pinned Python, .venv, pyproject.toml or uv.lock: `uv python
# install` writes to uv's managed store under ~/.local/share/uv/python, and both venvs live beside
# this script. Re-running is a no-op once they exist.
set -e
set -u

HERE="${0:A:h}"
REPO="${HERE:h:h}"
export UV_CACHE_DIR="$REPO/.uv-cache"

uv python install 3.14.6+freethreaded 3.14.6

uv venv --python 3.14.6+freethreaded "$HERE/venv-ft"
uv venv --python cpython-3.14.6-macos-aarch64-none "$HERE/venv-gil"

# PyYAML is the settlement kernel's only third-party import. 6.0.3 ships a cp314t wheel, so the
# free-threaded venv gets the same C loader the GIL venv does — no pure-Python fallback needed.
uv pip install --python "$HERE/venv-ft/bin/python" pyyaml
uv pip install --python "$HERE/venv-gil/bin/python" pyyaml

# A third venv carrying the whole repo dependency set, so the runner can report what adopting 3.14t
# would mean for the real driver rather than only for the kernel slice. uharfbuzz is expected to
# fail here; that failure is a finding, so the install is allowed to fail without stopping setup.
uv venv --python 3.14.6+freethreaded "$HERE/venv-full"
uv pip install --python "$HERE/venv-full/bin/python" 'fonttools>=4.61.1' 'pyyaml>=6.0.3' \
  'pytest>=8.0.0' 'pytest-xdist>=3.6.1'
uv pip install --python "$HERE/venv-full/bin/python" 'uharfbuzz>=0.43.0' || \
  print -u2 -- "[setup] uharfbuzz does not build on 3.14t — expected, and reported by run.sh"

"$HERE/venv-ft/bin/python" -c 'import sys; print(sys.version); print("gil_enabled =", sys._is_gil_enabled())'

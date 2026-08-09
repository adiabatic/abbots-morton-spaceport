#!/bin/zsh
set -e
ROOT="${0:A:h:h:h}"
BASE="$ROOT/bench-the-rebuild/levers"
NAME="$1"; shift
V="$BASE/v-$NAME"
rm -rf "$V"; mkdir -p "$V"
rsync -a --exclude 'out/' --exclude 'evidence/' --exclude '__pycache__/' "$ROOT/rebuild" "$V/"
ln -s "$ROOT/rebuild/out" "$V/rebuild/out"
ln -s "$ROOT/rebuild/evidence" "$V/rebuild/evidence"
for d in glyph_data tools site doc test; do ln -s "$ROOT/$d" "$V/$d"; done
ln -s "$ROOT/pyproject.toml" "$V/pyproject.toml"
uv run --project "$ROOT" python "$BASE/apply_m1_patches.py" "$V" "$@"
echo "$V"

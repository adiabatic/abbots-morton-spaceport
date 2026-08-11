#!/bin/zsh
set -e
ROOT="${0:A:h:h:h}"
BASE="$ROOT/bench-the-rebuild/levers"
NAME="$1"
REF="$2"
if [[ -z "$NAME" || -z "$REF" ]]; then print -u2 "usage: mktree_at.sh <name> <ref>"; exit 2; fi
T="$BASE/tree-$NAME"
rm -rf "$T"; mkdir -p "$T"
rsync -a --exclude 'out/' --exclude 'evidence/' --exclude '__pycache__/' "$ROOT/rebuild" "$T/"
ln -s "$ROOT/rebuild/out" "$T/rebuild/out"
ln -s "$ROOT/rebuild/evidence" "$T/rebuild/evidence"
for d in glyph_data tools site doc test; do ln -s "$ROOT/$d" "$T/$d"; done
ln -s "$ROOT/pyproject.toml" "$T/pyproject.toml"
rm -rf "$T/rebuild/pipeline"
# `git archive` reads the ref's tree straight out of the object store; `git checkout <ref> -- rebuild/pipeline` would stage it into the real index and working tree.
git -C "$ROOT" archive "$REF" rebuild/pipeline | tar -x -C "$T"
echo "$T"

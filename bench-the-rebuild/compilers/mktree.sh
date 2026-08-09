#!/bin/zsh
# Build a writable mirror of the kernel's import closure under bench-the-rebuild/compilers/<name>.
set -e
REPO="${0:A:h:h:h}"
BASE="$REPO/bench-the-rebuild/compilers"
NAME="$1"
DEST="$BASE/$NAME"
rm -rf "$DEST"
mkdir -p "$DEST/rebuild"
cp -R "$REPO/rebuild/pipeline" "$DEST/rebuild/pipeline"
rm -rf "$DEST/rebuild/pipeline/__pycache__"
ln -s "$REPO/rebuild/schema" "$DEST/rebuild/schema"
ln -s "$REPO/rebuild/script.yaml" "$DEST/rebuild/script.yaml"
ln -s "$REPO/glyph_data" "$DEST/glyph_data"
echo "$DEST"

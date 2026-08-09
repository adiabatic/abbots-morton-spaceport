#!/bin/zsh
# Interleaved A/B of `tools/build_font.py` (the whole of `make all` bar the 0.09 s typst step)
# under a lever set, writing the fonts to a scratch dir and hashing them.
set -e
ROOT="${0:A:h:h:h}"
BASE="$ROOT/bench-the-rebuild/levers"
REPS=${REPS:-3}
cd "$ROOT"
for rep in $(seq 1 $REPS); do
  if (( rep % 2 == 0 )); then arms=("cyaml,gcoff" "gcoff" "cyaml" ""); else arms=("" "cyaml" "gcoff" "cyaml,gcoff"); fi
  for arm in $arms; do
    out="$BASE/fontout/ab"
    rm -rf "$out"; mkdir -p "$out"
    start=$(uv run python -c "import time;print(time.perf_counter())")
    AMS_LEVERS="$arm" PYTHONPATH="$BASE/levers_site" \
      /usr/bin/time -p uv run python tools/build_font.py glyph_data/ "$out/" > /dev/null 2> "$BASE/fontout/t.txt"
    real=$(awk '/^real/{print $2}' "$BASE/fontout/t.txt")
    user=$(awk '/^user/{print $2}' "$BASE/fontout/t.txt")
    sysv=$(awk '/^sys/{print $2}' "$BASE/fontout/t.txt")
    dig=$(cat "$out"/*.otf "$out"/*.fea 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
    print "{\"rep\": $rep, \"levers\": \"${arm:-none}\", \"wall\": $real, \"cpu\": $(print "$user + $sysv" | bc), \"digest\": \"$dig\"}"
  done
done

#!/bin/zsh
# Price the CUT-THE-WORK levers: gate:conform's --conform-horizon, and max_chars_after in the calt sweep.
#
# Everything runs against an isolated (spec@HEAD, six decision tables, M1.otf) triple under
# $HARNESS/m1. Nothing under rebuild/out is read as input or written. No verdict store, journal,
# review surface, or tracked file is touched.
#
# Variants (this harness prices work avoidance, not a language change — there is no Rust/Go port here):
#   conform-h3-cold, conform-h4-cold, conform-h5-cold, conform-h5-warm
#   calt-shard-after2      one shard of one (2,2) sweep, timed, with an exact shaped-string count
#   calt-17-sweeps         all 17 sharded (2,2) sweeps under xdist, timed
#   depth2-states          how many pair renderings only a depth-2 surround can reach
#
# Env:
#   FULL=1   also rebuild the isolated M1 triple first (adds ~400 s and 8.4 GB peak RSS)
#   SKIP_CALT17=1  skip the 250 s xdist run
#
# Expected runtime with the triple already built: ~22 minutes. With FULL=1: ~29 minutes.

set -e
set -u

REPO=${REPO:-${0:A:h:h:h}}
HARNESS="$REPO/bench-the-rebuild/cut-the-work"
OUT="$HARNESS/results"
mkdir -p "$OUT"
cd "$REPO"

if [[ "${FULL:-0}" == "1" || ! -f "$HARNESS/m1/M1.otf" ]]; then
  PYTHONPATH="$REPO" uv run python "$HARNESS/build_isolated.py" "$HARNESS/m1" > "$OUT/build.log" 2>&1
fi

for h in 3 4 5; do
  rm -rf "$HARNESS/run-h$h"
  SWEEP_JSON="$OUT/conform-h$h-cold.json" PYTHONPATH="$REPO" \
    uv run python "$HARNESS/sweep_horizon.py" "$HARNESS/m1" "$h" "$HARNESS/run-h$h" boundary-green \
    > "$OUT/conform-h$h-cold.log" 2>&1
done

# Same horizon again over the witnesses the cold pass just recorded: the steady-state / ink-only-edit case.
SWEEP_JSON="$OUT/conform-h5-warm.json" PYTHONPATH="$REPO" \
  uv run python "$HARNESS/sweep_horizon.py" "$HARNESS/m1" 5 "$HARNESS/run-h5" boundary-green \
  > "$OUT/conform-h5-warm.log" 2>&1

# Horizon 5 with the boundary gate's proven horizon withheld: prices proven_boundary_horizon.
rm -rf "$HARNESS/run-h5-nb"
SWEEP_JSON="$OUT/conform-h5-noboundary.json" PYTHONPATH="$REPO" \
  uv run python "$HARNESS/sweep_horizon.py" "$HARNESS/m1" 5 "$HARNESS/run-h5-nb" none \
  > "$OUT/conform-h5-noboundary.log" 2>&1

PYTHONPATH="$HARNESS:$REPO/test" /usr/bin/time -p uv run pytest \
  "test/test_calt_regressions.py::test_utter_gay_tea_oy_uses_normal_utter" \
  -p no:xdist -p shapecount -q > "$OUT/calt-baseline.log" 2>&1 || true
PYTHONPATH="$HARNESS:$REPO/test" /usr/bin/time -p uv run pytest \
  "test/test_calt_regressions.py::test_it_it_never_joins[qsAh]" \
  -p no:xdist -p shapecount -q > "$OUT/calt-shard-after2.log" 2>&1 || true

if [[ "${SKIP_CALT17:-0}" != "1" ]]; then
  /usr/bin/time -p uv run pytest test/test_calt_regressions.py -k "$(cat "$HARNESS/k17.txt")" \
    -n auto --dist worksteal -q > "$OUT/calt-17-sweeps.log" 2>&1 || true
fi

PYTHONPATH="$REPO/test:$HARNESS" uv run python "$HARNESS/depth2_states.py" > "$OUT/depth2-states.log" 2>&1

PYTHONPATH="$HARNESS" uv run python "$HARNESS/collect.py" "$OUT"

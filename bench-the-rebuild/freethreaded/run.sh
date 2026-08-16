#!/bin/zsh
# Free-threaded CPython 3.14t against the M1 settlement fixpoint.
#
# Read-only against the repo: the slice calls rebuild.pipeline.table.build_tables in memory, so
# nothing under rebuild/out or any verdict store is touched. The two
# interpreters live in this directory's own venvs, built from uv's managed store; the repo's pinned
# Python, pyproject.toml and uv.lock are not read or written by any step here.
#
# Prints one JSON object to stdout. Progress goes to stderr.
set -e
set -u

HERE="${0:A:h}"
REPO="${HERE:h:h}"
FT="$HERE/venv-ft/bin/python"
GIL="$HERE/venv-gil/bin/python"
OUT="$HERE/results.ndjson"
export UV_CACHE_DIR="$REPO/.uv-cache"   # the repo's pinned cache dir; the probe below shells out to uv
KEEP="${KEEP:-5}"   # 8 of the 18 runes: 5 single letters plus all three ligatures. Sized so the whole sweep lands in ~6 minutes.
REPS="${REPS:-2}"   # six configs x REPS = the pile every thread count divides

for p in "$FT" "$GIL"; do
  [[ -x "$p" ]] || { echo "missing interpreter $p — see setup.sh" >&2; exit 1; }
done

: > "$OUT"

step() {  # step <label> <interpreter> <mode> <threads> <reps>
  print -u2 -- "[run] $1"
  "$2" "$HERE/bench.py" "$3" "$4" "$5" "$KEEP" >> "$OUT"
}

print -u2 -- "[env] load average before: $(uptime | sed 's/.*load averages: //')"

# --- environment facts -------------------------------------------------------------------------
"$GIL" "$HERE/envprobe.py" "$FT" "$GIL" > "$HERE/env.json"

# --- 1-thread A/B, interleaved so thermal drift cannot favour either interpreter -----------------
step "GIL   1 thread  rep1" "$GIL" serial 1 "$REPS"
step "3.14t 1 thread  rep1" "$FT"  serial 1 "$REPS"
step "GIL   1 thread  rep2" "$GIL" serial 1 "$REPS"
step "3.14t 1 thread  rep2" "$FT"  serial 1 "$REPS"

# --- free-threaded scaling, all threads reading one shared ResolvedSpec --------------------------
for n in 2 4 6 8; do step "3.14t $n threads (shared spec)" "$FT" shared "$n" "$REPS"; done

# --- free-threaded scaling, each thread holding a private ResolvedSpec ---------------------------
for n in 2 4 6 8; do step "3.14t $n threads (private spec)" "$FT" own "$n" "$REPS"; done

# --- GIL control: the same threading, on the interpreter that cannot use it ----------------------
step "GIL   6 threads (control)" "$GIL" shared 6 "$REPS"

# --- the real shape: exactly six configurations, six threads, nothing to load-balance ------------
step "GIL   1 thread, six configs only" "$GIL" serial 1 1
step "3.14t 1 thread, six configs only" "$FT"  serial 1 1
step "3.14t 6 threads, six configs only (shared spec)"  "$FT" shared 6 1
step "3.14t 6 threads, six configs only (private spec)" "$FT" own 6 1

# --- gc control: the cost model's font-build lever, applied to the fixpoint on both interpreters --
print -u2 -- "[run] GIL   1 thread, six configs, gc off"
AMS_BENCH_GC=off "$GIL" "$HERE/bench.py" serial 1 1 "$KEEP" >> "$OUT"
print -u2 -- "[run] 3.14t 1 thread, six configs, gc off"
AMS_BENCH_GC=off "$FT" "$HERE/bench.py" serial 1 1 "$KEEP" >> "$OUT"

# --- container control: are the two shared module-level LRU caches safe and cheap under threads? --
print -u2 -- "[run] OrderedDict stress (the shape _GUARD_STATES and _LIVENESS_PROBES are accessed in)"
"$FT"  "$HERE/odict_stress.py" 300000 1,2,4,6,8 > "$HERE/odict-ft.json"
"$GIL" "$HERE/odict_stress.py" 300000 1,2,4,6,8 > "$HERE/odict-gil.json"

print -u2 -- "[env] load average after: $(uptime | sed 's/.*load averages: //')"
"$GIL" "$HERE/aggregate.py" "$OUT" "$HERE/env.json" "$HERE/odict-ft.json" "$HERE/odict-gil.json"

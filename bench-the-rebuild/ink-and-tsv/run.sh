#!/bin/zsh
# K3 (placed-ink layer) and K5 (TSV parsing): Python baselines, optimized Python, Rust and Go ports.
# Prints one JSON object to stdout. Everything it writes stays under bench-the-rebuild/ink-and-tsv/.
#
# Compiler flags. Rust: `cargo build --release` with opt-level 3, lto = true, codegen-units = 1, and
# the sha2/sha1 crates' "asm" feature so the ports get the same ARMv8 SHA instructions CPython's
# hashlib gets from OpenSSL (without it Rust's sha256 runs at 538 MB/s against 2,500 with it, which
# would have made the repr-versus-hash split meaningless). Go: plain `go build`, no flags.
#
# Contention. Each measurement program already reports the min over internal reps, but a neighbour
# saturating the box inflates a whole pass by 25-50%. So the harness runs PASSES complete passes and
# reports the least contended one, chosen by the Python K3 baseline's own wall time — a long,
# single-threaded, allocation-heavy measurement that tracks machine load closely. Every number in the
# output comes from that one pass; nothing is spliced across passes. The spread across passes is
# reported too, so a reader can see how quiet the box was.

set -e
set -u

HERE="${0:A:h}"
REPO="${HERE:h:h:h}"
WORK="$HERE/work"
PASSES="${PASSES:-3}"
mkdir -p "$WORK"
cd "$REPO"

log() { print -u2 -- "[run.sh] $*" }

# ---- prep -----------------------------------------------------------------------------------------

if [[ ! -f "$WORK/baseline-default.tsv" ]]; then
  log "decompressing rebuild/out/baseline-default.tsv.gz (554 MB) ..."
  uv run python -c "
import gzip, shutil
with gzip.open('rebuild/out/baseline-default.tsv.gz','rb') as fi, open('$WORK/baseline-default.tsv','wb') as fo:
    shutil.copyfileobj(fi, fo, 1<<22)
"
fi

if [[ ! -f "$HERE/k3-input.bin" || ! -f "$HERE/k3-reference.json" ]]; then
  log "exporting K3 binary input + Python reference checksum ..."
  uv run python "$HERE/k3_python.py" export >/dev/null
fi

log "building Rust (release) ..."
build_rust() { ( cd "$1" && ( cargo build --release --quiet || cargo build --release --quiet --offline ) ) }
build_rust "$HERE/rust/k3"
build_rust "$HERE/rust/k5"
log "building Go ..."
( cd "$HERE/go/k3" && go build -o k3 . )
( cd "$HERE/go/k5" && go build -o k5 . )

# ---- passes ---------------------------------------------------------------------------------------

rm -rf "$WORK"/pass1 "$WORK"/pass2 "$WORK"/pass3 "$WORK"/pass4 "$WORK"/pass5 "$WORK"/pass6
for pass in $(seq 1 "$PASSES"); do
  OUT="$WORK/pass$pass"
  mkdir -p "$OUT"

  log "pass $pass/$PASSES: K3 python ..."
  uv run python "$HERE/k3_python.py" > "$OUT/k3-python.json"

  log "pass $pass/$PASSES: K3 rust + go ..."
  "$HERE/rust/k3/target/release/k3" "$HERE/k3-input.bin" 30 1 > "$OUT/k3-rust-1.json"
  "$HERE/rust/k3/target/release/k3" "$HERE/k3-input.bin" 30 8 > "$OUT/k3-rust-8.json"
  "$HERE/go/k3/k3" "$HERE/k3-input.bin" 30 1 > "$OUT/k3-go-1.json"
  "$HERE/go/k3/k3" "$HERE/k3-input.bin" 30 8 > "$OUT/k3-go-8.json"

  log "pass $pass/$PASSES: K5 python ..."
  uv run python "$HERE/k5_python.py" > "$OUT/k5-python.json"

  log "pass $pass/$PASSES: K5 rust + go ..."
  "$HERE/rust/k5/target/release/k5" \
    "$REPO/bench-the-rebuild/fixtures/baseline-rows.tsv" \
    "$REPO/rebuild/out/m1/divergence-audit.tsv" \
    "$WORK/baseline-default.tsv" "$OUT" 8 > "$OUT/k5-rust.json"
  "$HERE/go/k5/k5" \
    "$REPO/bench-the-rebuild/fixtures/baseline-rows.tsv" \
    "$REPO/rebuild/out/m1/divergence-audit.tsv" \
    "$WORK/baseline-default.tsv" "$OUT" 8 > "$OUT/k5-go.json"

  log "pass $pass/$PASSES: K5 mmap-vs-reparse ..."
  uv run python "$HERE/k5_mmap.py" 6000 > "$OUT/k5-mmap.json"
done

# ---- pick the least contended pass and combine -------------------------------------------------------

log "combining ..."
uv run python "$HERE/combine.py" "$WORK" "$HERE"

#!/bin/zsh
# k1-meso: build and run every variant of the M1 settlement-fixpoint harness, then print one JSON object.
#
# Variants: the real Python kernel (calibration anchor), the python-baseline model, and the Rust and Go ports,
# each in one-config, six-config-serial and six-config-parallel form. Every port prints a checksum over the
# canonically-rendered window rows; the assembler at the end asserts they all agree.
#
# Re-runnable: regenerates the spec, rebuilds both binaries, overwrites out/.
set -e -u

HERE=${0:A:h}
REPO=${HERE:h:h}
OUT=$HERE/out
rm -rf $OUT
mkdir -p $OUT

TIME=/usr/bin/time

run_timed() {  # name, then command
  local name=$1; shift
  $TIME -l "$@" > $OUT/$name.json 2> $OUT/$name.time || {
    echo "FAILED: $name" >&2
    cat $OUT/$name.time >&2
    exit 1
  }
}

# --- 1. build ---------------------------------------------------------------
cd $HERE
uv run python genspec.py 1587463201 spec.txt 2> $OUT/genspec.log
cd $HERE/rust
CARGO_HOME=$HERE/rust/.cargo cargo build --release --offline > $OUT/cargo.log 2>&1
cd $HERE/go
GOFLAGS=-mod=mod GOPATH=$HERE/go/.gopath go build -o k1meso . > $OUT/go-build.log 2>&1
cd $HERE

RUSTBIN=$HERE/rust/target/release/k1meso
GOBIN=$HERE/go/k1meso
# K1_LETTERS and K1_SKIP_REAL exist only to smoke-test this script's plumbing quickly; a real measurement run
# leaves both at their defaults.
L=${K1_LETTERS:-15}

# --- 2. calibration: the real M1 kernel on the real spec, default config -----
if [[ ${K1_SKIP_REAL:-0} != 1 ]]; then
  cd $REPO
  PYTHONPATH=$REPO $TIME -l uv run python $HERE/real_kernel.py \
    > $OUT/real-kernel.json 2> $OUT/real-kernel.time || { echo "FAILED: real-kernel" >&2; exit 1; }
  cd $HERE
fi

# --- 3. the model, in all three languages -----------------------------------
run_timed python-one          uv run python $HERE/model.py $HERE/spec.txt one $L
run_timed python-six          uv run python $HERE/model.py $HERE/spec.txt six $L
if [[ ${K1_FULL:-0} == 1 ]]; then
  run_timed python-six-noshare uv run python $HERE/model.py $HERE/spec.txt six-noshare $L
fi

run_timed rust-one             $RUSTBIN $HERE/spec.txt one $L
run_timed rust-six             $RUSTBIN $HERE/spec.txt six $L
run_timed rust-six-noshare     $RUSTBIN $HERE/spec.txt six-noshare $L
run_timed rust-six-par         $RUSTBIN $HERE/spec.txt six-par $L
run_timed rust-six-par-noshare $RUSTBIN $HERE/spec.txt six-par-noshare $L

run_timed go-one               $GOBIN $HERE/spec.txt one $L
run_timed go-six               $GOBIN $HERE/spec.txt six $L
run_timed go-six-noshare       $GOBIN $HERE/spec.txt six-noshare $L
run_timed go-six-par           $GOBIN $HERE/spec.txt six-par $L
run_timed go-six-par-noshare   $GOBIN $HERE/spec.txt six-par-noshare $L

# --- 4. the memo sub-question -----------------------------------------------
run_timed memo-python uv run python $HERE/memo.py
run_timed memo-rust   $RUSTBIN memo
run_timed memo-go     $GOBIN memo

# --- 5. assemble ------------------------------------------------------------
uv run python $HERE/assemble.py $OUT

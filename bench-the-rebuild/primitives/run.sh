#!/bin/zsh
# k1-micro: primitive-operation benchmark across CPython 3.14, Rust and Go on the
# data shapes the M1 settlement fixpoint spends its time in.
#
# Prints one JSON document on stdout. Progress goes to stderr.
#
#   ./run.sh              build everything, run all three, print the merged JSON
#   K1_SKIP_BUILD=1       reuse the existing rust/go binaries
#
# Read-only against the repo: it reads bench-the-rebuild/fixtures/memo-keys.tsv.gz
# and bench-the-rebuild/fixtures/candidate-fields.tsv and writes only under
# bench-the-rebuild/primitives/.
set -e -u

HERE=${0:A:h}
REPO=${0:A:h:h:h}
cd "$HERE"
mkdir -p out data

# 1. shared inputs. Cached: gen_data.py is deterministic and its source never
#    changes, so a second run skips ~5 s of gzip + packing.
if [[ ! -f data/meta.json || ! -f data/keys-packed.u64 ]]; then
  print -u2 "[k1-micro] generating shared inputs"
  (cd "$REPO" && uv run python "$HERE/gen_data.py" >/dev/null)
fi

# 2. builds
if [[ -z ${K1_SKIP_BUILD:-} ]]; then
  print -u2 "[k1-micro] cargo build --release"
  (cd rust && CARGO_NET_OFFLINE=true cargo build --release 2>&1 | tail -2 >&2)
  print -u2 "[k1-micro] go build"
  (cd go && go build -o k1micro . )
fi

# 3. run. Serial on purpose: these are single-threaded latency measurements and
#    must not contend with each other.
print -u2 "[k1-micro] python"
(cd "$REPO" && uv run python "$HERE/bench.py")
print -u2 "[k1-micro] rust"
./rust/target/release/k1micro ./data ./out/rust.json
print -u2 "[k1-micro] go"
./go/k1micro ./data ./out/go.json

# 4. merge + weighted composite
(cd "$REPO" && uv run python "$HERE/report.py")

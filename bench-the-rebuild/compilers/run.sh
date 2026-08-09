#!/bin/zsh
# Keep-the-Python accelerators on the M1 settlement kernel: mypyc, Cython (pure-Python mode), PyPy.
# Prints one JSON report to stdout. Everything it needs is prebuilt under this directory;
# ./build.sh rebuilds the accelerator trees from the repo source if they are missing.
#
# AMS_BENCH_SUBSET=k9 (default) | k11 | full  — how much of the real rune spec the fixpoint runs over.
set -e
HERE="${0:a:h}"
cd "$HERE"

for t in venv-mypyc venv-cython venv-pypy tree-mypyc tree-mypyc-all tree-cython tree-pypy; do
  if [[ ! -e "$HERE/$t" ]]; then
    print -u2 "missing $t — run $HERE/build.sh first (venvs: see README-setup in the report)"
    exit 1
  fi
done

exec "$HERE/venv-mypyc/bin/python" "$HERE/run_all.py"

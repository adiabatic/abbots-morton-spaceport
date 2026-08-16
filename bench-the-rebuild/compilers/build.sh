#!/bin/zsh
# Rebuild every accelerator tree from the repo source and record what each build cost.
# Not called by run.sh: the trees are prebuilt so the runner stays under its time budget.
# Usage: ./build.sh
set -e
HERE="${0:a:h}"
REPO="${HERE:h:h}"
cd "$HERE"
mkdir -p out

start=$(date +%s.%N)

# --- trees ---------------------------------------------------------------
./mktree.sh tree-mypyc      > /dev/null
./mktree.sh tree-mypyc-all  > /dev/null
./mktree.sh tree-cython     > /dev/null
./mktree.sh tree-pypy       > /dev/null

# --- source adaptations --------------------------------------------------
# mypyc: five annotation-only edits mypy demands, plus the mypy_extensions escape hatch for
# PartitionError(RuntimeError) (mypyc cannot make a native class out of a non-Exception builtin subclass).
"$HERE/venv-mypyc/bin/python" patch_for_mypyc.py tree-mypyc
"$HERE/venv-mypyc/bin/python" patch_for_mypyc.py tree-mypyc-all
for t in tree-mypyc tree-mypyc-all; do
  "$HERE/venv-mypyc/bin/python" - "$t" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]) / "rebuild/pipeline/table.py"
t = p.read_text()
if "mypy_extensions" not in t:
    t = t.replace("import gzip\nimport hashlib\nimport json\n",
                  "import gzip\nimport hashlib\nimport json\n\nimport mypy_extensions\n", 1)
    t = t.replace("class PartitionError(RuntimeError):\n",
                  "@mypy_extensions.mypyc_attr(native_class=False)\nclass PartitionError(RuntimeError):\n", 1)
    p.write_text(t)
PY
done
# mypyc also needs the Literal narrowing fix in model.py for the -all variant.
"$HERE/venv-mypyc/bin/python" - <<'PY'
import pathlib
p = pathlib.Path("tree-mypyc-all/rebuild/pipeline/model.py"); t = p.read_text()
if "cast" not in t.split("\n")[10]:
    t = t.replace("from typing import Collection, Literal, Mapping\n",
                  "from typing import Collection, Literal, Mapping, cast\n", 1)
    t = t.replace("        return (op, side, int(argument))\n",
                  '        return (cast(Literal["ext", "con", "trim"], op), side, int(argument))\n', 1)
    p.write_text(t)
PY

# Cython 3.2.9 and PyPy 3.11 both predate PEP 758, so their parsers reject `except A, B:`.
"$HERE/venv-cython/bin/python" patch_pep758.py tree-cython
"$HERE/venv-cython/bin/python" patch_pep758.py tree-pypy
# Cython derives extension names from the package path, so the namespace package needs an __init__.
touch tree-cython/rebuild/__init__.py
cp setup_cython.py tree-cython/setup_cython.py 2>/dev/null || true

# --- compiles ------------------------------------------------------------
t0=$(date +%s.%N)
( cd tree-mypyc && MYPYPATH=. "$HERE/venv-mypyc/bin/mypyc" --explicit-package-bases \
    rebuild/pipeline/settle.py rebuild/pipeline/table.py > "$HERE/out/build-mypyc.log" 2>&1 )
t1=$(date +%s.%N)
( cd tree-mypyc-all && MYPYPATH=. "$HERE/venv-mypyc/bin/mypyc" --explicit-package-bases \
    rebuild/pipeline/model.py rebuild/pipeline/specificity.py rebuild/pipeline/settle.py rebuild/pipeline/table.py \
    > "$HERE/out/build-mypyc-all.log" 2>&1 )
t2=$(date +%s.%N)
( cd tree-cython && "$HERE/venv-cython/bin/python" setup_cython.py > "$HERE/out/build-cython.log" 2>&1 )
t3=$(date +%s.%N)

"$HERE/venv-mypyc/bin/python" - "$t0" "$t1" "$t2" "$t3" "$start" <<'PY' > out/build-costs.json
import json, os, sys
t0, t1, t2, t3, start = (float(x) for x in sys.argv[1:6])
print(json.dumps({
  "note": "wall seconds for a cold compile of each accelerator's kernel modules; PyPy compiles nothing",
  "mypyc_settle_table_s": round(t1 - t0, 2),
  "mypyc_all_four_modules_s": round(t2 - t1, 2),
  "cython_all_four_modules_s": round(t3 - t2, 2),
  "tree_setup_and_patching_s": round(t0 - start, 2),
  "loadavg_1m": round(os.getloadavg()[0], 2),
}, indent=2))
PY
cat out/build-costs.json

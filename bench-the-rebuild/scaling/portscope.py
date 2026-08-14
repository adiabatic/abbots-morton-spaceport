"""Which lines of the settlement kernel does build_tables actually reach?
Line-trace a small-subset build; report executed lines per file, and per function for settle/table."""

import sys, ast, time
from dataclasses import replace
from pathlib import Path
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline import table as T, settle as S, specificity as SP, model as M

ROOT = Path.cwd()
WATCH = {
    str((ROOT / f).resolve()): f
    for f in [
        "rebuild/pipeline/settle.py",
        "rebuild/pipeline/table.py",
        "rebuild/pipeline/specificity.py",
        "rebuild/pipeline/model.py",
        "rebuild/pipeline/spec_load.py",
    ]
}
hit = {f: set() for f in WATCH.values()}


def tracer(frame, event, arg):
    fn = frame.f_code.co_filename
    rel = WATCH.get(fn)
    if rel is None:
        return None
    if event == "line":
        hit[rel].add(frame.f_lineno)
    return tracer


spec = load_default_spec()
KEEP = 6
names = sorted(spec.runes)
keep = set()
for n in [x for x in names if spec.runes[x].sequence]:
    keep.add(n)
    keep.update(spec.runes[n].sequence)
for n in names:
    if len(keep) >= KEEP:
        break
    keep.add(n)
sub = replace(spec, runes={n: r for n, r in spec.runes.items() if n in keep})

sys.settrace(tracer)
d, t = T.build_tables(sub, frozenset())
sys.settrace(None)
print(f"traced build_tables over {len(sub.runes)} runes -> {len(d.transitions)} windows")


def fn_map(rel):
    src = (ROOT / rel).read_text()
    tree = ast.parse(src)
    out = []

    def walk(node, prefix=""):
        for c in node.body:
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((prefix + c.name, c.lineno, c.end_lineno))
            elif isinstance(c, ast.ClassDef):
                walk(c, prefix + c.name + ".")

    walk(tree)
    return out, len(src.splitlines())


def executable_lines(rel):
    import dis

    src = (ROOT / rel).read_text()
    code = compile(src, rel, "exec")
    lines = set()
    stack = [code]
    while stack:
        c = stack.pop()
        for _, _, ln in c.co_lines():
            if ln:
                lines.add(ln)
        for const in c.co_consts:
            if hasattr(const, "co_lines"):
                stack.append(const)
    return lines


print()
for rel in [
    "rebuild/pipeline/settle.py",
    "rebuild/pipeline/table.py",
    "rebuild/pipeline/specificity.py",
    "rebuild/pipeline/model.py",
    "rebuild/pipeline/spec_load.py",
]:
    ex = executable_lines(rel)
    h = hit[rel] & ex
    print(f"{rel:34s} executable {len(ex):5d}  reached {len(h):5d}  ({100*len(h)/max(len(ex),1):4.1f}%)")

print()
for rel in ["rebuild/pipeline/settle.py", "rebuild/pipeline/table.py"]:
    ex = executable_lines(rel)
    fns, _ = fn_map(rel)
    print(f"--- {rel}: functions NEVER entered by build_tables ---")
    dead = []
    for name, a, b in fns:
        body = {l for l in ex if a <= l <= b}
        got = body & hit[rel]
        if not got:
            dead.append((name, b - a + 1))
    for name, n in sorted(dead, key=lambda r: -r[1]):
        print(f"   {n:4d} lines  {name}")
    print(
        f"   TOTAL never-entered: {sum(n for _,n in dead)} lines of {sum(1 for _ in (ROOT/rel).read_text().splitlines())}"
    )
    print()

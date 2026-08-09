import ast, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def mod_path(name):
    p = ROOT / (name.replace(".", "/") + ".py")
    return p if p.exists() else None


def imports_of(path):
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                out.add(node.module)
                # from X import Y where Y may be a module
                for a in node.names:
                    out.add(node.module + "." + a.name)
    return out


def closure(seeds):
    seen = set()
    third = set()
    frontier = list(seeds)
    while frontier:
        m = frontier.pop()
        if m in seen:
            continue
        p = mod_path(m)
        if p is None:
            continue
        seen.add(m)
        for imp in imports_of(p):
            if mod_path(imp):
                frontier.append(imp)
            elif imp.split(".")[0] in ("rebuild", "tools", "site"):
                pass
            else:
                third.add(imp.split(".")[0])
    return seen, third


seeds = ["rebuild.pipeline.table", "rebuild.pipeline.settle"]
seen, third = closure(seeds)
print("=== closure of table+settle (source modules) ===")
for m in sorted(seen):
    p = mod_path(m)
    print(f"  {sum(1 for _ in p.open()):6d}  {m}")
print("total lines:", sum(sum(1 for _ in mod_path(m).open()) for m in seen))
print("stdlib/third-party:", sorted(third))

print()
seeds2 = ["rebuild.pipeline.run_m1"]
seen2, third2 = closure(seeds2)
print("=== closure of run_m1 ===")
print("modules:", len(seen2), "lines:", sum(sum(1 for _ in mod_path(m).open()) for m in seen2))
print("extra beyond table+settle:", sorted(seen2 - seen))
print("stdlib/third-party:", sorted(third2))

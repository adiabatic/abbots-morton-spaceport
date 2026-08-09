import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def report(rel):
    p = ROOT / rel
    src = p.read_text().splitlines()
    tree = ast.parse("\n".join(src))
    rows = []

    def walk(node, prefix=""):
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = child.lineno
                end = child.end_lineno
                # count docstring lines
                doc = 0
                if (
                    child.body
                    and isinstance(child.body[0], ast.Expr)
                    and isinstance(child.body[0].value, ast.Constant)
                    and isinstance(child.body[0].value.value, str)
                ):
                    d = child.body[0]
                    doc = d.end_lineno - d.lineno + 1
                rows.append((prefix + child.name, end - start + 1, doc, start))
            elif isinstance(child, ast.ClassDef):
                rows.append(
                    (prefix + "class " + child.name, child.end_lineno - child.lineno + 1, 0, child.lineno)
                )
                walk(child, prefix + child.name + ".")

    walk(tree)
    print(f"### {rel}  total {len(src)} lines")
    for name, n, doc, start in sorted(rows, key=lambda r: -r[1]):
        print(f"{n:5d} ({doc:3d} doc)  L{start:<5d} {name}")
    print()


for f in ["rebuild/pipeline/settle.py", "rebuild/pipeline/table.py"]:
    report(f)

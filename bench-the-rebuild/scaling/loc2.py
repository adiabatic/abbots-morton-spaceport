import ast
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
def measure(rel):
    src = (ROOT/rel).read_text()
    lines = src.splitlines()
    tree = ast.parse(src)
    doc = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            doc.update(range(node.lineno, node.end_lineno+1))
    if tree.body and isinstance(tree.body[0], ast.Expr):
        pass
    dchars = sum(len(lines[i-1]) for i in doc)
    cchars = sum(len(l) for l in lines if l.strip().startswith("#"))
    total = sum(len(l) for l in lines)
    print(f"{rel:36s} chars {total:7d}  docstring {dchars:6d} ({100*dchars/total:4.1f}%)  comment {cchars:5d} ({100*cchars/total:4.1f}%)  prose {100*(dchars+cchars)/total:4.1f}%")
for f in ["rebuild/pipeline/settle.py","rebuild/pipeline/table.py","rebuild/pipeline/model.py","rebuild/pipeline/specificity.py","rebuild/pipeline/trace_memo.py","rebuild/pipeline/conform.py"]:
    measure(f)

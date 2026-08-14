import ast, io, tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def measure(rel):
    p = ROOT / rel
    src = p.read_text()
    lines = src.splitlines()
    n = len(lines)
    blank = sum(1 for l in lines if not l.strip())
    # docstring lines
    tree = ast.parse(src)
    doc = set()

    def mark(node):
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            d = node.body[0]
            doc.update(range(d.lineno, d.end_lineno + 1))

    mark(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            mark(node)
        # attribute docstrings / trailing string exprs in class bodies
    # standalone string expressions anywhere (field docstrings)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            doc.update(range(node.lineno, node.end_lineno + 1))
    comment = 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            ln = tok.start[0]
            if ln not in doc:
                # whole-line comment?
                if lines[ln - 1].strip().startswith("#"):
                    comment += 1
                else:
                    comment += 0  # trailing comment on a code line
    code = n - blank - len(doc) - comment
    print(
        f"{rel:38s} total {n:5d}  code {code:5d}  docstring {len(doc):5d}  comment {comment:4d}  blank {blank:4d}   prose%={100*(len(doc)+comment)/n:.1f}"
    )
    return code


tot = 0
for f in [
    "rebuild/pipeline/settle.py",
    "rebuild/pipeline/table.py",
    "rebuild/pipeline/model.py",
    "rebuild/pipeline/specificity.py",
    "rebuild/pipeline/spec_load.py",
    "rebuild/pipeline/conform.py",
    "rebuild/pipeline/emit_gsub.py",
    "rebuild/pipeline/geometry.py",
    "rebuild/pipeline/defects.py",
]:
    tot += measure(f)
print(
    "kernel(settle+table+model+specificity) code lines:",
)

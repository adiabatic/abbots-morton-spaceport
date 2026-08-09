"""Move the perf-study harnesses out of tmp/ into bench-the-rebuild/, rewriting the
paths they were written against. Idempotent: it rebuilds the destination from scratch.

Source trees live under tmp/perf2/<slug>/ and were written with the repo root hardcoded
and their outputs pointed at tmp/. Both have to change for the tree to survive a reboot
and a different checkout.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path.cwd().resolve()
while not (ROOT / "pyproject.toml").exists():
    ROOT = ROOT.parent
    if ROOT == ROOT.parent:
        raise SystemExit("run this from inside the repo")

SRC2 = ROOT / "tmp" / "perf2"
SRC1 = ROOT / "tmp" / "perf"
DEST = ROOT / "bench-the-rebuild"

HARDCODED = "/Users/comatoast/Projects/Quikscript/Fonts/abbots-morton-spaceport"

# old repo-relative directory -> new repo-relative directory
DIRMAP = {
    "tmp/perf2/k1-meso": "bench-the-rebuild/kernel-model",
    "tmp/perf2/k1-micro": "bench-the-rebuild/primitives",
    "tmp/perf2/k3-k5": "bench-the-rebuild/ink-and-tsv",
    "tmp/perf2/feasibility": "bench-the-rebuild/scaling",
    "tmp/perf2/python-levers": "bench-the-rebuild/levers",
    "tmp/perf2/compilers": "bench-the-rebuild/compilers",
    "tmp/perf2/freethreaded": "bench-the-rebuild/freethreaded",
    "tmp/perf2/cut-the-work": "bench-the-rebuild/cut-the-work",
    "tmp/perf/attr-overhead/data": "bench-the-rebuild/fixtures",
    # the two written records and the assembled bench output, now under evidence/
    "tmp/perf/cost-model.md": "bench-the-rebuild/evidence/cost-model.md",
    "tmp/perf2/decision.json": "bench-the-rebuild/evidence/decision.json",
    "tmp/perf2/decision.md": "bench-the-rebuild/evidence/decision.md",
    "tmp/perf2/bench": "bench-the-rebuild/evidence",
}

SKIP_DIRS = {
    ".venv",
    ".uv-cache",
    "__pycache__",
    "target",
    ".cargo",
    ".gopath",
    "site-packages",
    "run-v-control",
    "out",
    "work",
    "data",
    "mirror",
    "tree-cython",
    "tree-mypyc",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
}
# venvs the harnesses created for alternative interpreters; regenerable by their setup.sh
SKIP_DIR_PREFIXES = ("venv", ".venv")
KEEP_SUFFIX = {".py", ".rs", ".go", ".sh", ".toml", ".mod", ".sum", ".lock", ".md", ".txt"}

# whole-tree copies (source slug -> dest name); everything matching KEEP_SUFFIX comes over
TREES = [
    ("k1-meso", "kernel-model"),
    ("k1-micro", "primitives"),
    ("k3-k5", "ink-and-tsv"),
    ("feasibility", "scaling"),
    ("cut-the-work", "cut-the-work"),
    ("freethreaded", "freethreaded"),
]

# trees where the source dir is full of venvs/mirrors, so name the keepers explicitly
WHITELIST = {
    "python-levers": (
        "levers",
        [
            "apply_m1_patches.py",
            "lever_fontbuild.py",
            "lever_signature.py",
            "lever_spec_load.py",
            "lever_surface.py",
            "lever_yaml.py",
            "m1_slice.py",
            "prof_slice.py",
            "prof_variant.py",
            "probe_subset.py",
            "mkvariant.sh",
            "ab_fontbuild.sh",
            "levers_site/sitecustomize.py",
        ],
    ),
    "compilers": (
        "compilers",
        [
            "bench_kernel.py",
            "build.sh",
            "mktree.sh",
            "patch_for_mypyc.py",
            "patch_pep758.py",
            "preload_compiled.py",
            "probe_subset.py",
            "run_all.py",
            "run.sh",
            "setup_cython.py",
            "dcbench/dckernel.py",
            "dcbench/run_dc.py",
        ],
    ),
}

EVIDENCE = [
    (SRC1 / "cost-model.md", "cost-model.md"),
    (SRC2 / "decision.md", "decision.md"),
    (SRC2 / "decision.json", "decision.json"),
    (SRC2 / "feasibility" / "scaling.json", "alphabet-scaling.json"),
    (SRC2 / "feasibility" / "spec.json", "resolved-spec-dump.json"),
    (SRC2 / "bench" / "results.json", "bench-results.json"),
    (SRC2 / "bench" / "results.md", "bench-results.md"),
]

FIXTURES = [
    "baseline-rows.tsv",
    "candidate-fields.tsv",
    "coord-types.json",
    "memo-keys.tsv.gz",
    "outlines-after.json",
    "outlines-before.json",
    "shaped-runs.jsonl",
]


def insert_import(text: str, line: str) -> str:
    """Add an import after the module docstring and any __future__ import."""
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return line + "\n" + text
    at = 0
    for node in tree.body:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if is_docstring or is_future:
            at = node.end_lineno or at
        else:
            break
    lines = text.splitlines(keepends=True)
    return "".join(lines[:at]) + line + "\n" + "".join(lines[at:])


def rewrite(text: str, dest_file: Path) -> tuple[str, int]:
    """Repoint a harness at its new home. Returns the text and the number of edits."""
    n = 0
    depth = len(dest_file.relative_to(ROOT).parts) - 1
    root_expr = f"Path(__file__).resolve().parents[{depth}]"

    # 1. old tmp/ locations -> the new tree (longest prefix first, so the fixtures
    #    path under tmp/perf/ is not shadowed by a tmp/perf2/ rule). This runs before
    #    the root rewrite so an absolute literal has its tail corrected first.
    for old in sorted(DIRMAP, key=len, reverse=True):
        if old in text:
            text = text.replace(old, DIRMAP[old])
            n += 1

    # 2. hardcoded repo root -> derived from __file__ / $0, at this file's own depth
    if HARDCODED in text:
        if dest_file.suffix == ".py":
            for quoted in (f'Path("{HARDCODED}")', f"Path('{HARDCODED}')"):
                text = text.replace(quoted, root_expr)
            # an absolute string literal with a tail: "<root>/bench-the-rebuild/x" -> root / "..."
            text = re.sub(
                rf'"{re.escape(HARDCODED)}/([^"]*)"',
                lambda m: f'str({root_expr} / "{m.group(1)}")',
                text,
            )
            text = text.replace(f'"{HARDCODED}"', f"str({root_expr})")
        else:
            # zsh: ${0:A:h} is this script's dir; climb to the repo root
            text = text.replace(HARDCODED, "${0:A" + ":h" * (depth + 1) + "}")
        n += 1

    # 3. k1-meso's run.sh climbed three levels from tmp/perf2/<slug>; it is two now
    if "REPO=${HERE:h:h:h}" in text:
        text = text.replace("REPO=${HERE:h:h:h}", "REPO=${HERE:h:h}")
        n += 1

    if HARDCODED in text:
        raise SystemExit(f"{dest_file}: hardcoded repo path survived the rewrite")

    # 4. the rewrite may have introduced Path() into a file that never imported it
    if dest_file.suffix == ".py" and "Path(__file__)" in text:
        if not re.search(r"^\s*(from pathlib import|import pathlib)", text, re.M):
            text = insert_import(text, "from pathlib import Path")
            n += 1

    # 4. anything still pointing into tmp/perf* is a path this script did not know about
    leftover = re.findall(r"tmp/perf2?[\w./-]*", text)
    if leftover:
        raise SystemExit(f"{dest_file}: unmapped tmp path(s): {sorted(set(leftover))}")

    return text, n


def rewrite_evidence(text: str) -> str:
    """Evidence documents are historical: their per-slice citations stay, but repointed at
    the mirrored copies under evidence/raw/ so every path in them still resolves."""
    text = text.replace(HARDCODED + "/", "").replace(HARDCODED, "<repo>")
    text = text.replace("tmp/perf2/", "raw/perf2/").replace("tmp/perf/", "raw/perf/")
    return text


def copy_file(src: Path, dst: Path, stats: dict, mode: str = "harness") -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if mode == "evidence" and src.suffix in {".md", ".json", ".txt"}:
        try:
            dst.write_text(rewrite_evidence(src.read_text()))
        except UnicodeDecodeError:
            shutil.copy2(src, dst)
    elif mode == "harness" and src.suffix in KEEP_SUFFIX:
        text, n = rewrite(src.read_text(), dst)
        dst.write_text(text)
        stats["rewritten"] += 1 if n else 0
    else:
        shutil.copy2(src, dst)
    stats["files"] += 1


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    stats = {"files": 0, "rewritten": 0}

    for slug, name in TREES:
        base = SRC2 / slug
        if not base.exists():
            print(f"  ! missing source tree {slug}")
            continue
        for src in sorted(base.rglob("*")):
            if not src.is_file():
                continue
            parts = src.relative_to(base).parts
            if any(p in SKIP_DIRS or p.startswith(SKIP_DIR_PREFIXES) for p in parts):
                continue
            if src.suffix not in KEEP_SUFFIX:
                continue
            copy_file(src, DEST / name / src.relative_to(base), stats)

    for slug, (name, keepers) in WHITELIST.items():
        for rel in keepers:
            src = SRC2 / slug / rel
            if not src.exists():
                print(f"  ! missing {slug}/{rel}")
                continue
            copy_file(src, DEST / name / rel, stats)

    for rel in FIXTURES:
        src = SRC1 / "attr-overhead" / "data" / rel
        if src.exists():
            copy_file(src, DEST / "fixtures" / rel, stats)
        else:
            print(f"  ! missing fixture {rel}")

    for src, name in EVIDENCE:
        if src.exists():
            copy_file(src, DEST / "evidence" / name, stats, mode="evidence")
        else:
            print(f"  ! missing evidence {src.name}")

    # the numeric trail the two written records cite, mirrored so their paths resolve.
    # Small text only: the .prof dumps, venvs and cargo targets are regenerable and stay out.
    raw = 0
    for base, label in ((SRC1, "perf"), (SRC2, "perf2")):
        for src in sorted(base.rglob("*")):
            if not src.is_file() or src.suffix not in {".json", ".md", ".txt"}:
                continue
            rel = src.relative_to(base)
            if any(p in SKIP_DIRS or p.startswith(".") for p in rel.parts[:-1]):
                continue
            if src.stat().st_size > 600_000:
                continue
            copy_file(src, DEST / "evidence" / "raw" / label / rel, stats, mode="evidence")
            raw += 1
    print(f"mirrored {raw} raw evidence files")

    print(f"copied {stats['files']} files; {stats['rewritten']} had paths rewritten")
    for d in sorted(p for p in DEST.iterdir() if p.is_dir()):
        n = sum(1 for f in d.rglob("*") if f.is_file())
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        print(f"  {d.name:<16} {n:>4} files  {size / 1024:>9.1f} KiB")


if __name__ == "__main__":
    main()

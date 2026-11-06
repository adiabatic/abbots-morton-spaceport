"""Tests for `rebuild/pipeline/output_sink.py`: that only the fault kit sends a table build's memo files to the null sink, with the same tables, and that nothing the kit checks after a build reads them."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from rebuild.pipeline import conform, fixtures, kernel_exec, kernel_io, run_m1
from rebuild.tools import fault_kit
from rebuild.tools.cycle_timings import CYCLE_RUN_ENV

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = fixtures.mini_spec()
MEMO_READ = "rebuild.pipeline.run_m1:previous_memos"
MEMO_FILE_NAMES = frozenset(
    {"rebuild.pipeline.kernel_exec:memo_path", "rebuild.pipeline.kernel_exec:read_memo_head"}
)
MEMO_READERS = frozenset({MEMO_READ, *MEMO_FILE_NAMES, "rebuild.pipeline.kernel_exec:build_table_files"})


def test_only_the_fault_kit_sends_the_memo_files_to_the_null_sink(monkeypatch, tmp_path):
    """A stamped table build under the kit's step environment (`fault_kit.step_env`) writes the tables and digests a cycle pass's build writes and no memo file, where the cycle pass's build writes one per configuration. Under the kit an unstamped build passes no sink, which the crate would refuse."""
    spec_path = tmp_path / "spec.json"
    kernel_io.write_spec(SPEC, spec_path)
    kernel_exec.ensure_built()
    configs = conform.SETTLEMENT_CONFIGS
    kit = fault_kit.step_env(tmp_path, ("qsTea",))[CYCLE_RUN_ENV]

    def build(arm: str, run: str, stamp: str | None) -> dict[str, str]:
        monkeypatch.setenv(CYCLE_RUN_ENV, run)
        return kernel_exec.build_table_files(
            spec_path,
            tmp_path / arm,
            configs,
            inputs="sink-stamp",
            threads=2,
            symbols=len(conform.spec_alphabet(SPEC)),
            memo_stamp=stamp,
        ).digests

    cycle = build("cycle", "a-cycle-run", run_m1.memo_stamp(SPEC))
    assert build("kit", kit, run_m1.memo_stamp(SPEC)) == cycle == build("unstamped", kit, None)
    for config in configs:
        for family in ("settlement", "joins", "windows"):
            name = f"{family}-{config}.tsv"
            assert (tmp_path / "kit" / name).read_bytes() == (tmp_path / "cycle" / name).read_bytes(), name
    assert sorted(path.name for path in (tmp_path / "cycle").glob("memo-*")) == sorted(
        f"memo-{config}.tsv" for config in configs
    )
    assert not [*(tmp_path / "kit").glob("memo-*"), *(tmp_path / "unstamped").glob("memo-*")]


def _build_modules() -> Iterator[tuple[str, str, ast.Module]]:
    """Each module under rebuild/ but the tests, as its dotted name, the package its relative imports resolve against, and its syntax tree. The walk leaves out the crate and the live tree."""
    rebuild = REPO_ROOT / "rebuild"
    paths = [*rebuild.glob("*.py")]
    for directory in rebuild.iterdir():
        if directory.is_dir() and directory.name not in ("out", "kernel-rs"):
            paths.extend(directory.rglob("*.py"))
    for path in sorted(paths):
        relative = path.relative_to(REPO_ROOT)
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        parts = relative.with_suffix("").parts
        package = ".".join(parts[:-1])
        module = package if parts[-1] == "__init__" else ".".join(parts)
        yield module, package, ast.parse(path.read_text(encoding="utf-8"))


class _CallGraph:
    """The build's functions and methods, as `module:name` and `module:Class.name`, each with the ones its body names: a bare name or a dotted chain resolved through the module's own definitions and its imports, re-exports followed, and `self.` or `cls.` within its class. A class named stands for all of its methods. A method reached only through some other object is not followed, so the walk shows what the build reaches by name."""

    def __init__(self) -> None:
        self.bodies: dict[str, tuple[str, str | None, ast.AST]] = {}
        self.classes: dict[str, list[str]] = {}
        self.bindings: dict[str, dict[str, str]] = {}
        for module, package, tree in _build_modules():
            bound: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        bound[alias.asname or alias.name.split(".")[0]] = (
                            alias.name if alias.asname else alias.name.split(".")[0]
                        )
                elif isinstance(node, ast.ImportFrom):
                    base = package.rsplit(".", node.level - 1)[0] if node.level else ""
                    source = ".".join(part for part in (base, node.module or "") if part)
                    for alias in node.names:
                        bound[alias.asname or alias.name] = f"{source}.{alias.name}"
            self.bindings[module] = bound
            for node in tree.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    self.bodies[f"{module}:{node.name}"] = (module, None, node)
                elif isinstance(node, ast.ClassDef):
                    methods = [
                        item for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    ]
                    self.classes[f"{module}:{node.name}"] = [
                        f"{module}:{node.name}.{item.name}" for item in methods
                    ]
                    for item in methods:
                        self.bodies[f"{module}:{node.name}.{item.name}"] = (module, node.name, item)

    def _resolve(self, dotted: list[str], depth: int = 0) -> set[str]:
        for split in range(len(dotted) - 1, 0, -1):
            module, rest = ".".join(dotted[:split]), dotted[split:]
            if module not in self.bindings:
                continue
            named = f"{module}:{rest[0]}"
            if named in self.bodies:
                return {named}
            if named in self.classes:
                method = f"{named}.{rest[1]}" if len(rest) > 1 else None
                return {method} if method in self.bodies else set(self.classes[named])
            target = self.bindings[module].get(rest[0])
            if target is not None and depth < 4:
                return self._resolve([*target.split("."), *rest[1:]], depth + 1)
            return set()
        return set()

    def edges(self, module: str, cls: str | None, node: ast.AST) -> set[str]:
        found: set[str] = set()
        for sub in ast.walk(node):
            chain: list[str] = []
            while isinstance(sub, ast.Attribute):
                chain.insert(0, sub.attr)
                sub = sub.value
            if not isinstance(sub, ast.Name):
                continue
            chain.insert(0, sub.id)
            if chain[0] in ("self", "cls") and cls is not None and len(chain) > 1:
                found |= {f"{module}:{cls}.{chain[1]}"} & self.bodies.keys()
            elif chain[0] in self.bindings[module]:
                found |= self._resolve([*self.bindings[module][chain[0]].split("."), *chain[1:]])
            else:
                found |= self._resolve([*module.split("."), *chain])
        return found

    def reach(self, module: str, cls: str | None, node: ast.AST) -> set[str]:
        seen: set[str] = set()
        queue = list(self.edges(module, cls, node))
        while queue:
            name = queue.pop()
            if name not in seen:
                seen.add(name)
                queue.extend(self.edges(*self.bodies[name]))
        return seen


def test_nothing_the_kit_checks_after_a_build_reads_the_memo_files():
    """The kit's steps are bare `run_m1`, `run_m1 --conform-only` and `deep_replay` (`rebuild/perf/faults.yaml`). The memo files' one reader is `run_m1.previous_memos`, the only function besides the pack that names a memo file's path or head, and only `run_m1.build_tables` calls it, so a bare run reaches it and a build after a null-sink build runs cold. Neither the `--conform-only` branch of `run_m1_gates.main` nor `deep_replay.main` reaches it, `read_memo_head`, or `kernel_exec.build_table_files`, which runs the crate's `build-tables`, the only verb that reads a previous memo."""
    graph = _CallGraph()
    namers = {name for name, body in graph.bodies.items() if graph.edges(*body) & MEMO_FILE_NAMES}
    assert namers == {MEMO_READ, "rebuild.pipeline.run_m1:_pack_config"}
    callers = {name for name, body in graph.bodies.items() if MEMO_READ in graph.edges(*body)}
    assert callers == {"rebuild.pipeline.run_m1:build_tables"}
    assert MEMO_READERS <= graph.reach(*graph.bodies["rebuild.pipeline.run_m1:full_run"])
    gates_main = graph.bodies["rebuild.pipeline.run_m1_gates:main"][2]
    (conform_only,) = [
        node
        for node in ast.walk(gates_main)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Attribute)
        and node.test.attr == "conform_only"
    ]
    assert isinstance(conform_only.body[-1], ast.Return)
    for (module, cls, node), work in (
        (
            ("rebuild.pipeline.run_m1_gates", None, conform_only),
            "rebuild.pipeline.run_m1_gates:run_font_conformance",
        ),
        (graph.bodies["rebuild.tools.deep_replay:main"], "rebuild.pipeline.kernel_exec:replay_strings"),
    ):
        reached = graph.reach(module, cls, node)
        assert work in reached, work
        assert not reached & MEMO_READERS, sorted(reached & MEMO_READERS)

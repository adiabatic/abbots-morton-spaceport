"""The review server's kerning routes (rebuild/KERN-PLAN.md section 6), which the app's kerning view (`#view=kern`, `static/kern-view.js`) reads:

- `GET /kern/inventory` answers with `rebuild/out/m1/kern-inventory.json` as the build wrote it (`rebuild/pipeline/kern_inventory.py`), or a 404 whose body says to run the M1 build.
- `GET /kern` answers with the values the view applies to the inventory's pixels, as one JSON document (`values_payload`, format `VALUES_FORMAT`, checked by `check_values_payload`):
  - `live`: every facing pair a `kern:` record decides, resolved now from the rune files on disk over the tables under `rebuild/out/m1` (`kern.resolve`), each with its value and its deciding records, or the error the stage raises over them, in which case the view shows the built values.
  - `built`: every facing pair whose value in the built font is not 0, from the `kern-classes.json` beside `M1.otf`, which read-back vouches for, so the view marks a value the built font does not have yet.
  - `runes`: each rune file's `kern:` block, the raw text from its `kern:` line to the end of the file, that text's sha256, and its records with their key paths, values and flow form (`kern.describe`).
  - The staleness the view reports: `tables` (the tables' stamp and whether it is the one the sources on disk give, `run_m1.tables_inputs`, recomputed at most every `TABLES_TTL_S` seconds), `inventory` (whether the file is there, its stamp, and whether that stamp names these tables and the Manual pages on disk), and `pass` (the pid holding the cycle's pass lock, while a pass runs).

Anything that goes wrong while `GET /kern` is computed, tables whose configurations carry more than one stamp among it, comes back as the same document with `live.error` naming it (`failure_payload`), never as a server error, and a spec or tables that do not load are not read again until one of their files moves. Both routes read and write nothing. Neither settles a text, so neither runs the kernel or `cargo`. The tables are read with `table.read_windows_head(cells_only=True)` and `table.read_join_tsv`, and kept, with the spec, the minted glyphs and the last answer, until a file they came from moves (`file_signature`). The work runs in the server's executor, and concurrent requests share one computation.

It is server-only: the corpus build never imports it (`fingerprint.REVIEW_NON_BUILD_MODULES`), `serve.main` imports it when the server starts, so no tool that imports `serve.py` reaches it, and the server's code digest covers it (`landing.SERVED_MODULES`).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import warnings
from collections.abc import Callable
from pathlib import Path

from rebuild.pipeline import conform, kern, kern_inventory, spec_load
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import ResolvedSpec
from rebuild.review import store_lock

VALUES_FORMAT = "ams-kern-values/1"
TABLES_TTL_S = 10.0
KERN_LINE = "kern:"

Signature = tuple[int, int, int] | None


def file_signature(path: Path) -> Signature:
    """Return the file's inode, modification time and size, or None when it is missing."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (stat.st_ino, stat.st_mtime_ns, stat.st_size)


def kern_block_text(text: str) -> str:
    """Return a rune file's `kern:` block as written: the text from the line that opens with `kern:` to the end of the file, `kern` being the file's last key (rebuild/KERN-PLAN.md section 2), or the empty string when the file has none."""
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.startswith(KERN_LINE):
            return text[offset:]
        offset += len(line)
    return ""


def rune_blocks(spec: ResolvedSpec | None, runes_dir: Path, repo_root: Path) -> dict[str, dict]:
    """Return each rune file's `kern:` block by the rune's name: its repo-relative `file`, the block's raw `text` and that text's `sha256`, and its own `records` (inherited ones are its ligatures', not its own) with their `path`, `by` and `text` (`kern.describe`). The records come from the loaded spec, so a spec that did not load gives none."""
    blocks: dict[str, dict] = {}
    for path in sorted(Path(runes_dir).glob("*.yaml")):
        try:
            text = kern_block_text(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        rune = spec.runes.get(path.stem) if spec is not None else None
        try:
            file = str(path.relative_to(repo_root))
        except ValueError:
            file = str(path)
        blocks[path.stem] = {
            "file": file,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "text": text,
            "records": [
                {
                    "path": record.provenance.path if record.provenance is not None else "",
                    "by": record.by,
                    "text": kern.describe(record),
                }
                for record in (rune.kern.records if rune is not None else ())
            ],
        }
    return blocks


def live_values(resolution: kern.KernResolution) -> list[dict]:
    """Return every facing pair a record decides, with its value and its deciders, in `kern.tsv` order. A pair no record decides has the value 0 and is left out."""
    return [
        {
            "lead": pair.leading.name,
            "follow": pair.following.name,
            "by": pair.by,
            "deciders": [
                {
                    "file": decider.record.provenance.file if decider.record.provenance is not None else "",
                    "path": decider.record.provenance.path if decider.record.provenance is not None else "",
                    "inherited": decider.inherited,
                }
                for decider in pair.deciders
            ],
        }
        for pair in resolution.pairs
        if pair.deciders or pair.by
    ]


def built_values(m1_out: Path) -> dict:
    """Return the built font's values from the `kern-classes.json` beside it: `present`, and each facing pair whose value is not 0 as `[leading side, following side, by]`."""
    try:
        document = json.loads((Path(m1_out) / kern.KERN_CLASSES_JSON).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {"present": False, "values": []}
    if not isinstance(document, dict) or document.get("format") != kern.KERN_CLASSES_FORMAT:
        return {"present": False, "values": []}
    return {
        "present": True,
        "values": [
            list(row)
            for row in document.get("values", [])
            if isinstance(row, list) and len(row) == 3 and row[2]
        ],
    }


def failure_payload(message: str) -> dict:
    """Return `GET /kern`'s answer when it cannot be computed: no inventory, tables or values known, and `live.error` carrying `message`."""
    return {
        "format": VALUES_FORMAT,
        "inventory": {"present": False, "stamp": None, "tables_current": None, "manual_current": None},
        "tables": {"inputs": None, "current": None},
        "pass": None,
        "live": {"values": [], "error": {"message": message, "issues": []}},
        "built": {"present": False, "values": []},
        "runes": {},
    }


def check_values_payload(payload: object) -> list[str]:
    """Return every way `payload` breaks `GET /kern`'s shape, the module docstring's; an empty list means it holds. The app's node tests read a fixture this checks (`rebuild/review/fixtures/kern-values.json`)."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["the payload is not a JSON object"]
    if payload.get("format") != VALUES_FORMAT:
        errors.append(f"format is {payload.get('format')!r}, not {VALUES_FORMAT!r}")
    inventory = payload.get("inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("present"), bool):
        errors.append("inventory must say whether the file is there")
    elif inventory["present"] and not isinstance(inventory.get("stamp"), dict):
        errors.append("a present inventory must carry its stamp")
    tables = payload.get("tables")
    if not isinstance(tables, dict) or tables.get("current") not in (True, False, None):
        errors.append("tables must say whether they are current, or null when unknown")
    pass_holder = payload.get("pass")
    if pass_holder is not None and not isinstance(pass_holder, dict):
        errors.append("pass must be null or name the pass's pid")
    live = payload.get("live")
    if not isinstance(live, dict) or not isinstance(live.get("values"), list):
        errors.append("live must hold its values")
    else:
        for number, value in enumerate(live["values"]):
            if not (
                isinstance(value, dict)
                and isinstance(value.get("lead"), str)
                and isinstance(value.get("follow"), str)
                and isinstance(value.get("by"), int)
                and isinstance(value.get("deciders"), list)
            ):
                errors.append(f"live.values[{number}] must name two sides, a value and its deciders")
        error = live.get("error")
        if error is not None and (not isinstance(error, dict) or not isinstance(error.get("message"), str)):
            errors.append("live.error must be null or carry a message")
    built = payload.get("built")
    if (
        not isinstance(built, dict)
        or not isinstance(built.get("present"), bool)
        or not isinstance(built.get("values"), list)
    ):
        errors.append("built must say whether kern-classes.json is there and hold its values")
    runes = payload.get("runes")
    if not isinstance(runes, dict):
        errors.append("runes must map each rune to its kern block")
    else:
        for name, block in runes.items():
            if not (
                isinstance(block, dict)
                and isinstance(block.get("file"), str)
                and isinstance(block.get("text"), str)
                and block.get("sha256") == hashlib.sha256(block["text"].encode()).hexdigest()
                and isinstance(block.get("records"), list)
            ):
                errors.append(
                    f"runes.{name} must hold its file, its block's text and that text's sha256, and its records"
                )
    return errors


class KernRoutes:
    """The state behind the two routes: where to read, the cached tables, spec, glyphs and answer, and the time the tables' currency was last computed. `tables_inputs` gives the stamp the sources on disk would give the tables (`run_m1.tables_inputs` by default)."""

    def __init__(self, repo_root: Path, m1_out: Path, tables_inputs: Callable[[], str] | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.m1_out = Path(m1_out)
        self.runes_dir = self.repo_root / "glyph_data" / "runes"
        self.registry = self.repo_root / "rebuild" / "script.yaml"
        self.schema_dir = self.repo_root / "rebuild" / "schema"
        self.pass_lock = self.repo_root / "var" / "cycle" / "pass.lock"
        self._tables_inputs = tables_inputs
        self._lock = threading.Lock()
        self._tables_key: tuple | None = None
        self._tables: dict[str, tuple] | None = None
        self._tables_stamp: str | None = None
        self._tables_error: str | None = None
        self._spec_key: tuple | None = None
        self._spec: ResolvedSpec | None = None
        self._spec_error: str | None = None
        self._live_key: tuple | None = None
        self._live: dict | None = None
        self._current: tuple[float, str | None, bool | None] | None = None
        self._inventory: tuple[Signature, bytes | None, dict | None] | None = None
        self._future = None

    def _source_inputs(self) -> str:
        if self._tables_inputs is not None:
            return self._tables_inputs()
        from rebuild.pipeline import run_m1

        return run_m1.tables_inputs()

    def _table_paths(self) -> list[Path]:
        paths: list[Path] = []
        for config in conform.SETTLEMENT_CONFIGS:
            # The windows head records its head file's size and sha256, so a new head file comes with a new head.
            paths += [
                table_module.windows_head_path(self.m1_out, config),
                self.m1_out / f"joins-{config}.tsv",
            ]
        return paths

    def tables(self) -> tuple[dict[str, tuple] | None, str | None, str | None]:
        """Return every settlement configuration's tables as the kern stage reads them, their stamp, and the reason they cannot be read, reading them again only when one of their files moved."""
        key = tuple(file_signature(path) for path in self._table_paths())
        if key != self._tables_key:
            self._tables = None
            self._tables_stamp = None
            self._tables_error = None
            try:
                tables: dict[str, tuple] = {}
                stamps: dict[str, str] = {}
                for config in conform.SETTLEMENT_CONFIGS:
                    stamps[config], decision = table_module.read_windows_head(
                        self.m1_out, config, cells_only=True
                    )
                    tables[config] = (
                        decision,
                        table_module.read_join_tsv(self.m1_out / f"joins-{config}.tsv"),
                    )
                if len(set(stamps.values())) > 1:
                    self._tables_error = f"the tables under {self.m1_out} carry more than one stamp ({', '.join(f'{config}: {stamp}' for config, stamp in stamps.items())}), so they are no one build's; run the M1 build"
                else:
                    self._tables = tables
                    self._tables_stamp = next(iter(stamps.values()), None)
            except Exception as error:
                self._tables_error = f"the tables under {self.m1_out} cannot be read ({type(error).__name__}: {error}); run the M1 build"
            self._tables_key = key
        return self._tables, self._tables_stamp, self._tables_error

    def _spec_paths(self) -> list[Path]:
        return [
            *sorted(self.runes_dir.glob("*.yaml")),
            self.registry,
            *sorted(self.schema_dir.glob("*.json")),
        ]

    def spec(self) -> tuple[ResolvedSpec | None, tuple, str | None]:
        """Return the spec the rune files on disk load to, the signature of the files it was loaded from, and the loader's refusal, loading again only when one of those files moved."""
        key = tuple((str(path), file_signature(path)) for path in self._spec_paths())
        if key != self._spec_key:
            self._spec = None
            self._spec_error = None
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", spec_load.SpecWarning)
                    spec = spec_load.load_spec(self.runes_dir, self.registry, self.schema_dir)
                    runes = spec_load.m1_runes()
                    self._spec = spec if runes is None else spec_load.narrowed(spec, runes)
            except Exception as error:
                self._spec_error = f"the rune files do not load ({type(error).__name__}: {error})"
            self._spec_key = key
        return self._spec, key, self._spec_error

    def live(self) -> dict:
        """Return the `live` part of the answer: the values the rune files on disk resolve to over the tables on disk, or the reason they do not."""
        tables, _stamp, tables_error = self.tables()
        spec, spec_key, spec_error = self.spec()
        key = (self._tables_key, spec_key)
        if key == self._live_key and self._live is not None:
            return self._live
        if tables is None or spec is None:
            live = {"values": [], "error": {"message": tables_error or spec_error or "", "issues": []}}
        else:
            from rebuild.pipeline import run_m1

            try:
                glyphs = run_m1.mint_cell_glyphs(spec, tables)
                _bare, _copies, ss10_copies = run_m1.mint_raw_glyphs(spec)
                resolution = kern.resolve(spec, tables, glyphs, ss10_copies)
                live = {"values": live_values(resolution), "error": None}
            except kern.KernError as error:
                live = {
                    "values": [],
                    "error": {
                        "message": "the kern blocks on disk do not resolve over the tables on disk",
                        "issues": [list(issue) for issue in error.issues],
                    },
                }
            except Exception as error:
                live = {
                    "values": [],
                    "error": {
                        "message": f"the rune files on disk do not resolve over the tables on disk, which the next M1 build replaces ({type(error).__name__}: {error})",
                        "issues": [],
                    },
                }
        self._live_key = key
        self._live = live
        return live

    def tables_current(self, stamp: str | None) -> bool | None:
        """Return whether `stamp`, the tables' stamp, is the one the sources on disk give, recomputing that at most every `TABLES_TTL_S` seconds, or None when there are no tables."""
        if stamp is None:
            return None
        now = time.monotonic()
        if self._current is None or self._current[1] != stamp or now - self._current[0] >= TABLES_TTL_S:
            try:
                current: bool | None = self._source_inputs() == stamp
            except Exception:
                current = None
            self._current = (now, stamp, current)
        return self._current[2]

    def inventory_bytes(self) -> bytes | None:
        """Return the inventory's bytes as the build wrote them, or None when there is none, reading the file again only when it moved."""
        return self._read_inventory()[1]

    def _read_inventory(self) -> tuple[Signature, bytes | None, dict | None]:
        path = self.m1_out / kern_inventory.KERN_INVENTORY_JSON
        signature = file_signature(path)
        if self._inventory is None or self._inventory[0] != signature:
            try:
                body = path.read_bytes()
            except OSError:
                body = None
            stamp = None
            if body is not None:
                try:
                    document = json.loads(body)
                    stamp = document.get("stamp") if isinstance(document, dict) else None
                except ValueError:
                    stamp = None
            self._inventory = (signature, body, stamp if isinstance(stamp, dict) else None)
        return self._inventory

    def values_payload(self) -> dict:
        """Return `GET /kern`'s answer, as the module docstring lays it out. An exception while it is computed comes back as the same document with `live.error` naming it (`failure_payload`), never as a server error, so the view shows what went wrong and keeps polling."""
        with self._lock:
            try:
                return self._values_payload()
            except Exception as error:
                return failure_payload(
                    f"the server could not compute the kern values ({type(error).__name__}: {error})"
                )

    def _values_payload(self) -> dict:
        live = self.live()
        _tables, stamp, _error = self.tables()
        _signature, body, inventory_stamp = self._read_inventory()
        pages = kern_inventory.read_manual_pages(self.repo_root)
        holder = store_lock.holder(self.pass_lock)
        return {
            "format": VALUES_FORMAT,
            "inventory": {
                "present": body is not None,
                "stamp": inventory_stamp,
                "tables_current": (
                    None
                    if inventory_stamp is None or stamp is None
                    else inventory_stamp.get("tables") == stamp
                ),
                "manual_current": (
                    None
                    if inventory_stamp is None
                    else inventory_stamp.get("manual") == kern_inventory.manual_digest(pages)
                ),
            },
            "tables": {"inputs": stamp, "current": self.tables_current(stamp)},
            "pass": None if holder is None else {"pid": holder},
            "live": live,
            "built": built_values(self.m1_out),
            "runes": rune_blocks(self._spec, self.runes_dir, self.repo_root),
        }

    def values_bytes(self) -> bytes:
        return json.dumps(self.values_payload(), ensure_ascii=False).encode()

    def values_future(self):
        """Return the computation of `GET /kern`'s answer in the server's executor, shared by every request made while it runs."""
        from tornado.ioloop import IOLoop

        if self._future is None:
            future = IOLoop.current().run_in_executor(None, self.values_bytes)

            def done(_finished) -> None:
                self._future = None

            future.add_done_callback(done)
            self._future = future
        return self._future

    def handlers(self) -> list[tuple]:
        """Return the two routes' Tornado handlers, for `serve.main` to put before the static files."""
        from tornado.ioloop import IOLoop
        from tornado.web import RequestHandler

        routes = self

        class ValuesHandler(RequestHandler):
            def set_default_headers(self) -> None:
                self.set_header("Cache-Control", "no-store")

            async def get(self) -> None:
                try:
                    body = await routes.values_future()
                except Exception as error:
                    self.set_status(500)
                    self.set_header("Content-Type", "application/json")
                    self.finish(json.dumps({"error": f"{type(error).__name__}: {error}"}))
                    return
                self.set_header("Content-Type", "application/json; charset=utf-8")
                self.finish(body)

        class InventoryHandler(RequestHandler):
            def set_default_headers(self) -> None:
                self.set_header("Cache-Control", "no-store")

            async def get(self) -> None:
                body = await IOLoop.current().run_in_executor(None, routes.inventory_bytes)
                if body is None:
                    self.set_status(404)
                    self.set_header("Content-Type", "application/json")
                    self.finish(
                        json.dumps(
                            {
                                "error": f"no {kern_inventory.KERN_INVENTORY_JSON} under {routes.m1_out}; the M1 build writes it (make artifact-cycle)"
                            }
                        )
                    )
                    return
                self.set_header("Content-Type", "application/json; charset=utf-8")
                self.finish(body)

        return [(r"/kern", ValuesHandler), (r"/kern/inventory", InventoryHandler)]

# bench-the-rebuild

Measurement scaffolding for one open question: **is Python what makes the rebuild slow, and would a Rust or Go port help enough to be worth it?** `RUST-PORT-PLAN.md` holds the decision and the thresholds that settle it. A sibling question now lives here on the same isolation rules: **what occupies the rebuild's RAM high-water mark?** — `ram/` holds the attribution harnesses behind the tracker issue #50, and `rebuild/tools/peak_rss.py` is the one yardstick every RSS figure in this tree goes through. This file says what is here and how to run it.

Everything here reads the repo and writes only under this directory. Nothing in `make test`, `make all`, `make test-rebuild` or the artifact cycle touches it, and `bench-the-rebuild/` is in `MAKE_TEST_EXEMPT_PREFIXES` so editing a harness cannot re-arm gate:make-test. Pytest never collects it — `testpaths` is `test/` and `site/` — and pyright does not check it, since `[tool.pyright] include` names `tools`, `test`, `conftest.py` and `rebuild` only.

## Running it

Rust and Go are needed for the ports; everything else is `uv run`. Neither toolchain is required to build the font, run the tests, or drive the artifact cycle — only to re-measure the port's multiplier.

| Directory       | What it answers                                                                               | Entry point                         |
| --------------- | --------------------------------------------------------------------------------------------- | ----------------------------------- |
| `kernel-model/` | What a port of the settlement fixpoint would actually buy — same model in Python, Rust and Go | `zsh run.sh`                        |
| `scaling/`      | How the fixpoint grows with the alphabet, and what the port's true scope is                   | `uv run python scaling.py`          |
| `levers/`       | The six-configuration endpoint, and comparison trees at older revisions                       | `uv run python m1_all_configs.py`   |
| `compilers/`    | mypyc, Cython and PyPy on the same kernel                                                     | `zsh run.sh`                        |
| `freethreaded/` | Free-threaded 3.14t, and the thread-safety audit behind it                                    | `zsh setup.sh && zsh run.sh`        |
| `primitives/`   | Per-operation costs across the three languages                                                | `zsh run.sh`                        |
| `ink-and-tsv/`  | The two smaller port candidates: placed-ink and TSV parsing                                   | `zsh run.sh`                        |
| `cut-the-work/` | Coverage levers no language change can reach                                                  | `zsh run.sh`                        |
| `ram/`          | What occupies the RAM high-water mark — the attribution runs behind issue #50                 | `uv run python attr_fixpoint.py`    |

`ram/` holds three harnesses, one per attribution the tracker's sub-issues are gated on: `attr_fixpoint.py` (the settle fixpoint at a ladder rung, one process per rung), `attr_fold.py` (the rust-engine fold path — `kernel_io.read_transitions` plus `assemble_tables`), and `attr_fixtures.py` (what the rebuild suite's session fixtures materialize per xdist worker). Each phase records both the tracemalloc peak (compression-blind) and the process high-water, plus a top-allocation-sites table; `AMS_ATTR_TRACE=0` reruns any of them untraced for a clean high-water figure. Raw rows land in `ram/out/`; the curated record is `evidence/ram/`.

Three of those want a word of their own, because they are how a claim gets re-checked rather than re-argued. `levers/m1_all_configs.py` measures the real six-configuration `build_tables` stage — the same call `run_m1` makes, over the same in-process share — in three modes: `nostore` with no persisted memo at all, `fresh` with one present but distrusted, and `warm` with one primed. **`fresh` is the mode the endpoint is quoted in**; `nostore` is materially cheaper because it skips the memo writes, so comparing a `nostore` run against a recorded endpoint flatters it. Its warm mode refuses any output directory outside `levers/out/`, which is what stops a comparison tree's symlinked `rebuild/out` from writing over the real artifacts.

`levers/kernel_all_configs.py` is that endpoint's Rust arm: it dumps the live spec, hands it to `ams-m1-kernel enumerate-configs` over the same `conform.ACCEPTANCE_CONFIGS`, and prints rows in the same field idiom, so the two files' outputs read side by side. `--mode serial` is `--threads=1`, the arm comparable to the Python stage's serial loop; `--mode parallel` fans the configurations out, as wide as the machine allows unless `--threads` says otherwise. Streams go to files rather than through a pipe so the measurement is not charged for stdout plumbing, the child is what gets timed (`/usr/bin/time` for the peak resident set, `RUSAGE_CHILDREN` for the CPU, the kernel's own `[t]` lines for the per-configuration walls), and every row carries the sha256 of its stream so runs at different widths are comparable at a glance. One thing to know before running the parallel arm on a small box: the peak resident set is per configuration and a fixpoint's working set lives until it has emitted, so a full-width run wants roughly the serial peak times the configurations in flight. Do not size the box by a wide run's own reported peak either: when its system time dwarfs its user time, darwin is compressing pages to keep the run resident and the peak it reports understates what the run actually wanted, so the serial peak times the width stays the figure to plan against. `--rung N` cuts a rung of the scaling ladder instead and times the default configuration alone on it, which is the kernel arm of the ladder comparison `scaling.py`'s dump mode measures in Python.

`levers/mktree_at.sh <name> <ref>` builds a tree with `rebuild/pipeline` overlaid from any git ref, so "what did this lever buy" is a measurement rather than a memory. It uses `git archive` and never touches the index or working tree. Its companion `levers/apply_m1_patches.py` is now a record of the two patches that were measured and _not_ taken; it aborts against the live tree because the others landed, and that abort is the correct behavior rather than a bug to fix.

`kernel-model/run.sh` takes several minutes and starts with an ~80 s calibration against the real kernel. For a plumbing check that skips it:

```zsh
cd kernel-model && K1_LETTERS=9 K1_SKIP_REAL=1 zsh run.sh
```

Each harness prints one JSON object to stdout and leaves its raw output in its own `out/`, which is gitignored along with cargo targets, Go build products and the venvs the alternative-interpreter harnesses create.

## Why the kernel model is trustworthy

It is a model, not the real kernel, so it is worth knowing what makes its multiplier meaningful. All three implementations must produce the same window rows, the same cell count and the same FNV-1a checksum over a canonical rendering of every row — settled cell, adjustments, joint-floor flag, prospect value, carried extension. They must _also_ produce identical kernel call counters, which means they execute the same control flow rather than merely arriving at the same answer. `assemble.py` asserts this and reports `window_checksums_agree`, `distinct_answers_across_variants` and `share_is_answer_preserving`; a run that does not agree is not a measurement.

The model's fidelity against the real kernel — cost per kernel operation, and the call mix — is measured on every run rather than assumed, because that ratio is what the reported multiplier has to be discounted by. `real_kernel.py` is the calibration anchor.

Two results worth knowing before reading any speedup number, both of which argue against a naive port: CPython _beats_ both Rust and Go at inserting a ten-slot string key, because it caches string hashes; and a fast non-cryptographic hasher without a proper finalizer measured far slower than SipHash on these keys, whose low bits are a five-value alphabet. The prize is in packing the key, not in changing language.

## What is checked in, and for how long

- **Harness source** — the durable part. Kept as long as the question can be re-asked.
- **`fixtures/`** — real inputs extracted from the repo (memo keys, shaped runs, outlines, baseline rows) so the harnesses run without regenerating them. Live build inputs, not evidence.
- **`evidence/`** — the two written records (`cost-model.md`, `decision.md`), the machine-readable `decision.json`, and `raw/` mirroring the per-slice numeric trail the records cite, so every citation in them resolves. This is the proof pile for a still-open fork; per the repo's note-taking rules it goes when the fork closes.

`evidence/` is a snapshot of one study on one machine. Where it and a harness disagree, **the harness is right** — re-run it. Its numbers were measured before the Python levers landed and on different silicon, so they read high on the kernel side and are not comparable arm-to-arm with a re-run; that is expected, and it is why the records are left as a snapshot rather than edited in place. The levers and the class-grain deep slots have since landed and the sweep, the endpoints and the kernel model have all been re-measured against them, so treat every ratio in `evidence/` as a ratio against a kernel that no longer exists.

One caveat outlives the re-run and is worth reading before quoting any port figure. The model's three implementations still agree exactly, so the Rust-against-Python ratio remains a sound measurement of the model. But the real kernel has since gained memos on `candidates` and `transition_trace` that the model lacks, so the fidelity calibration — which divides cost by call count on both sides — now compares a kernel whose calls are mostly memo hits against a model whose calls all do full work. The discounted band it produces still looks like the old one; that is a coincidence, not a confirmation. `RUST-PORT-PLAN.md` carries the decision about how to repair it.

The rendered report is `evidence/rewrite-decision-report.html`, with an interactive alphabet-headroom instrument. Open it directly in a browser.

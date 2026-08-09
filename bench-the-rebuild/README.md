# bench-the-rebuild

Measurement scaffolding for one open question: **is Python what makes the rebuild slow, and would a Rust or Go port help enough to be worth it?** `RUST-PORT-PLAN.md` holds the decision and the thresholds that settle it. This file says what is here and how to run it.

Everything here reads the repo and writes only under this directory. Nothing in `make test`, `make all`, `make test-rebuild` or the artifact cycle touches it, and `bench-the-rebuild/` is in `MAKE_TEST_EXEMPT_PREFIXES` so editing a harness cannot re-arm gate:make-test. Pytest never collects it — `testpaths` is `test/` and `site/` — and pyright does not check it, since `[tool.pyright] include` names `tools`, `test`, `conftest.py` and `rebuild` only.

## Running it

Rust and Go are needed for the ports; everything else is `uv run`. Neither toolchain is required to build the font, run the tests, or drive the artifact cycle — only to re-measure the port's multiplier.

| Directory       | What it answers                                                                               | Entry point                         |
| --------------- | --------------------------------------------------------------------------------------------- | ----------------------------------- |
| `kernel-model/` | What a port of the settlement fixpoint would actually buy — same model in Python, Rust and Go | `zsh run.sh`                        |
| `scaling/`      | How the fixpoint grows with the alphabet, and what the port's true scope is                   | `uv run python scaling.py`          |
| `levers/`       | The keep-the-Python stack: gc, six memoizations, a NamedTuple                                 | `uv run python apply_m1_patches.py` |
| `compilers/`    | mypyc, Cython and PyPy on the same kernel                                                     | `zsh run.sh`                        |
| `freethreaded/` | Free-threaded 3.14t, and the thread-safety audit behind it                                    | `zsh setup.sh && zsh run.sh`        |
| `primitives/`   | Per-operation costs across the three languages                                                | `zsh run.sh`                        |
| `ink-and-tsv/`  | The two smaller port candidates: placed-ink and TSV parsing                                   | `zsh run.sh`                        |
| `cut-the-work/` | Coverage levers no language change can reach                                                  | `zsh run.sh`                        |

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

`evidence/` is a snapshot of one study on one machine. Where it and a harness disagree, **the harness is right** — re-run it. Numbers in the written records were measured before the Python levers landed, so they will read high the moment step 1 of the plan is done; that is expected and is the point of re-baselining.

The rendered report is `evidence/rewrite-decision-report.html`, with an interactive alphabet-headroom instrument. Open it directly in a browser.

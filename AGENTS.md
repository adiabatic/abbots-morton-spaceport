# Instructions for agents

## Roadmap and open decisions

- `WHATNEXT.md` is the live frontier: what ought to happen next and the open forks. Read it when a task needs that context.
  - When work lands, edit its state in place and delete what it supersedes. It is never a log; its “Keeping this file honest” block is the rule.

## Note-taking and the rebuild logs

Git holds the history, so a checked-in note earns its place only by recording the live frontier, a durable design fact, or the user’s rationale. `doc/note-taking.md` is the full form of these rules, with the homes each kind of record goes to.

- A milestone gets a forward `*-PLAN.md` under `rebuild/`, never a `*-REPORT.md`. Its record on landing is the commit history and the runes’ `why:` fields.
- An in-flight batch keeps at most one progress file, and it stays bounded.
  - It holds only what is parked and why, design overrides, the verification recipe, and the resume commands; commit lists and verification tallies belong to git.
  - Delete it when the batch closes, lifting any surviving forward pointer into `WHATNEXT.md`.
- A count in prose names the artifact that reports it, never the number; definitional numbers are not counts (`doc/note-taking.md` lists them).
- When a fact has an executable authority (a validator, a gate’s exempt list, a ledger, a keyboard map), the note names the authority and describes the shape instead of restating it.
- Reconnaissance and evidence dumps are scratch once the decision they informed has landed; keep evidence checked in only for a still-open fork or a live build input.
- Notes, docstrings, and code comments state what exists, in present tense.
  - No dated `Update (…)` stamps, no “used to” / “no longer” / “retired” / “now” narration; rewrite the current state in place.
  - The before-and-after belongs in the commit message.

## General

- Ask clarifying questions when there are multiple valid ways to do something.
- Never write a `why:` in `glyph_data/runes/qs*.yaml`.
  - There, `why:` is the user's rationale in the user's voice, and its durable home: other files may point at a `why:` but never re-narrate it.
  - The rule is scoped exactly that narrowly: the `why:` fields in `rebuild/m1-contact-allow.yaml` and `rebuild/m1-divergences.yaml` are agent-written.
    - Draft them yourself in the surrounding idiom; "Human-reviewed" there means the user decides whether the entry belongs, not who drafts the sentence.
  - Both ledgers hash prose-blind, so rewording a `why:` re-runs no gate.
- When a request names a letter ("after ·Pea") and the family has several variants (half/full, alt, `en-y6`, `ex-y0`, …), don't default to a bare `{family: qsX}` selector: enumerate the variants from `glyph_data/quikscript.yaml` and ask which subset is meant.
- Say what you mean: mannered prose substitutes metaphor and flourish for direct statement ("a dial worth turning" for "a parameter worth varying"), makes the reader work harder, and drags in connotations the writer did not choose. When a literal phrase is available, use it.
- After any glyph or code change, run `make test`. `doc/testing.md` maps every gate to the change it answers for.
  - `make test-rebuild` gates anything under `rebuild/` and `make kernel-gate` a kernel-semantics change. The validators lane refuses stale artifacts rather than rebuilding them, so run the M1 build after a rune or pipeline edit.
- Codex's sandbox fails one contracts-lane probe test by blocking `sysctl`; rerun it outside the sandbox and never weaken it (`doc/testing.md` names it).
- Detach any pass that includes the heavy gates (the rebuild suite and the conform sweep, so any `make artifact-cycle` or `make review-cycle` pass) from the shell's lifetime and watch `tmp/build-logs/latest/`; `doc/running-long-steps.md` has the recipe and the hung-run traps.
- The M1 table build runs in Rust: `rebuild/kernel-rs/` is the engine of record, `cargo` is a hard prerequisite beside `uv` for anything that builds tables (the rebuild suite included), and a settlement-semantics change is written once, in the crate. `rebuild/pipeline/kernel_exec.py` is the seam and its docstring the map of it; `doc/rebuild-design.md` §14.1 holds the design facts.
- Never commit without explicit user approval: show the changes and wait for the go-ahead.
- At a natural commit point, spawn a fresh sub-agent to draft commit-message suggestions and present them for approval.
- In this repository only: multiline commit messages are fine, though not mandatory, and no worktrees unless explicitly asked.
- Commit messages describe the author/reader experience ("Make tables.html store state in the URL, not localStorage") or how the letters look different ("Reduce the half-·He extension at the x-height", "Don't join ·Way·Thaw ever"), never the mechanism.

## Prose style

- Use American English in code and comments, even though The Manual and other parts of the project are British.
- Capitalize **Tall**, **Deep**, and **Short** wherever they name the height classes of letters (defined under “Specific background information”). Lowercase them only in an ordinary, non-class sense.
- Never abbreviate "isolated" / "isolation" to "iso" in any casing — in prose, identifiers, dataclass fields, or YAML keys. Spell it out: `isolated_form`, `isolated_left`, "in isolation".
- Don't hard-wrap comments or docstrings; one long line per paragraph.
- Write joins between letter names in `data-expect` notation and no other: `~b~` for a baseline join, `~x~` for an x-height join, `|` for a break, `+` for a ligature, stance suffixes on the letter (`·It ~b~ ·Day.half`). `doc/data-expect.md` documents the grammar; `parse_expect` in `test/test_shaping.py` is the authority.
  - A bare `~` is not an operator — never write `·Tea~·Utter`. Spell the height out, or when the sentence only means the pair or the seam, say that in prose ("the ·Tea·Utter seam").

## HTML/CSS/JS

- Prefer nested CSS over flat CSS.
- Prefer for-of loops to `.forEach()`.
- Use modern range syntax for media/container queries: `(width > 40em)` not `(max-width: 40em)`.

## Performance

- The Python and Rust here run against hard CPU and RAM limits: a routine that holds a little more per unit narrows a fan-out and lengthens every cycle after it. `doc/parallelism.md` has the policy and the coding guidance.
  - A change that moves a per-unit peak revises the `*_BYTES` constant and the docstring that argues it at the same call site; nothing else checks the widths derived from it.

## Python

- IMPORTANT: Always `uv run`, never bare `python`, `python3`, or `pytest`. `[tool.uv] cache-dir` in `pyproject.toml` keeps the cache inside the repo so sandboxes can write it.
- IMPORTANT: Never single-thread the test suite; the worksteal pool is many times faster than a serial run.
  - Run the whole suite with `make test`. Run any multi-file selection with:

    ```sh
    uv run pytest <targets> -n auto --dist worksteal
    ```

  - Reserve `-n 0` for a single test id or a tiny handful where you want an unscrambled traceback. Never spell it `-p no:xdist`: the root `conftest.py` defines xdist hooks, so disabling the plugin kills collection before any test runs.
  - When delegating test runs to sub-agents, tell them this explicitly.
- Fan-out widths derive from the box at run time; `rebuild/tools/memory_budget.py` is the authority on the arithmetic and `doc/parallelism.md` maps each width to its constant, its call site, and the environment variable that overrides it — never edit the checked-in values for a one-off run.
- IMPORTANT: After any Python change, run `make prettier`.
- Pyright covers the whole Python tree, `rebuild/` included. Run it bare, with no paths — `[tool.pyright] include` in `pyproject.toml` is the single authority for what is checked.
  - Suppress with `# pyright: ignore[reportSpecificRule]`, always naming the rule. Never `# type: ignore[...]`, which pyright reads as a blanket whole-line suppression.

## General background information

- “Orthodox” is Quikscript-speak for “English written in the Latin script”.
- Quikscript letters, when they’re being referred to as letters, are prefixed with `·`.

## Specific background information

- Height classes by bitmap rows: Tall is 9, Short is 6, Deep is 9 with `y_offset: -3`.
- See @doc/glyph-names.md for the map between a letter’s name (·Pea), PostScript family name (qsPea), and code point (U+E650).

## Adding glyphs

- A glyph added to any YAML under `glyph_data/` that uses a standard PostScript name (not `uniXXXX`) also needs an entry in `postscript_glyph_names.yaml`.
- Keep all glyphs alphabetized by code point (`uniXXXX`).
- “Ink” is a filled bitmap pixel — a `#` cell. The cursive-attachment tooling’s vocabulary (a row’s “leftmost-ink column”, “no ink at y=N”, `exit_ink_y`) follows from it.
- Bitmap rows are double-quoted strings, with a bare trailing `#` comment marker on the rows whose glyph-space `y` is 5 and 0; which rows those are depends on `y_offset`.

## Locations of things

- Quikscript source data lives under `glyph_families` in `glyph_data/quikscript.yaml`, with separate `mono`, `prop`, `shapes`, and `stances` records.
  - `entry` / `exit` anchors (under `anchors:`) go in the proportional record or stance that compiles into the proportional font; mono-only records carry no `curs` anchors.
  - Shared bitmaps go under a family’s `shapes`; contextual/alternate stances go under `stances` with `anchors`, `select`, `derive`, `traits`, and `modifiers`.
  - Keep `traits: [alt]` and `traits: [half]` when those concepts are real; other suffixes belong under ordered `modifiers`.

## Dev-server URLs

- `make serve` runs `tools/serve.py`, serving `site/` on `http://localhost:7293/`.
- `make review-serve` serves the rebuild review surface on `http://localhost:7294/`.
- When the user asks whether everything is ready to verdict, run `make verdict-ready` — never reason it out from the git log. The app’s banner shows the same status.
- `make review-cycle` is the hands-off loop: the artifact cycle, then serve (`SERVE=0` or `SERVE=bg` for a caller that must terminate). `make artifact-cycle` is the same verification without the serve, the form for commit time.
  - `doc/review-cycle.md` is the runbook: what a pass skips, which fingerprints are prose-blind, when the server comes down, the verdict store and journal, retention, logs, and timings.
  - Every non-rehearsal pass rewrites `rebuild/review-census-pins.json` and prints its diff; committing that diff accepts the census, so read the `invariant` block before committing.

## YAML files

- Stance keys (`alt_reaches_way_back`, `entry_xheight`) are local labels; compiled identity and compatibility come from each stance’s explicit `traits` and `modifiers`, and selectors in `select` / `derive` can combine all three.
- Keep `{family: qsX}` entries in code-point order (qsPea … qsOoze, per `postscript_glyph_names.yaml`) in `select` / `derive` lists and `context_sets`. Ligatures sort by the lead family, bare lead first.
- Prefer `inherits` over copying a stance; clear an inherited nested key with `null`.
- `doc/quikscript-yaml-conventions.md` holds the old engine’s selector, ligature, and `ex-noentry` mechanism, and the recipe that proves a selector change is a pure cleanup.

### Ductus

`ductus` says how a letter is drawn by hand: pen direction, stroke order, where the stroke enters and exits. `notes` records join constraints, never how-to-draw.

- The canonical `ductus` sits at the family level, a sibling of `mono` / `prop` / `shapes` / `stances`; it covers `mono` and `prop` at once because the motion is the same even when the bitmaps differ. Never hang it off the `mono` record.
- Several valid drawing orders for one bitmap go in that single family-level block scalar as `-` bullets (see `qsIt`).
- A `shapes:` or `stances:` entry gets its own `ductus` only when it is drawn differently from the family default (see `qsFee.shapes.connect_from_short_height`).
  - A variant’s `ductus` fully replaces the family one, so write it as a complete stroke description; a variant without one inherits.

### Formatting

- `tools/reflow_yaml.py` is the authority on flow-vs-block style and picks a policy by path; its docstring states both.
- Reflow after editing; the pass is idempotent, so a second run is a no-op:

  ```sh
  uv run python tools/reflow_yaml.py                       # quikscript.yaml plus every rune
  uv run python tools/reflow_yaml.py glyph_data/runes/qsMay.yaml
  ```

### Selectors

- For “every letter with an anchor at y=N” use `{exit_y: N}` / `{entry_y: N}`, with `except:` to drop families, instead of a hand-curated `context_set`.
- A family-scoped anchor selector (`{family: qsMay, exit_y: 5}`) narrows a family to the variants with a compatible anchor at that Y. `tools/suggest_scoped_anchor_selectors.py` finds candidates.
- Never narrow a broad family selector, or swap a long family list for an anchor selector, without proving the generated Senior feature code is byte-identical; the recipe is in `doc/quikscript-yaml-conventions.md`. A divergence is a shaping change to surface for review, never a cleanup.

### Anchors

- `exit.x = max_ink_x_at_exit_y + 1`: one pixel right of the stroke, which can land inside the bitmap when the stroke exits from the left or middle (·He, ·Ye, `qsThey.ex-y5`).
- `entry.x = min_ink_x_at_entry_y`: on the leftmost ink of the entry row, so an inset stroke lands at its actual ink rather than `x = 0`.
- `tools/audit_anchor_geometry.py` reports every deviation; its docstring names the derived buckets (`*.en-con-1`, `*.en-trim-N`) that deviate by design.

## Procedures that live in skills

- A one-line join tweak to the old font's `glyph_data/quikscript.yaml` edits an existing stance rather than authoring a new one.
  - Patterns and worked examples: the tweak-an-old-font-join skill (`.claude/skills/tweak-an-old-font-join/SKILL.md`); the rebuild's runes take expand-or-contract-a-join instead.
- Version bumps: the bump-major and bump-minor skills edit both version files, refresh the lockfile, and open the `FONTLOG.md` heading.
- Review sittings: `review-docket` to prepare one, `dont-bug-me-about-this-ever-again` for a standing approval, `just-verdicted-now-what` for the aftermath.

## Visual before/after diffs

- `site/check.html` is the side-by-side harness for eyeballing glyph changes: `make check-html-before` on the baseline captures the OTFs into `site/before/` (gitignored); `make check-html-after` after the change rebuilds the page.
- `tools/build_check_html.py` generates the whole page, so never hand-edit it; `doc/isolation-leaks.md` documents its auto-generated sections.

## Markdown-document style

- Sentence case for titles.
- No hard-wrapping: each paragraph or list item is one line.
- Tables formatted for humans, with lined-up columns.
- `markdownlint-cli2` must pass; `.markdownlint-cli2.yaml` is the config.

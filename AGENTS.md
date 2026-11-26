# Instructions for agents

## Roadmap and open decisions

- `WHATNEXT.md` is the live frontier: what ought to happen next and the open forks. Read it when a task needs that context.
  - When work lands, edit its state in place and delete what it supersedes. It is never a log.

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
  - The rule is scoped exactly that narrowly: the `why:` fields in `rebuild/m1-contact-allow.yaml` are agent-written.
    - Draft them yourself in the pattern of the surrounding entries; "Human-reviewed" there means the user decides whether the entry belongs, not who drafts the sentence.
  - The allow-list's hash ignores prose, so rewording a `why:` re-runs no gate.
- When a request names a letter ("after ·Pea") and the letter has several stances (half/full, alt, …), don't default to the whole letter: enumerate the stances from its rune file (`glyph_data/runes/qsX.yaml`) and ask which subset is meant.
- A change to a letter's entry does not reach the ligatures that begin with it: a ligature rune (`glyph_data/runes/qsX_qsY.yaml`) keeps its own entry-side records, and right-facing selectors (`toward:`, `when.right`, `then:`) name each ligature explicitly (`doc/rebuild-design.md` §3.4). Decide for each such ligature whether the change belongs on it too.
  - Make the same change to the ligature without asking when it enters with the letter's own stroke at that height (·Day+Utter.half begins with ·Day.half's foot hook) and it carries a matching record or already appears beside the letter in the selector being changed. Name the ligature in the summary and the commit message.
  - Ask when the ligature enters differently, or when a record or `why:` of its own argues for keeping the difference the change would remove.
  - The exit side mostly spreads by itself: left-facing selectors match every ligature that ends in the named letter, and a ligature inherits its last letter's exit-side records beside its own (`doc/rebuild-design.md` § Preserved outgoing strokes). Check only that none of the ligature's own records at that height keeps the old behavior.
- A join change is pair-wide: the two letters that bound the space decide it, never the letter's word position. Never author word-initial, word-final, or isolated special treatment (`word:`, a `self:` guard that only a word edge satisfies, a follower-scoped carve-out) unless The Manual mandates it. A mechanical constraint that happens to exclude a cell (a `bind:` that cannot coexist with an onward exit) is not a treatment, but say so in the commit message.
- Say what you mean: mannered prose substitutes metaphor and flourish for direct statement ("a dial worth turning" for "a parameter worth varying"), makes the reader work harder, and drags in connotations the writer did not choose. When a literal phrase is available, use it.
- After any glyph or code change, run `make test`. `doc/testing.md` maps every gate to the change it answers for.
  - `make test-rebuild` gates anything under `rebuild/` and `make kernel-gate` a kernel-semantics change. No rebuild test reads a live artifact; the build checks its own tables (the rule certificates among them), so run the M1 build after a rune or pipeline edit.
  - Once a rune edit's M1 build is green, run `make artifact-cycle` without being asked, so the review app's Queue already shows the edit when the user returns to reviewing. Present the cycle's `review diff:` line and the units it counts with the rest of the change.
  - Accept those units with `make review-accept ARGS=--apply` only when the user says so, and commit the `rebuild/review-snapshot.json` it rewrites with the edit it accepts.
- Codex's sandbox fails one contracts-lane probe test by blocking `sysctl`; rerun it outside the sandbox and never weaken it (`doc/testing.md` names it).
- Detach any pass that includes the heavy gates (the rebuild suite and the conform sweep, so any `make artifact-cycle` or `make review-cycle` pass) from the shell's lifetime and watch `var/build-logs/latest/`; `doc/running-long-steps.md` has the recipe, the hung-run traps, and how to stop a pass whole.
- `tmp/` is scratch and may be wiped between change sets; `var/` (gitignored too) holds what outlives a run: the cycle's build logs and hand-kept archives under `var/keep/`. Anything that must survive a `tmp/` wipe goes under `var/`.
- The M1 table build runs in Rust: `rebuild/kernel-rs/` is the engine of record, `cargo` is a hard prerequisite beside `uv` for anything that builds tables (the rebuild suite included), and a settlement-semantics change is written once, in the crate. `rebuild/pipeline/kernel_exec.py` is the kernel interface and its docstring the map of it; `doc/rebuild-design.md` §14.1 holds the design facts.
- Never commit without explicit user approval: show the changes and wait for the go-ahead.
- When creating a GitHub tracking issue with sub-issues, add the sub-issues in the order that suits the set (priority or dependency order, whichever fits) and do not repeat the list in the tracking issue body; the sub-issue list is the list.
- Never write a machine's hostname in code, docs, commit messages, or GitHub issues and pull requests. Name a machine by its `doc/fleet.md` row instead ("the M4 Pro Mac mini").
- At a natural commit point, spawn a fresh sub-agent to draft commit-message suggestions and present them for approval.
- In this repository only: multiline commit messages are fine, though not mandatory, and no worktrees unless explicitly asked.
- Commit messages describe the author/reader experience ("Make tables.html store state in the URL, not localStorage") or how the letters look different ("Reduce the half-·He extension at the x-height", "Don't join ·Way·Thaw ever"), never the mechanism.
  - When several letters fill the same slot of a join, consolidate them with braces inside one dot: `` Stop adding a pixel to the `·{Pea,Tea,He} ~x~ ·Eight` join ``, not "the ·Pea, ·Tea or ·He ~x~ ·Eight join".
  - When a letter and the ligatures that begin with it fill the same slot, brace the tails after the shared lead, with an empty first alternative for the bare letter: `` Contract `·Bay ~b~ ·Vie{,+Utter}` by a pixel ``, not "Contract `·Bay ~b~ ·Vie` and `·Bay ~b~ ·Vie+Utter` by a pixel".

## Workflows

- When using a workflow to do a bunch of things (say, a bunch of GitHub sub-issues), ignore the “don’t commit” instruction and commit at will.
- Feel free to use worktrees to parallelize work when using a workflow to do a bunch of things.
- When using workflows to do things, make sure to clean up generated files in between tasks.
- When using a workflow with multiple steps, add a way for me to signal “gracefully finish the sub-issues you’re working on, but don’t start any more”.
- Use as many sub-agents as you like in a workflow, but no more than 12 total runnning concurrently at a time (I don’t want to run out of my 5h limit and get paused). Don’t skimp on testing/judging/verifying-type agents. If that means the best idea is to fix sub-issues one at a time, then do that.
- Don’t forget closing keywords in the body of a commit that’s designed to fix a GitHub Issue.

## Prose style

- Use American English in code and comments, even though The Manual and other parts of the project are British.
- Capitalize **Tall**, **Deep**, and **Short** wherever they name the height classes of letters (defined under “Specific background information”). Lowercase them only in an ordinary, non-class sense.
- Never abbreviate "isolated" / "isolation" to "iso" in any casing — in prose, identifiers, dataclass fields, or YAML keys. Spell it out: `isolated_form`, `isolated_left`, "in isolation".
- Don't hard-wrap comments or docstrings; one long line per paragraph.
- Write joins between letter names in `data-expect` notation and no other: `~b~` for a baseline join, `~x~` for an x-height join, `|` for a break, `+` for a ligature, stance suffixes on the letter (`·It ~b~ ·Day.half`). `doc/data-expect.md` documents the grammar; `parse_expect` in `rebuild/validation/expect.py` is the authority.
  - A bare `~` is not an operator — never write `·Tea~·Utter`. Spell the height out, or when the sentence only means the pair or the junction, say that in prose ("the ·Tea·Utter junction").
- Always present suggested commit messages and join notation in inline code or fenced code blocks so Markdown preserves every literal character, including tildes.
  - When presenting several candidate commit messages, give each its own fenced block under a second-level heading that holds only its number in parentheses (`## (1)`, `## (2)`, …), numbered straight through every candidate, with no other label. Any comment on a candidate goes after its block.

## HTML/CSS/JS

- Prefer nested CSS over flat CSS.
- Prefer for-of loops to `.forEach()`.
- Use modern range syntax for media/container queries: `(width > 40em)` not `(max-width: 40em)`.

## Performance

- The Python and Rust here run against hard CPU and RAM limits: a routine that holds a little more per unit narrows a fan-out and lengthens every cycle after it. `doc/parallelism.md` has the policy and the coding guidance.
  - A change that moves a per-unit peak revises the `*_BYTES` constant and the docstring that argues it at the same call site; nothing else checks the widths derived from it.
- Every gate sizes its pool to the whole box, so two gates running at once oversubscribe it. When a workflow fans work out across agents, parallelize the editing and serialize the testing: one `make test` / `make test-rebuild` / cycle pass at a time across all agents, never one per agent. `doc/parallelism.md` says why.

## Python

- IMPORTANT: Always `uv run`, never bare `python`, `python3`, or `pytest`. `[tool.uv] cache-dir` in `pyproject.toml` keeps the cache inside the repo so sandboxes can write it.
  - uv resolves that `cache-dir` against the working directory, so a `uv run` started in a subdirectory starts a separate cache there. A Claude Code session gets `UV_CACHE_DIR` pinned to the checkout root's `.uv-cache` by the `SessionStart` hook in `.claude/settings.json`; any other agent runs `uv` from the repository root.
- IMPORTANT: Never single-thread the test suite; the worksteal pool is many times faster than a serial run.
  - Run the whole suite with `make test`. Run any multi-file selection with:

    ```sh
    uv run pytest <targets> -n auto --dist worksteal
    ```

  - Reserve `-n 0` for a single test id or a tiny handful where you want an unscrambled traceback. Never spell it `-p no:xdist`: the root `conftest.py` defines xdist hooks, so disabling the plugin kills collection before any test runs.
  - When delegating test runs to sub-agents, tell them this explicitly, and that only one of them runs a gate at a time.
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

- Each Quikscript letter and ligature is a rune file under `glyph_data/runes/`, whose grammar is `rebuild/schema/rune.schema.json`: its `mono:` block is the Mono drawing, and its `stances:` are the proportional shapes, each a `bitmap` with its join surface.
  - Keep `traits: [alt]` and `traits: [half]` when those concepts are real; anything else that distinguishes a stance lives in named bitmaps or policy records.
- The other glyphs, the metadata and the Latin kern rules are the other `glyph_data/*.yaml` files, and the variation sequences and stylistic sets are in the registry, `rebuild/script.yaml`.

## Dev-server URLs

- `make serve` runs `tools/serve.py`, serving `site/` on `http://localhost:7293/`.
- `make review-serve` serves the rebuild review corpus on `http://localhost:7294/`.
- When the user asks whether everything is ready to verdict, run `make verdict-ready` — never reason it out from the git log. The app’s banner shows the same status.
- `make review-cycle` is the hands-off loop: the artifact cycle, then serve (`SERVE=0` or `SERVE=bg` for a caller that must terminate). `make artifact-cycle` is the same verification without the serve, the form for commit time.
  - `doc/review-cycle.md` is the runbook: what a pass skips, which hashes ignore prose, how the server stays up through a pass, the verdict store, the accept, retention, logs, and timings.
  - The cycle builds and serves the table-diff corpus: the accepted snapshot `rebuild/review-snapshot.json` names against the working tree's build. A green pass ends with a `review diff:` line counting the unaccepted changes, which never fails the pass; `make review-accept ARGS=--apply` accepts them once none is blank or rejected.

## YAML files

### Ductus

`ductus` says how a letter is drawn by hand: pen direction, stroke order, where the stroke enters and exits. `notes` records join constraints, never how-to-draw.

- A rune's `ductus:` is its top-level map of motions, one entry each, and every stance names the motion it draws with `motion:`; `spec_load` checks that every motion has a stance and every stance a motion.
- Several valid drawing orders for one motion go in that motion's prose as `-` bullets (see `qsIt`).
- A stance gets a motion of its own only when it is drawn differently; ink that changes only at a join is a binding on the same motion (`rebuild/M1-PLAN.md` §8). Join constraints are never ductus: they are `pairings` and `unlocks` data.

### Formatting

- `tools/reflow_yaml.py` is the authority on the runes' flow-vs-block style; its docstring states the rule.
- Reflow after editing; the pass is idempotent, so a second run is a no-op:

  ```sh
  uv run python tools/reflow_yaml.py                       # every rune
  uv run python tools/reflow_yaml.py glyph_data/runes/qsMay.yaml
  ```

- Only the runes are kept in reflowed form, so the tool refuses any other YAML file.

## Procedures that live in skills

- Lengthening or shortening a join that already happens: the expand-or-contract-a-join skill.
- Version bumps: the bump-major and bump-minor skills edit both version files, refresh the lockfile, and open the `FONTLOG.md` heading.
- Review sessions: `just-verdicted-now-what` turns a finished review's rejects into rune changes.

## Markdown-document style

- Sentence case for titles.
- No hard-wrapping: each paragraph or list item is one line.
- Tables formatted for humans, with lined-up columns.
- `markdownlint-cli2` must pass; `.markdownlint-cli2.yaml` is the config.

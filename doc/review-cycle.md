# The review cycle

This is the operator's guide to the artifact cycle: what a pass does, what it skips, what it writes while the review app is running, and what it leaves on disk. The module docstring of `rebuild/tools/artifact_cycle.py` is the authority on the plan a pass resolves, and the Makefile comments above the targets named here are the authority on each target's flags. To prepare a sitting on the surface, use the `review-docket` skill. To turn a repeated verdict into a checked-in rule, use `dont-bug-me-about-this-ever-again`. To act on a sitting's rejects, use `just-verdicted-now-what`.

## The commands

```sh
make artifact-cycle              # the verification alone; the form to run at commit time
make review-cycle                # the same pass, then serve the surface
make review-cycle SERVE=0        # same pass; prints the serve command instead of serving, so the target terminates
make review-cycle SERVE=bg       # same pass; starts the server detached if it is not running, and waits for the port
make verdict-ready               # the readiness checklist; the app's banner shows the same status
make review-serve                # serve rebuild/out/review/ on http://localhost:7294/
make cycle-timings               # what each step costs on this machine; ARGS='--by-step', '--by-outcome', '--inner'
```

Flags are passed to the cycle through `ARGS`. `--verdicts <file>` names the master to carry, `--fresh` runs every stage, `--force-make-test` forces that one gate, `--skip-gates` and `--no-merge` narrow a pass, `--keep-history` skips retention, and `--stop-server` (which `make review-cycle` passes) permits the pass to stop the server and take the port.

`make review-cycle` serves in the foreground and never exits, so a caller that waits for the command to finish, such as an agent harness, uses `SERVE=0` or `SERVE=bg`. Stop a detached server the way the cycle does:

```sh
pkill -f 'rebuild\.review\.serve'
```

## What a pass does

- It rebuilds the M1 font and tables, rebuilds the review surface, runs the verdict chain, refreshes the census pins, runs the gates, and compares the checked-in per-unit memory peaks with what this machine measured. On a green finish it prunes the regenerable outputs.
- Every heavy stage is skipped when its green record shows its inputs are unchanged since its last green run, so a pass over unchanged inputs takes seconds. A gate that did not run is unverified, not waived: `make verdict-ready` reports NOT READY until it runs.
- A green pass ends by running the readiness checklist, so nothing needs to run after it. `make verdict-ready` runs the same checklist on its own.
- When only comparison-side inputs changed, the pass re-runs the gates over the artifacts already on disk instead of rebuilding them. `doc/testing.md` § Re-adjudicating without a build gives the recipe and lists those inputs.

### The census

Every non-rehearsal pass rewrites `rebuild/review-census-pins.json` from the census sidecar the surface build writes, and compares the result with the copy in the git index. The checked-in file is the last accepted census, and committing the rewritten file accepts the new one. The summary's `census pins` line says what that commit would accept:

- The `volatile` block holds totals that change with every migrated letter. When only this block changed, the line reads `invariant unchanged`, no diff is printed, and `rebuild/out/cycle_summary.json` holds the surface's totals.
- The `invariant` block records which classes the surface ships, which ones the build machine-approves, which are exempt from individual verdicts, and which verdict families the corpus reaches. When it changed, the line names the change (`classes +1 (…)`, `machine-approved -1 (…)`, `families +1 (…)`) and the block's own diff is printed under the census banner, without the volatile hunks. This is the diff to read carefully before committing.
- The `census reach` line compares the ledger's `ink_identical` and `no_verdict` declarations with what the corpus reached. It reports how many classes approve units and which of those the ledger never declared, which declared classes approve none, and which ledger entries and exemptions no unit reached. Neither the ledger nor the pins shows this by itself. `census.reach` in `rebuild/review/census.py` is the authority, and `cycle_summary.json` stores the sets under `census_reach_sets`.

## What is prose-blind

The fingerprints for the table build, the conform sweep, and the rebuild suite's one lane (`contracts`) ignore prose in rune files and ledgers, so rewording those triggers no heavy rebuild. Code-file prose is covered in the last item below. `rebuild/pipeline/fingerprint.py` is the authority. In outline:

- Rune files under `glyph_data/runes/` are hashed by `rune_file_digest`, which ignores YAML comments, formatting, and the documentation fields (`ductus`, `notes`, and every `why`, including those on refuse records).
- A refuse record's `why` is the only rune prose any later step reads: the surface's explain panel quotes it. It is hashed into `rune_explain_digest` and the surface's Stage B `explain_prose` component, so rewording one restamps the surface and re-enriches only the windows that quote it.
- The three human-reviewed ledgers are hashed through projections (`divergence_ledger_digest`, `contact_allow_digest`, `standing_approvals_digest`), so only a structural edit to an entry re-runs a gate. A divergence entry's `why` is copied into the surface manifest's class list and is hashed into `explain_prose` in the same way. A contact-allow `why` is read by nothing downstream. A standing rule's `note` is copied into every fill the rule writes, so the verdict plumbing's key (`plumbing_skip_fingerprint`) hashes the rules file's raw bytes. No key the standing fill's memo reads includes the note.
- Code files that reach `fingerprint.path_lines` are hashed through a projection too (`code_file_digest`). A Python docstring or a whole-line Rust comment does not affect it, so rewording either leaves these unchanged: the tables' stamp, the run_m1 skip key, the trace-memo and replay stamps, the oracle row cache and settle memo, both surface stores, and the standing-fill memo. `gate:rebuild-contracts` and `gate:make-test` still run after such an edit, because their closures hash code files raw: test fixtures read source text, and a `# pyright: ignore` comment changes the result being checked.

## The server and the pass

The cycle decides whether the review server is stopped for a pass (`server_may_stay_up` in `rebuild/tools/artifact_cycle.py`), because only the resolved plan knows what the pass writes:

- Two things a pass can write belong to the running app. One is the served surface: a restamped manifest leaves the open tab's store stamped for a surface that no longer exists. The other is the verdict store, which `merge_verdicts` does not write while a server is running. A pass that writes either one stops the server first. `--stop-server` gives permission to do that, and a bare `make artifact-cycle` refuses and says how to proceed.
- A pass that writes neither leaves the server running, and the open tab keeps working through the whole verification.
- An edit confined to `rebuild/review/static/` is refreshed in place (the `assets-refresh` step, `rebuild.review.build refresh-assets`). The served copy is overwritten and only the manifest's `static` component is restamped, so the tab's store stays aligned, the server stays up, and livereload loads the new app files. An edit to the app's JS, CSS, or HTML therefore costs a few seconds plus the contracts tests whose recorded closure includes the edited file (`doc/testing.md` describes the per-test closure).
- When a rehearsal has already built, byte for byte, the surface the current inputs would produce, the next live pass moves that directory into `rebuild/out/review` (the `surface-promote` step) instead of rebuilding, together with its unit store and signature store. `promotable_surface` in `rebuild/tools/artifact_cycle.py` checks the preconditions. The rehearsal is found through `plan.review_out` in the last `cycle_summary.json`, or at `var/rehearsal-review`, which is why rehearsals are built there. The move replaces every shard and the stamp under the app, so such a pass takes the port and runs the full carry instead of the plumbing skip or the store-only merge. During the move, the outgoing tree is renamed to `rebuild/out/review.superseded`. If an interrupted pass leaves a tree with that name, the next pass handles it at its start, before it checks whether this is a first run: a tree with no live surface beside it is renamed back to be the live surface, and a tree beside a live surface is deleted. A `--dry-run` pass still renames a lone tree back, so the plan it prints matches what a real pass would do, but it leaves a tree beside a live surface for the next real pass to delete, and says so.
- While a server is running, retention leaves the journal and the stash sweep alone, because the app appends to the journal as verdicts are recorded.

## The verdict store

The verdict chain (`rebuild.tools.verdict_chain`) runs in one process over the surface's slim `units-index.ndjson.gz`. It runs the carry, then `merge_verdicts` (the same union the app's import uses, where the newer `at` wins, run without a browser and never removing a verdict), then the echo and standing fills until they stop changing, then the complaint docket. The carried verdicts are written to `verdicts-autosave.json` without a browser import.

- A unit's id is derived from its content key (`unit_cache.unit_id_for`; `rebuild/REVIEW-PLAN.md` §2.1 describes the shape). The carry puts each verdict on the unit with the same id and opens no other surface. A verdict whose unit is on the new surface is carried to it, and one whose unit is not is stranded. The carry's four figures (human units, key hits, unhit, stranded) are recorded on the run line in `rebuild/out/cycle-timings.ndjson`.
- A pass whose surface did not change skips the carry and merges the master straight into the store (the store-only route), because every unit id would map to itself. The merge refuses any input stamped for another surface, so this route needs a master stamped for the served surface. The line for an auto-resolved carry source says whether it is, and `master_stamped_for_surface` in `rebuild/tools/artifact_cycle.py` decides it for a `--verdicts` master. If a pass stops after the surface build writes a new surface but before the carry, the store is left stamped for the previous surface. The next pass skips the build as unchanged, reports the master as stamped for an older surface, and carries it by unit id.
- `rebuild/standing-approvals.yaml` holds the standing approval rules, which `rebuild/tools/standing_verdicts.py` applies. That module's docstring is the authority on the rule shapes. Units in a rule's `except_left` families still wait for a human verdict, and any human verdict takes precedence over a standing fill on merge.
- The standing fill stores its per-unit decisions in `rebuild/out/standing-fill-memo.ndjson.gz`. The file sits beside the surface directory, not inside it, so a surface rebuild does not delete it. A pass whose surface changed evaluates only the units whose key is new. A unit's key is its build-time `content_key` stamp, its `ink_deltas`, and the after font's compiled-glyph digest for every family its after cells name, so a rune edit re-evaluates only the windows it can affect.
  - The memo file's stamp covers the deciding code, the before font without its `head` and `name` tables, the parts of the after font that belong to no family (helper glyphs, `cmap`, and GPOS wiring), and the dependency pins in `uv.lock`. A change to any of these discards the whole memo. The rebuilt fonts and refreshed lock from a version bump keep it, and a fontTools or uharfbuzz upgrade discards it.
  - The rules file is tracked per entry instead. The header records the rule roster: each rule's match digest and verdict, the non-composable rules that every composed reading consults, and whether composed readings are on. Each entry records the composable rules with a candidate position in its window. A stored decision holds rule ids and no note text. So a rules commit re-evaluates only the windows the changed rules can reach, and rewording a note re-evaluates nothing while every fill uses the new wording.
  - The fills and the report are byte-identical whether the decisions were served from the memo or computed. `--fresh-memo` on `rebuild/tools/standing_verdicts.py` recomputes every decision and rewrites the memo, and the `memo:` line in the plumbing step's log gives how many decisions a pass served and computed. `Memo`, `memo_environment`, `rules_roster`, `unit_key`, and `Decider._serve` in `rebuild/tools/standing_verdicts.py` are the authority, and `rebuild/test_standing_verdicts.py` checks the byte identity over the frozen mini bundle.
- Every store write, including the app's autosaves, is logged in `verdicts-journal.ndjson`:

```sh
uv run python -m rebuild.tools.merge_verdicts --list
uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply
```

## Retention

A green pass ends with a retention pass over the regenerable outputs. `--keep-history` skips it, and the tracked copy under `rebuild/evidence/` is never touched. The carried files, the autosave stashes, and the journal are at the repository root, and the build logs are under `var/`, the gitignored tree for output that outlives a run. `tmp/` holds only scratch and is safe to wipe between change sets.

- Among the `verdicts-carried-*.json` files at the repository root, only those stamped for the live surface, the one this pass wrote, and any that cannot be read are kept.
- `verdicts-autosave-*` stashes that no journal event since the last base event refers to are deleted; the journal can replay their state.
- The journal is compacted to start at the newest base event older than `RETENTION_WINDOW_DAYS` days (`rebuild/tools/artifact_cycle.py`), which is the earliest moment `--restore-as-of` can restore. Run directories under `var/build-logs/` beyond the newest `BUILD_LOGS_KEEP` are deleted (`rebuild/tools/cycle_paths.py`, which defines every path the cycle writes to).

## Logs and timings

- `console.Digest` numbers steps in the order their opening banners appear, parallel gates included. The plan lists the work without numbers, and skipped or unstarted steps get no number. Log filenames and the closing table use the same numbers, with the unnumbered rows after the steps that started.
- Every pass appends host-tagged per-step wall times and peak RSS to `rebuild/out/cycle-timings.ndjson`. Every judged check run also writes its verdict there, including interactive `make test`, `make test-rebuild`, and `run_m1` runs. `make cycle-timings` reads the file, and `doc/fleet.md` explains how to tell which machine wrote a row.
- `doc/running-long-steps.md` covers running a pass detached, where the per-step logs are written, how to tell whether a run is hung, and how to stop a whole pass.

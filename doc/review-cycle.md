# The review cycle

This is the operator's view of the artifact cycle: what a pass does, what it skips, what it writes under the running review app, and what it leaves on disk. The module docstring of `rebuild/tools/artifact_cycle.py` is the authority on the plan a pass resolves; the Makefile comments above the targets named here are the authority on each target's flags. For preparing a sitting on the surface, use the `review-docket` skill; for turning a repeated verdict into a checked-in rule, `dont-bug-me-about-this-ever-again`; for acting on a sitting's rejects, `just-verdicted-now-what`.

## The commands

```sh
make artifact-cycle              # the verification alone; the commit-time form
make review-cycle                # the same pass, then serve the surface
make review-cycle SERVE=0        # same pass; prints the serve command instead of serving, so the target terminates
make review-cycle SERVE=bg       # same pass; restarts the server detached if the pass stopped it, and waits for the port
make verdict-ready               # the readiness checklist; the app's banner shows the same status
make review-serve                # serve rebuild/out/review/ on http://localhost:7294/
make cycle-timings               # what each step costs on this box; ARGS='--by-step', '--by-outcome', '--inner'
```

Flags reach the cycle through `ARGS`: `--verdicts <file>` names the master to carry, `--fresh` runs every stage, `--force-make-test` forces that one gate, `--skip-gates` and `--no-merge` narrow a pass, `--keep-history` skips retention, and `--stop-server` (which `make review-cycle` passes for you) permits the pass to take the port.

Served in the foreground, `make review-cycle` never exits, so a caller that waits on a command — an agent harness included — uses `SERVE=0` or `SERVE=bg`. Stop a detached server the way the cycle does:

```sh
pkill -f 'rebuild\.review\.serve'
```

## What a pass does

- Snapshot the served surface, rebuild the M1 font and tables, rebuild the review surface, run the verdict chain, refresh the census pins, run the gates, check the checked-in per-unit memory peaks against what this box measured, and on a green finish prune the regenerable piles.
- Every heavy stage skips when its green record proves its inputs unchanged since its last green run, so a pass that changed nothing costs seconds. A gate that did not run is unverified, not waived: `make verdict-ready` reads NOT READY until one does.
- A green pass closes on the readiness checklist itself, so there is nothing to run after it; `make verdict-ready` is the form for asking on its own.
- When only comparison-side inputs moved, the pass re-runs the gates over the artifacts already on disk instead of rebuilding them; `doc/testing.md` § Re-adjudicating without a build is the recipe and names the roster.

### The census

Every non-rehearsal pass rewrites `rebuild/review-census-pins.json` from the census sidecar the surface build emits and prints `git diff -- rebuild/review-census-pins.json` in full. That file is the last accepted census and committing the diff is the acceptance, so reading it belongs to the commit:

- The `volatile` block holds totals that move with every migrated letter; glance at it.
- The `invariant` block holds which classes the surface ships, which the build machine-approves, which are exempt from individual verdicts, and which verdict families the corpus reaches; a change there wants real attention.

## What is prose-blind

The fingerprints the table build, the conform sweep, and both rebuild suite lanes key on ignore prose, so rewording never rebuilds anything heavy. `rebuild/pipeline/fingerprint.py` is the authority; the shape is:

- Rune files under `glyph_data/runes/` hash through `rune_file_digest`, which sees none of the YAML comments, formatting, or documentation fields (`ductus`, `notes`, `why`, refuse records included).
- A refuse record's `why` is the one rune prose anything downstream reads (the surface's explain panel quotes it). It rides `rune_explain_digest` and the surface's Stage B `explain_prose` component, so rewording one re-stamps the surface and re-enriches the windows that quote it, and nothing else.
- The three human-reviewed ledgers hash through a projection (`divergence_ledger_digest`, `contact_allow_digest`, `standing_approvals_digest`), so only a structural edit to an entry moves a gate. A divergence entry's `why` reaches the surface manifest's class list and rides `explain_prose` the same way; a contact-allow `why` reaches nothing downstream; a standing rule's `note` is quoted into every fill it lands and stays inside the verdict plumbing's key.

## The server and the pass

Whether the review server comes down for a pass is the cycle's decision (`server_may_stay_up` in `rebuild/tools/artifact_cycle.py`), because only the resolved plan knows what the pass writes:

- Two things a pass writes belong to the running app: the served surface, where a restamped manifest orphans the open tab's store, and the verdict store, which `merge_verdicts` refuses to touch under a live server. A pass that writes either stops the server first; `--stop-server` is the permission, and a bare `make artifact-cycle` refuses and says how.
- A pass that writes neither leaves the server up and the letters stay on screen through the whole verification.
- An edit confined to `rebuild/review/static/` is refreshed in place (the `assets-refresh` step, `rebuild.review.build refresh-assets`): the served copy is overwritten and only the manifest's `static` component restamped, so the tab's store stays aligned, the server stays up, and livereload reloads the new shell. That tree is outside the validators lane's closure, so an app JS/CSS/HTML edit costs seconds plus the contracts tests whose recorded closure reaches the edited file (`doc/testing.md` describes the per-test closure).
- While a server is up, retention leaves the journal and the stash sweep alone, because the app appends to the journal as you verdict.

## The verdict store

The verdict chain (`rebuild.tools.verdict_chain`) runs as one process over the surface's slim `units-index.ndjson.gz`: the carry, then `merge_verdicts` (the app's newer-`at`-wins import union, headless and never shrinking), then the echo and standing fills to their fixpoint, then the complaint docket. The carried verdicts land in `verdicts-autosave.json` with no browser import.

- `rebuild/standing-approvals.yaml` holds once-and-for-all pattern rules, applied by `rebuild/tools/standing_verdicts.py`; that module's docstring is the authority on the rule shapes. A rule's `except_left` families still queue, and any human verdict outranks a standing fill on merge.
- The standing fill memoizes its per-unit decisions in `rebuild/out/standing-fill-memo.ndjson.gz` — beside the surface directory rather than inside it, so a surface rebuild never clears it and the pre-pass snapshot never copies it — and a pass whose surface moved evaluates only the units whose key is new. A unit's key is its build-time `content_key` stamp, its `ink_deltas`, and the after font's compiled-glyph digest for every family its after cells name, so a rune edit re-evaluates exactly the windows that can feel it; the file's own stamp is the rules file's raw bytes (a rule's `note` is quoted into every fill), the deciding code, the before font, the after font's family-blind remainder, and `uv.lock`, and any of those moving drops it whole. The fills and the report come out byte-identical served or computed, `--fresh` rewrites the memo from scratch, and the `memo:` line in the plumbing step's log says what a pass served and computed. `Memo`, `memo_environment`, and `unit_key` in `rebuild/tools/standing_verdicts.py` are the authority, and `rebuild/test_standing_verdicts.py` proves the identity over the frozen mini bundle.
- Every store write, app autosaves included, is journaled in `verdicts-journal.ndjson`:

```sh
uv run python -m rebuild.tools.merge_verdicts --list
uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply
```

## Retention

A green pass ends with a retention pass over the regenerable piles; `--keep-history` skips it, and the tracked copy under `rebuild/evidence/` is never touched.

- Only this pass's `tmp/review-pre-*` snapshot and the stamp-aligned `verdicts-carried-<sha>.json` survive.
- `verdicts-autosave-*` stashes older than the journal's last base event go; the journal replays them.
- The journal is compacted to the restore floor `RETENTION_WINDOW_DAYS` states, and run directories under `tmp/build-logs/` beyond `BUILD_LOGS_KEEP` go (both in `rebuild/tools/artifact_cycle.py`).

## Logs and timings

- Every pass appends host-tagged per-step wall times and peak RSS to `rebuild/out/cycle-timings.ndjson`, and every judged check invocation files its verdict there too, interactive `make test`, `make test-rebuild`, and `run_m1` runs included. `make cycle-timings` reads it; `doc/fleet.md` is the key to the host column.
- `doc/running-long-steps.md` is the recipe for running a pass detached, where the per-step logs land, and judging whether a run is hung.

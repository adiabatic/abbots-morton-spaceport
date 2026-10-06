---
name: add-a-new-letter
description: Migrate one letter into the M1 rebuild alphabet as a single "Add ·X" batch. Gather the old font's pair evidence with the bundled tool, author the rune file and the neighbors' scopes, add the letter to the alphabet and the ledgers, extend the smoke set, run the gates, and make the commit. Use when the user asks to add or migrate a letter (or "the next letter") into the rebuild.
argument-hint: "[letter]"
---

The user wants one more letter in the rebuild's alphabet. A letter addition is one batch, committed as one commit titled `Add ·X` (`git log --oneline --grep='^Add ·'`). Its record is the commit plus the rune's `why:` fields, never a report. Before writing anything, read the two or three most recent `Add ·X` commits in full. They are the current template, and each batch refined the pattern. The authoritative rules are in `doc/rebuild-design.md` (§4 the five-step decision procedure for classifying records, §13 step 3 the record conversion) and `rebuild/M1-PLAN.md` (§3 the rune-file template, §8 the ductus drafting protocol). Don't re-derive or restate what they already say.

The user makes the design decisions. Where the old record can be converted in more than one way (which bitmaps become stances, which joins yield, whether an old exit tuck is really the receiver's entry contraction), present the evidence and ask. Then record each decision under "Recorded design overrides" in the batch progress file.

## Hard rules

- Never write `why:` in `glyph_data/runes/`. That field holds the user's rationale in the user's own words. The only files where an agent writes `why:` are `rebuild/m1-contact-allow.yaml` and `rebuild/m1-divergences.yaml`.
- Scope every entry `from:` and exit `toward:` list from the junctions that actually join in the baseline. Start with the pair map, then scan all in-scope triples and quadruples in every non-overlay acceptance configuration, because a predecessor's contextual exit can reach a different entry row than its isolated pair shows. Complete both neighbors' scopes from those rows. Reading the FEA is never the authority, because some joins use GPOS anchors alone.
- A rune is gated on its ductus. Every stance names a motion, and any motion prose not copied byte-for-byte from the old YAML gets `# DRAFT — pending author sign-off` on its key line.
- The old font's behavior takes precedence over a literal reading of the old YAML. When they disagree, transcribe faithfully and let the gates decide. A divergence goes into the ledger with evidence, never into an unrecorded spec edit.
- Before adding an ink-identical class for a form-name difference, compare the placed ink over all candidate windows. The position comparison catches moved origins and advances, but an unrelated shape change can leave both unchanged; exclude such windows from the class.
- Never commit without approval. At the commit point, spawn a fresh sub-agent for commit-message suggestions. The subject is `Add ·X`, and the body describes how the letters look and join after the change, not the mechanism.
- Detach the long steps, and never single-thread pytest. `doc/running-long-steps.md` has the detach recipe and how to judge whether a run is hung, and `doc/parallelism.md` has the width rules.

## 0 — orient

- Look up the letter and codepoint in `doc/glyph-names.md`. If the user didn't name one, pick an unmigrated letter without putting much thought into it: any codepoint missing from `M1_ALPHABET` will do. Don't deliberate and don't ask.
- Read the old record: the family's entry in `glyph_data/quikscript.yaml` (bitmaps, stances, anchors, `select`/`derive`, notes, ductus). Classify each piece with the design doc's §4 decision procedure: differs only in which neighbors select it → prefer/refuse/row scope; anchor-only difference → cell; ink that differs only at a join → binding; reach toward one neighbor → extend; a different pen motion → stance.
- Look for work already recorded against this letter. Grep the qs-name across `glyph_data/runes/` and `rebuild/*.yaml`, and list the open GitHub issues labeled for it (`gh issue list --label 'waits on ·X'`; delete the label once the letter is committed). Issues record work in advance for several letters (inactive contracts to re-adjudicate, unused records that take effect, `from:` list members to re-verify). Migrated runes may already hold records that wait on this unmigrated family and take effect with this migration.
- Every migration re-checks that a word-final ·Tea·Day pair keeps its join ahead of the joins around it (the `why:` on qsNo's and qsUtter's ·Tea·Day prefers). A letter that can exit into ·Tea almost certainly needs the yielding prefer (qsJai's record verbatim, as on qsAwe/qsOx/qsEight/qsOoze), plus an oracle spot-check that its ·Utter·Tea·Day windows match the old font.
- Ligatures: check `rebuild/script.yaml`'s ligature sequences. Every ligature whose two components are both migrated once this letter is added gets its own rune file in the same batch (the `Add ·Out` commit includes qsOut_qsTea, and the `Add ·J’ai` commit includes qsJai_qsUtter).
- Run the bundled evidence tool (last section) and keep its output available. Every later step uses a section of it.

## 1 — grow the alphabet

Add the codepoint to `M1_ALPHABET` in `rebuild/pipeline/baseline_subset.py`. This step needs nothing else. `run_m1` refilters the subset tables itself (`ensure_fresh`). It refuses to start while any glyph name in a subset row lacks a `rebuild/m1-aliases.yaml` entry, and it lists the missing names. An entry may map to `pending` while the migration is in progress.

## 2 — author the rune file(s)

Write `glyph_data/runes/qsX.yaml`; a ligature goes in its own file after its lead. The template is M1-PLAN §3. The nearest precedent is the latest Add commit whose letter has the same kind of shape (a Short single-stance letter: qsOoze; contextual stances: qsAt; a ligature: qsJai_qsUtter). Points that come up repeatedly:

- Copy bitmaps verbatim from the old YAML: double-quoted rows, with bare trailing `#` markers on the rows at glyph-space y 5 and 0.
- Rune files follow `tools/reflow_yaml.py`'s structural rule (every collection in block style except three flow leaf shapes). Finish with `uv run python tools/reflow_yaml.py` and expect a no-op.
- List `from:`/`toward:` members in code-point order, taken from the evidence tool's join map and completed against the longer baseline windows. A partner missing from the pair map is not a refusal: verify contextual join heights before closing a row's scope. Left-facing lists automatically include ligatures (they are ligature-transparent), so never add `qsA_qsX` lefts by hand. Name a ligature explicitly only to exclude it.
- Old `derive` directives that involve the letter become `extend:`/`contract:` records. As with qsJai, expect that an old exit tuck that removes ink across rows is usually rewritten as the receiver's own entry contraction, not as a contract on this side.

## 3 — neighbors and ledgers

- Neighbor runes: every migrated family that the old font joins into or out of this letter adds the letter to its own `toward:`/`from:` list. The evidence tool's two pair sections are this worklist, one section per side.
- ss10: the old font draws the letter under ss10 as its anchor-free `qsX.ss10` copy, so `rebuild/m1-aliases.yaml` gets a `qsX.ss10` entry that copies the bare family's entry (the file's conventions header says a `.ss10` name denotes what the bare name denotes), placed after that family's last entry. `classify_divergence` gets a new branch only for a new kind of change. Most letters add no ledger class, and ss10 needs none, because the classifier gives no class to an ss10 row that is not a boundary window.
- `rebuild/m1-contact-allow.yaml`: each off-anchor-contact error on an off-junction contact the old font already draws gets a signature plus an agent-written `why:` in the pattern of the surrounding entries.
- `rebuild/pipeline/smoke_sequences_m1.txt`: add the codepoint to the header list, then a block modeled on the latest letter's. The block covers isolation, every joining left, every joining right, the breaks, ligature junctions both ways, the yield chains, the ZWNJ locked copy, an exit severed by ZWNJ, and the namer dot.
- `rebuild/test_review_enrich.py::test_subset_tables_iterate` checks containment over the frozen mini bundle, so a migration needs no test-count edit. The subset growth the evidence tool prints is information about the live tables, and `baseline_subset.ensure_fresh` refreshes them.

## 4 — batch scratch and WHATNEXT

Create `rebuild/M1-BATCH<n+1>-PROGRESS.md` (n = the newest existing batch), holding only what the note-taking rules allow: what's parked, recorded design overrides, the verification recipe, and the resume commands. Delete an older batch file only if its review session has closed, and move its remaining pointers to open work into WHATNEXT.md. Update WHATNEXT's next-step paragraph in place, and edit or delete any bullet about a letter that this migration completes.

## 5 — verify

The verification recipe, in order (each open batch file has a recipe of the same form):

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py E6XX:E6XX E6XX:E6XX:E6XX …
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

- `run_m1`'s `--jobs` defaults to the `sweep_job_budget()` width the artifact cycle passes, so a run without the flag sizes itself to the machine. Detach `make artifact-cycle`.
- The probe run is one invocation that takes a set of windows: every joining pair in both directions, the yield chains, and neighbors that must not change. It prints one block per window in argument order. Every divergence from the old font must be one the user designed.
- A green result is defects 0/0, the conformance sweep exact, the read-back clean with its GSUB offset headroom at or above the floor and its largest packed group under the ceiling, and the Manual pins clean. The change in the oracle's unmatched rows is the measure of the batch: each new row either disappears with your records or falls under an existing ledger class as a designed divergence.
- Run `make prettier` after any Python edit. Review `git diff -- rebuild/review-facts-pins.json` at commit time, and read any change in its `invariant` block carefully.
- Re-run the scaling series (`uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-series.txt`, which refreshes the checked-in record; it builds every size's font as well, so detach it as `doc/running-long-steps.md` says) and compare the whole-series fit with the threshold in `scaling_sweep.py`'s docstring. A fit past the threshold is work for the speed-up tracking issue, not for this batch. The report also fits the settlement lookup's subtable count N, which read-back's headroom floor limits; `make cycle-timings ARGS='--by-commit'` shows the batch's own change in N and in the headroom, build by build.
- On the 18-core M5 Pro MacBook Pro, which holds the untracked harness, re-measure the deep sweep's cost per text with nothing else running (`uv run python var/keep/issue-482/harness/measure.py --alphabet full --max-length 5 --last qsEt --no-hits --label <letter> --out var/keep/issue-507/runs.ndjson`). Draft a comment for #507 with its `worker_wall_s` over `sequences`, the packer (`pack_gsub`'s shared-ClassDef packing today; #490's split and #491's refinement each change it), and `readback_summary.json`'s `checked.settle_rules` and `checked.settle_subtable_formats` (format 2 plus format 3), and post it once the user approves. Once #507 holds two or more repeats, restate #487's deep-sweep projection from them, over the shaping runs `make conform-deep` makes: `default` over every text and each other settlement configuration only over the texts that name a rune it renames, as its plan line and `sequences_by_config` show.
- Read the batch's growth in the table build from this machine's timings journal (`rebuild/out/cycle-timings.ndjson`). The batch's first `run_m1` is a fresh build, and the `kernel_build_tables` entry in its check line's `inner` (or in a cycle's run_m1 step line) carries the build's record, which `rebuild/tools/cycle_timings.py`'s module docstring describes. Pair it with the last build before the batch whose entry has `memos_read` 0 and the same `code` and `width`, from a pass of the same shape (standalone with standalone, or cycle passes that ran the same gates); when the code `run_m1.table_build_code_paths` names (the crate, `kernel_exec`, `kernel_io` and `model`) changed in between, no such build exists, so say that instead. Draft a comment for #505 with both builds' seconds and the growth beside what #487 projects for the batch, and post it once the user approves. Once #505 holds two or more batches, restate #487's kernel-growth projection from them.
- After that first fresh `run_m1`, on the 18-core M5 Pro MacBook Pro, which holds the untracked harness, re-measure the table build's kernel terms by the recipe in `DELTA_SLOT_BYTES`'s comment in `rebuild/pipeline/kernel_exec.py`, with nothing else running: `uv run python var/keep/issue-492/wave.py <letter>`, detached as `doc/running-long-steps.md` says (about an hour), then `uv run python var/keep/issue-492/report.py <letter>`. Where the terms it gives differ from the shipped ones, set them in the batch's commit, rewrite each constant's comment from the new readings, and re-pin the fleet widths if they move. Draft a comment for #492 with each width's reading, its booking, and the width the formula gives the 48 GiB machines, and post it once the user approves.

## 6 — commit and hand off

Show the diff, present the sub-agent's commit-message candidates, wait for the go-ahead, and commit everything as the single `Add ·X` commit. Afterward the user runs `make review-cycle` and adjudicates the new units in a review session. `/prepare-review-queue` prepares the session, and `/just-verdicted-now-what` acts on its verdicts. The batch closes when its review session closes, and only then is its progress file deleted.

## The bundled evidence tool

```zsh
uv run python .claude/skills/add-a-new-letter/letter_evidence.py ·Zoo
```

It accepts `·Zoo`, `Zoo`, `qsZoo`, or `E65B`, and scans every full baseline table (one per configuration in `CONFIGS` in `rebuild/baseline/model.py`) in about a minute. Each section of its output corresponds to one part of the work:

- The LEFT and RIGHT pair maps are the evidence for the `toward:` and `from:` scopes, and the outline of the smoke block. Partners marked `*` are unmigrated. A join with one of them may be recorded, but it waits on an unmigrated letter: re-verify it when that partner is migrated.
- The compiled-forms inventory is the stance worklist. A `.half`, `.alt`, or contextual name means a stance or cell to model, and `en-con`/`en-trim`/`en-ext`/`ex-ext` names mean contraction and extension records.
- The alias worklist is what `run_m1`'s completeness gate will require, available before the first build.
- The subset-growth line gives the expected growth of the live tables.

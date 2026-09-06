---
name: add-a-new-letter
description: Migrate one letter into the M1 rebuild alphabet as a single "Add ·X" batch — gather the old font's pair evidence with the bundled tool, author the rune file and neighbor scopes, grow the alphabet and the ledgers, extend the smoke set, run the gates, and land the commit. Use when the user asks to add or migrate a letter (or "the next letter") into the rebuild.
argument-hint: "[letter]"
---

The user wants one more letter in the rebuild's alphabet. A letter addition is one batch landing as one commit titled `Add ·X` (`git log --oneline --grep='^Add ·'`); its record is the commit plus the rune's `why:` fields, never a report. Before writing anything, read the two or three most recent `Add ·X` commits end to end — they are the living template, and each batch refined the idiom. The binding law lives in `doc/rebuild-design.md` §13 (the five-step record rubric in its step 3) and `rebuild/M1-PLAN.md` (§3 the rune-file template, §8 the ductus protocol); don't re-derive or restate what they already say.

The design calls are the user's. Where the old record forks — which bitmaps become stances, which joins yield, whether an old exit tuck is really the receiver's entry contraction — present the evidence and ask, then record each ruling under "Recorded design overrides" in the batch progress file.

## Hard rules

- Never author `why:` in `glyph_data/runes/` — that field is the user's voice. The agent-written `why:` homes are `rebuild/m1-contact-allow.yaml` and `rebuild/m1-divergences.yaml` only.
- Scope every entry `from:` and exit `toward:` list from baseline pair rows, never from FEA reconnaissance — the old font joins some pairs by GPOS anchors alone, with no calt rule for a grep to find.
- A rune is gated on its ductus: every stance names a motion, and any motion prose not carried byte-for-byte from the old YAML gets `# DRAFT — pending author sign-off` on its key line.
- Behavior ground truth outranks a literal reading of the old YAML: when they disagree, transcribe faithfully and let the gates arbitrate — divergences land in the ledger with evidence, never as silent spec edits.
- Never commit without approval; at the commit point, spawn a fresh sub-agent for commit-message suggestions. Subject `Add ·X`; the body describes how the letters now look and join, not the mechanism.
- Detach the long steps (`nohup … > tmp/… 2>&1 &`, `caffeinate -i`) and never single-thread pytest — AGENTS.md carries the exact traps and the liveness-watching recipe.

## 0 — orient

- Resolve the letter and codepoint via `doc/glyph-names.md`. If the user didn't name one, just pick an unmigrated letter without putting much thought into it at all — any codepoint missing from `M1_ALPHABET` will do; don't deliberate and don't ask.
- Read the old record: the family's entry in `glyph_data/quikscript.yaml` (bitmaps, stances, anchors, `select`/`derive`, notes, ductus). Classify each piece with the design doc's rubric: neighbor-summoned → prefer/refuse/row scope; anchor-only difference → cell; join-localized ink → binding; reach toward one neighbor → extend; a different pen motion → stance.
- Sweep for standing obligations: grep the qs-name across `WHATNEXT.md`, `glyph_data/runes/`, and `rebuild/*.yaml`. WHATNEXT's "Waiting on a specific migration" bullets pre-record work for several letters (dormant contracts to re-adjudicate, dead-listed records that come alive, from-list tails to re-verify), and migrated runes may already carry deferred-partner records naming this family that go live now. The ·Tea·Day tight bond is re-checked at every migration: a letter that can exit into ·Tea almost certainly needs the yielding prefer (qsJai's record verbatim, as on qsAwe/qsOx/qsEight/qsOoze) plus an oracle spot-check that its ·Utter·Tea·Day windows land old-font.
- Formation closure: check `rebuild/script.yaml`'s ligature sequences — every ligature both of whose components are now migrated gets its own rune file in the same batch (qsOut brought qsOut_qsTea; qsJai brought qsJai_qsUtter).
- Run the bundled evidence tool (last section) and keep its output at hand; every later step consumes a section of it.

## 1 — grow the alphabet

Add the codepoint to `M1_ALPHABET` in `rebuild/pipeline/baseline_subset.py`. Nothing else here: `run_m1` refilters the subset tables itself (`ensure_fresh`), and it refuses to start while any subset-row glyph name lacks an `rebuild/m1-aliases.yaml` entry, naming the missing names — an entry may map to `pending` mid-migration.

## 2 — author the rune file(s)

`glyph_data/runes/qsX.yaml`, a ligature in its own file after its lead. The template is M1-PLAN §3; the nearest precedent is the latest Add commit whose letter shares the shape (Short single-stance letter: qsOoze; contextual stances: qsAt; ligature: qsJai_qsUtter). What repeatedly matters:

- Bitmaps verbatim from the old YAML — double-quoted rows, bare trailing `#` markers on the rows at glyph-space y 5 and 0.
- Rune files use the structural YAML style (everything block, three flow leaf shapes); finish with `uv run python tools/reflow_yaml.py` and expect a no-op.
- `from:`/`toward:` members in code-point order, straight from the evidence tool's join map. Left-facing lists are ligature-transparent automatically — never hand-add `qsA_qsX` lefts; naming a ligature literally is for carving it out.
- Old `derive` directives touching the letter become `extend:`/`contract:` records — and expect the qsJai lesson: an old exit tuck that removes ink across rows usually re-spells as the receiver's own entry contraction, not as a contract on this side.

## 3 — neighbors and ledgers

- Neighbor runes: every migrated family the old font joins into or out of this letter widens its own `toward:`/`from:` list — the evidence tool's two pair sections are exactly this worklist, one side each.
- ss10: decide `SS10_UNCOVERED_BY_OLD_FONT` membership in `rebuild/pipeline/oracle.py` — the qsAwe shape (no stances in the old record, both anchors riding the base cmap glyph) or qsAt's direct pair evidence — and extend both the set's comment there and the `ss10-isolation-completed` `why:` in `rebuild/m1-divergences.yaml` the way every past member did. `classify_divergence` grows an arm only for a genuinely new phenomenon; most letters add no ledger class.
- `rebuild/m1-contact-allow.yaml`: each off-anchor-contact error on a corner the old font already draws gets a signature plus an agent-written `why:` in the surrounding idiom.
- `rebuild/pipeline/smoke_sequences_m1.txt`: add the codepoint to the header list, then a block modeled on the latest letter's — isolation, every joining left, every joining right, the breaks, ligature seams both ways, the yield chains, the ZWNJ locked twin, an exit severed by ZWNJ, the namer dot.
- `rebuild/test_review_enrich.py`'s subset-table row count is the one hand-update nothing prompts for; the evidence tool prints the expected number.

## 4 — batch scratch and WHATNEXT

Create `rebuild/M1-BATCH<n+1>-PROGRESS.md` (n = the newest existing batch) holding only what the note-taking rules allow: what's parked, recorded design overrides, the verification recipe, resume commands. Delete an older batch file only if its sitting has closed, lifting survivors into WHATNEXT.md. Update WHATNEXT's frontier paragraph in place, and edit or delete any letter-keyed bullet this migration discharges.

## 5 — verify

The batch recipe, in order (each open batch file carries the same shape):

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
PYTHONPATH=. uv run python rebuild/tools/probe.py E6XX:E6XX
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

- `--jobs` defaults to the `sweep_job_budget()` width the artifact cycle already passes, so a bare run sizes itself to the box. Detach `make artifact-cycle`.
- The probe battery is one window per invocation: every joining pair in both directions, the yield chains, and must-not-move neighbors — every divergence from the old font must be one the user designed.
- Green looks like: defects 0/0, conform exact, read-back clean with its GSUB headroom inside the floor, Manual pins clean; the oracle-unmatched delta is the score — new rows either disappear with your records or land under existing ledger classes as designed divergences.
- `make prettier` after any Python edit. Review `git diff -- rebuild/review-census-pins.json` at commit time — anything moving in its `invariant` block wants real attention.
- Re-run the scaling ladder (`uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-ladder.txt`, which refreshes the checked-in record) and read the whole-ladder fit against the threshold in `scaling_sweep.py`'s docstring — a tripped threshold goes to the speed-up tracker, not this batch.

## 6 — land and hand off

Show the diff, present the sub-agent's commit-message candidates, wait for the go-ahead, land everything as the single `Add ·X` commit. Afterward the user runs `make review-cycle` and adjudicates the new units in a sitting — preparing one is `/review-docket`'s job and consuming its verdicts is `/just-verdicted-now-what`'s. The batch closes when its sitting does; only then does its progress file go.

## The bundled evidence tool

```zsh
uv run python .claude/skills/add-a-new-letter/letter_evidence.py ·Zoo
```

Accepts `·Zoo`, `Zoo`, `qsZoo`, or `E65B`; scans all eleven full baseline tables in about a minute. Its sections map one-to-one onto the work: the LEFT/RIGHT pair maps are the `toward:`/`from:` scope evidence and the outline of the smoke block (partners marked `*` are unmigrated — recording one is deferred-partner evidence to re-verify at that partner's migration); the compiled-forms inventory is the stance worklist (a `.half`/`.alt`/contextual name means a stance or cell to model, `en-con`/`en-trim`/`en-ext`/`ex-ext` names mean contraction and extension records); the alias worklist is what `run_m1`'s completeness gate will demand, available before the first build; and the subset-growth line is the new `test_review_enrich.py` number.

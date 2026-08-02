# M1 batch 9 — qsSee (and qsSee_qsUtter)

Scratch for the ·See migration. Delete when the batch closes, lifting any surviving forward-pointer into `WHATNEXT.md`.

## Committed

Nothing yet.

## Parked

Nothing yet.

## State

The rune, the ligature rune, the alphabet entry, the aliases, the neighbor edits, and the smoke rows are in place, and the artifact cycle is green end to end: the conform sweep is exact over the grown alphabet, the boundary-equivalence sweep is exact, and every in-scope Manual pin replays clean — the ·See pins became gate-eligible with this batch, and the one initial disagreement (the pin expecting half-·Tea to bridge ·See on to ·Et) was the tell that qsTea.half's top entry still carried the curs-only transcription; the promotion below resolved it. The defect gates needed one blessing pass in `rebuild/m1-contact-allow.yaml`: ·See's declined-exit crest and the ligature's x-height bar (the E-DANGLE set, on the qsEt/qsRoe terminal precedent), the curled hook's corner one row off the y6 seam into ·Pea, and the top-entered full ·Tea's y1 corner into ·Utter. The oracle's unmatched rows are up, as a new letter always puts them, and are verdict-gated rather than failing (`oracle_summary.json`). The qsEt sitting before this batch closed by declaration — all remaining blanks bulk-approved with a `[bulk-close]` note, on the qsRoe/qsI precedent — so the store entered this batch with no open blanks.

The batch also forced one pipeline fix, in `rebuild/pipeline/conform.py`: the conform gate's witness assembler treated a formed-ligature label in a window slot as a raw letter when evaluating the section 5.7 formation guard, so every witness for a window pairing an unformed pair with a following formed ligature died in the second-slot hunt and 598 realizable transitions read as dead. qsSee_qsUtter is the first ligature whose lead is itself a member of another ligature's guard class (qsSee in qsDay_qsUtter's), which is why no earlier batch could hit it; `_guard_follower_slots` now expands a ligature label to the lead and trailing components the guard actually reads.

`rebuild/pipeline/run_m1` takes `--jobs`, and the table stage is most of its wall time: `--jobs 6` turns a fifteen-minute build into a three-minute one on this machine. Worth remembering for any iterate-and-rebuild loop.

One reporting artifact to leave alone: the dead-policy gate lists `qsIt.yaml:policy.refuse[6]` (the `self: {entry: live}` x-height refusal over lefts qsJay/qsYe/qsIt/qsEat) as dead in the current alphabet. Its only migrated left arm, qsIt, is structurally unreachable behind ·It's never-joins-itself refusals, and its real partners are the three unmigrated lefts — the record comes alive with them, so it stays.

## Shape of the letter, as modeled

·See is a Tall serpentine: a baseline foot on the left, an S-spine, and a crest at the top right. One baseline entry (on the foot), and three exit heights across four stances: `normal` (the base drawing, top exit — ·See·Tea and ·See·Fee join at y8), `curled-over` (the crest hooks down to exit at y6, into half-·Pea), and the entryless `straighter` / `straightest` twins (drawn top-down, no foot, baseline exit — scoped `toward:` ·Low plus the deferred qsAt/qsOut/qsOoze). Every entry/exit combination on the entered stances is legal, so there are no pairings blocks and no cells. The foot is base ink — the bare drawing is the live join carrier, as with ·Et's tail — so incoming baseline joins need no stub and no withdrawal variant, and the entry is unrestricted on the qsRoe/qsEt idiom (every migrated baseline exit reaches it in the old font; ·Low/·I/·Ah/·Fee break because they have no baseline exit at all).

The one authored subtlety is the baseline-exit tie-breaker: with a joinable left and ·Low on the right, join-count ties (keep the left join and strand ·Low, or yield it and serve ·Low), and the old font yields — except after ·It, which keeps its join and strands ·Low (the shipped `not_after: [qsIt]`). That is one default-mode prefer on qsSee: the `{entry: none, exit: baseline}` cell over `{entry: baseline, exit: none}`, left-scoped `class: can-exit-at-baseline` except qsIt, right-scoped to the four baseline receivers. A refuse cannot carry this fact: stated at candidacy grain it strands an x-height-entered alternate ·Utter (whose `require: [exit]` leaves no fallback cell) two positions ahead of where the closure can see ·Low — ·May·Utter·See·Low dies E-STRANDED — which is why the sitting's fix for the guard reading this yield went into the guard's grain instead (see the overrides below). The straighter exit extends by 2 before ·Low (meeting qsLow's own `en-ext-1`, whose left scope has named qsSee since the qsLow migration) and carries the dormant contract-by-1 toward qsOut.

qsSee_qsUtter is the batch's formation-closure obligation (both components now migrated). It is entryless by the shipped font's own `entry: null` — predecessors revert, per the qsTea_qsOy idiom — with one x-height exit that extends by 1 before ·Fee and ·May and, ss03-gated, before ·Tea. The shipped un-forming before ·See/·Low/·I/·Ah (where only the unformed alternate ·Utter can reach the follower at the baseline) is exactly `settle.formation_blocked`'s derivation and carries no data in the rune — with one refinement the sitting forced: before a ·See whose own right neighbor takes its entry out of play (a forming ·See·Utter pair, or a grounded receiver the prefer's vote yields to), the unformed trail would not reach the follower and the ligature forms, as shipped.

All ductus is drafted, not transcribed — the shipped YAML carries none for qsSee or the ligature — and is marked `# DRAFT — pending author sign-off` in both runes.

## What the neighbors already knew

Most of the wiring predated this batch: qsTea.full carries the bare top entry (·See·Tea joins at y8) and qsTea.half the `selectable: false` top entry (·See·Tea·It); qsFee.loop's top-entry stub is unrestricted (·See·Fee); qsPea's y6 entries are bare (·See·Pea·Pea chains); qsLow's baseline entry extends by 1 after qsSee; qsMay's grounded exit and qsRoe's baseline exit name qsSee in their `toward:` lists; qsFee and qsMay already named qsSee_qsUtter (entry from-list and the x-height-arrivers group). The four edits this batch made, all oracle-verified: qsPea's full-stance x-height entry from-list gains qsSee_qsUtter (·See·Utter·Pea joins full ·Pea at y5; the half is never entered by the ligature, so its list stays), qsOy's x-height entry from-list gains it, qsTea.half's ss03 unlock left scope gains it (the ss03 join is half-·Tea's — ·See·Utter·Tea·Day keeps the half and strands ·Day, so the full's unlock scope stays), and qsTea.half's top entry was promoted from `selectable: false` to a real entry — the transcription of the shipped `half_entry_top_exit_xheight` stance's `entry_curs_only: null, entry: [0, 8]` promotion, which could never fire before a top exiter existed. With it selectable, join-count and qsTea's standing two-verticals baseline refusal reproduce the old font's half-vs-full choice at the top entry: half before ·It and ·Et (the Manual pin at site/the-manual.html:3460 replays this), full before ·Day/·No/·Utter/·See and on ties.

## Recorded design overrides

- **The baseline entry is unrestricted where a from-list was possible** — the qsRoe/qsEt idiom over the qsAh one, on the same grounds as batch 8: every migrated baseline exit joins ·See in the old font, and the non-joiners lack baseline exits entirely.
- **The grounded prefer's left scope is the predicate class, not a hand list** — `can-exit-at-baseline` except qsIt names today's nine joinable lefts and grows with the alphabet; a future left that should behave like ·It (keep the join, strand the receiver) will surface as an oracle divergence at its own migration.
- **The prefer's right scope names all four shipped grounded receivers** (qsLow plus deferred qsAt/qsOut/qsOoze), transcribing the shipped `select.before` structure; only the qsLow arm is oracle-verified today, so re-verify the tie choice when each of the other three migrates.
- **The §5.7 guard settles the unformed trail at ranking grain and resolves a forming follower pair to its ligature** — the sitting's two fresh complaints are guard blindnesses, not record gaps, and both fixes live in the engine (`settle._blocked_under`: a `transition_trace` for the trail, `_follower_formation` for the pair) plus the forming-rows-first bucket in `emit_gsub._formation_lines` that compiles the newly expressible verdict shape (the §15.8 bend, recorded in `doc/rebuild-design.md` §5.7/§15.8). The neither (u-119427, ·Day·Utter·See·Low; "The ·Day·Utter ligature failing to happen because of — apparently — ·Low on the far side of another letter (·See) is a bug, and I might have waved through and approved a bunch of verdicts like this mistakenly") is the ranking-grain case: ·See's grounded prefer withholds the unformed ·Utter's reach, but a candidacy-grain guard could not see a vote — and restating the prefer as a refuse instead strands ·May·Utter·See·Low E-STRANDED, so the record route is closed. The reject (u-119428, ·Day·Utter·See·Utter; "The ·Day·Utter ligature failing to happen because of — apparently — an adjacent ligature — is a bug") is the forming-pair case: the approved u-136026 (·Utter·See·Utter·See) pins ·Utter joining a ·See whose trailing pair fails to form, and no window-decidable record separates that context from the guard's closure. The guard's engines bind slots past the verdict's two to the window edge (`vote_deep_slot=EDGE`) so only a vote firing definitively inside the window can flip a verdict — without that, the follower-vote optimism un-forms ·Day·Utter·Utter·Tea, whose unformed rendering u-119447/u-119448 carry as approvals. Exactly four guard keys flip — `(qsDay_qsUtter | qsSee_qsUtter, ·See, ·Low | ·Utter)` — every one to the shipped font's formation.

## Open questions for the sitting

- **`·Day·Tea·See` and `·No·Tea·See` join the ·Day·Tea·X yield family.** The shipped font yields the left letter and joins ·Tea~·See at the baseline; M1 keeps the ·Day/·No~·Tea join, same as the family's other members. A family question per the standing WHATNEXT fork, not a qsSee one.
- **`·See·Utter·Tea·Tea` under ss03: M1 should gain a join the old font declines.** The old font extends the ligature toward ·Tea but leaves it unentered before a second ·Tea (`lig,break,break`, with the extension dangling); M1's half-·Tea, in the ligature's unlock scope, has no reason to refuse the first seam, and M1 drops the dangling extension when no join lands. Expect a strict-gain divergence plus dangling-anchor-dropped churn.
- **ss05's ·Et·Tea·See chain** rides qsTea's existing both-baseline unlock — expect conformance, but the sweep verifies it against the grown alphabet for the first time.

## The WHATNEXT items this batch was supposed to answer

- **The ·Tea·Day tight bond** — qsSee exits into ·Tea only at the top, which spends nothing (·Tea's exclusivity is the `(baseline, baseline)` pairing); the shipped font keeps `·See·Tea·Day` fully joined (`y8,y0`) and M1 needs neither yielding shape. qsUtter's x-height entry from-list is unchanged by this batch — the ligature isn't in it (`·See·Utter·Utter` keeps the ligature and breaks) — so no new copy of the `mode: absolute` record either.
- **qsFee's forward-preference left scope** — nothing needed. ·See enters ·Fee at the top, not the x-height, so the prefer's seam question never arises; on the follower side ·Fee·See breaks in the old font (·Fee has no baseline exit) and falls out of join-count.

## Verification recipe

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal   # rune loads
uv run python -m rebuild.pipeline.run_m1 --jobs 6                   # build + gates + oracle (refreshes the baseline subset itself)
PYTHONPATH=. uv run python rebuild/tools/probe.py E65A:E652         # one window, all configs
make test-rebuild
make test
make artifact-cycle ARGS='--update-pins'
make verdict-ready
```

Oracle evidence is derived by scanning `rebuild/out/baseline-<config>.tsv.gz` directly; the length-2 block sits right after the length-1 block in canonical order, so the whole pair-level join map for a letter reads without a full pass. Two traps: `seams` is indexed per codepoint boundary, so map glyphs to codepoints through `clusters` rather than by position, and a ligature glyph owns several codepoint indices.

## Resume

```zsh
make review-cycle
```

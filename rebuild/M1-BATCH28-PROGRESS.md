# M1 batch 28 — qsWay and qsWay_qsUtter

Scratch for the ·Way migration. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsWay.yaml` and `glyph_data/runes/qsWay_qsUtter.yaml` waits for the author's review, and its `# DRAFT` markers mark what to review. The old record has no ductus or notes for either family, so every motion is drafted from the bitmaps.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Apart from the ·Cheer·Tea+Oy break (under the overrides below), each new unmatched row repeats a question the corpus already asks: an existing unmatched window stands beside a ·Way that joins neither side, or the rest of the window draws as it does after another lead (mostly ·May, else ·She, ·J’ai, ·It, ·Zoo, ·Low, ·Jay, or ·Gay, and ·J’ai+Utter or ·Jay+Utter for the ligature), whose unit is in most cases approved. The junction changes among them:
  - `·Way.half ~b~ ·Gay | ·Tea ~b~ ·Day` and the other ·Way·Gay·Tea windows, in default, ss03, and ss04;
  - `·Way.half ~b~ ·Gay | ·It ~b~ ·Day` and `·Way.half ~b~ ·Gay | ·It ~b~ ·Utter`, outside ss04;
  - `·Way.half ~b~ ·Gay ~x~ ·No | ·Ah` and its siblings, where the old font draws `·Way | ·Gay ~b~ ·No.alt ~b~ ·Ah`;
  - `·Way.half ~b~ ·May ~x~ ·Tea` under ss03 and ss03+ss05, and `·Way.half ~b~ ·May ~x~ ·Fee`;
  - `·Way ~x~ ·It ~b~ ·Day` and the same before ·Low and ·Utter, under ss04;
  - `·Way+Utter ~x~ ·They ~b~ ·No`, and `·Way+Utter ~x~ ·Tea | ·Tea` under ss03;
  - `·Way ~x~ ·Day ~b~ ·Tea ~b~ ·Day` and `·Way ~x~ ·Et ~b~ ·Tea ~b~ ·Day` under ss05 and ss03+ss05, where the old font leaves one of those junctions broken;
  - `·Way+Utter ~x~ ·Utter.alt ~b~ ·Low` (Q2 below).

  `·Way.half ~b~ ·Gay ~x~ ·No` keeps the old junctions but draws ·Gay's x-height exit a pixel longer (qsGay's extension before ·No). The ·She·Gay·No units carry the same extension and an open `neither` verdict asking for a one-pixel reduction.
- Two oracle questions from the batch's placed-ink check stay open past the batch, as open forks in the gates in `WHATNEXT.md`: whether a class marked `ink_identical: true`, which the oracle enforces only through its position check, may hold rows whose placed ink differs from the old font's, and whether that position check should blame a shift on a kern earlier in the row. Q7 to Q9 below accept the batch's rows that each question touches, so neither holds up the batch.
- Two refusals change no drawing (Q3 below): qsDay's `refuse[0]`, which keeps half-·Day's baseline entry from ·Way, and qsWay's refusal toward ·Thaw. qsWay's appears in the build's `unused_records` (`rebuild/out/m1/pipeline_summary.json`). qsDay's leaves `waiting_on_unmigrated` without entering `unused_records`, because the settlement engine cites it while tabulating; removing it changes no table, rule, or audit row. Issue #442's item on the §5 worked examples of `doc/rebuild-design.md` (·It·No·Owe, ·Way·Thaw, the ·Excite·Tea·Oy guard, ·Day·He·-ing) is ticked with the reason that the code cannot check them while qsWay has no rune. With the rune in place, the build checks the ·Way·Thaw part: §5.6 says the refusal yields no joining candidate and compiles to no `ignore sub`, and it holds: `rebuild/out/m1/M1.fea` has no rule from the record, and `unused_records` lists it. Recording the result on the issue is a GitHub mutation on a public repo, so it waits for the author.
- Joins with partners that are not migrated, to re-verify at each partner's migration, where the partner's entry row must admit the lead:
  - `·Way ~x~ ·Llan`, `·Way ~x~ ·Owe`, and `·Way ~x~ ·Foot`, which the full stance's `toward:` names. Under ss06 and ss06+ss07 the old font's gapped ·Owe breaks the ·Way·Owe pair.
  - `·Way.half ~b~ ·-ing` and `·Way.half ~b~ ·Exam`, which the half's `toward:` names.
  - `·Way+Utter ~x~ ·Llan`, `·Way+Utter ~x~ ·Owe`, and `·Way+Utter ~x~ ·Foot`, old pair joins that the inherited open exit admits once those entry rows admit qsUtter.
- qsWay's preference before ·Gay (Q1 below) gains qsIng and qsEat in its follower list at their migrations: the old font draws `·Way.half ~b~ ·Gay | ·-ing` and `·Way.half ~b~ ·Gay | ·Eat`, the same tie. qsExam stays out, because the old font draws `·Way | ·Gay ~b~ ·Exam`, which `order:` reproduces. Before ·May and ·Roe, the followers ·-ing, ·Excite, and ·Exam keep ·Way's join in the old font, and the unconditional ·May/·Roe preference already gives that.
- Old records that name ·Way in families that are not migrated, to reconcile at those migrations: the `not_after` list of ·He's `entry_baseline` stance, the `after:` lists of ·Excite's `exit_baseline_before_vertical_after_baseline_letter` and `entry_baseline_noexit` stances, and the `not_after` of qsDay_qsEat's `half` stance. ·Cheer's old `noentry_after` also names ·He and ·Why, so the plain `qsCheer.noentry` alias (Q4 below) covers `·He | ·Cheer` and `·Why | ·Cheer` too, and the placed-ink check repeats for those rows at their migrations.
- Noticed during the batch, each for its own commit:
  - With any of ·Bay, ·Day, ·Key, ·Gay, ·Zoo, ·She, ·J’ai, ·Cheer, ·Jay, ·Et, or ·Awe as the lead ·X, the old font draws `·X | ·Gay ~b~ ·Out` and the rebuild groups the window differently, in `regrouped-chain` units with no verdict. Adding qsOut to qsGay's `prefer[0]` restores the old drawing for all of those leads and leaves ·May·Gay·Out (`bare-name-live-join`) as it is. ·Way needs no such change: `order:` reproduces `·Way | ·Gay ~b~ ·Out`.
  - The rebuild draws `·May | ·Gay ~b~ ·Thaw` and its siblings where the old font keeps ·May's join, in `regrouped-chain` units with no verdict. qsMay's `order:` settles the same tie that qsWay's preferences settle (Q1), so the same form of preference may suit qsMay.
  - qsFee's `loop` x-height `toward:` lists `qsJay_qsUtter` twice. It also names qsVie_qsUtter, which enters only at the baseline, qsSee_qsUtter and qsWay_qsUtter, which have no entries, and qsWhy_qsUtter, which is not migrated. The first three can never take an x-height join.
  - The `count:` of `zwnj-word-initial-unification` in `rebuild/m1-divergences.yaml` does not match that class's rows in `rebuild/out/m1/divergence-audit.tsv`.

## Recorded design overrides

·Way's half is a stance, `half` with `traits: [half]`, rather than a binding. The Manual pins ·Way brings into scope name it (`·Way.half ~b~ ·I | ·Low ~x~ ·Day`, `·Way.half ~b~ ·I ~x~ ·Zoo`), and `manual_pins._stance_traits` reads `.half` from a stance's traits, which a cell binding cannot carry. The half requires its exit, following qsAt's `falling` stance, because the old half never dangles. That forces `order: [full, half]`: the first stance in `order:` is the cmap glyph, the ss10 overlay's isolated cell, and the formation guard's unjoined lead.

·Way keeps its half's join where the baseline follower could instead join the next letter for the same join count (Q1). The old font draws `·Way.half ~b~ ·Gay | ·Thaw`, `·Way.half ~b~ ·May | ·Gay`, `·Way.half ~b~ ·Roe | ·Thaw`, and their siblings, and gives the junction to the follower only where the follower has its own yield (qsGay's `prefer[0]`, qsAt's `prefer[0]`) and in `·Way | ·Gay ~b~ ·Out`. In the rebuild those windows tie on join count, and `order: [full, half]` alone settles them on the full stance. The rune writes the old behavior as two yields-to-joins preferences of the baseline exit over no exit, shaped like qsEight's `prefer[1]`: one before ·May and ·Roe, and one before ·Gay when ·Gay is followed by ·Gay, ·Thaw, ·Vie, ·Vie+Utter, ·See, ·May, ·Low, ·At, ·Ah, or ·Utter. A single record before ·Gay, ·May, and ·Roe fails the build with `E-INCOMPARABLE` against qsGay's `prefer[0]`, because `Engine::apply_prefers` compares each rune's preference in its own frame and neither record's right-hand family set contains the other's; issue #478 holds that frame question open. With the ·Gay follower list disjoint from qsGay's `prefer[0]` list, both records settle in every ·Way·Gay, ·Way·May, and ·Way·Roe window. Mirroring qsGay's `prefer[0]` with a `then:` and `except:` chain also builds, but it duplicates qsGay's list and diverges at ·Gay·Out and at ss04 ·Gay·It·Day. qsGay's `prefer[0]` is unedited, and neither new record has an `except:`, so `CHAIN_BEARING_EXCEPT_RECORDS` in `rebuild/test_spec_load.py` is unchanged.

·Way+Utter inherits ·Utter's open x-height exit through `outgoing: {stance: mono}`, with no authored `toward:` list and no `exceptions` (Q2), the shape of qsVie_qsUtter and qsDay_qsUtter. In the old font the ligature joins exactly where ·J’ai+Utter, ·Day+Utter, ·Vie+Utter, and ·Jay+Utter join. The inherited exit joins where qsJay_qsUtter's authored list joins, including `·Way+Utter ~x~ ·Utter.alt ~b~ ·Low`, where the old font breaks. The author chose that join for ·Jay+Utter, and it is approved after ·Vie+Utter, ·Day+Utter, and ·Jay+Utter. Authoring qsJai_qsUtter's list would keep the old break, and authoring qsJay_qsUtter's list with an exception gives the same output as inheriting but needs each later receiver added by hand.

Two refusals stay although neither changes any drawing (Q3). qsDay's `refuse[0]` is unchanged, and qsWay refuses any join into ·Thaw, the unilateral veto of `doc/rebuild-design.md` §5.6. That record's `why:` is the §3.3 sample copied byte for byte, “Joined ·Way·Thaw is ugly and awkward to write by hand.”, with the author's explicit approval, and it is the only `why:` this batch writes in a rune. The half's baseline `toward:` does not name ·Day, and neither stance's `toward:` names ·Thaw, so both records are inert. qsWay's refusal appears in `unused_records`. qsDay's does not, because the settlement engine cites it while tabulating; a scratch build without it gives the same settlement and join tables, GSUB rules, and divergence audit.

`qsCheer.noentry` maps to ·Cheer's plain cell, `{rune: qsCheer, stance: sole}`, not to the locked copy (Q4). ·Cheer's old `noentry_after` names ·Way, so after ·Way the old font draws `qsCheer.noentry` as an after-letter variant, and the rebuild draws ·Cheer's plain cell there because the pair breaks. `qsThey_qsUtter.noentry`, the other `noentry_after` variant that follows ·Way, maps the same way. With the plain alias, only the ·Way·Cheer rows where ·Cheer joins onward differ at name grain, and `zwnj-word-initial-unification`, which has no `no_verdict`, takes those whose only difference is ·Cheer's onward exit (Q5); the locked alias sends every ·Way·Cheer row there. The post-ZWNJ ·Cheer rows, which the locked alias matches exactly, become name-grain rows that `boundary-window` takes. The header of `rebuild/m1-aliases.yaml` states the `.noentry` convention with this after-letter exception, and the class's `why:` in `rebuild/m1-divergences.yaml` says what the class covers: the old font's `.noentry` after-letter variants that reach it, ·Thaw after a Tall letter and ·Cheer after ·Way, with no claim about the namer dot (Q6). No subset table has a `.noentry` glyph right after the namer dot. ·They+Utter's `noentry_after` variant reaches the class in no row, because every divergent row that has it also differs elsewhere. The `why:` also says that the ·Way·Cheer·It·Roe rows are not ink-identical end to end (Q8).

`classify_divergence` in `rebuild/pipeline/oracle.py` gives a row `zwnj-word-initial-unification` only when its `.noentry` name is its only difference, and `zwnj-follower-exit-restored` only for the post-ZWNJ ruling that class records (Q5). The name accounts for the `old-noentry` and lock tokens, and for an onward exit that a `.noentry` name with no `.ex-` suffix does not spell. Every other row is classified by its other tokens, as if the name were the plain one, or stays unmatched. The follower class takes a gained junction only when it is the right-side join of a `.noentry` glyph that follows a ZWNJ or the namer dot. `boundary-window` takes the ZWNJ rows first, and the old font draws no `.noentry` glyph right after the namer dot, so the class takes no row. The change lands as its own commit ahead of `Add ·Way`, does not depend on qsWay, and moves the ss03 ·May·They+Utter·Tea rows and their ·No, ·Low, and ·At siblings out of `zwnj-word-initial-unification`.

After the Q5 fall-through, some rows whose placed ink differs from the old font's sit in `bare-name-live-join` and `dangling-anchor-dropped`, each beside its plain sibling, the same window without the `.noentry` name, which shows the same difference (Q7). The ss03 full ·Tea after ·They+Utter, or after ·Cheer and a letter with an x-height exit, is in `bare-name-live-join`, where ss03 ·They+Utter·Tea and ·Cheer·May·Tea are; the ss03 ·May·They+Utter·Tea rows and their ·No, ·Low, and ·At siblings, which have no ·Way, are among them. The contraction before ·J’ai, which the rebuild puts on the other glyph of the junction for the same pixels, is in `dangling-anchor-dropped`, where ·Cheer·Low·J’ai and its siblings are. ·X·Way·Cheer·Gay and ·X·Way·Cheer·No are in `dangling-anchor-dropped` too (Q9). The author accepts these rows as they stand: they are not a batch defect. That the name-grain classes declare `ink_identical: true` while the oracle enforces only positions is an open fork in `WHATNEXT.md`. The batch's placed-ink check covers `zwnj-word-initial-unification` alone, with Q8's exception.

The ·Way·Cheer·It·Roe rows stay in `zwnj-word-initial-unification`, with no exclusion, although their placed ink differs (Q8). The `qsRoe.en-ext-1-at-5` alias maps the old shortened-bottom ·Roe to the rebuild's sole cell with the same entry extension, so the redrawn baseline bar is no name-grain difference and the `.noentry` name is the row's only one. The `roe-baseline-bar-kept-after-it` standing approval covers the redraw, as it does for ·Cheer·It·Roe in `bare-name-live-join`. The open fork in `WHATNEXT.md` on the `ink_identical: true` classes covers these rows with the Q7 rows.

In ·X·Way·Cheer·Gay and ·X·Way·Cheer·No, where the old font kerns the lead ·X against ·Way, the oracle's position check blames the one-pixel shift from ·Gay's entry contraction or ·Cheer's exit contraction on that kern, so the rows keep `dangling-anchor-dropped`, while ·Way·Cheer·Gay, ·Way·Cheer·No, ·Cheer·Gay, and ·Cheer·No, with the same shift and no kern before it, stay unmatched (Q9). The author accepts this as the oracle's existing behavior: `_position_mismatch` in `rebuild/pipeline/oracle_positions.py` counts a mismatch as the kern's when every mismatching slot follows a kern-attributable slot, one whose old advance carries a kern or that sits next to a ZWNJ, and a row in a single ink-identical class keeps that class when its mismatch is the kern's and no class takes the row with the mismatch. The batch leaves the check as it is, and `WHATNEXT.md` holds the open oracle question.

The rebuild breaks `·Way | ·Cheer | ·Tea+Oy`, where the old font draws `·Way ~x~ ·Cheer | ·Tea+Oy` in every configuration. The old font breaks the ·Way·Cheer pair in every other window (·Cheer's `noentry_after`), and before ·Tea+Oy it leaves ·Cheer bare, so the bare anchors attach. A join change is pair-wide, so the full stance's `toward:` leaves ·Cheer out; keeping the one join would need a record conditioned on the letter after ·Cheer. No approved unit matches the window, so it reaches the review session unmatched.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E661 E661:E653 E661:E65B E661:E65D E661:E65F E661:E666 E661:E670 E661:E672 E661:E673 E661:E677 E661:E678 \
  E661:E653:E67A E661:E65D:E67A E661:E65F:E67A \
  E661:E655 E661:E665 E661:E667 E661:E668 E661:E674 E661:E675 E661:E676 E661:E67B E661:E67B:E652 \
  E661:E650 E661:E652 E661:E656 E661:E658 E661:E659 E661:E65A E661:E65E E661:E660 E661:E661 E661:E679 E661:E67E E661:E652:E679 E661:E657:E67A \
  E650:E661 E665:E661 E670:E661 E67A:E661 E653:E67A:E661 \
  E661:E655:E656 E661:E655:E659 E661:E655:E65A E661:E655:E665 E661:E655:E667 E661:E655:E674 E661:E655:E676 E661:E655:E67A E661:E655:E655 E661:E655:E659:E67A \
  E661:E665:E655 E661:E665:E656 E661:E665:E67B E661:E668:E656 E661:E668:E665 E661:E668:E666 E661:E666:E655 E661:E65D:E655 E661:E670:E668 \
  E661:E655:E652 E661:E655:E653 E661:E655:E65B E661:E655:E668 E661:E655:E670 E661:E655:E675 E661:E655:E67B E661:E674:E665 E661:E655:E666 E661:E655:E666:E676 E661:E665:E658 \
  E661:E655:E670:E653 E661:E655:E670:E67A E650:E661:E655:E652 E665:E661:E655:E656 \
  E661:E655:E652:E653 E661:E652:E653 E661:E653:E652:E653 E661:E672:E652:E653 E661:E675:E652:E653 E661:E67B:E652:E653 \
  E661:E65E:E653 E661:E65E:E652:E679 E661:E665:E652 E661:E670:E653 E661:E670:E667 E661:E670:E67A \
  E661:E67A E661:E67A:E650 E661:E67A:E651 E661:E67A:E652 E661:E67A:E653 E661:E67A:E655 E661:E67A:E655:E65D E661:E67A:E657 E661:E67A:E658 E661:E67A:E65B E661:E67A:E65D E661:E67A:E65E E661:E67A:E660 E661:E67A:E665 E661:E67A:E666 E661:E67A:E668 E661:E67A:E670 E661:E67A:E679 \
  E661:E67A:E656 E661:E67A:E659 E661:E67A:E65A E661:E67A:E667 E661:E67A:E674 E661:E67A:E675 E661:E67A:E676 E661:E67A:E67B E661:E67A:E67E E661:E67A:E654 E661:E67A:E67A E661:E67A:E67A:E667 \
  E661:E67A:E652:E653 E661:E67A:E652:E652 E661:E67A:E657:E666 E661:E67A:E665:E653 E661:E67A:E670:E653 \
  200C:E661 200C:E661:E655 E661:200C:E653 E661:200C:E67A 200C:E661:E67A 00B7:E661 00B7:E661:E67A \
  E665:E655:E656 E65C:E655:E67B E665:E655:E652
make test-rebuild
make test
make artifact-cycle
uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-series.txt
uv run python -m rebuild.pipeline.coretext_smoke --font rebuild/out/m1/M1.otf
make verdict-ready
```

After the first green `run_m1` and before the commit, check placed ink for every row that `zwnj-word-initial-unification` takes. The accepted result prints only the ·Way·Cheer·It·Roe rows (Q8). Any other row it prints is one the class cannot take: stop and report it. Rows whose placed ink differs in `bare-name-live-join` and `dangling-anchor-dropped` are outside this check (Q7).

```zsh
PYTHONPATH=. uv run python - <<'EOF'
import csv
from rebuild.review.ink import InkComparator
ink = InkComparator("site/AbbotsMortonSpaceportSansSenior-Regular.otf", "rebuild/out/m1/M1.otf")
rows = [r for r in csv.DictReader(open("rebuild/out/m1/divergence-audit.tsv"), delimiter="\t") if r["matched_entry"] == "zwnj-word-initial-unification"]
for r in rows:
    if not ink.ink_identical("".join(chr(int(c, 16)) for c in r["codepoints"].split(":")), (r["config"],)):
        print(r["config"], r["codepoints"])
EOF
```

The ·Way block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, yield, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff; the plain `qsCheer.noentry` alias moves the post-ZWNJ ·Cheer rows into `boundary-window`, so that class's figure moves.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```

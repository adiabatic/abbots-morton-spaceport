# DirectWrite: deferral note for the week-one prototype

## Decision

DirectWrite verification is deferred for week one, per the recommendation in `prototype/recon/shapers.md` §3. The prototype’s cross-shaper matrix is HarfBuzz (exhaustive, `prototype/conform.py`) plus CoreText (curated, `prototype/coretext_smoke.py`) — two independent implementations of the contested semantics.

## Why it cannot run here

DirectWrite is a Windows-only COM API (`dwrite.dll`) with no macOS port. Wine’s `dwrite` is an independent reimplementation whose shaping core is not Microsoft’s, so a Wine pass would prove nothing about real DirectWrite behavior — a Wine harness is worse than useless as evidence and is deliberately not built.

## Why deferral is acceptable for week one

- The contested semantics — within-lookup sequential substitution, where the backtrack of a chained-context rule sees glyphs already substituted by earlier rules in the same lookup (“backtrack-sees-settled”) — is OpenType-spec-mandated behavior, not a HarfBuzz quirk.
- The shipping 30,838-line font already relies on exactly this behavior and ships against DirectWrite users today without reported breakage of its contextual joins.
- HarfBuzz and CoreText agreement (the (a) and (b) legs of the verification matrix) covers two independently developed implementations of the same semantics; a third independent implementation diverging from both an explicit spec mandate and two major implementations is a low-probability, detectable-later risk.
- The kill criteria of PLAN.md §6d are measurable without DirectWrite: K1 and K2 are size arithmetic, and K3 (semantics) is testable on the two locally available shapers. A DirectWrite-only divergence discovered later would reopen K3 but would not invalidate the budget conclusions.

## What closes the gap (filed follow-up)

A `windows-latest` GitHub Actions job running a roughly 60-line harness (C# or Python + ctypes) around `IDWriteTextAnalyzer::GetGlyphs` / `GetGlyphPlacements`, asserting the same GID-and-position table that `prototype/coretext_smoke.py` asserts, over the same curated sequence file (`prototype/smoke_sequences.txt`). Priority assertions, because DirectWrite is the one implementation whose default-ignorable-in-context behavior we have not observed locally:

1. **Lock fires, join does not** (`recon/shapers.md` §4 case 1): `X ZWNJ Y` must yield the locked/no-entry forms, never the joined contextual forms, even though the ZWNJ is invisible.
2. **ZWNJ in every rule slot** (case 3): `ZWNJ X Y`, `X ZWNJ Y`, and `X Y ZWNJ Z` must all resolve the boundary-outcome rows rather than letting a join rule match across the skipped slot.
3. **The leak_demo witness shapes** (PLAN.md deviation 13; `prototype/leak_demo.py` proves HarfBuzz leaks on these when the defenses are stripped, so they are the sharpest known probes of default-ignorable context matching): `It ZWNJ Tea Oy` (ZWNJ adjacent to a backtrack-classed rule on a never-locked input — the identity guard `sub uni200C qsTea_qsOy' by qsTea_qsOy;` must win), `It May ZWNJ Oy` and `Tea It May ZWNJ Oy` (ZWNJ at the second lookahead slot of a real two-slot rule — the boundary row with explicit `uni200C` must win over skipping to the qsOy beyond the break). All three are in `prototype/smoke_sequences.txt`.

This job must land before the rebuild lands; until then, every REPORT.md conclusion about cross-shaper safety carries the caveat “verified on HarfBuzz and CoreText; DirectWrite deferred.”

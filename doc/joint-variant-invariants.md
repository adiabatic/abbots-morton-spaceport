# Joint-variant invariants on adjacent pairs

Many cursive-attachment regression tests in `test/test_calt_regressions.py` assert that the shaped output never contains an adjacent pair of glyphs whose chosen variants fall in a forbidden combination, whatever surrounds the pair. This page gives the set math behind that pattern, so that tests built on `_collect_pair_with_forbidden_trait_co_occurrence_failures` (and the narrower `_collect_left_must_stay_isolated_before_right_failures`) can be read without working it out from the code.

## Why the pattern matters

A GSUB lookup that picks the left glyph’s variant usually sees the right glyph as it is before later lookups substitute it. If a later lookup turns the right glyph into an incompatible variant, the left glyph’s choice is wrong. Sweeping every surround catches these choices: if any surround produces the forbidden combination, the test fails.

## The set math

For each adjacent slot pair `(i, i+1)` in the shaped output, let

```text
Lᵢ    = meta_map[glyphs[i]]               # left glyph's compiled metadata
Rᵢ    = meta_map[glyphs[i + 1]]           # right glyph's compiled metadata
Tₗᵢ   = Lᵢ.traits                         # actual trait set on the chosen left variant
Tᵣᵢ   = Rᵢ.traits                         # actual trait set on the chosen right variant
```

and let the caller’s inputs be

```text
L*    = left_base                         # base name the left slot must equal
R*    = right_base                        # base name the right slot must equal
Fₗ    = forbidden_left_traits             # trait subset the left must carry to count
Fᵣ    = forbidden_right_traits            # trait subset the right must carry to count
```

The slot pair is _in scope_ when both base names match. A ligature also matches: the left matches when `Lᵢ.sequence` ends with `L*`, and the right matches when `Rᵢ.sequence` starts with `R*`. Every `_collect_pair_*` helper uses this convention. The slot pair is _forbidden_ when

```text
Fₗ ⊆ Tₗᵢ   ∧   Fᵣ ⊆ Tᵣᵢ              (★)
```

that is, when every trait in `forbidden_left_traits` is on the left’s chosen variant _and_ every trait in `forbidden_right_traits` is on the right’s chosen variant.

## Why subset and not equality

A compiled variant can carry several traits at once, such as `"alt"` and `"half"`, and new traits may be added later. With set equality, adding a trait to a forbidden variant would stop the test from matching it: an alt-half-·Way would make `{"half"} == traits` false, and the defect would go undetected. With subsets, adding traits does not change what the test catches, and the caller can say “must include this trait” without listing every other trait the variant may carry. Pass `Fₗ = {"alt", "half"}` to require both.

## The empty-set cases

The degenerate cases follow from (★):

- `Fₗ = ∅` makes (★)’s left conjunct always true (every trait set is a superset of ∅), so the sweep flags every in-scope slot pair where the right carries `Fᵣ`. Read this as “the right may never end up with `Fᵣ` immediately after `L*`, whatever the left variant”.
- `Fᵣ = ∅` is the mirror image: “the left may never end up with `Fₗ` immediately before `R*`, whatever the right variant”. `test_way_and_why_stay_full_and_nonjoining_before_right_base_in_context` uses this case with `Fₗ = {"half"}`.
- `Fₗ = ∅ ∧ Fᵣ = ∅` flags every in-scope slot pair, which asserts that `L*` never appears immediately before `R*`. Don’t use the trait helper for that. `_collect_pair_must_not_join_regardless_of_what_comes_before_or_after` is almost always what is meant, because it checks for a cursive join between the two rather than bare adjacency.

## Universal quantification over surround

Let `Σ = _context_chars()`: the 44 plain Quikscript letters plus the two boundary tokens, space and ZWNJ, 46 entries in all. Write `Σ^≤n` for every sequence of 0 to n entries from Σ. The full assertion the helper makes is

```text
∀ before ∈ Σ^≤max_chars_before,  ∀ after ∈ Σ^≤max_chars_after :
    ∀ adjacent (i, i+1) in shape(before · L* · R* · after) :
        in_scope(i)  →  ¬(Fₗ ⊆ Tₗᵢ  ∧  Fᵣ ⊆ Tᵣᵢ)
```

With the default `max_chars_before = max_chars_after = 1`, that is (1 + 46) × (1 + 46) = 2209 shaped strings. With both set to 2 it is (1 + 46 + 46²)² = 2163² ≈ 4.7 M, so consider sharding. `before_first_only` is the sharding parameter: it keeps only the non-empty `before` sequences whose first entry is the named context entry (`"qsPea"`, `"ZWNJ"`, …), and sweeps the empty prefix only in the shard named for the first entry of `_context_chars()`. Parametrized callers use it to spread one logical test across pytest-xdist workers, as with the other `_collect_pair_*` helpers.

## Worked example

`test_no_forbidden_trait_co_occurrence_on_adjacent_pair` checks that half-·Way is never chosen before an alternate ·Utter, whatever the surround:

```python
_collect_pair_with_forbidden_trait_co_occurrence_failures(
    "qsWay", "qsUtter",
    forbidden_left_traits=frozenset({"half"}),
    forbidden_right_traits=frozenset({"alt"}),
)
```

For this call, (★) holds when the left slot is a qsWay variant whose traits include `"half"` _and_ the adjacent right slot is a qsUtter variant whose traits include `"alt"`. An alt-half-·Way before an alt-·Utter also fails the test (`"alt"` is allowed on the left, not required). A half-·Way before a plain ·Utter passes (the right side fails `Fᵣ ⊆ Tᵣ`), and so does a full-·Way before an alt-·Utter (the left side fails `Fₗ ⊆ Tₗ`).

## Reading a failure message

Each failure line names the surround, the chosen glyph names of the matched pair (which can be searched for in the generated FEA), the trait sets on each side, and the forbidden subsets that matched. A short failure list usually points at one FEA rule that needs to be narrowed. A long one usually means the lookup is testing the neighbor’s stance before substitution instead of the stance it ends up with, and the fix belongs in `tools/quikscript_fea.py`, not in the YAML.

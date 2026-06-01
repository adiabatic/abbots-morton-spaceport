# Joint-variant invariants on adjacent pairs

A common shape for cursive-attachment regression tests in this repo is "the shaped output may not contain an adjacent slot pair whose chosen variants land in a forbidden region of the (left-variant × right-variant) product space, no matter what surrounds the pair". This page collects the set math behind that pattern so you can read tests like `_collect_pair_with_forbidden_trait_co_occurrence_failures` (and its narrower sibling `_collect_left_must_stay_isolated_before_right_failures`) without re-deriving it from the code each time.

## Why the pattern matters

A GSUB lookup that picks the left slot's variant typically sees only the predecessor's _pre-substitution_ form of the right slot. If the right slot is about to be substituted into something incompatible, the left's choice gets stranded. The "robust under every surround" sweep is how we catch those stale variant choices in test: if any surround makes the bad combination reachable, the invariant fires.

## The set math

For each adjacent slot pair `(i, i+1)` in the shaped output, let

```text
Lᵢ    = meta_map[glyphs[i]]               # left glyph's compiled metadata
Rᵢ    = meta_map[glyphs[i + 1]]           # right glyph's compiled metadata
Tₗᵢ   = Lᵢ.traits                         # actual trait set on the chosen left variant
Tᵣᵢ   = Rᵢ.traits                         # actual trait set on the chosen right variant
```

and let the caller's inputs be

```text
L*    = left_base                         # base name the left slot must equal
R*    = right_base                        # base name the right slot must equal
Fₗ    = forbidden_left_traits             # trait subset the left must carry to count
Fᵣ    = forbidden_right_traits            # trait subset the right must carry to count
```

The slot pair is _in scope_ when both base names match, with the usual ligature accommodation: the left also matches when `Lᵢ.sequence` ends with `L*`, and the right also matches when `Rᵢ.sequence` starts with `R*`. Every `_collect_pair_*` helper uses this same convention. The slot pair is _forbidden_ when

```text
Fₗ ⊆ Tₗᵢ   ∧   Fᵣ ⊆ Tᵣᵢ              (★)
```

i.e. every trait in `forbidden_left_traits` appears on the left's chosen variant _and_ every trait in `forbidden_right_traits` appears on the right's chosen variant.

## Why subset and not equality

Traits are an additive vocabulary: a single compiled variant can simultaneously be `"alt"` and `"half"`, or carry future traits this pattern doesn't yet know about. Requiring set equality on the chosen variant's `traits` would make every new trait silently un-flag previously-forbidden variants — adding a third trait to an alt-half-·Way would make `{"half"} == traits` false and the bug would walk back in undetected. Subset semantics make the assertion robust to that drift, and let the caller express "must include this trait" without having to enumerate every other trait the variant may legally combine with. Pass `Fₗ = {"alt", "half"}` when you really do want to require both at once.

## The empty-set cases

The two degenerate cases drop out cleanly from (★) and are worth holding in your head:

- `Fₗ = ∅` makes (★)'s left conjunct trivially true (every trait set is a superset of ∅), so the sweep flags every in-scope slot pair where the right carries `Fᵣ`. Read this as "the right may never end up with `Fᵣ` immediately after `L*`, no matter what left variant won".
- `Fᵣ = ∅` is the mirror image: "the left may never end up with `Fₗ` immediately before `R*`, no matter what right variant won". This is exactly the case `test_qs_way_and_qs_why_stay_full_and_nonjoining_before_right_base_in_context` covers, with `Fₗ = {"half"}` (it passes `forbidden_left_traits = {"half"}` to `_collect_pair_with_forbidden_trait_co_occurrence_failures`).
- `Fₗ = ∅ ∧ Fᵣ = ∅` flags every in-scope slot pair unconditionally — i.e. asserts that `L*` may never appear immediately before `R*`. Don't reach for the trait-co-occurrence helper for that; `_collect_pair_must_not_join_regardless_of_what_comes_before_or_after` is what you almost always actually mean, since it adds the cursive-join check on top of bare adjacency.

## Universal quantification over surround

Let `Σ = _context_chars()` (45 entries: every plain Quikscript letter plus ZWNJ). The full assertion the helper makes is

```text
∀ before ∈ Σ^chars_before,  ∀ after ∈ Σ^chars_after :
    ∀ adjacent (i, i+1) in shape(before · L* · R* · after) :
        in_scope(i)  →  ¬(Fₗ ⊆ Tₗᵢ  ∧  Fᵣ ⊆ Tᵣᵢ)
```

With the default `chars_before = chars_after = 1`, that is (1 + 45) × (1 + 45) = 2116 shaped strings; `chars_before = chars_after = 2` is 2071² ≈ 4.3 M, so consider sharding. `before_first_only` is the per-shard hook: it restricts the outer product so the first `before` slot is fixed to the named context glyph (`"qsPea"`, `"ZWNJ"`, …), letting parametrized callers fan a single logical test across pytest-xdist workers exactly the same way the other `_collect_pair_*` helpers do.

## Worked example

The motivating case is "half-·Way must never be chosen before an alternate ·Utter, no matter the surround":

```python
_collect_pair_with_forbidden_trait_co_occurrence_failures(
    "qsWay", "qsUtter",
    forbidden_left_traits=frozenset({"half"}),
    forbidden_right_traits=frozenset({"alt"}),
)
```

Read (★) for this call: it fires when the left slot is some qsWay variant whose traits include `"half"` _and_ the adjacent right slot is some qsUtter variant whose traits include `"alt"`. An alt-half-·Way before an alt-·Utter would also fire (`"alt"` is allowed on the left, just not required); a plain half-·Way before a plain ·Utter would not (right side fails `Fᵣ ⊆ Tᵣ`); a full-·Way before an alt-·Utter would not (left side fails `Fₗ ⊆ Tₗ`).

## Reading a failure message

Each failure line names the surround, the matched pair's chosen glyph names (so you can grep them in the generated FEA), the actual trait sets on each side, and the forbidden subsets that triggered (★). A short failure list usually points at a single FEA rule that needs to be tightened; a long one usually means the underlying lookup is conditioning on the predecessor's _pre-substitution_ form rather than the form it will eventually take, and the fix lives one layer up in `tools/quikscript_fea.py` rather than in YAML.

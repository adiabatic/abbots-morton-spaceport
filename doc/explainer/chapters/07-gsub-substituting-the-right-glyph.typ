#import "../style.typ": *

= GSUB: substituting the right glyph

`GSUB` is where the font picks context-appropriate variants.

== a. `calt` (contextual alternates)

This is the main engine for Quikscript joining.

Backward-looking rule example (choose entry variant from previous exit height):

```fea
lookup calt_qsPea {
    sub @exit_y6 qsPea' by qsPea.entry-y6;
} calt_qsPea;
```

Forward-looking rule example (choose exit variant from next entry height):

```fea
lookup calt_fwd_qsGay {
    sub qsGay' @entry_y5 by qsGay.exit-xheight.extended;
    sub qsGay' @entry_y0 by qsGay.exit-baseline;
} calt_fwd_qsGay;
```

Explicit overrides from YAML (`calt_before`, `calt_after`, `calt_not_before`, `calt_not_after`) become dedicated lookups or `ignore sub` guards:

```fea
lookup calt_pair_qsNo_alt_after-it {
    sub [qsIt qsIt.exit-baseline qsIt.exit-xheight qsVie qsVie.exit-baseline] qsNo' by qsNo.alt.after-it;
} calt_pair_qsNo_alt_after-it;
```

Word-final forms:

```fea
lookup calt_word_final_qsOut_fina {
    sub qsOut by qsOut.fina;
} calt_word_final_qsOut_fina;
```

Rule ordering matters because one substitution can create a new height context for the next rule. The build script topologically sorts dependencies and, for cycles, emits a `lookup calt_cycle` block.

== b. Ligature substitutions

Conceptually this is the `liga` step: replace a known sequence with one ligature glyph. In this Senior build, these substitutions are emitted in `lookup calt_liga`:

```fea
lookup calt_liga {
    sub qsDay qsUtter by qsDay_qsUtter;
    sub qsWay qsUtter by qsWay_qsUtter;
    sub qsThey qsZoo by qsThey_qsZoo;
} calt_liga;
```

Ligatures can carry cursive anchors too. For example, `qsDay_qsUtter.prop` has both entry and exit anchors in YAML.


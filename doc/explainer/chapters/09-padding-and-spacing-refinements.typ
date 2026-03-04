#import "../style.typ": *

= Padding and spacing refinements

Some pairings need extra breathing room even when anchors match.

== a. `pad_entry_after`

If a glyph has `pad_entry_after`, the build creates `.entry-padded` variants with entry anchor shifted left one pixel; cursive attachment then places the glyph one pixel farther right on screen.

Real data source:

```yaml
qsVie.prop:
  cursive_entry: [1, 0]
  pad_entry_after: [qsHe, qsPea, qsTea, qsIt, qsYe]
```

`·Roe` has a hand-tuned padded form after `·Ye`:

```yaml
qsRoe.entry-padded:
  cursive_entry:
    - [0, 0]
  calt_after: [qsYe]
```

== b. `.noentry` variants and ZWNJ

For Senior, the build auto-generates `.noentry` variants and a `calt` rule that maps a glyph to `.noentry` after `U+200C` ZWNJ:

```fea
lookup calt_zwnj {
    sub uni200C @qs_has_entry' by @qs_noentry;
} calt_zwnj;
```

That removes entry anchors and intentionally breaks the cursive chain.

#try_it([
Type two Quikscript letters that normally connect, then insert `U+200C` between them. The second glyph switches to `.noentry`, so the join disappears.
])


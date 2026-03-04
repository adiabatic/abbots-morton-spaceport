#import "../style.typ": *

= GPOS: positioning the joined glyphs

After GSUB picks shapes, GPOS decides final placement.

== a. `curs` (cursive attachment)

Anchor format in generated FEA is `<anchor X Y>` in font units.

```fea
pos cursive qsRoe.exit-baseline <anchor NULL> <anchor 300 0>;
pos cursive qsLow <anchor 50 0> <anchor 300 250>;
```

The engine offsets glyphs so left exit and right entry anchors overlap along a chain.

Y-grouped lookups are critical. The build emits separate lookups per Y height:

```fea
lookup cursive_y0 { ... } cursive_y0;
lookup cursive_y5 { ... } cursive_y5;
lookup cursive_y6 { ... } cursive_y6;
lookup cursive_y8 { ... } cursive_y8;
```

Without grouping, an entry intended for baseline could attach to an x-height exit and create a false connection.

Multiple entry anchors are supported. `·Roe` is an example:

```yaml
qsRoe.prop:
  cursive_entry:
    - [1, 0]
    - [1, 5]
```

== b. `kern` (kerning)

The font also contains normal kerning logic for Latin text, independent of Quikscript joining. Example:

```fea
lookup kern_f-before-short {
    pos [f] [a ae c e i ...] -50;
} kern_f-before-short;
```


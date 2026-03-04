#import "../style.typ": *

= Glyph variants: why one letter needs many shapes

A single logical letter often needs several glyphs.

== a. Monospace vs proportional

The Mono build uses fixed widths and excludes contextual variants. The Sans builds use proportional defaults, where `.prop` forms are renamed to base names during build.

== b. Entry/exit variants

Example family for `·Tea`:

- `qsTea.entry-top`
- `qsTea.entry-baseline`
- `qsTea.entry-xheight`
- `qsTea.exit-baseline`
- `qsTea.entry-top.exit-baseline`

== c. Half-letter variants

Half letters are compact forms used to keep joins smooth. Example:

```yaml
qsPea.prop.half:
  cursive_exit: [6, 5]
  calt_not_before: [qsDay]
```

== d. Alternate forms

`·Utter` and `·No` have alternates that reduce pen lifts in specific contexts:

```yaml
qsUtter.alt.before-may:
  cursive_exit: [6, 0]
  calt_before: [qsMay]

qsNo.alt.after-it:
  cursive_entry: [1, 0]
  cursive_exit: [6, 0]
  calt_after: [qsIt, qsVie]
```

== e. Naming convention

Names are compositional:

- `qsTea.entry-xheight.exit-baseline`
- `qsPea.prop.half`
- `qsDay_qsUtter.prop` (ligature)

Dots separate variant properties. Underscores separate ligature components.


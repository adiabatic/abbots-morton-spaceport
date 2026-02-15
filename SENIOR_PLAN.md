# Senior Quikscript plan

Senior Quikscript reduces pen lifts by using alternate letterforms and ligation. This plan describes how to add these features to Abbots Morton Spaceport Sans.

All Senior features are implemented at the font layer using OpenType GSUB features (`calt`, `liga`, etc.). No new Unicode code points are needed.

## Background: OpenType processing order

OpenType processes features in two phases:

1. **GSUB** (substitution) runs first: `calt`, `liga`, `rlig`, `ss01`, etc.
2. **GPOS** (positioning) runs second: `kern`, `mark`, etc.

All substitutions finish before any positioning begins. This means kerning rules will see the post-substitution glyphs. Glyph classes in kern rules must include any `.alt` variants.

## Letter connection points

Every Quikscript letter starts and ends at a particular height. Short letters connect at the **top** (x-height) or **baseline**. Tall letters can also connect in the **tall region** (ascender height), and deep letters in the **deep region** (descender depth). For example, ·Pea followed by ·Pea connects in the tall region, and ·Day followed by ·Day connects in the deep region. A pen lift occurs when adjacent letters don't share a matching connection height.

Senior features exist to eliminate pen lifts by providing alternate forms that change a letter's start or end point.

### Letters that start at the top

Short: ·No, ·Low, ·Roe, ·Llan, ·It, ·Eat, ·Et, ·Eight, ·At, ·I, ·Ah, ·Awe, ·Ox, ·Oy, ·Utter, ·Out, ·Owe, ·Foot, ·Ooze

Tall/deep: ·Pea, ·Bay, ·Tea, ·Day, ·Key, ·Gay, ·Thaw, ·They, ·Fee, ·Vie, ·See, ·Zoo, ·She, ·Zhivago, ·Cheer, ·Jay, ·Ye, ·Way, ·He, ·Why, ·Oolong, ·May, ·Loch, ·Excite, ·Exam

### Letters that end at the baseline

Most letters end at the baseline. The exceptions are those that end at the top: ·No (both), ·Owe (both), ·Foot (center-bottom).

### Letters that only connect leftward at the baseline

These are the letters where, if preceded by a letter ending at the top, there would be a pen lift: ·Thaw, ·Fee, ·Vie, ·See, ·She, ·Cheer, ·Ye, ·He, ·Oolong, ·May, ·Low.

## Phase 1: Alternate letterforms (done / in progress)

### Glyphs

- [x] `uniE67A.alt` — Alternate ·Utter (mirrored ·Roe shape; ends at top instead of baseline)
- [x] `uniE666.alt` — Alternate ·No (·No flipped upside down; ends at baseline instead of top)

### `calt` rules needed

Alternate ·Utter (`uniE67A.alt`) is used before letters that only connect leftward at the baseline:

```fea
sub uniE67A' [uniE656 uniE658 uniE659 uniE65A uniE65C uniE65E uniE660
              uniE662 uniE664 uniE665 uniE667] by uniE67A.alt;
```

Alternate ·No (`uniE666.alt`) is used before letters that only connect leftward at the baseline, and after ·She (for -tion endings):

```fea
sub uniE666' [uniE656 uniE658 uniE659 uniE65A uniE65C uniE65E uniE660
              uniE662 uniE664 uniE665 uniE667] by uniE666.alt;
sub uniE65C uniE666' by uniE666.alt;
```

### Build pipeline changes needed

`build_font.py` currently only generates GPOS features (kern, mark). To support `calt`:

1. Add a `generate_calt_fea()` function (or extend `opentype-features.yaml` with a `calt` section).
2. Prepend the generated GSUB code before the existing GPOS code when calling `addOpenTypeFeaturesFromString()`.
3. Update kern glyph classes to include `.alt` variants where appropriate.

## Phase 2: Ligation

Ligation connects adjacent letters into joined forms when their connection points match. This is the most complex phase and requires the most new glyphs.

### Approach options

1. **True ligatures** (`liga`): Create combined glyphs for common pairs. Produces the best results but requires many glyphs (N*M potential pairs).
2. **Contextual adjustment** (`calt` + `kern`): Use alternate glyphs with adjusted spacing to simulate connection. Fewer glyphs needed but less precise.
3. **Cursive attachment** (`curs`): Use GPOS cursive attachment to connect letters at matching anchor points. No extra glyphs needed but limited to positioning — can't change letterforms. Historically only used by RTL and vertical scripts, but research suggests modern shaping engines handle LTR `curs` fine.

The best approach is likely a combination: use `curs` for simple connections where letters already match, and `liga` for pairs that need shape changes.

### Prerequisites

- Phase 1 should be complete first
- Connection point anchors need to be defined for each letter (entry/exit points for `curs`)
- Common letter pairs in Quikscript need to be identified to prioritize ligature design

## Implementation order

1. Add `calt` infrastructure to `build_font.py`
2. Wire up Phase 1 rules (alternate ·Utter and ·No)
3. Add `curs` anchors for simple ligation
4. Design and add ligature glyphs for common pairs

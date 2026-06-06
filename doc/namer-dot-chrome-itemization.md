# The namer dot, Chrome, and PUA text itemization

## The finding

The contextual namer-dot lowering (`periodcentered` → `periodcentered.lowered` before a short letter, gated behind `calt`; see `emit_namer_dot_calt` in `tools/quikscript_fea.py`) works in Firefox and Safari but **does not fire in Chrome** when the dot is typed as U+00B7. Chrome still applies the font's `calt` for letter-to-letter joins, so it is not that `calt` is off — only the dot refuses to drop.

## Root cause

Chrome puts U+00B7 (Unicode script **Common**) in its own shaping run, separate from the Quikscript letters, which live in the Private Use Area (script **Unknown**). A contextual substitution needs the dot and the following short letter in the _same_ run to see each other; once Chrome splits them, the lookahead context never exists and the substitution can't fire. Firefox and Safari keep the dot in the same run as the following letter, so the lowering works there.

This is a Chrome text-**itemization** difference, not a font defect:

- Chrome and Firefox both shape with HarfBuzz, yet only Chrome fails on the same font file. Since the shaper is identical, the only thing that can differ is the run handed to it — i.e. the segmentation.
- Two PUA letters share a run (script Unknown on both sides), which is why cursive joins like ·Out·Oy render correctly in Chrome.
- The dot stays isolated regardless of neighbors: a dot between two PUA letters (Oy·No) does not drop in Chrome either, so it is not a "Common attaches to the preceding run" case — Chrome isolates U+00B7 outright.

## How it was diagnosed

A throwaway isolation page (`site/calt-namer-test.html`, own `@font-face`, unique family name, large text, `calt` on vs. `font-feature-settings: "calt" 0`) ruled out every other explanation:

| Row | Text                                        | Chrome                   | Safari / Firefox |
| --- | ------------------------------------------- | ------------------------ | ---------------- |
| A   | Out + Oy (two PUA letters)                  | joins toggle with `calt` | join             |
| B   | U+00B7 + No (dot + short)                   | dot does **not** drop    | dot drops        |
| C   | U+00B7 + Pea (dot + tall)                   | no drop (correct)        | no drop          |
| D   | a + U+00B7 + No (letter + dot + short)      | no drop (correct guard)  | no drop          |
| E   | space + U+00B7 + No                         | dot does **not** drop    | dot drops        |
| F   | Oy + U+00B7 + No (PUA letter + dot + short) | dot does **not** drop    | —                |
| G   | **U+F00B7** + No (PUA-encoded dot + short)  | **dot drops**            | dot drops        |
| H   | U+F00B7 + Pea (PUA-encoded dot + tall)      | no drop (correct)        | —                |

Earlier dead ends, recorded so they are not re-tried: it is **not** an installed-font / font-cache problem (it reproduces on a machine that never had the font installed, in a fresh Chrome), and it is **not** HTTP caching (a guest profile and hard refresh do not change it).

## The fix that works

Aliasing a PUA code point to the namer-dot glyph makes Chrome keep the dot in the same run as the following PUA letter, so the existing `calt` rule fires unchanged. `tools/build_font.py` maps **U+F00B7** — Supplementary PUA-A, Plane 15, a mnemonic for the `00B7` middle dot — to the `periodcentered` glyph in the proportional builds:

```python
if is_proportional and _NAMER_DOT in cmap.values():
    cmap[_NAMER_DOT_PUA] = _NAMER_DOT
```

No new glyphs and no change to the `calt` emitter are needed: the lookup matches the `periodcentered` _glyph_, so a dot reaching that glyph via either U+00B7 or U+F00B7 is lowered identically — the only thing that changes is which run Chrome assigns it to. U+F00B7 carries Unicode script Unknown, exactly like the Quikscript letters, so Chrome keeps it in their run and the lowering fires (verified in Chrome). Being on a supplementary plane, it pulls in a format-12 cmap subtable, which `FontBuilder.setupCharacterMap` adds automatically.

## Implications and open questions

- A dot typed as plain U+00B7 still will not lower in Chrome — only the PUA-encoded dot does. So this is a workflow/convention decision, not a transparent fix: text that wants the Chrome behavior must use the PUA code point.
- Open: how the namer dot gets authored/emitted as U+F00B7 (the site pages can emit it directly; real authoring needs an input path).
- Mono is unaffected either way — it has no `calt`, so the dot never lowers there.

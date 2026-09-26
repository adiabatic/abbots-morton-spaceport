# The namer dot, Chrome, and PUA text itemization

## The finding

The contextual namer-dot lowering (`periodcentered` → `periodcentered.lowered` before a short letter, gated behind `calt`; see `emit_namer_dot_calt` in `tools/quikscript_fea.py`) works in Firefox and Safari but **does not fire in Chrome** when the dot is typed as U+00B7. Chrome still applies the font’s `calt` for letter-to-letter joins, so it is not that `calt` is off — only the dot refuses to drop.

## Root cause

Chrome puts U+00B7 (Unicode script **Common**) in its own shaping run, separate from the Quikscript letters, which live in the Private Use Area (script **Unknown**). A contextual substitution needs the dot and the following short letter in the _same_ run to see each other; once Chrome splits them, the lookahead context never exists and the substitution can’t fire. Firefox and Safari keep the dot in the same run as the following letter, so the lowering works there.

This is a Chrome text-**itemization** difference, not a font defect:

- Chrome and Firefox both shape with HarfBuzz, yet only Chrome fails on the same font file. Since the shaper is identical, the only thing that can differ is the run handed to it — i.e. the segmentation.
- Two PUA letters share a run (script Unknown on both sides), which is why cursive joins like ·Out·Oy render correctly in Chrome.
- The dot stays isolated regardless of neighbors: a dot between two PUA letters (Oy·No) does not drop in Chrome either, so it is not a “Common attaches to the preceding run” case — Chrome isolates U+00B7 outright.

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

## A workaround that would work — but which we are not taking

Aliasing a PUA code point to the namer-dot glyph would make Chrome keep the dot in the same run as the following PUA letter, so the existing `calt` rule fires unchanged. The candidate was **U+F00B7** — Supplementary PUA-A, Plane 15, a mnemonic for the `00B7` middle dot — mapped to the `periodcentered` glyph in the proportional builds. It works because the lookup matches the `periodcentered` _glyph_, so a dot reaching that glyph via either U+00B7 or U+F00B7 is lowered identically; the only thing that changes is which run Chrome assigns it to. U+F00B7 carries Unicode script Unknown, exactly like the Quikscript letters, so Chrome keeps it in their run and the lowering fires (verified in Chrome — see row G above).

**We are deliberately not doing this.** It was briefly implemented and then reverted. The drawbacks outweigh the benefit:

- A dot typed as plain U+00B7 still would not lower in Chrome — only the PUA-encoded dot would. So this is a workflow/convention burden, not a transparent fix: every bit of text that wants the Chrome behavior would have to use a non-standard code point.
- There is no good authoring/emission path for U+F00B7. The site pages could emit it directly, but real authoring would need new input plumbing, and the resulting documents would carry a private-use code point that means nothing outside this font.
- Mono is unaffected either way — it has no `calt`, so the dot never lowers there.

## The other workaround we are not taking: client-side JS plus an on-copy handler

A second tempting fix is to leave the authored text as plain U+00B7 and have JavaScript on each page swap the dot into the run-friendly U+F00B7 at load time (so Chrome’s lowering fires), then register a `copy` event handler that rewrites the selection to put a plain U+00B7 back on the clipboard so the visible-vs.-copied text stays honest. We are not doing this either.

The trap is well documented in Gwern’s own design graveyard, under [Hyphenopoly hyphenation](https://gwern.net/design-graveyard#hyphenopoly-hyphenation). There the displayed-text manipulation was inserting soft hyphens for better justified line-breaking; here it would be swapping a dot’s code point. The failure modes are the same once you start mutating rendered text and patching the clipboard to compensate: some OS/browser combinations preserve the injected character in copy-paste, so you bolt on JS to strip it on copy — and then, as Gwern records, the injected characters “made the final HTML source code harder to read, made regexp & string searches/replaces more error-prone, and apparently some screen readers are so incompetent that they pronounce every soft-hyphen.” The on-copy handler is a patch over a problem the swap itself created, and it never fully closes the gap: middle-click paste on X11, drag-and-drop, “view source”, browser extensions, and accessibility tooling all read the DOM directly and bypass the `copy` handler, so each is a fresh way for the private-use code point to leak into text that should never contain it. The conclusion is the same as Gwern’s: don’t manipulate the displayed text on the client and then try to undo it at copy time. Author the real characters and let the font shape them.

## Keeping this on the record

This document is kept for the record: the finding, root cause, and diagnosis stand, and both workarounds are preserved so neither is rediscovered and re-attempted without weighing the same trade-offs.

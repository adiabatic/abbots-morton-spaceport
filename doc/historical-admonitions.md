# Historical admonitions

These conventions are for anyone who wants to reproduce how this font was made. `AGENTS.md` does not point agents here because the rules rarely matter during normal authoring. They apply when rebuilding the test setup or the Senior shaping corpus from scratch.

## Authoring the shipped font’s YAML

- See [quikscript-yaml-conventions.md](quikscript-yaml-conventions.md) for the selector, ligature, and `ex-noentry` mechanism behind `glyph_data/quikscript.yaml`, and for the recipe that proves a selector change is a pure cleanup.

## Transcribing passages from the manual

- The two word-list sections in `site/the-manual.html`, “Common words to be fully spelt” and “Contractions”, are the authority on how to spell words in Senior QS. Read the English word from the `<dt>` and the QS text from the `<dd>` or its child `<span>` elements. In an entry with several forms (such as `time/s`), each `<span>` has a `data-orthodox` attribute naming the English word its QS form spells.
- A passage’s `data-orthodox` attribute holds its English text.

## Tests

- See [data-expect.md](data-expect.md) for the `data-expect` attribute syntax (glyph tokens, connection operators, variant assertions, ligature notation, and duplicate rules).
- See [span-wrapping.md](span-wrapping.md) for how to wrap QS words in `data-expect` spans in passage blockquotes.
- To remove a duplicate test, remove the `data-expect` attribute. If the element is a `span` with no remaining attributes, unwrap it: remove the tags and keep the text in place. The text inside the element must stay identical. It often contains invisible PUA code points, so check with a program (for example, by comparing hex dumps of each changed line before and after) that only the attribute or tags were removed.
- Before adding `data-expect` attributes, check for content duplicates. Don’t wrap a word that is already tested elsewhere in the document unless told to.
- Don’t wrap one-letter Quikscript words in `data-expect` attributes unless told to. A single letter has no joins to test.
- When consolidating redundant tests, keep the existing `data-expect` values in `site/the-manual.html` unchanged and remove the redundant coverage from the other files.

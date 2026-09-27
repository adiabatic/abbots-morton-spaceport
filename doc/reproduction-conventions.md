# Conventions for reproducing the font

These conventions are for anyone who wants to reproduce how this font was made. `AGENTS.md` does not point agents here because the rules rarely matter during normal authoring. They apply when rebuilding the test setup or the Senior shaping corpus from scratch.

## Authoring the old font’s YAML

- See [quikscript-yaml-conventions.md](quikscript-yaml-conventions.md) for the selector, ligature, and `ex-noentry` mechanism behind `glyph_data/quikscript.yaml`, and for the recipe that proves a selector change is a pure cleanup.

## Transcribing passages from the manual

- The two word-list sections in `site/the-manual.html`, “Common words to be fully spelt” and “Contractions”, are the authority on how to spell words in Senior QS. Read the English word from the `<dt>` and the QS text from the `<dd>` or its child `<span>` elements. In an entry with several forms (such as `time/s`), each `<span>` has a `data-orthodox` attribute naming the English word its QS form spells.
- A passage’s `data-orthodox` attribute holds its English text.

## Tests

- See [data-expect.md](data-expect.md) for the `data-expect` attribute syntax (glyph tokens, connection operators, variant assertions, ligature notation, and duplicate rules).
- To remove a duplicate test, remove the `data-expect` attribute. If the element is a `span` with no remaining attributes, unwrap it: remove the tags and keep the text in place. The text inside the element must stay identical. It often contains invisible PUA code points, so check with a program (for example, by comparing hex dumps of each changed line before and after) that only the attribute or tags were removed.
- When consolidating redundant tests, keep the existing `data-expect` values in `site/the-manual.html` unchanged and remove the redundant coverage from the other files.

### Wrapping Quikscript words in `data-expect` spans

These rules apply when adding `<span data-expect="">` wrappers to the Quikscript passage blockquotes in `site/the-manual.html`.

- Wrap each Quikscript word of two or more letters in `<span data-expect="">…</span>`. Multi-letter contractions are wrapped too, such as but (·Bay·Tea), with (·Way·It), was (·Way·Utter·Zoo), in (·It·No), his (·He·Zoo), not (·No·Ox·Tea), and from (·Fee·May).
- Don’t wrap a one-letter Quikscript word unless told to. A single letter has no joins to test. This covers the single-letter contractions: the (·They), and (·No), to (·Tea), for (·Fee), of (·Vie), a (·Utter), as (·At), do (·Day), he (·He), is (·Zoo), we (·Way), what (·Why), which (·Cheer), it (·It), on (·Ox), and she (·She).
- Don’t wrap em dashes, standalone punctuation, or other non-letter content.
- Punctuation stays on the same line as the word, outside the closing `</span>` tag.
- Namer dots (·, U+00B7) go inside the span as part of the word.
- A hyphenated compound word is one span, with its hyphens inside it.
- Don’t wrap a word whose Quikscript text (byte-identical code points) already appears in a `data-expect` or `data-expect-noncanonically` span earlier in the document, unless told to. Search the whole file, not just the current passage. That later occurrence would be a content duplicate; the [Duplicates](data-expect.md#duplicates) section of data-expect.md defines the duplicate levels.
- Leave the `data-expect` value empty. The values are filled in separately, by hand or by a tool.

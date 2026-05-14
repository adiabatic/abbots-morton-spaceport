# Historical admonitions

These are conventions kept for humans who want to recreate this font's making technique. They are not auto-loaded by the agent tooling (CLAUDE.md / AGENTS.md) because they're rarely relevant during normal authoring work — but if you're rebuilding the test scaffolding or the Senior-shaping corpus from scratch, the rules below are how this repo handles them.

## Transcribing passages from the manual

- The two word-list sections in `test/the-manual.html` — "Common words to be fully spelt" and "Contractions" — are the source of truth for how to spell things in Senior QS. Parse the `<dt>` for the English word and the `<dd>` (or its child `<span>` elements) for the QS text. Multi-form entries (e.g., `time/s`) use `data-orthodox` attributes on each `<span>` to label which English word each QS form represents.
- The `data-orthodox` attribute provides the English text for each passage.

## Tests

- See [test/data-expect.md](../test/data-expect.md) for the `data-expect` attribute syntax (glyph tokens, connection operators, variant assertions, ligature notation, and duplicate rules).
- See [test/span-wrapping.md](../test/span-wrapping.md) for how to wrap QS words in `data-expect` spans in passage blockquotes.
- To remove a duplicate test: remove the `data-expect` attribute. If the element is a `span` with no remaining attributes, unwrap the `span` (remove the tags but keep the text content in place). Never remove the text inside the element — it must remain identical before and after. The text frequently contains invisible PUA code points, so verify with a program (e.g., compare hex dumps of each modified line before and after) that only the attribute and/or tags were removed.
- When adding `data-expect` attributes, always check for content duplicates first — do not wrap a word that is already tested elsewhere in the document unless explicitly told to.
- Do not wrap one-letter Quikscript words in `data-expect` attributes unless explicitly told to — there is no point in testing joins when there is only one letter.
- When consolidating redundant tests, do not rewrite existing `data-expect` values in `test/the-manual.html`; preserve the manual corpus and remove redundant coverage elsewhere instead.

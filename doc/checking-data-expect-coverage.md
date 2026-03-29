# How to check for missing data-expect coverage

Use this when you want to know whether a block of Quikscript text in `test/the-manual.html` is already covered by `data-expect` elsewhere in the file.

## Workflow

1. Pick the exact scope first: a blockquote, paragraph, or line range.
2. Ask Codex to use subagents for data collection.
3. Have one subagent inventory the target words in order, using rendered text content rather than raw source text.
4. Have a second subagent index every exact `data-expect` element in `test/the-manual.html` by rendered text content and line number.
5. Compare the target inventory against that file-wide index.
6. Report exact matches elsewhere first, then near-matches after normalization.

## Normalization rules

- For deciding whether something is a multi-letter Quikscript word, count only Quikscript PUA letters.
- Ignore trailing punctuation for that count.
- Ignore ZWNJ (`U+200C`) and variation selectors such as `U+FE00` for that count.
- When answering “is there a `data-expect` elsewhere for this exact word?”, compare the rendered text content exactly.
- If useful, also report normalized near-matches separately so you can spot cases where punctuation or ZWNJ is the only difference.

## What to ask Codex for

Tell Codex:

- what part of `test/the-manual.html` to inspect
- that “elsewhere” should exclude any `data-expect` already inside the target block if that is what you mean
- whether you want only exact misses, or exact misses plus normalized near-matches

Example prompt:

```text
Have a look at `test/the-manual.html`. Are there any multi-letter Quikscript words in the blockquote starting on line 3939 that don't have a `data-expect` for them elsewhere in the file? Use subagents to do the data collection. Treat “multi-letter” as “more than one Quikscript PUA character after stripping trailing punctuation, ZWNJ, and variation selectors”. Exclude any `data-expect` inside that same blockquote from “elsewhere”, and call out normalized near-matches separately.
```

## Follow-up edit

If the next step is to mark the missing words for later test authoring, wrap only the missing words with `span[data-expect=""]`.

- Keep punctuation outside the span when it is not part of the word.
- Leave any existing non-empty `data-expect` attributes alone.
- Use [test/data-expect.md](../test/data-expect.md) as the syntax reference when filling the empty attributes in later.

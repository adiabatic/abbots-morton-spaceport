# Wrapping QS words in `data-expect` spans

When adding `<span data-expect="">` wrappers to QS passage blockquotes in `test/the-manual.html`, follow these rules.

## Which words to wrap

- **Multi-letter QS words** (2+ QS characters) get wrapped in `<span data-expect="">…</span>`.
- **Single-letter QS words** (1 QS character) are **not wrapped**. There is no point testing joins when there is only one letter. These are the single-letter contractions: the (·They), and (·No), to (·Tea), for (·Fee), of (·Vie), a (·Utter), as (·At), do (·Day), he (·He), is (·Zoo), we (·Way), what (·Why), which (·Cheer), it (·It), on (·Ox), she (·She).
- **Multi-letter contractions** are still wrapped. For example: but (·Bay·Tea, 2 letters), with (·Way·It, 2 letters), was (·Way·Utter·Zoo, 3 letters), in (·It·No, 2 letters), his (·He·Zoo, 2 letters), not (·No·Ox·Tea, 3 letters), from (·Fee·May, 2 letters), etc.
- **Em dashes, standalone punctuation, and other non-letter content** are not wrapped.

## Punctuation and namer dots

- **Punctuation** stays on the same line as the word, outside the closing `</span>` tag.
- **Namer dots** (·, U+00B7) go inside the span as part of the word content.
- **Hyphens in compound words** stay inside the span — the whole compound is one span.

## Content duplicates

If the same QS text content (byte-identical code points) already appears in a `data-expect` or `data-expect-noncanonically` span earlier in the document, the later occurrence is **not wrapped**. Check across the entire file, not just the current passage.

## `data-expect` values

Leave the `data-expect` value empty when wrapping. Values are filled in separately, either manually or automatically.

## Example

Given this English text (from `data-orthodox`):

> He would sail a ship in the Pacific

The QS text with wrapping looks like:

```html
<!-- prettier-ignore -->
<p data-orthodox="He would sail a ship in the Pacific">

  <span data-expect="">︎︎</span>
  <span data-expect="">︎︎︎</span>

  <span data-expect="">︎︎︎</span>
  <span data-expect="">︎︎</span>

  ·<span data-expect="">︎︎︎︎︎︎︎</span>
</p>
```

Where single-letter words (He, a, the) are bare text, and multi-letter words (would, sail, ship, in, Pacific) are wrapped. The namer dot for ·Pacific goes inside the span.

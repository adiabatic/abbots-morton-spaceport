# Abbots Morton Spaceport

A pixel-font pair (mostly) for [Quikscript][]. Available as a sans-serif version, or you can pair its monospace version with [Departure Mono][] to get a full-featured monospace font.

[departure mono]: https://departuremono.com/
[quikscript]:     https://www.quikscript.net/

You can get a copy of the latest .otf and .woff2 files from [the Releases page][r].

If you’d like this in a non-pixel version, keep your eye on [Abbots Morton][am].

[r]: https://github.com/adiabatic/abbots-morton-spaceport/releases
[am]: https://github.com/adiabatic/abbots-morton

## Usage

This font comes in two-and-a-half variants:

- monospace (Abbots Morton Spaceport **Mono**)
- proportional (Abbots Morton Spaceport **Sans**)
  - unligated (Abbots Morton Spaceport Sans **Junior**)
  - ligated (Abbots Morton Spaceport Sans **Senior**) — but it has bugs in addition to its [Known issues](#known-issues) below

You’ll definitely need to:

- set your fonts to multiples of 11 pixels (unless you’re targeting print exclusively)

And, if you want a monospace font, you’ll need to:

- get [Departure Mono][] working, too

### Font sizing

For pixel-perfect rendering on a screen, you’ll want to limit yourself to font sizes that are multiples of 11 **pixels**.

On the other hand, if you’re aiming for print (in, say, Word or Typst), you don’t really need to care about pixel alignment if your target is a 600 DPI laser printer.

### Using with Departure Mono

While Abbots Morton Spaceport **Sans** is a full-featured font, Abbots Morton Spaceport **Mono** pretty much only supplies Quikscript-only characters. If you want to use Abbots Morton Spaceport in, say, a text editor, you’ll likely want to use it with [Departure Mono][].

Even if you _are_ using Abbots Morton Spaceport Sans, you may also want to use Departure Mono anyway — it has Greek and Cyrillic letters and doesn’t skimp on eastern-European diacritical marks.

#### Font-stack ordering

If you’re using Abbots Morton Spaceport **Sans**, you’ll want to put it first in your font stack. That way, its glyphs will get used instead of Departure Mono’s.

If you’re only using Abbots Morton Spaceport **Mono**, then, as far as I can tell, the order in which you specify fonts doesn’t matter.

#### CSS

```css
@font-face {
  font-family: 'Departure Mono';
  src: url('…/DepartureMono-Regular.woff2') format('woff2');
}

/* Pick Junior, Senior, or both. You’ll probably want the matching `-Bold.woff2` file, too. */
@font-face {
  font-family: 'Abbots Morton Spaceport Sans Junior';
  src: url('…/AbbotsMortonSpaceportSansJunior-Regular.woff2') format('woff2');
  font-weight: 400;
}

@font-face {
  font-family: 'Abbots Morton Spaceport Sans Junior';
  src: url('…/AbbotsMortonSpaceportSansJunior-Bold.woff2') format('woff2');
  font-weight: 700;
}

@font-face {
  font-family: 'Abbots Morton Spaceport Mono';
  src: url('…/AbbotsMortonSpaceportMono-Regular.woff2') format('woff2');
  font-weight: 400;
}

@font-face {
  font-family: 'Abbots Morton Spaceport Mono';
  src: url('…/AbbotsMortonSpaceportMono-Bold.woff2') format('woff2');
  font-weight: 700;
}

/* you probably know what selector you want already, but we’ll go with :root */
:root {
  /* For Abbots Morton Spaceport Sans, you want it in front */
  font-family: 'Abbots Morton Spaceport Sans Junior', 'Departure Mono', monospace;

  /* For Abbots Morton Spaceport Mono, the same order should be fine */
  font-family: 'Abbots Morton Spaceport Mono', 'Departure Mono', monospace;

  /* <https://caniuse.com/?search=font-smooth> isn’t universally supported as of early 2026, but it might be as you read this, so go check */
  -webkit-font-smoothing: none;
  -moz-osx-font-smoothing: unset;
  font-smooth: never;
}
```

### [Typst][tf]

```typst
#set text(
    // For Abbots Morton Spaceport Sans Junior, you want it in front
    font: ("Abbots Morton Spaceport Sans Junior", "Departure Mono"),
)
```

or:

```typst
#set text(
    // For Abbots Morton Spaceport Mono, the same order should be fine
    font: ("Abbots Morton Spaceport Mono", "Departure Mono"),
)
```

[tf]: https://typst.app/docs/reference/text/text/#parameters-font

### Microsoft Word

If you want to set a fallback font in Microsoft Word you’ll likely need to do something involved and nerdy, like opening up a saved Theme (it’s a .zip file with XML files in it) and editing its long list of language-dependent fallback fonts. Ask an LLM to help you out here. Also ask it to help you convert sizes in pixels to sizes in points.

Or…

### Others

You know how people use [Nerd Fonts][] to get their usual fonts with extra glyphs shoved into them? Maybe you could do that kind of thing and smash either Abbots Morton Spaceport font into Departure Mono.

[nerd fonts]: https://www.nerdfonts.com/

## Weights

Every family ships as a pair of static OTFs — a Regular and a Bold — for six files total:

- `AbbotsMortonSpaceportMono-Regular.otf` and `-Bold.otf`
- `AbbotsMortonSpaceportSansJunior-Regular.otf` and `-Bold.otf`
- `AbbotsMortonSpaceportSansSenior-Regular.otf` and `-Bold.otf`

The Bold variant is a half-pixel rightward overstrike of the Regular — every “on” pixel becomes 1½ pixels wide. This is totally fine if your text is set at 22px (instead of the bare-minimum 11px) or if you can assume that one CSS pixel is actually four device pixels, as is true for Retina-class displays (displays at 200% text zoom).

## Variation selectors

In order to properly transcribe [The Manual][tm], I needed a way to force the display of alternate ·Utter and ·No. I also needed a way to force the display of a half-·Way. I settled on using [variation selectors][vs] instead of dedicating extra code points to get this sort of behavior in a vacuum. If you put VS1 (U+FE00) after an ·Utter or ·No, you’ll get the alternate look. If you put VS2 (U+FE01) after ·Way (at least), you’ll get a half-·Way instead.

[tm]: ./reference/Quikscript%20Manual.pdf
[vs]: https://en.wikipedia.org/wiki/Variant_form_(Unicode)

## Stylistic sets

In order to exactly match the contents of [The Manual][tm] without peppering the HTML with zero-width joiners everywhere, I decided to add a number of OpenType stylistic sets to turn on and off behavior that only happened sometimes. They are:

- `ss01`: suppress ·Utter·Pea join
- `ss02`: allow ·I·Tea to join at the Short height
- `ss03`: allow ·Tea to be joined to at the x-height
- `ss04`: allow ·It to join at baseline after ·Day and before ·Low
- `ss05`: allow ·Ox·May to join at baseline
- `ss10`: suppress all joins for the wrapped letter(s) — reverts every contextual variant to its base form, removing all cursive anchors. Useful as an alternative to ZWNJ when you want a letter to stand alone without connecting to its neighbors; wrap the letter in a `<span>` with `font-feature-settings: "ss10" 1`. ZWNJ only blocks the join across that one boundary; it does not fully isolate either letter.

I make no guarantees that I’m going to keep these stylistic sets the same over multiple releases of this font. If you use these yourself and upgrade your Abbots Morton Spaceport font files ever, you’ll need to read the FONTLOG to see if I’ve rejiggered any of these.

## Known issues

Abbots Morton Spaceport Sans **Senior** uses OpenType’s `curs` feature. While this is very much the obviously correct OpenType feature to use to join letters to one another as done in Quikscript, support for it in <abbr title="left-to-right, top-to-bottom">LTRTTB</abbr> scripts like Latin and Quikscript isn’t universal because so far, `curs` is only used in <abbr title="right-to-left, top-to-bottom">RTLTTB</abbr> scripts like Arabic and the one naturally-occurring <abbr title="top-to-bottom, left-to-right">TTBLTR</abbr> script, [Mongolian](https://en.wikipedia.org/wiki/Mongolian_script).

In my testing, AMSS Senior works fine in:

- current evergreen browser engines (WebKit (Safari), Gecko (Firefox), Blink (Chrome, Edge))
- Typst (although it does complain about not supporting variable fonts well)

It _should_ work fine in:

- anything that uses [Harfbuzz](https://github.com/harfbuzz/harfbuzz)

It does _not_ work in:

- Microsoft Word 365 (as of February 18, 2026)

## Licensing

SIL OFL 1.1 for the font files themselves, and MIT for everything else.

## Acknowledgements

- [Helena Zhang](https://departuremono.com/) — Inspiration, more than a few directly-copied glyphs, several lightly-changed glyphs, and a handful of glyphs I just drew myself that amusingly happened to look exactly like DM’s
- [Brad Neil](https://friedorange.xyz/) — Design critique
- Kim Slawson for [his bold mockup for Departure Mono](https://github.com/rektdeckard/departure-mono/issues/17#issuecomment-2863240009)
- anyone who’s ever written about fontmaking in public on the Internet — your discussions have been the wind beneath my LLMs’ wings

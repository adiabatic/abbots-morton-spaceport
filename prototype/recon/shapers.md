# Recon C: shaping harnesses for the cross-shaper smoke tests

Everything below was actually run on this machine (Darwin 25.5.0, 2026-06-09) against the built `site/AbbotsMortonSpaceportSansSenior-Regular.otf` (1148 glyphs, upem 550; cmap covers U+0020, U+200C → `uni200C`, and all the Quikscript PUA codepoints).

## 1. HarfBuzz via uharfbuzz (the existing in-repo pattern)

`uharfbuzz>=0.43.0` and `fonttools>=4.61.1` are already project dependencies (`pyproject.toml`), so the prototype can import both with plain `uv run` and zero new dependencies.

The minimal load-shape-names pattern, distilled from `test/quikscript_shaping_helpers.py` (lines 21–51) and `test/test_shaping.py`:

```python
import uharfbuzz as hb
from fontTools.ttLib import TTFont

path = "site/AbbotsMortonSpaceportSansSenior-Regular.otf"
tt = TTFont(path)
font = hb.Font(hb.Face(hb.Blob.from_file_path(path)))

def shape(text, features=None):
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, features or {})
    return [
        (info.codepoint, tt.getGlyphName(info.codepoint), pos.x_advance, pos.x_offset, pos.y_offset)
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions)
    ]
```

Key facts about the pattern:

- **The >63-byte name workaround** (`test/test_shaping.py:376-378`, `shaped_glyph_name`): HarfBuzz’s `font.glyph_to_string(gid)` truncates glyph names to 63 bytes, and this font has compiled names longer than that. The fix is to treat `info.codepoint` (which is the **glyph ID** after shaping, not a Unicode codepoint) as a GID and resolve it through `fontTools.ttLib.TTFont(path).getGlyphName(gid)`. The same trick is the bridge for CoreText, which only ever gives you GIDs.
- **Positions**: `buf.glyph_positions` yields records with `x_advance`, `y_advance`, `x_offset`, `y_offset` in font units. Cursive attachment and contextual kerning show up here — e.g. `qsPea qsJay` shapes to `qsPea.half.ex-y5.ex-dips` (advance 250→200 via kern/curs) followed by `qsJay.en-y5.ex-y0.en-con-1` with `x_offset = -50`. The existing isolation invariant compares exactly the triple `(x_offset, y_offset, x_advance)` per glyph (`_positions_equivalent`, `test/test_shaping.py:760`).
- **Join verification in the existing tests is anchor-metadata-based, not GPOS-based**: `build_anchor_map` (`test/test_shaping.py:381`) recompiles the YAML via `compile_glyph_set(load_glyph_data(...), "senior")` and asserts that the shaped left glyph’s exit Ys intersect the right glyph’s entry Ys. The prototype smoke test does not need that machinery — for cross-shaper comparison, GID sequence plus accumulated pen positions is the right assertion (see §2).
- **Buffer reuse hazard**: `quikscript_shaping_helpers.py` reuses one module-level `hb.Buffer` and documents that `buf.glyph_infos` / `buf.glyph_positions` must be materialized into a list before the next `shape()` call invalidates them. A fresh `hb.Buffer()` per call (as in `tools/shape_sequences.py`) avoids the trap entirely and is fine at smoke-test scale.
- **Features**: pass a dict like `{"ss03": True}` as the third argument to `hb.shape`. `_shape_with_features` in the helpers caches on a tuple-of-tuples because dicts aren’t hashable.
- **Split-buffer isolation reference**: `_isolation_glyphs_split` (`test/test_shaping.py:710`) shapes each segment of a text in its own buffer and concatenates — the model for “ZWNJ pair must equal its halves shaped separately” assertions.

### tools/shape_sequences.py as a CLI

`uv run python tools/shape_sequences.py FIXTURE [--features ss03] [--font PATH]` reads a fixture of whitespace-separated family names (one sequence per line, `#` comments), shapes each through HarfBuzz, and prints one diff-stable line per sequence with the chosen variant and its compiled entry/exit anchors, e.g. `qsMay qsPea -> qsMay/exit=(6,5) | qsPea.en-y5/entry=(1,5)/exit=(5,0)`. Two caveats: its `DEFAULT_FONT` is `test/AbbotsMortonSpaceportSansSenior-Regular.otf`, which does **not** exist — always pass `--font site/AbbotsMortonSpaceportSansSenior-Regular.otf`; and it uses `font.glyph_to_string`, so it has the 63-byte truncation the test suite works around. Useful as a baseline-diff tool, but the prototype should use the GID-resolving pattern above.

## 2. CoreText: the Swift harness (chosen route, verified working)

Route findings, in the order checked:

- (a) `uv run python -c 'import CoreText'` fails (`ModuleNotFoundError`); PyObjC is not in the project env and must not be added.
- (c) `/usr/bin/python3 -c 'import CoreText'` also fails — this macOS does not ship PyObjC with the system Python. Tested, dead.
- (b) `/usr/bin/swift` and `/usr/bin/swiftc` both exist (xcrun version 72) and work without any setup. **This is the route.**

The working harness is `prototype/recon/coretext_shape.swift` (committed alongside this file), reproduced verbatim:

```swift
import CoreText
import Foundation

// Usage: swift coretext_shape.swift <font.otf> <hex codepoints, e.g. E665 E650 200C E650>. Prints one line per output glyph: glyph ID, x position, y position, and the PostScript name of the font that CoreText actually used for that run (so silent fallback to a system font is visible instead of corrupting the comparison).

let arguments = CommandLine.arguments
guard arguments.count >= 3 else {
    FileHandle.standardError.write("usage: swift coretext_shape.swift FONT.otf HEXCP [HEXCP ...]\n".data(using: .utf8)!)
    exit(2)
}

let fontPath = arguments[1]
var text = ""
for hex in arguments[2...] {
    guard let value = UInt32(hex, radix: 16), let scalar = Unicode.Scalar(value) else {
        FileHandle.standardError.write("bad codepoint: \(hex)\n".data(using: .utf8)!)
        exit(2)
    }
    text.unicodeScalars.append(scalar)
}

guard let provider = CGDataProvider(url: URL(fileURLWithPath: fontPath) as CFURL),
      let cgFont = CGFont(provider) else {
    FileHandle.standardError.write("cannot load font: \(fontPath)\n".data(using: .utf8)!)
    exit(1)
}
let ctFont = CTFontCreateWithGraphicsFont(cgFont, 100, nil, nil)

let attributed = NSAttributedString(string: text, attributes: [
    NSAttributedString.Key(kCTFontAttributeName as String): ctFont
])
let line = CTLineCreateWithAttributedString(attributed)
let runs = CTLineGetGlyphRuns(line) as! [CTRun]

for run in runs {
    let runAttributes = CTRunGetAttributes(run) as! [NSAttributedString.Key: Any]
    let runFont = runAttributes[NSAttributedString.Key(kCTFontAttributeName as String)] as! CTFont
    let runFontName = CTFontCopyPostScriptName(runFont) as String
    let count = CTRunGetGlyphCount(run)
    var glyphs = [CGGlyph](repeating: 0, count: count)
    var positions = [CGPoint](repeating: .zero, count: count)
    CTRunGetGlyphs(run, CFRange(location: 0, length: 0), &glyphs)
    CTRunGetPositions(run, CFRange(location: 0, length: 0), &positions)
    for index in 0..<count {
        print("\(glyphs[index])\t\(positions[index].x)\t\(positions[index].y)\t\(runFontName)")
    }
}
```

Invocation and cost: `swift prototype/recon/coretext_shape.swift FONT HEXCP...` interprets in ~3 s per call; `swiftc -O ... -o ct_shape` compiles once in ~2.5 s and then each run is ~0.03 s — compile once per smoke-test session and loop over sequences with the binary. Codepoints are passed as hex on the command line precisely because PUA literals do not survive shell quoting reliably (this bit me during recon — a Python `-c` string silently dropped the PUA characters once).

Design notes baked into the harness:

- `CGDataProvider` + `CGFont` + `CTFontCreateWithGraphicsFont` loads the font **file** directly with no installation or `CTFontManager` registration step.
- CoreText reports cumulative pen positions per glyph with GPOS offsets already applied (in points at the chosen 100 pt size); HarfBuzz reports per-glyph advance/offset in font units. To compare: accumulate HarfBuzz (`pen += x_advance` after emitting `pen + x_offset`), convert CoreText points to font units via `* upem / pointSize` (here `* 550 / 100`), and round to integers before equality.
- The harness prints the per-run resolved font name because CoreText silently falls back to a system font for any character outside the cmap, which would otherwise corrupt a GID comparison into nonsense. Assert the name column equals the expected PostScript name on every line.

### Verified cross-shaper agreement (CoreText applies calt by default)

| Sequence | HarfBuzz (GID, name, advance/offset) | CoreText (GID @ x-position in pt) | Agreement |
| ------------------------------ | ------------------------------------------------------------------------- | ----------------------------------------------- | --------- |
| ·May·Pea (E665 E650) | 734 `qsMay` adv 250; 343 `qsPea.en-y5.ex-y0` adv 250 | 734 @ 0.0; 343 @ 45.4545 (= 250/550×100) | exact |
| ·May ZWNJ ·Pea (E665 200C E650) | 734 `qsMay` adv 300; 1 `space` adv 0; 365 `qsPea.noentry` adv 250 | 734 @ 0.0; 1 @ 54.5454; 365 @ 54.5454 | exact |
| ·May space ·Pea (E665 0020 E650) | 734 `qsMay` adv 300; 1 `space` adv 350; 340 `qsPea` adv 250 | 734 @ 0.0; 1 @ 54.5454; 340 @ 118.1818 | exact |
| ·Pea·Jay (E650 E65F) | 364 `qsPea.half.ex-y5.ex-dips` adv 200; 651 `qsJay.en-y5.ex-y0.en-con-1` adv 250, x_offset −50 | 364 @ 0.0; 651 @ 27.2727 (= (200−50)/550×100) | exact |

This proves the three things the smoke test needs: CoreText applies `calt` by default (GID 343 / 651 are contextual variants, not the bare cmap glyphs 340 / `qsJay`), it applies GPOS including the −50 contextual offset, and its default-ignorable handling of ZWNJ (rendered as GID 1 = `space` at zero width, same as HarfBuzz’s invisible-glyph replacement) does not leak ZWNJ-crossing joins — both shapers picked `qsMay` (no exit) + `qsPea.noentry`. For ssNN runs later, `kCTFontFeatureSettingsAttribute` with `kCTFontOpenTypeFeatureTag`/`kCTFontOpenTypeFeatureValue` accepts raw OpenType tags like `"ss03"` on macOS 10.13+ (not yet exercised; noted as the extension point).

## 3. DirectWrite

DirectWrite cannot run on this Mac: it is a Windows-only COM API (`dwrite.dll`) with no macOS port, and Wine’s `dwrite` is an independent reimplementation whose shaping core is **not** Microsoft’s — passing under Wine would prove nothing about real DirectWrite, so a wine harness is worse than useless as evidence. Realistic options: (1) a GitHub Actions `windows-latest` job running a ~60-line C# or Python+ctypes harness around `IDWriteTextAnalyzer::GetGlyphs`/`GetGlyphPlacements`, asserting the same GID/position table as §2; (2) manual spot-check in Word/Notepad on a Windows machine or VM; (3) defer with a written caveat. **Recommendation for the report**: defer for week one with the caveat that within-lookup sequential substitution (backtrack-sees-settled) is OpenType-spec-mandated behavior that the current 30.8k-line font already relies on and ships against DirectWrite consumers today, and file the `windows-latest` CI harness (option 1) as the follow-up that closes the gap before the rebuild lands. The HarfBuzz+CoreText agreement above already covers two independent implementations of the contested semantics.

## 4. ZWNJ: what the smoke tests should assert

The shaper-difference risk is default-ignorable handling: shapers may hide default-ignorables (ZWNJ U+200C, ZWJ, etc.) from rendering, and historically some (old Uniscribe) deleted them from the glyph stream before GSUB, which would let a contextual join rule match **across** an invisible ZWNJ. This font’s whole boundary design (the `sub uni200C @entry-live' by @entry-locked` chokepoint, and §7’s requirement that `uni200C` appear explicitly at every slot of the settlement rule shape) depends on the opposite: ZWNJ is cmapped to a real `uni200C` glyph, occupies a slot during GSUB context matching, and is only hidden at the end (HarfBuzz and CoreText both replace it with the `space` glyph at zero advance post-shaping — verified above, and the smoke test must **not** assert a specific GID for the ZWNJ slot, only zero width and no ink). Concretely, for each shaper assert:

1. **Lock fires, join does not**: `X ZWNJ Y` yields the locked/no-entry forms (verified: `qsMay` + `qsPea.noentry`, GID 365), never the joined contextual form (GID 343), even though the ZWNJ is invisible.
2. **Split-buffer equivalence**: `X ZWNJ Y` shapes identically (names and positions) to `X` and `Y` shaped in separate buffers/lines — the cross-shaper version of `_isolation_glyphs_split`.
3. **ZWNJ in every rule slot**: probe ZWNJ at backtrack, input-adjacent, and lookahead positions of a settlement-shaped rule — e.g. `ZWNJ X Y` (backtrack slot), `X ZWNJ Y` (first lookahead), and `X Y ZWNJ Z` (second lookahead) — and assert the prototype emitter’s boundary-outcome rows win over any join row that would match across the skipped slot. This is exactly the §7 emitter invariant under test.
4. **ZWNJ slot rendering**: the ZWNJ output position contributes zero advance (CoreText: same x-position as the following glyph; HarfBuzz: `x_advance == 0`), so boundary handling never moves ink.
5. **Word-edge ZWNJ**: leading/trailing ZWNJ (`ZWNJ X`, `X ZWNJ`) must produce the same letter forms as the bare isolated letter, catching any emitter rule that assumed ZWNJ only appears between letters.

DirectWrite, when it is eventually tested (§3), is the shaper most worth running case 1 and 3 against, since it is the one implementation whose default-ignorable-in-context behavior we have not observed locally.

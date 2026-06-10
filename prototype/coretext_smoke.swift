import CoreText
import Foundation

// Usage: coretext_smoke FONT.otf [--features TAG[,TAG...]] HEXCP [HEXCP ...]. This is the recon harness (prototype/recon/coretext_shape.swift) extended with the optional --features argument, which applies raw OpenType feature tags through kCTFontFeatureSettingsAttribute using kCTFontOpenTypeFeatureTag/kCTFontOpenTypeFeatureValue (the extension point named in prototype/recon/shapers.md section 2), plus a per-glyph string-index column and a typographic-width header line so the Python driver can verify zero-advance ZWNJ slots even at the end of the line.
// Output: one header line "WIDTH <typographic width in points>", then one line per shaped glyph: glyph ID, UTF-16 string index, x position, y position, and the PostScript name of the font CoreText actually used for that run (silent fallback to a system font stays visible instead of corrupting the comparison). Every codepoint this harness is fed is in the Basic Multilingual Plane, so UTF-16 string indices equal scalar indices.
// Codepoints are passed as hex on argv because PUA literals do not reliably survive shell quoting.

let arguments = CommandLine.arguments

func fail(_ message: String, code: Int32) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

guard arguments.count >= 3 else {
    fail("usage: coretext_smoke FONT.otf [--features TAG[,TAG...]] HEXCP [HEXCP ...]", code: 2)
}

let fontPath = arguments[1]
var argumentIndex = 2
var featureTags: [String] = []
if arguments[argumentIndex] == "--features" {
    guard arguments.count > argumentIndex + 2 else {
        fail("--features needs a tag list and at least one codepoint", code: 2)
    }
    featureTags = arguments[argumentIndex + 1].split(separator: ",").map(String.init)
    argumentIndex += 2
}

var text = ""
for hex in arguments[argumentIndex...] {
    guard let value = UInt32(hex, radix: 16), let scalar = Unicode.Scalar(value) else {
        fail("bad codepoint: \(hex)", code: 2)
    }
    text.unicodeScalars.append(scalar)
}

guard let provider = CGDataProvider(url: URL(fileURLWithPath: fontPath) as CFURL),
      let cgFont = CGFont(provider) else {
    fail("cannot load font: \(fontPath)", code: 1)
}

var descriptor: CTFontDescriptor? = nil
if !featureTags.isEmpty {
    let settings: [[String: Any]] = featureTags.map { tag in
        [
            kCTFontOpenTypeFeatureTag as String: tag,
            kCTFontOpenTypeFeatureValue as String: 1,
        ]
    }
    let attributes = [kCTFontFeatureSettingsAttribute as String: settings] as CFDictionary
    descriptor = CTFontDescriptorCreateWithAttributes(attributes)
}
let ctFont = CTFontCreateWithGraphicsFont(cgFont, 100, nil, descriptor)

let attributed = NSAttributedString(string: text, attributes: [
    NSAttributedString.Key(kCTFontAttributeName as String): ctFont
])
let line = CTLineCreateWithAttributedString(attributed)
let width = CTLineGetTypographicBounds(line, nil, nil, nil)
print("WIDTH \(width)")
let runs = CTLineGetGlyphRuns(line) as! [CTRun]

for run in runs {
    let runAttributes = CTRunGetAttributes(run) as! [NSAttributedString.Key: Any]
    let runFont = runAttributes[NSAttributedString.Key(kCTFontAttributeName as String)] as! CTFont
    let runFontName = CTFontCopyPostScriptName(runFont) as String
    let count = CTRunGetGlyphCount(run)
    var glyphs = [CGGlyph](repeating: 0, count: count)
    var positions = [CGPoint](repeating: .zero, count: count)
    var stringIndices = [CFIndex](repeating: 0, count: count)
    CTRunGetGlyphs(run, CFRange(location: 0, length: 0), &glyphs)
    CTRunGetPositions(run, CFRange(location: 0, length: 0), &positions)
    CTRunGetStringIndices(run, CFRange(location: 0, length: 0), &stringIndices)
    for index in 0..<count {
        print("\(glyphs[index])\t\(stringIndices[index])\t\(positions[index].x)\t\(positions[index].y)\t\(runFontName)")
    }
}

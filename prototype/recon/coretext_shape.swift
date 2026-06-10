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

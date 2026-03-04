#import "../style.typ": *

= What is Quikscript?

Quikscript is a phonetic writing system for English designed by Kingsley Read and published in 1966 as an evolution of his earlier Shavian work. In this project, Quikscript letters live in the Private Use Area at `U+E650` to `U+E67F`.

Quikscript can be written in two styles:

- Junior Quikscript: letters are written as separate forms.
- Senior Quikscript: letters connect, use half-letters, and use context-sensitive alternates.

#technical_detail([
The reference chart in `inspo/csur/index.html` describes this same split as unligated (Junior) versus ligated/connected (Senior) writing.
])

At a glance, this project's Quikscript inventory (letters plus angled parentheses) is:

#grid(
  columns: (1fr, 1fr, 1fr),
  gutter: 7pt,
  [#glyph_label("·Pea", "U+E650")], [#glyph_label("·Bay", "U+E651")], [#glyph_label("·Tea", "U+E652")],
  [#glyph_label("·Day", "U+E653")], [#glyph_label("·Key", "U+E654")], [#glyph_label("·Gay", "U+E655")],
  [#glyph_label("·Thaw", "U+E656")], [#glyph_label("·They", "U+E657")], [#glyph_label("·Fee", "U+E658")],
  [#glyph_label("·Vie", "U+E659")], [#glyph_label("·See", "U+E65A")], [#glyph_label("·Zoo", "U+E65B")],
  [#glyph_label("·She", "U+E65C")], [#glyph_label("·J'ai", "U+E65D")], [#glyph_label("·Cheer", "U+E65E")],
  [#glyph_label("·Jay", "U+E65F")], [#glyph_label("·Ye", "U+E660")], [#glyph_label("·Way", "U+E661")],
  [#glyph_label("·He", "U+E662")], [#glyph_label("·Why", "U+E663")], [#glyph_label("·-ing", "U+E664")],
  [#glyph_label("·May", "U+E665")], [#glyph_label("·No", "U+E666")], [#glyph_label("·Low", "U+E667")],
  [#glyph_label("·Roe", "U+E668")], [#glyph_label("·Loch", "U+E669")], [#glyph_label("·Llan", "U+E66A")],
  [#glyph_label("·Excite", "U+E66B")], [#glyph_label("·Exam", "U+E66C")], [#glyph_label("⟨", "U+E66E")],
  [#glyph_label("⟩", "U+E66F")], [#glyph_label("·It", "U+E670")], [#glyph_label("·Eat", "U+E671")],
  [#glyph_label("·Et", "U+E672")], [#glyph_label("·Eight", "U+E673")], [#glyph_label("·At", "U+E674")],
  [#glyph_label("·I", "U+E675")], [#glyph_label("·Ah", "U+E676")], [#glyph_label("·Awe", "U+E677")],
  [#glyph_label("·Ox", "U+E678")], [#glyph_label("·Oy", "U+E679")], [#glyph_label("·Utter", "U+E67A")],
  [#glyph_label("·Out", "U+E67B")], [#glyph_label("·Owe", "U+E67C")], [#glyph_label("·Foot", "U+E67D")],
  [#glyph_label("·Ooze", "U+E67E")], [#glyph_label("(unused)", "U+E66D, U+E67F")], [#glyph_label("Namer dot", "U+00B7")],
)


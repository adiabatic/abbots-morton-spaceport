#let body-font = ("New Computer Modern", "Libertinus Serif", "Georgia")
#let mono-font = ("Departure Mono", "Courier New")
#let qs-font = ("Abbots Morton Spaceport Sans Senior", "Abbots Morton Spaceport Sans Junior", "Departure Mono")

#let accent = rgb("#0F5F7A")
#let accent-soft = rgb("#E8F4F8")
#let key-fill = rgb("#FFF7DE")
#let try-fill = rgb("#EAF8EE")
#let tech-fill = rgb("#F1EFFB")
#let code-fill = rgb("#F3F6F9")
#let frame = rgb("#C8D3DA")

#let apply_explainer_style(doc) = {
  set document(
    title: "Abbots Morton Spaceport: how Quikscript joining works",
    author: ("Abbots Morton Spaceport project",),
  )
  set page(
    paper: "us-letter",
    margin: (x: 0.95in, y: 0.9in),
    numbering: "1",
    number-align: bottom + right,
  )
  set text(font: body-font, size: 10.5pt, fill: rgb("#1A1A1A"))
  set par(justify: true, leading: 0.68em)
  set heading(numbering: "1.")
  show heading.where(level: 1): it => context {
    let chapter_n = counter(heading).get().first()
    if chapter_n > 1 {
      pagebreak()
    }
    set block(above: 1.5em, below: 0.8em)
    [
      #set text(fill: accent, size: 10.5pt, weight: "semibold")
      Chapter #chapter_n
      #linebreak()
      #set text(fill: accent, size: 15pt, weight: "bold")
      #it.body
    ]
  }
  show heading.where(level: 2): it => {
    set block(above: 1em, below: 0.35em)
    set text(fill: accent, size: 12pt, weight: "bold")
    it
  }
  show raw.where(block: true): it => block(
    fill: code-fill,
    stroke: (paint: frame, thickness: 0.6pt),
    radius: 3pt,
    inset: 8pt,
    above: 0.7em,
    below: 0.9em,
  )[
    #set text(font: mono-font, size: 9pt)
    #it
  ]
  show figure: it => {
    set block(above: 0.9em, below: 1em)
    it
  }
  doc
}

#let _callout(title, fill-color, body) = block(
  fill: fill-color,
  stroke: (paint: accent, thickness: 0.8pt),
  radius: 4pt,
  inset: 9pt,
  above: 0.8em,
  below: 0.9em,
)[
  #set text(weight: "bold", fill: accent)
  #title
  #set text(weight: "regular", fill: rgb("#1A1A1A"))
  #v(4pt)
  #body
]

#let key_idea(body) = _callout("Key idea", key-fill, body)
#let try_it(body) = _callout("Try it", try-fill, body)
#let technical_detail(body) = _callout("Technical detail", tech-fill, body)

#let qs(text) = {
  text(font: qs-font)[#text]
}

#let code_stream(items) = {
  box(
    fill: code-fill,
    stroke: (paint: frame, thickness: 0.6pt),
    radius: 3pt,
    inset: 6pt,
    width: 100%,
  )[
    #set text(font: mono-font, size: 9pt)
    #items.join(" -> ")
  ]
}

#let glyph_label(name, codepoint) = {
  box(
    fill: accent-soft,
    stroke: (paint: frame, thickness: 0.5pt),
    radius: 3pt,
    inset: (x: 6pt, y: 4pt),
    width: 100%,
  )[
    #set text(font: mono-font, size: 8.5pt)
    #name
    #h(8pt)
    #codepoint
  ]
}

#let bitmap_grid(rows, guide_rows: ()) = {
  let h = rows.len()
  let w = if h == 0 { 0 } else { rows.at(0).clusters().len() }
  let cells = ()
  for (row_i, row) in rows.enumerate() {
    for cell in row.clusters() {
      let on = cell == "#"
      let guide = row_i in guide_rows
      cells.push(
        rect(
          width: 9pt,
          height: 9pt,
          fill: if on { accent } else { white },
          stroke: if guide {
            (paint: rgb("#C45B39"), thickness: 0.9pt)
          } else {
            (paint: frame, thickness: 0.5pt)
          },
          radius: 1pt,
        )
      )
    }
  }
  grid(columns: w, gutter: 1.6pt, ..cells)
}

#let bitmap_figure(title, rows, guide_rows: ()) = figure(
  kind: "bitmap",
  supplement: [Bitmap],
  caption: [#title],
)[
  #bitmap_grid(rows, guide_rows: guide_rows)
]

#let two_col(left, right) = grid(
  columns: (1fr, 1fr),
  gutter: 12pt,
  [#left],
  [#right],
)

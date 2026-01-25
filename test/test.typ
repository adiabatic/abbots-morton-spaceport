#set document(
  // make document reproducible
  date: none, // datetime(year: 2024, month: 11, day: 2),
)

#set page(
  paper: "us-letter",
  numbering: "1 of 1",
)

#let fontStack = ("Abbots Morton Spaceport Mono", "Departure Mono")

// #let ppi = 96
// #let sizeInPixels = 11
// #let sizeInPoints = 72 / ppi * sizeInPixels * 1pt

// You don’t need to care about pixel alignment if you’re printing on a 600dpi laser printer, and 8.25pt is too small to read Quikscript comfortably anyway
#let sizeInPoints = 12pt

#set text(
  font: fontStack,
  size: sizeInPoints,
)

#show heading: it => [
  #set text(
    font: fontStack,
    size: sizeInPoints * 2,
  )

  #it
]

#show raw: it => [
  #set text(
    font: fontStack,
    size: sizeInPoints,
  )
  #it
]

#show link: underline

= Abbots Morton Spaceport Mono in print

#outline()

== Quikscript —       .

What is the price of a foot-bath kit? A goose in a dress near North Square thought it would cure jelly ankle. The goat nurse suggested palm oil in the mouth or wrapped around the ankle with a cloth. (Shiny yellow cheese helps with night vision on the ax exams in both Llanberis and Loch Affric.)

      - ?       ·       .                 .              ·   ·.

== Lorem ipsum.

#lorem(100)

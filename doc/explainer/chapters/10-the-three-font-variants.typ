#import "../style.typ": *

= The three font variants

The build produces three user-facing fonts:

== a. Mono

- File: `AbbotsMortonSpaceportMono.otf`
- Fixed width (7-pixel cell logic)
- No contextual Quikscript joining
- Best for tabular or code-like layout

== b. Junior (Sans)

- File: `AbbotsMortonSpaceportSansJunior.otf`
- Proportional forms (`.prop` promoted to defaults)
- Includes `kern` and `mark`
- No `calt` and no `curs`
- Good for Junior-style separated writing

== c. Senior (Sans)

- File: `AbbotsMortonSpaceportSansSenior.otf`
- Proportional plus full joining system
- Includes contextual variants, half letters, alternates, ligatures, padding rules, ZWNJ escape hatch, and cursive attachment


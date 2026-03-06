#import "../style.typ": *

= Feature files and the OpenType pipeline

The build script turns glyph YAML into OpenType behavior code.

== a. What a feature is

A feature is a named rule bundle (`kern`, `mark`, `calt`, `curs`, etc.). Layout engines activate them during shaping.

== b. What a lookup is

A lookup is a concrete rule set inside a feature. Features can contain many lookups, often ordered to control interactions.

== c. Build script high-level flow

1. Load YAML glyph and metadata files.
2. Build variant-specific glyph sets (Mono, Junior, Senior).
3. Generate FEA text (`kern`, `mark`, plus `calt`/`curs` for Senior).
4. Compile features into binary OpenType tables with fontTools.
5. Save both `.otf` and human-readable `.fea` in `test/`.

#technical_detail([
The key functions are `generate_calt_fea()`, `generate_curs_fea()`, and `generate_extended_entry_variants()`. A generic `generate_liga_fea()` function exists, but in the current Senior build ligature substitutions are emitted inside `calt` (`lookup calt_liga`) to preserve interaction with contextual alternates.
])


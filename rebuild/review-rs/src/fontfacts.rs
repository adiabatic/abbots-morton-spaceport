//! The font facts, `ams-review-font-facts/1`, which `units_exec.write_font_facts` writes through the ink module's own classes (`doc/rebuild-design.md` §14.2): each font's glyph order with each glyph's shape key and origin, the shapes' drawings and grid cells by shape key, and the after font's junction classifier sets. The engine reads no outline.
//!
//! A shape key derives from the outline's content, so keys compare across the three fonts. The engine holds each key as a [`ShapeId`], an index into the shape table sorted by key, so ids compare and sort as the keys' bytes do, which is the order the pieces a key leads compare in. A glyph with no outline has no shape (`ink.EMPTY_OUTLINE_KEY`), which orders before every shape as the empty key does.

use std::collections::HashMap;

use serde_json::Value;

use crate::wire::{
    Result, WireError, array, boolean, error, integer, object, read_file, string, tuple,
};

/// The font facts file's format marker, `units_exec.FONT_FACTS_FORMAT`.
pub const FONT_FACTS_FORMAT: &str = "ams-review-font-facts/1";

/// The bytes of one shape key, a sha1 digest (`ink.outline_shape_key`).
pub const KEY_BYTES: usize = 20;

/// A shape key as its index in the shape table, which is sorted by key, so the order of ids is the order of the keys' bytes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ShapeId(u32);

impl ShapeId {
    /// The id's index in the shape table.
    pub fn index(self) -> usize {
        self.0 as usize
    }
}

/// One interned outline: its key, whether it has any point (`OutlineIntern.draws`), and its filled grid cells in its canonical frame as sorted `(column, row)` pairs, or None when it is not a grid-rectilinear picture (`OutlineIntern.cells`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Shape {
    pub key: [u8; KEY_BYTES],
    pub drawn: bool,
    pub cells: Option<Vec<(i64, i64)>>,
}

/// One glyph's `OutlineCache.shape_key`: its shape, None for a glyph with no outline, and the origin of its outline in its own frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Placement {
    pub shape: Option<ShapeId>,
    pub origin_x: i64,
    pub origin_y: i64,
}

/// One glyph of a font's glyph order.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Glyph {
    pub name: String,
    pub placement: Placement,
}

/// One font's glyphs by glyph id, in its glyph order.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FontGlyphs {
    glyphs: Vec<Glyph>,
    ids: HashMap<String, u32>,
}

impl FontGlyphs {
    /// The number of glyphs in the font.
    pub fn len(&self) -> usize {
        self.glyphs.len()
    }

    /// Whether the font has no glyph.
    pub fn is_empty(&self) -> bool {
        self.glyphs.is_empty()
    }

    /// The glyphs in glyph id order.
    pub fn glyphs(&self) -> &[Glyph] {
        &self.glyphs
    }

    /// The glyph with an id, as `Shaper.glyph_name` names it from the glyph order.
    pub fn glyph(&self, id: u32) -> Option<&Glyph> {
        self.glyphs.get(id as usize)
    }

    /// The full name of the glyph with an id (`Shaper.glyph_name`).
    pub fn glyph_name(&self, id: u32) -> Option<&str> {
        self.glyph(id).map(|glyph| glyph.name.as_str())
    }

    /// The id of the glyph with a name.
    pub fn id_of(&self, name: &str) -> Option<u32> {
        self.ids.get(name).copied()
    }

    /// The `OutlineCache.shape_key` of the glyph with a name.
    pub fn shape_key(&self, name: &str) -> Option<Placement> {
        self.id_of(name)
            .map(|id| self.glyphs[id as usize].placement)
    }
}

/// The after font's curs sets as `JunctionClassifier.__init__` builds them: at each height, in ascending order, the glyphs with an exit anchor and the glyphs with an entry anchor, as sorted glyph ids.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Junctions {
    heights: Vec<i64>,
    exits: Vec<Vec<u32>>,
    entries: Vec<Vec<u32>>,
}

impl Junctions {
    /// The heights in ascending order (`JunctionClassifier.heights`).
    pub fn heights(&self) -> &[i64] {
        &self.heights
    }

    /// The glyphs with an exit anchor at the height at `index` in [`Junctions::heights`].
    pub fn exits(&self, index: usize) -> &[u32] {
        &self.exits[index]
    }

    /// The glyphs with an entry anchor at the height at `index` in [`Junctions::heights`].
    pub fn entries(&self, index: usize) -> &[u32] {
        &self.entries[index]
    }

    /// Whether the glyph has an exit anchor at the height at `index`.
    pub fn has_exit(&self, index: usize, glyph: u32) -> bool {
        self.exits[index].binary_search(&glyph).is_ok()
    }

    /// Whether the glyph has an entry anchor at the height at `index`.
    pub fn has_entry(&self, index: usize, glyph: u32) -> bool {
        self.entries[index].binary_search(&glyph).is_ok()
    }
}

/// One of the worker's three fonts.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Side {
    Before,
    After,
    Junior,
}

impl Side {
    /// The side's name in the init and the facts.
    pub fn name(self) -> &'static str {
        match self {
            Side::Before => "before",
            Side::After => "after",
            Side::Junior => "junior",
        }
    }
}

/// The font facts file, `ams-review-font-facts/1`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FontFacts {
    before: FontGlyphs,
    after: FontGlyphs,
    junior: FontGlyphs,
    junctions: Junctions,
    shapes: Vec<Shape>,
}

fn decode_key(hex: &str, what: &str) -> Result<[u8; KEY_BYTES]> {
    let refused = || {
        WireError(format!(
            "{what} is not {KEY_BYTES} bytes of lowercase hex: {hex:?}"
        ))
    };
    if hex.len() != 2 * KEY_BYTES
        || !hex
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(refused());
    }
    let mut key = [0; KEY_BYTES];
    for (index, byte) in key.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&hex[2 * index..2 * index + 2], 16).map_err(|_| refused())?;
    }
    Ok(key)
}

fn read_cells(value: &Value, what: &str) -> Result<Option<Vec<(i64, i64)>>> {
    if value.is_null() {
        return Ok(None);
    }
    let cells = array(value, what)?
        .iter()
        .map(|cell| {
            let [column, row] = tuple(cell, what)?;
            Ok((integer(column, what)?, integer(row, what)?))
        })
        .collect::<Result<Vec<_>>>()?;
    if cells.windows(2).any(|pair| pair[0] >= pair[1]) {
        return error(format!("{what} are not in strictly ascending order"));
    }
    Ok(Some(cells))
}

impl FontFacts {
    /// Read the font facts file at `path`.
    pub fn read(path: &str) -> Result<Self> {
        let bytes = read_file(path, "the font facts")?;
        let text = String::from_utf8(bytes)
            .map_err(|_| WireError(format!("the font facts {path} are not UTF-8")))?;
        Self::parse(&text)
    }

    /// Parse a font facts file's text, refusing another format, an origin that is not an integer, a glyph whose shape the table lacks, a glyph with no outline whose origin is not zero, a name a font gives two glyphs, cells out of order, and a curs set that names a glyph the after font lacks or whose heights differ from the other set's.
    pub fn parse(text: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(text)
            .map_err(|failure| WireError(format!("the font facts are not JSON: {failure}")))?;
        let map = object(&value, "the font facts", &["format", "fonts", "shapes"])?;
        let format = string(&map["format"], "the font facts' format")?;
        if format != FONT_FACTS_FORMAT {
            return error(format!(
                "the font facts are {format:?}, not {FONT_FACTS_FORMAT}"
            ));
        }
        let Value::Object(table) = &map["shapes"] else {
            return error("the font facts' shapes are not a JSON object");
        };
        let mut shapes = table
            .iter()
            .map(|(hex, entry)| {
                let [drawn, cells] = tuple(entry, "a shape")?;
                Ok(Shape {
                    key: decode_key(hex, "a shape key")?,
                    drawn: boolean(drawn, "a shape's drawn flag")?,
                    cells: read_cells(cells, "a shape's cells")?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        shapes.sort_by_key(|shape| shape.key);
        if shapes.windows(2).any(|pair| pair[0].key == pair[1].key) {
            return error("the font facts' shape table holds a key twice");
        }
        let fonts = object(
            &map["fonts"],
            "the font facts' fonts",
            &["before", "after", "junior"],
        )?;
        let glyphs = |side: Side, keys: &[&str]| -> Result<FontGlyphs> {
            let font = object(
                &fonts[side.name()],
                &format!("the {} font's facts", side.name()),
                keys,
            )?;
            Self::glyphs(&font["glyphs"], side, &shapes)
        };
        let before = glyphs(Side::Before, &["glyphs"])?;
        let junior = glyphs(Side::Junior, &["glyphs"])?;
        let after = glyphs(Side::After, &["glyphs", "exits", "entries"])?;
        let after_facts = &fonts["after"];
        let (heights, exits) = Self::curs_sets(&after_facts["exits"], "an exit set", &after)?;
        let (entry_heights, entries) =
            Self::curs_sets(&after_facts["entries"], "an entry set", &after)?;
        if heights != entry_heights {
            return error(format!(
                "the after font's exit heights {heights:?} are not its entry heights {entry_heights:?}"
            ));
        }
        Ok(Self {
            before,
            after,
            junior,
            junctions: Junctions {
                heights,
                exits,
                entries,
            },
            shapes,
        })
    }

    fn glyphs(value: &Value, side: Side, shapes: &[Shape]) -> Result<FontGlyphs> {
        let what = format!("a {} glyph", side.name());
        let mut ids = HashMap::new();
        let glyphs = array(value, &what)?
            .iter()
            .enumerate()
            .map(|(id, row)| {
                let [name, hex, origin_x, origin_y] = tuple(row, &what)?;
                let name = string(name, &what)?;
                let hex = string(hex, &what)?;
                let (origin_x, origin_y) = (integer(origin_x, &what)?, integer(origin_y, &what)?);
                let shape = if hex.is_empty() {
                    if (origin_x, origin_y) != (0, 0) {
                        return error(format!(
                            "{what} {name} has no outline but the origin ({origin_x}, {origin_y})"
                        ));
                    }
                    None
                } else {
                    let key = decode_key(&hex, &what)?;
                    let index = shapes
                        .binary_search_by(|shape| shape.key.cmp(&key))
                        .map_err(|_| {
                            WireError(format!(
                                "{what} {name} names a shape the table lacks: {hex}"
                            ))
                        })?;
                    Some(ShapeId(
                        u32::try_from(index).expect("a shape table fits u32"),
                    ))
                };
                let id = u32::try_from(id).expect("a glyph order fits u32");
                if ids.insert(name.clone(), id).is_some() {
                    return error(format!("{what} name {name} is on two glyph ids"));
                }
                Ok(Glyph {
                    name,
                    placement: Placement {
                        shape,
                        origin_x,
                        origin_y,
                    },
                })
            })
            .collect::<Result<Vec<_>>>()?;
        Ok(FontGlyphs { glyphs, ids })
    }

    fn curs_sets(
        value: &Value,
        what: &str,
        after: &FontGlyphs,
    ) -> Result<(Vec<i64>, Vec<Vec<u32>>)> {
        let mut heights = Vec::new();
        let mut sets = Vec::new();
        for item in array(value, what)? {
            let [height, names] = tuple(item, what)?;
            let height = integer(height, what)?;
            if heights.last().is_some_and(|last| *last >= height) {
                return error(format!(
                    "{what}'s heights are not in strictly ascending order"
                ));
            }
            let mut ids = array(names, what)?
                .iter()
                .map(|name| {
                    let name = string(name, what)?;
                    after.id_of(&name).ok_or_else(|| {
                        WireError(format!("{what} names {name}, which the after font lacks"))
                    })
                })
                .collect::<Result<Vec<u32>>>()?;
            ids.sort_unstable();
            ids.dedup();
            heights.push(height);
            sets.push(ids);
        }
        Ok((heights, sets))
    }

    /// One font's glyphs.
    pub fn font(&self, side: Side) -> &FontGlyphs {
        match side {
            Side::Before => &self.before,
            Side::After => &self.after,
            Side::Junior => &self.junior,
        }
    }

    /// The after font's curs sets.
    pub fn junctions(&self) -> &Junctions {
        &self.junctions
    }

    /// The shape table, sorted by key.
    pub fn shapes(&self) -> &[Shape] {
        &self.shapes
    }

    /// One shape.
    pub fn shape(&self, id: ShapeId) -> &Shape {
        &self.shapes[id.index()]
    }

    /// Whether the shape has any point (`OutlineIntern.draws`).
    pub fn draws(&self, id: ShapeId) -> bool {
        self.shape(id).drawn
    }

    /// The shape's filled grid cells in its canonical frame, or None when it is not a grid-rectilinear picture (`OutlineIntern.cells`).
    pub fn cells(&self, id: ShapeId) -> Option<&[(i64, i64)]> {
        self.shape(id).cells.as_deref()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LOW: &str = "00000000000000000000000000000000000000aa";
    const HIGH: &str = "ff000000000000000000000000000000000000aa";

    fn facts_text(after_glyphs: &str, shapes: &str) -> String {
        format!(
            r#"{{"format":"ams-review-font-facts/1","fonts":{{"before":{{"glyphs":[["space","",0,0],["qsPea","{HIGH}",50,-150]]}},"after":{{"glyphs":{after_glyphs},"exits":[[0,["qsPea.ex-y0"]],[5,[]]],"entries":[[0,[]],[5,["qsPea.ex-y0","qsPea"]]]}},"junior":{{"glyphs":[["qsPea","{LOW}",0,0]]}}}},"shapes":{shapes}}}"#
        )
    }

    fn default_shapes() -> String {
        format!(r#"{{"{HIGH}":[true,[[0,0],[0,1],[1,0]]],"{LOW}":[true,null]}}"#)
    }

    fn default_after() -> String {
        format!(r#"[["qsPea","{HIGH}",0,0],["qsPea.ex-y0","{LOW}",0,-150]]"#)
    }

    #[test]
    fn the_facts_read_back_as_the_values_python_holds() {
        let facts =
            FontFacts::parse(&facts_text(&default_after(), &default_shapes())).expect("facts");
        let pea = facts.font(Side::Before).shape_key("qsPea").expect("qsPea");
        let junior = facts.font(Side::Junior).shape_key("qsPea").expect("qsPea");
        assert!(
            junior.shape < pea.shape,
            "the ids do not order as the keys do"
        );
        assert_eq!(facts.font(Side::Before).glyph_name(0), Some("space"));
        assert_eq!(
            facts
                .font(Side::Before)
                .shape_key("space")
                .expect("space")
                .shape,
            None
        );
        assert_eq!(
            facts.cells(pea.shape.expect("a shape")),
            Some(&[(0, 0), (0, 1), (1, 0)][..])
        );
        assert_eq!(facts.cells(junior.shape.expect("a shape")), None);
        let junctions = facts.junctions();
        assert_eq!(junctions.heights(), &[0, 5]);
        assert!(junctions.has_exit(0, 1) && !junctions.has_exit(1, 1));
        assert_eq!(junctions.entries(1), &[0, 1]);
    }

    #[test]
    fn facts_python_never_writes_are_refused() {
        let refused = [
            (
                default_after().replace("-150]", "-150.0]"),
                default_shapes(),
            ),
            (
                default_after().replace(LOW, &LOW.replace("aa", "ab")),
                default_shapes(),
            ),
            (default_after().replace(LOW, ""), default_shapes()),
            (
                default_after().replace("qsPea.ex-y0", "qsPea"),
                default_shapes(),
            ),
            (
                default_after(),
                default_shapes().replace("[0,1],[1,0]", "[1,0],[0,1]"),
            ),
            (
                default_after(),
                default_shapes().replace(HIGH, &HIGH.to_uppercase()),
            ),
        ];
        for (after, shapes) in refused {
            assert!(
                FontFacts::parse(&facts_text(&after, &shapes)).is_err(),
                "{after} {shapes} was accepted"
            );
        }
        let unknown = facts_text(&default_after(), &default_shapes())
            .replace(r#"[5,["qsPea.ex-y0","qsPea"]]"#, r#"[5,["qsPea.ex-y9"]]"#);
        assert!(FontFacts::parse(&unknown).is_err());
        let uneven = facts_text(&default_after(), &default_shapes())
            .replace(r#""entries":[[0,[]],"#, r#""entries":[[1,[]],"#);
        assert!(FontFacts::parse(&uneven).is_err());
    }
}

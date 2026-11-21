//! Shaping as the Python worker shapes (`rebuild/CORPUS-UNITS-PORT-PLAN.md` § HarfBuzz and outlines): `shaping.Shaper.shape` with its two assertions over the linked HarfBuzz (`ffi`), the per-process memo of `ink._MemoizedShaper` as a per-thread one released at each batch's end ([`Shaper::release`], `ink.release_shape_memos`), and the feature dicts of `ink.features_for` and `ink.kern_neutral` ([`Features`]).
//!
//! [`Fonts`] holds one HarfBuzz font per side of the worker, shared read-only by every thread, and refuses an init whose HarfBuzz version is not the one this engine links. A [`Shaper`] owns one thread's buffer and memo. Glyph names come from the font facts' glyph order, never from HarfBuzz (`Shaper.glyph_name`).
//!
//! [`stage_shape`] is the `shape` stage of the stage fixtures.

use std::collections::HashMap;
use std::fmt;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::rc::Rc;

use ams_m1_kernel::emit::json_string;
use serde_json::Value;

use crate::ffi::{self, Buffer, Feature, Font};
use crate::fontfacts::{FontFacts, FontGlyphs, Side};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::{repr, repr_str};
use crate::wire::{Init, WireError};

/// A text the engine cannot shape as Python would, an init it refuses, or a `shape` stage input it cannot read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ShapeError(pub String);

impl fmt::Display for ShapeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ShapeError {}

impl From<WireError> for ShapeError {
    fn from(error: WireError) -> Self {
        Self(error.0)
    }
}

type Result<T> = std::result::Result<T, ShapeError>;

/// The HarfBuzz version this engine links, which an init's `harfbuzz` must equal.
pub fn harfbuzz_version() -> String {
    ffi::version()
}

/// Refuse an init's `harfbuzz`, the version the Python side shapes with, unless it is [`harfbuzz_version`].
pub fn check_harfbuzz(version: &str) -> Result<()> {
    let linked = harfbuzz_version();
    if version == linked {
        return Ok(());
    }
    Err(ShapeError(format!(
        "the init shapes with HarfBuzz {version}, and this engine links HarfBuzz {linked}; the pin in rebuild/review-rs/build.rs follows the locked uharfbuzz"
    )))
}

/// A HarfBuzz feature dict as the worker builds one, `dict[str, bool]`: each tag once, in insertion order.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct Features(Vec<(String, bool)>);

impl Features {
    /// No features, `{}`.
    pub fn new() -> Self {
        Self::default()
    }

    /// `features[tag] = on`: a tag already present keeps its place and takes the new value.
    pub fn set(&mut self, tag: &str, on: bool) {
        match self.0.iter_mut().find(|(held, _)| held == tag) {
            Some(entry) => entry.1 = on,
            None => self.0.push((tag.to_string(), on)),
        }
    }

    /// The entries in insertion order.
    pub fn entries(&self) -> &[(String, bool)] {
        &self.0
    }

    /// Whether the dict is empty, which `Shaper.shape` treats as no features.
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    /// `ink.features_for(config)`: no features for no configuration or `default`, otherwise one True entry per `+`-joined tag.
    pub fn for_config(config: Option<&str>) -> Self {
        let mut features = Self::new();
        match config {
            None | Some("" | "default") => {}
            Some(config) => {
                for tag in config.split('+') {
                    features.set(tag, true);
                }
            }
        }
        features
    }

    /// `ink.kern_neutral(features)`: the features with `kern` off.
    pub fn kern_neutral(&self) -> Self {
        let mut features = self.clone();
        features.set("kern", false);
        features
    }

    /// The dict as Python holds it.
    pub fn to_py(&self) -> PyValue {
        PyValue::Dict(
            self.0
                .iter()
                .map(|(tag, on)| (PyValue::str(tag), PyValue::Bool(*on)))
                .collect(),
        )
    }

    /// Read a `Shaper.shape` features argument as the typed encoding carries it: None, or a dict of `str` to `bool`.
    pub fn from_py(value: &PyValue) -> Result<Self> {
        let mut features = Self::new();
        match value {
            PyValue::None => {}
            PyValue::Dict(entries) => {
                for (tag, on) in entries {
                    let (PyValue::Str(tag), PyValue::Bool(on)) = (tag, on) else {
                        return Err(ShapeError(format!(
                            "a feature entry is not a str and a bool: {tag:?}, {on:?}"
                        )));
                    };
                    features.set(tag, *on);
                }
            }
            other => {
                return Err(ShapeError(format!(
                    "the features are {}, not a dict or None",
                    other.kind()
                )));
            }
        }
        Ok(features)
    }

    /// The memo's view of the dict, as `_MemoizedShaper.shape` keys it: the entries sorted, or None for an empty dict.
    fn memo_key(&self) -> Option<Vec<(String, bool)>> {
        if self.0.is_empty() {
            return None;
        }
        let mut entries = self.0.clone();
        entries.sort();
        Some(entries)
    }

    fn hb_features(&self) -> Result<Vec<Feature>> {
        self.0
            .iter()
            .map(|(tag, on)| {
                Feature::global(tag, u32::from(*on))
                    .ok_or_else(|| ShapeError(format!("HarfBuzz cannot parse the feature {tag:?}")))
            })
            .collect()
    }
}

/// One shaped run, `shaping.ShapeResult`: per glyph its id in the font's glyph order (the facts name it), its cluster, and `(x_offset, y_offset, x_advance)`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ShapeResult {
    pub glyphs: Vec<u32>,
    pub clusters: Vec<u32>,
    pub positions: Vec<(i32, i32, i32)>,
}

impl ShapeResult {
    /// The glyph names, as `ShapeResult.names` holds them, from `font`, the shaped font's facts.
    pub fn names<'a>(&'a self, font: &'a FontGlyphs) -> impl Iterator<Item = &'a str> + 'a {
        self.glyphs.iter().map(|&glyph| {
            font.glyph_name(glyph)
                .expect("a shaped glyph id is in the font's glyph order")
        })
    }

    /// The result as Python holds it: a `ShapeResult` of three tuples.
    pub fn to_py(&self, font: &FontGlyphs) -> PyValue {
        PyValue::Record(
            "ShapeResult".to_string(),
            vec![
                (
                    "names".to_string(),
                    PyValue::Tuple(self.names(font).map(PyValue::str).collect()),
                ),
                ("clusters".to_string(), clusters_value(&self.clusters)),
                (
                    "positions".to_string(),
                    PyValue::Tuple(
                        self.positions
                            .iter()
                            .map(|&(x_offset, y_offset, x_advance)| {
                                PyValue::Tuple(vec![
                                    PyValue::Int(x_offset.into()),
                                    PyValue::Int(y_offset.into()),
                                    PyValue::Int(x_advance.into()),
                                ])
                            })
                            .collect(),
                    ),
                ),
            ],
        )
    }
}

fn clusters_value(clusters: &[u32]) -> PyValue {
    PyValue::Tuple(
        clusters
            .iter()
            .map(|&cluster| PyValue::Int(cluster.into()))
            .collect(),
    )
}

/// The worker's three fonts as HarfBuzz fonts, shared read-only by every thread.
pub struct Fonts {
    before: Font,
    after: Font,
    junior: Font,
}

impl Fonts {
    /// Open the init's three fonts, refusing an init whose `harfbuzz` is not [`harfbuzz_version`] and a font whose glyph count differs from its glyph order in `facts`.
    pub fn open(init: &Init, facts: &FontFacts) -> Result<Self> {
        check_harfbuzz(&init.harfbuzz)?;
        let open = |side: Side| -> Result<Font> {
            let path = init_font(init, side);
            let font = Font::open(Path::new(path)).map_err(ShapeError)?;
            let ordered = facts.font(side).len();
            if font.glyph_count() as usize != ordered {
                return Err(ShapeError(format!(
                    "the {} font {path} has {} glyphs and its facts' glyph order {ordered}",
                    side.name(),
                    font.glyph_count()
                )));
            }
            Ok(font)
        };
        Ok(Self {
            before: open(Side::Before)?,
            after: open(Side::After)?,
            junior: open(Side::Junior)?,
        })
    }

    fn font(&self, side: Side) -> &Font {
        match side {
            Side::Before => &self.before,
            Side::After => &self.after,
            Side::Junior => &self.junior,
        }
    }
}

fn init_font(init: &Init, side: Side) -> &str {
    match side {
        Side::Before => &init.fonts.before,
        Side::After => &init.fonts.after,
        Side::Junior => &init.fonts.junior,
    }
}

type MemoKey = (Side, String, Option<Vec<(String, bool)>>);

/// One thread's shaper: its HarfBuzz buffer and its shape memo over the shared [`Fonts`].
pub struct Shaper<'a> {
    fonts: &'a Fonts,
    facts: &'a FontFacts,
    buffer: Buffer,
    glyphs: Vec<ffi::Glyph>,
    memo: HashMap<MemoKey, Rc<ShapeResult>>,
}

impl<'a> Shaper<'a> {
    /// A shaper over `fonts`, naming glyphs from `facts`, with an empty memo.
    pub fn new(fonts: &'a Fonts, facts: &'a FontFacts) -> Self {
        Self {
            fonts,
            facts,
            buffer: Buffer::new(),
            glyphs: Vec::new(),
            memo: HashMap::new(),
        }
    }

    /// `_MemoizedShaper.shape`: the memo's result for `(side, text, features)` when it holds one, otherwise `Shaper.shape`'s, kept until [`Shaper::release`]. Two dicts with the same entries in another order share a result, as they share Python's key.
    pub fn shape(
        &mut self,
        side: Side,
        text: &str,
        features: &Features,
    ) -> Result<Rc<ShapeResult>> {
        let key = (side, text.to_string(), features.memo_key());
        if let Some(result) = self.memo.get(&key) {
            return Ok(Rc::clone(result));
        }
        let result = Rc::new(self.shape_once(side, text, features)?);
        self.memo.insert(key, Rc::clone(&result));
        Ok(result)
    }

    /// Forget every shape held (`_MemoizedShaper.release`); the engine calls it at each batch's end, as `release_shape_memos` runs after each of the worker's.
    pub fn release(&mut self) {
        self.memo.clear();
    }

    /// `Shaper.shape`: shape with no features when the dict is empty, and refuse a glyph with a nonzero `y_advance`, then clusters that decrease, as its two assertions do.
    fn shape_once(&mut self, side: Side, text: &str, features: &Features) -> Result<ShapeResult> {
        let codepoints: Vec<u32> = text.chars().map(u32::from).collect();
        let hb_features = features.hb_features()?;
        self.buffer
            .shape(
                self.fonts.font(side),
                &codepoints,
                &hb_features,
                &mut self.glyphs,
            )
            .map_err(|failure| ShapeError(format!("shaping {}: {failure}", repr_str(text))))?;
        let order = self.facts.font(side);
        let mut result = ShapeResult {
            glyphs: Vec::with_capacity(self.glyphs.len()),
            clusters: Vec::with_capacity(self.glyphs.len()),
            positions: Vec::with_capacity(self.glyphs.len()),
        };
        for glyph in &self.glyphs {
            if order.glyph(glyph.id).is_none() {
                return Err(ShapeError(format!(
                    "shaping {} gave the glyph id {}, outside the {} font's glyph order",
                    repr_str(text),
                    glyph.id,
                    side.name()
                )));
            }
            result.glyphs.push(glyph.id);
            result.clusters.push(glyph.cluster);
        }
        for glyph in &self.glyphs {
            if glyph.y_advance != 0 {
                return Err(ShapeError(format!(
                    "nonzero y_advance shaping {}",
                    repr_str(text)
                )));
            }
            result
                .positions
                .push((glyph.x_offset, glyph.y_offset, glyph.x_advance));
        }
        if result.clusters.windows(2).any(|pair| pair[1] < pair[0]) {
            let clusters =
                repr(&clusters_value(&result.clusters)).expect("a tuple of ints has a repr");
            return Err(ShapeError(format!(
                "non-monotonic clusters shaping {}: {clusters}",
                repr_str(text)
            )));
        }
        Ok(result)
    }
}

// --- the shape stage ----------------------------------------------------------------------------

fn canonical(path: &str) -> Result<PathBuf> {
    std::fs::canonicalize(path)
        .map_err(|failure| ShapeError(format!("cannot resolve the font {path}: {failure}")))
}

/// The `shape` stage: for each line of `stage_file`, shape the line's `text` with its `features` on the init's font whose resolved path is the line's `font`, through one [`Shaper`], and write the `ShapeResult` as one `{"key", "value"}` line in the typed encoding.
pub fn stage_shape(init_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let init = Init::read(init_path)?;
    let facts = FontFacts::read(&init.font_facts)?;
    let fonts = Fonts::open(&init, &facts)?;
    let sides = Side::ALL
        .iter()
        .map(|&side| Ok((canonical(init_font(&init, side))?, side)))
        .collect::<Result<Vec<_>>>()?;
    let mut shaper = Shaper::new(&fonts, &facts);
    let file = std::fs::File::open(stage_file).map_err(|failure| {
        ShapeError(format!("cannot read {}: {failure}", stage_file.display()))
    })?;
    for line in std::io::BufReader::new(file).lines() {
        let line = line.map_err(|failure| {
            ShapeError(format!("cannot read {}: {failure}", stage_file.display()))
        })?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| ShapeError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return Err(ShapeError(format!("a stage line has no key: {line:.200}")));
        };
        let Some(font) = record["font"].as_str() else {
            return Err(ShapeError(format!("{key}: the line names no font")));
        };
        let Some(&(_, side)) = sides.iter().find(|(path, _)| path == Path::new(font)) else {
            return Err(ShapeError(format!(
                "{key}: the font {font} is none of the init's three"
            )));
        };
        let PyValue::Str(text) = PyValue::from_typed(&record["text"])
            .map_err(|failure| ShapeError(format!("{key}: {failure}")))?
        else {
            return Err(ShapeError(format!("{key}: the text is not a str")));
        };
        let features = PyValue::from_typed(&record["features"])
            .map_err(ShapeError)
            .and_then(|value| Features::from_py(&value))
            .map_err(|failure| ShapeError(format!("{key}: {failure}")))?;
        let result = shaper
            .shape(side, &text, &features)
            .map_err(|failure| ShapeError(format!("{key}: {failure}")))?;
        writeln!(
            out,
            "{{\"key\":{},\"value\":{}}}",
            json_string(key),
            result.to_py(facts.font(side)).to_typed()
        )
        .map_err(|failure| ShapeError(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entries(features: &Features) -> Vec<(&str, bool)> {
        features
            .entries()
            .iter()
            .map(|(tag, on)| (tag.as_str(), *on))
            .collect()
    }

    #[test]
    fn a_configuration_gives_the_feature_dict_python_builds() {
        assert!(Features::for_config(None).is_empty());
        assert!(Features::for_config(Some("default")).is_empty());
        assert!(Features::for_config(Some("")).is_empty());
        assert_eq!(
            entries(&Features::for_config(Some("ss03+ss05"))),
            [("ss03", true), ("ss05", true)]
        );
        assert_eq!(
            entries(&Features::for_config(Some("ss05+ss03+ss05"))),
            [("ss05", true), ("ss03", true)]
        );
    }

    #[test]
    fn kern_neutral_turns_kern_off_in_place_or_at_the_end() {
        assert_eq!(
            entries(&Features::for_config(Some("ss10")).kern_neutral()),
            [("ss10", true), ("kern", false)]
        );
        assert_eq!(
            entries(&Features::for_config(Some("kern+ss10")).kern_neutral()),
            [("kern", false), ("ss10", true)]
        );
        assert_eq!(entries(&Features::new().kern_neutral()), [("kern", false)]);
    }

    #[test]
    fn the_memo_keys_a_dict_by_its_sorted_entries_and_an_empty_one_as_none() {
        let mut forward = Features::new();
        forward.set("ss10", true);
        forward.set("kern", false);
        let mut backward = Features::new();
        backward.set("kern", false);
        backward.set("ss10", true);
        assert_eq!(forward.memo_key(), backward.memo_key());
        assert_eq!(Features::new().memo_key(), None);
        assert_eq!(
            Features::from_py(&PyValue::None).expect("None reads"),
            Features::new()
        );
        assert_eq!(
            Features::from_py(&forward.to_py()).expect("a dict reads"),
            forward
        );
        assert!(Features::from_py(&PyValue::List(vec![])).is_err());
    }

    #[test]
    fn only_the_linked_harfbuzz_version_is_accepted() {
        assert_eq!(check_harfbuzz(&harfbuzz_version()), Ok(()));
        let Err(ShapeError(message)) = check_harfbuzz("0.0.1") else {
            panic!("HarfBuzz 0.0.1 was accepted");
        };
        assert!(message.contains("HarfBuzz 0.0.1"), "{message}");
        assert!(message.contains(&harfbuzz_version()), "{message}");
    }
}

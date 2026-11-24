//! Shaping over the linked HarfBuzz (`ffi`), as `shaping.Shaper.shape` shapes with its two assertions, with a shape memo per thread released at each batch's end ([`Shaper::release`]), and the feature dicts a configuration shapes with, as `ink.features_for` and `ink.kern_neutral` build them ([`Features`]).
//!
//! [`Fonts`] holds one HarfBuzz font per side of the worker, shared read-only by every thread, and refuses an init whose HarfBuzz version is not the one this engine links. A [`Shaper`] owns one thread's buffer and memo. Glyph names come from the font facts' glyph order, never from HarfBuzz.

use std::collections::HashMap;
use std::fmt;
use std::path::Path;
use std::rc::Rc;

use crate::ffi::{self, Buffer, Feature, Font};
use crate::fontfacts::{FontFacts, FontGlyphs, Side};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::{repr, repr_str};
use crate::wire::{Init, WireError};

/// A text the engine cannot shape, or an init it refuses.
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

    /// The memo's key for the dict: the entries sorted, or None for an empty dict.
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
}

fn clusters_value(clusters: &[u32]) -> PyValue {
    PyValue::Tuple(
        clusters
            .iter()
            .map(|&cluster| PyValue::Int(cluster.into()))
            .collect(),
    )
}

/// The worker's fonts as HarfBuzz fonts, shared read-only by every thread: all three, or the before and after fonts alone for the ink signatures.
pub struct Fonts {
    before: Font,
    after: Font,
    junior: Option<Font>,
}

impl Fonts {
    /// Open the init's three fonts, refusing an init whose `harfbuzz` is not [`harfbuzz_version`] and a font whose glyph count differs from its glyph order in `facts`.
    pub fn open(init: &Init, facts: &FontFacts) -> Result<Self> {
        let mut fonts = Self::open_before_after(init, facts)?;
        fonts.junior = Some(open_font(init, facts, Side::Junior)?);
        Ok(fonts)
    }

    /// Open the init's before and after fonts only, with [`Fonts::open`]'s refusals; shaping on the Junior side then fails.
    pub fn open_before_after(init: &Init, facts: &FontFacts) -> Result<Self> {
        check_harfbuzz(&init.harfbuzz)?;
        Ok(Self {
            before: open_font(init, facts, Side::Before)?,
            after: open_font(init, facts, Side::After)?,
            junior: None,
        })
    }

    fn font(&self, side: Side) -> Result<&Font> {
        match side {
            Side::Before => Ok(&self.before),
            Side::After => Ok(&self.after),
            Side::Junior => self
                .junior
                .as_ref()
                .ok_or_else(|| ShapeError("the junior font is not open".to_owned())),
        }
    }
}

fn open_font(init: &Init, facts: &FontFacts, side: Side) -> Result<Font> {
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

    /// The memo's result for `(side, text, features)` when it holds one, otherwise `Shaper.shape`'s, kept until [`Shaper::release`]. Two dicts with the same entries in another order share a result, as they share Python's key.
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

    /// Forget every shape held; the engine calls it at each batch's end, as `release_shape_memos` runs after each of the worker's.
    pub fn release(&mut self) {
        self.memo.clear();
    }

    /// `Shaper.shape`: shape with no features when the dict is empty, and refuse a glyph with a nonzero `y_advance`, then clusters that decrease, as its two assertions do.
    fn shape_once(&mut self, side: Side, text: &str, features: &Features) -> Result<ShapeResult> {
        let codepoints: Vec<u32> = text.chars().map(u32::from).collect();
        let hb_features = features.hb_features()?;
        self.buffer
            .shape(
                self.fonts.font(side)?,
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

//! The ink readings of a unit as the Python worker takes them (`rebuild/review/ink.py`): `InkComparator`'s placed pieces ([`InkComparator::named_run`], [`InkComparator::run_ink`], [`InkComparator::ink_pieces`], [`InkComparator::junior_pieces`]), its identity checks, and `config_diff` up to its picture path ([`InkComparator::config_diff`], [`picture_diff`]); `delta_digest` ([`delta_digest`]); `JuniorOracle.approves` ([`JuniorOracle`]); and `build._ink_readings` with the flag lines at the top of `build._phase1_unit` ([`InkReader`]).
//!
//! A piece is placed from the font facts, never from an outline: its shape is the glyph's interned shape and its position is the pen position plus the glyph's offsets and its shape's own-frame origin. A window with a piece that has no pixel picture takes the outline-level delta (`InkComparator._piece_diff`), which this engine does not build: such a window is refused with an error that says so, and the unit is the Python engine's (`rebuild/CORPUS-UNITS-PORT-PLAN.md` § Byte-identity hazards).
//!
//! [`stage_ink`] is the `ink` stage of the stage fixtures.

use std::collections::BTreeSet;
use std::fmt;
use std::io::{BufRead, Write};
use std::path::Path;
use std::rc::Rc;

use ams_m1_kernel::emit::json_string;
use serde_json::Value;

use crate::config_fold::ConfigFold;
use crate::fontfacts::{FontFacts, ShapeId, Side};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::repr;
use crate::shape::{Features, Fonts, ShapeError, ShapeResult, Shaper};
use crate::wire::{Init, UnitRecord, WireError};

/// The vocabulary name of `classify.PIXEL_SIZE`, the grid a piece's picture lies on.
pub const PIXEL_SIZE_NAME: &str = "rebuild.validation.classify.PIXEL_SIZE";

/// The vocabulary name of `ink.IDENTITY_DIFF`, which must be the sentinel [`Diff::identity`] models.
pub const IDENTITY_DIFF_NAME: &str = "rebuild.review.ink.IDENTITY_DIFF";

/// The one configuration `JuniorOracle.approves` answers for.
pub const SS10: &str = "ss10";

/// The glyph name prefix `junior_pieces` removes the tracking after.
const LETTER_PREFIX: &str = "qs";

/// A window the engine cannot read as Python reads it, or an `ink` stage input it cannot read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InkError(pub String);

impl fmt::Display for InkError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for InkError {}

impl From<ShapeError> for InkError {
    fn from(error: ShapeError) -> Self {
        Self(error.0)
    }
}

impl From<WireError> for InkError {
    fn from(error: WireError) -> Self {
        Self(error.0)
    }
}

type Result<T> = std::result::Result<T, InkError>;

/// One grid cell, `(column, row)`.
pub type Cell = (i64, i64);

/// A placed piece without its own-frame origin, as `ink_pieces` sorts and compares it: `(shape, absolute x, absolute y)`.
pub type Placed = (ShapeId, i64, i64);

/// One `config_diff` result: the cells only the before font paints, the cells only the after font paints, each sorted, and the follower shift in columns.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Diff {
    pub lost: Vec<Cell>,
    pub gained: Vec<Cell>,
    pub shift: i64,
}

impl Diff {
    /// `IDENTITY_DIFF`, `((), (), 0)`.
    pub fn identity() -> Self {
        Self {
            lost: Vec::new(),
            gained: Vec::new(),
            shift: 0,
        }
    }

    /// Whether the diff is `IDENTITY_DIFF`.
    pub fn is_identity(&self) -> bool {
        self.lost.is_empty() && self.gained.is_empty() && self.shift == 0
    }

    /// The diff as Python holds it, a tuple of two tuples of `(column, row)` tuples and an int.
    pub fn to_py(&self) -> PyValue {
        let cells = |cells: &[Cell]| {
            PyValue::Tuple(
                cells
                    .iter()
                    .map(|&(column, row)| {
                        PyValue::Tuple(vec![PyValue::Int(column), PyValue::Int(row)])
                    })
                    .collect(),
            )
        };
        PyValue::Tuple(vec![
            cells(&self.lost),
            cells(&self.gained),
            PyValue::Int(self.shift),
        ])
    }
}

/// `repr(diffs)` for a unit's diffs, the tuple `_phase1_unit` hashes into its diffs digest.
pub fn diffs_repr(diffs: &[Diff]) -> String {
    repr(&PyValue::Tuple(diffs.iter().map(Diff::to_py).collect()))
        .expect("a tuple of diffs has a repr")
}

/// `ink.delta_digest(diff)`: `d-` and the first twelve hex digits of the sha1 of the diff's repr.
pub fn delta_digest(diff: &Diff) -> String {
    let text = repr(&diff.to_py()).expect("a diff has a repr");
    let mut digest = sha1_hex(text.as_bytes());
    digest.truncate(12);
    format!("d-{digest}")
}

/// One placed piece in run order (`InkComparator.run_ink`): its shape, its absolute x and y, and its shape's own-frame origin x.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Piece {
    pub shape: ShapeId,
    pub x: i64,
    pub y: i64,
    pub origin_x: i64,
}

impl Piece {
    /// The piece without its origin.
    pub fn placed(&self) -> Placed {
        (self.shape, self.x, self.y)
    }
}

/// One entry of `InkComparator.named_run`: the glyph that put the piece down, by its id in the shaped font's glyph order, and the piece.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NamedPiece {
    pub glyph: u32,
    pub piece: Piece,
}

/// `ink.InkComparator` over two of the worker's fonts, its `before` and `after` sides, reading outlines from the font facts.
pub struct InkComparator<'f> {
    facts: &'f FontFacts,
    pixel_size: i64,
    before: Side,
    after: Side,
}

impl<'f> InkComparator<'f> {
    /// A comparator of `before` against `after`, on a grid of `pixel_size` font units.
    pub fn new(facts: &'f FontFacts, pixel_size: i64, before: Side, after: Side) -> Self {
        Self {
            facts,
            pixel_size,
            before,
            after,
        }
    }

    /// `named_run`: one side's kern-neutral shaped run, and one entry per glyph with ink, placed at the pen position plus the glyph's offsets and its shape's origin.
    pub fn named_run(
        &self,
        shaper: &mut Shaper,
        side: Side,
        text: &str,
        features: &Features,
    ) -> Result<(Rc<ShapeResult>, Vec<NamedPiece>)> {
        let result = shaper.shape(side, text, &features.kern_neutral())?;
        let glyphs = self.facts.font(side);
        let mut pieces = Vec::new();
        let mut pen_x = 0;
        for (&glyph, &(x_offset, y_offset, x_advance)) in
            result.glyphs.iter().zip(&result.positions)
        {
            let placement = glyphs
                .glyph(glyph)
                .expect("a shaped glyph id is in the font's glyph order")
                .placement;
            if let Some(shape) = placement.shape {
                pieces.push(NamedPiece {
                    glyph,
                    piece: Piece {
                        shape,
                        x: pen_x + i64::from(x_offset) + placement.origin_x,
                        y: i64::from(y_offset) + placement.origin_y,
                        origin_x: placement.origin_x,
                    },
                });
            }
            pen_x += i64::from(x_advance);
        }
        Ok((result, pieces))
    }

    /// `run_ink`: `named_run`'s pieces without their glyphs, in run order.
    pub fn run_ink(
        &self,
        shaper: &mut Shaper,
        side: Side,
        text: &str,
        features: &Features,
    ) -> Result<Vec<Piece>> {
        let (_result, pieces) = self.named_run(shaper, side, text, features)?;
        Ok(pieces.into_iter().map(|named| named.piece).collect())
    }

    /// `ink_pieces`: one side's placed pieces without their origins, sorted.
    pub fn ink_pieces(
        &self,
        shaper: &mut Shaper,
        side: Side,
        text: &str,
        features: &Features,
    ) -> Result<Vec<Placed>> {
        Ok(sorted_placed(&self.run_ink(shaper, side, text, features)?))
    }

    /// `pieces_identical`: whether both sides' `ink_pieces` are equal under one configuration.
    pub fn pieces_identical(&self, shaper: &mut Shaper, text: &str, config: &str) -> Result<bool> {
        let features = Features::for_config(Some(config));
        Ok(self.ink_pieces(shaper, self.before, text, &features)?
            == self.ink_pieces(shaper, self.after, text, &features)?)
    }

    /// `ink_identical`: whether `pieces_identical` holds under every configuration, asked in order until one fails.
    pub fn ink_identical(
        &self,
        shaper: &mut Shaper,
        text: &str,
        configs: &[String],
    ) -> Result<bool> {
        for config in configs {
            if !self.pieces_identical(shaper, text, config)? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// `junior_pieces`: the before side's kern-neutral placed pieces with no features, each glyph whose name starts with `qs` advancing the pen by its advance less `tracking`, sorted.
    pub fn junior_pieces(
        &self,
        shaper: &mut Shaper,
        text: &str,
        tracking: i64,
    ) -> Result<Vec<Placed>> {
        let result = shaper.shape(self.before, text, &Features::new().kern_neutral())?;
        let glyphs = self.facts.font(self.before);
        let mut pieces = Vec::new();
        let mut pen_x = 0;
        for (&glyph, &(x_offset, y_offset, x_advance)) in
            result.glyphs.iter().zip(&result.positions)
        {
            let entry = glyphs
                .glyph(glyph)
                .expect("a shaped glyph id is in the font's glyph order");
            let placement = entry.placement;
            if let Some(shape) = placement.shape {
                pieces.push((
                    shape,
                    pen_x + i64::from(x_offset) + placement.origin_x,
                    i64::from(y_offset) + placement.origin_y,
                ));
            }
            let removed = if entry.name.starts_with(LETTER_PREFIX) {
                tracking
            } else {
                0
            };
            pen_x += i64::from(x_advance) - removed;
        }
        pieces.sort_unstable();
        Ok(pieces)
    }

    /// `config_diff`: `IDENTITY_DIFF` when both sides' placed pieces are equal, else the pixel-level delta of their pictures ([`picture_diff`]). A window with a piece that has no picture is refused, since its delta is the outline-level one.
    pub fn config_diff(&self, shaper: &mut Shaper, text: &str, config: &str) -> Result<Diff> {
        let features = Features::for_config(Some(config));
        let before = self.run_ink(shaper, self.before, text, &features)?;
        let after = self.run_ink(shaper, self.after, text, &features)?;
        self.diff_of_runs(&before, &after).ok_or_else(|| {
            InkError(format!(
                "the window {} needs the outline-level delta under {config}, since a piece it places has no pixel picture (`InkComparator._piece_diff`); only the Python engine builds it",
                crate::pyfmt::repr::repr_str(text)
            ))
        })
    }

    /// `config_diff` over two shaped runs: `IDENTITY_DIFF` when their sorted placed pieces are equal, the picture delta when every piece has a picture, and None otherwise.
    pub fn diff_of_runs(&self, before: &[Piece], after: &[Piece]) -> Option<Diff> {
        if sorted_placed(before) == sorted_placed(after) {
            return Some(Diff::identity());
        }
        let before = self.run_pictures(before)?;
        let after = self.run_pictures(after)?;
        Some(picture_diff(&before, &after))
    }

    /// `_piece_cells`: the piece's shape's cells moved to its placement, none for a shape with no point, or None when the shape is not a grid picture or the placement is off the grid.
    fn piece_cells(&self, piece: &Piece) -> Option<Vec<Cell>> {
        if !self.facts.draws(piece.shape) {
            return Some(Vec::new());
        }
        let cells = self.facts.cells(piece.shape)?;
        let size = self.pixel_size;
        if piece.x % size != 0 || piece.y % size != 0 {
            return None;
        }
        let (column, row) = (piece.x / size, piece.y / size);
        Some(
            cells
                .iter()
                .map(|&(dx, dy)| (column + dx, row + dy))
                .collect(),
        )
    }

    /// `_run_pictures`: every piece's picture in run order, or None when any piece has none.
    fn run_pictures(&self, run: &[Piece]) -> Option<Vec<Vec<Cell>>> {
        run.iter().map(|piece| self.piece_cells(piece)).collect()
    }
}

fn sorted_placed(run: &[Piece]) -> Vec<Placed> {
    let mut placed: Vec<Placed> = run.iter().map(Piece::placed).collect();
    placed.sort_unstable();
    placed
}

/// `ink._picture_diff`: find the longest tail of pieces whose after union is the before union moved by a whole number of columns, move that tail back, and subtract the whole windows' unions both ways, translated so the delta's leftmost column is 0.
pub fn picture_diff(before: &[Vec<Cell>], after: &[Vec<Cell>]) -> Diff {
    let (mut tail, mut shift) = (0, 0);
    let mut before_tail: BTreeSet<Cell> = BTreeSet::new();
    let mut after_tail: BTreeSet<Cell> = BTreeSet::new();
    for k in 1..=before.len().min(after.len()) {
        before_tail.extend(&before[before.len() - k]);
        after_tail.extend(&after[after.len() - k]);
        let (Some(&(before_column, _)), Some(&(after_column, _))) =
            (before_tail.first(), after_tail.first())
        else {
            continue;
        };
        let dx = after_column - before_column;
        if after_tail.len() == before_tail.len()
            && after_tail
                .iter()
                .all(|&(column, row)| before_tail.contains(&(column - dx, row)))
        {
            (tail, shift) = (k, dx);
        }
    }
    let before_picture: BTreeSet<Cell> = before.iter().flatten().copied().collect();
    let head = after.len() - tail;
    let mut after_picture: BTreeSet<Cell> = after[..head].iter().flatten().copied().collect();
    after_picture.extend(
        after[head..]
            .iter()
            .flatten()
            .map(|&(column, row)| (column - shift, row)),
    );
    let lost: Vec<Cell> = before_picture.difference(&after_picture).copied().collect();
    let gained: Vec<Cell> = after_picture.difference(&before_picture).copied().collect();
    let x0 = match (lost.first(), gained.first()) {
        (None, None) => {
            return Diff {
                lost,
                gained,
                shift,
            };
        }
        (Some(&(column, _)), None) | (None, Some(&(column, _))) => column,
        (Some(&(left, _)), Some(&(right, _))) => left.min(right),
    };
    let moved = |cells: Vec<Cell>| -> Vec<Cell> {
        cells
            .into_iter()
            .map(|(column, row)| (column - x0, row))
            .collect()
    };
    Diff {
        lost: moved(lost),
        gained: moved(gained),
        shift,
    }
}

/// `ink.JuniorOracle`: the Junior font against the after font, and the Junior letter tracking the parent derived (`junior_tracking`).
pub struct JuniorOracle<'f> {
    comparator: InkComparator<'f>,
    tracking: i64,
}

impl<'f> JuniorOracle<'f> {
    /// The oracle over the facts' Junior and after fonts.
    pub fn new(facts: &'f FontFacts, pixel_size: i64, tracking: i64) -> Self {
        Self {
            comparator: InkComparator::new(facts, pixel_size, Side::Junior, Side::After),
            tracking,
        }
    }

    /// The Junior letter tracking.
    pub fn tracking(&self) -> i64 {
        self.tracking
    }

    /// The Junior font's placed pieces with the tracking removed (`junior_pieces`).
    pub fn junior_pieces(&self, shaper: &mut Shaper, text: &str) -> Result<Vec<Placed>> {
        self.comparator.junior_pieces(shaper, text, self.tracking)
    }

    /// `approves`: for a unit whose configurations are `ss10` alone, whether the Junior pieces equal the after font's `ink_pieces` under `ss10`; False for any other set.
    pub fn approves(&self, shaper: &mut Shaper, configs: &[String], text: &str) -> Result<bool> {
        if configs != [SS10] {
            return Ok(false);
        }
        let junior = self.junior_pieces(shaper, text)?;
        let after = self.comparator.ink_pieces(
            shaper,
            Side::After,
            text,
            &Features::for_config(Some(SS10)),
        )?;
        Ok(junior == after)
    }
}

/// What phase 1 reads from a unit's ink (`_ink_readings` and the flag lines of `_phase1_unit`): the diffs, one per configuration in the unit's order, the three flags, the `ink_deltas` mapping as ordered pairs, `repr(diffs)` and its sha1, the diffs digest.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UnitInk {
    pub diffs: Vec<Diff>,
    pub ink_identical: bool,
    pub picture_identical: bool,
    pub junior_equivalent: bool,
    pub ink_deltas: Vec<(String, String)>,
    pub diffs_repr: String,
    pub diffs_digest: String,
}

impl UnitInk {
    /// The readings as the `ink` stage records them.
    pub fn to_py(&self) -> PyValue {
        PyValue::Dict(
            vec![
                ("diffs", PyValue::str(&self.diffs_repr)),
                ("ink_identical", PyValue::Bool(self.ink_identical)),
                ("picture_identical", PyValue::Bool(self.picture_identical)),
                ("junior_equivalent", PyValue::Bool(self.junior_equivalent)),
                (
                    "ink_deltas",
                    PyValue::Tuple(
                        self.ink_deltas
                            .iter()
                            .map(|(config, digest)| {
                                PyValue::Tuple(vec![PyValue::str(config), PyValue::str(digest)])
                            })
                            .collect(),
                    ),
                ),
                ("diffs_digest", PyValue::str(&self.diffs_digest)),
            ]
            .into_iter()
            .map(|(name, value)| (PyValue::str(name), value))
            .collect(),
        )
    }
}

/// The worker's ink readers: the before-against-after comparator, the Junior oracle and the configuration fold, over the init's facts.
pub struct InkReader<'f> {
    comparator: InkComparator<'f>,
    oracle: JuniorOracle<'f>,
    fold: ConfigFold,
}

impl<'f> InkReader<'f> {
    /// The readers the init describes: the grid from its `PIXEL_SIZE`, the Junior tracking and the fold. An init whose `IDENTITY_DIFF` is not `((), (), 0)` is refused, since every reading tests for that sentinel.
    pub fn new(init: &Init, facts: &'f FontFacts) -> Result<Self> {
        let pixel_size = match init.constant(PIXEL_SIZE_NAME) {
            Some(PyValue::Int(size)) if *size > 0 => *size,
            other => {
                return Err(InkError(format!(
                    "the init's {PIXEL_SIZE_NAME} is {other:?}, not a positive int"
                )));
            }
        };
        let identity = Diff::identity().to_py();
        if init.constant(IDENTITY_DIFF_NAME) != Some(&identity) {
            return Err(InkError(format!(
                "the init's {IDENTITY_DIFF_NAME} is {:?}, and the engine reads the sentinel ((), (), 0)",
                init.constant(IDENTITY_DIFF_NAME)
            )));
        }
        Ok(Self {
            comparator: InkComparator::new(facts, pixel_size, Side::Before, Side::After),
            oracle: JuniorOracle::new(facts, pixel_size, init.junior_tracking),
            fold: ConfigFold::from_init(init)?,
        })
    }

    /// The before-against-after comparator.
    pub fn comparator(&self) -> &InkComparator<'f> {
        &self.comparator
    }

    /// The Junior oracle.
    pub fn oracle(&self) -> &JuniorOracle<'f> {
        &self.oracle
    }

    /// `build._ink_readings`: each configuration's diff, read once per distinct effective configuration (`ConfigFold.effective`) and given to every configuration that maps to it, and `ink_identical` over the distinct effective configurations.
    pub fn ink_readings(
        &self,
        shaper: &mut Shaper,
        text: &str,
        configs: &[String],
    ) -> Result<(Vec<Diff>, bool)> {
        let effective = self.fold.effective(configs, text);
        let mut compared: Vec<&str> = Vec::new();
        for config in &effective {
            if !compared.contains(&config.as_str()) {
                compared.push(config);
            }
        }
        let mut read: Vec<(&str, Diff)> = Vec::with_capacity(compared.len());
        for config in &compared {
            read.push((config, self.comparator.config_diff(shaper, text, config)?));
        }
        let diffs = effective
            .iter()
            .map(|config| {
                read.iter()
                    .find(|(held, _)| held == config)
                    .expect("every effective configuration was compared")
                    .1
                    .clone()
            })
            .collect();
        let compared: Vec<String> = compared.into_iter().map(str::to_string).collect();
        let identical = self.comparator.ink_identical(shaper, text, &compared)?;
        Ok((diffs, identical))
    }

    /// The ink lines of `_phase1_unit` for a text and its unit's configurations: `_ink_readings`, then `picture_identical` for a unit that is not ink-identical and whose every diff is `IDENTITY_DIFF`, `junior_equivalent` from the oracle for a unit that is neither, and each configuration's `delta_digest` whose diff is not `IDENTITY_DIFF`, in the unit's order.
    pub fn read(&self, shaper: &mut Shaper, text: &str, configs: &[String]) -> Result<UnitInk> {
        let (diffs, ink_identical) = self.ink_readings(shaper, text, configs)?;
        let picture_identical = !ink_identical && diffs.iter().all(Diff::is_identity);
        let junior_equivalent =
            !(ink_identical || picture_identical) && self.oracle.approves(shaper, configs, text)?;
        let mut ink_deltas: Vec<(String, String)> = Vec::new();
        for (config, diff) in configs.iter().zip(&diffs) {
            if diff.is_identity() {
                continue;
            }
            let digest = delta_digest(diff);
            match ink_deltas.iter_mut().find(|(held, _)| held == config) {
                Some(entry) => entry.1 = digest,
                None => ink_deltas.push((config.clone(), digest)),
            }
        }
        let diffs_repr = diffs_repr(&diffs);
        let diffs_digest = sha1_hex(diffs_repr.as_bytes());
        Ok(UnitInk {
            diffs,
            ink_identical,
            picture_identical,
            junior_equivalent,
            ink_deltas,
            diffs_repr,
            diffs_digest,
        })
    }

    /// [`InkReader::read`] for one unit record, its text from its code points; a failure names the unit.
    pub fn read_unit(&self, shaper: &mut Shaper, unit: &UnitRecord) -> Result<UnitInk> {
        let text: Option<String> = unit
            .codepoint_values
            .iter()
            .map(|&point| char::from_u32(point))
            .collect();
        text.ok_or_else(|| InkError("a code point is not a Unicode scalar value".to_string()))
            .and_then(|text| self.read(shaper, &text, &unit.configs))
            .map_err(|failure| unit_failure(unit, &failure))
    }
}

/// A failure while reading one unit's ink, naming the unit by its ordinal and code points.
pub fn unit_failure(unit: &UnitRecord, failure: &InkError) -> InkError {
    InkError(format!(
        "unit {} ({}): {failure}",
        unit.ordinal, unit.codepoints
    ))
}

// --- sha1 ----------------------------------------------------------------------------------------

/// The sha1 digest of `data` (FIPS 180-4), which `hashlib.sha1` gives the worker's digests.
pub fn sha1(data: &[u8]) -> [u8; 20] {
    let mut state: [u32; 5] = [
        0x6745_2301,
        0xEFCD_AB89,
        0x98BA_DCFE,
        0x1032_5476,
        0xC3D2_E1F0,
    ];
    let mut message = Vec::with_capacity(data.len() + 72);
    message.extend_from_slice(data);
    message.push(0x80);
    while message.len() % 64 != 56 {
        message.push(0);
    }
    message.extend_from_slice(&((data.len() as u64).wrapping_mul(8)).to_be_bytes());
    let mut schedule = [0u32; 80];
    for block in message.as_chunks::<64>().0 {
        for (word, bytes) in schedule.iter_mut().zip(block.as_chunks::<4>().0) {
            *word = u32::from_be_bytes(*bytes);
        }
        for index in 16..80 {
            schedule[index] = (schedule[index - 3]
                ^ schedule[index - 8]
                ^ schedule[index - 14]
                ^ schedule[index - 16])
                .rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = state;
        for (index, word) in schedule.iter().enumerate() {
            let (mix, constant) = match index {
                0..=19 => ((b & c) | (!b & d), 0x5A82_7999),
                20..=39 => (b ^ c ^ d, 0x6ED9_EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
                _ => (b ^ c ^ d, 0xCA62_C1D6),
            };
            let next = a
                .rotate_left(5)
                .wrapping_add(mix)
                .wrapping_add(e)
                .wrapping_add(constant)
                .wrapping_add(*word);
            (e, d, c, b, a) = (d, c, b.rotate_left(30), a, next);
        }
        for (held, add) in state.iter_mut().zip([a, b, c, d, e]) {
            *held = held.wrapping_add(add);
        }
    }
    let mut digest = [0u8; 20];
    for (bytes, word) in digest.as_chunks_mut::<4>().0.iter_mut().zip(state) {
        *bytes = word.to_be_bytes();
    }
    digest
}

/// `hashlib.sha1(data).hexdigest()`.
pub fn sha1_hex(data: &[u8]) -> String {
    sha1(data)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

// --- the ink stage ------------------------------------------------------------------------------

fn placed_value(facts: &FontFacts, pieces: &[Placed]) -> PyValue {
    PyValue::Tuple(
        pieces
            .iter()
            .map(|&(shape, x, y)| {
                PyValue::Tuple(vec![
                    PyValue::Bytes(facts.shape(shape).key.to_vec()),
                    PyValue::Int(x),
                    PyValue::Int(y),
                ])
            })
            .collect(),
    )
}

fn line_text(record: &Value, key: &str) -> Result<String> {
    match PyValue::from_typed(&record["text"])
        .map_err(|failure| InkError(format!("{key}: {failure}")))?
    {
        PyValue::Str(text) => Ok(text),
        other => Err(InkError(format!(
            "{key}: the text is {}, not a str",
            other.kind()
        ))),
    }
}

fn line_configs(record: &Value, key: &str) -> Result<Vec<String>> {
    let PyValue::Tuple(items) = PyValue::from_typed(&record["configs"])
        .map_err(|failure| InkError(format!("{key}: {failure}")))?
    else {
        return Err(InkError(format!(
            "{key}: the configurations are not a tuple"
        )));
    };
    items
        .into_iter()
        .map(|item| match item {
            PyValue::Str(config) => Ok(config),
            other => Err(InkError(format!(
                "{key}: a configuration is {}, not a str",
                other.kind()
            ))),
        })
        .collect()
}

/// The `ink` stage: for each `ink:` line of `stage_file`, the unit's readings for its `text` and `configs` ([`InkReader::read`]); for each `junior:` line, the oracle's tracking-free Junior pieces for its `text` and its answer for `ss10` alone; each as one `{"key", "value"}` line in the typed encoding, through one [`Shaper`].
pub fn stage_ink(init_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let init = Init::read(init_path)?;
    let facts = FontFacts::read(&init.font_facts)?;
    let fonts = Fonts::open(&init, &facts)?;
    let reader = InkReader::new(&init, &facts)?;
    let mut shaper = Shaper::new(&fonts, &facts);
    let file = std::fs::File::open(stage_file)
        .map_err(|failure| InkError(format!("cannot read {}: {failure}", stage_file.display())))?;
    let ss10 = [SS10.to_string()];
    for line in std::io::BufReader::new(file).lines() {
        let line = line.map_err(|failure| {
            InkError(format!("cannot read {}: {failure}", stage_file.display()))
        })?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| InkError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return Err(InkError(format!("a stage line has no key: {line:.200}")));
        };
        let text = line_text(&record, key)?;
        let value = if key.starts_with("ink:") {
            let configs = line_configs(&record, key)?;
            reader
                .read(&mut shaper, &text, &configs)
                .map_err(|failure| InkError(format!("{key}: {failure}")))?
                .to_py()
        } else if key.starts_with("junior:") {
            let oracle = reader.oracle();
            let pieces = oracle
                .junior_pieces(&mut shaper, &text)
                .map_err(|failure| InkError(format!("{key}: {failure}")))?;
            let approves = oracle
                .approves(&mut shaper, &ss10, &text)
                .map_err(|failure| InkError(format!("{key}: {failure}")))?;
            PyValue::Dict(vec![
                (PyValue::str("junior_pieces"), placed_value(&facts, &pieces)),
                (PyValue::str("approves"), PyValue::Bool(approves)),
            ])
        } else {
            return Err(InkError(format!(
                "{key}: the ink stage answers ink: and junior: lines"
            )));
        };
        writeln!(
            out,
            "{{\"key\":{},\"value\":{}}}",
            json_string(key),
            value.to_typed()
        )
        .map_err(|failure| InkError(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::error_frame;

    const DRAWN: &str = "00000000000000000000000000000000000000aa";
    const CURVED: &str = "00000000000000000000000000000000000000bb";

    fn facts() -> FontFacts {
        let text = format!(
            r#"{{"format":"ams-review-font-facts/1","fonts":{{"before":{{"glyphs":[["qsPea","{DRAWN}",0,0],["qsCurve","{CURVED}",0,0]]}},"after":{{"glyphs":[["qsPea","{DRAWN}",0,0]],"exits":[],"entries":[]}},"junior":{{"glyphs":[]}}}},"shapes":{{"{DRAWN}":[true,[[0,0],[0,1]]],"{CURVED}":[true,null]}}}}"#
        );
        FontFacts::parse(&text).expect("facts")
    }

    fn piece(facts: &FontFacts, side: Side, name: &str, x: i64, y: i64) -> Piece {
        let shape = facts
            .font(side)
            .shape_key(name)
            .and_then(|placement| placement.shape)
            .expect("a drawn glyph");
        Piece {
            shape,
            x,
            y,
            origin_x: 0,
        }
    }

    fn cells(points: &[Cell]) -> Vec<Cell> {
        points.to_vec()
    }

    #[test]
    fn sha1_gives_the_standard_digests() {
        assert_eq!(sha1_hex(b""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(sha1_hex(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(
            sha1_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "84983e441c3bd26ebaae4aa1f95129e5e54670f1"
        );
        assert_eq!(
            sha1_hex(&[b'a'; 1_000_000]),
            "34aa973cd4c4daa4f61eeb2bdbad27316534016f"
        );
    }

    #[test]
    fn the_identity_diff_has_python_s_repr_and_digest() {
        assert_eq!(diffs_repr(&[Diff::identity()]), "(((), (), 0),)");
        let diff = Diff {
            lost: vec![(0, 1)],
            gained: vec![],
            shift: -2,
        };
        assert_eq!(
            diffs_repr(&[diff.clone(), Diff::identity()]),
            "((((0, 1),), (), -2), ((), (), 0))"
        );
        let digest = sha1_hex(b"(((0, 1),), (), -2)");
        assert_eq!(delta_digest(&diff), format!("d-{}", &digest[..12]));
    }

    #[test]
    fn a_tail_that_only_slid_is_moved_back_before_the_subtraction() {
        let before = [cells(&[(0, 0), (1, 0)]), cells(&[(3, 0)])];
        let after = [cells(&[(0, 0)]), cells(&[(5, 0)])];
        assert_eq!(
            picture_diff(&before, &after),
            Diff {
                lost: vec![(0, 0)],
                gained: vec![],
                shift: 2,
            }
        );
        let same = [cells(&[(0, 0)]), cells(&[(2, 1)])];
        assert_eq!(
            picture_diff(&same, &[cells(&[(0, 0)]), cells(&[]), cells(&[(4, 1)])]),
            Diff {
                lost: vec![],
                gained: vec![],
                shift: 2,
            }
        );
        assert_eq!(picture_diff(&[], &[]), Diff::identity());
    }

    #[test]
    fn a_window_with_a_piece_that_has_no_picture_needs_the_outline_level_delta() {
        let facts = facts();
        let comparator = InkComparator::new(&facts, 50, Side::Before, Side::After);
        let pea = piece(&facts, Side::Before, "qsPea", 0, 0);
        let moved = piece(&facts, Side::After, "qsPea", 50, 0);
        assert_eq!(
            comparator.diff_of_runs(&[pea], &[pea]),
            Some(Diff::identity())
        );
        assert_eq!(
            comparator.diff_of_runs(&[pea], &[moved]),
            Some(Diff {
                lost: vec![],
                gained: vec![],
                shift: 1,
            })
        );
        let off_grid = Piece { x: 25, ..moved };
        assert_eq!(comparator.diff_of_runs(&[pea], &[off_grid]), None);
        let curve = piece(&facts, Side::Before, "qsCurve", 0, 0);
        assert_eq!(comparator.diff_of_runs(&[curve], &[pea]), None);
        let unit = UnitRecord {
            ordinal: 41,
            codepoints: "E650:E652".to_string(),
            codepoint_values: vec![0xE650, 0xE652],
            new: vec![],
            class: String::new(),
            configs: vec!["default".to_string()],
            kinds: vec![],
            group: String::new(),
            exemplar: false,
            unit_id: String::new(),
            input_key: String::new(),
            render_groups: vec![],
            no_verdict: false,
            config_classes: vec![],
            duplicate_group: None,
            cluster: None,
            ledger_class: String::new(),
        };
        let failure = unit_failure(
            &unit,
            &InkError("the window needs the outline-level delta under default".to_string()),
        );
        let frame = error_frame(&failure.0);
        assert!(
            frame.starts_with(r#"{"op":"error","message":"unit 41 (E650:E652): "#),
            "{frame}"
        );
        assert!(frame.contains("outline-level delta"), "{frame}");
    }
}

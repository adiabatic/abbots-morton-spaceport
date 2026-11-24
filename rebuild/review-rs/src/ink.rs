//! A unit's ink readings: the placed pieces of a run ([`InkComparator::named_run`], [`InkComparator::run_ink`], [`InkComparator::ink_pieces`], [`InkComparator::junior_pieces`]), its signature and the digest over the signature's marshal version 2 bytes ([`InkComparator::signature`], [`Signature::digest`]), its identity checks, and the configuration diff up to its picture path ([`InkComparator::config_diff`], [`picture_diff`]); the delta digest ([`delta_digest`]); the junior oracle's approval ([`JuniorOracle`]); and a unit's ink readings with the flags they set ([`InkReader`]).
//!
//! A piece is placed from the font facts, never from an outline: its shape is the glyph's interned shape and its position is the pen position plus the glyph's offsets and its shape's own-frame origin. A window with a piece that has no pixel picture takes the outline-level delta, which this engine does not build: such a window is refused with an error that says so, and the build fails.

use std::collections::BTreeSet;
use std::fmt;
use std::rc::Rc;

use sha2::{Digest as _, Sha256};

use crate::config_fold::ConfigFold;
use crate::fontfacts::{FontFacts, ShapeId, Side};
use crate::pyfmt::{DECIMAL_WIDTH, PyValue, decimal, push_lower_hex};
use crate::shape::{Features, ShapeError, ShapeResult, Shaper};
use crate::wire::{Init, WireError};

/// The vocabulary name of `classify.PIXEL_SIZE`, the grid a piece's picture lies on.
pub const PIXEL_SIZE_NAME: &str = "rebuild.validation.classify.PIXEL_SIZE";

/// The vocabulary name of `ink.IDENTITY_DIFF`, which must be the sentinel [`Diff::identity`] models.
pub const IDENTITY_DIFF_NAME: &str = "rebuild.review.ink.IDENTITY_DIFF";

/// The one configuration the junior oracle answers for ([`JuniorOracle`]).
pub const SS10: &str = "ss10";

/// The glyph name prefix `junior_pieces` removes the tracking after.
const LETTER_PREFIX: &str = "qs";

/// A window the engine cannot read.
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

/// Append `repr` of a tuple whose items `write_item` appends: `()` for none, `(a,)` for one, `", "` between items.
fn write_tuple_repr<T>(out: &mut String, items: &[T], mut write_item: impl FnMut(&mut String, &T)) {
    out.push('(');
    for (index, item) in items.iter().enumerate() {
        if index > 0 {
            out.push_str(", ");
        }
        write_item(out, item);
    }
    if items.len() == 1 {
        out.push(',');
    }
    out.push(')');
}

/// Append `repr` of the diff as Python holds it ([`Diff::to_py`]), the text `repr(&diff.to_py())` gives.
fn write_diff_repr(out: &mut String, diff: &Diff) {
    let mut digits = [0; DECIMAL_WIDTH];
    let mut cells = |out: &mut String, cells: &[Cell]| {
        write_tuple_repr(out, cells, |out, &(column, row)| {
            out.push('(');
            out.push_str(decimal(column, &mut digits));
            out.push_str(", ");
            out.push_str(decimal(row, &mut digits));
            out.push(')');
        });
    };
    out.push('(');
    cells(out, &diff.lost);
    out.push_str(", ");
    cells(out, &diff.gained);
    out.push_str(", ");
    out.push_str(decimal(diff.shift, &mut [0; DECIMAL_WIDTH]));
    out.push(')');
}

/// `repr(diffs)` for a unit's diffs, the tuple a unit's diffs digest hashes.
pub fn diffs_repr(diffs: &[Diff]) -> String {
    let mut out = String::new();
    write_tuple_repr(&mut out, diffs, write_diff_repr);
    out
}

/// `ink.delta_digest(diff)`: `d-` and the first twelve hex digits of the sha1 of the diff's repr.
pub fn delta_digest(diff: &Diff) -> String {
    let mut text = String::new();
    write_diff_repr(&mut text, diff);
    let mut digest = String::with_capacity(14);
    digest.push_str("d-");
    push_lower_hex(&mut digest, &sha1(text.as_bytes())[..6]);
    digest
}

/// One placed piece in run order ([`InkComparator::run_ink`]): its shape, its absolute x and y, and its shape's own-frame origin x.
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
        self.neutral_run(shaper, side, text, &features.kern_neutral())
    }

    /// `run_ink` under `neutral`, features that are already kern-neutral: `named_run`'s pieces, placed as it places them, without their glyphs.
    fn neutral_run(
        &self,
        shaper: &mut Shaper,
        side: Side,
        text: &str,
        neutral: &Features,
    ) -> Result<Vec<Piece>> {
        let result = shaper.shape(side, text, neutral)?;
        let glyphs = self.facts.font(side);
        let mut pieces = Vec::with_capacity(result.glyphs.len());
        let mut pen_x = 0;
        for (&glyph, &(x_offset, y_offset, x_advance)) in
            result.glyphs.iter().zip(&result.positions)
        {
            let placement = glyphs
                .glyph(glyph)
                .expect("a shaped glyph id is in the font's glyph order")
                .placement;
            if let Some(shape) = placement.shape {
                pieces.push(Piece {
                    shape,
                    x: pen_x + i64::from(x_offset) + placement.origin_x,
                    y: i64::from(y_offset) + placement.origin_y,
                    origin_x: placement.origin_x,
                });
            }
            pen_x += i64::from(x_advance);
        }
        Ok(pieces)
    }

    /// `signature`: both sides' `run_ink` under one configuration, the rendered outcome the ink-duplicate merge groups by.
    pub fn signature(&self, shaper: &mut Shaper, text: &str, config: &str) -> Result<Signature> {
        let features = Features::for_config(Some(config));
        Ok(Signature {
            before: self.run_ink(shaper, self.before, text, &features)?,
            after: self.run_ink(shaper, self.after, text, &features)?,
        })
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
        Ok(self.config_reading(shaper, text, config)?.0)
    }

    /// `config_diff`, and whether `pieces_identical` holds under the configuration, which is whether both sides' sorted placed pieces are equal, the test `config_diff` makes first.
    fn config_reading(
        &self,
        shaper: &mut Shaper,
        text: &str,
        config: &str,
    ) -> Result<(Diff, bool)> {
        let neutral = Features::for_config(Some(config)).kern_neutral();
        let before = self.neutral_run(shaper, self.before, text, &neutral)?;
        let after = self.neutral_run(shaper, self.after, text, &neutral)?;
        self.reading_of_runs(&before, &after).ok_or_else(|| {
            InkError(format!(
                "the window {} needs the outline-level delta under {config}, since a piece it places has no pixel picture (`InkComparator._piece_diff`); only the Python engine builds it",
                crate::pyfmt::repr::repr_str(text)
            ))
        })
    }

    /// `config_diff` over two shaped runs: `IDENTITY_DIFF` when their sorted placed pieces are equal, the picture delta when every piece has a picture, and None otherwise.
    pub fn diff_of_runs(&self, before: &[Piece], after: &[Piece]) -> Option<Diff> {
        self.reading_of_runs(before, after).map(|(diff, _)| diff)
    }

    /// [`InkComparator::diff_of_runs`], and whether the sorted placed pieces are equal.
    fn reading_of_runs(&self, before: &[Piece], after: &[Piece]) -> Option<(Diff, bool)> {
        if sorted_placed(before) == sorted_placed(after) {
            return Some((Diff::identity(), true));
        }
        let before = self.run_pictures(before)?;
        let after = self.run_pictures(after)?;
        Some((picture_diff(&before, &after), false))
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

/// One `InkComparator.signature`: the before and after sides' placed pieces in run order.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Signature {
    pub before: Vec<Piece>,
    pub after: Vec<Piece>,
}

impl Signature {
    /// `marshal.dumps(signature, 2)`: version 2 has no back references, so the bytes are a function of the value alone.
    pub fn marshal(&self, facts: &FontFacts) -> Vec<u8> {
        let mut out = Vec::new();
        marshal_tuple(&mut out, 2);
        for pieces in [&self.before, &self.after] {
            marshal_tuple(&mut out, pieces.len());
            for piece in pieces {
                marshal_tuple(&mut out, 4);
                marshal_bytes(&mut out, &facts.shape(piece.shape).key);
                for value in [piece.x, piece.y, piece.origin_x] {
                    marshal_int(&mut out, value);
                }
            }
        }
        out
    }

    /// The sha256 of the signature's marshal version 2 bytes.
    pub fn digest(&self, facts: &FontFacts) -> [u8; 32] {
        Sha256::digest(self.marshal(facts)).into()
    }
}

/// A marshal tuple's head: `(` and its length as a little-endian 4-byte int.
fn marshal_tuple(out: &mut Vec<u8>, len: usize) {
    out.push(b'(');
    out.extend_from_slice(&marshal_length(len).to_le_bytes());
}

/// A marshal `bytes`: `s`, its length as a little-endian 4-byte int, and the bytes.
fn marshal_bytes(out: &mut Vec<u8>, bytes: &[u8]) {
    out.push(b's');
    out.extend_from_slice(&marshal_length(bytes.len()).to_le_bytes());
    out.extend_from_slice(bytes);
}

fn marshal_length(len: usize) -> u32 {
    u32::try_from(len).expect("a marshalled length fits in 32 bits")
}

/// A marshal `int`: `i` and a little-endian 4-byte int for a value within 32 bits, otherwise `l`, the count of its 15-bit digits signed as the value is, and the digits of its magnitude, least significant first, each a little-endian 2-byte int.
fn marshal_int(out: &mut Vec<u8>, value: i64) {
    if let Ok(small) = i32::try_from(value) {
        out.push(b'i');
        out.extend_from_slice(&small.to_le_bytes());
        return;
    }
    let mut magnitude = value.unsigned_abs();
    let mut digits = Vec::new();
    while magnitude != 0 {
        digits.push((magnitude & 0x7fff) as u16);
        magnitude >>= 15;
    }
    let count = i32::try_from(digits.len()).expect("an i64 has at most five 15-bit digits");
    out.push(b'l');
    out.extend_from_slice(&(if value < 0 { -count } else { count }).to_le_bytes());
    for digit in digits {
        out.extend_from_slice(&digit.to_le_bytes());
    }
}

/// The Junior font against the after font, and the Junior letter tracking Python derives (`junior_tracking`).
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

/// What phase 1 reads from a unit's ink: the diffs, one per configuration in the unit's order, the three flags, the `ink_deltas` mapping as ordered pairs, `repr(diffs)` and its sha1, the diffs digest.
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

/// The grid a piece's picture lies on, the init's `PIXEL_SIZE`, which must be a positive int.
pub fn pixel_size(init: &Init) -> Result<i64> {
    match init.constant(PIXEL_SIZE_NAME) {
        Some(PyValue::Int(size)) if *size > 0 => Ok(*size),
        other => Err(InkError(format!(
            "the init's {PIXEL_SIZE_NAME} is {other:?}, not a positive int"
        ))),
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
        let pixel_size = pixel_size(init)?;
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

    /// Each configuration's diff, read once per distinct effective configuration (`ConfigFold.effective`) and given to every configuration that maps to it, and `ink_identical` over the distinct effective configurations. `ink_identical` shapes the runs each diff was read from again, so it is answered from the diffs' readings: whether each configuration's sorted placed pieces were equal.
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
        let mut identical = true;
        for config in &compared {
            let (diff, same) = self.comparator.config_reading(shaper, text, config)?;
            identical &= same;
            read.push((config, diff));
        }
        let mut diffs = Vec::with_capacity(effective.len());
        for (position, config) in effective.iter().enumerate() {
            let at = read
                .iter()
                .position(|(held, _)| held == config)
                .expect("every effective configuration was compared");
            let last_use = !effective[position + 1..].contains(config);
            diffs.push(if last_use {
                std::mem::replace(&mut read[at].1, Diff::identity())
            } else {
                read[at].1.clone()
            });
        }
        Ok((diffs, identical))
    }

    /// A unit's ink readings for a text and its configurations: each configuration's diff and whether the unit is ink-identical, then `picture_identical` for a unit that is not ink-identical and whose every diff is `IDENTITY_DIFF`, `junior_equivalent` from the oracle for a unit that is neither, and each configuration's `delta_digest` whose diff is not `IDENTITY_DIFF`, in the unit's order.
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
    let (blocks, rest) = data.as_chunks::<64>();
    let mut tail = [0u8; 128];
    tail[..rest.len()].copy_from_slice(rest);
    tail[rest.len()] = 0x80;
    let tail_length = if rest.len() < 56 { 64 } else { 128 };
    tail[tail_length - 8..tail_length]
        .copy_from_slice(&((data.len() as u64).wrapping_mul(8)).to_be_bytes());
    let mut schedule = [0u32; 80];
    for block in blocks.iter().chain(tail[..tail_length].as_chunks::<64>().0) {
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
    let mut out = String::with_capacity(40);
    push_lower_hex(&mut out, &sha1(data));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn the_written_reprs_and_digests_are_the_repr_of_the_python_values() {
        use crate::pyfmt::repr::repr;
        let diffs = [
            Diff::identity(),
            Diff {
                lost: vec![(0, 1)],
                gained: vec![(-3, 12), (i64::MAX, i64::MIN)],
                shift: -2,
            },
            Diff {
                lost: vec![(5, -5), (6, 0), (7, 1)],
                gained: vec![],
                shift: 17,
            },
        ];
        for count in 0..=diffs.len() {
            let tuple = PyValue::Tuple(diffs[..count].iter().map(Diff::to_py).collect());
            assert_eq!(diffs_repr(&diffs[..count]), repr(&tuple).expect("repr"));
        }
        for diff in &diffs {
            let text = repr(&diff.to_py()).expect("repr");
            assert_eq!(
                delta_digest(diff),
                format!("d-{}", &sha1_hex(text.as_bytes())[..12])
            );
        }
    }

    #[test]
    fn sha1_pads_every_tail_length_as_the_padded_message_hashes() {
        let data: Vec<u8> = (0..300u32).map(|value| (value * 31 % 251) as u8).collect();
        for length in 0..data.len() {
            assert_eq!(
                sha1(&data[..length]),
                reference_sha1(&data[..length]),
                "{length}"
            );
        }
    }

    /// The sha1 digest over the whole padded message built in memory.
    fn reference_sha1(data: &[u8]) -> [u8; 20] {
        let mut state: [u32; 5] = [
            0x6745_2301,
            0xEFCD_AB89,
            0x98BA_DCFE,
            0x1032_5476,
            0xC3D2_E1F0,
        ];
        let mut message = data.to_vec();
        message.push(0x80);
        while message.len() % 64 != 56 {
            message.push(0);
        }
        message.extend_from_slice(&((data.len() as u64) * 8).to_be_bytes());
        for block in message.chunks(64) {
            let mut schedule = [0u32; 80];
            for (index, bytes) in block.chunks(4).enumerate() {
                schedule[index] = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
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
        for (index, word) in state.iter().enumerate() {
            digest[index * 4..index * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        digest
    }

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|byte| format!("{byte:02x}")).collect()
    }

    #[test]
    fn ints_marshal_as_python_s_marshal_writes_them_at_version_2() {
        let mut out = Vec::new();
        marshal_tuple(&mut out, 7);
        for value in [
            i64::MIN,
            i64::MAX,
            -(1 << 31),
            (1 << 31) - 1,
            1 << 31,
            -1,
            0,
        ] {
            marshal_int(&mut out, value);
        }
        assert_eq!(
            hex(&out),
            "28070000006cfbffffff000000000000000008006c05000000ff7fff7fff7fff7f0700690000008069ffffff7f6c0300000000000000020069ffffffff6900000000"
        );
    }

    #[test]
    fn a_signature_digests_as_python_s_signature_digest() {
        let facts = facts();
        let pea = |x, y, origin_x| Piece {
            origin_x,
            ..piece(&facts, Side::Before, "qsPea", x, y)
        };
        let signature = Signature {
            before: vec![pea(0, 0, 0), pea(50, -150, 0)],
            after: vec![pea(1 << 31, -(1 << 31) - 1, 1 << 45)],
        };
        assert_eq!(
            hex(&signature.digest(&facts)),
            "3039f162251b35b14e5f1815c8ddb6f9b5fc9ecb79b43e7ffab367843005d414"
        );
        let empty = Signature {
            before: vec![],
            after: vec![],
        };
        assert_eq!(
            hex(&empty.marshal(&facts)),
            "280200000028000000002800000000"
        );
        assert_eq!(
            hex(&empty.digest(&facts)),
            "b2ba84b7eac0b43c2a5e51dc7fdb370e0b8645eeaa7df483afe4de4632dad394"
        );
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
    }
}

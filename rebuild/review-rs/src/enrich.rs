//! An enriched unit.
//!
//! Its geometry: the isolated overlay, the formed spans, the after cells, the junction and extension tuples, the baseline subset row and its junction assertion, the divergent positions against the alias map, the ink-visible positions, the judged pair and the secondary pairs, the position-only mark, the highlight rects and the boundary marks.
//!
//! Its prose: the notation, the provenance, the filtered explain text, the summary, the kept trace, the two mismatch lines, and the primary-unit projection.
//!
//! A piece is placed from the font facts, as [`crate::ink`] places one: its shape is the glyph's interned shape, so two pieces compare equal across fonts exactly when their shape keys do.

use std::borrow::Cow;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fmt;

use ams_m1_kernel::index::SpecIndex;
use ams_m1_kernel::model::Sym;
use ams_m1_kernel::types::{
    AdjustmentToken, CellId, Settled, adjustment_text, is_boundary_settled, provenance_pointer,
};

use crate::explain::{ExplainError, filter_explain, python_int, render};
use crate::fontfacts::{FontFacts, ShapeId, Side};
use crate::ink::InkError;
use crate::pyfmt::repr::repr;
use crate::pyfmt::{PyFmtError, PyValue};
use crate::settle::{Report, SettleError, Settlement, Trace, features_for_config};
use crate::shape::{Features, ShapeError, ShapeResult, Shaper};
use crate::subset_pack::{SubsetPack, SubsetPackError, SubsetRow};
use crate::wire::{Alias, Init, UnitRecord, WireError};

/// The vocabulary name of `labels.BOUNDARY_GLYPH_NAMES`, the old glyph names the divergent positions never compare with a cell.
pub const BOUNDARY_GLYPH_NAMES_NAME: &str = "rebuild.pipeline.labels.BOUNDARY_GLYPH_NAMES";

/// The vocabulary name of `enrich.LETTERS`, each letter's code point and family, which the notation reads.
pub const LETTERS_NAME: &str = "rebuild.review.enrich.LETTERS";

/// Each boundary code point and the kind its boundary mark names.
pub const BOUNDARIES: [(u32, &str); 3] =
    [(0x0020, "space"), (0x00B7, "namer-dot"), (0x200C, "zwnj")];

/// `enrich._SPECIAL_DISPLAY`: the families whose display is not `·` and the name after `qs`.
const SPECIAL_DISPLAY: [(&str, &str); 2] = [("qsIng", "·-ing"), ("qsJai", "·J’ai")];

/// `enrich._BOUNDARY_NOTATION`: each boundary code point's token in the notation.
const BOUNDARY_NOTATION: [(u32, &str); 3] = [(0x0020, "␣"), (0x00B7, "·"), (0x200C, "◊ZWNJ")];

/// Each deciding stage's phrase in the summary.
const STAGE_PHRASES: [(&str, &str); 6] = [
    ("only-candidate", "the only surviving candidate"),
    ("absolute-prefer", "an absolute prefer"),
    ("join-count", "join-count rank"),
    ("yielding-prefer", "a yielding prefer"),
    ("order", "declaration order"),
    ("tiebreak", "the final tiebreak"),
];

/// The junction heights the summary names in words.
const HEIGHT_PHRASES: [(i64, &str); 3] = [
    (0, "at the baseline"),
    (5, "at the x-height"),
    (8, "at the top"),
];

/// Each boundary rune's name in the summary.
const BOUNDARY_SUMMARY_NAMES: [(&str, &str); 3] = [
    ("space", "the space"),
    ("zwnj", "◊ZWNJ"),
    ("namer-dot", "the namer dot"),
];

/// The junction token of a break, and of a ligature's inner gap in the subset rows.
const BREAK: &str = "break";
const LIGATURE: &str = "lig";

/// A unit the engine cannot enrich.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EnrichError(pub String);

impl fmt::Display for EnrichError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for EnrichError {}

macro_rules! from_error {
    ($($kind:ty),*) => {
        $(impl From<$kind> for EnrichError {
            fn from(error: $kind) -> Self {
                Self(error.0)
            }
        })*
    };
}

from_error!(
    ShapeError,
    SettleError,
    SubsetPackError,
    InkError,
    WireError,
    ExplainError,
    PyFmtError
);

type Result<T> = std::result::Result<T, EnrichError>;

fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(EnrichError(message.into()))
}

/// A half-open code point span, `(start, end)`.
pub type Span = (usize, usize);

/// A judged or secondary pair, `(left, right)` after-cell indices.
pub type Pair = (usize, usize);

/// One highlight rect in font units.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Highlight {
    pub x_min: i64,
    pub x_max: i64,
    pub advance_total: i64,
}

impl Highlight {
    /// The rect as a fragment holds it, a dict in its key order.
    pub fn to_py(&self) -> PyValue {
        PyValue::Dict(vec![
            (PyValue::str("x_min"), PyValue::Int(self.x_min)),
            (PyValue::str("x_max"), PyValue::Int(self.x_max)),
            (
                PyValue::str("advance_total"),
                PyValue::Int(self.advance_total),
            ),
        ])
    }
}

/// One `SecondaryJunction` as `enrich` makes it, before primary units are resolved.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SecondaryJunction {
    pub pair: Pair,
    pub highlight_before: Highlight,
    pub highlight_after: Highlight,
}

/// One boundary mark: the after cell's index, its boundary kind, and the pen x of the shaped glyph covering it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct BoundaryMark {
    pub index: usize,
    pub kind: &'static str,
    pub x: i64,
}

impl BoundaryMark {
    /// The mark as Python holds it, a dict in the literal's key order.
    pub fn to_py(&self) -> PyValue {
        PyValue::Dict(vec![
            (PyValue::str("index"), int(self.index)),
            (PyValue::str("kind"), PyValue::str(self.kind)),
            (PyValue::str("x"), PyValue::Int(self.x)),
        ])
    }
}

/// The geometry the enricher computes for one unit before its summary: the geometry fields of its enriched unit, and the ink-visible divergent positions the summary reads.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Geometry {
    pub before_glyphs: Vec<String>,
    pub before_junctions: Vec<String>,
    pub after_cells: Vec<String>,
    pub after_junctions: Vec<String>,
    pub after_extensions: Vec<i64>,
    pub diff_positions: Vec<usize>,
    pub ink_positions: Vec<usize>,
    pub pair: Option<Pair>,
    pub pair_codepoints: Option<Pair>,
    pub highlight_before: Highlight,
    pub highlight_after: Highlight,
    pub boundary_marks: Vec<BoundaryMark>,
    pub after_spans: Vec<Span>,
    pub before_spans: Vec<Span>,
    pub secondary_junctions: Vec<SecondaryJunction>,
}

fn int(value: usize) -> PyValue {
    PyValue::Int(i64::try_from(value).expect("an index fits an i64"))
}

/// The cell's rune, stance, entry and exit, `None` for an absent height as Python's f-string writes it, and its adjustments joined with `+`.
pub fn cell_token(index: &SpecIndex, cell: &CellId) -> String {
    let height = |height: Option<Sym>| height.map_or("None", |height| index.resolve(height));
    let adjustments: Vec<String> = cell
        .adjustments
        .iter()
        .map(|token| adjustment_text(index, *token))
        .collect();
    format!(
        "{}/{}/{}/{}/{}",
        index.resolve(cell.rune),
        index.resolve(cell.stance),
        height(cell.entry),
        height(cell.exit),
        adjustments.join("+")
    )
}

/// The entry extensions less the entry contractions among the adjustments, each read as `int` reads the text after its last `-`.
pub fn parse_entry_extension(index: &SpecIndex, adjustments: &[AdjustmentToken]) -> Result<i64> {
    let mut total = 0;
    for token in adjustments {
        let text = adjustment_text(index, *token);
        let sign = if text.starts_with("en-ext-") {
            1
        } else if text.starts_with("en-con-") {
            -1
        } else {
            continue;
        };
        let amount = text
            .rsplit_once('-')
            .map_or(text.as_str(), |(_, amount)| amount);
        let Some(amount) = python_int(amount) else {
            return error(format!(
                "the adjustment {text:?} ends in no int Python reads"
            ));
        };
        total += sign * amount;
    }
    Ok(total)
}

/// Each code point as a numeric character reference, `&#x`, its uppercase hex at least four digits wide and `;`, as `format!("&#x{value:04X};")` writes it, one after another.
pub fn text_entities(values: &[u32]) -> String {
    const UPPER: &[u8; 16] = b"0123456789ABCDEF";
    let mut out = String::with_capacity(values.len() * 9);
    for &value in values {
        out.push_str("&#x");
        let digits = ((32 - value.leading_zeros()).div_ceil(4) as usize).max(4);
        for shift in (0..digits).rev() {
            out.push(char::from(UPPER[((value >> (shift * 4)) & 0xf) as usize]));
        }
        out.push(';');
    }
    out
}

/// The pen x before each glyph and after the last.
pub fn pen_positions(positions: &[(i32, i32, i32)]) -> Vec<i64> {
    let mut pens = Vec::with_capacity(positions.len() + 1);
    pens.push(0);
    let mut pen = 0;
    for &(_x, _y, advance) in positions {
        pen += i64::from(advance);
        pens.push(pen);
    }
    pens
}

/// Each cluster's code point span, from its start to the next cluster's or the text's end, at least one code point wide.
pub fn spans_from_clusters(clusters: &[usize], length: usize) -> Vec<Span> {
    clusters
        .iter()
        .enumerate()
        .map(|(index, &start)| {
            let end = clusters.get(index + 1).copied().unwrap_or(length);
            (start, end.max(start + 1))
        })
        .collect()
}

/// The first span holding the position, or the last span when none does. The spans are never empty here (the caller refuses a window with none).
pub fn covering(spans: &[Span], position: usize) -> usize {
    spans
        .iter()
        .position(|&(start, end)| start <= position && position < end)
        .unwrap_or(spans.len() - 1)
}

/// The pen x range of the spans covering `cp_start` through `cp_end`, and the run's whole advance; pens that decrease are refused.
pub fn highlight(
    pens: &[i64],
    spans: &[Span],
    cp_start: usize,
    cp_end: usize,
) -> Result<Highlight> {
    if pens.windows(2).any(|pair| pair[1] < pair[0]) {
        return error(format!(
            "a highlight rect wants non-decreasing pen positions, got {pens:?}"
        ));
    }
    let first = covering(spans, cp_start);
    let last = covering(spans, cp_end);
    match (pens.get(first), pens.get(last + 1), pens.last()) {
        (Some(&x_min), Some(&x_max), Some(&advance_total)) => Ok(Highlight {
            x_min,
            x_max,
            advance_total,
        }),
        _ => error(format!(
            "a highlight rect reads past the {} pen positions",
            pens.len()
        )),
    }
}

/// The first cell whose kern-neutral advance differs between the two runs, or None.
pub fn advance_mismatch_cell(
    before_pens: &[i64],
    after_pens: &[i64],
    cell_count: usize,
) -> Option<usize> {
    let limit = cell_count
        .min(before_pens.len().saturating_sub(1))
        .min(after_pens.len().saturating_sub(1));
    (0..limit).find(|&index| {
        before_pens[index + 1] - before_pens[index] != after_pens[index + 1] - after_pens[index]
    })
}

/// Whether the cell at `position` joins the cell after it, or (with no cell after) does not join the one before, which is when the judged pair and the secondary pairs pair a lone position rightward.
fn pairs_rightward(position: usize, after_junctions: &[String], cell_count: usize) -> (bool, bool) {
    let joins_right = position + 1 < cell_count && after_junctions[position] != BREAK;
    let joins_left = position > 0 && after_junctions[position - 1] != BREAK;
    (
        joins_right || (position + 1 < cell_count && !joins_left),
        position > 0,
    )
}

/// The first divergent gap, else the first two adjacent anchor positions, else a lone anchor position paired with the neighbor it joins toward.
pub fn pick_pair(
    divergent_gaps: &[Pair],
    anchor_positions: &[usize],
    after_junctions: &[String],
    cell_count: usize,
) -> Option<Pair> {
    if let Some(&gap) = divergent_gaps.first() {
        return Some(gap);
    }
    if anchor_positions.is_empty() || cell_count < 2 {
        return None;
    }
    if let Some(window) = anchor_positions
        .windows(2)
        .find(|window| window[1] == window[0] + 1)
    {
        return Some((window[0], window[1]));
    }
    let position = anchor_positions[0];
    if pairs_rightward(position, after_junctions, cell_count).0 {
        return Some((position, position + 1));
    }
    Some((position - 1, position))
}

/// Every divergent adjacency beyond the primary pair in left-index order, the remaining divergent gaps and a neighbor junction for each anchor position the primary and the gaps do not cover.
pub fn secondary_pairs(
    primary: Pair,
    divergent_gaps: &[Pair],
    anchor_positions: &[usize],
    after_junctions: &[String],
    cell_count: usize,
) -> Vec<Pair> {
    let mut pairs: Vec<Pair> = Vec::new();
    let mut add = |candidate: Pair| {
        if candidate != primary && !pairs.contains(&candidate) {
            pairs.push(candidate);
        }
    };
    for &gap in divergent_gaps {
        add(gap);
    }
    let mut covered: HashSet<usize> = HashSet::from([primary.0, primary.1]);
    for &(left, right) in divergent_gaps {
        covered.extend([left, right]);
    }
    let remaining: Vec<usize> = anchor_positions
        .iter()
        .copied()
        .filter(|position| !covered.contains(position))
        .collect();
    let mut index = 0;
    while index < remaining.len() {
        let position = remaining[index];
        if remaining.get(index + 1) == Some(&(position + 1)) {
            add((position, position + 1));
            index += 2;
            continue;
        }
        match pairs_rightward(position, after_junctions, cell_count) {
            (true, _) => add((position, position + 1)),
            (false, true) => add((position - 1, position)),
            (false, false) => {}
        }
        index += 1;
    }
    pairs.sort_unstable();
    pairs
}

fn boundary_kind(codepoint: u32) -> Option<&'static str> {
    BOUNDARIES
        .iter()
        .find(|(boundary, _)| *boundary == codepoint)
        .map(|(_, kind)| *kind)
}

/// A placed piece as the segment's pieces are sorted and compared: `(shape, x, y)`.
type SegmentPiece = (ShapeId, i64, i64);

/// The enricher's shared readers: the settlement, the mapped subset pack, the font facts, the alias map, the boundary glyph names and the letters.
pub struct Enricher<'a> {
    settlement: &'a Settlement,
    pack: &'a SubsetPack,
    facts: &'a FontFacts,
    aliases: HashMap<&'a str, &'a Alias>,
    boundary_glyph_names: HashSet<String>,
    /// Each letter's code point and its display token ([`letter_display`] of its family).
    letters: HashMap<u32, String>,
}

/// The init's `LETTERS`, which must be a dict of code points to `str`.
pub(crate) fn init_letters(init: &Init) -> Result<HashMap<u32, String>> {
    let Some(PyValue::Dict(entries)) = init.constant(LETTERS_NAME) else {
        return error(format!("the init's {LETTERS_NAME} is not a dict"));
    };
    entries
        .iter()
        .map(|entry| match entry {
            (PyValue::Int(value), PyValue::Str(family)) => u32::try_from(*value)
                .map(|value| (value, family.clone()))
                .map_err(|_| EnrichError(format!("the init's {LETTERS_NAME} holds {value}"))),
            _ => error(format!(
                "the init's {LETTERS_NAME} holds an entry that is not an int and a str"
            )),
        })
        .collect()
}

impl<'a> Enricher<'a> {
    /// The enricher over the init's aliases, later entries winning as in Python's dict, its `BOUNDARY_GLYPH_NAMES`, which must be a set of `str`, and its `LETTERS`.
    pub fn new(
        init: &'a Init,
        settlement: &'a Settlement,
        pack: &'a SubsetPack,
        facts: &'a FontFacts,
    ) -> Result<Self> {
        let boundary_glyph_names = match init.constant(BOUNDARY_GLYPH_NAMES_NAME) {
            Some(PyValue::Set(items)) => items
                .iter()
                .map(|item| match item {
                    PyValue::Str(name) => Ok(name.clone()),
                    other => error(format!(
                        "the init's {BOUNDARY_GLYPH_NAMES_NAME} holds {}, not a str",
                        other.kind()
                    )),
                })
                .collect::<Result<HashSet<String>>>()?,
            other => {
                return error(format!(
                    "the init's {BOUNDARY_GLYPH_NAMES_NAME} is {other:?}, not a set"
                ));
            }
        };
        Ok(Self {
            settlement,
            pack,
            facts,
            aliases: init
                .aliases
                .iter()
                .map(|(name, alias)| (name.as_str(), alias))
                .collect(),
            boundary_glyph_names,
            letters: init_letters(init)?
                .into_iter()
                .map(|(value, family)| (value, letter_display(&family)))
                .collect(),
        })
    }

    fn index(&self) -> &SpecIndex {
        self.settlement.index()
    }

    /// `break` under the overlay or for no junction, else `y` and the height's y.
    fn junction_token(&self, junction: Option<Sym>, overlay: bool) -> Result<String> {
        match junction {
            Some(height) if !overlay => match self.index().y_of(height) {
                Some(y) => Ok(format!("y{y}")),
                None => error(format!(
                    "the registry declares no height {}",
                    self.index().resolve(height)
                )),
            },
            _ => Ok(BREAK.to_owned()),
        }
    }

    /// Whether the old glyph's alias is the cell: a `CellId` alias equal to it field by field, never a word or a missing alias.
    fn alias_is(&self, old_name: &str, cell: &CellId) -> bool {
        let index = self.index();
        let height = |height: Option<Sym>| height.map(|height| index.resolve(height));
        match self.aliases.get(old_name) {
            Some(Alias::Cell {
                rune,
                stance,
                entry,
                exit,
                adjustments,
            }) => {
                rune == index.resolve(cell.rune)
                    && stance == index.resolve(cell.stance)
                    && entry.as_deref() == height(cell.entry)
                    && exit.as_deref() == height(cell.exit)
                    && adjustments.len() == cell.adjustments.len()
                    && adjustments
                        .iter()
                        .zip(&cell.adjustments)
                        .all(|(text, token)| *text == adjustment_text(index, *token))
            }
            _ => false,
        }
    }

    /// The divergent code point positions (a covering-structure or alias-against-cell mismatch, and both sides of a divergent gap inside a ligature) and the divergent inter-cell gaps as after-cell pairs.
    fn diff_codepoints(
        &self,
        length: usize,
        row: &SubsetRow<'_>,
        before_spans: &[Span],
        settled: &[&Settled],
        after_spans: &[Span],
    ) -> Result<(BTreeSet<usize>, Vec<Pair>)> {
        let mut diff = BTreeSet::new();
        for position in 0..length {
            let before_index = covering(before_spans, position);
            let after_index = covering(after_spans, position);
            if before_spans[before_index] != after_spans[after_index] {
                diff.insert(position);
                continue;
            }
            let old_name = row.glyphs[before_index];
            if self.boundary_glyph_names.contains(old_name) {
                continue;
            }
            if !self.alias_is(old_name, &settled[after_index].cell) {
                diff.insert(position);
            }
        }
        let mut gaps = Vec::new();
        for gap in 0..length.saturating_sub(1) {
            let left_after = covering(after_spans, gap);
            let right_after = covering(after_spans, gap + 1);
            let after_junction = if left_after == right_after {
                LIGATURE.to_owned()
            } else {
                self.junction_token(settled[left_after].junction, false)?
            };
            let Some(&before_junction) = row.junctions.get(gap) else {
                return error(format!(
                    "the subset row has {} junctions and the window {length} code points",
                    row.junctions.len()
                ));
            };
            if before_junction != after_junction {
                if left_after != right_after {
                    gaps.push((left_after, right_after));
                } else {
                    diff.extend([gap, gap + 1]);
                }
            }
        }
        Ok((diff, gaps))
    }

    /// The placed ink of one run's glyphs covering code points `[cp_start, cp_end)`, translated so the segment's leftmost drawn ink is at x=0, a shape with no point at (0, 0), sorted; empty when nothing drawn is placed.
    fn segment_pieces(
        &self,
        side: Side,
        shaped: &ShapeResult,
        pens: &[i64],
        spans: &[Span],
        cp_start: usize,
        cp_end: usize,
    ) -> Vec<SegmentPiece> {
        let glyphs = self.facts.font(side);
        let mut placed: Vec<SegmentPiece> = Vec::new();
        for (index, &(start, end)) in spans.iter().enumerate() {
            if start < cp_end && cp_start < end {
                let (x_offset, y_offset, _advance) = shaped.positions[index];
                let placement = glyphs
                    .glyph(shaped.glyphs[index])
                    .expect("a shaped glyph id is in the font's glyph order")
                    .placement;
                if let Some(shape) = placement.shape {
                    placed.push((
                        shape,
                        pens[index] + i64::from(x_offset) + placement.origin_x,
                        i64::from(y_offset) + placement.origin_y,
                    ));
                }
            }
        }
        let Some(x0) = placed
            .iter()
            .filter(|(shape, _, _)| self.facts.draws(*shape))
            .map(|&(_, x, _)| x)
            .min()
        else {
            return Vec::new();
        };
        let mut pieces: Vec<SegmentPiece> = placed
            .into_iter()
            .map(|(shape, x, y)| {
                if self.facts.draws(shape) {
                    (shape, x - x0, y)
                } else {
                    (shape, 0, 0)
                }
            })
            .collect();
        pieces.sort_unstable();
        pieces
    }

    /// The geometry of one unit under its first configuration `config`, from its settled report, its flags, its baseline subset row and the shaper's kern-neutral runs of both fonts. The two mismatch lines `enrich` adds to the enricher's list go onto `mismatches`, in its order.
    #[allow(clippy::too_many_arguments)]
    fn geometry(
        &self,
        shaper: &mut Shaper,
        unit: &UnitRecord,
        config: &str,
        flags: &Flags,
        report: &Report,
        row: &SubsetRow<'_>,
        mismatches: &mut Vec<String>,
    ) -> Result<Geometry> {
        let index = self.index();
        let values = &unit.codepoint_values;
        let length = values.len();
        let overlay = self.settlement.overlay_active(&features_for_config(config));
        let settled: Vec<&Settled> = report.traces.iter().map(|trace| &trace.settled).collect();
        let cell_count = settled.len();
        let after_cells: Vec<String> = settled
            .iter()
            .map(|settled| cell_token(index, &settled.cell))
            .collect();
        if unit.new.iter().all(|token| token.contains('/')) && after_cells != unit.new {
            mismatches.push(format!(
                "{config} {}: derived cells {} != audit {}",
                unit.codepoints,
                repr(&PyValue::str_tuple(&after_cells))?,
                repr(&PyValue::str_tuple(&unit.new))?
            ));
        }

        let after_spans: Vec<Span> = if overlay {
            (0..length)
                .map(|position| (position, position + 1))
                .collect()
        } else {
            self.settlement.formed_spans(values)?
        };
        if after_spans.is_empty() || after_spans.len() != cell_count {
            return error(format!(
                "the window has {} formed spans and {cell_count} settled positions",
                after_spans.len()
            ));
        }
        let mut after_junctions = Vec::with_capacity(cell_count.saturating_sub(1));
        let mut after_extensions = Vec::with_capacity(cell_count.saturating_sub(1));
        for position in 0..cell_count.saturating_sub(1) {
            after_junctions.push(self.junction_token(settled[position].junction, overlay)?);
            after_extensions.push(if overlay {
                0
            } else {
                settled[position].extension
                    + parse_entry_extension(index, &settled[position + 1].cell.adjustments)?
            });
        }

        if row.glyphs.is_empty() || row.glyphs.len() != row.clusters.len() {
            return error(format!(
                "{config} {}: the subset row has {} glyphs and {} clusters",
                unit.codepoints,
                row.glyphs.len(),
                row.clusters.len()
            ));
        }
        let before_spans = spans_from_clusters(&row.clusters, length);
        let mut before_junctions = Vec::with_capacity(row.glyphs.len() - 1);
        for position in 0..row.glyphs.len() - 1 {
            let gap = row.clusters[position + 1] as i64 - 1;
            let at = if gap < 0 {
                gap + row.junctions.len() as i64
            } else {
                gap
            };
            let Some(junction) = usize::try_from(at)
                .ok()
                .and_then(|at| row.junctions.get(at))
            else {
                return error(format!(
                    "{config} {}: the subset row has no junction at {gap}",
                    unit.codepoints
                ));
            };
            before_junctions.push((*junction).to_owned());
        }
        if !before_junctions.iter().map(String::as_str).eq(row
            .junctions
            .iter()
            .copied()
            .filter(|junction| *junction != LIGATURE))
        {
            return error(format!(
                "{config} {}: before junctions {before_junctions:?} disagree with the lig-filtered row junctions",
                unit.codepoints
            ));
        }

        let (diff_codepoints, divergent_gaps) =
            self.diff_codepoints(length, row, &before_spans, &settled, &after_spans)?;
        let diff_positions: Vec<usize> = diff_codepoints
            .iter()
            .map(|&position| covering(&after_spans, position))
            .collect::<BTreeSet<usize>>()
            .into_iter()
            .collect();

        let text: String = values
            .iter()
            .map(|&value| char::from_u32(value))
            .collect::<Option<String>>()
            .ok_or_else(|| EnrichError("a code point is not a Unicode scalar value".to_owned()))?;
        let features = Features::for_config(Some(config)).kern_neutral();
        let shaped = shaper.shape(Side::After, &text, &features)?;
        let before_shaped = shaper.shape(Side::Before, &text, &features)?;
        let before_names: Vec<&str> = before_shaped.names(self.facts.font(Side::Before)).collect();
        if before_names != row.glyphs {
            mismatches.push(format!(
                "{config} {}: kern-neutral before glyphs {} != subset row {}",
                unit.codepoints,
                repr(&PyValue::str_tuple(&before_names))?,
                repr(&PyValue::str_tuple(&row.glyphs))?
            ));
        }
        if shaped.glyphs.is_empty() || before_shaped.glyphs.is_empty() {
            return error(format!(
                "{config} {}: a font shapes the window to no glyph",
                unit.codepoints
            ));
        }
        let cluster_spans = |shaped: &ShapeResult| {
            let clusters: Vec<usize> = shaped
                .clusters
                .iter()
                .map(|&cluster| cluster as usize)
                .collect();
            spans_from_clusters(&clusters, length)
        };
        let after_pens = pen_positions(&shaped.positions);
        let after_cluster_spans = cluster_spans(&shaped);
        let before_pens = pen_positions(&before_shaped.positions);
        let before_cluster_spans = cluster_spans(&before_shaped);

        let ink_positions: Vec<usize> = diff_positions
            .iter()
            .copied()
            .filter(|&position| {
                let (cp_start, cp_end) = after_spans[position];
                self.segment_pieces(
                    Side::Before,
                    &before_shaped,
                    &before_pens,
                    &before_cluster_spans,
                    cp_start,
                    cp_end,
                ) != self.segment_pieces(
                    Side::After,
                    &shaped,
                    &after_pens,
                    &after_cluster_spans,
                    cp_start,
                    cp_end,
                )
            })
            .collect();
        let anchor_positions = if !ink_positions.is_empty() || !divergent_gaps.is_empty() {
            &ink_positions
        } else {
            &diff_positions
        };
        let pair = pick_pair(
            &divergent_gaps,
            anchor_positions,
            &after_junctions,
            cell_count,
        );

        let span_codepoints =
            |cell: usize, other: usize| (after_spans[cell].0, after_spans[other].1 - 1);
        let mut pair_codepoints = pair.map(|(left, right)| span_codepoints(left, right));
        if pair.is_none()
            && diff_positions.is_empty()
            && let Some(mismatched) = advance_mismatch_cell(&before_pens, &after_pens, cell_count)
        {
            let mark = (0..cell_count)
                .filter(|&cell| boundary_kind(values[after_spans[cell].0]).is_some())
                .min_by_key(|&cell| (cell.abs_diff(mismatched), cell))
                .unwrap_or(mismatched);
            pair_codepoints = Some(span_codepoints(mark, mark));
        }
        let (cp_start, cp_end) = match (pair, diff_positions.first(), diff_positions.last()) {
            (Some((left, right)), _, _) => span_codepoints(left, right),
            (None, Some(&first), Some(&last)) => span_codepoints(first, last),
            _ => (0, length - 1),
        };
        let highlight_after = highlight(&after_pens, &after_cluster_spans, cp_start, cp_end)?;
        let highlight_before = highlight(&before_pens, &before_spans, cp_start, cp_end)?;

        let mut secondary_junctions = Vec::new();
        if let Some(primary) = pair
            && !(flags.ink_identical || flags.picture_identical)
        {
            for (left, right) in secondary_pairs(
                primary,
                &divergent_gaps,
                anchor_positions,
                &after_junctions,
                cell_count,
            ) {
                let (start, end) = span_codepoints(left, right);
                secondary_junctions.push(SecondaryJunction {
                    pair: (left, right),
                    highlight_before: highlight(&before_pens, &before_spans, start, end)?,
                    highlight_after: highlight(&after_pens, &after_cluster_spans, start, end)?,
                });
            }
        }

        let boundary_marks = (0..cell_count)
            .filter_map(|cell| {
                let start = after_spans[cell].0;
                boundary_kind(values[start]).map(|kind| BoundaryMark {
                    index: cell,
                    kind,
                    x: after_pens[covering(&after_cluster_spans, start)],
                })
            })
            .collect();

        Ok(Geometry {
            before_glyphs: row.glyphs.iter().map(|&name| name.to_owned()).collect(),
            before_junctions,
            after_cells,
            after_junctions,
            after_extensions,
            diff_positions,
            ink_positions,
            pair,
            pair_codepoints,
            highlight_before,
            highlight_after,
            boundary_marks,
            after_spans,
            before_spans,
            secondary_junctions,
        })
    }

    /// One unit enriched under its first configuration, against its baseline subset row from the mapped pack, which must hold one.
    pub fn enrich(
        &self,
        shaper: &mut Shaper,
        unit: &UnitRecord,
        flags: &Flags,
        report: &Report,
    ) -> Result<Enriched> {
        let Some(config) = unit.configs.first() else {
            return error("the unit has no configuration");
        };
        let Some(row) = self.pack.row(config, &unit.codepoints)? else {
            return error(format!(
                "no baseline subset row for {config} {}",
                unit.codepoints
            ));
        };
        self.enrich_with_row(shaper, unit, flags, report, &row)
    }

    /// [`Enricher::enrich`] against the given baseline subset row instead of the pack's.
    pub fn enrich_with_row(
        &self,
        shaper: &mut Shaper,
        unit: &UnitRecord,
        flags: &Flags,
        report: &Report,
        row: &SubsetRow<'_>,
    ) -> Result<Enriched> {
        let Some(config) = unit.configs.first() else {
            return error("the unit has no configuration");
        };
        let mut mismatches = Vec::new();
        let geometry = self.geometry(shaper, unit, config, flags, report, row, &mut mismatches)?;
        let index = self.index();
        let vocab = index.vocab();
        let unbounded = |position: &usize| {
            report
                .traces
                .get(*position)
                .filter(|trace| !is_boundary_settled(vocab, &trace.settled))
        };
        let provenance =
            collect_provenance(index, geometry.diff_positions.iter().filter_map(unbounded));
        let slim = flags.ink_identical
            || flags.picture_identical
            || flags.junior_equivalent
            || unit.no_verdict;
        let explain_text = if slim {
            String::new()
        } else {
            let positions: Vec<i64> = geometry
                .diff_positions
                .iter()
                .map(|&position| i64::try_from(position).expect("an index fits an i64"))
                .collect();
            filter_explain(&render(index, report), &positions)?
        };
        let summary_positions = if geometry.ink_positions.is_empty() {
            &geometry.diff_positions
        } else {
            &geometry.ink_positions
        };
        let position = summary_positions
            .iter()
            .copied()
            .find(|position| unbounded(position).is_some());
        let stage = position.map(|position| report.traces[position].decided_stage);
        let clause = self.summary_clause(&report.traces, &geometry, position)?;
        let summary = format!("New: {clause} — {}.", decided_by(&provenance, stage));
        let values = &unit.codepoint_values;
        Ok(Enriched {
            notation: self.notation(values),
            notation_tokens: values
                .iter()
                .map(|&value| self.token(value).into_owned())
                .collect(),
            text_entities: text_entities(values),
            explain_text,
            provenance,
            positions: report
                .traces
                .iter()
                .map(|trace| (trace.settled.clone(), trace.decided_stage))
                .collect(),
            summary,
            mismatches,
            geometry,
        })
    }

    /// One code point's display token, as `notation_tokens` writes it: a letter's name, a boundary's token, or `U+` and the code point.
    fn token(&self, value: u32) -> Cow<'_, str> {
        match self.letters.get(&value) {
            Some(display) => Cow::Borrowed(display),
            None => BOUNDARY_NOTATION
                .iter()
                .find(|(boundary, _)| *boundary == value)
                .map_or_else(
                    || Cow::Owned(format!("U+{value:04X}")),
                    |(_, token)| Cow::Borrowed(*token),
                ),
        }
    }

    /// `enrich.notation`: the tokens with letters concatenated and every other token set off by a space from what comes before it.
    fn notation(&self, values: &[u32]) -> String {
        let mut text = String::new();
        let mut previous_was_letter = false;
        for (position, &value) in values.iter().enumerate() {
            let letter = self.letters.contains_key(&value);
            if position > 0 && !(letter && previous_was_letter) {
                text.push(' ');
            }
            text.push_str(&self.token(value));
            previous_was_letter = letter;
        }
        text
    }

    /// What the new pipeline chose at the summary's position, over the settled traces and the unit's geometry.
    fn summary_clause(
        &self,
        traces: &[Trace],
        geometry: &Geometry,
        position: Option<usize>,
    ) -> Result<String> {
        let index = self.index();
        let rune = |cell: usize| rune_display(index.resolve(traces[cell].settled.cell.rune));
        let wide = |(start, end): Span| end - start > 1;
        if let Some(position) = position {
            let span = geometry.after_spans[position];
            if wide(span) && !geometry.before_spans.contains(&span) {
                return Ok(format!(
                    "{} now forms as one ligature (the old pipeline rendered the letters separately)",
                    rune(position)
                ));
            }
        }
        for (cell, &span) in geometry.before_spans.iter().enumerate() {
            if wide(span) && !geometry.after_spans.contains(&span) {
                let base = geometry.before_glyphs[cell]
                    .split('.')
                    .next()
                    .unwrap_or_default();
                return Ok(format!(
                    "the {} ligature no longer forms; the letters render separately",
                    rune_display(base)
                ));
            }
        }
        if let Some((left, right)) = geometry.pair {
            let Some(after_junction) = geometry.after_junctions.get(left) else {
                return error(format!(
                    "the judged pair ({left}, {right}) has no junction after its left cell"
                ));
            };
            let gap = geometry.after_spans[left].1 - 1;
            let before_junction = before_junction_at_codepoint_gap(
                &geometry.before_spans,
                &geometry.before_junctions,
                gap,
            );
            let joined = |token: &str| token.starts_with('y');
            let (left, right) = (rune(left), rune(right));
            match before_junction {
                None | Some(BREAK) if joined(after_junction) => {
                    return Ok(format!(
                        "{left} joins {right} {} (the old pipeline broke there)",
                        junction_phrase(after_junction)?
                    ));
                }
                Some(before) if after_junction == BREAK && joined(before) => {
                    return Ok(format!(
                        "{left} no longer joins {right} (the old pipeline joined {})",
                        junction_phrase(before)?
                    ));
                }
                Some(before)
                    if joined(after_junction) && joined(before) && after_junction != before =>
                {
                    return Ok(format!(
                        "{left} joins {right} {} instead of {}",
                        junction_phrase(after_junction)?,
                        junction_phrase(before)?
                    ));
                }
                _ => {}
            }
        }
        if let Some(position) = position {
            let cell = &traces[position].settled.cell;
            return Ok(format!(
                "{} keeps the same junctions but settles as a different cell ({})",
                rune_display(index.resolve(cell.rune)),
                cell_description(index, cell)
            ));
        }
        Ok(
            "only the boundary marker's glyph changed; every letter cell and junction is unchanged"
                .to_owned(),
        )
    }
}

/// `enrich.letter_display`: a family's display name, `·` and the name after `qs` unless the family has a special one.
pub fn letter_display(family: &str) -> String {
    SPECIAL_DISPLAY
        .iter()
        .find(|(special, _)| *special == family)
        .map_or_else(
            || format!("·{}", family.chars().skip(2).collect::<String>()),
            |(_, display)| (*display).to_owned(),
        )
}

/// A settled cell's rune in prose, a boundary by name, a ligature's letters joined with `+`, and a rune outside `qs` as it is.
pub fn rune_display(rune: &str) -> String {
    if let Some((_, name)) = BOUNDARY_SUMMARY_NAMES
        .iter()
        .find(|(boundary, _)| *boundary == rune)
    {
        return (*name).to_owned();
    }
    if !rune.starts_with("qs") {
        return rune.to_owned();
    }
    let mut parts = rune.split('_');
    let mut display = letter_display(parts.next().unwrap_or_default());
    for part in parts {
        let shown = letter_display(part);
        display.push('+');
        display.push_str(shown.strip_prefix('·').unwrap_or(&shown));
    }
    display
}

/// A junction token's height in words, or `at y=` and its y.
fn junction_phrase(token: &str) -> Result<String> {
    let rest: String = token.chars().skip(1).collect();
    let Some(y) = python_int(&rest) else {
        return error(format!(
            "the junction token {token:?} ends in no int Python reads"
        ));
    };
    Ok(HEIGHT_PHRASES
        .iter()
        .find(|(height, _)| *height == y)
        .map_or_else(|| format!("at y={y}"), |(_, phrase)| (*phrase).to_owned()))
}

/// A provenance pointer's file name and its path, the first colon a space.
fn short_provenance(pointer: &str) -> String {
    pointer
        .rsplit_once('/')
        .map_or(pointer, |(_, last)| last)
        .replacen(':', " ", 1)
}

/// The summary's tail, the first provenance pointer and the deciding stage's phrase as far as there are any.
fn decided_by(provenance: &[String], stage: Option<&str>) -> String {
    let phrase = stage.filter(|stage| !stage.is_empty()).map(|stage| {
        STAGE_PHRASES
            .iter()
            .find(|(name, _)| *name == stage)
            .map_or(stage, |(_, phrase)| phrase)
    });
    match (provenance.first(), phrase) {
        (Some(pointer), Some(phrase)) => {
            format!("decided by {} ({phrase})", short_provenance(pointer))
        }
        (Some(pointer), None) => format!("decided by {}", short_provenance(pointer)),
        (None, Some(phrase)) => format!("decided by {phrase} (no policy record involved)"),
        (None, None) => "no policy record involved".to_owned(),
    }
}

/// The before junction after the glyph whose successor starts just after the gap, or None.
fn before_junction_at_codepoint_gap<'j>(
    before_spans: &[Span],
    before_junctions: &'j [String],
    gap: usize,
) -> Option<&'j str> {
    (0..before_spans.len().saturating_sub(1))
        .find(|&cell| before_spans[cell + 1].0 == gap + 1)
        .and_then(|cell| before_junctions.get(cell))
        .map(String::as_str)
}

/// A cell's stance, entry and exit (`none` for an absent height) and any adjustments.
fn cell_description(index: &SpecIndex, cell: &CellId) -> String {
    let height = |height: Option<Sym>| {
        height
            .map(|height| index.resolve(height))
            .filter(|text| !text.is_empty())
            .unwrap_or("none")
    };
    let mut bits = vec![
        index.resolve(cell.stance).to_owned(),
        format!("entry {}", height(cell.entry)),
        format!("exit {}", height(cell.exit)),
    ];
    if !cell.adjustments.is_empty() {
        let adjustments: Vec<String> = cell
            .adjustments
            .iter()
            .map(|token| adjustment_text(index, *token))
            .collect();
        bits.push(format!("adjustments {}", adjustments.join("+")));
    }
    bits.join(", ")
}

/// Each trace's elimination pointers and the `glyph_data/` tail of each of its notes, in order, each once.
fn collect_provenance<'t>(
    index: &SpecIndex,
    traces: impl Iterator<Item = &'t Trace>,
) -> Vec<String> {
    let mut pointers: Vec<String> = Vec::new();
    let mut add = |pointer: String| {
        if !pointers.contains(&pointer) {
            pointers.push(pointer);
        }
    };
    for trace in traces {
        for elimination in &trace.ranking.eliminations {
            if let Some(at) = &elimination.provenance {
                add(provenance_pointer(index, at));
            }
        }
        for note in &trace.notes {
            if let Some(marker) = note.find("glyph_data/") {
                add(note[marker..].to_owned());
            }
        }
    }
    pointers
}

/// One `EnrichedUnit`, less the unit, and the mismatch lines the call adds to the enricher's list.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Enriched {
    pub geometry: Geometry,
    pub notation: String,
    pub notation_tokens: Vec<String>,
    pub text_entities: String,
    pub explain_text: String,
    pub provenance: Vec<String>,
    /// The kept trace, `TraceReport`: each position's settled record and deciding stage.
    pub positions: Vec<(Settled, &'static str)>,
    pub summary: String,
    pub mismatches: Vec<String>,
}

/// The phase-1 flags a unit is enriched with: what the worker set on it before enriching it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Flags {
    pub ink_identical: bool,
    pub picture_identical: bool,
    pub junior_equivalent: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn junctions(tokens: &[&str]) -> Vec<String> {
        tokens.iter().map(|token| (*token).to_owned()).collect()
    }

    #[test]
    fn spans_cover_each_cluster_at_least_one_code_point_wide() {
        assert_eq!(
            spans_from_clusters(&[0, 2, 2, 3], 5),
            [(0, 2), (2, 3), (2, 3), (3, 5)]
        );
        let spans = [(0, 2), (2, 3)];
        assert_eq!(covering(&spans, 1), 0);
        assert_eq!(covering(&spans, 2), 1);
        assert_eq!(covering(&spans, 9), 1);
        assert_eq!(pen_positions(&[(0, 0, 100), (5, -5, 50)]), [0, 100, 150]);
    }

    #[test]
    fn text_entities_write_what_the_format_writes() {
        let values = [
            0,
            0x7,
            0x20,
            0xB7,
            0xE650,
            0xFFFF,
            0x1_0000,
            0x1F600,
            0x10_FFFF,
            u32::MAX,
        ];
        let expected: String = values
            .iter()
            .map(|value| format!("&#x{value:04X};"))
            .collect();
        assert_eq!(text_entities(&values), expected);
        assert_eq!(text_entities(&[]), "");
    }

    #[test]
    fn a_highlight_spans_the_covering_glyphs_and_refuses_pens_that_go_back() {
        let pens = [0, 100, 150, 250];
        let spans = [(0, 1), (1, 3), (3, 4)];
        assert_eq!(
            highlight(&pens, &spans, 1, 2),
            Ok(Highlight {
                x_min: 100,
                x_max: 150,
                advance_total: 250
            })
        );
        assert!(highlight(&[0, 100, 90], &spans, 0, 0).is_err());
        assert!(highlight(&[0, 100], &spans, 0, 3).is_err());
    }

    #[test]
    fn the_judged_pair_prefers_a_gap_then_adjacent_anchors_then_the_join_direction() {
        let joined = junctions(&["y0", "break", "y5"]);
        assert_eq!(pick_pair(&[(2, 3)], &[0], &joined, 4), Some((2, 3)));
        assert_eq!(pick_pair(&[], &[0, 2, 3], &joined, 4), Some((2, 3)));
        assert_eq!(pick_pair(&[], &[1], &joined, 4), Some((0, 1)));
        assert_eq!(pick_pair(&[], &[3], &joined, 4), Some((2, 3)));
        assert_eq!(pick_pair(&[], &[2], &joined, 4), Some((2, 3)));
        assert_eq!(pick_pair(&[], &[], &joined, 4), None);
        assert_eq!(pick_pair(&[], &[0], &[], 1), None);
    }

    #[test]
    fn secondary_pairs_skip_the_primary_and_what_it_covers_and_sort() {
        let joined = junctions(&["y0", "break", "y5", "break", "break"]);
        assert_eq!(
            secondary_pairs((0, 1), &[(4, 5), (0, 1)], &[0, 2, 3, 5], &joined, 6),
            [(2, 3), (4, 5)]
        );
        assert_eq!(secondary_pairs((0, 1), &[], &[4], &joined, 6), [(4, 5)]);
        assert_eq!(secondary_pairs((0, 1), &[], &[5], &joined, 6), [(4, 5)]);
    }

    #[test]
    fn runes_display_as_letters_ligatures_and_boundary_names() {
        assert_eq!(letter_display("qsMay"), "·May");
        assert_eq!(letter_display("qsIng"), "·-ing");
        assert_eq!(rune_display("qsTea_qsOy"), "·Tea+Oy");
        assert_eq!(rune_display("qsDay_qsJai"), "·Day+J’ai");
        assert_eq!(rune_display("namer-dot"), "the namer dot");
        assert_eq!(rune_display("other"), "other");
    }

    #[test]
    fn the_summary_tail_names_the_first_pointer_and_the_stage() {
        let pointer = vec!["glyph_data/runes/qsMay.yaml:policy.extend[3]".to_owned()];
        assert_eq!(
            decided_by(&pointer, Some("join-count")),
            "decided by qsMay.yaml policy.extend[3] (join-count rank)"
        );
        assert_eq!(
            decided_by(&pointer, None),
            "decided by qsMay.yaml policy.extend[3]"
        );
        assert_eq!(
            decided_by(&[], Some("isolated-overlay")),
            "decided by isolated-overlay (no policy record involved)"
        );
        assert_eq!(decided_by(&[], Some("")), "no policy record involved");
        assert_eq!(junction_phrase("y5"), Ok("at the x-height".to_owned()));
        assert_eq!(junction_phrase("y6"), Ok("at y=6".to_owned()));
        assert!(junction_phrase("y").is_err());
    }

    #[test]
    fn the_before_junction_at_a_gap_is_the_one_before_the_glyph_after_it() {
        let spans = [(0, 1), (1, 3), (3, 4)];
        let junctions = junctions(&["y0", "break"]);
        assert_eq!(
            before_junction_at_codepoint_gap(&spans, &junctions, 0),
            Some("y0")
        );
        assert_eq!(
            before_junction_at_codepoint_gap(&spans, &junctions, 2),
            Some("break")
        );
        assert_eq!(
            before_junction_at_codepoint_gap(&spans, &junctions, 1),
            None
        );
    }

    #[test]
    fn the_first_cell_whose_advance_differs_is_the_mismatch() {
        assert_eq!(
            advance_mismatch_cell(&[0, 10, 20], &[0, 10, 21], 2),
            Some(1)
        );
        assert_eq!(advance_mismatch_cell(&[0, 10, 20], &[0, 10, 21], 1), None);
        assert_eq!(advance_mismatch_cell(&[0, 10], &[0, 10, 20], 3), None);
    }
}

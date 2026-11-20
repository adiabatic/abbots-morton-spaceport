//! The geometry of an enriched unit, as `Enricher.enrich` (`rebuild/review/enrich.py`) computes it before its summary: the isolated overlay, the formed spans, the after cells (`cell_token`), the junction and extension tuples (`junction_token`, `parse_entry_extension`), the baseline subset row and its junction assertion, the divergent positions against the alias map (`_diff_codepoints`), the ink-visible positions (`_ink_visible_positions`, `_segment_pieces`), the judged pair (`_pick_pair`) and the secondary pairs (`_secondary_pairs`), the position-only mark (`_advance_mismatch_cell`), the highlight rects (`_highlight`, `_spans_from_clusters`, `_covering`, `_pen_positions`) and the boundary marks.
//!
//! A piece is placed from the font facts, as [`crate::ink`] places one: its shape is the glyph's interned shape, so two pieces compare equal across fonts exactly when Python's shape keys do.
//!
//! [`stage_enrich`] is the `enrich` stage of the stage fixtures, for the geometry fields.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::fmt;
use std::io::{BufRead, Write};
use std::path::Path;

use ams_m1_kernel::emit::json_string;
use ams_m1_kernel::index::SpecIndex;
use ams_m1_kernel::model::Sym;
use ams_m1_kernel::types::{AdjustmentToken, CellId, Settled, adjustment_text};
use serde_json::Value;

use crate::explain::python_int;
use crate::fontfacts::{FontFacts, ShapeId, Side};
use crate::ink::{InkError, InkReader};
use crate::pyfmt::PyValue;
use crate::settle::{Report, SettleError, Settlement, Settler, features_for_config};
use crate::shape::{Features, Fonts, ShapeError, ShapeResult, Shaper};
use crate::subset_pack::{SubsetPack, SubsetPackError, SubsetRow};
use crate::wire::{Alias, Init, UnitRecord, WireError};

/// The vocabulary name of `labels.BOUNDARY_GLYPH_NAMES`, the old glyph names `_diff_codepoints` never compares with a cell.
pub const BOUNDARY_GLYPH_NAMES_NAME: &str = "rebuild.pipeline.labels.BOUNDARY_GLYPH_NAMES";

/// `enrich.BOUNDARIES`: each boundary code point and the kind its boundary mark names.
pub const BOUNDARIES: [(u32, &str); 3] =
    [(0x0020, "space"), (0x00B7, "namer-dot"), (0x200C, "zwnj")];

/// The junction token of a break, and of a ligature's inner gap in the subset rows.
const BREAK: &str = "break";
const LIGATURE: &str = "lig";

/// The geometry fields of an `EnrichedUnit` in its declaration order, which the `enrich` stage writes and `compare enrich --fields geometry` checks.
pub const GEOMETRY_FIELDS: [&str; 14] = [
    "before_glyphs",
    "before_junctions",
    "after_cells",
    "after_junctions",
    "after_extensions",
    "diff_positions",
    "pair",
    "highlight_before",
    "highlight_after",
    "boundary_marks",
    "after_spans",
    "before_spans",
    "secondary_junctions",
    "pair_codepoints",
];

/// A unit the engine cannot enrich as Python enriches it, or an `enrich` stage input it cannot read.
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
    WireError
);

type Result<T> = std::result::Result<T, EnrichError>;

fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(EnrichError(message.into()))
}

/// A half-open code point span, `(start, end)`.
pub type Span = (usize, usize);

/// A judged or secondary pair, `(left, right)` after-cell indices.
pub type Pair = (usize, usize);

/// One `_highlight` rect in font units.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Highlight {
    pub x_min: i64,
    pub x_max: i64,
    pub advance_total: i64,
}

impl Highlight {
    /// The rect as Python holds it, a dict in `_highlight`'s key order.
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

impl SecondaryJunction {
    /// The junction as Python holds it, its primary unit None and not suppressed.
    pub fn to_py(&self) -> PyValue {
        PyValue::Record(
            "SecondaryJunction".to_owned(),
            vec![
                ("pair".to_owned(), pair_value(self.pair)),
                ("highlight_before".to_owned(), self.highlight_before.to_py()),
                ("highlight_after".to_owned(), self.highlight_after.to_py()),
                ("primary_unit".to_owned(), PyValue::None),
                ("suppressed".to_owned(), PyValue::Bool(false)),
            ],
        )
    }
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

/// The geometry `Enricher.enrich` computes for one unit before its summary: the [`GEOMETRY_FIELDS`] of its `EnrichedUnit`, and the ink-visible divergent positions the summary reads.
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

impl Geometry {
    /// Each of the [`GEOMETRY_FIELDS`] as Python holds it on the `EnrichedUnit`, in that order.
    pub fn fields(&self) -> Vec<(&'static str, PyValue)> {
        let spans =
            |spans: &[Span]| PyValue::Tuple(spans.iter().map(|&span| pair_value(span)).collect());
        let optional_pair = |pair: Option<Pair>| pair.map_or(PyValue::None, pair_value);
        let values = [
            PyValue::str_tuple(&self.before_glyphs),
            PyValue::str_tuple(&self.before_junctions),
            PyValue::str_tuple(&self.after_cells),
            PyValue::str_tuple(&self.after_junctions),
            PyValue::Tuple(
                self.after_extensions
                    .iter()
                    .map(|&value| PyValue::Int(value))
                    .collect(),
            ),
            PyValue::Tuple(
                self.diff_positions
                    .iter()
                    .map(|&value| int(value))
                    .collect(),
            ),
            optional_pair(self.pair),
            self.highlight_before.to_py(),
            self.highlight_after.to_py(),
            PyValue::Tuple(
                self.boundary_marks
                    .iter()
                    .map(BoundaryMark::to_py)
                    .collect(),
            ),
            spans(&self.after_spans),
            spans(&self.before_spans),
            PyValue::Tuple(
                self.secondary_junctions
                    .iter()
                    .map(SecondaryJunction::to_py)
                    .collect(),
            ),
            optional_pair(self.pair_codepoints),
        ];
        GEOMETRY_FIELDS.into_iter().zip(values).collect()
    }
}

fn int(value: usize) -> PyValue {
    PyValue::Int(i64::try_from(value).expect("an index fits an i64"))
}

fn pair_value((left, right): Pair) -> PyValue {
    PyValue::Tuple(vec![int(left), int(right)])
}

/// `enrich.cell_token`: the cell's rune, stance, entry and exit, `None` for an absent height as Python's f-string writes it, and its adjustments joined with `+`.
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

/// `enrich.parse_entry_extension`: the entry extensions less the entry contractions among the adjustments, each read as `int` reads the text after its last `-`.
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

/// `enrich._pen_positions`: the pen x before each glyph and after the last.
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

/// `enrich._spans_from_clusters`: each cluster's code point span, from its start to the next cluster's or the text's end, at least one code point wide.
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

/// `enrich._covering`: the first span holding the position, or the last span when none does. The spans are never empty here (the caller refuses a window with none).
pub fn covering(spans: &[Span], position: usize) -> usize {
    spans
        .iter()
        .position(|&(start, end)| start <= position && position < end)
        .unwrap_or(spans.len() - 1)
}

/// `enrich._highlight`: the pen x range of the spans covering `cp_start` through `cp_end`, and the run's whole advance; pens that decrease are refused.
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

/// `enrich._advance_mismatch_cell`: the first cell whose kern-neutral advance differs between the two runs, or None.
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

/// Whether the cell at `position` joins the cell after it, or (with no cell after) does not join the one before, which is when `_pick_pair` and `_secondary_pairs` pair a lone position rightward.
fn pairs_rightward(position: usize, after_junctions: &[String], cell_count: usize) -> (bool, bool) {
    let joins_right = position + 1 < cell_count && after_junctions[position] != BREAK;
    let joins_left = position > 0 && after_junctions[position - 1] != BREAK;
    (
        joins_right || (position + 1 < cell_count && !joins_left),
        position > 0,
    )
}

/// `Enricher._pick_pair`: the first divergent gap, else the first two adjacent anchor positions, else a lone anchor position paired with the neighbor it joins toward.
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

/// `enrich._secondary_pairs`: every divergent adjacency beyond the primary pair in left-index order, the remaining divergent gaps and a neighbor junction for each anchor position the primary and the gaps do not cover.
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

/// A placed piece as `_segment_pieces` sorts and compares it: `(shape, x, y)`.
type SegmentPiece = (ShapeId, i64, i64);

/// The enricher's shared readers: the settlement, the mapped subset pack, the font facts, the alias map and the boundary glyph names.
pub struct Enricher<'a> {
    settlement: &'a Settlement,
    pack: &'a SubsetPack,
    facts: &'a FontFacts,
    aliases: HashMap<&'a str, &'a Alias>,
    boundary_glyph_names: HashSet<String>,
}

impl<'a> Enricher<'a> {
    /// The enricher over the init's aliases, later entries winning as in Python's dict, and its `BOUNDARY_GLYPH_NAMES`, which must be a set of `str`.
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
        })
    }

    fn index(&self) -> &SpecIndex {
        self.settlement.index()
    }

    /// `Enricher.junction_token`: `break` under the overlay or for no junction, else `y` and the height's y.
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

    /// `Enricher._diff_codepoints`: the divergent code point positions (a covering-structure or alias-against-cell mismatch, and both sides of a divergent gap inside a ligature) and the divergent inter-cell gaps as after-cell pairs.
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

    /// `Enricher._segment_pieces`: the placed ink of one run's glyphs covering code points `[cp_start, cp_end)`, translated so the segment's leftmost drawn ink is at x=0, a shape with no point at (0, 0), sorted; empty when nothing drawn is placed.
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

    /// The geometry of one unit under its first configuration (`Enricher.enrich` up to its summary), from its settled report, its flags and the shaper's kern-neutral runs of both fonts.
    pub fn geometry(
        &self,
        shaper: &mut Shaper,
        unit: &UnitRecord,
        ink_identical: bool,
        picture_identical: bool,
        report: &Report,
    ) -> Result<Geometry> {
        let index = self.index();
        let values = &unit.codepoint_values;
        let length = values.len();
        let Some(config) = unit.configs.first() else {
            return error("the unit has no configuration");
        };
        let overlay = self.settlement.overlay_active(&features_for_config(config));
        let settled: Vec<&Settled> = report.traces.iter().map(|trace| &trace.settled).collect();
        let cell_count = settled.len();
        let after_cells = settled
            .iter()
            .map(|settled| cell_token(index, &settled.cell))
            .collect();

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

        let Some(row) = self.pack.row(config, &unit.codepoints)? else {
            return error(format!(
                "no baseline subset row for {config} {}",
                unit.codepoints
            ));
        };
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
            self.diff_codepoints(length, &row, &before_spans, &settled, &after_spans)?;
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
            && !(ink_identical || picture_identical)
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
}

/// The phase-1 flags an `enrich` line carries: what the worker's `_phase1_unit` set on the unit before enriching it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Flags {
    ink_identical: bool,
    picture_identical: bool,
    junior_equivalent: bool,
}

fn line_flags(record: &Value, key: &str) -> Result<Flags> {
    let PyValue::Dict(entries) = PyValue::from_typed(&record["flags"])
        .map_err(|failure| EnrichError(format!("{key}: the flags: {failure}")))?
    else {
        return error(format!("{key}: the flags are not a dict"));
    };
    let flag = |name: &str| match entries.iter().find(|(held, _)| *held == PyValue::str(name)) {
        Some((_, PyValue::Bool(value))) => Ok(*value),
        _ => error(format!("{key}: the flags hold no bool {name}")),
    };
    Ok(Flags {
        ink_identical: flag("ink_identical")?,
        picture_identical: flag("picture_identical")?,
        junior_equivalent: flag("junior_equivalent")?,
    })
}

/// The `enrich` stage: for each `enrich:` line of `stage_file`, the unit its `record` carries, enriched under its first configuration, written as one line of the line's `key` and the [`GEOMETRY_FIELDS`] in the typed encoding. The upstream values are recomputed from the record through the stages that own them, and the ink stage's flags are checked first against the line's `flags`, which the worker's phase 1 set, so a failure there names the ink stage; the report is the settler's ([`Settler::explain`]), the row the mapped pack's and the runs the shaper's, each a stage of its own. The shaper's and the settler's memos carry from line to line as a batch's do.
pub fn stage_enrich(init_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let init = Init::read(init_path)?;
    let facts = FontFacts::read(&init.font_facts)?;
    let fonts = Fonts::open(&init, &facts)?;
    let reader = InkReader::new(&init, &facts)?;
    let settlement = Settlement::new(SpecIndex::new(init.read_spec()?), init.settlement.clone())?;
    let mut settler = Settler::new(&settlement);
    let pack = SubsetPack::open(&init.subset_pack)?;
    let enricher = Enricher::new(&init, &settlement, &pack, &facts)?;
    let mut shaper = Shaper::new(&fonts, &facts);
    let file = std::fs::File::open(stage_file).map_err(|failure| {
        EnrichError(format!("cannot read {}: {failure}", stage_file.display()))
    })?;
    for line in std::io::BufReader::new(file).lines() {
        let line = line.map_err(|failure| {
            EnrichError(format!("cannot read {}: {failure}", stage_file.display()))
        })?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| EnrichError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        if !key.starts_with("enrich:") {
            return error(format!("{key}: the enrich stage answers enrich: lines"));
        }
        let Some(unit) = record["record"].as_str() else {
            return error(format!("{key}: the line has no unit record"));
        };
        let unit: Value = serde_json::from_str(unit)
            .map_err(|failure| EnrichError(format!("{key}: the unit record: {failure}")))?;
        let unit = UnitRecord::from_json(&unit)?;
        let flags = line_flags(&record, key)?;
        let ink = reader.read_unit(&mut shaper, &unit)?;
        let read = Flags {
            ink_identical: ink.ink_identical,
            picture_identical: ink.picture_identical,
            junior_equivalent: ink.junior_equivalent,
        };
        if read != flags {
            return error(format!(
                "{key}: the ink stage reads {read:?}, and the worker's phase 1 set {flags:?}"
            ));
        }
        let config = unit.configs.first().map_or("", String::as_str);
        let report = settler
            .explain(&unit.codepoint_values, &features_for_config(config))
            .map_err(|failure| EnrichError(format!("{key}: the settle stage: {failure}")))?;
        let geometry = enricher
            .geometry(
                &mut shaper,
                &unit,
                flags.ink_identical,
                flags.picture_identical,
                &report,
            )
            .map_err(|failure| EnrichError(format!("{key}: {failure}")))?;
        let mut text = format!("{{\"key\":{}", json_string(key));
        for (name, value) in geometry.fields() {
            text.push_str(&format!(",{}:{}", json_string(name), value.to_typed()));
        }
        text.push('}');
        writeln!(out, "{text}")
            .map_err(|failure| EnrichError(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
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
    fn the_first_cell_whose_advance_differs_is_the_mismatch() {
        assert_eq!(
            advance_mismatch_cell(&[0, 10, 20], &[0, 10, 21], 2),
            Some(1)
        );
        assert_eq!(advance_mismatch_cell(&[0, 10, 20], &[0, 10, 21], 1), None);
        assert_eq!(advance_mismatch_cell(&[0, 10], &[0, 10, 20], 3), None);
    }
}

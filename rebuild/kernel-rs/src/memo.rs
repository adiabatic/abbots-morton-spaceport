//! A finished trace memo detached from the engine that filled it, and the rule under which another engine may read it. The memo is a pure function of the raw window: an entry records what one engine settled for one collapsed left, one input and four raw slots, together with the pointers that evaluation fired, and by the window-locality theorem (`doc/rebuild-design.md` §10) that answer is a function of the crate, the script registry and the rune files the key names, and of nothing else. So a memo one enumeration finished can answer another enumeration's windows wherever the two enumerations agree on every rune a key names — and the configuration corollary says exactly where a configuration disagrees with `default`: on the windows naming a rune with an unlock, a `feature:`-conditioned record, or an unlock gate under that configuration, and on nothing else.
//!
//! That is the whole mechanism of the per-configuration delta enumeration (issue #178). `default` enumerates first and its engine hands its memo over as a [`MemoSnapshot`]; every other configuration's engine takes that snapshot as a [`MemoBase`] whose [`Exclusion`] names the configuration's unlocking runes ([`unlocking_runes`]), runs the same worklist from the same seeds in the same order, and finds every window naming no unlocking rune already answered. Nothing about the worklist is seeded: reachability is re-derived by the traversal itself, which is what keeps a cell another configuration reaches first, or reaches only there, out of the theorem's way — the memo answers what a window settles to, never whether the window exists. The fired journal survives the same way a hit on the engine's own memo survives it: a base entry carries the delta its evaluation journaled, and a hit replays it, so a delta configuration's `cited_provenance` is the union over the windows it visited exactly as a from-scratch enumeration's is.
//!
//! The same rule carries a memo across builds (issue #179). A table build writes each configuration's finished memo beside its tables as `memo-<config>.tsv` ([`write_memo`]), and the next build reads it back as a base whose exclusion names every rune edited in between ([`read_memo`]): a window naming no edited rune settles as it settled last time, and only the rest are traced. The file is at the raw-window grain of the memo itself rather than at the rows' — it holds the probe windows the liveness and fiber derivations trace, with their virtual lefts and unknown coordinates, beside the rows' own — so the derivations that quantify over the whole alphabet are re-run every build and answered out of the base wherever a probe names no edited rune, which is what keeps a verdict that aggregates over every letter exact without persisting the verdict itself. Which runes count as edited, and whether the file may be read at all, is `run_m1`'s decision: the head carries the configuration, the world and an opaque stamp the writer chose, the crate refuses a file whose configuration or world is not the one asked for, and everything about the stamp — the structure it names and the rune digests it records — is read on the Python side.
//!
//! The snapshot is shared behind an [`Arc`] rather than copied per configuration, because the memo is the enumeration's high-water mark and a copy per delta configuration would put the fan-out back on the memory bound the delta was meant to lift. It therefore holds no `Rc`, no reference into any engine, and no ladder — the fixpoint never records one — and a base is read-only from the moment it is built.

use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;
use std::io::{BufRead, Write as _};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::engine::{DeltaSeat, Pointer, TraceEntry, TraceKey};
use crate::index::SpecIndex;
use crate::model::{PolicyRecord, Provenance, Sym, When};
use crate::types::{
    AdjustmentToken, CellId, DecidedStage, NotesSeat, Settled, SettledSeat, TokenKind,
    TransitionTrace, adjustment_from_text, adjustment_text, boundary_settled,
};

/// One engine's finished trace memo: the entries with the three tables their seats index. The tables are the memo's own pools flattened, so an entry read through the snapshot resolves exactly as it resolved through the engine that recorded it.
#[derive(Debug, Default)]
pub struct MemoSnapshot {
    pub(crate) entries: HashMap<TraceKey, TraceEntry>,
    pub(crate) settled: Vec<Settled>,
    pub(crate) notes: Vec<Vec<String>>,
    pub(crate) deltas: Vec<Box<[Pointer]>>,
}

impl MemoSnapshot {
    /// How many windows this snapshot answers.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The trace one entry stands for, rebuilt out of the tables exactly as the recording engine's miss returned it, less the ladder no fixpoint records.
    pub(crate) fn trace(&self, entry: TraceEntry) -> TransitionTrace {
        TransitionTrace {
            settled: self.settled[entry.settled.index()].clone(),
            joint_floor: entry.joint_floor,
            prospect: i64::from(entry.prospect),
            decided_stage: entry.decided_stage,
            notes: self.notes[entry.notes.index()].to_vec(),
            ladder: None,
        }
    }

    /// The settled record one entry names, where it sits.
    pub(crate) fn settled(&self, entry: TraceEntry) -> &Settled {
        &self.settled[entry.settled.index()]
    }

    /// The fired delta one entry names, where it sits.
    pub(crate) fn delta(&self, entry: TraceEntry) -> &[Pointer] {
        &self.deltas[entry.delta.index()]
    }
}

/// The runes a base may not answer for: a key naming any of them is a miss on that base, whatever the base holds. Membership is by the six runes a [`TraceKey`] carries — the left cell's, the input's and the four raw slots' — because those are the only runes the engine reads while settling the window (the module doc says why the theorem makes that the whole list).
#[derive(Clone, Debug, Default)]
pub struct Exclusion {
    runes: HashSet<Sym>,
}

impl Exclusion {
    /// An exclusion over exactly these runes.
    pub fn of(runes: impl IntoIterator<Item = Sym>) -> Self {
        Self {
            runes: runes.into_iter().collect(),
        }
    }

    /// An exclusion naming nothing, under which a base answers every key it holds.
    pub fn none() -> Self {
        Self::default()
    }

    /// Whether this base may answer for `key`: none of the runes it names is excluded.
    pub(crate) fn admits(&self, key: &TraceKey) -> bool {
        self.runes.is_empty() || !key.runes_named().any(|rune| self.runes.contains(&rune))
    }

    /// The runes this exclusion names.
    pub fn runes(&self) -> &HashSet<Sym> {
        &self.runes
    }
}

/// One memo another engine may read, and the runes it may not read it for.
#[derive(Clone, Debug)]
pub struct MemoBase {
    pub memo: Arc<MemoSnapshot>,
    pub excluded: Exclusion,
}

/// Every rune whose settlement can differ between `default` and the configuration enabling `features`: a rune with an unlock under one of them, or with any record — an unlock's own gate, a refusal, a prefer, an extension, a contraction or a resolution — whose `when:` names one. The scan reads every `when:` a rune can carry rather than the record kinds `rebuild/test_spec_load.py` pins feature conditions to, so a kind gaining a feature gate widens this set without an edit here.
///
/// A rune outside this set carries no record that reads the feature set at all, so every window naming only such runes settles identically under both configurations; that is the configuration corollary of the window-locality theorem, and the reason the delta enumeration excludes exactly this set from its base.
pub fn unlocking_runes(index: &SpecIndex, features: &[Sym]) -> HashSet<Sym> {
    let enabled: HashSet<Sym> = features.iter().copied().collect();
    let gated = |when: &When| {
        when.feature
            .is_some_and(|feature| enabled.contains(&feature))
    };
    let record_gated = |records: &[PolicyRecord]| records.iter().any(|record| gated(&record.when));
    index
        .runes()
        .iter()
        .filter(|(_, rune)| {
            let policy = &rune.policy;
            rune.stances.iter().any(|(_, stance)| {
                stance.surface.unlocks.iter().any(|unlock| {
                    enabled.contains(&unlock.feature) || unlock.when.as_ref().is_some_and(gated)
                })
            }) || record_gated(&policy.refuse)
                || record_gated(&policy.prefer)
                || record_gated(&policy.extend)
                || record_gated(&policy.contract)
                || record_gated(&policy.resolve)
        })
        .map(|(name, _)| *name)
        .collect()
}

/// The marker a memo file's head line carries. A file naming anything else is another format, not a newer spelling of this one.
pub const MEMO_FORMAT: &str = "ams-m1-memo/1";

/// What a memo file's head says about the memo: the configuration it was traced under, the world it was traced in (the enumeration's semantics tokens, [`crate::fixpoint::EnumerationModes::world_token`]), and the stamp its writer chose. The crate holds a file to the first two; the stamp is opaque here and is what `run_m1` reads back to decide which runes moved.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MemoHead {
    pub config: String,
    pub world: String,
    pub stamp: String,
}

/// What separates the members of a list inside one field — a notes list, a fired delta — which is the ASCII unit separator because a note is prose (`unlocked by ss03`) and may carry a space, while nothing the engine writes carries this byte; a writer meeting one in a note refuses rather than writes a list that would read back split.
const LIST_SEPARATOR: char = '\u{1f}';

/// Where one configuration's memo file sits under a directory.
pub fn memo_path(dir: &Path, token: &str) -> PathBuf {
    dir.join(format!("memo-{token}.tsv"))
}

/// One symbol as the file spells it: its seat in the file's own `Y` table, minted the first time a window, a record or a pointer names it, so every rune, stance, height and pointer half is written once as text and every later mention is an integer.
struct Symbols {
    seats: HashMap<Sym, u32>,
    lines: Vec<String>,
}

impl Symbols {
    fn new() -> Self {
        Self {
            seats: HashMap::new(),
            lines: Vec::new(),
        }
    }

    fn seat(&mut self, index: &SpecIndex, symbol: Sym) -> u32 {
        if let Some(&seat) = self.seats.get(&symbol) {
            return seat;
        }
        let seat =
            u32::try_from(self.lines.len()).expect("a memo file names fewer than 2^32 symbols");
        self.seats.insert(symbol, seat);
        self.lines.push(format!("Y\t{}", index.resolve(symbol)));
        seat
    }

    fn optional(&mut self, index: &SpecIndex, symbol: Option<Sym>) -> String {
        symbol.map_or_else(
            || "-".to_owned(),
            |symbol| self.seat(index, symbol).to_string(),
        )
    }

    /// One slot as the file spells it: `#` and the kind's first letter for a non-letter, the rune's symbol seat for a letter.
    fn slot(&mut self, index: &SpecIndex, kind: TokenKind, rune: Option<Sym>) -> String {
        match rune {
            Some(rune) => self.seat(index, rune).to_string(),
            None => format!("#{}", kind_letter(kind)),
        }
    }
}

/// The one letter each kind is spelled by in the file — the first of its own spelling, which the six kinds keep distinct.
fn kind_letter(kind: TokenKind) -> char {
    kind.as_str()
        .chars()
        .next()
        .expect("every kind has a spelling")
}

fn kind_of_letter(letter: &str) -> Option<TokenKind> {
    [
        TokenKind::Edge,
        TokenKind::Space,
        TokenKind::Zwnj,
        TokenKind::NamerDot,
        TokenKind::Letter,
        TokenKind::Unknown,
    ]
    .into_iter()
    .find(|kind| kind_letter(*kind).to_string() == letter)
}

/// The file's interning of one kind of record while it is written: every distinct value once, at the seat its `S`, `N` or `D` line holds in the file.
struct FileTable<T> {
    seats: HashMap<T, u32>,
    lines: Vec<String>,
}

impl<T: std::hash::Hash + Eq + Clone> FileTable<T> {
    fn new() -> Self {
        Self {
            seats: HashMap::new(),
            lines: Vec::new(),
        }
    }

    fn seat(
        &mut self,
        value: &T,
        spell: impl FnOnce(&T) -> Result<String, String>,
    ) -> Result<u32, String> {
        if let Some(&seat) = self.seats.get(value) {
            return Ok(seat);
        }
        let seat =
            u32::try_from(self.lines.len()).expect("a memo file seats fewer than 2^32 records");
        let line = spell(value)?;
        self.seats.insert(value.clone(), seat);
        self.lines.push(line);
        Ok(seat)
    }
}

fn settled_line(index: &SpecIndex, symbols: &mut Symbols, settled: &Settled) -> String {
    let cell = &settled.cell;
    let adjustments = if cell.adjustments.is_empty() {
        "-".to_owned()
    } else {
        cell.adjustments
            .iter()
            .map(|token| adjustment_text(index, *token))
            .collect::<Vec<String>>()
            .join(" ")
    };
    format!(
        "S\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
        symbols.seat(index, cell.rune),
        symbols.seat(index, cell.stance),
        symbols.optional(index, cell.entry),
        symbols.optional(index, cell.exit),
        adjustments,
        symbols.optional(index, settled.seam),
        settled.extension
    )
}

fn delta_line(index: &SpecIndex, symbols: &mut Symbols, pointers: &[Pointer]) -> String {
    let spelled: Vec<String> = pointers
        .iter()
        .map(|pointer| {
            format!(
                "{}:{}",
                symbols.seat(index, pointer.file),
                symbols.seat(index, pointer.path)
            )
        })
        .collect();
    format!("D\t{}", spelled.join(&LIST_SEPARATOR.to_string()))
}

/// One notes list as its `N` line, or the refusal a note the format cannot carry earns.
fn notes_line(list: &[String]) -> Result<String, String> {
    for note in list {
        if note.contains([LIST_SEPARATOR, '\t', '\n']) {
            return Err(format!("a note the memo format cannot carry: {note:?}"));
        }
    }
    Ok(format!("N\t{}", list.join(&LIST_SEPARATOR.to_string())))
}

/// One configuration's memo written as `memo-<config>.tsv`: the head line, then the four tables — `Y` for every symbol the file names, `S` for the settled records, `N` for the notes lists, `D` for the fired deltas, each seated in file order, the lists' members set apart by [`LIST_SEPARATOR`] — then one `E` line per window naming its key by symbol seats, its three record seats, its prospect, its joint flag and its stage. The windows are `own`'s and, after them, every window of each carried base that the base's exclusion admits and no earlier source held, so the file is the union a later build may read and never a copy of a window twice, and they go out in key order rather than in the order the maps happen to hold them, so two builds of one memo write one file. Every name is spelled as text once, in the `Y` table, because a symbol is an interning order this spec happens to have and the next spec need not; the windows themselves are streamed to the file rather than built up in memory, since a configuration's memo runs to millions of them.
///
/// The stamp may carry neither a tab nor a newline, since the head is one tab-separated line; a writer handing one over is refused rather than written around.
pub fn write_memo(
    index: &SpecIndex,
    path: &Path,
    head: &MemoHead,
    own: &MemoSnapshot,
    carried: &[MemoBase],
) -> Result<(), String> {
    if head.stamp.contains(['\t', '\n']) || head.config.contains(['\t', '\n']) {
        return Err("a memo stamp is one line with no tabs".to_owned());
    }
    let none = Exclusion::none();
    let sources: Vec<(&MemoSnapshot, &Exclusion)> = std::iter::once((own, &none))
        .chain(carried.iter().map(|base| (&*base.memo, &base.excluded)))
        .collect();
    let held_earlier = |seat: usize, key: &TraceKey| {
        sources[..seat]
            .iter()
            .any(|(memo, excluded)| memo.entries.contains_key(key) && excluded.admits(key))
    };
    let mut keyed: Vec<(&TraceKey, TraceEntry, usize)> = Vec::new();
    for (seat, (memo, excluded)) in sources.iter().enumerate() {
        for (key, entry) in &memo.entries {
            if !excluded.admits(key) || held_earlier(seat, key) {
                continue;
            }
            keyed.push((key, *entry, seat));
        }
    }
    keyed.sort_unstable_by_key(|(key, _, _)| **key);
    let mut symbols = Symbols::new();
    let mut settled: FileTable<Settled> = FileTable::new();
    let mut notes: FileTable<Vec<String>> = FileTable::new();
    let mut deltas: FileTable<Box<[Pointer]>> = FileTable::new();
    // Two passes over the same order: the first seats every symbol and record so the tables can go out ahead of the windows that name them, the second streams the windows.
    let mut seated: Vec<(u32, u32, u32)> = Vec::with_capacity(keyed.len());
    for (key, entry, seat) in &keyed {
        let (memo, _) = sources[*seat];
        for symbol in key.runes_named() {
            symbols.seat(index, symbol);
        }
        if let Some(stance) = key.left_stance {
            symbols.seat(index, stance);
        }
        if let Some(seam) = key.left_seam {
            symbols.seat(index, seam);
        }
        let settled_seat = settled.seat(memo.settled(*entry), |record| {
            Ok(settled_line(index, &mut symbols, record))
        })?;
        let notes_seat = notes.seat(&memo.notes[entry.notes.index()], |list| notes_line(list))?;
        let delta_seat = deltas.seat(&memo.deltas[entry.delta.index()], |delta| {
            Ok(delta_line(index, &mut symbols, delta))
        })?;
        seated.push((settled_seat, notes_seat, delta_seat));
    }
    let file =
        std::fs::File::create(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let mut out = std::io::BufWriter::with_capacity(1 << 20, file);
    let complain = |error: std::io::Error| format!("{}: {error}", path.display());
    writeln!(
        out,
        "# {MEMO_FORMAT}\t{}\t{}\t{}",
        head.config, head.world, head.stamp
    )
    .map_err(complain)?;
    for table in [&symbols.lines, &settled.lines, &notes.lines, &deltas.lines] {
        for line in table {
            writeln!(out, "{line}").map_err(complain)?;
        }
    }
    let mut line = String::new();
    for ((key, entry, _), (settled_seat, notes_seat, delta_seat)) in keyed.iter().zip(seated) {
        line.clear();
        let _ = write!(
            line,
            "E\t{}\t{}\t{}\t{}\t{}\t{}",
            kind_letter(key.left_kind),
            symbols.optional(index, key.left_rune),
            symbols.optional(index, key.left_stance),
            symbols.optional(index, key.left_seam),
            key.left_extension,
            symbols.seat(index, key.token)
        );
        for slot in 0..4 {
            let _ = write!(
                line,
                "\t{}",
                symbols.slot(index, key.kinds[slot], key.runes[slot])
            );
        }
        let _ = write!(
            line,
            "\t{settled_seat}\t{notes_seat}\t{delta_seat}\t{}\t{}\t{}",
            entry.prospect,
            u8::from(entry.joint_floor),
            entry.decided_stage.as_str()
        );
        writeln!(out, "{line}").map_err(complain)?;
    }
    out.flush().map_err(complain)
}

/// The head of a memo file, read without the rest of it.
pub fn read_memo_head(path: &Path) -> Result<MemoHead, String> {
    let file = std::fs::File::open(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let mut first = String::new();
    std::io::BufReader::new(file)
        .read_line(&mut first)
        .map_err(|error| format!("{}: {error}", path.display()))?;
    parse_head(first.trim_end_matches('\n')).ok_or_else(|| {
        format!(
            "{}: not an {MEMO_FORMAT} memo (head line {first:?})",
            path.display()
        )
    })
}

fn parse_head(line: &str) -> Option<MemoHead> {
    let rest = line.strip_prefix(&format!("# {MEMO_FORMAT}\t"))?;
    let mut fields = rest.splitn(3, '\t');
    let config = fields.next()?.to_owned();
    let world = fields.next()?.to_owned();
    let stamp = fields.next()?.to_owned();
    Some(MemoHead {
        config,
        world,
        stamp,
    })
}

/// A seat into the file's symbol table resolved to this spec's symbol, `None` for an absent field, and `Err` for a seat the table never seated or a name this spec never interned.
fn symbol_at(table: &[Option<Sym>], text: &str) -> Result<Option<Sym>, ()> {
    if text == "-" {
        return Ok(None);
    }
    let seat: usize = text.parse().map_err(|_| ())?;
    table.get(seat).copied().flatten().map(Some).ok_or(())
}

fn seat_at(text: &str) -> Option<usize> {
    text.parse().ok()
}

/// One memo file read back as a snapshot over this spec, holding only the windows `keep` admits. The configuration and the world are held to `expected`'s (its stamp is not read, being the caller's business); a window naming a symbol this spec never interned — a rune, a stance or a height that left the spec, or a pointer whose record did — is dropped rather than refused, because such a window names something that moved and would be excluded by the caller's rule in any case, and a line the format does not spell is a refusal naming it.
pub(crate) fn read_memo(
    index: &SpecIndex,
    path: &Path,
    expected: &MemoHead,
    keep: impl Fn(&TraceKey) -> bool,
) -> Result<MemoSnapshot, String> {
    let file = std::fs::File::open(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let complain = |number: usize, what: &str| format!("{}: line {number}: {what}", path.display());
    let mut lines = std::io::BufReader::with_capacity(1 << 20, file)
        .lines()
        .enumerate();
    let (_, head) = lines
        .next()
        .ok_or_else(|| complain(1, "an empty file is not a memo"))?;
    let head = head.map_err(|error| format!("{}: {error}", path.display()))?;
    let head = parse_head(&head).ok_or_else(|| complain(1, "not a memo head line"))?;
    if head.config != expected.config || head.world != expected.world {
        return Err(format!(
            "{}: a memo for configuration {} in world {}, not {} in {}",
            path.display(),
            head.config,
            head.world,
            expected.config,
            expected.world
        ));
    }
    let placeholder = boundary_settled(index.vocab(), TokenKind::Edge);
    let mut memo = MemoSnapshot::default();
    let mut symbols: Vec<Option<Sym>> = Vec::new();
    let mut settled_usable: Vec<bool> = Vec::new();
    let mut delta_usable: Vec<bool> = Vec::new();
    for (offset, line) in lines {
        let number = offset + 1;
        let line = line.map_err(|error| format!("{}: {error}", path.display()))?;
        let mut fields = line.split('\t');
        match fields.next() {
            Some("Y") => {
                let text = fields.next().unwrap_or_default();
                if fields.next().is_some() {
                    return Err(complain(number, "a symbol is one field"));
                }
                symbols.push(index.sym_of(text));
            }
            Some("S") => {
                let fields: Vec<&str> = fields.collect();
                let [rune, stance, entry, exit, adjustments, seam, extension] = fields.as_slice()
                else {
                    return Err(complain(number, "a settled record has seven fields"));
                };
                let extension: i64 = extension
                    .parse()
                    .map_err(|_| complain(number, "an extension is a count"))?;
                let parsed = (|| {
                    let adjustments: Vec<AdjustmentToken> = if *adjustments == "-" {
                        Vec::new()
                    } else {
                        adjustments
                            .split(' ')
                            .map(|token| adjustment_from_text(index, token))
                            .collect::<Option<Vec<_>>>()?
                    };
                    Some(Settled {
                        cell: CellId {
                            rune: symbol_at(&symbols, rune).ok()??,
                            stance: symbol_at(&symbols, stance).ok()??,
                            entry: symbol_at(&symbols, entry).ok()?,
                            exit: symbol_at(&symbols, exit).ok()?,
                            adjustments,
                        },
                        seam: symbol_at(&symbols, seam).ok()?,
                        extension,
                    })
                })();
                settled_usable.push(parsed.is_some());
                memo.settled
                    .push(parsed.unwrap_or_else(|| placeholder.clone()));
            }
            Some("N") => {
                let text = fields.next().unwrap_or_default();
                if fields.next().is_some() {
                    return Err(complain(number, "a notes list is one field"));
                }
                memo.notes.push(if text.is_empty() {
                    Vec::new()
                } else {
                    text.split(LIST_SEPARATOR).map(str::to_owned).collect()
                });
            }
            Some("D") => {
                let text = fields.next().unwrap_or_default();
                if fields.next().is_some() {
                    return Err(complain(number, "a delta is one field"));
                }
                let parsed: Option<Vec<Pointer>> = if text.is_empty() {
                    Some(Vec::new())
                } else {
                    text.split(LIST_SEPARATOR)
                        .map(|pointer| {
                            let (file, path) = pointer.split_once(':')?;
                            Some(Pointer::of(&Provenance {
                                file: symbol_at(&symbols, file).ok()??,
                                path: symbol_at(&symbols, path).ok()??,
                            }))
                        })
                        .collect()
                };
                delta_usable.push(parsed.is_some());
                memo.deltas
                    .push(parsed.unwrap_or_default().into_boxed_slice());
            }
            Some("E") => {
                let fields: Vec<&str> = fields.collect();
                let [
                    left_kind,
                    left_rune,
                    left_stance,
                    left_seam,
                    left_extension,
                    token,
                    slot1,
                    slot2,
                    slot3,
                    slot4,
                    settled_seat,
                    notes_seat,
                    delta_seat,
                    prospect,
                    joint,
                    stage,
                ] = fields.as_slice()
                else {
                    return Err(complain(number, "a window has sixteen fields"));
                };
                let left_kind = kind_of_letter(left_kind)
                    .ok_or_else(|| complain(number, "a left kind is one of the six"))?;
                let left_extension: i16 = left_extension
                    .parse()
                    .map_err(|_| complain(number, "a left extension is a count"))?;
                let (Some(settled_seat), Some(notes_seat), Some(delta_seat)) = (
                    seat_at(settled_seat),
                    seat_at(notes_seat),
                    seat_at(delta_seat),
                ) else {
                    return Err(complain(number, "a seat is a count"));
                };
                let prospect: i8 = prospect
                    .parse()
                    .map_err(|_| complain(number, "a prospect is a seam count"))?;
                let joint_floor = match *joint {
                    "0" => false,
                    "1" => true,
                    _ => return Err(complain(number, "a joint flag is 0 or 1")),
                };
                let decided_stage = DecidedStage::from_text(stage)
                    .ok_or_else(|| complain(number, "a stage is one of the seven"))?;
                if settled_seat >= memo.settled.len()
                    || notes_seat >= memo.notes.len()
                    || delta_seat >= memo.deltas.len()
                {
                    return Err(complain(
                        number,
                        "a seat names a record the file seated first",
                    ));
                }
                if !settled_usable[settled_seat] || !delta_usable[delta_seat] {
                    continue;
                }
                let key = (|| {
                    let mut kinds = [TokenKind::Edge; 4];
                    let mut runes = [None; 4];
                    for (slot, text) in [slot1, slot2, slot3, slot4].into_iter().enumerate() {
                        match text.strip_prefix('#') {
                            Some(letter) => {
                                kinds[slot] = kind_of_letter(letter)?;
                            }
                            None => {
                                kinds[slot] = TokenKind::Letter;
                                runes[slot] = Some(symbol_at(&symbols, text).ok()??);
                            }
                        }
                    }
                    Some(TraceKey {
                        left_kind,
                        left_rune: symbol_at(&symbols, left_rune).ok()?,
                        left_stance: symbol_at(&symbols, left_stance).ok()?,
                        left_seam: symbol_at(&symbols, left_seam).ok()?,
                        left_extension,
                        token: symbol_at(&symbols, token).ok()??,
                        kinds,
                        runes,
                    })
                })();
                let Some(key) = key else {
                    continue;
                };
                if !keep(&key) {
                    continue;
                }
                memo.entries.insert(
                    key,
                    TraceEntry {
                        settled: SettledSeat::at(settled_seat),
                        notes: NotesSeat::at(notes_seat),
                        delta: DeltaSeat::at(delta_seat),
                        prospect,
                        joint_floor,
                        decided_stage,
                    },
                );
            }
            Some(other) => {
                return Err(complain(
                    number,
                    &format!("{other:?} is not a line the memo format spells"),
                ));
            }
            None => return Err(complain(number, "an empty line")),
        }
    }
    Ok(memo)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixpoint::{EnumerationModes, Seed, enumerate_for_tables};
    use crate::index::fixtures;
    use crate::stream::emit_transitions;

    /// A scratch directory of this module's own under `target/`, cleared first.
    fn scratch(name: &str) -> PathBuf {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target/test-scratch")
            .join(name);
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).expect("the scratch directory is makeable");
        directory
    }

    /// The mini dump with `qsTea`'s refusal struck, which is the edit the seed tests invalidate `qsTea` for.
    fn mini_dump_without_the_tea_refusal() -> String {
        let refusal = format!(
            "\"refuse\":{}",
            fixtures::seq(&[&fixtures::record(&[
                ("kind", "\"refuse\""),
                (
                    "provenance",
                    &fixtures::names(&["qsTea.yaml", "policy.refuse[0]"])
                ),
            ])])
        );
        let dump = fixtures::mini_dump();
        assert!(
            dump.contains(&refusal),
            "the fixture spells the refusal this test strikes"
        );
        dump.replacen(&refusal, "\"refuse\":[]", 1)
    }

    fn head(config: &str) -> MemoHead {
        MemoHead {
            config: config.to_owned(),
            world: EnumerationModes::default().world_token(),
            stamp: "stamp-under-test".to_owned(),
        }
    }

    /// One configuration enumerated with its memo kept, over whatever bases it is handed.
    fn enumerate_keeping(
        index: &SpecIndex,
        features: &[Sym],
        bases: Vec<MemoBase>,
    ) -> (crate::stream::FixpointProduct, MemoSnapshot) {
        let (product, _, memo) = enumerate_for_tables(
            index,
            features,
            EnumerationModes::default(),
            None,
            Seed {
                bases,
                keep_memo: true,
            },
        )
        .expect("the fixture closes");
        (product, memo.expect("kept"))
    }

    /// The file is the memo: written and read back over the same spec it holds every window with its record, its notes, its delta and its stage, and an enumeration reading it back as a base answers every window out of it and reaches the same product.
    #[test]
    fn a_memo_file_reads_back_as_the_memo_that_wrote_it() {
        let index = fixtures::mini();
        let (product, memo) = enumerate_keeping(&index, &[], Vec::new());
        let path = scratch("memo-round-trip").join("memo-default.tsv");
        write_memo(&index, &path, &head("default"), &memo, &[]).expect("the file writes");
        assert_eq!(
            read_memo_head(&path).expect("the head reads"),
            head("default")
        );
        let back = read_memo(&index, &path, &head("default"), |_| true).expect("the file reads");
        assert_eq!(back.len(), memo.len());
        for (key, entry) in &memo.entries {
            let again = back.entries[key];
            assert_eq!(back.settled(again), memo.settled(*entry));
            assert_eq!(
                back.notes[again.notes.index()],
                memo.notes[entry.notes.index()]
            );
            assert_eq!(back.delta(again), memo.delta(*entry));
            assert_eq!(
                (again.prospect, again.joint_floor, again.decided_stage),
                (entry.prospect, entry.joint_floor, entry.decided_stage)
            );
        }
        let base = MemoBase {
            memo: Arc::new(back),
            excluded: Exclusion::none(),
        };
        let (seeded, own) = enumerate_keeping(&index, &[], vec![base]);
        assert_eq!(
            emit_transitions(&index, &product),
            emit_transitions(&index, &seeded)
        );
        assert!(own.is_empty(), "every window was answered out of the file");
    }

    /// The seed across builds (issue #179): the edited spec enumerated over the previous spec's memo, behind an exclusion naming the edited rune, reaches the edited spec's from-scratch product — and the edit is a real one, since the two specs' products differ.
    #[test]
    fn an_edited_spec_seeded_from_the_previous_memo_reaches_its_from_scratch_product() {
        let before = fixtures::mini();
        let after = fixtures::index_of(&mini_dump_without_the_tea_refusal());
        let (product_before, memo_before) = enumerate_keeping(&before, &[], Vec::new());
        let path = scratch("memo-edited").join("memo-default.tsv");
        write_memo(&before, &path, &head("default"), &memo_before, &[]).expect("the file writes");
        let (product_after, _) = enumerate_keeping(&after, &[], Vec::new());
        assert_ne!(
            emit_transitions(&before, &product_before),
            emit_transitions(&after, &product_after),
            "striking the refusal moves the product"
        );
        let previous = read_memo(&after, &path, &head("default"), |_| true)
            .expect("reads over the edited spec");
        let tea = fixtures::sym(&after, "qsTea");
        let (seeded, own) = enumerate_keeping(
            &after,
            &[],
            vec![MemoBase {
                memo: Arc::new(previous),
                excluded: Exclusion::of([tea]),
            }],
        );
        assert_eq!(
            emit_transitions(&after, &product_after),
            emit_transitions(&after, &seeded)
        );
        assert!(
            !own.is_empty(),
            "the windows naming qsTea were traced afresh"
        );
        assert!(
            own.entries
                .keys()
                .all(|key| key.runes_named().any(|rune| rune == tea)),
            "and nothing else was"
        );
    }

    /// What the reader refuses and what it drops: another configuration's file is a refusal naming both, and a window naming a stance the spec no longer has is dropped while its neighbors read.
    #[test]
    fn a_memo_for_another_configuration_is_refused_and_a_stale_window_is_dropped() {
        let index = fixtures::mini();
        let (_, memo) = enumerate_keeping(&index, &[], Vec::new());
        let path = scratch("memo-refusals").join("memo-default.tsv");
        write_memo(&index, &path, &head("default"), &memo, &[]).expect("the file writes");
        let complaint = read_memo(&index, &path, &head("ss03"), |_| true).expect_err("refused");
        assert!(complaint.contains("configuration default"), "{complaint}");
        let text = std::fs::read_to_string(&path).expect("the file is text");
        let stale = text.replace("\nY\talt\n", "\nY\tgone\n");
        assert_ne!(
            stale, text,
            "the memo names the alt stance in its symbol table"
        );
        std::fs::write(&path, stale).expect("rewritten");
        let back = read_memo(&index, &path, &head("default"), |_| true).expect("still reads");
        assert!(back.len() < memo.len());
        assert!(!back.is_empty());
    }

    /// The mini fixture unlocks a `qsMay` entry under `ss03` and nothing else under anything, so `ss03` names `qsMay` alone and a feature nothing unlocks names no rune.
    #[test]
    fn the_unlocking_runes_of_a_configuration_are_the_ones_reading_its_features() {
        let index = fixtures::mini();
        let ss03 = fixtures::sym(&index, "ss03");
        let named = unlocking_runes(&index, &[ss03]);
        let names: Vec<&str> = {
            let mut names: Vec<&str> = named.iter().map(|rune| index.resolve(*rune)).collect();
            names.sort_unstable();
            names
        };
        assert_eq!(names, ["qsMay"]);
        assert!(unlocking_runes(&index, &[]).is_empty());
    }

    /// An exclusion is a miss on any key naming one of its runes, on the left or on any raw slot, and an empty exclusion admits everything.
    #[test]
    fn an_exclusion_refuses_a_key_naming_any_of_its_runes_anywhere() {
        let index = fixtures::mini();
        let may = fixtures::sym(&index, "qsMay");
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let excluded = Exclusion::of([may]);
        let mut key = TraceKey::for_test(pea, [Some(tea), None, None, None]);
        assert!(excluded.admits(&key));
        assert!(Exclusion::none().admits(&key));
        key.runes[2] = Some(may);
        assert!(!excluded.admits(&key));
        key.runes[2] = None;
        key.left_rune = Some(may);
        assert!(!excluded.admits(&key));
        key.left_rune = None;
        key.token = may;
        assert!(!excluded.admits(&key));
    }
}
